import { engToLocal, type Vec3 } from "./coords";

/**
 * Potree 1.8.2 控制器（主工程用）。
 *
 * 职责：viewer 生命周期、数据集加载/卸载、点击拾取、原生量测桥接、
 * 加载阶段与进度输出、渲染循环暂停、容器自适应。
 *
 * 坐标口径：本文件只输出「局部工程坐标」= Potree 世界坐标 − cloud-meta.localCenter，
 * 与 pnts / 业务接口一致；当前数据未做大地基准转换，不做任何经纬度换算。
 */

export type PotreeColorMode = "rgb" | "elevation" | "intensity" | "solid";
export type PotreeMeasureMode = "distance" | "height" | "area" | "profile" | null;

export type PotreeStageName =
  | "init"
  | "metadata"
  | "hierarchy"
  | "nodes"
  | "ready"
  | "error";

export interface PotreeStage {
  stage: PotreeStageName;
  text: string;
  /** 0–100，仅用于展示；节点阶段按可见节点已载入比例估算 */
  percent: number;
  elapsedMs: number;
  loadedNodes?: number;
  totalVisibleNodes?: number;
  points?: number;
}

export interface PotreePick {
  x: number;
  y: number;
  z: number;
}

export interface PotreeMeasureInfo {
  mode: PotreeMeasureMode;
  text: string;
}

export interface PotreeDatasetSource {
  key: "tunnel" | "bridge";
  label: string;
  /** Potree 2.0 数据集的 metadata.json */
  url: string;
  /** cloud-meta.json，提供 localCenter 与推荐入点 */
  metaUrl: string;
}

export interface PotreeControllerOptions {
  onStage?: (stage: PotreeStage) => void;
  onPick?: (point: PotreePick) => void;
  onMeasure?: (info: PotreeMeasureInfo) => void;
  onMeasureMode?: (mode: PotreeMeasureMode) => void;
  onError?: (message: string) => void;
}

export interface PotreeController {
  /** 在宿主元素里创建 viewer（幂等） */
  attach(): void;
  load(source: PotreeDatasetSource): Promise<void>;
  unload(): void;
  pause(): void;
  resume(): void;
  setMeasureMode(mode: PotreeMeasureMode): void;
  clearMeasure(): void;
  resetView(): void;
  setPointSize(px: number): void;
  setColorMode(mode: PotreeColorMode): void;
  setVisible(on: boolean): void;
  resize(): void;
  captureScreenshot(): string | null;
  destroy(): void;
}

interface CloudEye {
  x: number;
  y: number;
  z: number;
  headingDeg?: number;
  floorZ?: number;
}

interface CloudMetaLite {
  localCenter?: Vec3;
  viewpoint?: { x: number; y: number; zBottom: number; headingDeg?: number };
  alarms?: Array<{ eye?: CloudEye }>;
}

const NODE_POLL_MS = 150;
const READY_TIMEOUT_MS = 20_000;

