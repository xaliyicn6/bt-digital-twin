<script setup lang="ts">
/**
 * 隧道安全余量色阶图例（界面先行版）。
 *
 * 口径（确认门 C7）：0.3m 是**安全余量（Clearance Margin）**，不是侵限深度。
 *   🟢 余量 > 0.3m  安全
 *   🟡 余量 0 ~ 0.3m 不足
 *   🔴 余量 < 0（侵限深度 > 0m）侵限
 * 缺口：逐点余量需要设计断面基线 + 2D ICP 配准，后端目前只给整条断面的
 * clearance_reference_mm / intrusion_depth_mm，尚不能驱动逐点着色。
 * 因此本图例只定义色阶与符号方向，点云暂不按此上色。
 */
const DEMO_NOTE = "示例阈值 · 可被甲方规范覆盖";

const items = [
  { key: "safe", label: "安全", hint: "余量 > 0.3 m", tone: "ok" },
  { key: "warn", label: "预警", hint: "余量不足 0 ~ 0.3 m", tone: "warn" },
  { key: "intrusion", label: "侵限", hint: "余量 < 0（侵限深度 > 0 m）", tone: "danger" },
] as const;
</script>

<template>
  <div class="intrusion-legend">
    <div class="legend-head">
      <span class="legend-title">侵限色阶</span>
      <span class="legend-demo">{{ DEMO_NOTE }}</span>
    </div>
    <div v-for="item in items" :key="item.key" class="legend-row">
      <span class="legend-dot" :class="item.tone" />
      <span class="legend-label">{{ item.label }}</span>
      <span class="legend-hint">{{ item.hint }}</span>
    </div>
    <div class="legend-note">
      0.3m 为安全余量（Clearance Margin），不是侵限深度；设计断面基线未接入，
      点云暂不按此色阶着色。
    </div>
  </div>
</template>

<style scoped>
.intrusion-legend {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed rgba(80, 130, 220, 0.25);
}

.legend-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.legend-title {
  font-size: 12px;
  font-weight: 600;
  color: #e8f1ff;
}

.legend-demo {
  font-size: 10px;
  color: #d9c088;
}

.legend-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
  font-size: 11px;
}

.legend-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex: 0 0 auto;
}

.legend-dot.ok {
  background: #4cd17a;
}

.legend-dot.warn {
  background: #f0b429;
}

.legend-dot.danger {
  background: #ff5d5d;
  animation: intrusion-blink 1s ease-in-out infinite;
}

@keyframes intrusion-blink {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(255, 93, 93, 0.7);
  }
  50% {
    box-shadow: 0 0 8px 3px rgba(255, 93, 93, 0.35);
  }
}

.legend-label {
  color: #cfe2ff;
  width: 32px;
}

.legend-hint {
  color: #7f96b6;
}

.legend-note {
  margin-top: 6px;
  font-size: 10px;
  color: #d9c088;
}
</style>
