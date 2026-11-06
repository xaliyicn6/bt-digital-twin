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
  ImageryLayer,
  Ion,
  Matrix4,
  OpenStreetMapImageryProvider,
  ScreenSpaceEventHandler,
  ScreenSpaceEventType,
  Transforms,
  Viewer,
  WebMapTileServiceImageryProvider,
  WebMercatorTilingScheme,
} from "cesium";

// 点云实际地理坐标（WHU-TLS 隧道采样中心）
const POINT_CLOUD_LON = 114.31;
const POINT_CLOUD_LAT = 30.52;
const POINT_CLOUD_HEIGHT = 150;  // 相机飞到 150m 高度看 50m 高的点云

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

/** 后端 cloud-meta.json 里的侵限报警点（ENU 局部坐标，米）。 */
export interface CloudMetaAlarm {
  id: string;
  label: string;
  x: number;
  y: number;
  z: number;
  offsetMm: number;
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
}

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
  resetView(): void;

  // ---- 3D Tiles ----
  /**
   * 加载 3D Tiles pnts 瓦片。
   * @param url 相对 / 绝对 URL；Vite public 目录下的文件走 '/tiles/tunnel/tileset.json'
   * @param opts 默认点大小 / 距离衰减 —— 覆盖构造时默认值
   * @returns Promise<TilesetLoadState> 调用方决定怎么显示状态
   */
  loadTileset(url: string, opts?: { pointSize?: number; attenuation?: boolean }): Promise<TilesetLoadState>;
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

  const applyColorMode = (mode: PointColorMode): void => {
    colorMode = mode;
    if (!tileset) return;
    if (mode === "rgb") {
      // 不设 color 条件 → 着色器不接管颜色，回到 pnts 里的顶点 RGB；
      // 但仍要带 pointSize，否则 Cesium 按默认 1px 画点，近处几乎看不见。
      tileset.style = new Cesium3DTileStyle({ pointSize: `${pointSizePx}` });
      return;
    }
    if (mode === "solid") {
      tileset.style = new Cesium3DTileStyle({
        color: "color('#ffffff')",
        pointSize: `${pointSizePx}`,
      });
      return;
    }
    if (mode === "elevation") {
      tileset.style = gradientStyle(
        "Z",
        cloudMeta?.zMin ?? -5,
        cloudMeta?.zMax ?? 5,
        ELEVATION_STOPS,
      );
      return;
    }
    tileset.style = gradientStyle(
      "Intensity",
      cloudMeta?.intensityMin ?? 0,
      cloudMeta?.intensityMax ?? 1,
      INTENSITY_STOPS,
    );
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
    ts.pointCloudShading.attenuation = attenuation;
    ts.maximumScreenSpaceError = 8;
    // 重建当前风格的 style，把新的 pointSize 带进去
    applyColorMode(colorMode);
  };

  const clearTileset = (): void => {
    if (!tileset) return;
    // 报警点是 Entity，先清掉再销毁瓦片
    clearAlarms();
    cloudMeta = null;
    // 从场景移除再 destroy，否则 WebGL 缓冲区不会释放
    viewer.scene.primitives.remove(tileset);
    tileset.destroy();
    tileset = null;
  };

  const loadTileset = async (
    url: string,
    opts?: { pointSize?: number; attenuation?: boolean },
  ): Promise<TilesetLoadState> => {
    // 防止重复并发加载（用户快速切 tab 的极端情况）
    if (loadPromise) return loadPromise;

    clearTileset();
    loadPromise = (async (): Promise<TilesetLoadState> => {
      try {
        const ts = await Cesium3DTileset.fromUrl(url);
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

        // 直接 flyTo 点云的经纬度（不依赖 boundingSphere，避免 SSE 提前返回导致
        // boundingSphere 算错）
        viewer.camera.flyTo({
          destination: Cartesian3.fromDegrees(
            POINT_CLOUD_LON,
            POINT_CLOUD_LAT,
            POINT_CLOUD_HEIGHT,
          ),
          duration: 2.5,
        });
        return { kind: "ready", tileset: ts };
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        return { kind: "error", message: msg };
      } finally {
        loadPromise = null;
      }
    })();
    return loadPromise;
  };

  const flyToTileset = (): void => {
    if (!tileset) return;
    viewer.camera.flyTo({
      destination: Cartesian3.fromDegrees(
        POINT_CLOUD_LON,
        POINT_CLOUD_LAT,
        POINT_CLOUD_HEIGHT,
      ),
      duration: 2.0,
    });
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
