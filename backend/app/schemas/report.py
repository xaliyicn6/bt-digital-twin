"""报告导出请求模型（契约 ``POST /reports/{task_id}/export``）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ReportExportRequest(BaseModel):
    """截图可选：前端 ``viewer.canvas.toDataURL('image/jpeg', 0.8)`` 的 Base64。

    这里只做长度上限（防止超大报文进内存）；真正的 JPEG 魔数、尺寸与解码校验
    在 ``services/report.py`` 的 ``validate_screenshot`` 里做（失败 → BT-60005）。
    """

    format: Literal["html"] = "html"
    screenshot_base64: str | None = Field(
        default=None,
        max_length=6_000_000,
        description="data:image/jpeg;base64,... 或裸 Base64；≤ 6,000,000 字符",
    )
