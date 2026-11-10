<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { ensurePotreeAssets } from "./loadPotree";
import {
  createPotreeController,
  type PotreeColorMode,
  type PotreeController,
  type PotreeDatasetSource,
  type PotreeMeasureMode,
  type PotreePick,
  type PotreeStage,
} from "./PotreeController";

/**
 * 宏观地球 / 微观点云 双视图容器。
 *
 * - 默认插槽放原有 Cesium 容器，组件只负责分层显隐，不接管 Cesium 生命周期。
 * - 微观层按需加载 Potree 脚本，并负责点云加载、拾取、量测、暂停与释放。
 * - 坐标口径：微观点云一律为「局部工程坐标 · 未做大地基准转换」。
 */

type DatasetKey = "tunnel" | "bridge";
type ViewMode = "globe" | "micro";

const DATASET_SOURCES: Record<DatasetKey, PotreeDatasetSource> = {
  tunnel: {
    key: "tunnel",
    label: "隧道（lunxiao-L）",
    url: "/tiles/potree/tunnel/metadata.json",
    metaUrl: "/tiles/local/tunnel/cloud-meta.json",
  },
  bridge: {
    key: "bridge",
    label: "桥梁（beam_disc）",
    url: "/tiles/potree/bridge/metadata.json",
    metaUrl: "/tiles/local/bridge/cloud-meta.json",
  },
};

const props = defineProps<{
  mode: ViewMode;
  dataset: DatasetKey | null;
  reloadToken: number;
  pointSize: number;
  colorMode: PotreeColorMode;
  pointCloudVisible: boolean;
}>();

const emit = defineEmits<{
  (e: "update:mode", value: ViewMode): void;
  (e: "pick", point: PotreePick): void;
  (e: "measure", info: { mode: PotreeMeasureMode; text: string }): void;
  (e: "measure-mode", mode: PotreeMeasureMode): void;
  (e: "state", state: { kind: "empty" | "loading" | "ready" | "error"; message?: string }): void;
  (e: "stage", stage: PotreeStage): void;
}>();

interface ViewerState {
  kind: "empty" | "loading" | "ready" | "error";
  message?: string;
}

const host = ref<HTMLDivElement | null>(null);
const state = ref<ViewerState>({ kind: "empty" });
const stage = ref<PotreeStage | null>(null);
const currentMeasure = ref<PotreeMeasureMode>(null);

let controller: PotreeController | null = null;
let viewerReady = false;
let resizeObserver: ResizeObserver | null = null;
let readyKey: DatasetKey | null = null;
let pendingKey: DatasetKey | null = null;

const percent = computed(() => Math.round(stage.value?.percent ?? 0));
const seconds = computed(() =>
  ((stage.value?.elapsedMs ?? 0) / 1000).toFixed(stage.value && stage.value.elapsedMs > 1000 ? 1 : 2),
);

function setState(next: ViewerState): void {
  state.value = next;
  emit("state", next);
}

function observeResize(): void {
  if (!host.value || resizeObserver) return;
  resizeObserver = new ResizeObserver(() => controller?.resize());
  resizeObserver.observe(host.value);
  window.addEventListener("resize", onWindowResize);
}

function onWindowResize(): void {
  controller?.resize();
}

async function ensureViewer(): Promise<boolean> {
  if (viewerReady && controller) return true;
  if (!host.value) return false;

  setState({ kind: "loading", message: "初始化 Potree…" });
  try {
    await ensurePotreeAssets();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    setState({ kind: "error", message });
    return false;
  }
  if (!host.value) return false;

  try {
    controller = createPotreeController(host.value, {
      onStage: (next) => {
        stage.value = next;
        emit("stage", next);
        if (next.stage === "ready") {
          readyKey = pendingKey;
          setState({ kind: "ready" });
        } else if (next.stage === "error") {
          setState({ kind: "error", message: next.text });
        } else {
          setState({ kind: "loading" });
        }
      },
      onPick: (point) => emit("pick", point),
      onMeasure: (info) => emit("measure", info),
      onMeasureMode: (mode) => {
        currentMeasure.value = mode;
        emit("measure-mode", mode);
      },
      onError: (message) => setState({ kind: "error", message }),
    });
    controller.attach();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    controller = null;
    setState({ kind: "error", message });
    return false;
  }

  controller.setPointSize(props.pointSize);
  controller.setColorMode(props.colorMode);
  controller.setVisible(props.pointCloudVisible);
  observeResize();
  viewerReady = true;
  return true;
}

async function loadDataset(force = false): Promise<void> {
  if (props.mode !== "micro") return;
  await nextTick();

  if (!props.dataset) {
    controller?.unload();
    controller?.pause();
    readyKey = null;
    pendingKey = null;
    stage.value = null;
    setState({ kind: "empty" });
    return;
  }

  const key = props.dataset;
  if (!force && readyKey === key) {
    controller?.resume();
    return;
  }

  const ok = await ensureViewer();
  if (!ok || !controller) return;
  if (props.mode !== "micro" || props.dataset !== key) return;

  pendingKey = key;
  readyKey = null;
  stage.value = null;
  controller.resume();
  setState({ kind: "loading", message: `加载 ${DATASET_SOURCES[key].label}…` });
  await controller.load(DATASET_SOURCES[key]);
}

