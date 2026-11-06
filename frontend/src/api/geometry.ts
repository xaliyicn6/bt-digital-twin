/**
 * 几何分析任务的最小客户端（契约 contracts/openapi.yaml: /geometry/tasks）。
 *
 * 字段口径严格对齐契约：
 *   POST 202 → TaskAccepted {task_id, status, created_at}，必须带 Idempotency-Key
 *   GET  200 → GeometryTaskResult {task_id, status, progress, result, error_code, error_detail, ...}
 *   result.tilt_permille = VERTICALITY 的倾斜率（‰）
 *
 * 请求只经本文件，组件不手写 fetch（AGENTS.md 前端规范）。
 */

export type AnalysisType = "VERTICALITY" | "CLEARANCE" | "SECTION_SHAPE";

export type GeometryTaskStatus =
  | "QUEUED"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "RETRYING"
  | "CANCELLED"
  | "TIMEOUT";

/** 契约里 result 的常用字段（未列出的字段后端可能扩展，这里只取展示需要的）。 */
export interface GeometryTaskResultData {
  value?: number;
  unit?: "mm" | "permille" | "sqm";
  tilt_permille?: number | null;
  direction_deg?: number | null;
  degraded?: boolean;
  degrade_reason?: string | null;
  valid_layers?: number | null;
  total_layers?: number | null;
  z_bottom?: number | null;
  z_top?: number | null;
  residual_rms_mm?: number | null;
  algorithm_version?: string;
  [key: string]: unknown;
}

export interface TaskAccepted {
  task_id: string;
  status: GeometryTaskStatus;
  created_at: string;
}

export interface GeometryTaskDetail {
  task_id: string;
  status: GeometryTaskStatus;
  analysis_type?: AnalysisType;
  progress?: number;
  result?: GeometryTaskResultData | null;
  error_code?: string | null;
  error_detail?: string | null;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
}

/** 后端问题体（problem+json）；code 形如 BT-10002 / BT-30005。 */
export class GeometryApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly traceId: string | null;

  constructor(code: string, status: number, message: string, traceId: string | null = null) {
    super(message);
    this.name = "GeometryApiError";
    this.code = code;
    this.status = status;
    this.traceId = traceId;
  }
}

interface ProblemBody {
  code?: string;
  title?: string;
  detail?: string;
  status?: number;
  traceId?: string;
}

const TASK_BASE = "/api/v1/geometry/tasks";

/** 可选 Bearer：真实登录后写 localStorage；开发环境后端可用 BT_DEV_ANONYMOUS=1 免鉴权。 */
function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("bt_access_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function toApiError(resp: Response): Promise<GeometryApiError> {
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
  return new GeometryApiError(code, resp.status, `${code} ${title}${detail}${trace}`, body.traceId ?? null);
}

/** 提交几何分析任务，返回受理结果（202）。 */
export async function createGeometryTask(input: {
  component_code: string;
  analysis_type: AnalysisType;
}): Promise<TaskAccepted> {
  const idemKey = globalThis.crypto?.randomUUID?.() ?? `idem-${Date.now()}-${Math.random()}`;
  const resp = await fetch(TASK_BASE, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "Idempotency-Key": idemKey,
      ...authHeaders(),
    },
    body: JSON.stringify(input),
  });
  if (!resp.ok) throw await toApiError(resp);
  return (await resp.json()) as TaskAccepted;
}

/** 查询任务进度与结果（200）。 */
export async function fetchGeometryTask(taskId: string): Promise<GeometryTaskDetail> {
  const resp = await fetch(`${TASK_BASE}/${encodeURIComponent(taskId)}`, {
    headers: { Accept: "application/json", ...authHeaders() },
  });
  if (!resp.ok) throw await toApiError(resp);
  return (await resp.json()) as GeometryTaskDetail;
}

const TERMINAL_STATUSES: ReadonlySet<GeometryTaskStatus> = new Set([
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
  "TIMEOUT",
]);

export function isTerminalStatus(status: GeometryTaskStatus | undefined | null): boolean {
  return status ? TERMINAL_STATUSES.has(status) : false;
}
