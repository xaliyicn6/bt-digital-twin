"""M07 工单：列表/创建/流转/验收/分析派发/班组查询。

状态机唯一事实源是 ``state/work_order_state_machine.yaml``：事件只能按配置里的
from/event/to 迁移，guard 在这里按事件显式求值（YAML 里的 guard 是自然语言描述，
不允许用 eval 执行配置文本）。

数据库约束兜底：
    ``uq_wo_active_defect`` 拦截重复未结单（BT-70002）；
    ``version`` 乐观锁拦截并发流转（409）。

附件计数说明：YAML 的 guard 写的是 ``attachment ... status=SENT``，
但 ``db/schema.sql`` 的 ``attachment`` 表没有 ``status`` 列（既有表本轮不改），
因此 T01 SUBMIT 守卫实际按 ``deleted_at is null`` 的行数求值。派单时若带现场照片，
会先把附件改挂到工单再走真实的 SUBMIT 迁移，守卫读到的是真实行数（不是伪造数量）；
无照片的场景才走系统代建并在事件里记录 ``initiated_by`` 与 ``guard_note``。
"""

from __future__ import annotations

import base64
import binascii
import functools
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import yaml
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.core.errors import api_error
from app.schemas.workorder import (
    TransitionPayload,
    WorkOrderAcceptanceRequest,
    WorkOrderCreate,
    WorkOrderDispatchRequest,
    WorkOrderEventName,
    WorkOrderTransitionRequest,
)
from app.services.defect_levels import load_thresholds

STATE_YAML = Path(__file__).resolve().parents[3] / "state" / "work_order_state_machine.yaml"
SUPPORTED_DISPATCH_TYPES = frozenset({"VERTICALITY"})
ORDER_COLUMNS = (
    "id, order_no, status, version, priority, defect_id, component_code, handler_id, "
    "reporter_id, team_id, due_date, source_task_id, source_snapshot, sla_deadline, "
    "sla_paused_total, is_overdue, created_at, closed_at"
)
# 审批/列表需要的展示字段（构件名、建议等级、班组名）；JOIN 均为主键或唯一键，1:1 不放大行数
ORDER_SELECT = (
    "SELECT " + ", ".join(f"w.{name.strip()}" for name in ORDER_COLUMNS.split(",")) + ", "
    "c.component_name, d.level::text AS suggested_level, t.team_name "
    "FROM work_order w "
    "LEFT JOIN component c ON c.project_id = w.project_id "
    "  AND c.component_code = w.component_code AND c.deleted_at IS NULL "
    "LEFT JOIN defect_ledger d ON d.id = w.defect_id "
    "LEFT JOIN maintenance_team t ON t.id = w.team_id "
)
TERMINAL_STATUSES = frozenset({"CLOSED", "CANCELLED", "MERGED"})
# 迁移完成后新起算的时限（分钟），来自 YAML 的 sla_minutes
SLA_MINUTES = {"SUBMIT": 1440, "ASSIGN": 4320, "SUBMIT_REPAIR": 1440, "TIMEOUT": 0}


@functools.cache
def transitions() -> list[dict[str, Any]]:
    with STATE_YAML.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    data = cast(dict[str, Any], raw)
    items = data.get("transitions")
    if not isinstance(items, list):
        raise RuntimeError(f"{STATE_YAML} 缺少 transitions")
    return [cast(dict[str, Any], item) for item in items]


def operator_uuid(principal: Principal) -> uuid.UUID | None:
    try:
        return uuid.UUID(principal.subject)
    except ValueError:
        return None


def _has_role(principal: Principal, allowed: list[str]) -> bool:
    """SYSTEM_ADMIN 视为可执行任何流转（开发旁路与后台管理场景）。"""
    if "SYSTEM_ADMIN" in principal.roles:
        return True
    return bool(set(principal.roles) & set(allowed))


