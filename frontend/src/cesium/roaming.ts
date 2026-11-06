import {
  Cartesian2,
  Cartesian3,
  Cartographic,
  ScreenSpaceEventHandler,
  ScreenSpaceEventType,
  Viewer,
} from "cesium";
import type { CloudMeta, GlobeController } from "./scene";

export interface RoamingController {
  enter(): void;
  exit(): void;
  isActive(): boolean;
  destroy(): void;
}

export interface RoamingOptions {
  /** 进入/退出回调，App 用于切换按钮态。 */
  onActiveChange?: (active: boolean) => void;
}

/** 人眼高度（米）。 */
const EYE_HEIGHT = 1.6;
/** 步行速度（米/秒）。点云主体只有十几米，8 m/s 会瞬间撞到边界。 */
const MOVE_SPEED = 3.0;
/** 相机距地最小高度（米），防止贴地/穿地。 */
const MIN_GROUND_CLEARANCE = 1.2;
/** 鼠标拖拽灵敏度（弧度/像素）。 */
const LOOK_SENSITIVITY = 0.0025;
/** 俯仰限制（弧度），约 ±77°，防止翻转。 */
const MAX_PITCH = 1.35;
/** 包围球边界使用比例，留出余量避免贴边。 */
const BOUND_RATIO = 0.9;

const DEG2RAD = Math.PI / 180;

interface SavedCamera {
  destination: Cartesian3;
  heading: number;
  pitch: number;
  roll: number;
}

interface ControllerFlags {
  enableTranslate: boolean;
  enableZoom: boolean;
  enableRotate: boolean;
  enableTilt: boolean;
  enableLook: boolean;
  enableInputs: boolean;
}

/** 依据报警点分布判断长轴：本地 +Y（北）或 +X（东）。 */
function axisHeading(meta: CloudMeta, fallback: number): number {
  if (meta.alarms.length < 2) return fallback;
  let minX = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const alarm of meta.alarms) {
    minX = Math.min(minX, alarm.x);
    maxX = Math.max(maxX, alarm.x);
    minY = Math.min(minY, alarm.y);
    maxY = Math.max(maxY, alarm.y);
  }
  const spanX = maxX - minX;
  const spanY = maxY - minY;
  // 长轴为 Y（南北）→ heading=0；长轴为 X（东西）→ heading=π/2
  return Math.abs(spanY) >= Math.abs(spanX) ? 0 : Math.PI / 2;
}

/**
 * 第一人称内部漫游。
 *
 * 取舍说明（踩过的坑见下）：
 * - 入点必须用 `cloud-meta.viewpoint`（按点云主体 2%~98% 分位 + PCA 主轴算出来的
 *   内部视点 / 主体底面 / 主轴朝向），**不能**用 meta.center（ENU 原点）或包围球中心：
 *   原点由生成脚本按 min/max 包围盒中心决定，几百米外的少量离群点会把包围盒拉大，
 *   原点落进点云主体之外的空隙，结果就是“进入漫游后一片空白、只能看到地形和天空”。
 * - 同理，meta.zMin 是离群点最低值，不能直接当“点云底面”；viewpoint.zBottom 才是主体底面。
 * - 人眼高度 = 主体底面 + 1.6 m；heading 取 PCA 主轴（薄片主轴常与坐标轴偏 20° 以上，
 *   按 X/Y 轴向猜会把视线对到空处），pitch = 0 平视。
 * - 进入时保存相机位姿与 screenSpaceCameraController 的全部输入开关；
 *   默认相机控制器整体禁用（enableInputs=false），由本模块自己的 handler/tick 驱动，
 *   避免“拖拽旋转”和 Cesium 默认相机操作互相抢事件；
 * - W/A/S/D 按当前 heading 在本地 ENU 东/北方向推进（不要在 ECEF 里给 direction 清 Z：
 *   本地北向在 ECEF 里有 -Z 分量，清零会让前进变成下扎）；
 * - 鼠标左键拖拽改 heading/pitch，pitch 限制 ±77°；
 * - 每帧把相机限制在 viewpoint 的椭圆边界（无椭圆字段时退回半径 90%），
 *   并保持高于“点云主体底面 / 地形”中较高者 1.2 m；
 * - 退出时按保存值 setView 恢复，并还原控制器开关、移除 tick/键盘/鼠标监听。
 */
