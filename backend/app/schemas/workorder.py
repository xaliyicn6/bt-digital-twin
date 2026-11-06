"""M07 工单 API 请求/响应模型（字段名以 contracts/openapi.yaml 为准）。"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class WorkOrderStatus(str, Enum):
    """与 db/schema.sql 的 workorder_status 枚举逐字一致。"""

    DRAFT = "DRAFT"
    PENDING_CONFIRM = "PENDING_CONFIRM"
    CONFIRMED = "CONFIRMED"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_ACCEPT = "PENDING_ACCEPT"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"
    OVERDUE = "OVERDUE"
    CANCELLED = "CANCELLED"
    MERGED = "MERGED"


class WorkOrderPriority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class WorkOrderEventName(str, Enum):
    """契约里允许由 API 触发的事件；TIMEOUT/升级只允许 SYSTEM 定时器。"""

    SUBMIT = "SUBMIT"
    CANCEL = "CANCEL"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REVISE = "REVISE"
    ASSIGN = "ASSIGN"
    ACCEPT_TASK = "ACCEPT_TASK"
    REASSIGN = "REASSIGN"
    SUSPEND = "SUSPEND"
    RESUME = "RESUME"
    SUBMIT_REPAIR = "SUBMIT_REPAIR"
    ACCEPT = "ACCEPT"
    RETURN = "RETURN"
    MERGE = "MERGE"
    ESCALATE_L2 = "ESCALATE_L2"
    ESCALATE_L3 = "ESCALATE_L3"


class WorkOrder(BaseModel):
    id: uuid.UUID
    order_no: str
    status: WorkOrderStatus
    version: int
    priority: WorkOrderPriority = WorkOrderPriority.P2
    defect_id: uuid.UUID | None = None
    component_code: str | None = None
    handler_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    due_date: date | None = None
    source_task_id: uuid.UUID | None = None
    source_snapshot: dict[str, Any] | None = None
    sla_deadline: datetime | None = None
    sla_paused_total: int = 0
    is_overdue: bool = False
    created_at: datetime
    closed_at: datetime | None = None


class WorkOrderEvent(BaseModel):
    id: uuid.UUID
    event: str
    from_status: str
    to_status: str
    operator_id: uuid.UUID | None = None
    reason: str | None = None
    occurred_at: datetime


class WorkOrderAttachment(BaseModel):
    id: uuid.UUID
    file_name: str
    content_type: str


class WorkOrderDetail(WorkOrder):
    events: list[WorkOrderEvent] = Field(default_factory=list)
    attachments: list[WorkOrderAttachment] = Field(default_factory=list)


class WorkOrderList(BaseModel):
    items: list[WorkOrder]
    next_cursor: str | None = None


class WorkOrderCreate(BaseModel):
    defect_id: uuid.UUID
    component_code: str = Field(min_length=1, max_length=64)
    handler_id: uuid.UUID
    due_date: date | None = None
    remark: str | None = Field(default=None, max_length=2000)


class TransitionPayload(BaseModel):
    reason: str | None = None
    handler_id: uuid.UUID | None = None
    merge_into: uuid.UUID | None = None
    suspend_reason: str | None = None


class WorkOrderTransitionRequest(BaseModel):
    event: WorkOrderEventName
    expected_version: int = Field(ge=0)
    payload: TransitionPayload | None = None


class WorkOrderAcceptanceRequest(BaseModel):
    result: str = Field(pattern="^(PASS|RETURN)$")
    comment: str | None = None
    attachment_ids: list[uuid.UUID] = Field(default_factory=list)


class WorkOrderDispatchRequest(BaseModel):
    source_task_id: uuid.UUID
    component_code: str = Field(min_length=1, max_length=64)
    priority: WorkOrderPriority
    team_id: uuid.UUID
    due_date: date | None = None
    remark: str | None = Field(default=None, max_length=200)


class MaintenanceTeamMember(BaseModel):
    user_id: uuid.UUID
    username: str
    role_in_team: str | None = None


class MaintenanceTeam(BaseModel):
    team_id: uuid.UUID
    project_id: uuid.UUID
    team_code: str
    team_name: str
    leader_id: uuid.UUID | None = None
    leader_name: str | None = None
    contact_phone: str | None = None
    line_scope: str | None = None
    member_count: int = 0
    members: list[MaintenanceTeamMember] = Field(default_factory=list)


class MaintenanceTeamList(BaseModel):
    items: list[MaintenanceTeam]
    next_cursor: str | None = None


class WorkOrderDispatchResult(WorkOrder):
    suggested_level: str
    threshold_note: str
    team: MaintenanceTeam


class DefectLevelThresholdsOut(BaseModel):
    version: str
    note: str
    tilt_permille: dict[str, Any]
