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

export async function fetchReadiness(): Promise<Readiness> {
  const resp = await fetch("/api/v1/readyz", {
    headers: { Accept: "application/json" },
    credentials: "omit",
  });
  if (!resp.ok) {
    const problem = (await resp.json()) as Problem;
    throw new Error(`${problem.code} ${problem.title}（traceId=${problem.traceId}）`);
  }
  return (await resp.json()) as Readiness;
}
