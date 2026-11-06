<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import AssetTree from "./components/AssetTree.vue";
import { DEFAULT_ASSET_NODE, type AssetNode } from "./components/assetTreeData";
import { fetchReadiness, type ReadinessResult } from "./api/client";
import { createGeometryTask, GeometryApiError, type GeometryTaskStatus } from "./api/geometry";
import { useGeometryTaskPolling } from "./hooks/useGeometryTaskPolling";
import {
  createGlobe,
  POINT_SIZE_RANGE,
  type CloudMetaAlarm,
  type GlobeController,
  type MeasureResult,
  type PointColorMode,
  type TilesetLoadState,
} from "./cesium/scene";

const container = ref<HTMLDivElement | null>(null);
const autoRotate = ref(false);
const result = ref<ReadinessResult>({ kind: "pending" });

// ---- 右侧面板 UI 状态（只存 3D Tiles 交互态，不存 Cesium 对象本身） ----
const tilesetUrl = "/tiles/tunnel/tileset.json" as const;
const tilesetState = ref<TilesetLoadState>({ kind: "empty" });
const tilesetShow = ref(true);
const pointSize = ref(8.0);
const attenuation = ref(false);
const colorMode = ref<PointColorMode>("rgb");

// 点击量测结果 + 报警弹窗
const measure = ref<MeasureResult | null>(null);
const alarm = ref<CloudMetaAlarm | null>(null);

// ---- 资产树 → 几何分析任务 ----
const selectedAsset = ref<AssetNode>(DEFAULT_ASSET_NODE);
const submitError = ref<string | null>(null);
const task = useGeometryTaskPolling({ intervalMs: 1500 });

const STATUS_LABELS: Record<GeometryTaskStatus, string> = {
  QUEUED: "排队中",
  RUNNING: "计算中",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  RETRYING: "重试中",
  CANCELLED: "已取消",
  TIMEOUT: "超时",
};

const taskStatus = computed<GeometryTaskStatus | null>(
  () => task.detail.value?.status ?? null,
);

const statusLabel = computed(() =>
  taskStatus.value ? STATUS_LABELS[taskStatus.value] : "待提交",
);

const progressPct = computed(() => {
  const p = task.detail.value?.progress;
  if (typeof p === "number") return Math.max(0, Math.min(100, p));
  return task.polling.value ? 5 : 0;
});

const progressStatus = computed<"success" | "exception" | undefined>(() => {
  if (taskStatus.value === "SUCCEEDED") return "success";
  if (taskStatus.value && ["FAILED", "CANCELLED", "TIMEOUT"].includes(taskStatus.value)) {
    return "exception";
  }
  return undefined;
});

/** 契约口径：VERTICALITY 的倾斜率字段是 result.tilt_permille（‰）。 */
const tiltPermille = computed<number | null>(() => {
  const r = task.detail.value?.result;
  if (!r) return null;
  if (typeof r.tilt_permille === "number") return r.tilt_permille;
  if (r.unit === "permille" && typeof r.value === "number") return r.value;
  return null;
});

const taskError = computed(() => task.error.value ?? submitError.value);

const COLOR_MODE_OPTIONS: Array<{ value: PointColorMode; label: string }> = [
  { value: "rgb", label: "RGB 真彩" },
  { value: "elevation", label: "按高程 (Z) 渐变" },
  { value: "intensity", label: "按强度 (Intensity) 渐变" },
  { value: "solid", label: "纯色（白）" },
];

// 刻意用普通变量，不用 ref：Viewer / Tileset 都是重对象，不能被 Vue 的深响应代理包住
let globe: GlobeController | null = null;

const pointSizeText = computed(() => `${pointSize.value.toFixed(1)} px`);

onMounted(async () => {
  if (container.value) {
    globe = createGlobe(container.value, { autoRotate: autoRotate.value });
  }
  // 注册点云点击量测与报警点点击回调
  globe?.onMeasure((r) => {
    measure.value = r;
  });
  globe?.onAlarm((a) => {
    alarm.value = a;
  });
  result.value = await fetchReadiness();
  // 自动加载点云（开发期直接看效果，生产期可以改成手动触发）
  void loadTileset();
});

onBeforeUnmount(() => {
  globe?.destroy();
  globe = null;
});

function toggleAutoRotate(): void {
  autoRotate.value = !autoRotate.value;
  globe?.setAutoRotate(autoRotate.value);
}

