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
  Cartesian3,
  Cesium3DTileset,
  ImageryLayer,
  Ion,
  OpenStreetMapImageryProvider,
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

  const applyPointCloudShading = (
    ts: Cesium3DTileset,
    pointSize: number,
    attenuation: boolean,
  ): void => {
    // Cesium 1.119 类型声明里 pointCloudShading 接口没 pointSize 字段，
    // 但运行时真实可用（PointCloudShading 类型声明不完整）。
    // 用 unknown 双重断言绕过类型检查；这是我们自己维护的内部逻辑，风险可控。
    const shading = (ts as unknown as { pointCloudShading: Record<string, unknown> })
      .pointCloudShading;
    shading.attenuation = attenuation;
    shading.pointSize = pointSize;
    // maximumScreenSpaceError 默认 16，调小→近点更密
    shading.maximumScreenSpaceError = 8;
  };

  const clearTileset = (): void => {
    if (!tileset) return;
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
    const next = clampPointSize(px);
    const shading = (tileset as unknown as { pointCloudShading: Record<string, unknown> })
      .pointCloudShading;
    applyPointCloudShading(tileset, next, shading.attenuation as boolean);
  };

  const setAttenuation = (on: boolean): void => {
    if (!tileset) return;
    const shading = (tileset as unknown as { pointCloudShading: Record<string, unknown> })
      .pointCloudShading;
    applyPointCloudShading(tileset, shading.pointSize as number, on);
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

    destroy(): void {
      stopRotate();
      clearTileset();
      // 销毁 viewer 前还得把 primitives 清空一遍（双保险，防内部悬挂引用）
      viewer.scene.primitives.removeAll();
      viewer.destroy();
    },
  };
}

// PointCloudAttenuation 枚举在 Cesium 1.119 类型声明文件里不存在（实际用 attenuation 布尔值），
// 因此移除 import；此处不再需要类型占位。
