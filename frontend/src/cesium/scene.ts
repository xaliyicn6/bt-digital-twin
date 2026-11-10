/**
 * Cesium 场景封装。
 * - 底图：天地图卫星影像（沿用 VegtationMon 的 key）→ 无 key 时 OSM 兜底。
 * - 禁用 Cesium ion（AGENTS.md 第 2/6 条）。
 * - 3D Tiles 瓦片加载：通过 GlobeController 暴露 loadTileset / setTilesetShow / setPointSize / flyToTileset / clearTileset。
 *   点云瓦片走 pnts 格式，后端 python 脚本在 backend/scripts/generate_3dtiles.py，
 *   生成的 tileset.json + .pnts 放在 frontend/public/tiles/，Vite 自动挂根路径。
 *
 * 所有 Cesium 对象**刻意用普通变量**存放，不包 Vue ref——Viewer/Tileset 都是重对象，
 * 不经过 Proxy 包装才能避免 getter 性能陷阱和意外触发响应式更新。
 */
import {
  CallbackProperty,
  Cartesian2,
  Cartesian3,
  Cartographic,
  Cesium3DTileStyle,
  Cesium3DTileset,
  Color,
  Entity,
  GeometryInstance,
  HeadingPitchRange,
  HorizontalOrigin,
  ImageryLayer,
  Ion,
  Matrix4,
  OpenStreetMapImageryProvider,
  PolylineColorAppearance,
  PolylineGeometry,
  Primitive,
  ScreenSpaceEventHandler,
  ScreenSpaceEventType,
  Transforms,
  VerticalOrigin,
  Viewer,
  WebMapTileServiceImageryProvider,
  WebMercatorTilingScheme,
} from "cesium";

// 点云的地理锚点由各自 cloud-meta.json 的 center 决定；页面不再写死坐标。
// 坐标系口径见 backend/config/pointcloud_sources.yaml（局部工程坐标 · 未做大地基准转换）。

// 与 D:\project\AI\VegtationMon\vegfe 保持一致的天地图 key；可用环境变量覆盖
const DEFAULT_TIANDITU_TOKEN = "bec181d9f0852500dfacaea5ac2ace66";

const TIANDITU_TOKEN = (
  import.meta.env.VITE_TIANDITU_TOKEN ?? DEFAULT_TIANDITU_TOKEN
).trim();

// 禁用 Cesium ion（官方写法就是赋 undefined，但类型声明为 string，故显式断言）
Ion.defaultAccessToken = undefined as unknown as string;

// 初始视角：东亚上空，能同时看到中国与周边地形
export const INITIAL_VIEW = { lon: 108, lat: 30, height: 12_000_000 } as const;

// 每帧绕 Z 轴转约 0.05°，肉眼可见但不至于眩晕
const ROTATE_RAD_PER_FRAME = -0.0009;

// 3D Tiles 默认参数
const DEFAULT_POINT_SIZE = 8.0;  // pixels —— 开发期调大一点确保能看到
const DEFAULT_ATTENUATION = false;  // 关掉衰减，远处也能看清点云

/**
 * 屏幕空间误差阈值（像素）。
 *
 * 取 Cesium 默认值 16：隧道瓦片是「概览根瓦片 + 沿轴分块」两层结构，叶子
 * geometricError=0，16 与 8 在视觉上没有差别，但 16 会让相机飞近时更晚触发
 * 分块细化，避免在 1~2 km 外就把 10 个分块全部请求下来。
 */
const MAX_SCREEN_SPACE_ERROR = 16;

/** 加载阶段（用于界面给出可见的进度反馈）。 */
export type TilesetLoadStage = "tileset" | "content" | "gpu" | "visible" | "done";

export interface TilesetLoadStageDetail {
  stage: TilesetLoadStage;
  /** 已完成的瓦片内容数。 */
  loadedTiles: number;
  /** tileset 里登记的瓦片总数（根 + 分块）。 */
  totalTiles: number;
  elapsedMs: number;
}

/**
 * 单次加载的分阶段耗时（毫秒，均相对 `loadTileset` 调用时刻）。
 *
 * - `tilesetJsonMs`：tileset.json 解析完成（还没开始下载 pnts）
 * - `networkMs` / `networkBytes`：浏览器 Resource Timing 里本批 pnts 请求的
 *   最早开始到最晚结束；`contentReadyMs` 与它的差值即 Cesium 解析（含 Web Worker 解码）
 * - `firstDrawMs`：首个瓦片内容就绪 → 下一帧渲染，含 GPU 上传 + 首次绘制
 * - `firstVisibleMs`：首个瓦片真正通过遍历/裁剪进入画面（Cesium `tileVisible`）
 */
export interface TilesetLoadTiming {
  tilesetJsonMs: number | null;
  contentReadyMs: number | null;
  /** 本批 pnts 请求的最早开始时刻（相对 loadTileset 调用）。 */
  networkStartMs: number | null;
  /** 本批 pnts 请求的最晚结束时刻（相对 loadTileset 调用）。 */
  networkEndMs: number | null;
  networkMs: number | null;
  networkBytes: number | null;
  pntsRequests: number;
  firstDrawMs: number | null;
  firstVisibleMs: number | null;
  allLoadedMs: number | null;
  pointCount: number | null;
}

function tiandituProvider(): WebMapTileServiceImageryProvider {
  return new WebMapTileServiceImageryProvider({
    url:
      "https://t{s}.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0" +
      "&LAYER=img&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles" +
      "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=" +
      TIANDITU_TOKEN,
    layer: "img",
    style: "default",
    tileMatrixSetID: "w",
    format: "tiles",
    subdomains: ["0", "1", "2", "3", "4", "5", "6", "7"],
    maximumLevel: 18,
    tilingScheme: new WebMercatorTilingScheme(),
  });
}

function baseLayer(): ImageryLayer | undefined {
  if (TIANDITU_TOKEN) {
    return new ImageryLayer(tiandituProvider());
  }
  return new ImageryLayer(
    new OpenStreetMapImageryProvider({ url: "https://tile.openstreetmap.org/" }),
  );
}

/** 3D Tiles 加载结果。kind 区分成功/失败/正在加载，方便 UI 层决策。 */
export type TilesetLoadState =
  | { kind: "loading" }
  | { kind: "ready"; tileset: Cesium3DTileset }
  | { kind: "error"; message: string }
  | { kind: "empty" };

/** 点云着色模式。切换只改 tileset.style，不重新请求瓦片。 */
export type PointColorMode = "rgb" | "elevation" | "intensity" | "solid";

/** 报警点就地漫游入点（ENU 局部米，z 已含 1.6 m 人眼高度）。 */
export interface CloudMetaAlarmEye {
  x: number;
  y: number;
  z: number;
  headingDeg: number;
  /** 该处点云底面高程（局部 Z），用于漫游不穿地下限。 */
  floorZ: number;
}

