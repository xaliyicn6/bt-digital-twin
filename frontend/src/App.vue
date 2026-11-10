<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import AssetTree from "./components/AssetTree.vue";
import DispatchWorkOrderDialog from "./components/DispatchWorkOrderDialog.vue";
import DefectUpload from "./components/DefectUpload.vue";
import IntrusionLegend from "./components/IntrusionLegend.vue";
import OverviewBoard from "./components/OverviewBoard.vue";
import PendingApprovalPanel from "./components/PendingApprovalPanel.vue";
import {
  ASSET_TREE,
  DEFAULT_ASSET_NODE,
  DEMO_PIER_CODES,
  type AssetNode,
} from "./components/assetTreeData";
import { fetchComponentGrades } from "./api/assets";
import { fetchReadiness, type ReadinessResult } from "./api/client";
import {
  createGeometryBatch,
  createGeometryTask,
  exportGeometryReport,
  fetchBatchReportCsv,
  GeometryApiError,
  type GeometryTaskInput,
  type GeometryTaskStatus,
} from "./api/geometry";
import { useGeometryTaskPolling } from "./hooks/useGeometryTaskPolling";
import {
  useGeometryBatchPolling,
  type BatchItemState,
} from "./hooks/useGeometryBatchPolling";
import {
  createGlobe,
  POINT_SIZE_RANGE,
  type CloudMeta,
  type CloudMetaAlarm,
  type DesignLayerState,
  type GlobeController,
  type MeasureResult,
  type PointColorMode,
  type TilesetLoadStageDetail,
  type TilesetLoadTiming,
} from "./cesium/scene";
import {
  createMeasureTool,
  type MeasureMode,
  type MeasureToolController,
} from "./cesium/measure";
import {
  createFirstPersonRoaming,
  type RoamingController,
} from "./cesium/roaming";
import Micro3DViewer from "./potree/Micro3DViewer.vue";
import type {
  PotreeMeasureMode,
  PotreePick,
  PotreeStage,
} from "./potree/PotreeController";

const container = ref<HTMLDivElement | null>(null);
const autoRotate = ref(false);
const result = ref<ReadinessResult>({ kind: "pending" });

// ---- 右侧面板 UI 状态（只存 3D Tiles 交互态，不存 Cesium 对象本身） ----
// 页面不再默认加载旧 pnts（旧 /tiles/tunnel 关联已作废）；
// 数据集由「点云数据集」面板显式选择，各自加载自己的 tileset + cloud-meta。
interface DatasetEntry {
  code: "tunnel" | "bridge";
  label: string;
  tilesetUrl: string;
  metaUrl: string;
  analysisType: "CLEARANCE" | "VERTICALITY";
  componentCode: string;
  /** 只有隧道有设计模型（桥梁不显示设计图层开关的可用态）。 */
  designUrl?: string;
}

const DATASETS: DatasetEntry[] = [
  {
    code: "tunnel",
    label: "隧道（lunxiao-L）",
    tilesetUrl: "/tiles/local/tunnel/tileset.json",
    metaUrl: "/tiles/local/tunnel/cloud-meta.json",
    analysisType: "CLEARANCE",
    componentCode: "LNX 轮小区间",
    designUrl: "/tiles/local/tunnel/design-model.json",
  },
  {
    code: "bridge",
    label: "桥梁（beam_disc）",
    tilesetUrl: "/tiles/local/bridge/tileset.json",
    metaUrl: "/tiles/local/bridge/cloud-meta.json",
    analysisType: "VERTICALITY",
    componentCode: "BD 梁体（beam_disc）",
  },
];

const activeDataset = ref<DatasetEntry["code"] | null>(null);
const datasetMetas = ref<Record<string, CloudMeta | null>>({});
const datasetMetaErrors = ref<Record<string, string>>({});
/** 视图层加载状态：Cesium 带 tileset，Potree 只有状态，UI 只关心 kind 与错误文案。 */
type ViewerLoadState =
  | { kind: "empty" }
  | { kind: "loading" }
  | { kind: "ready" }
  | { kind: "error"; message: string };

const datasetState = ref<ViewerLoadState>({ kind: "empty" });
/** 加载阶段（骨架屏文案）与实测耗时（网络/解析/GPU/首帧）。 */
const loadStage = ref<TilesetLoadStageDetail | null>(null);
const loadTiming = ref<TilesetLoadTiming | null>(null);
const loadElapsedMs = ref(0);
let loadTimer: number | null = null;
const designOn = ref(false);
const designState = ref<DesignLayerState>({ kind: "empty" });

const tilesetState = datasetState;
const tilesetShow = ref(true);
const pointSize = ref(8.0);
const attenuation = ref(false);
const colorMode = ref<PointColorMode>("rgb");

const activeDatasetEntry = computed<DatasetEntry | null>(
  () => DATASETS.find((d) => d.code === activeDataset.value) ?? null,
);

// 点击量测结果 + 报警弹窗
const measure = ref<MeasureResult | null>(null);
const alarm = ref<CloudMetaAlarm | null>(null);

/** 就地漫游时局部精载的加载状态（面板上给可见反馈，失败要显式提示）。 */
interface AlarmViewState {
  kind: "idle" | "loading" | "ready" | "fallback";
  text: string;
}
const alarmView = ref<AlarmViewState>({ kind: "idle", text: "" });

// ---- 三维量测 / 第一人称漫游 ----
const measureMode = ref<MeasureMode>(null);
const measureHint = ref("");
const roaming = ref(false);

// ---- 双视图：宏观 Cesium 地球 / 微观 Potree 点云 ----
type ViewMode = "globe" | "micro";
const viewMode = ref<ViewMode>("globe");
const microViewer = ref<InstanceType<typeof Micro3DViewer> | null>(null);
const microReloadToken = ref(0);
/** Cesium 当前已加载的数据集，用于切回地球时判断是否需要补载。 */
const cesiumLoadedCode = ref<string | null>(null);
/** Potree 点击拾取到的局部工程坐标（与 Cesium 的经纬度卡严格区分）。 */
const localMeasure = ref<PotreePick | null>(null);
const microMeasureMode = ref<PotreeMeasureMode>(null);
const microStage = ref<PotreeStage | null>(null);
const microPercent = computed(() => Math.round(microStage.value?.percent ?? 0));
/** 工具栏高亮同时覆盖 Cesium 量测与 Potree 量测。 */
const activeMeasure = computed<MeasureMode | PotreeMeasureMode>(() =>
  viewMode.value === "micro" ? microMeasureMode.value : measureMode.value,
);

const MODE_HINTS: Record<Exclude<MeasureMode, null>, string> = {
  distance: "距离：点击起点，再点终点（可连续测量）",
  height: "高度：点击一点，显示到地面/椭球的垂直高差",
  area: "面积：连续点击顶点，双击自动闭合",
};

/** Potree 剖面只在微观视图出现，单独维护提示文案。 */
const PROFILE_HINT = "剖面：在点云上连续点击确定剖面线，完成后在剖面窗口查看";

// ---- 资产树 → 几何分析任务 ----
const selectedAsset = ref<AssetNode>(DEFAULT_ASSET_NODE);
const submitError = ref<string | null>(null);
const task = useGeometryTaskPolling({ intervalMs: 500 });

// ---- 点云数据集 → 分析任务（与资产树任务独立的第二条轮询） ----
const datasetTask = useGeometryTaskPolling({ intervalMs: 500 });
const datasetSubmitError = ref<string | null>(null);

// ---- 资产树：种子初值 + 后端 tech_grade 回写同步 ----
const assetNodes = ref<AssetNode[]>(ASSET_TREE);
const batchComponentCodes = ref<string[]>([]);

function withTechGrades(
  nodes: AssetNode[],
  grades: Record<string, number | null>,
): AssetNode[] {
  return nodes.map((node) => ({
    ...node,
    ...(grades[node.label] != null ? { techGrade: grades[node.label] as number } : {}),
    ...(node.children ? { children: withTechGrades(node.children, grades) } : {}),
  }));
}

/** 刷新资产树等级：批量分析回写后调用，保证圆点/徽标与结果一致。 */
async function refreshAssetTreeGrades(): Promise<void> {
  try {
    const grades = await fetchComponentGrades("B001");
    assetNodes.value = withTechGrades(ASSET_TREE, grades);
  } catch {
    // 后端不可用时保留种子初值，不阻塞三维视图。
  }
}

// ---- P1 批量处理流水线（示例数据 · 合成点云 · 与单条任务状态完全独立） ----
const batch = useGeometryBatchPolling({ intervalMs: 800 });
const batchError = ref<string | null>(null);
const batchCsvBusy = ref(false);

/** 10 个真实构件编码（PIR 1–10号桥墩），提交前整体校验，失败不留半批。 */
function buildMockBatch(): GeometryTaskInput[] {
  return DEMO_PIER_CODES.map((component_code) => ({
    component_code,
    analysis_type: "VERTICALITY" as const,
  }));
}