watch(
  () => props.mode,
  (mode) => {
    if (mode === "micro") {
      void loadDataset(false);
      return;
    }
    controller?.unload();
    controller?.pause();
    readyKey = null;
    pendingKey = null;
    stage.value = null;
    if (currentMeasure.value !== null) {
      currentMeasure.value = null;
      emit("measure-mode", null);
    }
    setState({ kind: "empty" });
  },
  { immediate: true, flush: "post" },
);

watch(
  () => props.dataset,
  () => {
    if (props.mode === "micro") void loadDataset(false);
  },
  { flush: "post" },
);

watch(
  () => props.reloadToken,
  () => {
    if (props.mode === "micro") void loadDataset(true);
  },
);

watch(
  () => props.pointSize,
  (value) => controller?.setPointSize(value),
);

watch(
  () => props.colorMode,
  (value) => controller?.setColorMode(value),
);

watch(
  () => props.pointCloudVisible,
  (value) => controller?.setVisible(value),
);

/** 与右侧量测工具栏一致：再次点击当前模式 → 退出量测。 */
function toggleMeasure(mode: Exclude<PotreeMeasureMode, null>): void {
  const next: PotreeMeasureMode = currentMeasure.value === mode ? null : mode;
  currentMeasure.value = next;
  controller?.setMeasureMode(next);
  emit("measure-mode", next);
}

function clearMeasure(): void {
  currentMeasure.value = null;
  controller?.clearMeasure();
  emit("measure-mode", null);
}

function resetView(): void {
  controller?.resetView();
}

function captureScreenshot(): string | null {
  return controller?.captureScreenshot() ?? null;
}

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  resizeObserver = null;
  window.removeEventListener("resize", onWindowResize);
  controller?.destroy();
  controller = null;
  viewerReady = false;
});

defineExpose({ toggleMeasure, clearMeasure, resetView, captureScreenshot });
</script>

<template>
  <div class="micro3d">
    <!-- 原有 Cesium 容器：地球模式显示；微观模式下隐藏但保留实例与已加载瓦片 -->
    <div v-show="mode === 'globe'" class="view-layer cesium-layer">
      <slot />
    </div>

    <!-- Potree 容器：微观模式显示，尺寸完全跟随父容器 -->
    <div v-show="mode === 'micro'" ref="host" class="view-layer potree-layer" />

    <div v-if="mode === 'micro' && state.kind === 'loading'" class="micro-overlay">
      <div class="micro-card">
        <div class="micro-title">正在加载点云数据集</div>
        <div class="micro-bar"><i :style="{ width: `${percent}%` }" /></div>
        <div class="micro-stage">
          {{ stage?.text ?? state.message ?? "准备中" }} · {{ seconds }} s
        </div>
        <div v-if="stage?.totalVisibleNodes" class="micro-note mono">
          节点 {{ stage.loadedNodes ?? 0 }}/{{ stage.totalVisibleNodes }}
          <template v-if="stage.points"> · {{ stage.points.toLocaleString("zh-CN") }} 点</template>
        </div>
        <div class="micro-note">Potree 近景渲染 · 局部工程坐标 · 未做大地基准转换</div>
      </div>
    </div>

    <div v-else-if="mode === 'micro' && state.kind === 'error'" class="micro-overlay">
      <div class="micro-card micro-error">
        <div class="micro-title">点云加载失败</div>
        <div class="micro-stage">{{ state.message }}</div>
        <div class="micro-note">
          请确认 frontend/public/tiles/potree 已由 backend/scripts/build_potree.py 生成
        </div>
      </div>
    </div>

    <div v-else-if="mode === 'micro' && !dataset" class="micro-overlay">
      <div class="micro-card">
        <div class="micro-title">请先选择点云数据集</div>
        <div class="micro-note">在右侧「点云数据集」面板选择隧道或桥梁，选中后会自动加载</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.micro3d {
  position: relative;
  width: 100%;
  height: 100%;
  background: #05070d;
}

.view-layer {
  position: absolute;
  inset: 0;
}

.potree-layer {
  /* Potree 的 canvas 会填满 renderArea；这里固定成 100% 避免黑边 */
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.micro-overlay {
  position: absolute;
  inset: 0;
  z-index: 6;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(4, 10, 22, 0.55);
  backdrop-filter: blur(2px);
  pointer-events: none;
}

.micro-card {
  min-width: 300px;
  max-width: 420px;
  padding: 18px 20px;
  border-radius: 10px;
  border: 1px solid rgba(79, 209, 255, 0.35);
  background: rgba(8, 20, 38, 0.86);
  color: #cfe2ff;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45);
}

.micro-card.micro-error {
  border-color: rgba(255, 120, 120, 0.5);
}

.micro-title {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: #e8f1ff;
}

.micro-bar {
  margin: 12px 0 8px;
  height: 6px;
  border-radius: 999px;
  background: rgba(80, 130, 220, 0.22);
  overflow: hidden;
}

.micro-bar i {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #4fd1ff, #7fe0a5);
  transition: width 0.2s ease;
}

.micro-stage {
  font-size: 12px;
  line-height: 1.6;
  color: #a9c4e4;
}

.micro-note {
  margin-top: 6px;
  font-size: 11px;
  line-height: 1.6;
  color: #7f9bbd;
}

.micro-note.mono {
  font-family: "Cascadia Code", Consolas, monospace;
}
</style>