function resetView(): void {
  globe?.resetView();
}

async function loadTileset(): Promise<void> {
  if (!globe) return;
  tilesetState.value = { kind: "loading" };
  const state = await globe.loadTileset(tilesetUrl, {
    pointSize: pointSize.value,
    attenuation: attenuation.value,
  });
  tilesetState.value = state;
  if (state.kind === "ready") {
    globe.setTilesetShow(tilesetShow.value);
  }
}

function toggleShow(): void {
  tilesetShow.value = !tilesetShow.value;
  globe?.setTilesetShow(tilesetShow.value);
}

function stepPointSize(delta: number): void {
  const next = Math.max(
    POINT_SIZE_RANGE.min,
    Math.min(POINT_SIZE_RANGE.max, pointSize.value + delta),
  );
  pointSize.value = Number(next.toFixed(1));
  globe?.setPointSize(next);
}

function toggleAttenuation(): void {
  attenuation.value = !attenuation.value;
  globe?.setAttenuation(attenuation.value);
}

function changeColorMode(e: Event): void {
  const v = (e.target as HTMLSelectElement).value as PointColorMode;
  colorMode.value = v;
  globe?.setColorMode(v);
}

function closeAlarm(): void {
  alarm.value = null;
}

function flyToTileset(): void {
  globe?.flyToTileset();
}

function onSelectAsset(node: AssetNode): void {
  selectedAsset.value = node;
  submitError.value = null;
}

/** 发起垂直度分析：POST /api/v1/geometry/tasks，受理后用轮询 Hook 跟踪进度。 */
async function startVerticality(): Promise<void> {
  if (task.polling.value) return;
  submitError.value = null;
  task.reset();
  try {
    const accepted = await createGeometryTask({
      component_code: selectedAsset.value.label,
      analysis_type: "VERTICALITY",
    });
    task.start(accepted.task_id);
  } catch (err) {
    submitError.value =
      err instanceof GeometryApiError
        ? err.message
        : err instanceof Error
          ? `提交失败：${err.message}`
          : `提交失败：${String(err)}`;
  }
}

// 仅在进入 SUCCEEDED 时弹一次成功框；FAILED/TIMEOUT 的错误已在面板内展示，不静默。
watch(taskStatus, (status) => {
  if (status !== "SUCCEEDED") return;
  const tilt = tiltPermille.value;
  const text =
    tilt !== null
      ? `垂直度分析完成，倾斜率 ${tilt.toFixed(3)} ‰`
      : "垂直度分析完成，但结果未返回 tilt_permille";
  void ElMessageBox.alert(text, "分析完成", {
    type: "success",
    confirmButtonText: "知道了",
  });
});
</script>