async function startBatchAnalysis(): Promise<void> {
  if (batch.polling.value) return;
  batchError.value = null;
  try {
    const accepted = await createGeometryBatch(buildMockBatch());
    // 响应顺序与请求顺序一致，用它给表格映射构件名。
    batchComponentCodes.value = DEMO_PIER_CODES.slice(0, accepted.items.length);
    batch.start(accepted.items.map((item) => item.task_id));
  } catch (err) {
    batchError.value =
      err instanceof GeometryApiError
        ? err.message
        : err instanceof Error
          ? `批量提交失败：${err.message}`
          : `批量提交失败：${String(err)}`;
  }
}

async function exportBatchCsv(): Promise<void> {
  const ids = batch.taskIds.value;
  if (ids.length === 0 || batchCsvBusy.value) return;
  batchCsvBusy.value = true;
  batchError.value = null;
  try {
    const blob = await fetchBatchReportCsv(ids);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `batch-report-${Date.now()}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    batchError.value =
      err instanceof GeometryApiError
        ? err.message
        : err instanceof Error
          ? `导出失败：${err.message}`
          : `导出失败：${String(err)}`;
  } finally {
    batchCsvBusy.value = false;
  }
}

const batchPercent = computed(() =>
  batch.total.value === 0
    ? 0
    : Math.round((batch.completed.value / batch.total.value) * 100),
);

const batchProgressStatus = computed<"success" | "exception" | undefined>(() => {
  if (batch.polling.value || batch.total.value === 0) return undefined;
  return batch.failed.value === 0 ? "success" : "exception";
});

/** 批量表格的一行：构件名 + 状态 + 进度 + 倾斜率 + 等级 + 错误码。 */
interface BatchRow {
  index: number;
  code: string;
  statusLabel: string;
  statusClass: "ok" | "warn" | "bad" | "idle";
  progress: number;
  tiltText: string;
  techGrade: number | null;
  gradeText: string;
  gradeClass: "ok" | "warn" | "bad" | "idle";
  errorCode: string | null;
}

function parseTilt(item: BatchItemState): number | null {
  const r = item.detail?.result;
  if (!r) return null;
  if (typeof r.tilt_permille === "number") return r.tilt_permille;
  if (r.unit === "permille" && typeof r.value === "number") return r.value;
  return null;
}

/** 表格纵向顺序与提交顺序一致（batch.items 由 taskIds 顺序生成）。 */
const batchRows = computed<BatchRow[]>(() =>
  batch.items.value.map((item) => {
    const code = batchComponentCodes.value[item.index] ?? `#${item.index + 1}`;
    const status = item.status;
    const tilt = parseTilt(item);
    const grade = item.detail?.result?.tech_grade;
    const techGrade = typeof grade === "number" ? grade : null;
    const failed =
      item.error !== null ||
      (status !== null && FAILURE_STATUSES.includes(status));
    const errorCode = failed
      ? item.detail?.error_code ?? item.error ?? status ?? "未知错误"
      : null;
    return {
      index: item.index,
      code,
      statusLabel: status ? STATUS_LABELS[status] : item.error ? "失败" : "排队中",
      statusClass: failed
        ? "bad"
        : status === "SUCCEEDED"
          ? "ok"
          : status === "RUNNING" || status === "RETRYING"
            ? "warn"
            : "idle",
      progress:
        status === "SUCCEEDED" ? 100 : Math.max(0, Math.round(item.detail?.progress ?? 0)),
      tiltText: tilt === null ? "—" : tilt.toFixed(3),
      techGrade,
      gradeText: techGrade === null ? "—" : `${techGrade}类`,
      gradeClass:
        techGrade === null
          ? "idle"
          : techGrade >= 4
            ? "bad"
            : techGrade === 3
              ? "warn"
              : "ok",
      errorCode,
    };
  }),
);

// 整批轮询结束后刷新资产树，展示服务端回写的实算等级（避免种子初值打架）。
watch(
  () => [batch.polling.value, batch.completed.value, batch.total.value] as const,
  ([polling, completed, total]) => {
    if (!polling && total > 0 && completed === total) void refreshAssetTreeGrades();
  },
);

/** 演示要求：从点击发起开始，进度条/“计算中”至少展示 2 秒。 */
const MIN_VISIBLE_MS = 2000;
const startedAt = ref<number | null>(null);
const elapsedMs = ref(0);
/** 真实任务已成功、但仍在补足最少展示时长。 */
const releasing = ref(false);
/** 界面实际呈现的状态：成功会压到最短展示时长之后再切换。 */
const displayStatus = ref<GeometryTaskStatus | null>(null);

const STATUS_LABELS: Record<GeometryTaskStatus, string> = {
  QUEUED: "排队中",
  RUNNING: "计算中",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  RETRYING: "重试中",
  CANCELLED: "已取消",
  TIMEOUT: "超时",
};

const FAILURE_STATUSES: GeometryTaskStatus[] = ["FAILED", "CANCELLED", "TIMEOUT"];

const taskStatus = computed<GeometryTaskStatus | null>(
  () => task.detail.value?.status ?? null,
);

const taskBusy = computed(() => task.polling.value || releasing.value);

const statusLabel = computed(() => {
  if (releasing.value) return "正在计算中…";
  return displayStatus.value ? STATUS_LABELS[displayStatus.value] : "待提交";
});

/** 任务进度未返回或为 0 时用耗时驱动视觉进度，最高逼近 90%，终态再置 100。 */
const progressPct = computed(() => {
  if (displayStatus.value === "SUCCEEDED") return 100;
  const real = task.detail.value?.progress;
  // 补足最短展示时长期间忽略真实 100%，避免提前“看起来已完成”。
  const realPct =
    !releasing.value && typeof real === "number" && real > 0 ? Math.min(real, 95) : 0;
  const visualPct = Math.min(90, 8 + (elapsedMs.value / MIN_VISIBLE_MS) * 82);
  return Math.max(0, Math.min(95, Math.max(realPct, visualPct)));
});

const progressStatus = computed<"success" | "exception" | undefined>(() => {
  if (displayStatus.value === "SUCCEEDED") return "success";
  if (displayStatus.value && FAILURE_STATUSES.includes(displayStatus.value)) {
    return "exception";
  }
  return undefined;
});

const progressVisible = computed(
  () => releasing.value || task.polling.value || displayStatus.value !== null,
);

/** 最少展示时长结束前，不展示成功结果。 */
const showResult = computed(() => displayStatus.value === "SUCCEEDED");

/** 契约口径：VERTICALITY 的倾斜率字段是 result.tilt_permille（‰）。 */
const tiltPermille = computed<number | null>(() => {
  const r = task.detail.value?.result;
  if (!r) return null;
  if (typeof r.tilt_permille === "number") return r.tilt_permille;
  if (r.unit === "permille" && typeof r.value === "number") return r.value;
  return null;
});

const taskError = computed(() => task.error.value ?? submitError.value);

// ---- 一键派发维修工单（分析成功后才可点；只生成待确认工单，不直接下发） ----
const dispatchVisible = ref(false);

const dispatchTaskId = computed<string | null>(() => task.detail.value?.task_id ?? null);

const algorithmVersion = computed<string | null>(
  () => task.detail.value?.result?.algorithm_version ?? null,
);

const resultDegraded = computed<boolean>(() => task.detail.value?.result?.degraded === true);

const degradeReason = computed<string | null>(
  () => task.detail.value?.result?.degrade_reason ?? null,
);

// ---- 技术状况等级（1-5 类）与规范依据 ----
// 等级来自后端 result.tech_grade；tech_grade_source 非空即表示阈值是示例口径，
// 界面必须原样展示该标注，不能只显示等级而不显示"示例阈值"。
const techGrade = computed<number | null>(() => {
  const grade = task.detail.value?.result?.tech_grade;
  return typeof grade === "number" ? grade : null;
});

const techGradeLabel = computed<string | null>(
  () => task.detail.value?.result?.tech_grade_label ?? null,
);

const standardReference = computed<string | null>(
  () => task.detail.value?.result?.standard_reference ?? null,
);

const techGradeSource = computed<string | null>(
  () => task.detail.value?.result?.tech_grade_source ?? null,
);

/** 4/5 类红色（材料明确），3 类黄色，1/2 类绿色。 */
const gradeTagType = computed<"success" | "warning" | "danger">(() => {
  const grade = techGrade.value;
  if (grade !== null && grade >= 4) return "danger";
  if (grade === 3) return "warning";
  return "success";
});

const clauseVisible = ref(false);
const reportBusy = ref(false);
const reportError = ref<string | null>(null);

/** C8：抓取当前三维视角为 JPEG data URL；失败时报告退回占位图（截图字段可选）。 */
function captureViewerScreenshot(): string | null {
  try {
    // 微观视图导出 Potree 画布，避免报告里出现“隐藏的 Cesium 视角”
    if (viewMode.value === "micro") {
      return microViewer.value?.captureScreenshot() ?? null;
    }
    if (!globe) return null;
    const canvas = globe.viewer.canvas;
    globe.viewer.scene.render();
    const shot = canvas.toDataURL("image/jpeg", 0.8);
    if (shot.startsWith("data:image/jpeg")) return shot;
    // 部分环境 canvas 不产出 JPEG（回落 PNG / 空画布 "data:,"），而后端只接受 JPEG
    // （否则 BT-60005 报告截图不合规）。这里再经 2D canvas 转一次；
    // 仍拿不到 JPEG 就返回 null，让报告退回占位图，不能因此让整个导出失败。
    const out = document.createElement("canvas");
    out.width = canvas.width;
    out.height = canvas.height;
    const ctx = out.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(canvas, 0, 0);
    const converted = out.toDataURL("image/jpeg", 0.8);
    return converted.startsWith("data:image/jpeg") ? converted : null;
  } catch {
    return null;
  }
}

