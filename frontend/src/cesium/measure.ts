import {
  ArcType,
  CallbackProperty,
  Cartesian2,
  Cartesian3,
  Cartographic,
  Color,
  Entity,
  HorizontalOrigin,
  LabelStyle,
  PolygonHierarchy,
  ScreenSpaceEventHandler,
  ScreenSpaceEventType,
  VerticalOrigin,
  Viewer,
} from "cesium";
import type { GlobeController } from "./scene";

export type MeasureMode = "distance" | "height" | "area" | null;

export interface MeasureToolOptions {
  /** 模式切换回调（null = 退出量测）。App 据此清空坐标卡并更新工具栏态。 */
  onModeChange?: (mode: MeasureMode) => void;
  /** 过程提示，如“已取起点”“拾取失败”。 */
  onHint?: (hint: string) => void;
}

export interface MeasureToolController {
  setMode(mode: MeasureMode): void;
  getMode(): MeasureMode;
  clear(): void;
  destroy(): void;
}

const DISTANCE_COLOR = Color.fromCssColorString("#4fd1ff");
const HEIGHT_COLOR = Color.fromCssColorString("#ffcf6b");
const AREA_COLOR = Color.fromCssColorString("#7fe0a5");

const LABEL_FONT = "13px 'Cascadia Code', Consolas, monospace";
const LABEL_BG = new Color(0.03, 0.08, 0.16, 0.88);

/** 同一像素双击会先触发两次 LEFT_CLICK，最终点若与上一点几乎重合则丢弃。 */
const DUPLICATE_TOLERANCE_M = 0.05;

function addPoint(viewer: Viewer, world: Cartesian3, color: Color): Entity {
  return viewer.entities.add({
    position: world,
    point: {
      pixelSize: 7,
      color,
      outlineColor: Color.WHITE.withAlpha(0.85),
      outlineWidth: 1,
      // 点云遮挡时不消失
      disableDepthTestDistance: Number.POSITIVE_INFINITY,
    },
  });
}

function addLabel(
  viewer: Viewer,
  position: Cartesian3,
  text: string,
  color: Color,
): Entity {
  return viewer.entities.add({
    position,
    label: {
      text,
      font: LABEL_FONT,
      style: LabelStyle.FILL_AND_OUTLINE,
      fillColor: color,
      outlineColor: new Color(0, 0, 0, 0.65),
      outlineWidth: 2,
      showBackground: true,
      backgroundColor: LABEL_BG,
      backgroundPadding: new Cartesian2(8, 5),
      horizontalOrigin: HorizontalOrigin.CENTER,
      verticalOrigin: VerticalOrigin.BOTTOM,
      pixelOffset: new Cartesian2(0, -10),
      disableDepthTestDistance: Number.POSITIVE_INFINITY,
    },
  });
}

function addPolyline(
  viewer: Viewer,
  positions: Cartesian3[],
  color: Color,
  width = 2,
): Entity {
  return viewer.entities.add({
    polyline: {
      positions,
      width,
      // NONE：严格两点直线，不按地表测地线弯曲
      arcType: ArcType.NONE,
      material: color,
      // 被点云/地形挡住时仍以半透明显示，便于观察
      depthFailMaterial: color.withAlpha(0.55),
    },
  });
}

/** 三维平面多边形面积（Newell 法，支持倾斜面）。 */
function polygonArea(vertices: Cartesian3[]): number {
  if (vertices.length < 3) return 0;
  const origin = vertices[0];
  let nx = 0;
  let ny = 0;
  let nz = 0;
  for (let i = 1; i < vertices.length - 1; i += 1) {
    const a = Cartesian3.subtract(vertices[i], origin, new Cartesian3());
    const b = Cartesian3.subtract(vertices[i + 1], origin, new Cartesian3());
    const cross = Cartesian3.cross(a, b, new Cartesian3());
    nx += cross.x;
    ny += cross.y;
    nz += cross.z;
  }
  return Math.sqrt(nx * nx + ny * ny + nz * nz) / 2;
}