<template>
  <div class="stage">
    <div ref="container" class="globe" />

    <!-- 左侧资产树 -->
    <AssetTree :current-id="selectedAsset.id" @select="onSelectAsset" />

    <header class="hud">
      <div class="titles">
        <h1>桥隧结构智能检测与数字孪生运维平台</h1>
        <p>Vue3 + Cesium · 一期三维大屏骨架</p>
      </div>
      <div class="actions">
        <button type="button" @click="toggleAutoRotate">
          {{ autoRotate ? "暂停自转" : "开始自转" }}
        </button>
        <button type="button" @click="resetView">复位视角</button>
      </div>
    </header>

    <!-- 右侧 3D Tiles 面板 -->
    <aside class="panel">
      <div class="panel-title">真实点云</div>
      <div class="panel-subtitle">WHU-TLS 隧道采样 · 28 万点</div>

      <div v-if="tilesetState.kind === 'loading'" class="panel-status loading">
        ⏳ 加载 pnts 瓦片…
      </div>
      <div v-else-if="tilesetState.kind === 'error'" class="panel-status error">
        ❌ {{ tilesetState.message }}
      </div>
      <div v-else-if="tilesetState.kind === 'empty'" class="panel-status empty">
        未加载
      </div>
      <div v-else class="panel-status ready">
        ✅ 已加载
      </div>

      <div class="panel-actions">
        <button type="button" class="primary" @click="loadTileset">
          {{ tilesetState.kind === "empty" ? "加载真实点云" : "重新加载" }}
        </button>
        <button
          type="button"
          :class="tilesetShow ? 'toggle-on' : 'toggle-off'"
          @click="toggleShow"
          :disabled="tilesetState.kind !== 'ready'"
        >
          {{ tilesetShow ? "显示" : "隐藏" }}
        </button>
        <button
          type="button"
          @click="flyToTileset"
          :disabled="tilesetState.kind !== 'ready'"
        >
          飞至此云
        </button>
      </div>

      <div class="panel-group" :class="{ disabled: tilesetState.kind !== 'ready' }">
        <div class="panel-row">
          <span>颜色模式</span>
          <select
            class="color-select"
            :value="colorMode"
            :disabled="tilesetState.kind !== 'ready'"
            @change="changeColorMode"
          >
            <option
              v-for="opt in COLOR_MODE_OPTIONS"
              :key="opt.value"
              :value="opt.value"
            >
              {{ opt.label }}
            </option>
          </select>
        </div>
        <div class="panel-row">
          <span>点大小</span>
          <span class="value">{{ pointSizeText }}</span>
        </div>
        <div class="panel-row row-actions">
          <button type="button" @click="stepPointSize(-POINT_SIZE_RANGE.step)">
            −
          </button>
          <button type="button" @click="stepPointSize(POINT_SIZE_RANGE.step)">
            +
          </button>
        </div>
        <div class="panel-row">
          <span>距离衰减</span>
          <label class="switch">
            <input type="checkbox" :checked="attenuation" @change="toggleAttenuation" />
            <span class="slider" />
          </label>
        </div>
      </div>

      <!-- 资产选中 → 垂直度分析任务 -->
      <div class="task-block">
        <div class="selected-row">
          当前选中：<strong>{{ selectedAsset.label }}</strong>
        </div>
        <button
          type="button"
          class="primary task-btn"
          :disabled="task.polling.value"
          @click="startVerticality"
        >
          {{ task.polling.value ? "分析中…" : "发起垂直度分析" }}
        </button>

        <template v-if="task.detail.value || task.polling.value">
          <el-progress
            class="task-progress"
            :percentage="progressPct"
            :status="progressStatus"
            :stroke-width="8"
          />
          <div class="task-status">状态：{{ statusLabel }}</div>
          <div v-if="tiltPermille !== null" class="task-result">
            倾斜率：<code>{{ tiltPermille.toFixed(3) }} ‰</code>
          </div>
        </template>

        <div v-if="taskError" class="task-error">{{ taskError }}</div>
      </div>

      <!-- 点击量测结果 -->
      <div v-if="measure" class="measure-card">
        <div class="measure-title">📍 点击位置坐标</div>
        <div class="measure-row">
          <span>经度</span>
          <code>{{ measure.longitude.toFixed(6) }}°</code>
        </div>
        <div class="measure-row">
          <span>纬度</span>
          <code>{{ measure.latitude.toFixed(6) }}°</code>
        </div>
        <div class="measure-row">
          <span>高程</span>
          <code>{{ measure.height.toFixed(3) }} m</code>
        </div>
      </div>

      <div class="panel-path">
        <span>路径</span>
        <code>{{ tilesetUrl }}</code>
      </div>
    </aside>

    <footer class="status">
      <template v-if="result.kind === 'ok'">
        <span class="good">后端：{{ result.data.status }}</span>
        <span class="meta">traceId {{ result.data.traceId }}</span>
      </template>
      <template v-else-if="result.kind === 'server-error'">
        <span class="bad">后端：{{ result.message }}</span>
      </template>
      <template v-else-if="result.kind === 'reconnecting'">
        <span class="reconnecting">后端：{{ result.message }}</span>
      </template>
      <template v-else>
        <span class="meta">后端：检查中…</span>
      </template>
    </footer>

    <!-- 侵限报警弹窗 -->
    <div v-if="alarm" class="alarm-popup">
      <div class="alarm-header">
        <span class="alarm-badge">⚠ 侵限报警</span>
        <button type="button" class="alarm-close" @click="closeAlarm">×</button>
      </div>
      <div class="alarm-body">
        <div class="alarm-name">{{ alarm.label }}</div>
        <div class="alarm-msg">警告：此处隧道结构侵限 {{ alarm.offsetMm }} mm</div>
        <div class="alarm-coord">
          局部坐标 (ENU)：x={{ alarm.x.toFixed(2) }} · y={{ alarm.y.toFixed(2) }} ·
          z={{ alarm.z.toFixed(2) }} m
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stage {
  position: relative;
  width: 100%;
  height: 100%;
  background: #05070d;
}