async function downloadReport(): Promise<void> {
  const taskId = task.detail.value?.task_id;
  if (!taskId || reportBusy.value) return;
  reportBusy.value = true;
  reportError.value = null;
  try {
    const screenshot = captureViewerScreenshot();
    const blob = await exportGeometryReport(taskId, screenshot);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `report-${taskId}.html`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    reportError.value = err instanceof Error ? err.message : String(err);
  } finally {
    reportBusy.value = false;
  }
}

/** 派单成功后递增，通知「待我审批」面板刷新计数与列表。 */
const approvalRefreshToken = ref(0);

function onDispatched(): void {
  approvalRefreshToken.value += 1;
}

let progressTimer: ReturnType<typeof setInterval> | null = null;
let releaseTimer: ReturnType<typeof setTimeout> | null = null;

function clearProgressTimer(): void {
  if (progressTimer !== null) {
    clearInterval(progressTimer);
    progressTimer = null;
  }
}

function clearReleaseTimer(): void {
  if (releaseTimer !== null) {
    clearTimeout(releaseTimer);
    releaseTimer = null;
  }
}

function startProgressClock(): void {
  clearProgressTimer();
  startedAt.value = Date.now();
  elapsedMs.value = 0;
  progressTimer = setInterval(() => {
    if (startedAt.value !== null) elapsedMs.value = Date.now() - startedAt.value;
  }, 100);
}

function stopTimers(): void {
  clearProgressTimer();
  clearReleaseTimer();
  releasing.value = false;
}

const COLOR_MODE_OPTIONS: Array<{ value: PointColorMode; label: string }> = [
  { value: "rgb", label: "RGB 真彩" },
  { value: "elevation", label: "按高程 (Z) 渐变" },
  { value: "intensity", label: "按强度 (Intensity) 渐变" },
  { value: "solid", label: "纯色（白）" },
];

// 刻意用普通变量，不用 ref：Viewer / Tileset 都是重对象，不能被 Vue 的深响应代理包住
let globe: GlobeController | null = null;
let measureTool: MeasureToolController | null = null;
let roamingTool: RoamingController | null = null;

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
    openAlarm(a);
  });
  if (globe) {
    measureTool = createMeasureTool(globe.viewer, globe, {
      onModeChange: (mode) => {
        measureMode.value = mode;
        // 量测模式激活时隐藏原有的点击坐标卡，避免同一次点击出现两套反馈
        if (mode !== null) measure.value = null;
        measureHint.value = mode ? MODE_HINTS[mode] : "";
      },
      onHint: (text) => {
        measureHint.value = text;
      },
    });
    roamingTool = createFirstPersonRoaming(globe.viewer, globe, {
      onActiveChange: (active) => {
        roaming.value = active;
      },
    });
  }
  result.value = await fetchReadiness();
  // 资产树先展示种子初值，再用库内 tech_grade 覆盖（与批量回写结果保持一致）。
  void refreshAssetTreeGrades();
  // 不再默认加载任何点云：先取两份 meta 展示点数/范围，由用户点击数据集显式加载。
  void refreshDatasetMetas();
});

onBeforeUnmount(() => {
  stopTimers();
  stopFpsMeter();
  if (loadTimer !== null) {
    window.clearInterval(loadTimer);
    loadTimer = null;
  }
  // 先销毁交互工具（移除 handler/tick/键盘监听、还原相机控制器），再销毁 viewer
  measureTool?.destroy();
  measureTool = null;
  roamingTool?.destroy();
  roamingTool = null;
  globe?.destroy();
  globe = null;
});

function toggleMeasure(mode: Exclude<MeasureMode, null> | "profile"): void {
  if (viewMode.value === "micro") {
    microViewer.value?.toggleMeasure(mode);
    return;
  }
  // Cesium 不使用剖面工具
  if (mode === "profile") return;
  // 再次点击当前模式 → 退出量测（恢复原有弹卡 handler）
  measureTool?.setMode(measureMode.value === mode ? null : mode);
}

function clearMeasure(): void {
  if (viewMode.value === "micro") {
    microViewer.value?.clearMeasure();
    localMeasure.value = null;
    return;
  }
  measureTool?.clear();
}

function toggleRoaming(): void {
  // 内部漫游是 Cesium 专属能力，微观视图下不响应
  if (viewMode.value === "micro") return;
  if (!roamingTool) return;
  if (roaming.value) {
    roamingTool.exit();
    return;
  }
  // 漫游与量测互斥：退出量测，避免拖拽旋转和取点同时发生
  measureTool?.setMode(null);
  roamingTool.enter();
}

/**
 * 报警点范围圈：半径与局部精载瓦片实际覆盖严格一致，
 * 避免“圈画得大、点其实很少”的错觉。
 */
function applyAlarmZone(a: CloudMetaAlarm | null): void {
  const radius = a?.zoneRadiusM ?? a?.localTile?.radiusM ?? 0;
  if (!a || !radius) {
    globe?.clearAlarmZone();
    return;
  }
  globe?.setAlarmZone({
    x: a.x,
    y: a.y,
    z: a.eye?.floorZ ?? a.z,
    radiusM: radius,
    label: `${a.label} · 示例范围 R=${radius.toFixed(1)} m`,
  });
}

function openAlarm(a: CloudMetaAlarm): void {
  alarm.value = a;
  applyAlarmZone(a);
}

/** 报警点「就地内部漫游」：视点用生成脚本算好的实测点云入点（眼高 1.6 m）。 */
async function enterAlarmRoaming(): Promise<void> {
  if (viewMode.value === "micro") return;
  const target = alarm.value;
  const eye = target?.eye;
  if (!target || !eye || !roamingTool || !globe) return;
  measureTool?.setMode(null);
  alarm.value = null;
  // 弹窗关掉但圈体保留：漫游时需要它标出问题范围
  applyAlarmZone(target);
  roamingTool.enter({
    x: eye.x,
    y: eye.y,
    z: eye.z,
    headingDeg: eye.headingDeg,
    floorZ: eye.floorZ,
  });

  const local = target.localTile;
  if (!local) {
    alarmView.value = { kind: "fallback", text: "该处为抽稀视图（meta 未提供局部精载）" };
    return;
  }
  alarmView.value = { kind: "loading", text: "加载局部精载…" };
  const base = (activeDatasetEntry.value?.tilesetUrl ?? "").replace(/[^/]+$/, "");
  const result = await globe.loadAlarmDetail(`${base}${local.uri}`, {
    expectedBytes: local.bytes,
    expectedPoints: local.points,
  });
  // 加载期间用户可能已经退出漫游，不要再把状态写回去
  if (!roaming.value) return;
  if (result.kind === "ready") {
    alarmView.value = {
      kind: "ready",
      text:
        `精载视图 · ${result.points.toLocaleString("zh-CN")} 点 · ` +
        `${(result.bytes / 1024 / 1024).toFixed(1)} MB · ` +
        `就绪 ${fmtSeconds(result.contentReadyMs)}（首帧 ${fmtSeconds(result.firstDrawMs)}）`,
    };
  } else {
    alarmView.value = {
      kind: "fallback",
      text: "局部精载不可用，当前为抽稀视图",
    };
  }
}

// ---- 加载阶段文案 / 耗时 ----
const LOAD_STAGE_TEXT: Record<TilesetLoadStageDetail["stage"], string> = {
  tileset: "读取 tileset.json",
  content: "下载瓦片内容",
  gpu: "解析点云 / 上传 GPU",
  visible: "提交首帧",
  done: "完成",
};
const loadStageText = computed(() => {
  const stage = loadStage.value;
  return stage ? LOAD_STAGE_TEXT[stage.stage] : "准备中";
});

function fmtSeconds(value: number | null): string {
  return value === null ? "—" : `${(Math.max(value, 0) / 1000).toFixed(2)} s`;
}

const loadTimingText = computed(() => {
  const t = loadTiming.value;
  if (!t) return "";
  const parseMs =
    t.contentReadyMs !== null && t.networkEndMs !== null
      ? t.contentReadyMs - t.networkEndMs
      : null;
  const gpuMs =
    t.firstDrawMs !== null && t.contentReadyMs !== null
      ? t.firstDrawMs - t.contentReadyMs
      : null;
  const size = t.networkBytes
    ? `${(t.networkBytes / 1024 / 1024).toFixed(1)} MB`
    : "—";
  return (
    `首帧可见 ${fmtSeconds(t.firstVisibleMs)} · 总 ${fmtSeconds(t.allLoadedMs)} · ` +
    `网络 ${fmtSeconds(t.networkMs)}（${size} / ${t.pntsRequests} 请求） · ` +
    `解析 ${fmtSeconds(parseMs)} · GPU+首帧 ${fmtSeconds(gpuMs)} · ` +
    `点数 ${t.pointCount?.toLocaleString() ?? "—"}`
  );
});

// ---- 漫游 FPS（判断 pointSize 填充率是否成为瓶颈） ----
const roamFps = ref(0);
let fpsTimer: number | null = null;
let fpsRemove: (() => void) | null = null;

