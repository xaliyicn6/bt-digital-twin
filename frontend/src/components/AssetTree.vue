<script setup lang="ts">
/**
 * 左侧资产树悬浮面板。
 * Mock 数据：B001 特大桥 → K01 第1跨 → PIR 1号桥墩。
 * 默认展开并高亮 PIR 1号桥墩；节点点击通过 select 事件抛给 App.vue。
 */
import { onMounted, ref } from "vue";
import { ElTree } from "element-plus";
import { ASSET_TREE, DEFAULT_ASSET_NODE, type AssetNode } from "./assetTreeData";

const props = defineProps<{
  /** 当前高亮节点 id。 */
  currentId?: string | null;
}>();

const emit = defineEmits<{
  (e: "select", node: AssetNode): void;
}>();

const treeRef = ref<InstanceType<typeof ElTree> | null>(null);

function handleNodeClick(node: AssetNode): void {
  emit("select", node);
}

onMounted(() => {
  // 默认高亮并同步一次选中，保证右侧面板首屏就有"当前选中"。
  treeRef.value?.setCurrentKey(DEFAULT_ASSET_NODE.id);
  emit("select", DEFAULT_ASSET_NODE);
});
</script>

<template>
  <aside class="asset-tree">
    <div class="asset-title">资产树</div>
    <div class="asset-subtitle">B001 特大桥 · 结构导航</div>

    <el-tree
      ref="treeRef"
      class="asset-el-tree"
      :data="ASSET_TREE"
      node-key="id"
      :props="{ label: 'label', children: 'children' }"
      :current-node-key="props.currentId ?? undefined"
      default-expand-all
      highlight-current
      :expand-on-click-node="false"
      @node-click="handleNodeClick"
    />
  </aside>
</template>

<style scoped>
.asset-tree {
  position: absolute;
  top: 96px;
  left: 24px;
  width: 252px;
  max-height: calc(100% - 190px);
  overflow-y: auto;
  padding: 14px 14px 16px;
  border-radius: 8px;
  background: rgba(10, 24, 48, 0.85);
  border: 1px solid rgba(80, 130, 220, 0.35);
  color: #cfe2ff;
  font-size: 13px;
  pointer-events: auto;
  backdrop-filter: blur(6px);
}

.asset-title {
  font-size: 15px;
  font-weight: 600;
  color: #e8f1ff;
  letter-spacing: 0.02em;
}

.asset-subtitle {
  font-size: 11px;
  color: #7f96b6;
  margin: 2px 0 10px;
}

.asset-el-tree {
  background: transparent;
  color: #cfe2ff;
}

.asset-tree :deep(.el-tree) {
  background: transparent;
  color: #cfe2ff;
}

.asset-tree :deep(.el-tree-node__content) {
  height: 30px;
  border-radius: 4px;
  color: #cfe2ff;
}

.asset-tree :deep(.el-tree-node__content:hover) {
  background: rgba(26, 58, 110, 0.55);
}

.asset-tree :deep(.el-tree-node.is-current > .el-tree-node__content) {
  background: rgba(36, 90, 160, 0.75);
  color: #ffffff;
}

.asset-tree :deep(.el-tree-node__expand-icon) {
  color: #88a4cf;
}

.asset-tree :deep(.el-tree-node__expand-icon.is-leaf) {
  color: transparent;
}
</style>
