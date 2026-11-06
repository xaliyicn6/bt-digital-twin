"""几何分析任务路由（契约 contracts/openapi.yaml: /geometry/tasks 与 /geometry/tasks/{id}）。

主路径只做三件事：校验幂等键与构件、落一行 ``async_task``（``QUEUED``）、立刻 202。
真正的 numpy 计算由独立进程 ``python -m app.workers.geometry_worker`` 领取执行；
``GEOMETRY_TASK_INLINE=1`` 才用 ``BackgroundTasks`` 在 API 进程内兜底（**仅限本地开发**）。
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, Path, Response, status

from app.api.deps import CurrentUser, DbSession
from app.core.errors import api_error
from app.db.session import get_session_factory
from app.schemas.geometry import (
    BatchTaskAccepted,
    GeometryTaskBatchCreate,
    GeometryTaskCreate,
    GeometryTaskDetail,
    TaskAccepted,
    TaskStatus,
)
from app.services.geometry_task import (
    create_task,
    fetch_result_detail,
    find_component,
    get_task_row,
    inline_enabled,
    run_task_now,
    task_location,
)

router = APIRouter(prefix="/geometry/tasks", tags=["geometry"])

IDEM_KEY_MIN = 8
IDEM_KEY_MAX = 64


def _require_idempotency_key(raw: str | None) -> str:
    """契约把缺失/超长定义为 400 BT-10002；交给 pydantic 会变成 422 BT-10003。"""
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
    "",
    response_model=TaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="提交几何分析任务（异步执行，返回任务 id 与查询地址）",
    responses={
        400: {"description": "缺少或非法的 Idempotency-Key（BT-10002）"},
        401: {"description": "未认证"},
        404: {"description": "构件不存在（BT-30001）"},
        422: {"description": "analysis_type 非法或构件编码跨项目重名（BT-30003）"},
    },
)
def create_geometry_task(
    db: DbSession,
    background_tasks: BackgroundTasks,
    response: Response,
    _user: CurrentUser,
    payload: GeometryTaskCreate,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", description="幂等键，8–64 位")
    ] = None,
) -> TaskAccepted:
    key = _require_idempotency_key(idempotency_key)
    component = find_component(db, payload.component_code)
    task_id, task_status, created_at, _replayed = create_task(
        db, component=component, payload=payload.model_dump(mode="json"), idem_key=key
    )
    db.commit()
    response.headers["Location"] = task_location(task_id)
    if inline_enabled():
        background_tasks.add_task(run_task_now, get_session_factory(), task_id)
    return TaskAccepted(task_id=task_id, status=TaskStatus(task_status), created_at=created_at)


def _batch_idempotency_key(base: str, index: int) -> str:
    """由批次幂等键派生单条键（≤64 位），保证重放同一批得到同一组 task_id。"""
    digest = hashlib.sha256(f"{base}:{index}".encode()).hexdigest()[:12]
    return f"{base[:48]}-{digest}{index:02d}"


@router.post(
    "/batch",
    response_model=BatchTaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="批量提交几何分析任务（一次 1–50 条）",
    responses={
        400: {"description": "缺少或非法的 Idempotency-Key（BT-10002）"},
        401: {"description": "未认证"},
        404: {"description": "任一构件不存在（BT-30001），整批不落库"},
        422: {"description": "items 为空/超 50 条或单条 analysis_type 非法"},
    },
)
def create_geometry_tasks_batch(
    db: DbSession,
    background_tasks: BackgroundTasks,
    _user: CurrentUser,
    payload: GeometryTaskBatchCreate,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", description="批次幂等键，8–64 位")
    ] = None,
) -> BatchTaskAccepted:
    key = _require_idempotency_key(idempotency_key)
    # 先整体校验构件，避免"前 2 条已落库、第 3 条失败"的半批脏数据。
    components = [find_component(db, item.component_code) for item in payload.items]
    accepted: list[TaskAccepted] = []
    for index, (item, component) in enumerate(zip(payload.items, components, strict=True)):
        task_id, task_status, created_at, _replayed = create_task(
            db,
            component=component,
            payload=item.model_dump(mode="json"),
            idem_key=_batch_idempotency_key(key, index),
        )
        accepted.append(
            TaskAccepted(
                task_id=task_id,
                status=TaskStatus(task_status),
                created_at=created_at,
            )
        )
    db.commit()
    if inline_enabled():
        for accepted_item in accepted:
            background_tasks.add_task(run_task_now, get_session_factory(), accepted_item.task_id)
    return BatchTaskAccepted(items=accepted)


@router.get(
    "/{task_id}",
    response_model=GeometryTaskDetail,
    summary="查询几何分析任务进度与结果",
    responses={404: {"description": "任务不存在（BT-60003）"}},
)
def get_geometry_task(
    db: DbSession,
    _user: CurrentUser,
    task_id: Annotated[uuid.UUID, Path(description="受理时返回的 task_id")],
) -> GeometryTaskDetail:
    row = get_task_row(db, task_id)
    if row is None:
        raise api_error("BT-60003", f"几何任务不存在：{task_id}")
    result = fetch_result_detail(db, task_id) if row["status"] == "SUCCEEDED" else None
    return GeometryTaskDetail(
        task_id=row["id"],
        status=row["status"],
        analysis_type=row["payload"].get("analysis_type", "VERTICALITY"),
        progress=row["progress"],
        result=result,
        error_code=row["error_code"],
        error_detail=row["error_detail"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )
