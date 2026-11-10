<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { engToLocal } from "./coords";

/**
 * Potree 近景点云 PoC。
 *
 * 定位：这是一个独立入口页面（/potree.html），不改动现有 App.vue 与 CesiumViewer，
 * 用于评估 Potree 在桥隧近景的渲染质量与原生量测手感。
 *
 * 数据：由 backend/scripts/build_potree.py 调用 PotreeConverter 2.1.5 生成，
 * 产物位于 frontend/public/tiles/potree/<dataset>/（已被 .gitignore 忽略）。
 * 换成真实桥隧数据时，只需替换下面 DATASETS 里的 url。
 */

type DatasetKey = "tunnel" | "bridge";

interface DatasetConfig {
  label: string;
  /** Potree 2.0 数据集的 metadata.json 地址 */
  url: string;
  /** 默认着色属性：无 RGB 的扫描数据用 elevation / intensity 更直观 */
  attribute: string;
}

const DATASETS: Record<DatasetKey, DatasetConfig> = {
  tunnel: {
    label: "隧道 lunxiao-L",
    url: "/tiles/potree/tunnel/metadata.json",
    attribute: "elevation",
  },
  bridge: {
    label: "桥梁 beam_disc",
    url: "/tiles/potree/bridge/metadata.json",
    attribute: "elevation",
  },
};

const renderArea = ref<HTMLDivElement | null>(null);
const activeKey = ref<DatasetKey>("tunnel");
const loading = ref(true);
const status = ref("初始化 Potree…");
const pickedText = ref("在点云上单击可拾取坐标");
const pointCount = ref<number | null>(null);
const cameraText = ref("相机：待定位");
const measureText = ref("量测：未开始");
const diagnostics = ref<string[]>([]);

function pushDiag(message: string) {
  diagnostics.value = [...diagnostics.value.slice(-2), message];
}

let viewer: any = null;
let canvas: HTMLCanvasElement | null = null;
let pendingPick = false;
let downPoint: { x: number; y: number } | null = null;
/** cloud-meta.json 的 localCenter：局部工程坐标 = Potree 世界坐标 − localCenter */
let localCenter: { x: number; y: number; z: number } | null = null;
let disposed = false;

function initViewer() {
  if (typeof Potree === "undefined") {
    status.value = "Potree 脚本未加载，请检查 /vendor/potree 静态资源";
    loading.value = false;
    return;
  }

  viewer = new Potree.Viewer(renderArea.value);
  viewer.setEDLEnabled(true);
  viewer.setFOV(60);
  viewer.setPointBudget(2_000_000);
  viewer.setBackground("black");

  // 原生工具条与侧边栏由 Potree 自己构建，量测/剖面按钮都在这里
  viewer.loadGUI(() => {
    try {
      viewer.setLanguage("en");
    } catch {
      // 语言切换失败不影响使用
    }
  });

  const domCanvas: HTMLCanvasElement = viewer.renderer.domElement;
  canvas = domCanvas;
  domCanvas.addEventListener("pointerdown", onPointerDown);
  domCanvas.addEventListener("pointerup", onPointerUp);

  // 量测状态回显：start_inserting_measurement 拿到当前 Measure 实例，
  // 之后按 marker_added / marker_moved 同步端点数，便于确认点位是否真的落下
  viewer.measuringTool.addEventListener("start_inserting_measurement", (event: any) => {
    const measure = event?.measure;
    if (!measure) return;
    const sync = () => {
      measureText.value = `${measure.name} · ${measure.points.length} 点`;
    };
    measure.addEventListener("marker_added", sync);
    measure.addEventListener("marker_moved", sync);
    sync();
  });

  loadDataset(activeKey.value);
}

function clearPointClouds() {
  if (!viewer) return;
  const scene = viewer.scene;
  scene.pointclouds.slice().forEach((pc: any) => scene.scenePointCloud.remove(pc));
  scene.pointclouds.length = 0;
}

