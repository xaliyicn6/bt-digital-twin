"""检测报告导出：HTML 渲染 + 导出审计（契约 ``/reports/{task_id}/export``）。

当前只做 HTML（无模板依赖，避免为一张报告引入 jinja2 运行时）。
PDF 需要服务端渲染引擎（WeasyPrint 等），未接入前不提供，也不假装能出 PDF。

报告里的技术状况等级取自 ``result.tech_grade``，其阈值是示例口径
（``tech_grade_source`` 非空即代表未核验）。报告页眉会原样展示该标注，
禁止把这份报告当作验收/正式交差文件（TODO(C2)(C3)）。

导出属"数据导出"，按 AGENTS.md 6 节写入 ``audit_log``（module=report，action=EXPORT）。
``audit_log`` 的 prev_hash + hash 链在这里按行顺序串接；并发导出时锁粒度不足，
属 P0 简化，接正式审计服务时统一改由 outbox/单写者补齐。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import html
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import current_trace_id, get_logger
from app.services.geometry_task import fetch_result_detail, get_task_row

log = get_logger()

# 界面与报告的占位标注统一口径：缺算法/缺真实数据时必须原样出现。
SCREENSHOT_PLACEHOLDER = "三维现场截图位（示例数据 · 算法待接入）"

# C8：前端 canvas.toDataURL('image/jpeg', 0.8) 上传；限制解码后 4MB、边长 100-4096。
ScreenshotPayload = str
MAX_SCREENSHOT_BYTES = 4 * 1024 * 1024
MIN_SHOT_SIDE = 100
MAX_SHOT_SIDE = 4096
_JPEG_DATA_PREFIX = "data:image/jpeg;base64,"
_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """从 JPEG 字节流解析 (width, height)，不依赖 Pillow。"""
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("截图必须是 JPEG（data:image/jpeg;base64）")
    index = 2
    size = len(data)
    while index + 1 < size:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker == 0xFF or marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        if index + 4 > size:
            break
        segment_length = int.from_bytes(data[index + 2 : index + 4], "big")
        if segment_length < 2:
            raise ValueError("JPEG 段长度非法")
        if marker in _JPEG_SOF_MARKERS:
            if index + 9 > size:
                break
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        index += 2 + segment_length
    raise ValueError("无法从 JPEG 读取尺寸")


def validate_screenshot(raw: str) -> ScreenshotPayload:
    """校验前端截图并返回可嵌入的 data URL；不合规抛 ValueError（→ BT-60005）。"""
    value = raw.strip()
    if value.startswith(_JPEG_DATA_PREFIX):
        value = value[len(_JPEG_DATA_PREFIX) :]
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("截图 Base64 非法") from exc
    if not decoded:
        raise ValueError("截图为空")
    if len(decoded) > MAX_SCREENSHOT_BYTES:
        raise ValueError(f"截图超过 {MAX_SCREENSHOT_BYTES // (1024 * 1024)}MB")
    width, height = jpeg_dimensions(decoded)
    if not (MIN_SHOT_SIDE <= width <= MAX_SHOT_SIDE and MIN_SHOT_SIDE <= height <= MAX_SHOT_SIDE):
        raise ValueError(f"截图尺寸 {width}x{height} 超出 {MIN_SHOT_SIDE}-{MAX_SHOT_SIDE} 像素范围")
    return f"{_JPEG_DATA_PREFIX}{value}"


@dataclass(frozen=True)
class ReportContext:
    task_id: uuid.UUID
    project_name: str
    component_code: str
    component_name: str
    analysis_type: str
    status: str
    result: dict[str, Any] | None
    created_at: datetime | None
    finished_at: datetime | None


def load_report_context(session: Session, task_id: uuid.UUID) -> ReportContext | None:
    """取报告所需的只读上下文；任务不存在返回 None（由路由转 BT-60003）。"""
    row = get_task_row(session, task_id)
    if row is None:
        return None
    payload = row["payload"]
    component_code = str(payload.get("component_code", ""))
    meta = session.execute(
        text(
            "SELECT c.component_name, p.project_name FROM component c "
            "JOIN project p ON p.id = c.project_id "
            "WHERE c.component_code = :code AND c.deleted_at IS NULL"
        ),
        {"code": component_code},
    ).first()
    return ReportContext(
        task_id=task_id,
        project_name="" if meta is None else str(meta[1]),
        component_code=component_code,
        component_name="" if meta is None else str(meta[0]),
        analysis_type=str(payload.get("analysis_type", "")),
        status=str(row["status"]),
        result=fetch_result_detail(session, task_id),
        created_at=row["created_at"],
        finished_at=row["finished_at"],
    )


def _e(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return html.escape(str(value))


def _time(value: datetime | None) -> str:
    return "—" if value is None else value.strftime("%Y-%m-%d %H:%M:%S")


def _metric_text(result: dict[str, Any] | None) -> str:
    if not result:
        return "无结构化结果"
    value = result.get("value")
    unit = result.get("unit")
    if value is None:
        return "无结构化结果"
    return f"{_e(value)} {_e(unit)}"


def _rows(result: dict[str, Any] | None) -> list[tuple[str, str]]:
    if not result:
        return [("量测结论", "无结构化结果（示例数据 · 算法待接入）")]
    rows: list[tuple[str, str]] = [
        ("量测结论", _metric_text(result)),
        ("倾斜率", f"{_e(result.get('tilt_permille'))} ‰"),
        ("技术状况等级", _e(result.get("tech_grade_label") or result.get("tech_grade"))),
        ("等级阈值来源", _e(result.get("tech_grade_source") or "—")),
        (
            "规范依据",
            _e(result.get("standard_reference")),
        ),
        ("算法版本", _e(result.get("algorithm_version"))),
        ("有效层数", _e(result.get("valid_layers"))),
        ("残差 RMS", f"{_e(result.get('residual_rms_mm'))} mm"),
    ]
    if result.get("intrusion_depth_mm") is not None:
        rows.extend(
            [
                ("最大侵限深度", f"{_e(result.get('intrusion_depth_mm'))} mm"),
                ("侵限面积", f"{_e(result.get('intrusion_area_sqm'))} m²"),
                ("最不利断面里程", _e(result.get("worst_section_stake"))),
            ]
        )
    if result.get("degraded"):
        rows.append(("降级说明", _e(result.get("degrade_reason"))))
    return rows


def render_report_html(
    ctx: ReportContext, screenshot_data_url: ScreenshotPayload | None = None
) -> str:
    """纯函数：上下文 → 打印友好的 HTML（不碰 DB，便于单测）。

    ``screenshot_data_url`` 由路由层先经 ``validate_screenshot`` 校验；
    未提供时保留占位框并明确标注算法/截图待接入。
    """
    result = ctx.result
    grade = "" if not result else str(result.get("tech_grade_label") or "")
    source = "" if not result else str(result.get("tech_grade_source") or "")
    reference = "" if not result else str(result.get("standard_reference") or "")

    table_rows = "".join(
        f"<tr><th>{_e(name)}</th><td>{value}</td></tr>" for name, value in _rows(result)
    )
    grade_block = (
        f'<p class="grade">{_e(grade)}</p>' if grade else '<p class="grade muted">等级未产出</p>'
    )
    source_note = (
        f'<div class="warn">等级阈值来源：{_e(source)}。该等级仅用于界面/报告流程演示，'
        "不得作为验收依据。</div>"
        if source
        else ""
    )
    reference_block = f"<p>{_e(reference)}</p>" if reference else "<p>规范依据待补充。</p>"
    degraded_note = (
        '<div class="warn">本次结果包含降级/示例数据，禁止用于验收。</div>'
        if result and result.get("degraded")
        else ""
    )
    shot_block = (
        f'<img class="shot-img" src="{screenshot_data_url}" alt="三维现场截图">'
        if screenshot_data_url
        else f'<div class="shot">{SCREENSHOT_PLACEHOLDER}</div>'
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>检测报告 {_e(ctx.task_id)}</title>
<style>
  body {{ font-family: "Microsoft YaHei", sans-serif; color: #1f2937; margin: 32px; }}
  h1 {{ font-size: 24px; margin: 0 0 4px; }}
  .sub {{ color: #6b7280; margin-bottom: 24px; }}
  .cover {{ border: 1px solid #d1d5db; padding: 24px; margin-bottom: 24px; }}
  .cover .stamp {{ margin-top: 32px; color: #9ca3af; }}
  table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
  th, td {{ border: 1px solid #d1d5db; padding: 8px 12px; text-align: left; font-size: 13px; }}
  th {{ width: 180px; background: #f3f4f6; }}
  .grade {{ font-size: 22px; font-weight: 700; color: #b45309; }}
  .muted {{ color: #9ca3af; }}
  .warn {{ border-left: 4px solid #f59e0b; background: #fffbeb; padding: 8px 12px;
           font-size: 12px; margin: 12px 0; }}
  .shot {{ border: 1px dashed #9ca3af; height: 180px; display: flex; align-items: center;
           justify-content: center; color: #9ca3af; margin: 16px 0; }}
  .shot-img {{ width: 100%; border: 1px solid #d1d5db; margin: 16px 0; }}
  footer {{ margin-top: 32px; color: #9ca3af; font-size: 12px; }}
</style>
</head>
<body>
  <div class="cover">
    <h1>桥隧结构检测报告</h1>
    <div class="sub">项目：{_e(ctx.project_name)}</div>
    <div>构件编码：{_e(ctx.component_code)}</div>
    <div>构件名称：{_e(ctx.component_name)}</div>
    <div>分析类型：{_e(ctx.analysis_type)}</div>
    <div>任务 ID：{_e(ctx.task_id)}</div>
    <div>任务时间：{_time(ctx.created_at)} → {_time(ctx.finished_at)}</div>
    <div class="stamp">（盖章处）</div>
  </div>

  <h2>量测与评定</h2>
  {grade_block}
  {source_note}
  <table>{table_rows}</table>

  <h2>三维现场截图</h2>
  {shot_block}

  <h2>规范评定结论</h2>
  {reference_block}
  {degraded_note}

  <footer>本报告由系统自动生成；含"示例数据/示例阈值"标注时不得用于验收。</footer>
</body>
</html>
"""