function startFpsMeter(): void {
  if (!globe || fpsTimer !== null) return;
  let frames = 0;
  // Scene.frameState 是内部字段，公开 API 里用 postRender 计帧最稳
  fpsRemove = globe.viewer.scene.postRender.addEventListener(() => {
    frames += 1;
  });
  fpsTimer = window.setInterval(() => {
    roamFps.value = frames;
    frames = 0;
  }, 1000);
}

function stopFpsMeter(): void {
  if (fpsTimer !== null) {
    window.clearInterval(fpsTimer);
    fpsTimer = null;
  }
  fpsRemove?.();
  fpsRemove = null;
  roamFps.value = 0;
}

watch(roaming, (active) => {
  if (active) {
    startFpsMeter();
    return;
  }
  stopFpsMeter();
  // 退出漫游：释放局部精载瓦片与范围圈，不留 WebGL 资源
  globe?.clearAlarmDetail();
  globe?.clearAlarmZone();
  alarmView.value = { kind: "idle", text: "" };
});

function toggleAutoRotate(): void {
  if (viewMode.value === "micro") return;
  autoRotate.value = !autoRotate.value;
  globe?.setAutoRotate(autoRotate.value);
}

function resetView(): void {
  if (viewMode.value === "micro") {
    microViewer.value?.resetView();
    return;
  }
  globe?.resetView();
}

// ---------------- 双视图切换 ----------------

function setViewMode(next: ViewMode): void {
  if (viewMode.value === next) return;

  if (next === "micro") {
    // 漫游/量测都是 Cesium 侧交互，进入微观前先退出，避免隐藏相机被继续操作
    if (roaming.value) roamingTool?.exit();
    measureTool?.setMode(null);
    measureMode.value = null;
    microMeasureMode.value = null;
    measure.value = null;
    localMeasure.value = null;
    measureHint.value = "";
    globe?.setRenderPaused(true);
    viewMode.value = "micro";
    return;
  }

  viewMode.value = "globe";
  globe?.setRenderPaused(false);
  // 微观期间选中了其它数据集时，Cesium 这边还没有对应瓦片，切回后自动补载
  if (activeDataset.value && cesiumLoadedCode.value !== activeDataset.value) {
    void selectDataset(activeDataset.value);
  }
}

function onMicroPick(point: PotreePick): void {
  localMeasure.value = point;
}

function onMicroMeasure(info: { mode: PotreeMeasureMode; text: string }): void {
  if (info.mode === null) {
    measureHint.value = "";
    return;
  }
  measureHint.value = info.text;
}

function onMicroMeasureMode(mode: PotreeMeasureMode): void {
  microMeasureMode.value = mode;
  // 与 Cesium 口径一致：量测时隐藏点击坐标卡，避免同一次点击两套反馈
  if (mode !== null) localMeasure.value = null;
  if (mode === "profile") measureHint.value = PROFILE_HINT;
  else if (mode) measureHint.value = MODE_HINTS[mode];
  else measureHint.value = "";
}

function onMicroState(state: {
  kind: "empty" | "loading" | "ready" | "error";
  message?: string;
}): void {
  // 切回地球时组件会卸载并回报 empty，这里不能覆盖 Cesium 刚刚设置的加载状态
  if (viewMode.value !== "micro") return;
  if (state.kind === "loading") {
    datasetState.value = { kind: "loading" };
    return;
  }
  if (state.kind === "error") {
    datasetState.value = { kind: "error", message: state.message ?? "点云加载失败" };
    return;
  }
  datasetState.value = { kind: state.kind };
}

function onMicroStage(stage: PotreeStage): void {
  microStage.value = stage;
}

// ---------------- 点云数据集 ----------------

function formatPoints(n: number | undefined): string {
  if (n == null || !Number.isFinite(n)) return "点数未知";
  return `${n.toLocaleString("zh-CN")} 点`;
}

function boundsText(meta: CloudMeta | null | undefined): string {
  const b = meta?.bounds;
  if (!b) return "范围未知";
  const scope = (axis: "x" | "y" | "z"): string =>
    `${b.min[axis].toFixed(1)} ~ ${b.max[axis].toFixed(1)}`;
  return `X ${scope("x")} / Y ${scope("y")} / Z ${scope("z")} m`;
}

function datasetMetaError(code: string): string | null {
  const meta = datasetMetas.value[code];
  if (meta) return null;
  return datasetMetaErrors.value[code] ?? "尚未加载数据集元信息";
}

async function refreshDatasetMetas(): Promise<void> {
  await Promise.all(
    DATASETS.map(async (entry) => {
      try {
        const resp = await fetch(entry.metaUrl, { cache: "no-store" });
        if (!resp.ok) {
          datasetMetaErrors.value[entry.code] =
            `元信息缺失（HTTP ${resp.status}）：瓦片需在本地用 build_local_datasets.py 生成`;
          datasetMetas.value[entry.code] = null;
          return;
        }
        datasetMetas.value[entry.code] = (await resp.json()) as CloudMeta;
        delete datasetMetaErrors.value[entry.code];
      } catch (err) {
        datasetMetaErrors.value[entry.code] = `元信息加载失败：${
          err instanceof Error ? err.message : String(err)
        }`;
        datasetMetas.value[entry.code] = null;
      }
    }),
  );
}

async function selectDataset(code: DatasetEntry["code"]): Promise<void> {
  const entry = DATASETS.find((d) => d.code === code);
  if (!entry) return;
  if (
    activeDataset.value === code &&
    datasetState.value.kind === "ready" &&
    cesiumLoadedCode.value === code
  ) {
    // 再次点击同一数据集：地球视图下只飞入，不重复加载
    if (viewMode.value === "globe") globe?.flyToTileset();
    return;
  }

  designOn.value = false;
  designState.value = { kind: "empty" };
  alarm.value = null;
  alarmView.value = { kind: "idle", text: "" };
  activeDataset.value = code;
  datasetSubmitError.value = null;
  datasetTask.reset();
  datasetState.value = { kind: "loading" };
  loadStage.value = null;
  loadTiming.value = null;
  loadElapsedMs.value = 0;

  // 微观视图：加载交给 Micro3DViewer（watch dataset），不再操作隐藏的 Cesium
  if (viewMode.value === "micro") {
    microStage.value = null;
    return;
  }

  if (!globe) return;
  const startedAt = performance.now();
  loadTimer = window.setInterval(() => {
    loadElapsedMs.value = Math.round(performance.now() - startedAt);
  }, 100);
  const state = await globe.loadTileset(entry.tilesetUrl, {
    pointSize: pointSize.value,
    attenuation: attenuation.value,
    onStage: (detail) => {
      loadStage.value = detail;
      loadElapsedMs.value = detail.elapsedMs;
    },
    onTiming: (timing) => {
      loadTiming.value = timing;
    },
  });
  if (loadTimer !== null) {
    window.clearInterval(loadTimer);
    loadTimer = null;
  }
  datasetState.value = state;
  if (state.kind === "ready") {
    cesiumLoadedCode.value = code;
    globe.setTilesetShow(tilesetShow.value);
    globe.setColorMode(colorMode.value);
    // 加载成功再补一次 meta（本地瓦片生成后无需刷新页面）
    void refreshDatasetMetas();
  } else if (state.kind === "error") {
    activeDataset.value = null;
    cesiumLoadedCode.value = null;
  }
}

function reloadActiveDataset(): void {
  if (!activeDataset.value) return;
  if (viewMode.value === "micro") {
    datasetState.value = { kind: "loading" };
    microReloadToken.value += 1;
    return;
  }
  void selectDataset(activeDataset.value);
}

async function toggleDesignLayer(): Promise<void> {
  const entry = activeDatasetEntry.value;
  if (!entry?.designUrl) return;
  designOn.value = !designOn.value;
  if (!designOn.value) {
    globe?.setDesignShow(false);
    return;
  }
  const state = await globe!.loadDesignLayer(entry.designUrl);
  designState.value = state;
  if (state.kind !== "ready") {
    designOn.value = false;
    return;
  }
  globe!.setDesignShow(true);
}

async function startDatasetAnalysis(): Promise<void> {
  const entry = activeDatasetEntry.value;
  if (!entry || datasetTask.polling.value) return;
  datasetSubmitError.value = null;
  datasetTask.reset();
  try {
    const accepted = await createGeometryTask({
      component_code: entry.componentCode,
      analysis_type: entry.analysisType,
    });
    datasetTask.start(accepted.task_id);
  } catch (err) {
    datasetSubmitError.value =
      err instanceof GeometryApiError
        ? err.message
        : err instanceof Error
          ? `任务提交失败：${err.message}`
          : `任务提交失败：${String(err)}`;
  }
}

