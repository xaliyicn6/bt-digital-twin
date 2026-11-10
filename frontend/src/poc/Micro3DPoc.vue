<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from "vue";
import {
  createPocScene,
  type PocColorMode,
  type PocLoadReport,
  type PocPick,
  type PocSceneController,
  type PocStats,
  type PocTileRef,
} from "./Micro3DScene";

interface PocMeta {
  label: string;
  sourcePoints: number;
  pointCount: number;
  localCenter: { x: number; y: number; z: number };
  zMin: number;
  zMax: number;
  alarms: Array<{
    id: string;
    label: string;
    x: number;
    y: number;
    z: number;
    zoneRadiusM?: number;
    localTile?: { pnts?: string; points: number; radiusM: number };
    eye: { x: number; y: number; z: number; headingDeg: number };
  }>;
}

interface PocTileset {
  root: {
    children?: Array<{ content?: { uri?: string } }>;
  };
}

const POINT_MODES = ["rgb", "intensity", "elevation"] as const;

const BASE = "/tiles/local/tunnel";
const host = ref<HTMLDivElement | null>(null);
const sceneRef = shallowRef<PocSceneController | null>(null);

const meta = ref<PocMeta | null>(null);
const report = ref<PocLoadReport | null>(null);
const stats = ref<PocStats | null>(null);
const pick = ref<PocPick | null>(null);
const progress = ref({ done: 0, total: 0 });
const status = ref<"idle" | "loading" | "ready" | "error">("idle");
const errorText = ref("");
const colorMode = ref<PocColorMode>("rgb");
const pointSize = ref(6);
const showOverview = ref(true);
const showChunks = ref(true);
const showAlarms = ref(true);
const fov = ref(45);

const sourceCoord = computed(() => {
  const m = meta.value;
  const p = pick.value;
  if (!m || !p) return null;
  return {
    x: (m.localCenter.x + p.x).toFixed(3),
    y: (m.localCenter.y + p.y).toFixed(3),
    z: (m.localCenter.z + p.z).toFixed(3),
  };
});

const loadText = computed(() => {
  const r = report.value;
  if (!r) return "—";
  return `${(r.bytes / 1024 / 1024).toFixed(2)} MB · ${r.tiles.length} 瓦片 · ${r.totalMs} ms`;
});

const tilesSorted = computed(() => report.value?.tiles ?? []);

