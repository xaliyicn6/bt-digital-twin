"""批量结果汇总 CSV（UTF-8 BOM，Excel 双击即可正确识别中文）。

只读 ``async_task`` 与结果明细，不改任何表；任务不存在时该行状态写 NOT_FOUND，
保证"导出 10 条"不会因为个别缺失而整份失败。
"""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.services.geometry_task import fetch_result_detail, get_task_row

HEADERS = [
    "任务ID",
    "构件编码",
    "分析类型",
    "状态",
    "倾斜率(‰)",
    "水平偏移(mm)",
    "技术等级",
    "等级来源",
    "最不利断面",
    "错误码",
    "完成时间",
]


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def build_batch_csv(session: Session, task_ids: list[uuid.UUID]) -> str:
    """生成 CSV 文本（含 BOM 前缀）。"""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    for task_id in task_ids:
        row = get_task_row(session, task_id)
        if row is None:
            writer.writerow([str(task_id), "", "", "NOT_FOUND", "", "", "", "", "", "", ""])
            continue
        payload = row["payload"]
        result = fetch_result_detail(session, task_id) if row["status"] == "SUCCEEDED" else None
        result = result or {}
        finished = row["finished_at"]
        writer.writerow(
            [
                str(task_id),
                _text(payload.get("component_code")),
                _text(payload.get("analysis_type")),
                _text(row["status"]),
                _text(result.get("tilt_permille")),
                _text(result.get("horizontal_offset_mm")),
                _text(result.get("tech_grade_label") or result.get("tech_grade")),
                _text(result.get("tech_grade_source")),
                _text(result.get("worst_section_stake")),
                _text(row["error_code"]),
                "" if finished is None else finished.strftime("%Y-%m-%d %H:%M:%S"),
            ]
        )
    return "\ufeff" + buffer.getvalue()