const datasetResult = computed(
  () => (datasetTask.detail.value?.result ?? null) as Record<string, unknown> | null,
);
const datasetStatusText = computed(() => {
  const d = datasetTask.detail.value;
  if (!d) return datasetTask.polling.value ? "排队中…" : "未发起";
  const map: Record<GeometryTaskStatus, string> = {
    QUEUED: "排队",
    RUNNING: "计算中",
    SUCCEEDED: "已完成",
    FAILED: "失败",
    RETRYING: "重试中",
    CANCELLED: "已取消",
    TIMEOUT: "超时",
  };
  return map[d.status] ?? d.status;
});
const datasetTilt = computed(() => {
  const v = datasetResult.value?.tilt_permille;
  return typeof v === "number" ? v : null;
});
const datasetGrade = computed(() => {
  const v = datasetResult.value?.tech_grade;
  return typeof v === "number" ? v : null;
});
const datasetReference = computed(() => {
  const v = datasetResult.value?.clearance_reference_label;
  return typeof v === "string" ? v : null;
});
const datasetMargin = computed(() => {
  const v = datasetResult.value?.design_margin_mm;
  return typeof v === "number" ? v : null;
});
const datasetReferenceNote = computed(() => {
  const v = datasetResult.value?.reference_note;
  return typeof v === "string" ? v : null;
});
// 垂直度适用性：false 时后端不回写 component.tech_grade，界面也不得把等级当结论展示。
const datasetApplicable = computed(() => {
  const v = datasetResult.value?.verticality_applicable;
  return typeof v === "boolean" ? v : undefined;
});
const datasetDegradeReason = computed(() => {
  const v = datasetResult.value?.degrade_reason;
  return typeof v === "string" ? v : null;
});
// 数据集分析同样要能出报告 / 派单（真实点云构件只能从数据集面板发起分析）。
const datasetSucceeded = computed(() => datasetTask.detail.value?.status === "SUCCEEDED");
const datasetDispatchVisible = ref(false);
const datasetDispatchTaskId = computed<string | null>(
  () => datasetTask.detail.value?.task_id ?? null,
);
const datasetAlgorithmVersion = computed<string | null>(() => {
  const v = datasetResult.value?.algorithm_version;
  return typeof v === "string" ? v : null;
});
const datasetReportBusy = ref(false);
const datasetReportError = ref<string | null>(null);

async function downloadDatasetReport(): Promise<void> {
  const taskId = datasetDispatchTaskId.value;
  if (!taskId || datasetReportBusy.value) return;
  datasetReportBusy.value = true;
  datasetReportError.value = null;
  try {
    const screenshot = captureViewerScreenshot();
    const blob = await exportGeometryReport(taskId, screenshot);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `report-${taskId}.html`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    datasetReportError.value = err instanceof Error ? err.message : String(err);
  } finally {
    datasetReportBusy.value = false;
  }
}
const datasetTaskError = computed(() => datasetTask.error.value);
const designMeta = computed(() =>
  designState.value.kind === "ready" ? designState.value.meta : null,
);

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
  // 漫游中圈体还在用；非漫游状态关弹窗即收起
  if (!roaming.value) applyAlarmZone(null);
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
  if (taskBusy.value) return;
  submitError.value = null;
  task.reset();
  displayStatus.value = "QUEUED";
  releasing.value = false;
  clearReleaseTimer();
  // 计时从“点击发起”开始，包含 POST 往返时间。
  startProgressClock();
  try {
    const accepted = await createGeometryTask({
      component_code: selectedAsset.value.label,
      analysis_type: "VERTICALITY",
    });
    task.start(accepted.task_id);
  } catch (err) {
    stopTimers();
    displayStatus.value = null;
    submitError.value =
      err instanceof GeometryApiError
        ? err.message
        : err instanceof Error
          ? `提交失败：${err.message}`
          : `提交失败：${String(err)}`;
  }
}

function showSuccessDialog(): void {
  const tilt = tiltPermille.value;
  const text =
    tilt !== null
      ? `垂直度分析完成，倾斜率 ${tilt.toFixed(3)} ‰`
      : "垂直度分析完成，但结果未返回 tilt_permille";
  void ElMessageBox.alert(text, "分析完成", {
    type: "success",
    confirmButtonText: "知道了",
  });
}

// 成功要压到最短展示时长之后再切换；失败立即呈现，不阻塞真实错误。
watch(taskStatus, (status) => {
  if (!status) return;
  if (status === "SUCCEEDED") {
    const elapsed = startedAt.value ? Date.now() - startedAt.value : MIN_VISIBLE_MS;
    const remain = Math.max(0, MIN_VISIBLE_MS - elapsed);
    clearReleaseTimer();
    releasing.value = remain > 0;
    if (remain > 0) {
      displayStatus.value = "RUNNING"; // 结构未完成：继续显示“正在计算中…”
      releaseTimer = setTimeout(() => {
        releaseTimer = null;
        releasing.value = false;
        displayStatus.value = "SUCCEEDED";
        clearProgressTimer();
        showSuccessDialog();
      }, remain);
    } else {
      displayStatus.value = "SUCCEEDED";
      clearProgressTimer();
      showSuccessDialog();
    }
    return;
  }
  if (FAILURE_STATUSES.includes(status)) {
    stopTimers();
    displayStatus.value = status;
    return;
  }
  displayStatus.value = status;
});
</script>