async function loadJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} → HTTP ${res.status}`);
  return (await res.json()) as T;
}

function buildRefs(m: PocMeta, ts: PocTileset): PocTileRef[] {
  const refs: PocTileRef[] = [
    { label: "overview", url: `${BASE}/overview.pnts`, kind: "overview" },
  ];
  for (const child of ts.root.children ?? []) {
    const uri = child.content?.uri;
    if (uri) {
      refs.push({
        label: uri.replace(".pnts", ""),
        url: `${BASE}/${uri}`,
        kind: "chunk",
      });
    }
  }
  for (const alarm of m.alarms) {
    const pnts = alarm.localTile?.pnts;
    if (pnts) {
      refs.push({ label: alarm.id, url: `${BASE}/${pnts}`, kind: "alarm" });
    }
  }
  return refs;
}

function applyVisibility(): void {
  const scene = sceneRef.value;
  if (!scene) return;
  scene.setKindVisible("overview", showOverview.value);
  scene.setKindVisible("chunk", showChunks.value);
  scene.setKindVisible("alarm", showAlarms.value);
}

function applyColorMode(mode: PocColorMode): void {
  colorMode.value = mode;
  sceneRef.value?.setColorMode(mode);
}

function applyPointSize(): void {
  sceneRef.value?.setPointSize(pointSize.value / 100);
}

function applyFov(): void {
  sceneRef.value?.setFov(fov.value);
}

function focusAlarm(index: number): void {
  const alarm = meta.value?.alarms[index];
  if (!alarm) return;
  sceneRef.value?.focusNear(alarm.eye);
}

async function boot(): Promise<void> {
  const el = host.value;
  if (!el) return;
  status.value = "loading";
  errorText.value = "";
  const scene = createPocScene({
    container: el,
    onStats: (s) => {
      stats.value = s;
    },
    onPick: (p) => {
      pick.value = p;
    },
  });
  sceneRef.value = scene;
  try {
    const [m, ts] = await Promise.all([
      loadJson<PocMeta>(`${BASE}/cloud-meta.json`),
      loadJson<PocTileset>(`${BASE}/tileset.json`),
    ]);
    meta.value = m;
    scene.setRange(m.zMin, m.zMax);
    const refs = buildRefs(m, ts);
    progress.value = { done: 0, total: refs.length };
    const result = await scene.loadTiles(refs, (done, total) => {
      progress.value = { done, total };
    });
    report.value = result;
    applyColorMode(colorMode.value);
    status.value = "ready";
  } catch (err) {
    status.value = "error";
    errorText.value = err instanceof Error ? err.message : String(err);
  }
}

onMounted(() => {
  void boot();
});

onBeforeUnmount(() => {
  sceneRef.value?.destroy();
  sceneRef.value = null;
});
</script>

<template>
  <div class="poc-root">
    <div ref="host" class="poc-canvas" />

    <header class="poc-brand">
      <div class="poc-brand-title">Micro3D PoC</div>
      <div class="poc-brand-sub">
        Three.js · 局部 ENU 米制 · 非地球坐标系 · 读取现有 3D Tiles pnts
      </div>
    </header>

    <aside class="poc-hud">
      <section class="hud-block">
        <div class="hud-label">数据集</div>
        <div class="hud-value">
          {{ meta?.label ?? "隧道（lunxiao-L）" }}
          <span class="hud-dim">
            源 {{ meta ? (meta.sourcePoints / 10000).toFixed(0) : "—" }} 万点
          </span>
        </div>
        <div class="hud-row">
          <span>状态</span>
          <span :class="['hud-state', `is-${status}`]">
            {{
              status === "loading"
                ? `加载中 ${progress.done}/${progress.total}`
                : status === "ready"
                  ? "就绪"
                  : status === "error"
                    ? "失败"
                    : "待启动"
            }}
          </span>
        </div>
        <div v-if="errorText" class="hud-error">{{ errorText }}</div>
        <div class="hud-row">
          <span>已加载</span>
          <span>{{ loadText }}</span>
        </div>
        <div class="hud-row">
          <span>总点数</span>
          <span>{{ report?.points.toLocaleString() ?? "—" }}</span>
        </div>
        <div class="hud-row">
          <span>首帧</span>
          <span>{{ report ? `${report.firstFrameMs} ms` : "—" }}</span>
        </div>
        <div class="hud-row">
          <span>FPS</span>
          <span class="hud-hl">{{ stats?.fps ?? "—" }}</span>
        </div>
        <div class="hud-row">
          <span>帧时间 / 绘制</span>
          <span>{{ stats ? `${stats.frameMs} ms · ${stats.drawCalls}` : "—" }}</span>
        </div>
        <div class="hud-row">
          <span>可见点 / 瓦片</span>
          <span>
            {{
              stats
                ? `${stats.visiblePoints.toLocaleString()} / ${stats.visibleTiles}`
                : "—"
            }}
          </span>
        </div>
      </section>

      <section class="hud-block">
        <div class="hud-label">渲染</div>
        <div class="hud-seg">
          <button
            v-for="mode in POINT_MODES"
            :key="mode"
            :class="{ active: colorMode === mode }"
            @click="applyColorMode(mode)"
          >
            {{ mode === "rgb" ? "RGB" : mode === "intensity" ? "强度" : "高程" }}
          </button>
        </div>
        <label class="hud-slider">
          <span>点径 {{ (pointSize / 100).toFixed(2) }} m</span>
          <input
            v-model.number="pointSize"
            type="range"
            min="1"
            max="30"
            step="1"
            @input="applyPointSize"
          />
        </label>
        <label class="hud-slider">
          <span>FOV {{ fov }}°</span>
          <input v-model.number="fov" type="range" min="35" max="75" step="5" @input="applyFov" />
        </label>
        <div class="hud-checks">
          <label><input v-model="showOverview" type="checkbox" @change="applyVisibility" />概览</label>
          <label><input v-model="showChunks" type="checkbox" @change="applyVisibility" />11 分块</label>
          <label><input v-model="showAlarms" type="checkbox" @change="applyVisibility" />报警精载</label>
        </div>
      </section>

      <section class="hud-block">
        <div class="hud-label">报警点近景（眼高入点）</div>
        <div class="hud-alarms">
          <button
            v-for="(a, i) in meta?.alarms ?? []"
            :key="a.id"
            @click="focusAlarm(i)"
          >
            {{ a.label }} · R={{ a.zoneRadiusM?.toFixed(1) ?? "—" }} m
          </button>
        </div>
      </section>

      <section class="hud-block">
        <div class="hud-label">拾取</div>
        <template v-if="pick">
          <div class="hud-row">
            <span>局部 ENU (m)</span>
            <span class="hud-mono">
              {{ pick.x.toFixed(3) }}, {{ pick.y.toFixed(3) }}, {{ pick.z.toFixed(3) }}
            </span>
          </div>
          <div class="hud-row">
            <span>源工程坐标</span>
            <span class="hud-mono">
              {{ sourceCoord?.x }}, {{ sourceCoord?.y }}, {{ sourceCoord?.z }}
            </span>
          </div>
          <div class="hud-row">
            <span>所属瓦片</span>
            <span>{{ pick.label }}</span>
          </div>
          <div class="hud-row">
            <span>距相机 / 偏差</span>
            <span>{{ pick.distanceM.toFixed(2) }} m · {{ pick.screenPx }} px</span>
          </div>
        </template>
        <div v-else class="hud-dim">在地面/点云上单击取点（拖拽旋转、滚轮缩放）</div>
      </section>

      <section class="hud-block hud-tiles">
        <div class="hud-label">瓦片加载耗时</div>
        <div class="hud-tile-list">
          <div v-for="t in tilesSorted" :key="t.label" class="hud-tile">
            <span class="hud-tile-name">{{ t.label }}</span>
            <span>{{ t.ms }} ms</span>
            <span>{{ t.points.toLocaleString() }}</span>
            <span>{{ (t.bytes / 1024).toFixed(0) }} KB</span>
          </div>
        </div>
      </section>

      <footer class="hud-foot">
        局部坐标：pnts POSITION 已减 localCenter ({{ meta?.localCenter.x }},
        {{ meta?.localCenter.y }}, {{ meta?.localCenter.z }})，单位米、Z 轴向上；源工程坐标 =
        局部值 + localCenter。未加载 Cesium Ion / 在线底图。
      </footer>
    </aside>
  </div>
</template>

<style scoped>
.poc-root {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background:
    radial-gradient(1200px 700px at 30% 20%, #0d1a2e 0%, #070c16 55%, #04060b 100%);
  color: #d7e6f7;
  font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
}

.poc-canvas {
  position: absolute;
  inset: 0;
}

.poc-brand {
  position: absolute;
  left: 20px;
  top: 18px;
  pointer-events: none;
}

.poc-brand-title {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: #e8f3ff;
}

.poc-brand-sub {
  margin-top: 4px;
  font-size: 11px;
  color: #7d97b5;
}

.poc-hud {
  position: absolute;
  right: 16px;
  top: 16px;
  bottom: 16px;
  width: 336px;
  overflow-y: auto;
  padding: 14px;
  border: 1px solid rgba(90, 140, 200, 0.22);
  border-radius: 12px;
  background: rgba(8, 14, 26, 0.78);
  backdrop-filter: blur(12px);
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.45);
  font-size: 12px;
  scrollbar-width: thin;
}

.hud-block {
  padding-bottom: 10px;
  margin-bottom: 10px;
  border-bottom: 1px solid rgba(90, 140, 200, 0.14);
}

.hud-label {
  margin-bottom: 6px;
  font-size: 11px;
  letter-spacing: 0.1em;
  color: #6f8cad;
}

.hud-value {
  margin-bottom: 6px;
  font-size: 13px;
  color: #e2eefb;
}

.hud-dim {
  color: #7489a3;
  font-size: 11px;
}

.hud-row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 2px 0;
  color: #a9c0d8;
}

.hud-row > span:last-child {
  color: #d8e7f7;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.hud-hl {
  color: #5fd2ff !important;
  font-weight: 600;
}

.hud-mono {
  font-family: ui-monospace, "Cascadia Mono", Consolas, monospace;
  font-size: 11px;
}

.hud-state.is-ready {
  color: #5fe08a;
}

.hud-state.is-loading {
  color: #ffc53d;
}

.hud-state.is-error {
  color: #ff6b6b;
}

.hud-error {
  margin: 4px 0 6px;
  padding: 6px 8px;
  border-radius: 6px;
  background: rgba(255, 80, 80, 0.12);
  color: #ff9d9d;
  font-size: 11px;
}

.hud-seg {
  display: flex;
  gap: 6px;
  margin-bottom: 8px;
}

.hud-seg button,
.hud-alarms button {
  flex: 1;
  padding: 5px 8px;
  border: 1px solid rgba(90, 140, 200, 0.3);
  border-radius: 6px;
  background: rgba(20, 32, 52, 0.7);
  color: #a9c0d8;
  font-size: 11px;
  cursor: pointer;
}

.hud-seg button.active {
  border-color: rgba(95, 210, 255, 0.7);
  background: rgba(40, 110, 160, 0.35);
  color: #dcf3ff;
}

.hud-seg button:hover,
.hud-alarms button:hover {
  border-color: rgba(95, 210, 255, 0.6);
  color: #e8f6ff;
}

.hud-slider {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 6px 0;
  color: #93aac3;
}

.hud-slider span {
  width: 92px;
  flex: none;
  font-variant-numeric: tabular-nums;
}

.hud-slider input {
  flex: 1;
  accent-color: #4ab4e8;
}

.hud-checks {
  display: flex;
  gap: 14px;
  margin-top: 6px;
  color: #a9c0d8;
}

.hud-checks input {
  margin-right: 4px;
  accent-color: #4ab4e8;
}

.hud-alarms {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.hud-tile-list {
  max-height: 168px;
  overflow-y: auto;
  font-family: ui-monospace, "Cascadia Mono", Consolas, monospace;
  font-size: 10.5px;
  color: #9fb6ce;
}

.hud-tile {
  display: grid;
  grid-template-columns: 1fr 54px 62px 54px;
  gap: 4px;
  padding: 2px 0;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.hud-tile-name {
  text-align: left;
  color: #c3d6ea;
}

.hud-foot {
  color: #6d829c;
  font-size: 10.5px;
  line-height: 1.6;
}
</style>
