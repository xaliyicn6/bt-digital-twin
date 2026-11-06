"""检测报告路由（契约 ``/reports/{task_id}/export``）。

只做参数解析与响应组装；渲染与审计逻辑在 ``app/services/report.py``。
``GET`` 兼容无截图导出；``POST`` 承载前端 ``canvas.toDataURL`` 的三维截图（C8）。
当前仅支持 ``format=html``；PDF 渲染引擎未接入，契约里也不给出 pdf 枚举值。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, Response

from app.api.deps import CurrentUser, DbSession
from app.core.errors import api_error
from app.schemas.report import ReportExportRequest
from app.services.batch_report import build_batch_csv
from app.services.report import (
    load_report_context,
    record_batch_export_audit,
    record_export_audit,
    render_report_html,
    validate_screenshot,
)

router = APIRouter(prefix="/reports", tags=["reports"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "未认证"},
    404: {"description": "任务不存在（BT-60003）"},
    409: {"description": "任务未成功，不能出报告（BT-70003）"},
    422: {"description": "截图不合规（BT-60005）"},
}


def _parse_task_ids(raw: str) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        try:
            ids.append(uuid.UUID(value))
        except ValueError as exc:
            raise api_error("BT-60006", f"非法任务 id：{value}") from exc
    if not ids:
        raise api_error("BT-60006", "至少提供 1 个任务 id")
    if len(ids) > 100:
        raise api_error("BT-60006", f"单次最多导出 100 条，实际 {len(ids)}")
    return ids


@router.get(
    "/batch/export",
    summary="批量导出结果汇总 CSV（UTF-8 BOM，Excel 可直接打开）",
    responses={
        401: {"description": "未认证"},
        422: {"description": "task_ids 缺失/非法/超 100 条（BT-60006）"},
    },
)
def export_batch_report(
    db: DbSession,
    user: CurrentUser,
    task_ids: Annotated[str, Query(description="逗号分隔的几何任务 id（1–100 条）", min_length=1)],
) -> Response:
    ids = _parse_task_ids(task_ids)
    filename = "batch-report.csv"
    content = build_batch_csv(db, ids)
    record_batch_export_audit(db, task_ids=ids, subject=user.subject, filename=filename)
    db.commit()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _export(
    db: DbSession,
    user: CurrentUser,
    task_id: uuid.UUID,
    report_format: str,
    screenshot_base64: str | None,
) -> Response:
    ctx = load_report_context(db, task_id)
    if ctx is None:
        raise api_error("BT-60003", f"几何任务不存在：{task_id}")
    if ctx.status != "SUCCEEDED":
        raise api_error("BT-70003", f"源任务未成功（当前 {ctx.status}），不能生成报告")
    screenshot: str | None = None
    if screenshot_base64:
        try:
            screenshot = validate_screenshot(screenshot_base64)
        except ValueError as exc:
            raise api_error("BT-60005", str(exc)) from exc
    filename = f"report-{task_id}.{report_format}"
    html_body = render_report_html(ctx, screenshot)
    record_export_audit(db, task_id=task_id, subject=user.subject, filename=filename)
    db.commit()
    return Response(
        content=html_body,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{task_id}/export", summary="导出检测报告（HTML，附件下载）", responses=_ERRORS)
def export_report(
    db: DbSession,
    user: CurrentUser,
    task_id: Annotated[uuid.UUID, Path(description="几何分析任务 id")],
    report_format: Annotated[
        Literal["html"], Query(alias="format", description="报告格式，当前仅 html")
    ] = "html",
) -> Response:
    return _export(db, user, task_id, report_format, None)


@router.post(
    "/{task_id}/export",
    summary="导出检测报告（HTML，可带前端三维截图）",
    responses=_ERRORS,
)
def export_report_with_screenshot(
    db: DbSession,
    user: CurrentUser,
    task_id: Annotated[uuid.UUID, Path(description="几何分析任务 id")],
    body: ReportExportRequest | None = None,
) -> Response:
    payload = body or ReportExportRequest()
    return _export(db, user, task_id, payload.format, payload.screenshot_base64)