/** 报警点周边全精度局部瓦片（由 build_alarm_tiles.py 生成）。 */
export interface CloudMetaAlarmLocalTile {
  /** 局部 tileset.json 文件名（与主 tileset 同目录）。 */
  uri: string;
  /** pnts 文件名（仅追溯用）。 */
  pnts?: string;
  points: number;
  bytes: number;
  /** 实际覆盖半径（米），与范围圈一致。 */
  radiusM: number;
}

/** 后端 cloud-meta.json 里的侵限报警点（ENU 局部坐标，米）。 */
export interface CloudMetaAlarm {
  id: string;
  label: string;
  x: number;
  y: number;
  z: number;
  offsetMm: number;
  /** 报警点来源口径（SEGMENT_LOW_POINT = 区段最低点示例，非 CLEARANCE 结论）。 */
  source?: string;
  /** 就地漫游入点；缺省时前端退回 dataset viewpoint。 */
  eye?: CloudMetaAlarmEye;
  /** 周边全精度局部瓦片；缺省表示只能看抽稀视图。 */
  localTile?: CloudMetaAlarmLocalTile;
  /** 问题范围圈半径（米），与 localTile.radiusM 一致。 */
  zoneRadiusM?: number;
}

/** 报警点问题范围标识（水平圆环 + 短立柱 + 标签）。 */
export interface AlarmZoneSpec {
  /** 圆心（ENU 局部米），通常取报警点。 */
  x: number;
  y: number;
  /** 环所在高度（ENU 局部米），取该处点云底面。 */
  z: number;
  radiusM: number;
  /** 标签文案；缺省显示“示例报警范围 R=x.x m”。 */
  label?: string;
}

/** 局部精载瓦片加载结果（含分阶段耗时，供验收对比）。 */
export interface AlarmDetailLoadResult {
  kind: "ready" | "error";
  message?: string;
  points: number;
  bytes: number;
  tilesetJsonMs: number | null;
  contentReadyMs: number | null;
  firstDrawMs: number | null;
}

/**
 * 第一人称入点（ENU 局部坐标，米）。
 *
 * 不能用 `center`（ENU 原点）当入点：原点由生成脚本按 `min/max` 包围盒中心决定，
 * 少量离群点会把包围盒拉大几百米，原点可能落在点云主体之外的空隙。
 * 这里由脚本按 2%~98% 分位取“点云主体”中心、底部高程与长轴朝向。
 */
export interface CloudMetaViewpoint {
  /** 入点 X（东向，米）。 */
  x: number;
  /** 入点 Y（北向，米）。 */
  y: number;
  /** 主体底部高程（局部 Z，米）；眼高 = zBottom + 1.6。 */
  zBottom: number;
  /** 入点朝向（度，0=北，90=东），沿点云主轴，保证视线前方有点。 */
  headingDeg: number;
  /** 允许漫游的水平半径（米）；无椭圆字段时用它限制。 */
  radius: number;
  /** 主体中心 X（米）。旧版 meta 没有，退回入点 x。 */
  centerX?: number;
  /** 主体中心 Y（米）。 */
  centerY?: number;
  /** 主体主轴朝向（度，0=北，90=东）。 */
  majorHeadingDeg?: number;
  /** 边界椭圆沿主轴的半长（米）。 */
  halfLength?: number;
  /** 边界椭圆沿法向的半宽（米）。 */
  halfWidth?: number;
}

/** 后端 cloud-meta.json：着色范围 + 报警点 + 漫游入点。 */
export interface CloudMeta {
  pointCount: number;
  center: { lon: number; lat: number; height: number };
  zMin: number;
  zMax: number;
  intensityMin: number;
  intensityMax: number;
  alarms: CloudMetaAlarm[];
  /** 旧版 meta 没有该字段，前端需回退到包围球/原点方案。 */
  viewpoint?: CloudMetaViewpoint;

  // ---- 本地点云数据集新增字段（旧 meta 可能缺失） ----
  datasetCode?: string;
  label?: string;
  sourceFile?: string;
  sourcePoints?: number;
  renderedPoints?: number;
  /** 坐标系标记；当前统一为 LOCAL_ENGINEERING。 */
  crs?: string;
  /** 坐标系说明（界面显著标注）。 */
  crsNote?: string;
  /** pnts 局部坐标 = 源工程坐标 − localCenter。 */
  localCenter?: { x: number; y: number; z: number };
  /** 源工程坐标包围盒（米）。 */
  bounds?: {
    min: { x: number; y: number; z: number };
    max: { x: number; y: number; z: number };
  };
  deprecated?: boolean;
  deprecatedNote?: string;
  alarmsNote?: string;
  /** 报警点来源口径说明（示例数据必须让界面看得见）。 */
  alarmSource?: string;
}

/** 设计图层元信息（design-model.json 的 meta）。 */
export interface DesignLayerMeta {
  source: string;
  axisSource: string;
  sectionVertices: number;
  sectionWidthM: number;
  sectionHeightM: number;
  sectionCount: number;
  ringSpacingM: number;
  registration: string;
  registrationNote: string;
  crs?: string;
  crsNote?: string;
}

/** 设计图层加载状态；kind=ready 时 meta 必有值。 */
export type DesignLayerState =
  | { kind: "empty" }
  | { kind: "loading" }
  | { kind: "ready"; meta: DesignLayerMeta; visible: boolean }
  | { kind: "error"; message: string };

/** 点击量测结果（WGS84 经纬度 + 椭球高）。 */
export interface MeasureResult {
  longitude: number;
  latitude: number;
  height: number;
}

/** 屏幕坐标 → 世界坐标的拾取来源。 */
export type PickSource = "feature" | "depth" | "ellipsoid";

/** 拾取结果。source 决定精度：feature（逐点属性）最准，depth 次之，ellipsoid 最差。 */
export interface PickPoint {
  world: Cartesian3;
  source: PickSource;
}

/** 瓦片包围球（用于漫游边界限制）。 */
export interface TilesetBound {
  center: Cartesian3;
  radius: number;
}

export interface GlobeController {
  viewer: Viewer;
  setAutoRotate(on: boolean): void;
  /**
   * 暂停/恢复 Cesium 默认渲染循环。
   * 切到微观点云视图时暂停，避免 Cesium 与 Potree 两套 WebGL 同时常驻渲染。
   */
  setRenderPaused(paused: boolean): void;
  resetView(): void;

  // ---- 3D Tiles ----
  /**
   * 加载 3D Tiles pnts 瓦片。
   * @param url 相对 / 绝对 URL；Vite public 目录下的文件走 '/tiles/tunnel/tileset.json'
   * @param opts 默认点大小 / 距离衰减，以及加载阶段与耗时回调
   * @returns Promise<TilesetLoadState> 调用方决定怎么显示状态
   */
  loadTileset(
    url: string,
    opts?: {
      pointSize?: number;
      attenuation?: boolean;
      onStage?: (detail: TilesetLoadStageDetail) => void;
      onTiming?: (timing: TilesetLoadTiming) => void;
    },
  ): Promise<TilesetLoadState>;
  /** 清空当前瓦片场景（销毁 + 释放资源）。 */
  clearTileset(): void;
  /** 是否存在已加载的瓦片。 */
  hasTileset(): boolean;

