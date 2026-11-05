/**
 * 会话内最小的请求封装：只做 problem+json 解析与 traceId 回传。
 * 正式客户端由 contracts/openapi.yaml 生成（T-01 之后接入），本文件届时删除。
 */

export interface Readiness {
  status: string;
  checks: Record<string, string>;
  traceId: string;
}

export interface Problem {
  type: string;
  title: string;
  status: number;
  code: string;
  traceId: string;
  detail?: string;
  instance?: string;
}

export type ReadinessResult =
  | { kind: "ok"; data: Readiness }
  /** 后端自身 5xx/4xx 并返回了 problem+json：应该标红。 */
  | { kind: "server-error"; status: number; message: string }
  /** 连接层问题：fetch 抛 TypeError / Vite 代理转发失败返回空 body 500。
   *  这类属于"后端还没起来 / 重连中"，不标红，提示用户即可。 */
  | { kind: "reconnecting"; message: string }
  /** 本地前端还没发请求时的占位态。 */
  | { kind: "pending" };

/** 判定 response body 是否是后端真实的 problem+json。 */
function looksLikeProblemJson(raw: string): Problem | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<Problem>;
    if (
      typeof parsed.status === "number" &&
      typeof parsed.code === "string" &&
      typeof parsed.title === "string"
    ) {
      return parsed as Problem;
    }
  } catch {
    /* 不是 JSON，继续走 fallback */
  }
  return null;
}

/** 判定 fetch 抛出的异常是否属于"连接/代理层断了"。
 *  fetch 对 ECONNREFUSED、DNS 失败、CORS（浏览器端拦截）统一抛 TypeError；
 *  这类统统视为"重连中"，不是代码逻辑 bug。 */
function isConnectionError(err: unknown): boolean {
  return err instanceof TypeError;
}

export async function fetchReadiness(): Promise<ReadinessResult> {
  try {
    const resp = await fetch("/api/v1/readyz", {
      headers: { Accept: "application/json" },
      credentials: "omit",
    });
    if (resp.ok) {
      const data = (await resp.json()) as Readiness;
      return { kind: "ok", data };
    }
    const raw = await resp.text();
    const problem = looksLikeProblemJson(raw);

    // 5xx 且无 problem+json → Vite 代理/网关层返回的 500（空 body / HTML 错误页）
    // 这类最典型：后端冷启动未就绪时，Vite 代理吞掉连接错误后回 500
    if (resp.status >= 500 && !problem) {
      return {
        kind: "reconnecting",
        message: `HTTP ${resp.status} — 后端服务重连中`,
      };
    }
    if (problem) {
      return {
        kind: "server-error",
        status: resp.status,
        message: `${problem.code} ${problem.title}${problem.traceId ? `（traceId=${problem.traceId}）` : ""}`,
      };
    }
    // 4xx / 其它非 problem+json
    return {
      kind: "server-error",
      status: resp.status,
      message: `HTTP ${resp.status}（${raw.slice(0, 80)}）`,
    };
  } catch (err) {
    if (isConnectionError(err)) {
      return { kind: "reconnecting", message: "后端服务重连中" };
    }
    return {
      kind: "server-error",
      status: 0,
      message: err instanceof Error ? err.message : String(err),
    };
  }
}
