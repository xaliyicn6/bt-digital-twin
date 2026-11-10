/**
 * Micro3D PoC 场景（纯 Three.js，局部 ENU 米制坐标，Z 轴向上）。
 *
 * 与 Cesium 版的关键差异：
 * - 不进入 ECEF/地球椭球：pnts 的局部坐标直接当世界坐标，量测与拾取都在米制局部系里，
 *   没有 1e7 级大坐标带来的 float32 抖动；
 * - 相机 near=0.05 m、far 按点云尺度自适应，深度精度足够看 2 cm 级细节；
 * - 透视 FOV 45°，可调；不用 Cesium 的 60° 默认视锥。
 */
import {
  AxesHelper,
  Box3,
  BufferAttribute,
  BufferGeometry,
  Color,
  GridHelper,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  Points,
  Scene,
  ShaderMaterial,
  SphereGeometry,
  Vector2,
  Vector3,
  WebGLRenderer,
} from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { fetchPnts, type PntsPayload } from "./pnts";

export type PocColorMode = "rgb" | "intensity" | "elevation";
export type PocTileKind = "overview" | "chunk" | "alarm";

export interface PocTileRef {
  label: string;
  url: string;
  kind: PocTileKind;
}

export interface PocTileTiming {
  label: string;
  kind: PocTileKind;
  ms: number;
  points: number;
  bytes: number;
}

export interface PocLoadReport {
  points: number;
  bytes: number;
  firstFrameMs: number;
  totalMs: number;
  tiles: PocTileTiming[];
}

export interface PocStats {
  totalPoints: number;
  visiblePoints: number;
  visibleTiles: number;
  fps: number;
  frameMs: number;
  drawCalls: number;
}

export interface PocPick {
  x: number;
  y: number;
  z: number;
  label: string;
  distanceM: number;
  screenPx: number;
}

export interface PocEye {
  x: number;
  y: number;
  z: number;
  headingDeg: number;
}

export interface PocSceneOptions {
  container: HTMLElement;
  onStats?: (stats: PocStats) => void;
  onPick?: (pick: PocPick | null) => void;
}

export interface PocSceneController {
  loadTiles(
    refs: PocTileRef[],
    onProgress?: (done: number, total: number) => void,
  ): Promise<PocLoadReport>;
  setPointSize(sizeM: number): void;
  setColorMode(mode: PocColorMode): void;
  setFov(fovDeg: number): void;
  setKindVisible(kind: PocTileKind, visible: boolean): void;
  focusNear(eye: PocEye): void;
  fitView(): void;
  setRange(zMin: number, zMax: number): void;
  getStats(): PocStats;
  destroy(): void;
}

const PICK_RADIUS_PX = 12;

function nextFrame(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => resolve()));
}

async function runWithConcurrency<T>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<void>,
): Promise<void> {
  let cursor = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      await fn(items[index]);
    }
  });
  await Promise.all(workers);
}

interface TileEntry {
  ref: PocTileRef;
  data: PntsPayload;
  points: Points;
  colorAttr: BufferAttribute;
  timing: PocTileTiming;
  visible: boolean;
}

