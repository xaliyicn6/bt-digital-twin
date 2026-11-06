/**
 * 工单派发的最小客户端（契约 contracts/openapi.yaml: /workorders/dispatch、
 * /maintenance/teams、/dict/defect-level-thresholds）。
 *
 * 口径：
 *   POST 201 → WorkOrderDispatchResult：生成的是 PENDING_CONFIRM 工单，
 *             主管在后台点「通过」(T03 APPROVE) 后才真正下发。
 *   suggested_level 由后端按阈值字典推导，前端只做展示与预选，不落库、不改阈值。
 *
 * 请求只经本文件，组件不手写 fetch（AGENTS.md 前端规范）。
 */

export type WorkOrderPriority = "P1" | "P2" | "P3";

export type DefectLevel = "L1" | "L2" | "L3";

export type WorkOrderStatus =
  | "DRAFT"
  | "PENDING_CONFIRM"
  | "CONFIRMED"
  | "ASSIGNED"
  | "IN_PROGRESS"
  | "PENDING_ACCEPT"
  | "CLOSED"
  | "REJECTED"
  | "SUSPENDED"
  | "OVERDUE"
  | "CANCELLED"
  | "MERGED";

export interface MaintenanceTeamMember {
  user_id: string;
  username: string;
  role_in_team?: string | null;
}

export interface MaintenanceTeam {
  team_id: string;
  project_id?: string;
  team_code: string;
  team_name: string;
  leader_id?: string | null;
  leader_name?: string | null;
  contact_phone?: string | null;
  line_scope?: string | null;
  member_count: number;
  members?: MaintenanceTeamMember[];
}

export interface MaintenanceTeamList {
  items: MaintenanceTeam[];
  next_cursor?: string | null;
}

export interface DefectLevelThresholds {
  version: string;
  note: string;
  tilt_permille: {
    L1: number;
    L2: number;
    default_level: DefectLevel;
  };
}

export interface WorkOrder {
  id: string;
  order_no: string;
  status: WorkOrderStatus;
  version: number;
  priority: WorkOrderPriority;
  defect_id?: string | null;
  component_code?: string | null;
  component_name?: string | null;
  handler_id?: string | null;
  reporter_id?: string | null;
  team_id?: string | null;
  team_name?: string | null;
  suggested_level?: DefectLevel | null;
  due_date?: string | null;
  source_task_id?: string | null;
  source_snapshot?: Record<string, unknown> | null;
  sla_deadline?: string | null;
  is_overdue?: boolean;
  created_at: string;
}

export interface WorkOrderList {
  items: WorkOrder[];
  next_cursor?: string | null;
}

export interface WorkOrderDispatchRequest {
  source_task_id: string;
  component_code: string;
  priority: WorkOrderPriority;
  team_id: string;
  due_date?: string | null;
  remark?: string | null;
  /** 预上传的现场照片 id（POST /attachments 返回），用于真正穿过 T01 附件守卫。 */
  attachment_ids?: string[];
}

export interface WorkOrderDispatchResult extends WorkOrder {
  suggested_level: DefectLevel;
  threshold_note: string;
  team: MaintenanceTeam;
}

export interface AttachmentUploadResult {
  attachment_id: string;
  file_name: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
}

export interface CurrentUserInfo {
  subject: string;
  user_id?: string | null;
  roles: string[];
}

export interface DictEntry {
  code: string;
  name: string;
  sort_no: number;
  enabled: boolean;
}

export interface DictEntries {
  items: DictEntry[];
}

/** 审批只允许 APPROVE / REJECT；其他事件不在此面板开放。 */
export interface ApprovalTransitionInput {
  event: "APPROVE" | "REJECT";
  expectedVersion: number;
  reason?: string;
  rejectReasonCode?: string;
}

/** 后端问题体（problem+json）；code 形如 BT-70003 / BT-70005。 */
export class WorkOrderApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly traceId: string | null;

  constructor(code: string, status: number, message: string, traceId: string | null = null) {
    super(message);
    this.name = "WorkOrderApiError";
    this.code = code;
    this.status = status;
    this.traceId = traceId;
  }
}

interface ProblemBody {
  code?: string;
  title?: string;
  detail?: string;
  traceId?: string;
}

const BASE = "/api/v1";

/** 可选 Bearer：真实登录后写 localStorage；开发环境后端可用 BT_DEV_ANONYMOUS=1 免鉴权。 */
function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("bt_access_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function newIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `idem-${Date.now()}-${Math.random()}`;
}

async function toApiError(resp: Response): Promise<WorkOrderApiError> {
  const raw = await resp.text();
  let body: ProblemBody = {};
  try {
    body = JSON.parse(raw) as ProblemBody;
  } catch {
    /* 非 JSON：交给下面的兜底 */
  }
  const code = body.code ?? `HTTP-${resp.status}`;
  const title = body.title ?? resp.statusText ?? "请求失败";
  const detail = body.detail ? `：${body.detail}` : "";
  const trace = body.traceId ? `（traceId=${body.traceId}）` : "";
  return new WorkOrderApiError(
    code,
    resp.status,
    `${code} ${title}${detail}${trace}`,
    body.traceId ?? null,
  );
}

