"""M07 工单路由：补齐既有 5 个端点 + 分析派发 + 班组 + 阈值字典。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, status

from app.api.deps import CurrentUser, DbSession
from app.core.errors import api_error
from app.core.logging import current_trace_id, get_logger
from app.schemas.workorder import (
    DefectLevelThresholdsOut,
    MaintenanceTeam,
    MaintenanceTeamList,
    WorkOrder,
    WorkOrderAcceptanceRequest,
    WorkOrderAttachment,
    WorkOrderCreate,
    WorkOrderDetail,
    WorkOrderDispatchRequest,
    WorkOrderDispatchResult,
    WorkOrderEvent,
    WorkOrderList,
    WorkOrderTransitionRequest,
)
from app.services import workorder as service
from app.services.defect_levels import load_thresholds

log = get_logger()

router = APIRouter(prefix="/workorders", tags=["workorder"])
teams_router = APIRouter(prefix="/maintenance/teams", tags=["workorder"])
dict_router = APIRouter(prefix="/dict", tags=["admin"])

IDEM_KEY_MIN = 8
IDEM_KEY_MAX = 64


def _require_idempotency_key(raw: str | None) -> str:
    key = (raw or "").strip()
    if not key:
        raise api_error("BT-10002", "缺少 Idempotency-Key 请求头（8–64 位）")
    if not IDEM_KEY_MIN <= len(key) <= IDEM_KEY_MAX:
        raise api_error(
            "BT-10002",
            f"Idempotency-Key 长度必须在 {IDEM_KEY_MIN}–{IDEM_KEY_MAX} 之间，实际 {len(key)}",
        )
    return key


@router.post(
    "/dispatch",
    response_model=WorkOrderDispatchResult,
    status_code=status.HTTP_201_CREATED,
    summary="从几何分析结果生成待确认工单（缺陷 + PENDING_CONFIRM，不直接派工）",
    responses={
        403: {"description": "班组超出数据范围（BT-70005）"},
        404: {"description": "源任务/构件/班组不存在"},
        409: {"description": "源任务未成功（BT-70003）或重复建单（BT-70002）"},
        422: {"description": "分析类型不支持派单（BT-70004）或班组未配置负责人（BT-70007）"},
    },
)
def dispatch_work_order(
    db: DbSession,
    user: CurrentUser,
    payload: WorkOrderDispatchRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> WorkOrderDispatchResult:
    _require_idempotency_key(idempotency_key)
    result = service.dispatch(db, user, payload)
    log.info(
        "workorder dispatched",
        extra={
            "bt_extra": {
                "trace_id": current_trace_id(),
                "order_no": result["order_no"],
                "source_task_id": str(payload.source_task_id),
                "priority": payload.priority.value,
            }
        },
    )
    return WorkOrderDispatchResult(**result)


@router.get(
    "",
    response_model=WorkOrderList,
    summary="工单列表（分页默认 20，上限 100）",
)
def list_work_orders(
    db: DbSession,
    user: CurrentUser,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    component_code: Annotated[str | None, Query(max_length=64)] = None,
    mine: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> WorkOrderList:
    handler_id = service.operator_uuid(user) if mine else None
    items, next_cursor = service.list_orders(
        db,
        status=status_filter,
        component_code=component_code,
        handler_id=handler_id,
        limit=limit,
        cursor=cursor,
    )
    return WorkOrderList(items=[WorkOrder(**item) for item in items], next_cursor=next_cursor)


@router.post(
    "",
    response_model=WorkOrder,
    status_code=status.HTTP_201_CREATED,
    summary="创建工单（缺陷须为 CONFIRMED，落 DRAFT 等待提交）",
    responses={
        404: {"description": "缺陷或构件不存在"},
        409: {"description": "缺陷未确认或重复建单（BT-70002）"},
    },
)
def create_work_order(db: DbSession, user: CurrentUser, payload: WorkOrderCreate) -> WorkOrder:
    return WorkOrder(**service.create_order(db, user, payload))


@router.get(
    "/{order_id}",
    response_model=WorkOrderDetail,
    summary="工单详情（含流转事件与附件）",
    responses={404: {"description": "工单不存在"}},
)
def get_work_order(db: DbSession, order_id: str) -> WorkOrderDetail:
    parsed = _parse_order_id(order_id)
    order = service.get_order(db, parsed)
    events = [WorkOrderEvent(**item) for item in service.order_events(db, parsed)]
    attachments = [WorkOrderAttachment(**item) for item in service.order_attachments(db, parsed)]
    return WorkOrderDetail(**order, events=events, attachments=attachments)


def _parse_order_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise api_error("BT-10003", f"工单 id 非法：{raw}") from exc


@router.post(
    "/{order_id}/transitions",
    response_model=WorkOrder,
    summary="工单状态流转（按 work_order_state_machine.yaml）",
    responses={
        409: {"description": "非法迁移或版本冲突（BT-70001）"},
    },
)
def transition_work_order(
    db: DbSession, user: CurrentUser, order_id: str, payload: WorkOrderTransitionRequest
) -> WorkOrder:
    return WorkOrder(**service.transition(db, user, _parse_order_id(order_id), payload))


@router.post(
    "/{order_id}/acceptance",
    response_model=WorkOrder,
    summary="验收：PASS 关闭工单 / RETURN 退回返修",
)
def accept_work_order(
    db: DbSession, user: CurrentUser, order_id: str, payload: WorkOrderAcceptanceRequest
) -> WorkOrder:
    return WorkOrder(**service.acceptance(db, user, _parse_order_id(order_id), payload))


@teams_router.get("", response_model=MaintenanceTeamList, summary="维修班组列表（按数据范围过滤）")
def list_teams(
    db: DbSession,
    user: CurrentUser,
    component_code: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> MaintenanceTeamList:
    project_id = None
    if component_code:
        component = service.component_of(db, component_code)
        project_id = None if component is None else component["project_id"]
    items, next_cursor = service.list_teams(db, project_id=project_id, limit=limit, cursor=cursor)
    return MaintenanceTeamList(
        items=[MaintenanceTeam(**item) for item in items], next_cursor=next_cursor
    )


@dict_router.get(
    "/defect-level-thresholds",
    response_model=DefectLevelThresholdsOut,
    summary="倾斜率缺陷等级阈值（字典配置，可调）",
)
def defect_level_thresholds() -> DefectLevelThresholdsOut:
    thresholds = load_thresholds()
    return DefectLevelThresholdsOut(**thresholds.as_dict())