export function createFirstPersonRoaming(
  viewer: Viewer,
  globe: GlobeController,
  options: RoamingOptions = {},
): RoamingController {
  let active = false;
  let saved: SavedCamera | null = null;
  let savedFlags: ControllerFlags | null = null;
  let heading = 0;
  let pitch = 0;
  let keys = new Set<string>();
  let dragging = false;
  let lastPointer = new Cartesian2();
  let handler: ScreenSpaceEventHandler | null = null;
  let tickListener: (() => void) | null = null;
  let lastTickMs = 0;
  let boundCenter: Cartesian3 | null = null;
  let boundRadius = 0;
  /** 主体主轴朝向（弧度），边界椭圆的纵轴方向；仅在有 halfLength/halfWidth 时使用。 */
  let boundMajorHeading = 0;
  /** 边界椭圆沿主轴半长（米）；0 表示退回圆形半径。 */
  let boundHalfLength = 0;
  /** 边界椭圆沿法向半宽（米）。 */
  let boundHalfWidth = 0;
  /** 世界坐标下的最低相机高度（点云底面/地形较高者 + 安全余量）。 */
  let floorHeight = 0;
  // 复用的 Cartographic 缓存，避免每帧新建对象
  const scratchCurrentCarto = new Cartographic();
  const scratchNextCarto = new Cartographic();
  const scratchCenterCarto = new Cartographic();

  const onKeyDown = (event: KeyboardEvent): void => {
    if (!active) return;
    const target = event.target as HTMLElement | null;
    if (
      target &&
      (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
        target.isContentEditable)
    ) {
      return;
    }
    const key = event.key.toLowerCase();
    if (key === "w" || key === "a" || key === "s" || key === "d") {
      keys.add(key);
      event.preventDefault();
    }
  };

  const onKeyUp = (event: KeyboardEvent): void => {
    if (!active) return;
    keys.delete(event.key.toLowerCase());
  };

  /** 窗口失焦时清空按键，避免“松手后继续前进”。 */
  const onBlur = (): void => {
    keys.clear();
    dragging = false;
  };

  const tick = (): void => {
    if (!active) return;
    const now = performance.now();
    if (keys.size === 0) {
      // 没按键时不累计时间，避免下次按下时第一帧用过期 delta 跳一大步
      lastTickMs = 0;
      return;
    }
    const deltaSeconds = lastTickMs ? Math.min(0.1, (now - lastTickMs) / 1000) : 0;
    lastTickMs = now;
    if (deltaSeconds <= 0) return;

    // 本地 ENU 东/北单位向量（ECEF）。不能像旧实现那样直接把 direction 的 Z 分量
    // 清零再归一化：本地北向在 ECEF 里有明显的 -Z 分量（北纬 30° 约 -0.5），
    // 清零 Z 等于把视线压向地面，走 1 m 会掉 0.46 m，撞到地面下限后原地踏步。
    const ellipsoid = viewer.scene.globe.ellipsoid;
    const cameraCarto = Cartographic.fromCartesian(
      viewer.camera.positionWC,
      ellipsoid,
      scratchCurrentCarto,
    );
    const sinLon = Math.sin(cameraCarto.longitude);
    const cosLon = Math.cos(cameraCarto.longitude);
    const sinLat = Math.sin(cameraCarto.latitude);
    const cosLat = Math.cos(cameraCarto.latitude);
    const east = new Cartesian3(-sinLon, cosLon, 0);
    const north = new Cartesian3(-sinLat * cosLon, -sinLat * sinLon, cosLat);
    const sinHeading = Math.sin(heading);
    const cosHeading = Math.cos(heading);
    // forward = east*sin(h) + north*cos(h)；right = east*cos(h) - north*sin(h)
    const forward = new Cartesian3(
      east.x * sinHeading + north.x * cosHeading,
      east.y * sinHeading + north.y * cosHeading,
      east.z * sinHeading + north.z * cosHeading,
    );
    const right = new Cartesian3(
      east.x * cosHeading - north.x * sinHeading,
      east.y * cosHeading - north.y * sinHeading,
      east.z * cosHeading - north.z * sinHeading,
    );

    const move = new Cartesian3();
    if (keys.has("w")) Cartesian3.add(move, forward, move);
    if (keys.has("s")) Cartesian3.subtract(move, forward, move);
    if (keys.has("d")) Cartesian3.add(move, right, move);
    if (keys.has("a")) Cartesian3.subtract(move, right, move);
    if (Cartesian3.magnitude(move) < 1e-6) return;
    Cartesian3.normalize(move, move);

    const step = MOVE_SPEED * deltaSeconds;
    const next = Cartesian3.add(
      viewer.camera.positionWC,
      Cartesian3.multiplyByScalar(move, step, new Cartesian3()),
      new Cartesian3(),
    );

    // 水平边界：用经纬度算真实水平距离，限制在“主体半径 90%”内。
    // 不能直接用 ECEF 的 x/y 差值——ECEF 坐标轴和本地水平面不重合，
    // 朝北走 11 m 只让 ECEF 的 XY 变化约 5.6 m，会放行近 2 倍的距离。
    const carto = Cartographic.fromCartesian(next, ellipsoid, scratchNextCarto);
    let targetLon = carto.longitude;
    let targetLat = carto.latitude;
    let targetHeight = carto.height;
    if (boundCenter && boundRadius > 0) {
      const centerCarto = Cartographic.fromCartesian(
        boundCenter,
        ellipsoid,
        scratchCenterCarto,
      );
      const cosCenterLat = Math.cos(centerCarto.latitude);
      const dLon = carto.longitude - centerCarto.longitude;
      const dLat = carto.latitude - centerCarto.latitude;
      const eastMeters = dLon * ellipsoid.maximumRadius * cosCenterLat;
      const northMeters = dLat * ellipsoid.maximumRadius;
      if (boundHalfLength > 0 && boundHalfWidth > 0) {
        // 椭圆边界（沿点云主轴）。点云主体是薄片，圆形边界横向会放行 2~3 倍，
        // 人一按 A/D 就走出点云、正前方变空白。
        const sinA = Math.sin(boundMajorHeading);
        const cosA = Math.cos(boundMajorHeading);
        const projMajor = eastMeters * sinA + northMeters * cosA;
        const projMinor = eastMeters * cosA - northMeters * sinA;
        const ratio = Math.hypot(
          projMajor / boundHalfLength,
          projMinor / boundHalfWidth,
        );
        if (ratio > 1) {
          const k = 1 / ratio;
          targetLon =
            centerCarto.longitude +
            (eastMeters * k) / (ellipsoid.maximumRadius * cosCenterLat);
          targetLat = centerCarto.latitude + (northMeters * k) / ellipsoid.maximumRadius;
        }
      } else {
        const distance = Math.hypot(eastMeters, northMeters);
        const limit = boundRadius * BOUND_RATIO;
        if (distance > limit) {
          const k = limit / distance;
          targetLon = centerCarto.longitude + dLon * k;
          targetLat = centerCarto.latitude + dLat * k;
        }
      }
    }

    // 不穿地/不穿底面：用 fromRadians 在经纬度上重建，避免 ECEF 直接加 Z 偏移
    // 造成经纬度漂移。下限 = max(点云主体底面, 地形) + 1.2 m。
    targetHeight = Math.max(targetHeight, floorHeight);
    Cartesian3.fromRadians(targetLon, targetLat, targetHeight, ellipsoid, next);

    viewer.camera.setView({
      destination: next,
      orientation: { heading, pitch, roll: 0 },
    });
  };

  const enter = (): void => {
    if (active) return;
    const camera = viewer.camera;
    saved = {
      destination: Cartesian3.clone(camera.positionWC),
      heading: camera.heading,
      pitch: camera.pitch,
      roll: camera.roll,
    };

    const controller = viewer.scene.screenSpaceCameraController;
    savedFlags = {
      enableTranslate: controller.enableTranslate,
      enableZoom: controller.enableZoom,
      enableRotate: controller.enableRotate,
      enableTilt: controller.enableTilt,
      enableLook: controller.enableLook,
      enableInputs: controller.enableInputs,
    };
    controller.enableTranslate = false;
    controller.enableZoom = false;
    controller.enableRotate = false;
    controller.enableTilt = false;
    controller.enableLook = false;
    // 完全禁用默认相机输入，全部交给本模块
    controller.enableInputs = false;

    const meta = globe.getCloudMeta();
    const vp = meta?.viewpoint;
    let destination: Cartesian3 | null = null;
    if (meta && vp) {
      // 点云主体中心 + 主体底面 1.6 m（人眼高度），heading 沿长轴 → 正前方一定是点云
      destination = globe.localToWorld(vp.x, vp.y, vp.zBottom + EYE_HEIGHT);
      heading = vp.headingDeg * DEG2RAD;
    } else if (meta) {
      // 旧版 meta（无 viewpoint）：退回 ENU 原点 + zMin，仍可能落在主体外，只作兜底
      destination = Cartesian3.fromDegrees(
        meta.center.lon,
        meta.center.lat,
        meta.center.height + meta.zMin + EYE_HEIGHT,
      );
      heading = axisHeading(meta, camera.heading);
    }

    if (!destination) {
      // 连 meta 都没有：直接在当前经纬度抬到 1.6 m 眼高
      const carto = Cartographic.fromCartesian(camera.positionWC);
      const ground = viewer.scene.globe.getHeight(carto);
      destination = Cartesian3.fromRadians(
        carto.longitude,
        carto.latitude,
        (typeof ground === "number" ? ground : 0) + EYE_HEIGHT,
        viewer.scene.globe.ellipsoid,
      );
      heading = camera.heading;
    }

    let floorBase: number;
    if (meta && vp) {
      const bottom = globe.localToWorld(vp.x, vp.y, vp.zBottom);
      floorBase = bottom ? Cartographic.fromCartesian(bottom).height : 0;
      // 边界以“主体中心”为原点（入点已沿主轴后移，不再是中心）
      const bcx = vp.centerX ?? vp.x;
      const bcy = vp.centerY ?? vp.y;
      boundCenter =
        globe.localToWorld(bcx, bcy, vp.zBottom) ??
        bottom ??
        Cartesian3.clone(destination);
      boundRadius = vp.radius;
      if (
        typeof vp.halfLength === "number" &&
        typeof vp.halfWidth === "number" &&
        typeof vp.majorHeadingDeg === "number" &&
        vp.halfLength > 0 &&
        vp.halfWidth > 0
      ) {
        boundHalfLength = vp.halfLength;
        boundHalfWidth = vp.halfWidth;
        boundMajorHeading = vp.majorHeadingDeg * DEG2RAD;
      }
    } else {
      const tilesetBound = globe.getTilesetBound();
      if (tilesetBound && tilesetBound.radius > 1) {
        boundCenter = Cartesian3.clone(tilesetBound.center);
        boundRadius = tilesetBound.radius;
      } else {
        // 无瓦片包围球时，以进入点为中心给 400 m 安全边界
        boundCenter = Cartesian3.clone(destination);
        boundRadius = 400;
      }
      floorBase = 0;
    }
    pitch = 0;

    // 下限 = max(点云主体底面, 地形) + 安全余量；避免相机掉到点云底部以下或穿地
    const destCarto = Cartographic.fromCartesian(destination);
    const terrain = viewer.scene.globe.getHeight(destCarto);
    const terrainHeight = typeof terrain === "number" ? terrain : 0;
    floorHeight = Math.max(terrainHeight, floorBase) + MIN_GROUND_CLEARANCE;

    camera.setView({
      destination,
      orientation: { heading, pitch, roll: 0 },
    });

    // 记录进入时的对位数值：排查“漫游里看不到点云”时可直接在控制台核对
    const bound = globe.getTilesetBound();
    console.info("[roaming] enter", {
      cameraEcef: destination,
      cameraHeight: +destCarto.height.toFixed(2),
      headingDeg: +(heading * 180 / Math.PI).toFixed(1),
      pitchDeg: 0,
      viewpoint: vp ?? null,
      tilesetBoundHeight: bound
        ? +Cartographic.fromCartesian(bound.center).height.toFixed(2)
        : null,
      tilesetBoundRadius: bound ? +bound.radius.toFixed(1) : null,
      boundEllipse: boundHalfLength
        ? {
            halfLength: boundHalfLength,
            halfWidth: boundHalfWidth,
            majorHeadingDeg: +(boundMajorHeading * 180 / Math.PI).toFixed(1),
          }
        : null,
      floorHeight: +floorHeight.toFixed(2),
    });

    handler = new ScreenSpaceEventHandler(viewer.scene.canvas);
    handler.setInputAction(
      (movement: { position: Cartesian2 }): void => {
        if (!active) return;
        dragging = true;
        lastPointer = Cartesian2.clone(movement.position);
      },
      ScreenSpaceEventType.LEFT_DOWN,
    );
    handler.setInputAction(
      (movement: { endPosition: Cartesian2 }): void => {
        if (!active || !dragging) return;
        const dx = movement.endPosition.x - lastPointer.x;
        const dy = movement.endPosition.y - lastPointer.y;
        lastPointer = Cartesian2.clone(movement.endPosition);
        // 拖拽右移 → 视野左转（与常见三维漫游拖拽方向一致）
        heading -= dx * LOOK_SENSITIVITY;
        pitch -= dy * LOOK_SENSITIVITY;
        pitch = Math.max(-MAX_PITCH, Math.min(MAX_PITCH, pitch));
        viewer.camera.setView({
          orientation: { heading, pitch, roll: 0 },
        });
      },
      ScreenSpaceEventType.MOUSE_MOVE,
    );
    handler.setInputAction((): void => {
      dragging = false;
    }, ScreenSpaceEventType.LEFT_UP);

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    lastTickMs = performance.now();
    tickListener = tick;
    viewer.clock.onTick.addEventListener(tickListener);

    active = true;
    options.onActiveChange?.(true);
  };

  const exit = (): void => {
    if (!active) return;
    active = false;

    if (tickListener) {
      viewer.clock.onTick.removeEventListener(tickListener);
      tickListener = null;
    }
    window.removeEventListener("keydown", onKeyDown);
    window.removeEventListener("keyup", onKeyUp);
    window.removeEventListener("blur", onBlur);
    keys.clear();
    dragging = false;

    if (handler) {
      handler.destroy();
      handler = null;
    }

    const controller = viewer.scene.screenSpaceCameraController;
    if (savedFlags) {
      controller.enableTranslate = savedFlags.enableTranslate;
      controller.enableZoom = savedFlags.enableZoom;
      controller.enableRotate = savedFlags.enableRotate;
      controller.enableTilt = savedFlags.enableTilt;
      controller.enableLook = savedFlags.enableLook;
      controller.enableInputs = savedFlags.enableInputs;
      savedFlags = null;
    }

    if (saved) {
      viewer.camera.setView({
        destination: saved.destination,
        orientation: {
          heading: saved.heading,
          pitch: saved.pitch,
          roll: saved.roll,
        },
      });
      saved = null;
    }

    boundCenter = null;
    boundRadius = 0;
    boundHalfLength = 0;
    boundHalfWidth = 0;
    boundMajorHeading = 0;
    floorHeight = 0;
    options.onActiveChange?.(false);
  };

  return {
    enter,
    exit,
    isActive(): boolean {
      return active;
    },
    destroy(): void {
      exit();
    },
  };
}