export function createPocScene(options: PocSceneOptions): PocSceneController {
  const { container } = options;

  const scene = new Scene();
  const camera = new PerspectiveCamera(45, 1, 0.05, 4000);
  camera.up.set(0, 0, 1);

  const renderer = new WebGLRenderer({
    antialias: true,
    alpha: true,
    powerPreference: "high-performance",
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);
  renderer.domElement.style.display = "block";
  renderer.domElement.style.width = "100%";
  renderer.domElement.style.height = "100%";
  renderer.domElement.style.touchAction = "none";
  container.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.rotateSpeed = 0.75;
  controls.zoomSpeed = 0.9;
  controls.screenSpacePanning = true;
  controls.minDistance = 0.3;
  controls.maxDistance = 3000;

  const grid = new GridHelper(1600, 64, 0x1c3c60, 0x102338);
  grid.rotation.x = Math.PI / 2;
  grid.position.set(0, 0, -12.4);
  scene.add(grid);

  const axes = new AxesHelper(25);
  axes.position.set(0, 0, -12.4);
  scene.add(axes);

  // 自写点着色器：像素上下限 + 圆形点。
  // 只用 PointsMaterial 的 sizeAttenuation 时，0.06 m 的点在 200 m 外只有 0.1 px，
  // 会被光栅化直接丢掉；Cesium 的固定 8 px 又会在 2 m 近景糊成方块。
  // 这里按"世界尺寸换算像素"再夹上下限：远处不消失（uMinPx），
  // 近处不糊成大片色块（uMaxPx，贴脸 0.3 m 时 0.06 m 点原本会撑到 200 px）。
  const material = new ShaderMaterial({
    uniforms: {
      uSize: { value: 0.06 },
      uMinPx: { value: 1.8 },
      uMaxPx: { value: 18 },
      uViewportH: { value: 900 },
    },
    vertexShader: `
      attribute vec3 aColor;
      uniform float uSize;
      uniform float uMinPx;
      uniform float uMaxPx;
      uniform float uViewportH;
      varying vec3 vColor;
      void main() {
        vColor = aColor;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        float px = uSize * projectionMatrix[1][1] * uViewportH * 0.5 / max(0.001, -mv.z);
        gl_PointSize = clamp(px, uMinPx, uMaxPx);
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: `
      varying vec3 vColor;
      void main() {
        vec2 d = gl_PointCoord - vec2(0.5);
        if (dot(d, d) > 0.25) discard;
        gl_FragColor = vec4(vColor, 1.0);
      }
    `,
    transparent: false,
    depthWrite: true,
    depthTest: true,
  });

  const marker = new Mesh(
    new SphereGeometry(0.16, 16, 16),
    new MeshBasicMaterial({ color: 0xffc53d, depthTest: false }),
  );
  marker.renderOrder = 10;
  marker.visible = false;
  scene.add(marker);

  const tiles = new Map<string, TileEntry>();
  let colorMode: PocColorMode = "rgb";
  let zMin = -12.4;
  let zMax = 12.4;
  const tmpColor = new Color();
  const projected = new Vector3();

  const clamp01 = (v: number): number => Math.min(1, Math.max(0, v));

  function paintColors(): void {
    for (const entry of tiles.values()) {
      const { count, positions, colors, intensity } = entry.data;
      const out = entry.colorAttr.array as Float32Array;
      if (colorMode === "rgb") {
        for (let i = 0; i < count * 3; i += 1) out[i] = colors[i] / 255;
      } else if (colorMode === "intensity") {
        for (let i = 0; i < count; i += 1) {
          tmpColor.setHSL(0.1, 0.9, 0.18 + 0.45 * clamp01(intensity[i]));
          const o = i * 3;
          out[o] = tmpColor.r;
          out[o + 1] = tmpColor.g;
          out[o + 2] = tmpColor.b;
        }
      } else {
        const span = Math.max(0.001, zMax - zMin);
        for (let i = 0; i < count; i += 1) {
          const t = clamp01((positions[i * 3 + 2] - zMin) / span);
          tmpColor.setHSL(0.66 - 0.66 * t, 0.85, 0.55);
          const o = i * 3;
          out[o] = tmpColor.r;
          out[o + 1] = tmpColor.g;
          out[o + 2] = tmpColor.b;
        }
      }
      entry.colorAttr.needsUpdate = true;
    }
  }

  function addTile(ref: PocTileRef, data: PntsPayload, timing: PocTileTiming): void {
    const { count } = data;
    const floatColors = new Float32Array(count * 3);
    for (let i = 0; i < count * 3; i += 1) floatColors[i] = data.colors[i] / 255;

    const geometry = new BufferGeometry();
    geometry.setAttribute("position", new BufferAttribute(data.positions, 3));
    const colorAttr = new BufferAttribute(floatColors, 3);
    geometry.setAttribute("aColor", colorAttr);
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();

    const points = new Points(geometry, material);
    points.name = ref.label;
    points.frustumCulled = true;
    scene.add(points);

    tiles.set(ref.label, {
      ref,
      data,
      points,
      colorAttr,
      timing,
      visible: true,
    });
    if (colorMode !== "rgb") paintColors();
  }

  async function loadOne(ref: PocTileRef): Promise<PocTileTiming> {
    const started = performance.now();
    const { payload, bytes } = await fetchPnts(ref.url);
    const timing: PocTileTiming = {
      label: ref.label,
      kind: ref.kind,
      ms: Math.round(performance.now() - started),
      points: payload.count,
      bytes,
    };
    addTile(ref, payload, timing);
    return timing;
  }

  function pickAt(clientX: number, clientY: number): PocPick | null {
    const rect = renderer.domElement.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return null;
    const nx = ((clientX - rect.left) / rect.width) * 2 - 1;
    const ny = -((clientY - rect.top) / rect.height) * 2 + 1;
    camera.updateMatrixWorld();
    let best: { entry: TileEntry; index: number } | null = null;
    let bestD2 = PICK_RADIUS_PX * PICK_RADIUS_PX;

    for (const entry of tiles.values()) {
      if (!entry.visible) continue;
      const pos = entry.data.positions;
      const count = entry.data.count;
      for (let i = 0; i < count; i += 1) {
        projected.set(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]).project(camera);
        if (projected.z < -1 || projected.z > 1) continue;
        const dx = (projected.x - nx) * rect.width * 0.5;
        const dy = (projected.y - ny) * rect.height * 0.5;
        const d2 = dx * dx + dy * dy;
        if (d2 < bestD2) {
          bestD2 = d2;
          best = { entry, index: i };
        }
      }
    }
    if (!best) return null;
    const pos = best.entry.data.positions;
    const x = pos[best.index * 3];
    const y = pos[best.index * 3 + 1];
    const z = pos[best.index * 3 + 2];
    marker.position.set(x, y, z);
    marker.visible = true;
    return {
      x,
      y,
      z,
      label: best.entry.ref.label,
      distanceM: camera.position.distanceTo(new Vector3(x, y, z)),
      screenPx: Math.round(Math.sqrt(bestD2) * 10) / 10,
    };
  }

  let downX = 0;
  let downY = 0;
  let downAt = 0;
  const onPointerDown = (event: PointerEvent): void => {
    downX = event.clientX;
    downY = event.clientY;
    downAt = performance.now();
  };
  const onPointerUp = (event: PointerEvent): void => {
    if (event.button !== 0) return;
    if (Math.hypot(event.clientX - downX, event.clientY - downY) > 4) return;
    if (performance.now() - downAt > 800) return;
    const pick = pickAt(event.clientX, event.clientY);
    marker.visible = pick !== null;
    options.onPick?.(pick);
  };
  renderer.domElement.addEventListener("pointerdown", onPointerDown);
  renderer.domElement.addEventListener("pointerup", onPointerUp);

  let frames = 0;
  let fps = 0;
  let frameMs = 0;
  let fpsStart = performance.now();
  let lastFrameAt = performance.now();

  function computeStats(): PocStats {
    let totalPoints = 0;
    let visiblePoints = 0;
    let visibleTiles = 0;
    for (const entry of tiles.values()) {
      totalPoints += entry.data.count;
      if (entry.visible) {
        visiblePoints += entry.data.count;
        visibleTiles += 1;
      }
    }
    return {
      totalPoints,
      visiblePoints,
      visibleTiles,
      fps: Math.round(fps * 10) / 10,
      frameMs: Math.round(frameMs * 10) / 10,
      drawCalls: renderer.info.render.calls,
    };
  }

  renderer.setAnimationLoop(() => {
    const now = performance.now();
    frameMs = now - lastFrameAt;
    lastFrameAt = now;
    controls.update();
    renderer.render(scene, camera);
    frames += 1;
    if (now - fpsStart >= 500) {
      fps = (frames * 1000) / (now - fpsStart);
      frames = 0;
      fpsStart = now;
      options.onStats?.(computeStats());
    }
  });

  const resize = (): void => {
    const w = container.clientWidth;
    const h = container.clientHeight;
    if (w === 0 || h === 0) return;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h, false);
    const buffer = renderer.getDrawingBufferSize(new Vector2());
    material.uniforms.uViewportH.value = buffer.y;
    material.uniforms.uMinPx.value = 1.8 * renderer.getPixelRatio();
  };
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(container);
  resize();

  function fitView(): void {
    if (tiles.size === 0) return;
    const box = new Box3();
    let densest: TileEntry | null = null;
    for (const entry of tiles.values()) {
      const bb = entry.points.geometry.boundingBox;
      if (bb) box.union(bb);
      if (!densest || entry.data.count > densest.data.count) densest = entry;
    }
    if (box.isEmpty()) return;
    const size = box.getSize(new Vector3());
    // 弧形隧道的全局包围盒中心 / cloud-meta.viewpoint 都可能落在弧线内侧的空处
    // （实测 viewpoint 距最近点 139 m），直接取景会看到空白。
    // 用点数最多那块瓦片的包围盒中心：分块最长 150 m，中心一定挨着点云。
    const target =
      densest?.points.geometry.boundingBox?.getCenter(new Vector3()) ??
      box.getCenter(new Vector3());
    const maxDim = Math.max(size.x, size.y, size.z);
    const dir = new Vector3(0.55, -0.78, 0.32).normalize();
    camera.position.copy(target).addScaledVector(dir, 90);
    camera.near = 0.1;
    camera.far = Math.max(2000, maxDim * 6);
    camera.updateProjectionMatrix();
    controls.target.copy(target);
    controls.update();
    grid.position.set(target.x, target.y, zMin);
    axes.position.set(target.x, target.y, zMin);
  }

  return {
    async loadTiles(refs, onProgress) {
      const started = performance.now();
      const timings: PocTileTiming[] = [];
      let points = 0;
      let bytes = 0;

      const consume = (timing: PocTileTiming): void => {
        timings.push(timing);
        points += timing.points;
        bytes += timing.bytes;
      };

      const first = refs[0];
      if (first) {
        consume(await loadOne(first));
        onProgress?.(timings.length, refs.length);
      }

      await nextFrame();
      const firstFrameMs = Math.round(performance.now() - started);

      let done = timings.length;
      await runWithConcurrency(refs.slice(1), 4, async (ref) => {
        consume(await loadOne(ref));
        done += 1;
        onProgress?.(done, refs.length);
      });

      fitView();
      return {
        points,
        bytes,
        firstFrameMs,
        totalMs: Math.round(performance.now() - started),
        tiles: timings.sort((a, b) => a.label.localeCompare(b.label)),
      };
    },

    setPointSize(sizeM) {
      material.uniforms.uSize.value = sizeM;
    },

    setColorMode(mode) {
      colorMode = mode;
      paintColors();
    },

    setFov(fovDeg) {
      camera.fov = fovDeg;
      camera.updateProjectionMatrix();
    },

    setKindVisible(kind, visible) {
      for (const entry of tiles.values()) {
        if (entry.ref.kind !== kind) continue;
        entry.visible = visible;
        entry.points.visible = visible;
      }
      options.onStats?.(computeStats());
    },

    focusNear(eye) {
      const heading = (eye.headingDeg * Math.PI) / 180;
      // 站位退到报警点后方 5 m，视线略向下 7°，让精载球体完整落在画面里，
      // 而不是站在球心内部看成一团白。
      const dir = new Vector3(Math.sin(heading), Math.cos(heading), -0.12).normalize();
      const base = new Vector3(eye.x, eye.y, eye.z);
      camera.position.copy(base).addScaledVector(dir, -5);
      controls.target.copy(base).addScaledVector(dir, 5);
      camera.near = 0.02;
      camera.far = 600;
      camera.updateProjectionMatrix();
      controls.update();
      marker.visible = false;
      options.onPick?.(null);
    },

    fitView,

    setRange(nextZMin, nextZMax) {
      zMin = nextZMin;
      zMax = nextZMax;
      if (colorMode === "elevation") paintColors();
    },

    getStats: computeStats,

    destroy() {
      renderer.setAnimationLoop(null);
      resizeObserver.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      renderer.domElement.removeEventListener("pointerup", onPointerUp);
      controls.dispose();
      for (const entry of tiles.values()) {
        entry.points.geometry.dispose();
      }
      tiles.clear();
      material.dispose();
      marker.geometry.dispose();
      marker.material.dispose();
      scene.clear();
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