export function createPotreeController(
  host: HTMLElement,
  opts: PotreeControllerOptions = {},
): PotreeController {
  let viewer: any = null;
  let canvas: HTMLCanvasElement | null = null;
  let attached = false;
  let paused = false;

  let pointcloud: any = null;
  let localCenter: Vec3 | null = null;
  let generation = 0;
  let nodeTimer: number | null = null;
  let disposed = false;

  let pendingPick = true;
  let downPoint: { x: number; y: number } | null = null;
  let skipClick = false;
  let measureMode: PotreeMeasureMode = null;
  let colorMode: PotreeColorMode = "elevation";
  const measureListenerBound = new WeakSet<object>();

  const emitStage = (stage: PotreeStage): void => opts.onStage?.(stage);
  const emitError = (message: string): void => opts.onError?.(message);

  function stopNodePoll(): void {
    if (nodeTimer !== null) {
      window.clearInterval(nodeTimer);
      nodeTimer = null;
    }
  }

  function disposeHierarchy(nodes: unknown): void {
    if (!Array.isArray(nodes)) return;
    for (const node of nodes as any[]) {
      try {
        node?.dispose?.();
      } catch {
        // 单节点释放失败不阻塞整体卸载
      }
      if (node?.children) disposeHierarchy(node.children);
    }
  }

  function cancelInsertions(): void {
    try {
      viewer?.dispatchEvent({ type: "cancel_insertions" });
    } catch {
      // viewer 尚未初始化
    }
  }

  function clearMeasurements(): void {
    if (!viewer) return;
    const scene = viewer.scene;
    const list: any[] = Array.isArray(scene.measurements) ? scene.measurements : [];
    for (const measure of list.slice()) {
      try {
        scene.removeMeasurement(measure);
      } catch {
        try {
          scene.remove(measure);
        } catch {
          // 忽略
        }
      }
    }
    if (Array.isArray(scene.measurements)) scene.measurements.length = 0;
  }

  function unload(): void {
    generation += 1;
    stopNodePoll();
    // 卸载后回到“可拾取”状态，测量模式关闭
    pendingPick = true;
    downPoint = null;
    skipClick = false;
    if (!viewer) return;

    cancelInsertions();
    clearMeasurements();

    const scene = viewer.scene;
    const clouds: any[] = Array.isArray(scene.pointclouds) ? scene.pointclouds.slice() : [];
    for (const pc of clouds) {
      try {
        scene.scenePointCloud.remove(pc);
      } catch {
        // 已移除
      }
      disposeHierarchy(pc?.pcoGeometry?.hierarchy);
      try {
        pc?.material?.dispose?.();
      } catch {
        // material 可能已被释放
      }
    }
    if (Array.isArray(scene.pointclouds)) scene.pointclouds.length = 0;
    pointcloud = null;
    localCenter = null;
    measureMode = null;
  }

  function attach(): void {
    if (attached || disposed) return;
    if (typeof Potree === "undefined") {
      throw new Error("Potree 全局对象不存在，请先调用 ensurePotreeAssets()");
    }

    viewer = new Potree.Viewer(host);
    viewer.setEDLEnabled(true);
    viewer.setFOV(60);
    viewer.setPointBudget(2_000_000);
    viewer.setBackground("black");
    // 不调用 loadGUI()：主工程沿用右侧业务面板，避免 Potree 侧栏叠加。

    canvas = viewer.renderer.domElement as HTMLCanvasElement;
    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointerup", onPointerUp);
    canvas.addEventListener("click", onClick);

    // 量测状态回显：拿到当前 Measure 实例后同步点数/距离/面积/高差
    viewer.measuringTool.addEventListener("start_inserting_measurement", (event: any) => {
      const measure = event?.measure;
      if (!measure) return;
      bindMeasure(measure);
      emitMeasure(measure);
    });

    attached = true;
    if (paused) {
      viewer.renderer.setAnimationLoop(null);
    }
  }

  function bindMeasure(measure: any): void {
    if (measureListenerBound.has(measure)) return;
    measureListenerBound.add(measure);
    const sync = () => emitMeasure(measure);
    measure.addEventListener("marker_added", sync);
    measure.addEventListener("marker_moved", sync);
    measure.addEventListener("marker_removed", sync);
  }

  function emitMeasure(measure: any): void {
    const points: any[] = Array.isArray(measure?.points) ? measure.points : [];
    if (points.length === 0) {
      opts.onMeasure?.({ mode: measureMode, text: "等待取点…" });
      return;
    }

    let mode: PotreeMeasureMode = measureMode;
    if (!mode) {
      if (measure?.showArea) mode = "area";
      else if (measure?.showHeight) mode = "height";
      else mode = "distance";
    }

    let text: string;
    if (mode === "area") {
      const area = typeof measure?.getArea === "function" ? measure.getArea() : 0;
      text = `面积 ${area.toFixed(3)} m² · ${points.length} 点`;
    } else if (mode === "height") {
      const zs = points.map((p) => p.position.z as number);
      const height = Math.max(...zs) - Math.min(...zs);
      text = `高差 ${height.toFixed(3)} m · ${points.length} 点`;
    } else {
      const distance =
        typeof measure?.getTotalDistance === "function" ? measure.getTotalDistance() : 0;
      text = `距离 ${distance.toFixed(3)} m · ${points.length} 点`;
    }
    opts.onMeasure?.({ mode, text });
  }

  async function load(source: PotreeDatasetSource): Promise<void> {
    if (!viewer) attach();
    if (!viewer) return;

    // unload() 内部会自增 generation，随后取到的 token 才是本次加载的标识
    unload();
    const token = ++generation;
    const startedAt = performance.now();
    const elapsed = () => performance.now() - startedAt;

    emitStage({
      stage: "metadata",
      text: `读取 ${source.label} 元数据…`,
      percent: 8,
      elapsedMs: 0,
    });

    // Potree.loadPointCloud 的 Promise 在 404 时不会 reject，先自己做一次存在性检查
    try {
      const probe = await fetch(source.url, { cache: "no-store" });
      if (!probe.ok) {
        throw new Error(`数据集缺失（HTTP ${probe.status}）：${source.url}`);
      }
    } catch (error) {
      const message =
        error instanceof Error ? error.message : `数据集不可访问：${String(error)}`;
      emitStage({ stage: "error", text: message, percent: 0, elapsedMs: elapsed() });
      emitError(message);
      return;
    }

    let meta: CloudMetaLite = {};
    try {
      const resp = await fetch(source.metaUrl, { cache: "no-store" });
      if (resp.ok) meta = (await resp.json()) as CloudMetaLite;
    } catch {
      // cloud-meta 只用于入点与 localCenter，缺失时退回 0 原点
    }
    if (token !== generation) return;

    localCenter = meta.localCenter ? { ...meta.localCenter } : { x: 0, y: 0, z: 0 };

    emitStage({
      stage: "hierarchy",
      text: `解析八叉树（${source.label}）…`,
      percent: 30,
      elapsedMs: elapsed(),
    });

    let pc: any;
    try {
      const event: any = await Potree.loadPointCloud(source.url, source.label);
      pc = event?.pointcloud;
      if (!pc) throw new Error("Potree 未返回点云对象");
    } catch (error) {
      const message =
        error instanceof Error ? error.message : `点云加载失败：${String(error)}`;
      emitStage({ stage: "error", text: message, percent: 0, elapsedMs: elapsed() });
      emitError(message);
      return;
    }
    if (token !== generation || disposed) return;

    const material = pc.material;
    applyColorModeToMaterial(material);
    material.pointSizeType = Potree.PointSizeType.ADAPTIVE;
    material.size = 1.0;

    viewer.scene.addPointCloud(pc);
    pointcloud = pc;

    applyViewpoint(meta);

    const points = pc.pcoGeometry?.numPoints ?? null;
    emitStage({
      stage: "nodes",
      text: `流式加载节点（${source.label}）…`,
      percent: 45,
      elapsedMs: elapsed(),
      points: points ?? undefined,
    });

    startNodePoll(token, startedAt, source, points);
  }

  function startNodePoll(
    token: number,
    startedAt: number,
    source: PotreeDatasetSource,
    points: number | null,
  ): void {
    stopNodePoll();
    nodeTimer = window.setInterval(() => {
      if (token !== generation || !pointcloud || paused) return;
      const elapsedMs = performance.now() - startedAt;
      const nodes: any[] = Array.isArray(pointcloud.visibleNodes)
        ? pointcloud.visibleNodes
        : [];
      const loaded = nodes.reduce((sum, n) => sum + (n?.loaded ? 1 : 0), 0);
      const total = nodes.length;
      const inFlight = Number(Potree.numNodesLoading ?? 0);

      if (total > 0 && loaded === total && inFlight === 0) {
        stopNodePoll();
        emitStage({
          stage: "ready",
          text: `已加载 ${source.label} · ${(elapsedMs / 1000).toFixed(2)} s`,
          percent: 100,
          elapsedMs,
          loadedNodes: loaded,
          totalVisibleNodes: total,
          points: points ?? undefined,
        });
        return;
      }

      if (elapsedMs >= READY_TIMEOUT_MS) {
        stopNodePoll();
        emitStage({
          stage: "ready",
          text: `已就绪（节点仍在后台补载）· ${(elapsedMs / 1000).toFixed(1)} s`,
          percent: 100,
          elapsedMs,
          loadedNodes: loaded,
          totalVisibleNodes: total,
          points: points ?? undefined,
        });
        return;
      }

      const ratio = total > 0 ? loaded / total : 0;
      emitStage({
        stage: "nodes",
        text:
          total > 0
            ? `流式加载节点 ${loaded}/${total} · 在途 ${inFlight}`
            : "分配可见节点…",
        percent: Math.min(99, 45 + 54 * ratio),
        elapsedMs,
        loadedNodes: loaded,
        totalVisibleNodes: total,
        points: points ?? undefined,
      });
    }, NODE_POLL_MS);
  }

  /**
   * 用 cloud-meta 的示例报警点眼高入点定位；缺失时退回 viewpoint，再退回整体取景。
   * 坐标系：x=东、y=北、z=天（Potree 局部 ENU，Z 轴向上）。
   */
  function applyViewpoint(meta: CloudMetaLite): void {
    if (!viewer) return;
    const lc = localCenter ?? { x: 0, y: 0, z: 0 };
    const eye = Array.isArray(meta.alarms)
      ? meta.alarms.find((alarm) => alarm?.eye)?.eye
      : undefined;
    const vp = meta.viewpoint;

    let x: number;
    let y: number;
    let z: number;
    let headingDeg: number;

    if (eye) {
      x = lc.x + eye.x;
      y = lc.y + eye.y;
      z = lc.z + eye.z;
      headingDeg = eye.headingDeg ?? 0;
    } else if (vp) {
      x = lc.x + vp.x;
      y = lc.y + vp.y;
      z = lc.z + vp.zBottom + 1.6;
      headingDeg = vp.headingDeg ?? 0;
    } else {
      viewer.fitToScreen(1.0);
      return;
    }

    const heading = (headingDeg * Math.PI) / 180;
    const view = viewer.scene.view;
    view.position.set(x, y, z);
    const target = view.position
      .clone()
      .set(x + Math.sin(heading) * 40, y + Math.cos(heading) * 40, z - 1.5);
    view.lookAt(target);
  }

  function applyColorModeToMaterial(material: any): void {
    if (!material) return;
    const map: Record<PotreeColorMode, string> = {
      rgb: "rgba",
      elevation: "elevation",
      intensity: "intensity",
      // Potree 1.8.2 没有独立纯色属性，退回 rgba 并保留原色
      solid: "rgba",
    };
    material.activeAttributeName = map[colorMode];
  }

  function setColorMode(mode: PotreeColorMode): void {
    colorMode = mode;
    if (pointcloud?.material) applyColorModeToMaterial(pointcloud.material);
  }

  function setPointSize(px: number): void {
    if (!pointcloud?.material) return;
    // Cesium 默认 8px 对应 Potree ADAPTIVE 的 1.0 倍率
    pointcloud.material.size = Math.max(0.5, Math.min(3, px / 8));
  }

  function setVisible(on: boolean): void {
    if (pointcloud) pointcloud.visible = on;
  }

  function pickAtScreen(clientX: number, clientY: number): void {
    if (!viewer || !canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mouse = { x: clientX - rect.left, y: clientY - rect.top };
    try {
      const hit = Potree.Utils.getMousePointCloudIntersection(
        mouse,
        viewer.scene.getActiveCamera(),
        viewer,
        viewer.scene.pointclouds,
      );
      if (!hit) {
        return;
      }
      const world = hit.location;
      const local = engToLocal(
        { x: world.x, y: world.y, z: world.z },
        localCenter ?? { x: 0, y: 0, z: 0 },
      );
      console.log("[Micro3D] 拾取局部坐标", local);
      opts.onPick?.(local);
    } catch (error) {
      emitError(`拾取异常：${error instanceof Error ? error.message : String(error)}`);
    }
  }

  function onPointerDown(event: PointerEvent): void {
    downPoint = { x: event.clientX, y: event.clientY };
    skipClick = false;
  }

  function onPointerUp(event: PointerEvent): void {
    if (!downPoint) return;
    const moved = Math.hypot(event.clientX - downPoint.x, event.clientY - downPoint.y);
    // 拖拽旋转/平移后不再触发拾取，真正的拾取统一交给 click
    skipClick = moved > 4;
    downPoint = null;
  }

  function onClick(event: MouseEvent): void {
    if (!pendingPick) return;
    if (skipClick) {
      skipClick = false;
      return;
    }
    pickAtScreen(event.clientX, event.clientY);
  }

  function setMeasureMode(mode: PotreeMeasureMode): void {
    if (!viewer) return;
    cancelInsertions();
    measureMode = mode;
    // 量测期间让出点击拾取，避免同一次点击出现两套反馈
    pendingPick = mode === null;
    opts.onMeasureMode?.(mode);

    if (mode === "distance") {
      viewer.measuringTool.startInsertion({
        showDistances: true,
        showArea: false,
        closed: false,
        name: "Distance",
      });
    } else if (mode === "height") {
      viewer.measuringTool.startInsertion({
        showDistances: false,
        showHeight: true,
        showArea: false,
        closed: false,
        maxMarkers: 2,
        name: "Height",
      });
    } else if (mode === "area") {
      viewer.measuringTool.startInsertion({
        showDistances: true,
        showArea: true,
        closed: true,
        name: "Area",
      });
    } else if (mode === "profile") {
      viewer.profileTool.startInsertion();
    }
  }

  function clearMeasure(): void {
    cancelInsertions();
    clearMeasurements();
    measureMode = null;
    pendingPick = true;
    opts.onMeasureMode?.(null);
    opts.onMeasure?.({ mode: null, text: "" });
  }

  function resetView(): void {
    viewer?.fitToScreen(1.0);
  }

  function pause(): void {
    paused = true;
    if (viewer) viewer.renderer.setAnimationLoop(null);
  }

  function resume(): void {
    paused = false;
    if (viewer && typeof viewer.loop === "function") {
      viewer.renderer.setAnimationLoop(viewer.loop.bind(viewer));
      resize();
    }
  }

  function resize(): void {
    if (!viewer || paused) return;
    const width = host.clientWidth;
    const height = host.clientHeight;
    if (width <= 0 || height <= 0) return;
    try {
      viewer.renderer.setSize(width, height);
    } catch {
      // 容器尺寸尚未稳定
    }
  }

  function captureScreenshot(): string | null {
    if (!viewer || !canvas) return null;
    try {
      const shot = canvas.toDataURL("image/jpeg", 0.8);
      return shot.startsWith("data:image/jpeg") ? shot : null;
    } catch {
      return null;
    }
  }

  function destroy(): void {
    if (disposed) return;
    disposed = true;
    generation += 1;
    stopNodePoll();
    unload();
    if (canvas) {
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("click", onClick);
    }
    try {
      viewer?.renderer?.setAnimationLoop?.(null);
      viewer?.renderer?.dispose?.();
    } catch {
      // renderer 已释放
    }
    try {
      canvas?.remove();
    } catch {
      // 已被 DOM 清理
    }
    viewer = null;
    canvas = null;
    attached = false;
    host.innerHTML = "";
  }

  return {
    attach,
    load,
    unload,
    pause,
    resume,
    setMeasureMode,
    clearMeasure,
    resetView,
    setPointSize,
    setColorMode,
    setVisible,
    resize,
    captureScreenshot,
    destroy,
  };
}
