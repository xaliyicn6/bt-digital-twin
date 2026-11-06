/**
 * 资产树 Mock 数据（一期演示口径）。
 * 节点名称 label 直接作为下游几何任务的 component_code。
 */
export interface AssetNode {
  /** 树内唯一键（非业务编码）。 */
  id: string;
  /** 节点名称；叶子节点 label 即 component_code。 */
  label: string;
  children?: AssetNode[];
}

export const ASSET_TREE: AssetNode[] = [
  {
    id: "B001",
    label: "B001 特大桥",
    children: [
      {
        id: "K01",
        label: "K01 第1跨",
        children: [{ id: "PIR-1", label: "PIR 1号桥墩" }],
      },
    ],
  },
];

/** 默认选中叶子节点（PIR 1号桥墩）。 */
export const DEFAULT_ASSET_NODE: AssetNode =
  ASSET_TREE[0].children?.[0]?.children?.[0] ?? { id: "PIR-1", label: "PIR 1号桥墩" };
