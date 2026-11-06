<script setup lang="ts">
/**
 * 顶部综合看板（半透明玻璃拟态）。
 *
 * ⚠️ 面板数字全部是**示例数据 · 算法待接入**（确认门 C6）。
 * ``score_detail`` 预留了未来计算口径：按"各构件权重 + 病害扣分"动态计算；
 * 接口落地后替换本文件常量，在此之前不得把这里的数字当真实结论。
 */
const DEMO_NOTE = "示例数据 · 算法待接入";

interface ScoreDetail {
  score: number;
  grade: string;
  method: string;
  is_demo: boolean;
}

const scoreDetail: ScoreDetail = {
  score: 82.5,
  grade: "B",
  method: "未来按“各构件权重 + 病害扣分”动态计算（接口待接入）",
  is_demo: true,
};

interface OverviewStat {
  key: string;
  label: string;
  value: string;
  tone: "normal" | "warn";
}

const stats: OverviewStat[] = [
  { key: "score", label: "全桥评分", value: `${scoreDetail.score}（${scoreDetail.grade}级）`, tone: "normal" },
  { key: "components", label: "已检测构件", value: "12", tone: "normal" },
  { key: "defects", label: "病害", value: "3", tone: "warn" },
  { key: "orders", label: "待处理工单", value: "1", tone: "warn" },
];
</script>

<template>
  <div class="overview-board">
    <div class="overview-head">
      <span class="overview-title">全桥技术状况</span>
      <span class="overview-demo">{{ DEMO_NOTE }}</span>
    </div>
    <div class="overview-stats">
      <div v-for="item in stats" :key="item.key" class="overview-stat">
        <span class="stat-label">{{ item.label }}</span>
        <span class="stat-value" :class="item.tone === 'warn' ? 'warn' : ''">
          {{ item.value }}
        </span>
      </div>
    </div>
    <div class="overview-detail">score_detail：{{ scoreDetail.method }}</div>
  </div>
</template>

<style scoped>
.overview-board {
  min-width: 320px;
  padding: 10px 14px 12px;
  border-radius: 8px;
  background: rgba(10, 24, 48, 0.62);
  border: 1px solid rgba(120, 190, 255, 0.3);
  backdrop-filter: blur(10px);
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.35);
  pointer-events: none;
}

.overview-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.overview-title {
  font-size: 13px;
  font-weight: 600;
  color: #e8f1ff;
  letter-spacing: 0.04em;
}

.overview-demo {
  font-size: 10px;
  color: #d9c088;
  border: 1px dashed rgba(217, 192, 136, 0.5);
  border-radius: 3px;
  padding: 1px 5px;
}

.overview-stats {
  display: flex;
  gap: 18px;
}

.overview-stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-label {
  font-size: 10px;
  color: #7f96b6;
}

.stat-value {
  font-size: 16px;
  font-weight: 700;
  color: #7fe0a5;
  font-variant-numeric: tabular-nums;
}

.stat-value.warn {
  color: #ffb86b;
}

.overview-detail {
  margin-top: 8px;
  font-size: 10px;
  color: #7f96b6;
}
</style>