  /** 控制瓦片显隐；没有已加载瓦片时静默忽略。 */
  setTilesetShow(on: boolean): void;
  /** 调整点大小（像素）；范围 [0.5, 10] 内。 */
  setPointSize(px: number): void;
  /** 距离衰减开关（远小点大）。 */
  setAttenuation(on: boolean): void;
  /** 飞到瓦片包围盒中心，相机距包围盒对角线约 1.5 倍。无瓦片时静默忽略。 */
  flyToTileset(): void;

  // ---- 设计图层（隧道设计断面环，青色线框；不参与拾取） ----
  /** 加载设计模型（design-model.json）；失败返回 error，不抛异常。 */
  loadDesignLayer(url: string): Promise<DesignLayerState>;
  /** 设计图层显隐；未加载时静默忽略。 */
  setDesignShow(on: boolean): void;
  /** 是否存在已加载设计图层。 */
  hasDesignLayer(): boolean;
  /** 当前设计图层元信息；未加载为 null。 */
  getDesignMeta(): DesignLayerMeta | null;
  /** 卸载设计图层（释放 primitive）。 */
  clearDesignLayer(): void;

  /**
   * 切换点云着色模式。
   * 只改 `tileset.style`（Cesium3DTileStyle），不重新请求任何瓦片，切换是即时的。
   */
  setColorMode(mode: PointColorMode): void;
  /** 注册/注销点云点击量测回调；传 null 取消。 */
  onMeasure(cb: ((result: MeasureResult) => void) | null): void;
  /** 注册/注销报警点点击回调；传 null 取消。 */
  onAlarm(cb: ((alarm: CloudMetaAlarm) => void) | null): void;
  /** 当前已解析的 cloud-meta.json（未加载/加载失败时为 null）。 */
  getCloudMeta(): CloudMeta | null;

  /**
   * 量测模式激活时让位：关闭现有“点击点云弹坐标卡”的 handler。
   * 只影响业务回调，不销毁 handler；传 true 恢复。
   */
  setPointClickEnabled(on: boolean): void;
  /** 屏幕坐标拾取世界点：Batch 属性 → 深度缓冲 pickPosition → 椭球 fallback。 */
  pickWorldAtScreen(screen: Cartesian2): PickPoint | null;
  /** 当前瓦片包围球；未加载时为 null。 */
  getTilesetBound(): TilesetBound | null;
  /** ENU 局部坐标（米）→ 世界坐标；meta 未加载时为 null。 */
  localToWorld(x: number, y: number, z: number): Cartesian3 | null;

  // ---- 报警点就地精载（进入漫游时加载，退出即释放） ----
  /** 加载单个报警点的全精度局部 tileset；失败返回 error，不抛异常。 */
  loadAlarmDetail(
    url: string,
    opts?: { expectedBytes?: number; expectedPoints?: number },
  ): Promise<AlarmDetailLoadResult>;
  /** 卸载局部精载瓦片并释放 WebGL 资源。 */
  clearAlarmDetail(): void;
  /** 是否存在已加载的局部精载瓦片。 */
  hasAlarmDetail(): boolean;
  /** 画/清报警点问题范围标识（水平圆环 + 短立柱 + 标签）。 */
  setAlarmZone(zone: AlarmZoneSpec | null): void;
  /** 清除报警点范围标识。 */
  clearAlarmZone(): void;

  /** 销毁 viewer 并释放所有 WebGL 资源。 */
  destroy(): void;
}

export interface GlobePointSize {
  min: number;
  max: number;
  step: number;
}
export const POINT_SIZE_RANGE: GlobePointSize = { min: 0.5, max: 10, step: 0.5 };