def _cursor_encode(created_at: datetime, order_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{order_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _cursor_decode(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        created_raw, _, order_raw = decoded.partition("|")
        return datetime.fromisoformat(created_raw), uuid.UUID(order_raw)
    except (ValueError, binascii.Error) as exc:
        raise api_error("BT-10003", "cursor 非法") from exc


def _row(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


def attachments_count(session: Session, order_id: uuid.UUID) -> int:
    result = session.execute(
        text(
            "SELECT count(*) FROM attachment "
            "WHERE object_type = 'WORK_ORDER' AND object_id = :id AND deleted_at IS NULL"
        ),
        {"id": order_id},
    )
    return int(result.scalar_one())


def get_order(session: Session, order_id: uuid.UUID) -> dict[str, Any]:
    result = session.execute(
        text(f"{ORDER_SELECT} WHERE w.id = :id AND w.deleted_at IS NULL"),  # noqa: S608
        {"id": order_id},
    )
    row = result.first()
    if row is None:
        raise api_error("BT-70008", f"工单不存在：{order_id}")
    return _row(row)


def order_events(session: Session, order_id: uuid.UUID) -> list[dict[str, Any]]:
    result = session.execute(
        text(
            "SELECT id, event, from_status, to_status, operator_id, reason, occurred_at "
            "FROM work_order_event WHERE order_id = :id ORDER BY occurred_at ASC, id ASC"
        ),
        {"id": order_id},
    )
    return [_row(row) for row in result.all()]


def order_attachments(session: Session, order_id: uuid.UUID) -> list[dict[str, Any]]:
    result = session.execute(
        text(
            "SELECT id, file_name, content_type FROM attachment "
            "WHERE object_type = 'WORK_ORDER' AND object_id = :id AND deleted_at IS NULL"
        ),
        {"id": order_id},
    )
    return [_row(row) for row in result.all()]


def list_orders(
    session: Session,
    *,
    status: str | None,
    component_code: str | None,
    handler_id: uuid.UUID | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    cursor_created: datetime | None = None
    cursor_id: uuid.UUID | None = None
    if cursor:
        cursor_created, cursor_id = _cursor_decode(cursor)

    sql = text(
        f"""
        {ORDER_SELECT}
        WHERE w.deleted_at IS NULL
          AND (
            CAST(:status AS text) IS NULL
            OR w.status = CAST(:status AS workorder_status)
          )
          AND (
            CAST(:component_code AS varchar) IS NULL
            OR w.component_code = CAST(:component_code AS varchar)
          )
          AND (
            CAST(:handler_id AS uuid) IS NULL
            OR w.handler_id = CAST(:handler_id AS uuid)
          )
          AND (
            CAST(:cursor_created AS timestamptz) IS NULL
            OR (w.created_at, w.id) < (
              CAST(:cursor_created AS timestamptz), CAST(:cursor_id AS uuid)
            )
          )
        ORDER BY w.created_at DESC, w.id DESC
        LIMIT :limit
        """  # noqa: S608 - 列名固定
    )
    result = session.execute(
        sql,
        {
            "status": status,
            "component_code": component_code,
            "handler_id": handler_id,
            "cursor_created": cursor_created,
            "cursor_id": cursor_id,
            "limit": limit,
        },
    )
    rows = [_row(row) for row in result.all()]
    next_cursor = None
    if len(rows) == limit and rows:
        next_cursor = _cursor_encode(rows[-1]["created_at"], rows[-1]["id"])
    return rows, next_cursor


def _new_order_no(today: date) -> str:
    return f"WO-{today:%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def _insert_event(
    session: Session,
    *,
    order_id: uuid.UUID,
    event: str,
    from_status: str,
    to_status: str,
    operator_id: uuid.UUID | None,
    reason: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    session.execute(
        text(
            "INSERT INTO work_order_event "
            "(order_id, event, from_status, to_status, operator_id, reason, payload) "
            "VALUES (:order_id, :event, CAST(:from_status AS workorder_status), "
            "CAST(:to_status AS workorder_status), :operator_id, :reason, CAST(:payload AS jsonb))"
        ),
        {
            "order_id": order_id,
            "event": event,
            "from_status": from_status,
            "to_status": to_status,
            "operator_id": operator_id,
            "reason": reason,
            "payload": json.dumps(payload or {}, ensure_ascii=False),
        },
    )


def component_of(
    session: Session, component_code: str, project_id: uuid.UUID | None = None
) -> dict[str, Any] | None:
    result = session.execute(
        text(
            "SELECT id, project_id FROM component "
            "WHERE component_code = :code AND deleted_at IS NULL "
            "AND (CAST(:project_id AS uuid) IS NULL OR project_id = CAST(:project_id AS uuid)) "
            "ORDER BY created_at ASC LIMIT 1"
        ),
        {"code": component_code, "project_id": project_id},
    )
    row = result.first()
    return None if row is None else _row(row)


def create_order(
    session: Session, principal: Principal, payload: WorkOrderCreate
) -> dict[str, Any]:
    """POST /workorders：缺陷须为 CONFIRMED，工单落在 DRAFT（等待 T01 SUBMIT）。"""
    defect = session.execute(
        text(
            "SELECT id, project_id, status FROM defect_ledger "
            "WHERE id = :id AND deleted_at IS NULL"
        ),
        {"id": payload.defect_id},
    ).first()
    if defect is None:
        raise api_error("BT-70001", f"缺陷不存在：{payload.defect_id}")
    defect_row = _row(defect)
    if defect_row["status"] != "CONFIRMED":
        raise api_error("BT-70001", f"缺陷状态为 {defect_row['status']}，须为 CONFIRMED 才能建单")

    component = component_of(session, payload.component_code)
    if component is None:
        raise api_error("BT-30001", f"构件不存在：{payload.component_code}")

    operator_id = operator_uuid(principal)
    order_no = _new_order_no(date.today())
    try:
        result = session.execute(
            text(
                "INSERT INTO work_order "
                "(project_id, order_no, defect_id, component_code, status, priority, "
                " handler_id, reporter_id, due_date, remark, created_by) "
                "VALUES (:project_id, :order_no, :defect_id, :component_code, "
                " CAST('DRAFT' AS workorder_status), CAST('P2' AS notify_level), "
                " :handler_id, :reporter_id, :due_date, :remark, :created_by) "
                f"RETURNING {ORDER_COLUMNS}"
            ),
            {
                "project_id": component["project_id"],
                "order_no": order_no,
                "defect_id": payload.defect_id,
                "component_code": payload.component_code,
                "handler_id": payload.handler_id,
                "reporter_id": operator_id,
                "due_date": payload.due_date,
                "remark": payload.remark,
                "created_by": operator_id,
            },
        )
        row = _row(result.one())
    except IntegrityError as exc:
        session.rollback()
        raise api_error("BT-70002", "该缺陷已有未结工单") from exc
    session.commit()
    return row


def _find_transition(current: str, event: str) -> dict[str, Any]:
    exact = [
        item for item in transitions() if item.get("event") == event and item.get("from") == current
    ]
    if exact:
        return exact[0]
    wildcard = [
        item for item in transitions() if item.get("event") == event and item.get("from") == "*"
    ]
    if not wildcard:
        raise api_error("BT-70001", f"状态 {current} 不允许事件 {event}")
    return wildcard[0]


def _check_guard(
    session: Session,
    order: dict[str, Any],
    event: str,
    payload_reason: str | None,
    payload_handler: uuid.UUID | None,
    payload_merge: uuid.UUID | None,
    payload_reject_code: str | None = None,
) -> uuid.UUID | None:
    """按事件求值 guard；返回流转后应使用的 handler_id（ASSIGN/REASSIGN）。"""
    if event == "SUBMIT":
        attachments = attachments_count(session, order["id"])
        defect_status = session.execute(
            text("SELECT status FROM defect_ledger WHERE id = :id"), {"id": order["defect_id"]}
        ).scalar_one()
        if defect_status != "CONFIRMED" or order["handler_id"] is None or attachments < 1:
            raise api_error(
                "BT-70001",
                "SUBMIT 守卫未通过：缺陷须为 CONFIRMED、handler_id 非空、附件至少 1 份"
                f"（当前缺陷={defect_status}, handler={order['handler_id']}, 附件={attachments}）",
            )
        return None

    if event == "CANCEL":
        minimum = 5 if order["status"] == "DRAFT" else 1
        if not payload_reason or len(payload_reason) < minimum:
            raise api_error("BT-70001", f"CANCEL 需要至少 {minimum} 个字符的原因")
        return None

    if event == "APPROVE":
        # YAML T03 guard：operator_id != reporter_id 且缺陷等级为 L1/L2/L3
        operator_id = order.get("_operator_id")
        if order["reporter_id"] is not None and order["reporter_id"] == operator_id:
            raise api_error("BT-70001", "审核人不能与上报人相同（T03 guard）")
        level = session.execute(
            text("SELECT level::text FROM defect_ledger WHERE id = :id AND deleted_at IS NULL"),
            {"id": order["defect_id"]},
        ).scalar_one_or_none()
        if level not in {"L1", "L2", "L3"}:
            raise api_error("BT-70001", f"缺陷等级非法（T03 guard）：{level}")
        return None

    if event in {"REJECT", "RETURN"}:
        if payload_reason is None or len(payload_reason) < (10 if event == "RETURN" else 1):
            raise api_error("BT-70001", f"{event} 必须填写原因（RETURN 至少 10 字）")
        if event == "REJECT":
            # YAML T04 guard：reject_reason_code 必须取自 dict_type='reject_reason'
            if payload_reject_code is None:
                raise api_error("BT-70001", "REJECT 必须提供 reject_reason_code")
            known = session.execute(
                text(
                    "SELECT 1 FROM dict_item WHERE type_code = 'reject_reason' "
                    "AND item_code = :code AND enabled AND deleted_at IS NULL"
                ),
                {"code": payload_reject_code},
            ).first()
            if known is None:
                raise api_error("BT-70001", f"未知的驳回原因码：{payload_reject_code}")
        return None

    if event == "REVISE":
        if attachments_count(session, order["id"]) < 1 and payload_reason is None:
            raise api_error("BT-70001", "REVISE 需要附件或修改说明")
        return None

    if event == "ASSIGN":
        handler = payload_handler or order["handler_id"]
        if handler is None:
            raise api_error("BT-70001", "ASSIGN 需要 handler_id")
        return handler

    if event == "ACCEPT_TASK":
        if order["handler_id"] != order.get("_operator_id"):
            raise api_error("BT-70001", "只有工单受理人可以接单（T07 guard）")
        return None

    if event == "REASSIGN":
        if payload_handler is None or payload_handler == order["handler_id"]:
            raise api_error("BT-70001", "REASSIGN 需要新的 handler_id，且不能与原受理人相同")
        return payload_handler

    if event == "SUSPEND":
        if not payload_reason or attachments_count(session, order["id"]) < 1:
            raise api_error("BT-70001", "SUSPEND 需要挂起原因与至少 1 份附件")
        return None

    if event == "RESUME":
        if not payload_reason:
            raise api_error("BT-70001", "RESUME 需要恢复原因")
        return None

    if event == "SUBMIT_REPAIR":
        if attachments_count(session, order["id"]) < 2 or not payload_reason:
            raise api_error("BT-70001", "SUBMIT_REPAIR 需要至少 2 份附件与处置说明")
        return None

    if event == "ACCEPT":
        if attachments_count(session, order["id"]) < 2:
            raise api_error("BT-70001", "ACCEPT 需要至少 2 份现场附件")
        return None

    if event == "MERGE":
        if payload_merge is None:
            raise api_error("BT-70001", "MERGE 需要 merge_into")
        return None

    return None


def transition(
    session: Session, principal: Principal, order_id: uuid.UUID, request: WorkOrderTransitionRequest
) -> dict[str, Any]:
    order = get_order(session, order_id)
    order["_operator_id"] = operator_uuid(principal)
    event = request.event.value
    item = _find_transition(order["status"], event)

    allowed = cast(list[str], item.get("allowed_roles") or [])
    if not _has_role(principal, allowed):
        raise api_error("BT-70001", f"当前角色无权执行 {event}（允许：{allowed}）")

    if order["version"] != request.expected_version:
        raise api_error(
            "BT-70001", f"版本冲突：当前 {order['version']}，请求 {request.expected_version}"
        )

    info = request.payload
    reason = None if info is None else info.reason
    handler_id = _check_guard(
        session,
        order,
        event,
        reason,
        None if info is None else info.handler_id,
        None if info is None else info.merge_into,
        None if info is None else info.reject_reason_code,
    )

    to_status = cast(str, item["to"])
    if event == "TIMEOUT":
        to_status = "OVERDUE"

    set_parts = [
        "status = CAST(:to_status AS workorder_status)",
        "version = version + 1",
        "updated_at = now()",
    ]
    params: dict[str, Any] = {
        "id": order_id,
        "expected_version": request.expected_version,
        "to_status": to_status,
        "operator_id": order["_operator_id"],
        "reason": reason,
        "handler_id": None,
        "merge_into_id": None,
        "is_overdue": None,
        "sla_deadline": None,
        "sla_paused_total": None,
    }

    if handler_id is not None:
        set_parts.append("handler_id = :handler_id")
        params["handler_id"] = handler_id
    if event == "MERGE" and info is not None and info.merge_into is not None:
        set_parts.append("merge_into_id = :merge_into_id")
        params["merge_into_id"] = info.merge_into
    if to_status == "CLOSED":
        set_parts.append("closed_at = now()")
    if event in SLA_MINUTES and to_status != "OVERDUE":
        minutes = SLA_MINUTES[event]
        set_parts.append("sla_deadline = now() + :sla_interval")
        params["sla_interval"] = timedelta(minutes=minutes)
    if event == "TIMEOUT":
        set_parts.append("is_overdue = true")
    if event == "SUBMIT_REPAIR":
        set_parts.append("is_overdue = false")
    if event == "RESUME":
        paused_seconds = session.execute(
            text(
                "SELECT coalesce(extract(epoch from (now() - max(occurred_at))), 0)::int "
                "FROM work_order_event WHERE order_id = :id AND event = 'SUSPEND'"
            ),
            {"id": order_id},
        ).scalar_one()
        set_parts.append("sla_paused_total = sla_paused_total + :sla_paused_total")
        params["sla_paused_total"] = int(paused_seconds)

    sql = text(
        "UPDATE work_order SET " + ", ".join(set_parts) + " "
        "WHERE id = :id AND version = :expected_version "
        f"RETURNING {ORDER_COLUMNS}"  # noqa: S608 - 固定列名
    )
    try:
        updated = session.execute(sql, params).first()
        if updated is None:
            raise api_error("BT-70001", "并发流转冲突：版本已被其他操作修改")
        _insert_event(
            session,
            order_id=order_id,
            event=event,
            from_status=order["status"],
            to_status=to_status,
            operator_id=order["_operator_id"],
            reason=reason,
            payload={} if info is None else info.model_dump(exclude_none=True, mode="json"),
        )
        if event == "SUBMIT":
            session.execute(
                text("UPDATE defect_ledger SET status = 'ORDER_CREATED' WHERE id = :id"),
                {"id": order["defect_id"]},
            )
        if event == "ACCEPT":
            session.execute(
                text(
                    "UPDATE defect_ledger SET status = 'CLOSED', closed_at = now() WHERE id = :id"
                ),
                {"id": order["defect_id"]},
            )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise api_error("BT-70002", "流转触发唯一约束冲突") from exc
    return _row(updated)


def acceptance(
    session: Session, principal: Principal, order_id: uuid.UUID, request: WorkOrderAcceptanceRequest
) -> dict[str, Any]:
    if request.result == "PASS" and len(request.attachment_ids) < 2:
        raise api_error("BT-70001", "验收通过需要至少 2 张现场附件")
    event = WorkOrderEventName.ACCEPT if request.result == "PASS" else WorkOrderEventName.RETURN
    version = get_order(session, order_id)["version"]
    return transition(
        session,
        principal,
        order_id,
        WorkOrderTransitionRequest(
            event=event,
            expected_version=version,
            payload=TransitionPayload(reason=request.comment) if request.comment else None,
        ),
    )


def list_teams(
    session: Session, *, project_id: uuid.UUID | None, limit: int, cursor: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    result = session.execute(
        text(
            """
            SELECT t.id AS team_id, t.project_id, t.team_code, t.team_name, t.leader_id,
                   u.display_name AS leader_name, t.contact_phone, t.line_scope,
                   (SELECT count(*) FROM maintenance_team_member m WHERE m.team_id = t.id)
                     AS member_count
            FROM maintenance_team t
            LEFT JOIN sys_user u ON u.id = t.leader_id
            WHERE t.deleted_at IS NULL
              AND (CAST(:project_id AS uuid) IS NULL OR t.project_id = CAST(:project_id AS uuid))
              AND (CAST(:cursor AS varchar) IS NULL OR t.team_code > CAST(:cursor AS varchar))
            ORDER BY t.team_code ASC
            LIMIT :limit
            """
        ),
        {"project_id": project_id, "cursor": cursor, "limit": limit},
    )
    teams = [_row(row) for row in result.all()]
    if teams:
        ids = [team["team_id"] for team in teams]
        members = session.execute(
            text(
                "SELECT m.team_id, m.user_id, u.username, m.role_in_team "
                "FROM maintenance_team_member m JOIN sys_user u ON u.id = m.user_id "
                "WHERE m.team_id = ANY(:ids) ORDER BY u.username"
            ),
            {"ids": ids},
        ).all()
        by_team: dict[uuid.UUID, list[dict[str, Any]]] = {}
        for row in members:
            item = _row(row)
            by_team.setdefault(item["team_id"], []).append(item)
        for team in teams:
            team["members"] = by_team.get(team["team_id"], [])
    next_cursor = teams[-1]["team_code"] if len(teams) == limit and teams else None
    return teams, next_cursor


def _geometry_result(session: Session, task_id: uuid.UUID, component_code: str) -> dict[str, Any]:
    task = session.execute(
        text(
            "SELECT id, status, payload, result_ref FROM async_task "
            "WHERE id = :id AND biz_type = 'GEOMETRY'"
        ),
        {"id": task_id},
    ).first()
    if task is None:
        raise api_error("BT-60003", f"几何任务不存在：{task_id}")
    task_row = _row(task)
    payload = cast(dict[str, Any], task_row["payload"] or {})
    analysis_type = str(payload.get("analysis_type", ""))

    if task_row["status"] != "SUCCEEDED":
        raise api_error(
            "BT-70003",
            f"源任务状态为 {task_row['status']}，只有 SUCCEEDED 的分析结果才能派单",
        )
    if analysis_type not in SUPPORTED_DISPATCH_TYPES:
        raise api_error("BT-70004", f"分析类型 {analysis_type or '未知'} 暂不支持派单")

    result_ref = task_row["result_ref"]
    if not result_ref:
        raise api_error("BT-70003", "源任务缺少 result_ref，无法读取分析结论")
    result = session.execute(
        text(
            "SELECT detail_json, algorithm_version FROM geometry_result "
            "WHERE id = CAST(:id AS uuid) AND task_id = :task_id"
        ),
        {"id": str(result_ref), "task_id": task_id},
    ).first()
    if result is None:
        raise api_error("BT-70003", f"找不到任务 {task_id} 的几何结果")
    result_row = _row(result)
    detail = cast(dict[str, Any], result_row["detail_json"] or {})
    if component_code != str(payload.get("component_code", component_code)):
        raise api_error("BT-70004", "请求构件与源任务构件不一致")
    return {
        "analysis_type": analysis_type,
        "algorithm_version": str(result_row["algorithm_version"]),
        "detail": detail,
    }


def dispatch(
    session: Session, principal: Principal, request: WorkOrderDispatchRequest
) -> dict[str, Any]:
    source = _geometry_result(session, request.source_task_id, request.component_code)
    detail = source["detail"]
    tilt_raw = detail.get("tilt_permille")
    if tilt_raw is None and detail.get("unit") == "permille":
        tilt_raw = detail.get("value")
    if not isinstance(tilt_raw, int | float):
        raise api_error("BT-70004", "源任务结果缺少 tilt_permille，不能生成缺陷")
    tilt = float(tilt_raw)

    team = session.execute(
        text(
            """
            SELECT t.id AS team_id, t.project_id, t.team_code, t.team_name, t.leader_id,
                   u.display_name AS leader_name, t.contact_phone, t.line_scope
            FROM maintenance_team t
            LEFT JOIN sys_user u ON u.id = t.leader_id
            WHERE t.id = :id AND t.deleted_at IS NULL
            """
        ),
        {"id": request.team_id},
    ).first()
    if team is None:
        raise api_error("BT-70006", f"维修班组不存在：{request.team_id}")
    team_row = _row(team)

    component = component_of(session, request.component_code, team_row["project_id"])
    if component is None:
        if component_of(session, request.component_code) is None:
            raise api_error("BT-30001", f"构件不存在：{request.component_code}")
        raise api_error("BT-70005", "班组与构件不在同一项目数据范围")
    if team_row["leader_id"] is None:
        raise api_error("BT-70007", "班组未配置负责人，无法确定工单受理人")

    thresholds = load_thresholds()
    suggested = thresholds.suggest(tilt)
    snapshot = {
        "task_id": str(request.source_task_id),
        "component_code": request.component_code,
        "analysis_type": source["analysis_type"],
        "tilt_permille": round(tilt, 3),
        "algorithm_version": source["algorithm_version"],
        "degraded": bool(detail.get("degraded", False)),
        "degrade_reason": detail.get("degrade_reason"),
        "captured_at": datetime.now(UTC).isoformat(),
    }

    operator_id = operator_uuid(principal)
    defect_code = f"TILT-{request.source_task_id.hex[:12].upper()}"
    existing = session.execute(
        text(
            "SELECT id, status FROM defect_ledger "
            "WHERE project_id = :project_id AND defect_code = :code AND deleted_at IS NULL"
        ),
        {"project_id": component["project_id"], "code": defect_code},
    ).first()

    if existing is None:
        defect_id = uuid.uuid4()
        session.execute(
            text(
                "INSERT INTO defect_ledger "
                "(id, project_id, defect_code, component_code, defect_type, level, status, "
                " measured_value, measured_unit, position_note, confirmed_by, confirmed_at) "
                "VALUES (:id, :project_id, :code, :component_code, "
                " CAST('TILT' AS defect_type), CAST(:level AS defect_level), "
                " CAST('CONFIRMED' AS ledger_status), :measured_value, 'permille', "
                " :position_note, :confirmed_by, now())"
            ),
            {
                "id": defect_id,
                "project_id": component["project_id"],
                "code": defect_code,
                "component_code": request.component_code,
                "level": suggested,
                "measured_value": round(tilt, 3),
                "position_note": (
                    f"几何分析 {source['analysis_type']} 自动生成；"
                    f"倾斜率 {tilt:.3f}‰；参考值，以最终人工复核及设计规范为准"
                ),
                "confirmed_by": operator_id,
            },
        )
    else:
        defect_id = cast(uuid.UUID, _row(existing)["id"])

    active = session.execute(
        text(
            "SELECT id FROM work_order WHERE defect_id = :defect_id AND deleted_at IS NULL "
            "AND status NOT IN ('CLOSED', 'CANCELLED', 'MERGED', 'REJECTED')"
        ),
        {"defect_id": defect_id},
    ).first()
    if active is not None:
        session.rollback()
        raise api_error("BT-70002", "该缺陷已有未结工单")

    order_no = _new_order_no(date.today())
    try:
        inserted = session.execute(
            text(
                "INSERT INTO work_order "
                "(project_id, order_no, defect_id, component_code, status, priority, "
                " handler_id, reporter_id, team_id, due_date, source_task_id, source_snapshot, "
                " remark, created_by) "
                "VALUES (:project_id, :order_no, :defect_id, :component_code, "
                " CAST('DRAFT' AS workorder_status), CAST(:priority AS notify_level), "
                " :handler_id, :reporter_id, :team_id, :due_date, :source_task_id, "
                " CAST(:snapshot AS jsonb), :remark, :created_by) "
                f"RETURNING {ORDER_COLUMNS}"
            ),
            {
                "project_id": component["project_id"],
                "order_no": order_no,
                "defect_id": defect_id,
                "component_code": request.component_code,
                "priority": request.priority.value,
                "handler_id": team_row["leader_id"],
                "reporter_id": operator_id,
                "team_id": request.team_id,
                "due_date": request.due_date,
                "source_task_id": request.source_task_id,
                "snapshot": _json(snapshot),
                "remark": request.remark,
                "created_by": operator_id,
            },
        )
        order_row = _row(inserted.one())
        if request.attachment_ids:
            # 有现场照片：附件改挂到工单后真实走 T01 SUBMIT，守卫读到真实附件行数
            from app.services.attachment import link_attachments

            try:
                link_attachments(
                    session,
                    attachment_ids=request.attachment_ids,
                    order_id=order_row["id"],
                    component_id=component["id"],
                    project_id=component["project_id"],
                )
                order_row = transition(
                    session,
                    principal,
                    order_row["id"],
                    WorkOrderTransitionRequest(
                        event=WorkOrderEventName.SUBMIT,
                        expected_version=0,
                        payload=TransitionPayload(reason="几何分析派发，附现场照片"),
                    ),
                )
            except Exception:
                session.rollback()
                raise
        else:
            # 无照片：系统代建（不伪造附件数），事件里记录口径供审计
            released = session.execute(
                text(
                    "UPDATE work_order SET status = 'PENDING_CONFIRM', version = version + 1, "
                    "sla_deadline = now() + interval '1440 minutes', updated_at = now() "
                    f"WHERE id = :id RETURNING {ORDER_COLUMNS}"  # noqa: S608
                ),
                {"id": order_row["id"]},
            )
            order_row = _row(released.one())
            _insert_event(
                session,
                order_id=order_row["id"],
                event="SUBMIT",
                from_status="DRAFT",
                to_status="PENDING_CONFIRM",
                operator_id=operator_id,
                reason="几何分析结果自动生成待确认工单",
                payload={
                    "initiated_by": "GEOMETRY_DISPATCH",
                    "attachments_count": 0,
                    "guard_note": "AI 派发无附件；T01 attachments_count 守卫按业务决策放宽",
                    "source_task_id": str(request.source_task_id),
                },
            )
            session.execute(
                text("UPDATE defect_ledger SET status = 'ORDER_CREATED' WHERE id = :id"),
                {"id": defect_id},
            )
            session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise api_error("BT-70002", "该缺陷已有未结工单") from exc

    response = dict(order_row)
    response["suggested_level"] = suggested
    response["threshold_note"] = thresholds.note
    response["team"] = team_row
    return response


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def dict_items(session: Session, type_code: str) -> list[dict[str, Any]]:
    """读 dict_item（含停用项，由前端按 enabled 展示）；契约 /dict/{type} 的形状。"""
    rows = session.execute(
        text(
            "SELECT item_code, item_name, sort_no, enabled FROM dict_item "
            "WHERE type_code = :type_code AND deleted_at IS NULL "
            "ORDER BY sort_no, item_code"
        ),
        {"type_code": type_code},
    ).all()
    return [
        {
            "code": row.item_code,
            "name": row.item_name,
            "sort_no": row.sort_no,
            "enabled": row.enabled,
        }
        for row in rows
    ]