function loadDataset(key: DatasetKey) {
  if (!viewer) return;
  const config = DATASETS[key];
  activeKey.value = key;
  loading.value = true;
  status.value = `加载 ${config.label}…`;
  pointCount.value = null;
  pickedText.value = "在点云上单击可拾取坐标";
  localCenter = null;
  clearPointClouds();

  const startedAt = performance.now();
  Potree.loadPointCloud(config.url, config.label, (e: any) => {
    if (disposed) return;
    const pc = e.pointcloud;
    const material = pc.material;
    material.activeAttributeName = config.attribute;
    material.pointSizeType = Potree.PointSizeType.ADAPTIVE;
    material.size = 1.0;

    viewer.scene.addPointCloud(pc);
    // 优先按 cloud-meta.viewpoint 进入隧道内部（近景评估的关键），缺失时退回整体取景
    void applyViewpoint(key).then((applied) => {
      if (!applied) viewer.fitToScreen(1.0);
    });

    pointCount.value = pc.pcoGeometry?.numPoints ?? null;
    const cost = ((performance.now() - startedAt) / 1000).toFixed(2);
    status.value = `已加载 · 用时 ${cost}s`;
    loading.value = false;
    console.log("[Potree PoC] 点云就绪", {
      dataset: key,
      points: pointCount.value,
      offset: pc.pcoGeometry?.offset,
      position: pc.position,
    });
  });
}

/**
 * 用 cloud-meta.json 的 viewpoint 把相机放进隧道内部。
 *
 * 坐标口径：pnts / Potree 的局部坐标 = 源工程坐标 − localCenter，
 * 所以“工程坐标”= localCenter + viewpoint。heading 以正北为 0 顺时针增大，
 * 而 Potree 是 Z 轴向上的局部 ENU（x=东，y=北），故方向向量取 (sin, cos, 0)。
 */
async function applyViewpoint(key: DatasetKey): Promise<boolean> {
  try {
    const response = await fetch(`/tiles/local/${key}/cloud-meta.json`);
    if (!response.ok) return false;
    const meta = await response.json();
    const vp = meta?.viewpoint;
    const lc = meta?.localCenter;
    if (!lc || !viewer) return false;
    localCenter = { x: lc.x, y: lc.y, z: lc.z };

    // 隧道数据集的 viewpoint 经实测并不在衬砌内侧（离最近点约 139m），
    // 这里优先用示例报警点的 eye（已验证落在点云内 1.6m 眼高位置）作为近景入点。
    const eye = Array.isArray(meta?.alarms)
      ? meta.alarms.find((alarm: any) => alarm?.eye)?.eye
      : undefined;

    let x: number;
    let y: number;
    let z: number;
    let headingDeg: number;
    let source: string;

    if (eye) {
      x = lc.x + eye.x;
      y = lc.y + eye.y;
      z = lc.z + eye.z;
      headingDeg = eye.headingDeg ?? 0;
      source = "示例报警点#1 入点";
    } else if (vp) {
      x = lc.x + vp.x;
      y = lc.y + vp.y;
      z = lc.z + vp.zBottom + 1.6; // 人眼高度
      headingDeg = vp.headingDeg ?? 0;
      source = "cloud-meta viewpoint";
    } else {
      return false;
    }

    const heading = (headingDeg * Math.PI) / 180;

    const view = viewer.scene.view;
    view.position.set(x, y, z);
    const target = view.position
      .clone()
      .set(x + Math.sin(heading) * 40, y + Math.cos(heading) * 40, z - 1.5);
    view.lookAt(target);
    cameraText.value = `相机：${x.toFixed(1)}, ${y.toFixed(1)}, ${z.toFixed(1)} · ${source} · 朝向 ${headingDeg}°`;
    return true;
  } catch (error) {
    pushDiag(`viewpoint 失败：${error instanceof Error ? error.message : String(error)}`);
    return false;
  }
}

/**
 * 点位坐标：先进入“待拾取”状态，下一次在画布上的单击（位移 < 4px）触发 pickAtScreen。
 */
function startPointPick() {
  pendingPick = true;
  pickedText.value = "请在点云上单击…";
}

/**
 * 屏幕坐标 → 点云拾取。
 *
 * 直接调 Potree 的 `Utils.getMousePointCloudIntersection`：内部用离屏渲染 + 深度
 * 缓冲反读实现 GPU 拾取，比遍历点云快几个数量级。失败时把 stack 打到控制台，
 * 便于确认是否属于“1.8.2 viewer + 2.0 数据”的兼容问题。
 */