export function createGlobe(
  container: HTMLElement,
  options: { autoRotate?: boolean } = {},
): GlobeController {
  const viewer = new Viewer(container, {
    baseLayer: baseLayer(),
    baseLayerPicker: false,
    geocoder: false,
    homeButton: false,
    sceneModePicker: false,
    navigationHelpButton: false,
    timeline: false,
    animation: false,
    infoBox: false,
    selectionIndicator: false,
    // C8 报告截图：canvas.toDataURL 需要保留绘图缓冲，否则可能拿到空白帧。
    contextOptions: {
      webgl: { preserveDrawingBuffer: true },
    },
  });

  // 隐藏右下角 Cesium credit / Cesium ion logo。
  // Cesium 1.119+ DOM 结构用 class 而不是 ID：
  //   .cesium-widget-credits（容器）→ display:none 整体屏蔽
  // 同时兜住旧版本的 creditContainer ID（兼容性）。
  const creditContainer = viewer.cesiumWidget.creditContainer as HTMLElement | undefined;
  if (creditContainer) creditContainer.style.display = "none";
  const creditsRoot = (viewer.cesiumWidget as unknown as { container: HTMLElement })
    .container;
  const widgetCredits = creditsRoot?.querySelector<HTMLElement>(".cesium-widget-credits");
  if (widgetCredits) widgetCredits.style.display = "none";

  // 调试：暴露 viewer/tileset 到 window 方便浏览器控制台和 Playwright evaluate 检查状态
  (globalThis as { __globeDebug__?: unknown }).__globeDebug__ = { viewer, tileset: null as unknown };

  let autoRotate = options.autoRotate ?? true;
  const stopRotate = viewer.scene.postRender.addEventListener(() => {
    if (!autoRotate) return;
    viewer.camera.rotate(Cartesian3.UNIT_Z, ROTATE_RAD_PER_FRAME);
  });

  const resetView = (): void => {
    viewer.camera.setView({
      destination: Cartesian3.fromDegrees(INITIAL_VIEW.lon, INITIAL_VIEW.lat, INITIAL_VIEW.height),
    });
  };
  resetView();

  // ---- 3D Tiles 管理（闭包内，外部只通过 GlobeController 接口操作） ----
  let tileset: Cesium3DTileset | null = null;
  let loadPromise: Promise<TilesetLoadState> | null = null;
  /** 报警点局部精载瓦片（与主 tileset 并存，退出漫游时销毁）。 */
  let detailTileset: Cesium3DTileset | null = null;
  /** 当前距离衰减状态，新加载的局部瓦片要沿用。 */
  let attenuationOn = DEFAULT_ATTENUATION;

  // ---- 着色 / 量测 / 报警点状态 ----
  let colorMode: PointColorMode = "rgb";
  /** 当前点大小（像素）。Cesium 只认 Cesium3DTileStyle.pointSize，需随样式一起重建。 */
  let pointSizePx = DEFAULT_POINT_SIZE;
  let cloudMeta: CloudMeta | null = null;
  let measureHandler: ((result: MeasureResult) => void) | null = null;
  let alarmHandler: ((alarm: CloudMetaAlarm) => void) | null = null;
  // 量测模式激活时置 false：现有点击弹卡 handler 直接 return，避免同一次点击
  // 既画量测线又弹坐标卡。退出量测后恢复 true。
  let pointClickEnabled = true;
  const alarmEntities: Entity[] = [];
  const alarmById = new Map<string, CloudMetaAlarm>();

  type Rgb = readonly [number, number, number];
  // 高程：低蓝 → 中绿 → 高红；强度：黑 → 白
  const ELEVATION_STOPS: ReadonlyArray<{ t: number; rgb: Rgb }> = [
    { t: 0, rgb: [34, 84, 255] },
    { t: 0.5, rgb: [52, 214, 120] },
    { t: 1, rgb: [255, 72, 56] },
  ];
  const INTENSITY_STOPS: ReadonlyArray<{ t: number; rgb: Rgb }> = [
    { t: 0, rgb: [12, 12, 12] },
    { t: 1, rgb: [250, 250, 250] },
  ];

  const lerpRgb = (stops: ReadonlyArray<{ t: number; rgb: Rgb }>, t: number): Rgb => {
    const clamped = Math.min(1, Math.max(0, t));
    for (let i = 1; i < stops.length; i++) {
      if (clamped <= stops[i].t) {
        const lo = stops[i - 1];
        const hi = stops[i];
        const k = (clamped - lo.t) / Math.max(hi.t - lo.t, 1e-6);
        return [0, 1, 2].map((idx) =>
          Math.round(lo.rgb[idx] + (hi.rgb[idx] - lo.rgb[idx]) * k),
        ) as unknown as Rgb;
      }
    }
    return stops[stops.length - 1].rgb;
  };

  const toCssHex = (rgb: Rgb): string =>
    `#${rgb.map((c) => Math.min(255, Math.max(0, Math.round(c))).toString(16).padStart(2, "0")).join("")}`;

  /**
   * 把 [min, max] 切成 `bands` 段，按 stops 取色，生成 Cesium3DTileStyle 的条件表达式。
   * 3D Tiles Styling 语言没有线性插值函数，只能用离散条件；
   * 段数足够多（20 段）时肉眼就是连续渐变。
   */
  const gradientStyle = (
    property: string,
    min: number,
    max: number,
    stops: ReadonlyArray<{ t: number; rgb: Rgb }>,
    bands = 20,
  ): Cesium3DTileStyle => {
    const span = Math.max(max - min, 1e-6);
    const conditions: Array<[string, string]> = [];
    // 从高到低排列：先匹配的条件先生效
    for (let i = bands - 1; i >= 0; i--) {
      const threshold = min + span * (i / bands);
      const rgb = lerpRgb(stops, (i + 0.5) / bands);
      conditions.push([`\${${property}} >= ${threshold.toFixed(3)}`, `color('${toCssHex(rgb)}')`]);
    }
    conditions.push(["true", `color('${toCssHex(lerpRgb(stops, 0.5 / bands))}')`]);
    return new Cesium3DTileStyle({
      color: { conditions },
      pointSize: `${pointSizePx}`,
    });
  };

  /** 按当前着色模式与点大小生成样式；主瓦片与局部精载共用，保证视觉一致。 */
  const buildStyle = (mode: PointColorMode): Cesium3DTileStyle => {
    if (mode === "rgb") {
      // 不设 color 条件 → 着色器不接管颜色，回到 pnts 里的顶点 RGB；
      // 但仍要带 pointSize，否则 Cesium 按默认 1px 画点，近处几乎看不见。
      return new Cesium3DTileStyle({ pointSize: `${pointSizePx}` });
    }
    if (mode === "solid") {
      return new Cesium3DTileStyle({
        color: "color('#ffffff')",
        pointSize: `${pointSizePx}`,
      });
    }
    if (mode === "elevation") {
      return gradientStyle(
        "Z",
        cloudMeta?.zMin ?? -5,
        cloudMeta?.zMax ?? 5,
        ELEVATION_STOPS,
      );
    }
    return gradientStyle(
      "Intensity",
      cloudMeta?.intensityMin ?? 0,
      cloudMeta?.intensityMax ?? 1,
      INTENSITY_STOPS,
    );
  };

  const applyColorMode = (mode: PointColorMode): void => {
    colorMode = mode;
    if (tileset) tileset.style = buildStyle(mode);
    // 局部精载必须跟着换色，否则切“按强度”时只有全局瓦片变色
    if (detailTileset) detailTileset.style = buildStyle(mode);
  };

  const clearAlarms = (): void => {
    for (const entity of alarmEntities) viewer.entities.remove(entity);
    alarmEntities.length = 0;
    alarmById.clear();
  };

  /** 后端 cloud-meta.json 里的报警点（ENU 局部坐标）→ 闪烁红点实体。 */
  const addAlarms = (meta: CloudMeta): void => {
    clearAlarms();
    const frame = Transforms.eastNorthUpToFixedFrame(
      Cartesian3.fromDegrees(meta.center.lon, meta.center.lat, meta.center.height),
    );
    for (const alarm of meta.alarms) {
      alarmById.set(alarm.id, alarm);
      const world = Matrix4.multiplyByPoint(
        frame,
        new Cartesian3(alarm.x, alarm.y, alarm.z),
        new Cartesian3(),
      );
      // CallbackProperty 每帧求值 → 半径与透明度一起呼吸，肉眼就是"闪烁"
      const blink = 6;
      const phase = (t: number) => Math.sin(t * blink);
      const entity = viewer.entities.add({
        id: alarm.id,
        position: world,
        point: {
          pixelSize: new CallbackProperty(
            (time) => (time ? 18 + 8 * phase(time.secondsOfDay) : 18),
            false,
          ),
          color: new CallbackProperty(
            (time) =>
              time
                ? Color.RED.withAlpha(0.45 + 0.55 * Math.abs(phase(time.secondsOfDay)))
                : Color.RED,
            false,
          ),
          outlineColor: Color.WHITE,
          outlineWidth: 2,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
      });
      alarmEntities.push(entity);
    }
  };

  const readMeta = async (tilesetUrl: string): Promise<CloudMeta | null> => {
    try {
      const url = new URL("cloud-meta.json", new URL(tilesetUrl, window.location.href));
      const res = await fetch(url.href);
      if (!res.ok) return null;
      const raw = (await res.json()) as Partial<CloudMeta>;
      if (!raw.center || !Array.isArray(raw.alarms)) return null;
      return raw as CloudMeta;
    } catch {
      return null;
    }
  };

  /** pnts 的 Batch Table 会转成逐点属性（_X/_Y/_Z/_INTENSITY），拾取到的特征可直接读属性。 */
  type PropertyReadable = { getProperty(name: string): unknown };
  const asPropertyReadable = (obj: unknown): PropertyReadable | null => {
    if (!obj || typeof obj !== "object") return null;
    const direct = obj as PropertyReadable;
    if (typeof direct.getProperty === "function") return direct;
    const primitive = (obj as { primitive?: unknown }).primitive;
    if (primitive && typeof (primitive as PropertyReadable).getProperty === "function") {
      return primitive as PropertyReadable;
    }
    return null;
  };

  const worldFromLocal = (x: number, y: number, z: number): Cartesian3 | null => {
    const meta = cloudMeta;
    if (!meta) return null;
    const frame = Transforms.eastNorthUpToFixedFrame(
      Cartesian3.fromDegrees(meta.center.lon, meta.center.lat, meta.center.height),
    );
    return Matrix4.multiplyByPoint(frame, new Cartesian3(x, y, z), new Cartesian3());
  };

  const worldFromFeature = (picked: unknown): Cartesian3 | null => {
    const feature = asPropertyReadable(picked);
    if (!feature) return null;
    try {
      const x = feature.getProperty("X");
      const y = feature.getProperty("Y");
      const z = feature.getProperty("Z");
      if (typeof x !== "number" || typeof y !== "number" || typeof z !== "number") return null;
      return worldFromLocal(x, y, z);
    } catch {
      // 特征表不存在时 getProperty 会抛 DeveloperError，静默回落到 pickPosition
      return null;
    }
  };

  const toMeasure = (world: Cartesian3): MeasureResult => {
    const carto = Cartographic.fromCartesian(world);
    const deg = 180 / Math.PI;
    return {
      longitude: carto.longitude * deg,
      latitude: carto.latitude * deg,
      height: carto.height,
    };
  };

  /** 屏幕坐标 → 世界坐标。供量测工具复用，避免在外部重复实现拾取链。 */
  const pickWorldAtScreen = (screen: Cartesian2): PickPoint | null => {
    const picked = viewer.scene.pick(screen) as { id?: unknown } | undefined;
    // 1) Batch Table 逐点属性：精度最高（直接是该点云点的坐标）
    const featureWorld = worldFromFeature(picked);
    if (featureWorld) return { world: featureWorld, source: "feature" };

    // 2) 深度缓冲：命中点云/模型表面；无实体表面时可能返回 undefined
    const depthWorld = viewer.scene.pickPosition(screen);
    if (depthWorld) {
      const carto = Cartographic.fromCartesian(depthWorld);
      // 过滤深度缓冲的背面/异常值（高度远低于椭球视为无效）
      if (carto && carto.height > -100_000) {
        return { world: depthWorld, source: "depth" };
      }
    }

    // 3) 兜底：椭球面。精度最差（海平面/椭球面，不代表真实地形或结构表面）
    const ellipsoidWorld = viewer.camera.pickEllipsoid(
      screen,
      viewer.scene.globe.ellipsoid,
    );
    if (ellipsoidWorld) return { world: ellipsoidWorld, source: "ellipsoid" };
    return null;
  };

  const clickHandler = new ScreenSpaceEventHandler(viewer.scene.canvas);
  clickHandler.setInputAction((movement: { position: Cartesian2 }): void => {
    // 量测模式激活：让位给量测工具，不弹坐标卡/报警卡
    if (!pointClickEnabled) return;

    const picked = viewer.scene.pick(movement.position) as { id?: unknown } | undefined;

    // 1) 报警点优先（Entity 实体命中）
    if (picked && picked.id instanceof Entity) {
      const alarm = alarmById.get(String((picked.id as Entity).id));
      if (alarm) {
        alarmHandler?.(alarm);
        return;
      }
    }

    // 2) 只有命中三维瓦片/实体才量测，避免把地球表面当成点云
    if (!picked) return;

    // 优先用 Batch 属性（逐点精确坐标）；取不到再用深度缓冲（pickPosition）
    const world = worldFromFeature(picked) ?? viewer.scene.pickPosition(movement.position);
    if (world) measureHandler?.(toMeasure(world));
  }, ScreenSpaceEventType.LEFT_CLICK);

  const applyPointCloudShading = (
    ts: Cesium3DTileset,
    pointSize: number,
    attenuation: boolean,
  ): void => {
    // PointCloudShading 只有 attenuation / geometricErrorScale / maximumAttenuation
    // 等字段，**没有** pointSize 和 maximumScreenSpaceError（已核对
    // @cesium/engine 的 PointCloudShading.js）。点大小必须写进
    // Cesium3DTileStyle.pointSize；否则 Cesium 用默认 1px，进入点云内部后
    // 就是一层几乎看不见的散点，看起来像“什么都没渲染”。
    pointSizePx = clampPointSize(pointSize);
    attenuationOn = attenuation;
    ts.pointCloudShading.attenuation = attenuation;
    ts.maximumScreenSpaceError = MAX_SCREEN_SPACE_ERROR;
    // 重建当前风格的 style，把新的 pointSize 带进去
    applyColorMode(colorMode);
  };

  // ---- 设计图层（青色线框断面环；allowPicking=false，不阻挡点云拾取） ----
  let designPrimitive: Primitive | null = null;
  let designMeta: DesignLayerMeta | null = null;

  const clearDesignLayer = (): void => {
    if (designPrimitive) {
      viewer.scene.primitives.remove(designPrimitive);
      designPrimitive = null;
    }
    designMeta = null;
  };

  const loadDesignLayer = async (url: string): Promise<DesignLayerState> => {
    clearDesignLayer();
    try {
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const payload = (await resp.json()) as {
        meta?: DesignLayerMeta;
        rings?: number[][][];
        longitudinal?: number[][][];
      };
      const meta = payload.meta;
      if (!meta) throw new Error("设计模型缺少 meta");
      const rings = payload.rings ?? [];
      if (rings.length === 0) throw new Error("设计模型为空（rings 为空）");

      // 单条折线 = 一个断面环；颜色走 PolylineGeometry 的逐顶点 colors
      const DESIGN_COLOR = Color.fromCssColorString("#1fe0cf");
      const instances: GeometryInstance[] = [];
      const addPolyline = (coords: number[][]): void => {
        if (coords.length < 2) return;
        const positions = coords.map((p) => new Cartesian3(p[0], p[1], p[2]));
        instances.push(
          new GeometryInstance({
            geometry: new PolylineGeometry({
              positions,
              colors: positions.map(() => DESIGN_COLOR),
              width: 2.0,
              vertexFormat: PolylineColorAppearance.VERTEX_FORMAT,
            }),
          }),
        );
      };
      for (const ring of rings) {
        // 闭合断面环
        addPolyline([...ring, ring[0]]);
      }
      for (const line of payload.longitudinal ?? []) addPolyline(line);

      // 设计环与 pnts 用同一套「局部工程坐标」：必须套用 tileset 根变换
      // （root.computedTransform = ENU→ECEF），否则局部坐标会被当成 ECEF 米。
      if (!tileset) throw new Error("请先加载点云数据集，再打开设计图层");
      const localToEcef = tileset.root.computedTransform;

      const primitive = new Primitive({
        geometryInstances: instances,
        appearance: new PolylineColorAppearance({ translucent: true }),
        asynchronous: false,
        allowPicking: false,
        modelMatrix: localToEcef,
      });
      viewer.scene.primitives.add(primitive);
      designPrimitive = primitive;
      designMeta = meta;
      return { kind: "ready", meta, visible: true };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return { kind: "error", message };
    }
  };

  const setDesignShow = (on: boolean): void => {
    if (designPrimitive) designPrimitive.show = on;
  };

  // ---- 报警点问题范围标识（水平圆环 + 短立柱 + 标签；随局部精载一起清） ----
  const alarmZoneEntities: Entity[] = [];

  const clearAlarmZone = (): void => {
    for (const entity of alarmZoneEntities) viewer.entities.remove(entity);
    alarmZoneEntities.length = 0;
  };

  /**
   * 画报警点范围圈。
   *
   * 取舍：
   * - 只画「地面圆环 + 8 根 1.2 m 短立柱 + 标签」，不做半透明球壳——球壳虽然
   *   体积感强，但第一人称在球内会糊住眼前的点云，得不偿失；
   * - 圆环半径 = 局部精载瓦片实际覆盖半径，两者严格一致，避免“圈大点少”的错觉；
   * - `PolylineGraphics` 没有 `disableDepthTestDistance`，所以沿圈再放 36 个
   *   小圆点（`PointGraphics` 支持该参数）：60 m 内圆点始终画在点云之上，
   *   贴脸漫游也能看清边界；线框与立柱保留正常深度测试，远处不会隔山透视；
   * - 标签同样 60 m 内不做深度测试，明确写“示例”；
   * - 颜色取警示琥珀色，与侵限图例的红/黄/绿区分开。
   */
  const setAlarmZone = (zone: AlarmZoneSpec | null): void => {
    clearAlarmZone();
    if (!zone || !(zone.radiusM > 0)) return;
    const segments = 96;
    const ring: Cartesian3[] = [];
    for (let i = 0; i <= segments; i++) {
      const angle = (i / segments) * Math.PI * 2;
      const world = worldFromLocal(
        zone.x + Math.cos(angle) * zone.radiusM,
        zone.y + Math.sin(angle) * zone.radiusM,
        zone.z,
      );
      if (world) ring.push(world);
    }
    if (ring.length < 3) return;

    const amber = Color.fromCssColorString("#ffc53d");
    alarmZoneEntities.push(
      viewer.entities.add({
        polyline: {
          positions: ring,
          width: 2.5,
          material: amber.withAlpha(0.9),
        },
      }),
    );

    // 沿圈的常显圆点：解决线框被点云遮住的问题（漫游贴脸时尤其明显）
    const markers = 36;
    for (let i = 0; i < markers; i++) {
      const angle = (i / markers) * Math.PI * 2;
      const world = worldFromLocal(
        zone.x + Math.cos(angle) * zone.radiusM,
        zone.y + Math.sin(angle) * zone.radiusM,
        zone.z + 0.05,
      );
      if (!world) continue;
      alarmZoneEntities.push(
        viewer.entities.add({
          position: world,
          point: {
            pixelSize: 5,
            color: amber.withAlpha(0.85),
            outlineColor: Color.fromCssColorString("rgba(20, 24, 32, 0.85)"),
            outlineWidth: 1,
            disableDepthTestDistance: 60,
          },
        }),
      );
    }

    for (let i = 0; i < 8; i++) {
      const angle = (i / 8) * Math.PI * 2;
      const x = zone.x + Math.cos(angle) * zone.radiusM;
      const y = zone.y + Math.sin(angle) * zone.radiusM;
      const bottom = worldFromLocal(x, y, zone.z);
      const top = worldFromLocal(x, y, zone.z + 1.2);
      if (!bottom || !top) continue;
      alarmZoneEntities.push(
        viewer.entities.add({
          polyline: {
            positions: [bottom, top],
            width: 1.5,
            material: amber.withAlpha(0.6),
          },
        }),
      );
    }

    const labelWorld = worldFromLocal(zone.x, zone.y, zone.z + 2.4);
    if (labelWorld) {
      alarmZoneEntities.push(
        viewer.entities.add({
          position: labelWorld,
          label: {
            text: zone.label ?? `示例报警范围 R=${zone.radiusM.toFixed(1)} m`,
            font: "12px ui-sans-serif, sans-serif",
            fillColor: Color.fromCssColorString("#ffe8b0"),
            showBackground: true,
            backgroundColor: Color.fromCssColorString("rgba(20, 24, 32, 0.72)"),
            horizontalOrigin: HorizontalOrigin.CENTER,
            verticalOrigin: VerticalOrigin.BOTTOM,
            pixelOffset: new Cartesian2(0, -4),
            disableDepthTestDistance: 60,
          },
        }),
      );
    }
  };

  // ---- 报警点局部精载瓦片（与主 tileset 并存，独立加载/销毁） ----
  const clearAlarmDetail = (): void => {
    if (!detailTileset) return;
    const ts = detailTileset;
    detailTileset = null;
    // PrimitiveCollection 默认 destroyPrimitives=true，remove 内部已 destroy；
    // 再手动 destroy 会触发 throwOnDestroyed 的 DeveloperError。
    if (!viewer.scene.primitives.remove(ts)) ts.destroy();
    console.info("[alarm-detail] released");
  };

  /**
   * 等首个瓦片内容加载完成；超时兜底，避免漫游卡在等待。
   *
   * 不能用 `allTilesLoaded`：瓦片刚 add 进场景时还没有任何“必须加载”的瓦片，
   * 该事件会立刻触发，导致耗时全部是 null。
   */
  const waitForDetailTiles = (ts: Cesium3DTileset, timeoutMs = 15000): Promise<void> =>
    new Promise((resolve) => {
      let settled = false;
      const finish = (): void => {
        if (settled) return;
        settled = true;
        remove?.();
        window.clearTimeout(timer);
        resolve();
      };
      const remove = ts.tileLoad.addEventListener(finish);
      const timer = window.setTimeout(finish, timeoutMs);
    });

  const loadAlarmDetail = async (
    url: string,
    opts?: { expectedBytes?: number; expectedPoints?: number },
  ): Promise<AlarmDetailLoadResult> => {
    clearAlarmDetail();
    const startedAt = performance.now();
    const failed = (message: string): AlarmDetailLoadResult => {
      console.warn("[alarm-detail] load failed", message);
      return {
        kind: "error",
        message,
        points: 0,
        bytes: 0,
        tilesetJsonMs: null,
        contentReadyMs: null,
        firstDrawMs: null,
      };
    };
    try {
      const ts = await Cesium3DTileset.fromUrl(url);
      const tilesetJsonMs = Math.round(performance.now() - startedAt);
      let points = 0;
      let contentReadyMs: number | null = null;
      let firstDrawMs: number | null = null;
      ts.tileLoad.addEventListener((tile) => {
        const content = (tile as unknown as { content?: { pointsLength?: number } }).content;
        if (typeof content?.pointsLength === "number") points += content.pointsLength;
        if (contentReadyMs !== null) return;
        contentReadyMs = Math.round(performance.now() - startedAt);
        // 内容就绪后的下一帧完成 GPU 上传 + 首次绘制
        const remove = viewer.scene.postRender.addEventListener(() => {
          remove();
          if (firstDrawMs === null) firstDrawMs = Math.round(performance.now() - startedAt);
        });
      });
      viewer.scene.primitives.add(ts);
      ts.pointCloudShading.attenuation = attenuationOn;
      ts.maximumScreenSpaceError = MAX_SCREEN_SPACE_ERROR;
      ts.style = buildStyle(colorMode);
      detailTileset = ts;
      await waitForDetailTiles(ts);
      // 再等一帧，保证 postRender 里的 firstDraw 已记录
      await new Promise<void>((resolve) => {
        const remove = viewer.scene.postRender.addEventListener(() => {
          remove();
          resolve();
        });
      });
      const result: AlarmDetailLoadResult = {
        kind: "ready",
        points: points || opts?.expectedPoints || 0,
        bytes: opts?.expectedBytes ?? 0,
        tilesetJsonMs,
        contentReadyMs,
        firstDrawMs,
      };
      const dbg = (globalThis as { __globeDebug__?: Record<string, unknown> }).__globeDebug__;
      if (dbg) dbg.lastAlarmDetailTiming = result;
      console.info("[alarm-detail] load timing", result);
      return result;
    } catch (err) {
      clearAlarmDetail();
      return failed(err instanceof Error ? err.message : String(err));
    }
  };

  const clearTileset = (): void => {
    // 设计图层随点云数据集一起卸载（避免切到桥梁后还留着隧道设计轮廓）
    clearDesignLayer();
    // 报警点精载与范围圈也一并释放，避免切数据集后残留
    clearAlarmDetail();
    clearAlarmZone();
    if (!tileset) return;
    // 报警点是 Entity，先清掉再销毁瓦片
    clearAlarms();
    cloudMeta = null;
    // remove 内部已按 destroyPrimitives=true 销毁瓦片，不要再 destroy 第二次
    const current = tileset;
    tileset = null;
    viewer.scene.primitives.remove(current);
  };

  /** 读浏览器 Resource Timing 里本批 pnts 的网络耗时/字节数（用于量化加载）。 */
  const readPntsNetwork = (
    sinceMs: number,
  ): { startMs: number | null; endMs: number | null; bytes: number | null; count: number } => {
    if (typeof performance === "undefined" || !performance.getEntriesByType) {
      return { startMs: null, endMs: null, bytes: null, count: 0 };
    }
    const entries = performance.getEntriesByType("resource") as PerformanceResourceTiming[];
    const pnts = entries.filter(
      (entry) => entry.name.includes(".pnts") && entry.startTime >= sinceMs - 5,
    );
    if (pnts.length === 0) return { startMs: null, endMs: null, bytes: null, count: 0 };
    // 并行请求：取最早开始到最晚结束；字节数按 transferSize（被缓存时为 0）回落 encodedBodySize
    const start = Math.min(...pnts.map((entry) => entry.startTime)) - sinceMs;
    const end = Math.max(...pnts.map((entry) => entry.responseEnd)) - sinceMs;
    const bytes = pnts.reduce(
      (sum, entry) => sum + (entry.transferSize || entry.encodedBodySize || 0),
      0,
    );
    return {
      startMs: Math.max(start, 0),
      endMs: Math.max(end, 0),
      bytes,
      count: pnts.length,
    };
  };

  const loadTileset = async (
    url: string,
    opts?: {
      pointSize?: number;
      attenuation?: boolean;
      onStage?: (detail: TilesetLoadStageDetail) => void;
      onTiming?: (timing: TilesetLoadTiming) => void;
    },
  ): Promise<TilesetLoadState> => {
    // 防止重复并发加载（用户快速切 tab 的极端情况）
    if (loadPromise) return loadPromise;

    clearTileset();
    loadPromise = (async (): Promise<TilesetLoadState> => {
      const startedAt = performance.now();
      let tilesetJsonMs: number | null = null;
      let contentReadyMs: number | null = null;
      let firstDrawMs: number | null = null;
      let firstVisibleMs: number | null = null;
      let allLoadedMs: number | null = null;
      let loadedTiles = 0;
      let totalTiles = 1;
      let pointCount = 0;
      let published = false;
      let publishedWithVisible = false;

      const emit = (stage: TilesetLoadStage): void => {
        opts?.onStage?.({
          stage,
          loadedTiles,
          totalTiles,
          elapsedMs: Math.round(performance.now() - startedAt),
        });
      };

      const round = (value: number | null): number | null =>
        value === null ? null : Math.round(value);

      const publish = (): void => {
        // 首次发布补上“总耗时”；首帧可见可能更晚（相机还在飞行），
        // 等 tileVisible 到了再发布一次，只补首帧可见，不重复刷 done
        if (published && (firstVisibleMs === null || publishedWithVisible)) return;
        published = true;
        publishedWithVisible = firstVisibleMs !== null;
        const net = readPntsNetwork(startedAt);
        const timing: TilesetLoadTiming = {
          tilesetJsonMs: round(tilesetJsonMs),
          contentReadyMs: round(contentReadyMs),
          networkStartMs: round(net.startMs),
          networkEndMs: round(net.endMs),
          networkMs:
            net.startMs === null || net.endMs === null
              ? null
              : Math.round(net.endMs - net.startMs),
          networkBytes: net.bytes,
          pntsRequests: net.count,
          firstDrawMs: round(firstDrawMs),
          firstVisibleMs: round(firstVisibleMs),
          allLoadedMs: round(allLoadedMs),
          pointCount: pointCount || null,
        };
        const dbg = (globalThis as { __globeDebug__?: Record<string, unknown> })
          .__globeDebug__;
        if (dbg) dbg.lastLoadTiming = timing;
        console.info("[tileset] load timing", timing);
        opts?.onTiming?.(timing);
        emit("done");
      };

      try {
        const ts = await Cesium3DTileset.fromUrl(url);
        tilesetJsonMs = performance.now() - startedAt;
        const rootChildren = (ts.root as unknown as { children?: unknown[] }).children;
        totalTiles = 1 + (rootChildren?.length ?? 0);
        emit("tileset");

        ts.tileLoad.addEventListener((tile) => {
          loadedTiles += 1;
          const content = (tile as unknown as { content?: { pointsLength?: number } })
            .content;
          if (typeof content?.pointsLength === "number") {
            pointCount += content.pointsLength;
          }
          if (contentReadyMs === null) {
            contentReadyMs = performance.now() - startedAt;
            emit("gpu");
            // 内容就绪后的下一帧才完成 GPU 上传与首次绘制
            const remove = viewer.scene.postRender.addEventListener(() => {
              remove();
              if (firstDrawMs === null) firstDrawMs = performance.now() - startedAt;
              emit("visible");
            });
          }
        });
        // 瓦片真正通过遍历/裁剪进入画面 = 首帧可见
        ts.tileVisible.addEventListener(() => {
          if (firstVisibleMs !== null) return;
          firstVisibleMs = performance.now() - startedAt;
          emit("visible");
          publish();
        });
        ts.allTilesLoaded.addEventListener(() => {
          if (allLoadedMs === null) allLoadedMs = performance.now() - startedAt;
          // 下一帧再发布：保证“GPU 上传 + 首次绘制”那个 postRender 回调已先记录
          const remove = viewer.scene.postRender.addEventListener(() => {
            remove();
            publish();
          });
        });
        // initialTilesLoaded：首屏需要的瓦片全部就绪（个别情况下 allTilesLoaded 不触发）
        ts.initialTilesLoaded.addEventListener(() => {
          if (allLoadedMs === null) allLoadedMs = performance.now() - startedAt;
          publish();
        });

        viewer.scene.primitives.add(ts);
        applyPointCloudShading(
          ts,
          opts?.pointSize ?? DEFAULT_POINT_SIZE,
          opts?.attenuation ?? DEFAULT_ATTENUATION,
        );
        // 默认显示
        ts.show = true;
        tileset = ts;
        // 同步 window 调试引用（Playwright evaluate 用）
        (globalThis as { __globeDebug__?: unknown }).__globeDebug__ = {
          viewer,
          tileset: ts,
        };

        // 加载 cloud-meta.json（着色范围 + 侵限报警点），失败也不影响瓦片显示
        const meta = await readMeta(url);
        cloudMeta = meta;
        if (meta) addAlarms(meta);
        // 用真实 z/intensity 范围应用当前着色模式（rgb 模式下这一步是 no-op）
        applyColorMode(colorMode);

        // 按瓦片包围球（由 tileset.json 的 root.transform/boundingVolume 推导）飞入，
        // 不再使用写死的地理坐标；两份点云各自飞到自己位置。
        flyToTileset();
        return { kind: "ready", tileset: ts };
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        publish();
        return { kind: "error", message: msg };
      } finally {
        loadPromise = null;
      }
    })();
    return loadPromise;
  };

  const flyToTileset = (): void => {
    if (!tileset) return;
    // Cesium3DTileset.fromUrl 解析完成时根瓦片已加载，boundingSphere 可用。
    const bound = tileset.boundingSphere;
    if (Number.isFinite(bound.radius) && bound.radius > 0) {
      // 斜俯视整个点云：距离 = 半径 × 2.2，俯角 35°
      viewer.camera.flyToBoundingSphere(bound, {
        duration: 2.0,
        offset: new HeadingPitchRange(0.0, -0.6, Math.max(bound.radius * 2.2, 40)),
      });
      return;
    }
    // 包围球尚未就绪时退回 meta 的锚点（底图仅作背景，不声明位置精度）
    const meta = cloudMeta;
    if (meta) {
      viewer.camera.flyTo({
        destination: Cartesian3.fromDegrees(
          meta.center.lon,
          meta.center.lat,
          meta.center.height + 600,
        ),
        duration: 2.0,
      });
    }
  };

  const clampPointSize = (px: number): number =>
    Math.max(POINT_SIZE_RANGE.min, Math.min(POINT_SIZE_RANGE.max, px));

  const setPointSize = (px: number): void => {
    if (!tileset) return;
    applyPointCloudShading(
      tileset,
      px,
      tileset.pointCloudShading.attenuation,
    );
  };

  const setAttenuation = (on: boolean): void => {
    if (!tileset) return;
    applyPointCloudShading(tileset, pointSizePx, on);
  };

  return {
    viewer,
    setAutoRotate(on: boolean): void {
      autoRotate = on;
    },
    setRenderPaused(paused: boolean): void {
      viewer.useDefaultRenderLoop = !paused;
      if (!paused) {
        // 隐藏期间容器尺寸可能变化，恢复时先校正画布再补一帧
        viewer.resize();
        viewer.scene.requestRender();
      }
    },
    resetView,

    loadTileset,
    clearTileset,
    hasTileset(): boolean {
      return tileset !== null;
    },
    setTilesetShow(on: boolean): void {
      if (tileset) tileset.show = on;
    },
    setPointSize,
    setAttenuation,
    flyToTileset,
    loadDesignLayer,
    setDesignShow,
    hasDesignLayer(): boolean {
      return designPrimitive !== null;
    },
    getDesignMeta(): DesignLayerMeta | null {
      return designMeta;
    },
    clearDesignLayer,
    setColorMode: applyColorMode,
    onMeasure(cb: ((result: MeasureResult) => void) | null): void {
      measureHandler = cb;
    },
    onAlarm(cb: ((alarm: CloudMetaAlarm) => void) | null): void {
      alarmHandler = cb;
    },
    getCloudMeta(): CloudMeta | null {
      return cloudMeta;
    },
    setPointClickEnabled(on: boolean): void {
      pointClickEnabled = on;
    },
    pickWorldAtScreen,
    localToWorld: worldFromLocal,
    getTilesetBound(): TilesetBound | null {
      if (!tileset) return null;
      // Cesium 1.119 的 Cesium3DTileset 类型声明里没有 boundingSphere，运行时存在
      const bound = (tileset as unknown as {
        boundingSphere?: { center: Cartesian3; radius: number };
      }).boundingSphere;
      if (!bound) return null;
      return { center: bound.center, radius: bound.radius };
    },
    loadAlarmDetail,
    clearAlarmDetail,
    hasAlarmDetail(): boolean {
      return detailTileset !== null;
    },
    setAlarmZone,
    clearAlarmZone,

    destroy(): void {
      stopRotate();
      // 点击事件 + 报警点实体先清掉，再销毁瓦片/viewer，避免悬挂回调
      if (!clickHandler.isDestroyed()) clickHandler.destroy();
      clearTileset();
      // 销毁 viewer 前还得把 primitives 清空一遍（双保险，防内部悬挂引用）
      viewer.scene.primitives.removeAll();
      viewer.destroy();
    },
  };
}

// PointCloudAttenuation 枚举在 Cesium 1.119 类型声明文件里不存在（实际用 attenuation 布尔值），
// 因此移除 import；此处不再需要类型占位。
