"""设计断面侵限判定：基准是**设计断面**（不是数据拟合圆），输出真实带符号距离。

口径（已与需求方确认）：
1. 参数化断面 = 三段**独立圆心**圆弧（拱顶 + 左右拱腰）+ 直线底板（仰拱高程）；
   起止角由参数显式给出，不做自动相切推导；底板与拱腰圆弧的交点用一元二次方程求。
2. 余量 ``clearance_margin_m``（默认 0.05 m）**计入限界**：判定界线 = 设计轮廓外扩余量，
   只有离开判定界线才叫不侵限；``intrusion_depth_mm`` 是相对这条红线的深度。
3. 面积用 shapely 做真实多边形裁剪（``外扩包络 - 实测空腔``），不做网格估算。
4. ``clearance_reference_mm``（拟合圆弧口径）与本模块互不替代，不得命名为 limit_intrusion。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import shapely
from shapely.geometry import Polygon
from shapely.validation import explain_validity

from app.services.geometry import InsufficientGeometryError

MIN_POINTS = 100
DEFAULT_CLEARANCE_MARGIN_M = 0.05
DEFAULT_STEP_DEG = 2.0
DEFAULT_AREA_BINS = 180


@dataclass(frozen=True)
class ArcSpec:
    """一段圆弧（局部断面坐标 uv，单位 m，v 向上；0° = +u，逆时针为正）。"""

    center_u: float
    center_v: float
    radius: float
    start_deg: float
    end_deg: float


@dataclass(frozen=True)
class DesignSectionParams:
    crown: ArcSpec
    left_haunch: ArcSpec
    right_haunch: ArcSpec
    invert_v: float
    clearance_margin_m: float = DEFAULT_CLEARANCE_MARGIN_M
    step_deg: float = DEFAULT_STEP_DEG


@dataclass(frozen=True)
class IntrusionResult:
    """单断面判定结果。

    ``signed_distance_mm`` 相对**设计轮廓**：正 = 轮廓外（有余量），负 = 轮廓内。
    ``intrusion_points`` 是落在**外扩余量后的判定界线**之内的点（(K,2) 局部 uv）。
    """

    signed_distance_mm: npt.NDArray[np.float64]
    intrusion_points: npt.NDArray[np.float64]
    intrusion_depth_mm: float
    intrusion_count: int
    intrusion_area_sqm: float | None
    clearance_margin_m: float
    area_unavailable_reason: str | None


def _sample_arc(arc: ArcSpec, step_deg: float) -> npt.NDArray[np.float64]:
    if arc.radius <= 0.0:
        raise ValueError(f"圆弧半径必须为正：{arc.radius}")
    span = abs(arc.end_deg - arc.start_deg)
    if span <= 0.0:
        raise ValueError("圆弧起止角不能相同")
    count = max(int(np.ceil(span / step_deg)) + 1, 3)
    deg = np.linspace(arc.start_deg, arc.end_deg, count)
    rad = np.radians(deg)
    return np.column_stack(
        [arc.center_u + arc.radius * np.cos(rad), arc.center_v + arc.radius * np.sin(rad)]
    )


def _floor_roots(arc: ArcSpec, invert_v: float) -> tuple[float, float]:
    """解 ``(u-cu)^2 + (invert_v-cv)^2 = r^2``，返回底板与圆的左右两个交点 u。"""
    dv = invert_v - arc.center_v
    disc = arc.radius**2 - dv**2
    if disc < 0.0:
        raise ValueError(
            f"底板 v={invert_v} 与圆弧(cu={arc.center_u}, cv={arc.center_v}, r={arc.radius}) 无交点"
        )
    half = float(np.sqrt(disc))
    return arc.center_u - half, arc.center_u + half


def _endpoint_v(arc: ArcSpec, deg: float) -> float:
    return arc.center_v + arc.radius * float(np.sin(np.radians(deg)))


def _trim_at_floor(arc: ArcSpec, invert_v: float, step_deg: float) -> npt.NDArray[np.float64]:
    """用直线 v=invert_v 与圆的一元二次方程根，把圆弧**截断**在底板交点处。

    只替换端点是不够的：圆弧若越过底板，截断前的点会落到 v<invert_v，
    多边形底部就不再是直线。这里按起止角方向裁剪，并把端点钉到精确交点。
    """
    roots = _floor_roots(arc, invert_v)
    span = abs(arc.end_deg - arc.start_deg)
    direction = 1.0 if arc.end_deg >= arc.start_deg else -1.0
    candidates: list[tuple[float, float, float]] = []
    for u in roots:
        raw = float(np.degrees(np.arctan2(invert_v - arc.center_v, u - arc.center_u)))
        # atan2 落在 (-180, 180]，210° 会变成 -150°；必须回投到起止角的数值方向，
        # 否则截断后的 (start, end) 会按反方向长弧采样（-150→90 是 240° 而非 120°）。
        delta = ((raw - arc.start_deg) * direction) % 360.0
        if delta <= span + 1e-9:
            candidates.append((u, arc.start_deg + direction * delta, delta))
    if not candidates:
        raise ValueError("底板与拱腰圆弧在给定起止角范围内无交点")

    lower_at_start = _endpoint_v(arc, arc.start_deg) <= _endpoint_v(arc, arc.end_deg)
    u, angle, _ = (
        min(candidates, key=lambda item: item[2])
        if lower_at_start
        else max(candidates, key=lambda item: item[2])
    )
    trimmed = (
        ArcSpec(arc.center_u, arc.center_v, arc.radius, angle, arc.end_deg)
        if lower_at_start
        else ArcSpec(arc.center_u, arc.center_v, arc.radius, arc.start_deg, angle)
    )
    points = _sample_arc(trimmed, step_deg)
    points[0 if lower_at_start else -1] = (u, invert_v)
    return points


def build_design_section_polygon(params: DesignSectionParams) -> npt.NDArray[np.float64]:
    """三段圆弧 + 直线底板 → CCW 简单多边形 (M,2)。

    弦高误差上界 ``r * (1 - cos(step/2))``：r=3 m、2° 时约 0.4 mm。
    底板与左右拱腰的交点由一元二次方程求出；无实根、多边形自交都会抛 ``ValueError``。
    """
    left = _trim_at_floor(params.left_haunch, params.invert_v, params.step_deg)
    crown = _sample_arc(params.crown, params.step_deg)
    right = _trim_at_floor(params.right_haunch, params.invert_v, params.step_deg)
    ring = np.vstack([left, crown, right])
    # 去掉相邻重复点，避免 shapely 把零长边判成退化
    keep = np.ones(ring.shape[0], dtype=bool)
    keep[1:] = np.linalg.norm(np.diff(ring, axis=0), axis=1) > 1e-9
    ring = ring[keep]

    signed_area = 0.5 * float(
        np.sum(ring[:, 0] * np.roll(ring[:, 1], -1) - np.roll(ring[:, 0], -1) * ring[:, 1])
    )
    if signed_area < 0.0:
        ring = ring[::-1]

    polygon = Polygon(ring)
    if not polygon.is_valid:
        raise ValueError(f"设计断面多边形非法：{explain_validity(polygon)}")
    return np.asarray(polygon.exterior.coords[:-1], dtype=np.float64)


def _measured_free_space(
    points: npt.NDArray[np.float64], center: npt.NDArray[np.float64], bins: int
) -> Polygon | None:
    """按极角分箱取每箱**最内圈**点，连成实测空腔多边形；有空箱则视为覆盖不足。"""
    rel = points - center
    angle = np.arctan2(rel[:, 1], rel[:, 0])
    radius = np.hypot(rel[:, 0], rel[:, 1])
    index = ((angle + np.pi) / (2.0 * np.pi) * bins).astype(np.int64) % bins

    ring = np.empty((bins, 2), dtype=np.float64)
    filled = np.zeros(bins, dtype=bool)
    for bin_id in range(bins):
        mask = index == bin_id
        if not np.any(mask):
            continue
        inner = points[mask][int(np.argmin(radius[mask]))]
        ring[bin_id] = inner
        filled[bin_id] = True
    if not np.all(filled):
        return None
    polygon = Polygon(ring)
    return polygon if polygon.is_valid and polygon.area > 0.0 else None


def _intrusion_area(
    points: npt.NDArray[np.float64],
    envelope: Polygon,
    center: npt.NDArray[np.float64],
    bins: int,
) -> tuple[float | None, str | None]:
    free_space = _measured_free_space(points, center, bins)
    if free_space is None:
        return None, "实测空腔覆盖不足或退化（存在空箱/自交），面积不可用"
    region = envelope.difference(free_space)
    if region.is_empty:
        return 0.0, None
    return float(region.area), None


def check_intrusion(
    cloud_points: npt.NDArray[np.float64],
    design_section_polygon: npt.NDArray[np.float64],
    *,
    clearance_margin_m: float = DEFAULT_CLEARANCE_MARGIN_M,
    area_bins: int = DEFAULT_AREA_BINS,
) -> IntrusionResult:
    """单断面侵限判定。

    ``cloud_points`` 为该片点云在局部正交基下的 (N,2)/(N,3) 投影（单位 m）。
    ``design_section_polygon`` 为 ``build_design_section_polygon`` 的返回值或等价 CCW 多边形。
    """
    pts = np.asarray(cloud_points, dtype=np.float64)
    poly = np.asarray(design_section_polygon, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] < 2:
        raise ValueError("cloud_points 必须是 (N,2) 或 (N,3) 的二维数组")
    if pts.shape[0] < MIN_POINTS:
        raise InsufficientGeometryError(f"断面点数不足：{pts.shape[0]} < {MIN_POINTS}")
    if poly.ndim != 2 or poly.shape[0] < 3:
        raise ValueError("设计断面多边形至少需要 3 个顶点")
    if clearance_margin_m < 0.0:
        raise ValueError(f"clearance_margin_m 不能为负：{clearance_margin_m}")

    design = Polygon(poly)
    if not design.is_valid:
        raise ValueError(f"设计断面多边形非法：{explain_validity(design)}")
    envelope = (
        design.buffer(clearance_margin_m, quad_segs=16) if clearance_margin_m > 0.0 else design
    )
    if envelope.is_empty or envelope.area <= 0.0:
        raise ValueError("设计断面外扩余量后为空，检查 clearance_margin_m")

    uv = pts[:, :2]
    geom = shapely.points(uv[:, 0], uv[:, 1])

    # 相对设计轮廓的带符号距离：正=外，负=内
    inside_design = shapely.contains_xy(design, uv[:, 0], uv[:, 1])
    dist_design = np.asarray(shapely.distance(geom, design.boundary), dtype=np.float64)
    signed = np.where(inside_design, -dist_design, dist_design)

    # 判定界线 = 外扩余量后的包络；落在包络内部即侵限（余量被吃掉）
    inside_envelope = shapely.contains_xy(envelope, uv[:, 0], uv[:, 1])
    dist_envelope = np.asarray(shapely.distance(geom, envelope.boundary), dtype=np.float64)

    depth_mm = np.where(inside_envelope, dist_envelope * 1000.0, 0.0)
    area, reason = _intrusion_area(uv, envelope, np.asarray(design.centroid.coords[0]), area_bins)
    return IntrusionResult(
        signed_distance_mm=signed * 1000.0,
        intrusion_points=uv[inside_envelope],
        intrusion_depth_mm=float(depth_mm.max()) if inside_envelope.any() else 0.0,
        intrusion_count=int(inside_envelope.sum()),
        intrusion_area_sqm=area,
        clearance_margin_m=clearance_margin_m,
        area_unavailable_reason=reason,
    )
