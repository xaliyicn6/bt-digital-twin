/**
 * 资产树 Mock 数据（一期演示口径）。
 * 节点名称 label 直接作为下游几何任务的 component_code。
 *
 * ``techGrade`` 是**示例等级**（1-5 类），对应后端 ``component.tech_grade``；
 * 只用于演示资产树徽标，真实构件等级需后端评定与持久化接入（C1/C6）。
 */
export interface AssetNode {
  /** 树内唯一键（非业务编码）。 */
  id: string;
  /** 节点名称；叶子节点 label 即 component_code。 */
  label: string;
  /** 构件技术状况等级（1-5，对应 component.tech_grade）；示例值，不是实测评定。 */
  techGrade?: number;
  children?: AssetNode[];
}

/** 资产树等级徽标的统一标注。 */
export const GRADE_DEMO_NOTE = "示例数据 · 算法待接入";

export const ASSET_TREE: AssetNode[] = [
  {
    id: "B001",
    label: "B001 特大桥",
    children: [
      {
        id: "K01",
        label: "K01 第1跨",
        children: [{ id: "PIR-1", label: "PIR 1号桥墩", techGrade: 3 }],
      },
    ],
  },
];

/** 默认选中叶子节点（PIR 1号桥墩）。 */
export const DEFAULT_ASSET_NODE: AssetNode =
  ASSET_TREE[0].children?.[0]?.children?.[0] ?? { id: "PIR-1", label: "PIR 1号桥墩" };