function centroid(vertices: Cartesian3[]): Cartesian3 {
  const sum = new Cartesian3();
  for (const vertex of vertices) {
    Cartesian3.add(sum, vertex, sum);
  }
  return Cartesian3.divideByScalar(sum, vertices.length, sum);
}

/**
 * 三维空间量测工具。
 *
 * 拾取链由 GlobeController.pickWorldAtScreen 提供：
 * Batch Table 逐点属性（最准）→ 深度缓冲 pickPosition → 椭球 fallback。
 * 点云没有实体表面（例如瓦片尚未加载完）时会退化为椭球面，标签照常显示但精度下降。
 *
 * 与现有点击弹卡 handler 的冲突：进入任一秒量测模式即调用
 * globe.setPointClickEnabled(false)，原有 LEFT_CLICK 回调直接 return；
 * 退出量测（setMode(null)）恢复 true，不会出现“画线的同时弹坐标卡”。
 */
export function createMeasureTool(
  viewer: Viewer,
  globe: GlobeController,
  options: MeasureToolOptions = {},
): MeasureToolController {
  let mode: MeasureMode = null;
  let points: Cartesian3[] = [];
  // pending：未完成的临时图元（起点标记、动态多边形/边）
  let pending: Entity[] = [];
  // completed：已完成的测量线与标签，切模式保留、清除时删除
  let completed: Entity[] = [];
  let areaPolygon: Entity | null = null;
  let areaBorder: Entity | null = null;

  const removeEntities = (list: Entity[]): void => {
    for (const entity of list) {
      viewer.entities.remove(entity);
    }
    list.length = 0;
  };

  const resetPending = (): void => {
    removeEntities(pending);
    points = [];
    areaPolygon = null;
    areaBorder = null;
  };

  const clearAll = (): void => {
    resetPending();
    removeEntities(completed);
  };

  const hint = (text: string): void => {
    options.onHint?.(text);
  };

  const finishDistance = (second: Cartesian3): void => {
    const first = points[0];
    resetPending();
    const distance = Cartesian3.distance(first, second);
    const midpoint = Cartesian3.midpoint(first, second, new Cartesian3());
    completed.push(addPolyline(viewer, [first, second], DISTANCE_COLOR));
    completed.push(addLabel(viewer, midpoint, `${distance.toFixed(2)} m`, DISTANCE_COLOR));
    hint(`距离 ${distance.toFixed(2)} m，可继续点击测量`);
  };

  const measureHeight = (world: Cartesian3): void => {
    const carto = Cartographic.fromCartesian(world);
    // 无地形数据时 globe.getHeight 返回 undefined，退化为椭球面 0 m。
    const terrain = viewer.scene.globe.getHeight(carto);
    const groundHeight = typeof terrain === "number" ? terrain : 0;
    const bottom = Cartesian3.fromRadians(
      carto.longitude,
      carto.latitude,
      groundHeight,
      viewer.scene.globe.ellipsoid,
    );
    const diff = Math.abs(carto.height - groundHeight);
    completed.push(addPoint(viewer, world, HEIGHT_COLOR));
    completed.push(addPolyline(viewer, [world, bottom], HEIGHT_COLOR));
    completed.push(
      addLabel(
        viewer,
        Cartesian3.midpoint(world, bottom, new Cartesian3()),
        `${diff.toFixed(2)} m`,
        HEIGHT_COLOR,
      ),
    );
    hint(
      `高程差 ${diff.toFixed(2)} m（基准：${typeof terrain === "number" ? "地形面" : "椭球面"}）`,
    );
  };

  const ensureAreaBorder = (): void => {
    if (areaBorder) return;
    areaBorder = viewer.entities.add({
      polyline: {
        positions: new CallbackProperty(
          () => (points.length >= 2 ? [...points, points[0]] : []),
          false,
        ),
        width: 2,
        arcType: ArcType.NONE,
        material: AREA_COLOR,
      },
    });
    pending.push(areaBorder);
  };

  const ensureAreaPolygon = (): void => {
    if (areaPolygon) return;
    areaPolygon = viewer.entities.add({
      polygon: {
        hierarchy: new CallbackProperty(
          () => (points.length >= 3 ? new PolygonHierarchy([...points]) : undefined),
          false,
        ),
        material: AREA_COLOR.withAlpha(0.24),
        perPositionHeight: true,
      },
    });
    pending.push(areaPolygon);
  };

  const finishArea = (): void => {
    if (mode !== "area" || points.length < 3) {
      if (mode === "area") hint("面积至少需要 3 个顶点");
      return;
    }
    // 双击的第二次 LEFT_CLICK 会追加一个几乎重合的点
    if (
      points.length >= 2 &&
      Cartesian3.distance(points[points.length - 1], points[points.length - 2]) <
        DUPLICATE_TOLERANCE_M
    ) {
      points.pop();
    }
    if (points.length < 3) {
      hint("面积至少需要 3 个顶点");
      resetPending();
      return;
    }

    const frozen = points.map((point) => Cartesian3.clone(point));
    const area = polygonArea(frozen);
    const center = centroid(frozen);
    resetPending();

    completed.push(
      viewer.entities.add({
        polygon: {
          hierarchy: new PolygonHierarchy(frozen),
          material: AREA_COLOR.withAlpha(0.24),
          perPositionHeight: true,
        },
      }),
    );
    completed.push(addPolyline(viewer, [...frozen, frozen[0]], AREA_COLOR));
    completed.push(addLabel(viewer, center, `${area.toFixed(2)} m²`, AREA_COLOR));
    hint(`面积 ${area.toFixed(2)} m²，可继续绘制`);
  };

  const handleClick = (world: Cartesian3, source: string): void => {
    if (source === "ellipsoid") {
      hint("未命中点云表面，已退化为椭球面拾取（精度较低）");
    }
    if (mode === "distance") {
      if (points.length === 0) {
        points = [Cartesian3.clone(world)];
        pending.push(addPoint(viewer, points[0], DISTANCE_COLOR));
        hint("已取起点，再点第二点");
        return;
      }
      finishDistance(world);
      return;
    }
    if (mode === "height") {
      measureHeight(world);
      return;
    }
    if (mode === "area") {
      points.push(Cartesian3.clone(world));
      pending.push(addPoint(viewer, world, AREA_COLOR));
      ensureAreaBorder();
      ensureAreaPolygon();
      hint(`面积：已 ${points.length} 个顶点，双击闭合`);
    }
  };

  const handler = new ScreenSpaceEventHandler(viewer.scene.canvas);
  handler.setInputAction(
    (movement: { position: Cartesian2 }): void => {
      if (!mode) return;
      const pick = globe.pickWorldAtScreen(movement.position);
      if (!pick) {
        hint("未拾取到有效点，请点击点云或地面");
        return;
      }
      handleClick(pick.world, pick.source);
    },
    ScreenSpaceEventType.LEFT_CLICK,
  );
  handler.setInputAction((): void => {
    if (mode === "area") finishArea();
  }, ScreenSpaceEventType.LEFT_DOUBLE_CLICK);

  return {
    setMode(next: MeasureMode): void {
      if (next === mode) return;
      // 切换/退出：只清理未完成的临时图元，已完成的测量保留
      resetPending();
      mode = next;
      // 关键：量测模式激活时让位给量测 handler，退出时恢复原有弹卡行为
      globe.setPointClickEnabled(next === null);
      options.onModeChange?.(next);
    },
    getMode(): MeasureMode {
      return mode;
    },
    clear(): void {
      clearAll();
      hint("已清除全部测量");
    },
    destroy(): void {
      clearAll();
      handler.destroy();
      globe.setPointClickEnabled(true);
      mode = null;
    },
  };
}
