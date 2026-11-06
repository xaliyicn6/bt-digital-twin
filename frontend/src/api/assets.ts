/**
 * 构件台账最小客户端：只取资产树需要的 ``component_code + tech_grade``。
 * 契约：GET /components?struct_code=&type=&level=&limit= → {items: Component[]}
 */
interface ComponentSummary {
  component_code: string;
  tech_grade?: number | null;
}

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("bt_access_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** 返回 {component_code: tech_grade}；未评定为 null。 */
export async function fetchComponentGrades(
  structCode: string,
): Promise<Record<string, number | null>> {
  const query = new URLSearchParams({
    struct_code: structCode,
    type: "PIER",
    level: "3",
    limit: "100",
  });
  const resp = await fetch(`/api/v1/components?${query.toString()}`, {
    headers: { Accept: "application/json", ...authHeaders() },
  });
  if (!resp.ok) throw new Error(`构件等级查询失败 HTTP ${resp.status}`);
  const body = (await resp.json()) as { items?: ComponentSummary[] };
  const grades: Record<string, number | null> = {};
  for (const item of body.items ?? []) {
    grades[item.component_code] = item.tech_grade ?? null;
  }
  return grades;
}