function pickAtScreen(clientX: number, clientY: number) {
  if (!viewer) return;
  const dom = viewer.renderer.domElement as HTMLCanvasElement;
  const rect = dom.getBoundingClientRect();
  const mouse = { x: clientX - rect.left, y: clientY - rect.top };

  try {
    const hit = Potree.Utils.getMousePointCloudIntersection(
      mouse,
      viewer.scene.getActiveCamera(),
      viewer,
      viewer.scene.pointclouds,
    );
    if (!hit) {
      pickedText.value = "未命中点云，请对准点云再单击";
      return;
    }
    const p = hit.location;
    // Potree 世界坐标与源「工程坐标」同一量级（offset 取包围盒最小值），
    // 局部工程坐标 = 世界坐标 − localCenter，与 cloud-meta.json / pnts 口径一致。
    const local = engToLocal(
      { x: p.x, y: p.y, z: p.z },
      localCenter ?? { x: 0, y: 0, z: 0 },
    );

    pickedText.value =
      `局部 X ${local.x.toFixed(3)}  Y ${local.y.toFixed(3)}  Z ${local.z.toFixed(3)}`;
    console.log("[Potree PoC] 点击点位局部坐标", {
      local,
      world: { x: p.x, y: p.y, z: p.z },
      point: hit.point,
    });
  } catch (error) {
    pushDiag(`拾取异常：${error instanceof Error ? error.message : String(error)}`);
    console.error("[Potree PoC] 拾取失败", error instanceof Error ? error.stack : error);
    pickedText.value = "拾取失败，详见控制台";
  }
}

function onPointerDown(event: PointerEvent) {
  if (!pendingPick) return;
  downPoint = { x: event.clientX, y: event.clientY };
}

function onPointerUp(event: PointerEvent) {
  if (!pendingPick || !downPoint) return;
  const moved = Math.hypot(event.clientX - downPoint.x, event.clientY - downPoint.y);
  downPoint = null;
  if (moved > 4) return;
  pickAtScreen(event.clientX, event.clientY);
}

/** 直接调用 Potree 原生量测工具的插入接口，参数与其侧边栏按钮完全一致 */
function startMeasure(kind: "distance" | "height" | "area" | "profile") {
  if (!viewer) return;
  pendingPick = false;
  if (kind === "distance") {
    viewer.measuringTool.startInsertion({
      showDistances: true,
      showArea: false,
      closed: false,
      name: "Distance",
    });
  } else if (kind === "height") {
    viewer.measuringTool.startInsertion({
      showDistances: false,
      showHeight: true,
      showArea: false,
      closed: false,
      maxMarkers: 2,
      name: "Height",
    });
  } else if (kind === "area") {
    viewer.measuringTool.startInsertion({
      showDistances: true,
      showArea: true,
      closed: true,
      name: "Area",
    });
  } else {
    viewer.profileTool.startInsertion();
  }
}

function resetView() {
  viewer?.fitToScreen(1.0);
}

onMounted(() => {
  window.addEventListener("error", onWindowError);
  initViewer();
});

function onWindowError(event: ErrorEvent) {
  pushDiag(`${event.message} @ ${event.filename?.split("/").pop() ?? "?"}:${event.lineno}`);
}

onBeforeUnmount(() => {
  disposed = true;
  window.removeEventListener("error", onWindowError);
  if (canvas) {
    canvas.removeEventListener("pointerdown", onPointerDown);
    canvas.removeEventListener("pointerup", onPointerUp);
  }
  viewer = null;
  canvas = null;
});
</script>

<template>
  <div class="potree-demo">
    <div ref="renderArea" class="render-area"></div>

    <section class="hud hud-title">
      <div class="hud-kicker">Potree PoC · 近景点云查看器</div>
      <div class="hud-title-row">
        <h1>桥隧内部高精度点云</h1>
        <span class="badge">局部工程坐标 · 未做大地基准转换</span>
      </div>
      <div class="hud-meta">
        <span>{{ status }}</span>
        <span v-if="pointCount">· {{ pointCount.toLocaleString() }} 点</span>
      </div>
      <div class="dataset-row">
        <button
          v-for="(config, key) in DATASETS"
          :key="key"
          class="chip"
          :class="{ active: activeKey === key }"
          @click="loadDataset(key as DatasetKey)"
        >
          {{ config.label }}
        </button>
      </div>
    </section>

    <section class="hud hud-tools">
      <button class="tool" @click="startPointPick">点位坐标</button>
      <button class="tool" @click="startMeasure('distance')">距离</button>
      <button class="tool" @click="startMeasure('height')">高度</button>
      <button class="tool" @click="startMeasure('area')">面积</button>
      <button class="tool" @click="startMeasure('profile')">剖面</button>
      <button class="tool ghost" @click="resetView">重置视角</button>
    </section>

    <section class="hud hud-pick">
      <div class="hud-kicker">拾取点 · 局部坐标 (m)</div>
      <div class="pick-value">{{ pickedText }}</div>
      <div class="camera-line">{{ cameraText }}</div>
      <div class="camera-line">量测状态：{{ measureText }}</div>
      <button class="pick-button" @click="startPointPick">开始点击点云取点</button>
      <div v-if="diagnostics.length" class="diag-line">{{ diagnostics.join(" | ") }}</div>
      <div class="hud-hint">点「点位坐标」后在点云上单击 · 左键拖拽旋转 · 滚轮缩放 · 右键平移</div>
    </section>

    <div v-if="loading" class="loading-mask">
      <div class="loading-card">
        <div class="spinner"></div>
        <p>{{ status }}</p>
        <small>首次加载需解析八叉树索引，请稍候</small>
      </div>
    </div>
  </div>