.globe {
  position: absolute;
  inset: 0;
}

.hud {
  position: absolute;
  top: 20px;
  left: 24px;
  right: 320px; /* 给右侧面板留位 */
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  pointer-events: none;
  color: #e8f1ff;
  text-shadow: 0 1px 6px rgba(0, 0, 0, 0.65);
}

.titles h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.titles p {
  margin: 6px 0 0;
  font-size: 13px;
  color: #9fb4d0;
}

.actions {
  display: flex;
  gap: 10px;
  pointer-events: auto;
}

.actions button {
  padding: 8px 14px;
  border: 1px solid rgba(120, 170, 255, 0.45);
  border-radius: 6px;
  background: rgba(10, 24, 48, 0.72);
  color: #cfe2ff;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.2s ease;
}

.actions button:hover {
  background: rgba(26, 58, 110, 0.9);
}

/* ---- 右侧 3D Tiles 面板 ---- */
.panel {
  position: absolute;
  top: 20px;
  right: 24px;
  width: 280px;
  max-height: calc(100% - 40px);
  overflow-y: auto;
  padding: 16px 18px 18px;
  border-radius: 8px;
  background: rgba(10, 24, 48, 0.85);
  border: 1px solid rgba(80, 130, 220, 0.35);
  color: #cfe2ff;
  font-size: 13px;
  pointer-events: auto;
  backdrop-filter: blur(6px);
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
  color: #e8f1ff;
  letter-spacing: 0.02em;
}

.panel-subtitle {
  font-size: 11px;
  color: #7f96b6;
  margin-top: 2px;
}

.panel-status {
  margin: 10px 0 12px;
  padding: 6px 10px;
  border-radius: 4px;
  font-size: 12px;
}

.panel-status.ready {
  background: rgba(40, 160, 100, 0.2);
  color: #7fe0a5;
}

.panel-status.loading {
  background: rgba(200, 160, 60, 0.18);
  color: #d9c088;
}

.panel-status.error {
  background: rgba(200, 80, 80, 0.18);
  color: #ff9b9b;
  word-break: break-all;
}

.panel-status.empty {
  background: rgba(120, 130, 150, 0.15);
  color: #8aa0be;
}

