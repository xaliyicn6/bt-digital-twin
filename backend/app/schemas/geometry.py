"""M05 API-14 几何分析任务的请求/响应模型（字段名以 contracts/openapi.yaml 为准）。

契约现状（`/geometry/tasks`，服务器基址 ``/api/v1``）：
    POST 请求体 required [component_code, analysis_type]，
    ``analysis_type`` ∈ {VERTICALITY, CLEARANCE, SECTION_SHAPE}，可带
    ``pointcloud_id`` / ``baseline_id``；202 返回 ``TaskAccepted``。
    注意：设计说明书 9.3.4 写的是 ``component_id`` + ``PLUMB/TUNNEL_CLEARANCE``，
    与契约不一致；按 AGENTS.md「契约是唯一事实源」以契约为准，文档差异单独上报。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AnalysisType(str, Enum):
    """与 ``db/schema.sql`` 的 ``analysis_type`` 枚举逐字一致。"""

    VERTICALITY = "VERTICALITY"
    CLEARANCE = "CLEARANCE"
    SECTION_SHAPE = "SECTION_SHAPE"


class TaskStatus(str, Enum):
    """与 ``db/schema.sql`` 的 ``task_status`` 枚举逐字一致（不是 PENDING/COMPLETED）。"""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


class GeometryTaskCreate(BaseModel):
    component_code: str = Field(min_length=1, max_length=64, description="构件编码")
    analysis_type: AnalysisType
    pointcloud_id: uuid.UUID | None = Field(default=None, description="点云文件 id（可空）")
    baseline_id: uuid.UUID | None = Field(default=None, description="设计断面基线 id（本轮未使用）")


class TaskAccepted(BaseModel):
    task_id: uuid.UUID
    status: TaskStatus
    created_at: datetime


class BatchTaskAccepted(BaseModel):
    """批量受理结果：按请求顺序返回每条的 task_id（重复提交返回首次结果）。"""

    items: list[TaskAccepted]


class GeometryTaskBatchCreate(BaseModel):
    """批量提交：1–50 条，逐条按单任务规则校验构件与 analysis_type。"""

    items: list[GeometryTaskCreate] = Field(min_length=1, max_length=50)


class GeometryTaskDetail(BaseModel):
    task_id: uuid.UUID
    status: TaskStatus
    analysis_type: AnalysisType
    progress: int = Field(ge=0, le=100, description="仅展示，不作为状态判据")
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_detail: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