</template>

<style scoped>
.potree-demo {
  position: fixed;
  inset: 0;
  overflow: hidden;
  background:
    radial-gradient(1200px 600px at 15% 0%, rgba(14, 116, 144, 0.22), transparent 60%),
    radial-gradient(900px 500px at 85% 100%, rgba(37, 99, 235, 0.18), transparent 60%),
    #05070c;
  color: #cfe8ff;
  font-family: "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
}

.render-area {
  position: absolute;
  inset: 0;
}

/* 隐藏 Potree 自带的顶部菜单条，保留左侧原生工具侧边栏 */
.potree-demo :deep(#potree_navbar) {
  display: none !important;
}

.hud {
  position: absolute;
  z-index: 10;
  padding: 12px 14px;
  border: 1px solid rgba(56, 189, 248, 0.28);
  border-radius: 10px;
  background: rgba(8, 16, 26, 0.72);
  backdrop-filter: blur(10px);
  box-shadow: 0 12px 32px rgba(2, 6, 23, 0.55);
  pointer-events: auto;
}

.hud-title {
  top: 16px;
  right: 16px;
  min-width: 300px;
}

.hud-kicker {
  font-size: 11px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: #5eead4;
}

.hud-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
}

.hud-title-row h1 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #e2f2ff;
}

.badge {
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid rgba(251, 191, 36, 0.45);
  background: rgba(251, 191, 36, 0.12);
  color: #fcd34d;
  font-size: 11px;
  white-space: nowrap;
}

.hud-meta {
  margin-top: 8px;
  font-size: 12px;
  color: #93c5fd;
}

.dataset-row {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}

.chip {
  flex: 1;
  padding: 6px 10px;
  border-radius: 8px;
  border: 1px solid rgba(56, 189, 248, 0.3);
  background: rgba(15, 23, 42, 0.8);
  color: #bae6fd;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.chip:hover {
  border-color: rgba(56, 189, 248, 0.7);
}

.chip.active {
  border-color: #38bdf8;
  background: rgba(14, 165, 233, 0.22);
  color: #e0f2fe;
}

.hud-tools {
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: 8px;
}

.tool {
  padding: 8px 14px;
  border-radius: 8px;
  border: 1px solid rgba(56, 189, 248, 0.35);
  background: rgba(14, 165, 233, 0.18);
  color: #e0f2fe;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.tool:hover {
  background: rgba(14, 165, 233, 0.34);
}

.tool.ghost {
  background: rgba(15, 23, 42, 0.7);
  color: #93c5fd;
}

.hud-pick {
  bottom: 20px;
  right: 16px;
  min-width: 240px;
}

.pick-value {
  margin-top: 6px;
  font-family: "Cascadia Mono", Consolas, monospace;
  font-size: 13px;
  color: #7dd3fc;
}

.hud-hint {
  margin-top: 6px;
  font-size: 11px;
  color: #64748b;
}

.pick-button {
  margin-top: 8px;
  padding: 5px 10px;
  border-radius: 6px;
  border: 1px solid rgba(56, 189, 248, 0.35);
  background: rgba(14, 165, 233, 0.16);
  color: #bae6fd;
  font-size: 11px;
  cursor: pointer;
}

.pick-button:hover {
  background: rgba(14, 165, 233, 0.3);
}

.camera-line {
  margin-top: 6px;
  font-size: 11px;
  color: #94a3b8;
}

.diag-line {
  margin-top: 6px;
  max-width: 320px;
  font-size: 10px;
  line-height: 1.4;
  color: #fca5a5;
  word-break: break-all;
}

.loading-mask {
  position: absolute;
  inset: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(3, 7, 18, 0.72);
}

.loading-card {
  padding: 26px 32px;
  border: 1px solid rgba(56, 189, 248, 0.3);
  border-radius: 12px;
  background: rgba(8, 16, 26, 0.92);
  text-align: center;
}

.loading-card p {
  margin: 12px 0 4px;
  color: #e0f2fe;
}

.loading-card small {
  color: #64748b;
}

.spinner {
  width: 34px;
  height: 34px;
  margin: 0 auto;
  border: 2px solid rgba(56, 189, 248, 0.2);
  border-top-color: #38bdf8;
  border-radius: 50%;
  animation: spin 0.9s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