.panel-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.panel-actions button {
  padding: 6px 12px;
  border-radius: 5px;
  border: 1px solid rgba(120, 170, 255, 0.4);
  background: rgba(16, 36, 72, 0.7);
  color: #cfe2ff;
  font-size: 12px;
  cursor: pointer;
  flex: 1 1 72px;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.panel-actions button:hover:not(:disabled) {
  background: rgba(26, 58, 110, 0.9);
  border-color: rgba(160, 200, 255, 0.65);
}

.panel-actions button:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.panel-actions button.primary {
  background: rgba(36, 90, 160, 0.75);
  border-color: rgba(130, 180, 255, 0.6);
  font-weight: 500;
}

.panel-actions button.primary:hover:not(:disabled) {
  background: rgba(46, 110, 200, 0.9);
}

.panel-actions button.toggle-on {
  background: rgba(40, 140, 80, 0.4);
  border-color: rgba(120, 220, 160, 0.55);
}

.panel-actions button.toggle-off {
  background: rgba(100, 80, 60, 0.4);
  border-color: rgba(220, 160, 120, 0.55);
}

.panel-group {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed rgba(80, 130, 220, 0.25);
}

.panel-group.disabled {
  opacity: 0.4;
  pointer-events: none;
}

/* ---- 资产选中 + 垂直度分析任务 ---- */
.task-block {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed rgba(80, 130, 220, 0.25);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.selected-row {
  color: #9fb4d0;
  font-size: 12px;
}

.selected-row strong {
  color: #e8f1ff;
  font-weight: 600;
}

.task-btn {
  padding: 8px 12px;
  border-radius: 5px;
  border: 1px solid rgba(130, 180, 255, 0.6);
  background: rgba(36, 90, 160, 0.75);
  color: #e8f1ff;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease;
}

.task-btn:hover:not(:disabled) {
  background: rgba(46, 110, 200, 0.9);
}

.task-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.task-progress {
  margin-top: 4px;
}

.task-status {
  font-size: 11px;
  color: #9fb4d0;
}

.task-result {
  font-size: 12px;
  color: #7fe0a5;
}

.task-result code {
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  color: #c8ffdd;
}

.task-error {
  padding: 6px 8px;
  border-radius: 4px;
  background: rgba(200, 80, 80, 0.18);
  color: #ff9b9b;
  font-size: 11px;
  word-break: break-all;
}

.panel-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 5px 0;
  color: #9fb4d0;
}

.panel-row .value {
  color: #cfe2ff;
  font-variant-numeric: tabular-nums;
}

.panel-row.row-actions {
  gap: 6px;
}

.panel-row.row-actions button {
  padding: 4px 14px;
  border-radius: 4px;
  border: 1px solid rgba(120, 170, 255, 0.4);
  background: rgba(16, 36, 72, 0.7);
  color: #cfe2ff;
  font-size: 14px;
  cursor: pointer;
}

/* 开关 */
.switch {
  position: relative;
  display: inline-block;
  width: 38px;
  height: 20px;
}

.switch input {
  opacity: 0;
  width: 0;
  height: 0;
}

.slider {
  position: absolute;
  cursor: pointer;
  inset: 0;
  background: rgba(80, 100, 130, 0.7);
  border-radius: 20px;
  transition: background 0.2s ease;
}

.slider::before {
  position: absolute;
  content: "";
  height: 14px;
  width: 14px;
  left: 3px;
  bottom: 3px;
  background: #e8f1ff;
  border-radius: 50%;
  transition: transform 0.2s ease;
}

.switch input:checked + .slider {
  background: rgba(80, 160, 255, 0.7);
}

.switch input:checked + .slider::before {
  transform: translateX(18px);
}

/* 颜色模式下拉框 */
.color-select {
  background: rgba(16, 36, 72, 0.85);
  border: 1px solid rgba(120, 170, 255, 0.4);
  border-radius: 4px;
  color: #cfe2ff;
  font-size: 12px;
  padding: 4px 6px;
  cursor: pointer;
  outline: none;
}

.color-select:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* 点击量测结果卡 */
.measure-card {
  margin-top: 14px;
  padding: 10px 12px;
  border-radius: 6px;
  background: rgba(40, 160, 100, 0.12);
  border: 1px solid rgba(120, 220, 160, 0.3);
}

.measure-title {
  font-size: 12px;
  font-weight: 600;
  color: #7fe0a5;
  margin-bottom: 6px;
}

.measure-row {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: #9fb4d0;
  padding: 2px 0;
}

.measure-row code {
  color: #e8f1ff;
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 11px;
}

.panel-path {
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px dashed rgba(80, 130, 220, 0.25);
  color: #6f83a0;
  font-size: 11px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.panel-path code {
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  color: #88a4cf;
  font-size: 11px;
}

.status {
  position: absolute;
  left: 24px;
  bottom: 22px;
  display: flex;
  gap: 10px;
  align-items: baseline;
  padding: 8px 12px;
  border-radius: 6px;
  background: rgba(10, 24, 48, 0.68);
  color: #b9c9df;
  font-size: 12px;
  pointer-events: none;
}

.status .good {
  color: #7fe0a5;
  font-weight: 600;
}

.status .bad {
  color: #ff9b9b;
  font-weight: 600;
}

.status .reconnecting {
  color: #d0b89c;
}

.status .meta {
  color: #8aa0be;
}

/* 侵限报警弹窗 */
.alarm-popup {
  position: absolute;
  top: 90px;
  left: 50%;
  transform: translateX(-50%);
  width: 360px;
  border-radius: 8px;
  background: rgba(58, 16, 16, 0.92);
  border: 1px solid rgba(255, 90, 90, 0.6);
  box-shadow: 0 6px 28px rgba(255, 60, 60, 0.35);
  color: #ffe0e0;
  pointer-events: auto;
  backdrop-filter: blur(6px);
  z-index: 10;
}

.alarm-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid rgba(255, 90, 90, 0.25);
}

.alarm-badge {
  font-size: 14px;
  font-weight: 600;
  color: #ff6b6b;
  letter-spacing: 0.04em;
}

.alarm-close {
  background: transparent;
  border: none;
  color: #ffb0b0;
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  padding: 0 4px;
}

.alarm-close:hover {
  color: #fff;
}

.alarm-body {
  padding: 12px 14px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.alarm-name {
  font-size: 13px;
  font-weight: 600;
  color: #ffcece;
}

.alarm-msg {
  font-size: 13px;
  color: #ff8a8a;
}

.alarm-coord {
  font-size: 11px;
  color: #d9a8a8;
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
}
</style>