<template>
  <div class="stage">
    <!-- 双视图容器：默认插槽是原 Cesium 容器，微观层由 Micro3DViewer 管理 -->
    <Micro3DViewer
      ref="microViewer"
      :mode="viewMode"
      :dataset="activeDataset"
      :reload-token="microReloadToken"
      :point-size="pointSize"
      :color-mode="colorMode"
      :point-cloud-visible="tilesetShow"
      @pick="onMicroPick"
      @measure="onMicroMeasure"
      @measure-mode="onMicroMeasureMode"
      @state="onMicroState"
      @stage="onMicroStage"
    >
      <div ref="container" class="globe" />
    </Micro3DViewer>

    <!-- 大瓦片加载骨架：不要让用户对着黑屏干等（微观层有自己的进度遮罩） -->
    <div v-if="viewMode === 'globe' && tilesetState.kind === 'loading'" class="load-skeleton">
      <div class="load-skeleton-card">
        <div class="load-skeleton-title">正在加载 {{ activeDatasetEntry?.label }}</div>
        <div class="load-skeleton-bar"><i /></div>
        <div class="load-skeleton-stage">
          {{ loadStageText }} · {{ (loadElapsedMs / 1000).toFixed(1) }} s
          <template v-if="loadStage && loadStage.totalTiles > 1">
            · 已就绪 {{ loadStage.loadedTiles }}/{{ loadStage.totalTiles }} 瓦片
          </template>
        </div>
        <div class="load-skeleton-note">
          点云先显示概览层，细节分块随视角补载；不用等待全部下载完成
        </div>
      </div>
    </div>

    <!-- 左侧资产树 -->
    <AssetTree :current-id="selectedAsset.id" :nodes="assetNodes" @select="onSelectAsset" />

    <!-- 三维量测工具栏（左上角，资产树右侧） -->
    <div class="measure-toolbar" :class="{ active: activeMeasure !== null }">
      <div class="measure-buttons">
        <span class="measure-tag">量测</span>
        <button
          type="button"
          :class="{ on: activeMeasure === 'distance' }"
          @click="toggleMeasure('distance')"
        >
          距离
        </button>
        <button
          type="button"
          :class="{ on: activeMeasure === 'height' }"
          @click="toggleMeasure('height')"
        >
          高度
        </button>
        <button
          type="button"
          :class="{ on: activeMeasure === 'area' }"
          @click="toggleMeasure('area')"
        >
          面积
        </button>
        <button
          v-if="viewMode === 'micro'"
          type="button"
          :class="{ on: activeMeasure === 'profile' }"
          @click="toggleMeasure('profile')"
        >
          剖面
        </button>
        <button type="button" class="danger" @click="clearMeasure">清除</button>
      </div>
      <div v-if="measureHint" class="measure-hint">{{ measureHint }}</div>
    </div>

    <header class="hud">
      <div class="titles">
        <h1>桥隧结构智能检测与数字孪生运维平台</h1>
        <p>Vue3 + Cesium · 一期三维大屏骨架</p>
      </div>
      <OverviewBoard />
      <div class="actions">
        <div class="view-switch" role="group" aria-label="视图切换">
          <span class="view-switch-label">视图</span>
          <button
            type="button"
            :class="{ on: viewMode === 'globe' }"
            @click="setViewMode('globe')"
          >
            地球
          </button>
          <button
            type="button"
            :class="{ on: viewMode === 'micro' }"
            @click="setViewMode('micro')"
          >
            微观
          </button>
        </div>
        <button
          type="button"
          class="roaming"
          :class="{ on: roaming }"
          :disabled="viewMode === 'micro'"
          @click="toggleRoaming"
        >
          {{ roaming ? "退出内部漫游" : "进入内部漫游" }}
        </button>
        <button type="button" :disabled="viewMode === 'micro'" @click="toggleAutoRotate">
          {{ autoRotate ? "暂停自转" : "开始自转" }}
        </button>
        <button type="button" @click="resetView">复位视角</button>
      </div>
    </header>

    <!-- 右侧 3D Tiles 面板 -->
    <aside class="panel">
      <!-- 主管审批入口：待确认工单计数 + 通过/驳回 -->
      <PendingApprovalPanel :refresh-signal="approvalRefreshToken" />

      <div class="panel-title">点云数据集</div>
      <div class="panel-subtitle">
        局部工程坐标 · 未做大地基准转换（底图仅作背景，不声明位置精度）
      </div>

      <div class="dataset-list">
        <button
          v-for="ds in DATASETS"
          :key="ds.code"
          type="button"
          class="dataset-item"
          :class="{ active: activeDataset === ds.code }"
          @click="selectDataset(ds.code)"
        >
          <div class="dataset-head">
            <span class="dataset-name">{{ ds.label }}</span>
            <span class="dataset-badge">
              {{ activeDataset === ds.code ? "当前" : "点击加载" }}
            </span>
          </div>
          <div class="dataset-line">{{ formatPoints(datasetMetas[ds.code]?.pointCount) }}</div>
          <div class="dataset-line mono">{{ boundsText(datasetMetas[ds.code]) }}</div>
          <div v-if="datasetMetas[ds.code]?.sourceFile" class="dataset-line">
            源：{{ datasetMetas[ds.code]?.sourceFile }}
          </div>
          <div v-if="datasetMetaError(ds.code)" class="dataset-line warn">
            {{ datasetMetaError(ds.code) }}
          </div>
        </button>
      </div>

      <!-- 报警点快捷入口：3D 里的小红点不好找，点击等同上图的报警弹窗 -->
      <div
        v-if="
          tilesetState.kind === 'ready' &&
          activeDatasetEntry &&
          (datasetMetas[activeDatasetEntry.code]?.alarms?.length ?? 0) > 0
        "
        class="alarm-list"
      >
        <button
          v-for="a in datasetMetas[activeDatasetEntry?.code ?? '']?.alarms ?? []"
          :key="a.id"
          type="button"
          class="alarm-chip"
          @click="openAlarm(a)"
        >
          {{ a.label }}
        </button>
      </div>

      <template v-if="viewMode === 'micro'">
        <div v-if="tilesetState.kind === 'loading'" class="panel-status loading">
          <span>
            Potree 点云 · {{ microStage?.text ?? "准备中" }} · {{ microPercent }}%
            <template v-if="microStage?.points">
              · {{ microStage.points.toLocaleString("zh-CN") }} 点
            </template>
          </span>
          <span class="load-bar"><i /></span>
        </div>
        <div v-else-if="tilesetState.kind === 'error'" class="panel-status error">
          ❌ {{ tilesetState.message }}
        </div>
        <div v-else-if="tilesetState.kind === 'empty'" class="panel-status empty">
          未加载（请选择上方数据集）
        </div>
        <div v-else class="panel-status ready">
          ✅ 已加载 {{ activeDatasetEntry?.label }} · Potree 近景
        </div>
        <div class="load-timing mono">局部工程坐标 · 未做大地基准转换 · 单位 m / m²</div>
      </template>
      <template v-else>
        <div v-if="tilesetState.kind === 'loading'" class="panel-status loading">
          <span>加载 pnts 瓦片 · {{ loadStageText }} · {{ (loadElapsedMs / 1000).toFixed(1) }} s</span>
          <span class="load-bar"><i /></span>
        </div>
        <div v-else-if="tilesetState.kind === 'error'" class="panel-status error">
          ❌ {{ tilesetState.message }}
        </div>
        <div v-else-if="tilesetState.kind === 'empty'" class="panel-status empty">
          未加载（请选择上方数据集）
        </div>
        <div v-else class="panel-status ready">
          ✅ 已加载 {{ activeDatasetEntry?.label }}
        </div>
        <div v-if="loadTimingText" class="load-timing mono">{{ loadTimingText }}</div>
      </template>
      <div
        v-if="alarmView.kind !== 'idle'"
        class="alarm-view mono"
        :class="`alarm-view-${alarmView.kind}`"
      >
        {{ alarmView.text }}
      </div>
      <div v-if="roaming" class="load-timing mono">漫游渲染 {{ roamFps }} fps</div>

      <div class="panel-actions">
        <button
          type="button"
          class="primary"
          :disabled="!activeDataset || tilesetState.kind === 'loading'"
          @click="reloadActiveDataset"
        >
          重新加载
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
          :disabled="viewMode === 'micro' || tilesetState.kind !== 'ready'"
        >
          飞至此云
        </button>
      </div>

      <!-- 设计图层：只有隧道数据集有设计模型 -->
      <div v-if="activeDatasetEntry?.designUrl" class="panel-group design-group">
        <div class="panel-row">
          <span>设计图层</span>
          <label class="switch">
            <input
              type="checkbox"
              :checked="designOn"
              :disabled="viewMode === 'micro'"
              @change="toggleDesignLayer"
            />
            <span class="slider" />
          </label>
        </div>
        <div class="design-note">
          <div class="warn">设计轮廓 · 竖向基准拟合（示意）· 未做控制点校核</div>
          <div v-if="designMeta">来源：{{ designMeta.source }}</div>
          <div v-if="designMeta">
            断面 {{ designMeta.sectionCount }} 个 ·
            {{ designMeta.sectionWidthM }} × {{ designMeta.sectionHeightM }} m
          </div>
          <div v-if="designMeta">配准：{{ designMeta.registrationNote }}</div>
          <div v-else>配准：未配准，仅示意（开启前不参与任何判定）</div>
          <div v-if="designState.kind === 'error'" class="task-error">
            {{ designState.message }}
          </div>
        </div>
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

      <!-- 当前数据集 → 分析任务（隧道走净空/断面，桥梁走垂直度） -->
      <div class="task-block dataset-analysis">
        <div class="selected-row">
          数据集分析：<strong>{{ activeDatasetEntry?.label ?? "未选择数据集" }}</strong>
        </div>
        <button
          type="button"
          class="primary task-btn"
          :disabled="!activeDataset || datasetTask.polling.value"
          @click="startDatasetAnalysis"
        >
          {{
            datasetTask.polling.value
              ? "分析中…"
              : activeDatasetEntry?.analysisType === "CLEARANCE"
                ? "发起净空 / 断面分析"
                : "发起垂直度分析"
          }}
        </button>
        <div class="task-status">状态：{{ datasetStatusText }}</div>

        <template v-if="datasetResult">
          <div v-if="datasetReference" class="task-result">
            净空参照：<code>{{ datasetReference }}</code>
          </div>
          <div v-if="datasetReferenceNote" class="grade-source">
            {{ datasetReferenceNote }}
          </div>
          <div v-if="datasetMargin !== null" class="task-result">
            设计余量：<code>{{ datasetMargin.toFixed(1) }} mm</code>
            {{ datasetMargin < 0 ? "（侵限，示例口径）" : "" }}
          </div>
          <div v-if="datasetTilt !== null" class="task-result">
            倾斜率：<code>{{ datasetTilt.toFixed(3) }} ‰</code>
          </div>
          <div v-if="datasetGrade !== null && datasetApplicable !== false" class="grade-row">
            <el-tag type="warning" size="large" effect="dark">
              技术等级：{{ datasetGrade }} 类
            </el-tag>
          </div>
          <div v-else-if="datasetApplicable === false" class="grade-row">
            <el-tag type="info" size="large" effect="dark">
              垂直度不适用（非竖向构件）
            </el-tag>
          </div>
          <div v-if="datasetDegradeReason" class="grade-source">
            {{ datasetDegradeReason }}
          </div>
          <template v-if="datasetSucceeded">
            <button
              type="button"
              class="task-btn dispatch-btn"
              @click="datasetDispatchVisible = true"
            >
              一键派发维修工单
            </button>
            <button
              type="button"
              class="task-btn report-btn"
              :disabled="datasetReportBusy"
              @click="downloadDatasetReport"
            >
              {{ datasetReportBusy ? "生成中…" : "生成报告" }}
            </button>
            <div v-if="datasetReportError" class="task-error">{{ datasetReportError }}</div>
          </template>
        </template>

        <div v-if="datasetTaskError" class="task-error">{{ datasetTaskError }}</div>
        <div v-if="datasetSubmitError" class="task-error">{{ datasetSubmitError }}</div>
      </div>

      <IntrusionLegend />

      <DefectUpload :component-code="selectedAsset.label" />

      <!-- 资产选中 → 垂直度分析任务 -->
      <div class="task-block">
        <div class="selected-row">
          当前选中：<strong>{{ selectedAsset.label }}</strong>
        </div>
        <button
          type="button"
          class="primary task-btn"
          :disabled="taskBusy"
          @click="startVerticality"
        >
          {{ taskBusy ? "分析中…" : "发起垂直度分析" }}
        </button>

        <template v-if="progressVisible">
          <el-progress
            class="task-progress"
            :percentage="progressPct"
            :status="progressStatus"
            :stroke-width="8"
          />
          <div class="task-status">状态：{{ statusLabel }}</div>
          <div v-if="showResult && tiltPermille !== null" class="task-result">
            倾斜率：<code>{{ tiltPermille.toFixed(3) }} ‰</code>
          </div>
          <div v-if="showResult && techGradeLabel" class="grade-row">
            <el-tag :type="gradeTagType" size="large" effect="dark">
              当前评定：{{ techGradeLabel }}
            </el-tag>
            <button type="button" class="clause-btn" @click="clauseVisible = true">
              规范依据
            </button>
          </div>
          <div v-if="showResult && techGradeSource" class="grade-source">
            {{ techGradeSource }}：等级仅用于流程演示，不作为验收依据
          </div>
          <template v-if="showResult">
            <button
              type="button"
              class="task-btn dispatch-btn"
              @click="dispatchVisible = true"
            >
              一键派发维修工单
            </button>
            <button
              type="button"
              class="task-btn report-btn"
              :disabled="reportBusy"
              @click="downloadReport"
            >
              {{ reportBusy ? "生成中…" : "生成报告" }}
            </button>
            <div v-if="reportError" class="task-error">{{ reportError }}</div>
          </template>
        </template>

        <div v-if="taskError" class="task-error">{{ taskError }}</div>
      </div>

      <!-- P1 批量处理流水线（10 个真实构件 · 示例数据 · 合成点云） -->
      <div class="batch-block">
        <div class="batch-head">
          <span class="batch-title">批量分析</span>
          <span class="batch-badge">示例数据 · 合成点云</span>
        </div>
        <div class="batch-note">
          一次下发 PIR 1–10号桥墩 共 10 条任务（不同构件、不同倾斜率），用于演示批量建任务、
          进度聚合与 CSV 汇总，不代表真实批量生产。
        </div>
        <button
          type="button"
          class="primary task-btn"
          :disabled="batch.polling.value"
          @click="startBatchAnalysis"
        >
          {{ batch.polling.value ? "批量分析中…" : "批量分析（10 构件）" }}
        </button>

        <template v-if="batch.total.value > 0">
          <el-progress
            class="task-progress"
            :percentage="batchPercent"
            :status="batchProgressStatus"
            :stroke-width="8"
          />
          <div class="task-status">
            整批进度 {{ batch.completed.value }}/{{ batch.total.value }}（成功
            {{ batch.succeeded.value }}，失败 {{ batch.failed.value }}）
          </div>
          <table class="batch-table">
            <thead>
              <tr>
                <th>构件</th>
                <th>状态</th>
                <th>进度</th>
                <th>倾斜率‰</th>
                <th>等级</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in batchRows" :key="row.index">
                <td class="cell-code">{{ row.code }}</td>
                <td>
                  <span class="cell-status" :class="`status-${row.statusClass}`">
                    {{ row.statusLabel }}
                  </span>
                  <span v-if="row.errorCode" class="cell-error">{{ row.errorCode }}</span>
                </td>
                <td class="cell-num">{{ row.progress }}%</td>
                <td class="cell-num">{{ row.tiltText }}</td>
                <td>
                  <span class="grade-chip" :class="`chip-${row.gradeClass}`">
                    {{ row.gradeText }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
          <button
            type="button"
            class="task-btn batch-export-btn"
            :disabled="batchCsvBusy || batch.polling.value"
            @click="exportBatchCsv"
          >
            {{ batchCsvBusy ? "导出中…" : "导出批量 CSV" }}
          </button>
        </template>
        <div v-if="batchError" class="task-error">{{ batchError }}</div>
      </div>

      <!-- 点击量测结果：微观点云用局部工程坐标，绝不展示经纬度 -->
      <div v-if="viewMode === 'micro' && localMeasure" class="measure-card">
        <div class="measure-title">📍 点击位置坐标 · 点云局部</div>
        <div class="measure-row">
          <span>X（东）</span>
          <code>{{ localMeasure.x.toFixed(3) }} m</code>
        </div>
        <div class="measure-row">
          <span>Y（北）</span>
          <code>{{ localMeasure.y.toFixed(3) }} m</code>
        </div>
        <div class="measure-row">
          <span>Z（高程）</span>
          <code>{{ localMeasure.z.toFixed(3) }} m</code>
        </div>
        <div class="measure-note">局部工程坐标 · 未做大地基准转换</div>
      </div>

      <div v-else-if="viewMode === 'globe' && measure" class="measure-card">
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
        <code>{{ activeDatasetEntry?.tilesetUrl ?? "未加载数据集" }}</code>
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
        <div class="alarm-msg">
          示例偏移：{{ alarm.offsetMm }} mm
        </div>
        <div class="alarm-note">
          {{ activeDatasetEntry && datasetMetas[activeDatasetEntry.code]?.alarmsNote }}
        </div>
        <div class="alarm-coord">
          局部坐标 (ENU)：x={{ alarm.x.toFixed(2) }} · y={{ alarm.y.toFixed(2) }} ·
          z={{ alarm.z.toFixed(2) }} m
        </div>
        <div v-if="alarm.localTile" class="alarm-hint">
          就地漫游将加载该点周边 {{ alarm.localTile.radiusM.toFixed(1) }} m
          全精度点云（{{ alarm.localTile.points.toLocaleString("zh-CN") }} 点 ·
          {{ (alarm.localTile.bytes / 1024 / 1024).toFixed(1) }} MB），退出即释放；
          琥珀色圈为该范围标识（示例口径，非实测侵限边界）。
        </div>
        <button
          v-if="alarm.eye"
          type="button"
          class="alarm-roam"
          :disabled="viewMode === 'micro'"
          :title="viewMode === 'micro' ? '内部漫游为 Cesium 能力，请切换到地球视图' : undefined"
          @click="enterAlarmRoaming"
        >
          {{ viewMode === "micro" ? "切回地球视图后可漫游" : "就地内部漫游" }}
        </button>
        <div v-else class="alarm-note">
          该数据集未提供报警点就地入点（旧 meta），无法定位到该点位漫游
        </div>
      </div>
    </div>

    <!-- 规范依据（统一 5.x，弹窗里原样标注"以正式规范为准"） -->
    <el-dialog v-model="clauseVisible" title="规范依据" width="520px">
      <div class="clause-text">
        {{ standardReference ?? "规范依据待补充。" }}
      </div>
      <div class="clause-note">
        统一写为第 5.x 条（具体条款以正式规范为准），不编造具体条款号；未核验前不得据此出具正式结论。
      </div>
      <template #footer>
        <button type="button" class="task-btn" @click="clauseVisible = false">关闭</button>
      </template>
    </el-dialog>

    <!-- 数据集分析结果派单（真实点云构件走这条，资产树只覆盖示例桥墩） -->
    <DispatchWorkOrderDialog
      :visible="datasetDispatchVisible"
      :component-code="activeDatasetEntry?.componentCode ?? ''"
      :task-id="datasetDispatchTaskId"
      :tilt-permille="datasetTilt"
      :algorithm-version="datasetAlgorithmVersion"
      :degraded="datasetDegradeReason !== null"
      :degrade-reason="datasetDegradeReason"
      @update:visible="datasetDispatchVisible = $event"
      @dispatched="onDispatched"
    />

    <!-- 一键派发维修工单（AI 结果仅生成 PENDING_CONFIRM，主管复核后才下发） -->
    <DispatchWorkOrderDialog
      :visible="dispatchVisible"
      :component-code="selectedAsset.label"
      :task-id="dispatchTaskId"
      :tilt-permille="tiltPermille"
      :algorithm-version="algorithmVersion"
      :degraded="resultDegraded"
      :degrade-reason="degradeReason"
      @update:visible="dispatchVisible = $event"
      @dispatched="onDispatched"
    />
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

.actions button.roaming.on {
  border-color: transparent;
  background: linear-gradient(135deg, #7fe0a5, #4fd1ff);
  color: #04121f;
  font-weight: 600;
}

.actions button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* 视图切换：地球 / 微观 */
.view-switch {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 4px 3px 8px;
  border-radius: 8px;
  border: 1px solid rgba(120, 170, 255, 0.28);
  background: rgba(8, 20, 38, 0.72);
}

.view-switch-label {
  font-size: 11px;
  letter-spacing: 1px;
  color: #8fb8e6;
}

.view-switch button {
  padding: 5px 10px;
  font-size: 12px;
}

.actions .view-switch button.on {
  border-color: transparent;
  background: linear-gradient(135deg, #4fd1ff, #7fe0a5);
  color: #04121f;
  font-weight: 600;
}

/* ---- 三维量测工具栏 ---- */
.measure-toolbar {
  position: absolute;
  top: 96px;
  left: 288px;
  z-index: 12;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  border-radius: 12px;
  border: 1px solid rgba(120, 190, 255, 0.28);
  background: rgba(8, 20, 38, 0.62);
  backdrop-filter: blur(12px);
  pointer-events: auto;
  max-width: 430px;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.measure-toolbar.active {
  border-color: rgba(79, 209, 255, 0.55);
  box-shadow: 0 0 18px rgba(79, 209, 255, 0.18);
}

.measure-buttons {
  display: flex;
  align-items: center;
  gap: 6px;
}

.measure-tag {
  margin-right: 2px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 1px;
  color: #8fb8e6;
}

.measure-buttons button {
  padding: 6px 10px;
  border: 1px solid rgba(120, 170, 255, 0.35);
  border-radius: 6px;
  background: rgba(10, 24, 48, 0.65);
  color: #cfe2ff;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.2s ease, color 0.2s ease;
}

.measure-buttons button:hover {
  background: rgba(26, 58, 110, 0.9);
}

.measure-buttons button.on {
  border-color: transparent;
  background: linear-gradient(135deg, #4fd1ff, #7fe0a5);
  color: #04121f;
  font-weight: 600;
}

.measure-buttons button.danger {
  border-color: rgba(255, 150, 150, 0.4);
  color: #ffb3b3;
}

.measure-buttons button.danger:hover {
  background: rgba(120, 30, 40, 0.6);
}

.measure-hint {
  font-size: 11px;
  line-height: 1.5;
  color: #9fb4d0;
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

.panel-status.loading {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.load-bar {
  display: block;
  height: 3px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.12);
  overflow: hidden;
}

.load-bar i {
  display: block;
  height: 100%;
  width: 40%;
  border-radius: 2px;
  background: linear-gradient(90deg, transparent, #d9c088, transparent);
  animation: load-slide 1.2s ease-in-out infinite;
}

@keyframes load-slide {
  0% {
    transform: translateX(-110%);
  }
  100% {
    transform: translateX(320%);
  }
}

.load-timing {
  margin: -6px 0 10px;
  font-size: 11px;
  line-height: 1.5;
  color: #8fb0d6;
  word-break: break-all;
}

/* 大瓦片加载骨架：卡片居中，不挡后续操作 */
.load-skeleton {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  z-index: 6;
}

.load-skeleton-card {
  width: 320px;
  padding: 18px 20px;
  border-radius: 10px;
  background: rgba(12, 22, 38, 0.72);
  border: 1px solid rgba(120, 170, 255, 0.28);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(10px);
  color: #dbe9ff;
}

.load-skeleton-title {
  font-size: 13px;
  letter-spacing: 0.04em;
  margin-bottom: 12px;
}

.load-skeleton-bar {
  height: 4px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.1);
  overflow: hidden;
}

.load-skeleton-bar i {
  display: block;
  height: 100%;
  width: 42%;
  border-radius: 2px;
  background: linear-gradient(90deg, transparent, #6cc0ff, transparent);
  animation: load-slide 1.2s ease-in-out infinite;
}

.load-skeleton-stage {
  margin-top: 10px;
  font-size: 12px;
  color: #9fc3ee;
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
}

.load-skeleton-note {
  margin-top: 6px;
  font-size: 11px;
  line-height: 1.5;
  color: #6f8bab;
}

/* 报警点快捷入口（3D 里的小红点不好找） */
.alarm-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 2px 0 10px;
}

.alarm-chip {
  padding: 4px 9px;
  border-radius: 999px;
  border: 1px solid rgba(255, 120, 120, 0.45);
  background: rgba(90, 20, 20, 0.5);
  color: #ffbdbd;
  font-size: 11px;
  cursor: pointer;
}

.alarm-chip:hover {
  background: rgba(140, 30, 30, 0.6);
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

/* ---- 点云数据集面板 ---- */
.dataset-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 10px 0;
}

.dataset-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 9px 10px;
  text-align: left;
  border-radius: 6px;
  border: 1px solid rgba(80, 130, 220, 0.35);
  background: rgba(18, 34, 60, 0.7);
  color: #cfe0f5;
  font-size: 11px;
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}

.dataset-item:hover {
  border-color: rgba(120, 180, 255, 0.7);
  background: rgba(26, 52, 88, 0.85);
}

.dataset-item.active {
  border-color: #1fe0cf;
  box-shadow: 0 0 0 1px rgba(31, 224, 207, 0.35) inset;
}

.dataset-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.dataset-name {
  color: #e8f1ff;
  font-size: 12px;
  font-weight: 600;
}

.dataset-badge {
  padding: 1px 6px;
  border-radius: 3px;
  border: 1px solid rgba(80, 130, 220, 0.45);
  color: #9fb4d0;
  font-size: 10px;
}

.dataset-item.active .dataset-badge {
  border-color: rgba(31, 224, 207, 0.6);
  color: #7ff0e4;
}

.dataset-line {
  line-height: 1.5;
  color: #9fb4d0;
}

.dataset-line.mono {
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 10px;
  word-break: break-all;
}

.dataset-line.warn {
  color: #ffb86b;
}

.design-note {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 10px;
  line-height: 1.5;
  color: #9fb4d0;
}

.design-note .warn {
  color: #ffb86b;
  font-size: 11px;
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

.dispatch-btn {
  border-color: rgba(120, 220, 160, 0.55);
  background: rgba(32, 110, 78, 0.75);
}

.dispatch-btn:hover:not(:disabled) {
  background: rgba(40, 140, 96, 0.9);
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

/* ---- P1 批量流水线 ---- */
.batch-block {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed rgba(80, 130, 220, 0.25);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.batch-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.batch-title {
  font-size: 12px;
  font-weight: 600;
  color: #e8f1ff;
}

.batch-badge {
  padding: 2px 6px;
  border-radius: 3px;
  border: 1px solid rgba(217, 192, 136, 0.5);
  background: rgba(120, 96, 40, 0.25);
  color: #d9c088;
  font-size: 10px;
}

.batch-note {
  font-size: 11px;
  line-height: 1.5;
  color: #9fb4d0;
}

.batch-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}

.batch-table th {
  padding: 3px 2px;
  border-bottom: 1px solid rgba(80, 130, 220, 0.3);
  color: #7f96b6;
  font-weight: 500;
  text-align: left;
  white-space: nowrap;
}

.batch-table td {
  padding: 3px 2px;
  border-bottom: 1px solid rgba(80, 130, 220, 0.12);
  color: #cfe2ff;
  vertical-align: top;
}

.batch-table .cell-code {
  max-width: 76px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.batch-table .cell-num {
  text-align: right;
  white-space: nowrap;
}

.cell-status.status-ok {
  color: #7fe0a5;
}

.cell-status.status-warn {
  color: #f0c45c;
}

.cell-status.status-bad {
  color: #ff9b9b;
}

.cell-status.status-idle {
  color: #8aa0be;
}

.cell-error {
  display: block;
  color: #ff9b9b;
  font-size: 9px;
  word-break: break-all;
}

.grade-chip {
  display: inline-block;
  min-width: 22px;
  padding: 0 3px;
  border-radius: 3px;
  text-align: center;
  font-weight: 600;
}

.grade-chip.chip-ok {
  background: rgba(76, 209, 122, 0.18);
  color: #7fe0a5;
}

.grade-chip.chip-warn {
  background: rgba(240, 180, 41, 0.18);
  color: #f0c45c;
}

.grade-chip.chip-bad {
  background: rgba(255, 93, 93, 0.18);
  color: #ff9b9b;
}

.grade-chip.chip-idle {
  color: #7f96b6;
}

.batch-export-btn {
  border-color: rgba(120, 220, 160, 0.55);
  background: rgba(32, 110, 78, 0.75);
}

.batch-export-btn:hover:not(:disabled) {
  background: rgba(40, 140, 96, 0.9);
}

/* ---- 技术状况等级 ---- */
.grade-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}

.grade-row :deep(.el-tag) {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.clause-btn {
  padding: 4px 10px;
  border-radius: 4px;
  border: 1px solid rgba(120, 170, 255, 0.45);
  background: rgba(16, 36, 72, 0.7);
  color: #cfe2ff;
  font-size: 11px;
  cursor: pointer;
}

.clause-btn:hover {
  background: rgba(26, 58, 110, 0.9);
}

.grade-source {
  margin-top: 2px;
  font-size: 11px;
  color: #d9c088;
}

.report-btn {
  border-color: rgba(120, 190, 255, 0.55);
  background: rgba(30, 80, 150, 0.75);
}

.clause-text {
  font-size: 13px;
  line-height: 1.7;
  color: #1f2937;
}

.clause-note {
  margin-top: 10px;
  padding: 8px 10px;
  border-left: 3px solid #f59e0b;
  background: #fffbeb;
  color: #92400e;
  font-size: 12px;
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

.measure-note {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed rgba(120, 220, 160, 0.25);
  font-size: 10px;
  color: #7f9bbd;
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

.alarm-note {
  font-size: 11px;
  line-height: 1.5;
  color: #d9b3b3;
  background: rgba(255, 110, 110, 0.1);
  border-left: 2px solid rgba(255, 110, 110, 0.5);
  padding: 6px 8px;
  border-radius: 3px;
}

.alarm-hint {
  font-size: 11px;
  line-height: 1.5;
  color: #e8d9b0;
  background: rgba(255, 197, 61, 0.1);
  border-left: 2px solid rgba(255, 197, 61, 0.55);
  padding: 6px 8px;
  border-radius: 3px;
}

/* 就地漫游时的局部精载状态（面板内常驻，避免只在 console 里可见） */
.alarm-view {
  margin: -6px 0 10px;
  padding: 5px 8px;
  font-size: 11px;
  line-height: 1.5;
  border-radius: 4px;
}

.alarm-view-loading {
  color: #cfe4ff;
  background: rgba(90, 160, 255, 0.12);
}

.alarm-view-ready {
  color: #d6e8c8;
  background: rgba(120, 210, 140, 0.12);
}

.alarm-view-fallback {
  color: #ffe0b0;
  background: rgba(255, 197, 61, 0.12);
}

.alarm-roam {
  margin-top: 2px;
  padding: 7px 12px;
  border-radius: 6px;
  border: 1px solid rgba(120, 200, 255, 0.55);
  background: rgba(30, 90, 140, 0.55);
  color: #d8efff;
  cursor: pointer;
  font-size: 12px;
  letter-spacing: 0.04em;
}

.alarm-roam:hover {
  background: rgba(40, 130, 200, 0.65);
}
</style>
