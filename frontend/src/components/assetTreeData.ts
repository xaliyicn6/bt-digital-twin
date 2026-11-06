/**
 * 资产树演示数据（与 ``db/seeds/seed_piers_k01.sql`` 的 10 个桥墩一致）。
 * 节点名称 label 直接作为下游几何任务的 component_code。
 *
 * ``techGrade`` 是种子初值（1-5 类），对应后端 ``component.tech_grade``；
 * 页面启动时用 ``GET /components`` 覆盖为库内实值，批量分析成功后服务端会回写
 * 实算等级，接口返回后资产树随刷新保持一致。
 */
export interface AssetNode {
  /** 树内唯一键（非业务编码）。 */
  id: string;
  /** 节点名称；叶子节点 label 即 component_code。 */
  label: string;
  /** 构件技术状况等级（1-5，对应 component.tech_grade）。 */
  techGrade?: number;
  children?: AssetNode[];
}

/** 资产树等级徽标的统一标注。 */
export const GRADE_DEMO_NOTE = "示例数据 · 合成点云";

/** K01 第1跨下的 10 个演示桥墩（顺序即批量提交流水）。 */
export const DEMO_PIERS: ReadonlyArray<{ id: string; label: string; techGrade: number }> = [
  { id: "PIR-1", label: "PIR 1号桥墩", techGrade: 1 },
  { id: "PIR-2", label: "PIR 2号桥墩", techGrade: 3 },
  { id: "PIR-3", label: "PIR 3号桥墩", techGrade: 4 },
  { id: "PIR-4", label: "PIR 4号桥墩", techGrade: 5 },
  { id: "PIR-5", label: "PIR 5号桥墩", techGrade: 2 },
  { id: "PIR-6", label: "PIR 6号桥墩", techGrade: 1 },
  { id: "PIR-7", label: "PIR 7号桥墩", techGrade: 3 },
  { id: "PIR-8", label: "PIR 8号桥墩", techGrade: 4 },
  { id: "PIR-9", label: "PIR 9号桥墩", techGrade: 5 },
  { id: "PIR-10", label: "PIR 10号桥墩", techGrade: 2 },
];

export const DEMO_PIER_CODES: ReadonlyArray<string> = DEMO_PIERS.map((pier) => pier.label);

export const ASSET_TREE: AssetNode[] = [
  {
    id: "B001",
    label: "B001 特大桥",
    children: [
      {
        id: "K01",
        label: "K01 第1跨",
        children: DEMO_PIERS.map((pier) => ({
          id: pier.id,
          label: pier.label,
          techGrade: pier.techGrade,
        })),
      },
    ],
  },
];

/** 默认选中叶子节点（PIR 1号桥墩）。 */
export const DEFAULT_ASSET_NODE: AssetNode =
  ASSET_TREE[0].children?.[0]?.children?.[0] ?? { id: "PIR-1", label: "PIR 1号桥墩" };