def _audit_hash(prev_hash: str | None, body: dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"{prev_hash or ''}{canonical}".encode()).hexdigest()


def _insert_audit(
    session: Session,
    *,
    subject: str,
    object_type: str,
    object_id: str,
    detail: dict[str, Any],
) -> None:
    trace_id = current_trace_id() or ""
    body: dict[str, Any] = {
        "trace_id": trace_id,
        "module": "report",
        "action": "EXPORT",
        "object_type": object_type,
        "object_id": object_id,
        "result": "SUCCESS",
        "detail": detail,
    }
    prev = session.execute(text("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1")).first()
    prev_hash = None if prev is None else str(prev[0])
    user_id: str | None
    try:
        user_id = str(uuid.UUID(subject))
    except ValueError:
        user_id = None
    session.execute(
        text(
            "INSERT INTO audit_log (trace_id, user_id, module, action, object_type, object_id, "
            "result, detail, prev_hash, hash) VALUES (:trace_id, :user_id, :module, :action, "
            ":object_type, :object_id, :result, :detail, :prev_hash, :hash)"
        ),
        {
            "trace_id": trace_id[:64],
            "user_id": user_id,
            "module": body["module"],
            "action": body["action"],
            "object_type": body["object_type"],
            "object_id": body["object_id"],
            "result": body["result"],
            "detail": json.dumps(body["detail"], ensure_ascii=False),
            "prev_hash": prev_hash,
            "hash": _audit_hash(prev_hash, body),
        },
    )


def record_export_audit(
    session: Session, *, task_id: uuid.UUID, subject: str, filename: str
) -> None:
    """写 audit_log（只追加）。user_id 非 UUID（如 dev-anonymous）时落 NULL。"""
    _insert_audit(
        session,
        subject=subject,
        object_type="geometry_task",
        object_id=str(task_id),
        detail={"filename": filename, "format": "html"},
    )
    log.info(
        "report exported",
        extra={"bt_extra": {"taskId": str(task_id), "filename": filename}},
    )


def record_batch_export_audit(
    session: Session, *, task_ids: list[uuid.UUID], subject: str, filename: str
) -> None:
    """批量 CSV 导出审计：object_id 取批次首条任务，detail 记录条数。"""
    _insert_audit(
        session,
        subject=subject,
        object_type="geometry_task_batch",
        object_id=str(task_ids[0]),
        detail={"filename": filename, "format": "csv", "count": len(task_ids)},
    )
    log.info(
        "batch report exported",
        extra={"bt_extra": {"count": len(task_ids), "filename": filename}},
    )