/** 按阈值字典给出建议等级；仅用于展示/预选，最终以后端返回与人工复核为准。 */
export function suggestDefectLevel(
  tiltPermille: number,
  thresholds: DefectLevelThresholds,
): DefectLevel {
  const t = thresholds.tilt_permille;
  if (tiltPermille > t.L1) return "L1";
  if (tiltPermille > t.L2) return "L2";
  return t.default_level;
}

/** 查询维修班组（可按构件过滤数据范围）。 */
export async function fetchMaintenanceTeams(params: {
  componentCode?: string;
  limit?: number;
} = {}): Promise<MaintenanceTeamList> {
  const query = new URLSearchParams();
  if (params.componentCode) query.set("component_code", params.componentCode);
  if (params.limit) query.set("limit", String(params.limit));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const resp = await fetch(`${BASE}/maintenance/teams${suffix}`, {
    headers: { Accept: "application/json", ...authHeaders() },
  });
  if (!resp.ok) throw await toApiError(resp);
  return (await resp.json()) as MaintenanceTeamList;
}

/** 查询当前生效的倾斜率阈值（字典配置，可调）。 */
export async function fetchDefectLevelThresholds(): Promise<DefectLevelThresholds> {
  const resp = await fetch(`${BASE}/dict/defect-level-thresholds`, {
    headers: { Accept: "application/json", ...authHeaders() },
  });
  if (!resp.ok) throw await toApiError(resp);
  return (await resp.json()) as DefectLevelThresholds;
}

/** 从几何分析结果生成待确认工单（缺陷 + PENDING_CONFIRM，不直接派工）。 */
export async function dispatchWorkOrder(
  input: WorkOrderDispatchRequest,
): Promise<WorkOrderDispatchResult> {
  const resp = await fetch(`${BASE}/workorders/dispatch`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "Idempotency-Key": newIdempotencyKey(),
      ...authHeaders(),
    },
    body: JSON.stringify(input),
  });
  if (!resp.ok) throw await toApiError(resp);
  return (await resp.json()) as WorkOrderDispatchResult;
}

/** 上传现场照片（multipart 直传，先挂构件；派单时再通过 attachment_ids 改挂工单）。 */
export async function uploadAttachment(
  componentCode: string,
  file: File,
): Promise<AttachmentUploadResult> {
  const query = new URLSearchParams({ component_code: componentCode });
  const form = new FormData();
  form.append("file", file);
  const resp = await fetch(`${BASE}/attachments?${query.toString()}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Idempotency-Key": newIdempotencyKey(),
      ...authHeaders(),
    },
    body: form,
  });
  if (!resp.ok) throw await toApiError(resp);
  return (await resp.json()) as AttachmentUploadResult;
}

/** 当前用户：前端据此判断审批权限与“不能审批本人上报”的禁用态。 */
export async function fetchCurrentUser(): Promise<CurrentUserInfo> {
  const resp = await fetch(`${BASE}/auth/me`, {
    headers: { Accept: "application/json", ...authHeaders() },
  });
  if (!resp.ok) throw await toApiError(resp);
  return (await resp.json()) as CurrentUserInfo;
}

/** 待主管确认列表；mine=false 表示按状态全量拉取（不是只看本人受理的）。 */
export async function fetchPendingOrders(limit = 20, cursor?: string): Promise<WorkOrderList> {
  const query = new URLSearchParams({ status: "PENDING_CONFIRM", mine: "false", limit: `${limit}` });
  if (cursor) query.set("cursor", cursor);
  const resp = await fetch(`${BASE}/workorders?${query.toString()}`, {
    headers: { Accept: "application/json", ...authHeaders() },
  });
  if (!resp.ok) throw await toApiError(resp);
  return (await resp.json()) as WorkOrderList;
}

/** 驳回原因字典（dict_type=reject_reason）。 */
export async function fetchRejectReasons(): Promise<DictEntry[]> {
  const resp = await fetch(`${BASE}/dict/reject_reason`, {
    headers: { Accept: "application/json", ...authHeaders() },
  });
  if (!resp.ok) throw await toApiError(resp);
  return ((await resp.json()) as DictEntries).items.filter((item) => item.enabled);
}

/** 工单流转（审批只用 APPROVE / REJECT）；expected_version 用于乐观锁。 */
export async function transitionWorkOrder(
  orderId: string,
  input: ApprovalTransitionInput,
): Promise<WorkOrder> {
  const payload: Record<string, string> = {};
  if (input.reason) payload.reason = input.reason;
  if (input.rejectReasonCode) payload.reject_reason_code = input.rejectReasonCode;
  const body: Record<string, unknown> = {
    event: input.event,
    expected_version: input.expectedVersion,
  };
  if (Object.keys(payload).length > 0) body.payload = payload;

  const resp = await fetch(`${BASE}/workorders/${orderId}/transitions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "Idempotency-Key": newIdempotencyKey(),
      ...authHeaders(),
    },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw await toApiError(resp);
  return (await resp.json()) as WorkOrder;
}

