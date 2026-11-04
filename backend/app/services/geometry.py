"""M07 核心几何分析：分层截面中心 → 稳健中轴线 → 垂直度（‰）。

算法依据（《公路桥隧结构智能检测与数字孪生运维平台详细设计说明书 V1.2》4.1）：

4.1.3 分层截面中心估计（矩形墩 RECT 分支）原文：
    H = 二维凸包(P_layer)
    R = 旋转卡壳最小面积外接矩形(H)
    center = R 的中心；width = R 短边；length = R 长边
本模块的 ``min_area_rect`` / ``mabr_center`` / ``extract_centers`` 即此分支。

4.1.4 稳健轴线拟合 原文：
    1. 初值：对分层中心点集做 PCA，取第一主成分作为初始方向 d0 与初始点 p0；
    2. 稳健拟合：以正交距离（点到直线垂距）为目标函数，用 IRLS（Huber 权）迭代；
    3. 终止：两次迭代方向夹角 < 0.01°，或残差变化 < 1e-4；
    4. 输出：p0 + d，及每层点到轴线的垂距残差。
文档同段明确："PCA 仅用于提供初值，不作为最终中轴线；最终结果必须由正交距离（TLS）
稳健拟合给出。普通最小二乘（沿某一坐标轴回归）会产生方向偏置，不采用。"
本模块 ``fit_robust_axis`` 严格遵守：Huber 阈值 = 1.345 × MAD 尺度，尺度用残差中位
绝对偏差换算（σ ≈ 1.4826 × MAD）；每轮用加权协方差最大特征向量更新方向、加权质心
更新线上一点；收敛判据与最大迭代次数均为显式参数。文档写的是"或"，实现照做，
但首轮不判收敛（残差变化初值为 +inf），避免误停。

4.1.5 垂直度定义式 原文（**文档已给出可直接编码的公式，本模块未另作解释**）：
    以设计高程区间 [z_b, z_t]（默认承台顶面至墩顶）为准：
        Δ_h = 拟合轴线在 z = z_t 处的水平坐标 − 在 z = z_b 处的水平坐标
        i   = |Δ_h| / (z_t − z_b) × 1000   （单位：‰，即倾斜率）
        最大局部偏离 δ_max = max_i( 第 i 层圆心/中心到拟合轴线的水平垂距 )
所以倾斜率单位是 ‰，分子是水平偏移（m），分母是高差（m），不是 mm/高度。

4.1.7 质量判据 原文：有效层数 < 总层数 60% → 判"数据不足"；墩高 < 5 m → 不出倾斜率。

与文档的差异（显式声明，均为"文档未覆盖"的补齐，不是把自造规则当成文档要求）：

1. [z_b, z_t] 的实际取值：本模块是纯算法层，拿不到设计承台顶面/墩顶高程，
   因此默认取"有效层的 z 覆盖区间"（层边界），并允许调用方传入 ``total_height``
   作为设计墩高用于降级判定。设计资料接入后应由调用方传入设计高程区间。
2. 4.1.3 的覆盖率 cover = 有效点数 / (密度 × R 周长 × 切片厚度) 需要点云密度输入，
   本模块没有密度，故用"层点数 / 中位数层点数"作为代理指标 ``valid_ratio``，
   仅作质量提示与调试，不参与阈值判定（不伪造 density）。
3. 4.1.2 的统计滤波去噪（SOR）、附属物剔除不在本模块范围内：输入点云视为已完成预处理。
   这也是当前"每层中心对噪声敏感"的根因（见 tests 中的裕度说明）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt
from scipy.spatial import ConvexHull, QhullError

EPS = 1e-12
# 每层最小点数：实现参数（文档按"覆盖率 ≥ 0.7"判定，但需要密度输入），可由调用方覆盖
MIN_LAYER_POINTS = 30
HUBER_K = 1.345
MAX_ITERATIONS = 50
TOL_ANGLE_DEG = 0.01  # 文档 4.1.4 第 3 条
TOL_RESIDUAL = 1e-4  # 文档 4.1.4 第 3 条，单位 m
MAD_TO_SIGMA = 1.4826
MIN_VALID_LAYERS = 3
MAX_DEGRADE_REASON_LAYERS = 3
MIN_SPAN_RATIO = 0.6
MIN_VALID_LAYER_RATIO = 0.6  # 文档 4.1.7："有效层数 < 总层数 60% → 数据不足"


class InsufficientGeometryError(Exception):
    """几何量不可计算：点云为空、全部切片被跳过、有效层不足以定线。"""


@dataclass
class Layer:
    """一个 z 切片。

    ``z_min``/``z_max`` 是切片边界（下闭上开）；``z_data_min``/``z_data_max``/``z_mean``
    是该层点的实测高程统计，用于计算 z_b/z_t 与轴线上的点，避免按边界外推。
    """

    index: int
    z_min: float
    z_max: float
    points: npt.NDArray[np.float64]  # 该层点的 xy，(k, 2)
    z_data_min: float = 0.0
    z_data_max: float = 0.0
    z_mean: float = 0.0
    skipped: bool = False
    skip_reason: str | None = None
    center: tuple[float, float] | None = None
    valid_ratio: float = 0.0  # 代理指标：层点数 / 中位数层点数（见模块 docstring 差异 2）
    rect_width: float = 0.0
    rect_length: float = 0.0
    rect_angle_deg: float = 0.0

    @property
    def n_points(self) -> int:
        return int(self.points.shape[0])


@dataclass(frozen=True)
class OrientedRect:
    """最小面积外接矩形：center 为矩形中心，width/length 为短/长边。"""

    center: tuple[float, float]
    width: float
    length: float
    angle_deg: float  # 长边与 +X 的夹角，规范到 [0, 90)
    area: float


@dataclass(frozen=True)
class AxisFit:
    """稳健拟合结果。``history`` 每项为 (迭代序号, 方向变化角(°), 残差 RMS(m))。"""

    point: npt.NDArray[np.float64]
    direction: npt.NDArray[np.float64]
    rms: float
    inliers: int
    iterations: int
    converged: bool
    mad_scale: float
    history: tuple[tuple[int, float, float], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PlumbResult:
    tilt_permille: float
    slope_ratio: float  # 原始量 m/m，便于核对 i = slope_ratio × 1000
    horizontal_offset_mm: float
    azimuth_deg: float | None
    valid_layers: int
    total_layers: int
    skipped_layers: tuple[Layer, ...]
    occlusion_ratio: float
    residual_rms_mm: float
    degraded: bool
    degrade_reason: str | None
    z_bottom: float
    z_top: float
    axis: AxisFit


# ---------------------------------------------------------------------------- 输入校验


def _as_points(points: npt.ArrayLike) -> npt.NDArray[np.float64]:
    arr = np.asarray(points, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError(f"点云必须是 (N, 3) 数组，当前形状 {arr.shape}")
    return arr


# ---------------------------------------------------------------------------- 1. 切片


def slice_layers(
    points: npt.ArrayLike, step: float = 1.0, min_points: int = MIN_LAYER_POINTS
) -> list[Layer]:
    """沿 Z 按 ``step`` 切片，返回全部层（含被跳过的层，便于追溯）。

    边界规则：从实测最低点 z0 起算，第 b 层为 [z0 + b·step, z0 + (b+1)·step)。
    恰好落在边界上的点归入**上一层**（下闭上开），因此每个点只属于一层，
    切割不会因浮点抖动把点分到两层。
    """
    cloud = _as_points(points) if not isinstance(points, np.ndarray) else points
    if cloud.shape[0] == 0:
        raise InsufficientGeometryError("点云为空，无法切片")
    if step <= 0.0:
        raise ValueError("step 必须为正")
    if min_points < 0:
        raise ValueError("min_points 不能为负")

    z = cloud[:, 2]
    z0 = float(z.min())
    bin_idx = np.floor((z - z0) / step).astype(np.int64)
    n_bins = int(bin_idx.max()) + 1

    layers: list[Layer] = []
    for b in range(n_bins):
        mask = bin_idx == b
        xy = np.ascontiguousarray(cloud[mask, :2], dtype=np.float64)
        zs = cloud[mask, 2]
        z_min = z0 + b * step
        z_max = z0 + (b + 1) * step
        layer = Layer(index=b, z_min=z_min, z_max=z_max, points=xy)
        if xy.shape[0] > 0:
            layer.z_data_min = float(zs.min())
            layer.z_data_max = float(zs.max())
            layer.z_mean = float(zs.mean())
        else:
            layer.z_data_min = z_min
            layer.z_data_max = z_max
            layer.z_mean = 0.5 * (z_min + z_max)
        if layer.n_points < min_points:
            layer.skipped = True
            layer.skip_reason = f"点数不足({layer.n_points}<{min_points})"
        layers.append(layer)
    return layers


# ---------------------------------------------------------------------------- 2. 最小外接矩形


def _convex_hull_2d(xy: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """二维凸包，逆时针返回顶点（scipy 的 ConvexHull 对 2D 保证 CCW 顺序）。

    退化处理：去重后不足 3 点直接返回；共线（QHull 报退化）时返回该线段的两个端点。
    """
    unique = np.unique(xy, axis=0)
    if unique.shape[0] < 3:
        return np.asarray(unique, dtype=np.float64)
    try:
        hull = ConvexHull(unique)
    except QhullError:
        centered = unique - unique.mean(axis=0)
        _, _, vt = np.linalg.svd(centered)
        proj = centered @ vt[0]
        ends = np.array([unique[int(np.argmin(proj))], unique[int(np.argmax(proj))]])
        return np.asarray(ends, dtype=np.float64)
    return np.asarray(unique[hull.vertices], dtype=np.float64)


def _fold_angle(deg: float) -> float:
    """把方向角折到 [0, 90)：矩形长边方向以 90° 为周期。"""
    value = deg % 180.0
    return value - 90.0 if value >= 90.0 else value


def min_area_rect(hull_points_2d: npt.ArrayLike) -> OrientedRect:
    """旋转卡壳求最小面积外接矩形（O(h)，不对角度做枚举采样）。

    三个指针（最远点 k、沿边投影最小/最大点 lo/hi）随边号单调前进，一个回转内
    每个指针至多走 h 步，故整体 O(h)。退化（0/1/2 点、共线、重复点）不抛异常，
    返回零宽度或零面积矩形。
    """
    xy = np.asarray(hull_points_2d, dtype=np.float64)
    if xy.ndim != 2 or xy.shape[1] != 2:
        raise ValueError(f"外接矩形输入必须是 (h, 2) 数组，当前形状 {xy.shape}")
    hull = _convex_hull_2d(xy)
    h = int(hull.shape[0])
    if h == 0:
        raise InsufficientGeometryError("无点可拟合外接矩形")
    if h == 1:
        p = hull[0]
        return OrientedRect((float(p[0]), float(p[1])), 0.0, 0.0, 0.0, 0.0)
    if h == 2:
        a, b = hull[0], hull[1]
        d = b - a
        length = float(np.hypot(d[0], d[1]))
        mid = (a + b) / 2.0
        angle = _fold_angle(float(np.degrees(np.arctan2(d[1], d[0]))))
        return OrientedRect((float(mid[0]), float(mid[1])), 0.0, length, angle, 0.0)

    # 初始化：只对第 0 条边做一次全扫描（一次性 O(h)），之后三个指针随边号单调前进，
    # 一个回转内各自至多走 h 步 → 整体 O(h)。
    # 若像常见写法那样直接把指针置为索引 1，首条边的极值点可能落在"后方"，
    # 指针会立刻停住（正方形会因此返回零宽度）。
    a0 = hull[0]
    b0 = hull[1]
    e0 = b0 - a0
    u0 = e0 / float(np.hypot(e0[0], e0[1]))
    v0 = np.array([-u0[1], u0[0]])
    proj_u = hull @ u0
    k = int(np.argmax(hull @ v0))
    lo = int(np.argmin(proj_u))
    hi = int(np.argmax(proj_u))

    best = OrientedRect((0.0, 0.0), 0.0, 0.0, 0.0, float("inf"))
    for i in range(h):
        a = hull[i]
        b = hull[(i + 1) % h]
        edge = b - a
        edge_len = float(np.hypot(edge[0], edge[1]))
        if edge_len <= EPS:
            continue
        u = edge / edge_len
        v = np.array([-u[1], u[0]])

        for _ in range(h):  # 最远点：max v·(p - a)
            nk = (k + 1) % h
            if float(v @ (hull[nk] - a)) > float(v @ (hull[k] - a)):
                k = nk
            else:
                break
        for _ in range(h):  # 沿边投影最小点
            nl = (lo + 1) % h
            if float(u @ hull[nl]) < float(u @ hull[lo]):
                lo = nl
            else:
                break
        for _ in range(h):  # 沿边投影最大点
            nh = (hi + 1) % h
            if float(u @ hull[nh]) > float(u @ hull[hi]):
                hi = nh
            else:
                break

        height = float(v @ (hull[k] - a))
        if height < 0.0:  # 退化方向（理论上 CCW 不出现），翻转法向保持非负
            v = -v
            height = -height
        span = float(u @ hull[hi]) - float(u @ hull[lo])
        if span < 0.0:
            span = 0.0
        area = height * span
        if area < best.area:
            s = (float(u @ hull[lo]) + float(u @ hull[hi])) / 2.0
            t = float(v @ a) + height / 2.0
            center = s * u + t * v
            if span >= height:
                long_side, short_side = span, height
                angle = _fold_angle(float(np.degrees(np.arctan2(u[1], u[0]))))
            else:
                long_side, short_side = height, span
                angle = _fold_angle(float(np.degrees(np.arctan2(v[1], v[0]))))
            best = OrientedRect(
                (float(center[0]), float(center[1])), short_side, long_side, angle, area
            )
    return best


def mabr_center(hull_points_2d: npt.ArrayLike) -> tuple[float, float]:
    """最小外接矩形中心（x, y）。需要宽高/角度做调试时用 :func:`min_area_rect`。"""
    return min_area_rect(hull_points_2d).center


# ---------------------------------------------------------------------------- 3. 分层中心


def extract_centers(layers: list[Layer]) -> npt.NDArray[np.float64]:
    """RECT 分支：逐层 2D 凸包 + MABR 取中心，返回 (m, 2)。

    同时把质量指标回填到传入的 ``Layer``（原地修改）：``center``、``rect_width``、
    ``rect_length``、``rect_angle_deg``、``valid_ratio``。被跳过的层不参与。
    """
    active = [layer for layer in layers if not layer.skipped]
    if not active:
        raise InsufficientGeometryError("没有可用层，无法提取中心")
    median_count = float(np.median([layer.n_points for layer in active]))
    centers: list[tuple[float, float]] = []
    for layer in active:
        rect = min_area_rect(layer.points)
        layer.center = rect.center
        layer.rect_width = rect.width
        layer.rect_length = rect.length
        layer.rect_angle_deg = rect.angle_deg
        layer.valid_ratio = float(layer.n_points) / median_count if median_count > EPS else 0.0
        centers.append(rect.center)
    return np.asarray(centers, dtype=np.float64)


# ---------------------------------------------------------------------------- 4. 稳健轴线


def _orthogonal_residuals(
    pts: npt.NDArray[np.float64], point: npt.NDArray[np.float64], direction: npt.NDArray[np.float64]
) -> npt.NDArray[np.float64]:
    """点到直线的正交距离（垂距），不是逐轴残差。"""
    rel = pts - point
    along = rel @ direction
    perp = rel - along[:, None] * direction[None, :]
    return np.asarray(np.sqrt(np.einsum("ij,ij->i", perp, perp)), dtype=np.float64)


def fit_robust_axis(
    centers: npt.ArrayLike,
    *,
    max_iter: int = MAX_ITERATIONS,
    tol_angle_deg: float = TOL_ANGLE_DEG,
    tol_residual: float = TOL_RESIDUAL,
    huber_k: float = HUBER_K,
) -> AxisFit:
    """PCA 初值 + IRLS(Huber) 正交距离拟合三维直线（文档 4.1.4）。"""
    pts = _as_points(centers)
    if pts.shape[0] < 2:
        raise InsufficientGeometryError("分层中心少于 2 个，无法拟合轴线")
    if max_iter < 1:
        raise ValueError("max_iter 必须 >= 1")

    point = pts.mean(axis=0)
    delta = pts - point
    cov = delta.T @ delta / float(pts.shape[0])
    eigvals, eigvecs = np.linalg.eigh(cov)
    if float(eigvals[-1]) <= EPS:
        raise InsufficientGeometryError("分层中心退化（重合），无法确定轴线方向")
    direction = np.asarray(eigvecs[:, -1], dtype=np.float64)

    history: list[tuple[int, float, float]] = []
    rms = float("inf")
    converged = False
    iterations = 0

    for it in range(1, max_iter + 1):
        iterations = it
        residuals = _orthogonal_residuals(pts, point, direction)
        mad = float(np.median(residuals))
        scale = MAD_TO_SIGMA * mad
        if scale <= EPS:
            scale = 1.0  # 全部中心已在轴上：权重全 1，不发生除零
        weights = np.where(
            residuals <= huber_k * scale, 1.0, huber_k * scale / np.maximum(residuals, EPS)
        )
        wsum = float(weights.sum())
        point_next = (weights[:, None] * pts).sum(axis=0) / wsum
        shifted = pts - point_next
        cov_w = (shifted * weights[:, None]).T @ shifted / wsum
        _, vecs = np.linalg.eigh(cov_w)
        d_next = np.asarray(vecs[:, -1], dtype=np.float64)
        if float(d_next @ direction) < 0.0:
            d_next = -d_next

        cos_angle = min(1.0, abs(float(d_next @ direction)))
        angle = float(np.degrees(np.arccos(cos_angle)))
        new_rms = float(np.sqrt(np.mean(_orthogonal_residuals(pts, point_next, d_next) ** 2)))
        delta_rms = abs(new_rms - rms)
        point, direction, rms = point_next, d_next, new_rms
        history.append((it, angle, rms))
        if angle < tol_angle_deg or delta_rms < tol_residual:  # 文档为"或"
            converged = True
            break

    residuals = _orthogonal_residuals(pts, point, direction)
    mad = float(np.median(residuals))
    scale = MAD_TO_SIGMA * mad if mad > EPS else 1.0
    if mad > EPS:
        inliers = int(np.count_nonzero(residuals <= huber_k * scale))
    else:
        inliers = int(residuals.shape[0])
    return AxisFit(
        point=np.asarray(point, dtype=np.float64),
        direction=np.asarray(direction, dtype=np.float64),
        rms=float(np.sqrt(np.mean(residuals**2))),
        inliers=inliers,
        iterations=iterations,
        converged=converged,
        mad_scale=float(scale),
        history=tuple(history),
    )


# ---------------------------------------------------------------------------- 5. 垂直度


def _axis_point_at(
    point: npt.NDArray[np.float64], direction: npt.NDArray[np.float64], z: float
) -> npt.NDArray[np.float64]:
    """轴线上 z 处的一点。轴线近水平（|d_z|≈0）时无法按高程求水平坐标，直接报错。"""
    dz = float(direction[2])
    if abs(dz) <= 1e-9:
        raise InsufficientGeometryError("拟合轴线接近水平，无法按高程求水平坐标")
    t = (z - float(point[2])) / dz
    return point + t * direction


def calculate_plumb(
    points: npt.ArrayLike,
    *,
    step: float = 1.0,
    total_height: float | None = None,
    min_points: int = MIN_LAYER_POINTS,
) -> PlumbResult:
    """串起「切片 → 分层中心 → 稳健轴线 → 倾斜率（‰）」，按文档 4.1.5 出数。

    ``total_height``：设计墩高（m）。仅用于降级判定中的"跨度占总高比例"；
    不传则用实测 z 跨度（此时跨度判据恒不触发，因为分母同源）。
    """
    cloud = _as_points(points)
    if cloud.shape[0] == 0:
        raise InsufficientGeometryError("点云为空，无法计算垂直度")

    layers = slice_layers(cloud, step=step, min_points=min_points)
    valid = [layer for layer in layers if not layer.skipped]
    skipped = tuple(layer for layer in layers if layer.skipped)
    if not valid:
        raise InsufficientGeometryError(f"全部 {len(layers)} 个切片均被跳过（遮挡或点数不足）")
    if len(valid) == 1:
        raise InsufficientGeometryError("有效层数仅 1，无法拟合轴线（至少需要 2 层）")

    centers = extract_centers(valid)
    # 分层中心的高程取该层实测 z 均值（不按切片边界中点，避免外推）
    z_mid = np.asarray([layer.z_mean for layer in valid], dtype=np.float64)
    centers3d = np.column_stack([centers, z_mid])
    axis = fit_robust_axis(centers3d)

    # 4.1.5 的 [z_b, z_t]：本模块取有效层的实测高程极值；设计资料接入后应改为设计高程区间
    z_b = min(layer.z_data_min for layer in valid)
    z_t = max(layer.z_data_max for layer in valid)
    span = z_t - z_b
    if span <= EPS:
        raise InsufficientGeometryError("有效层跨越的高度为 0，无法计算倾斜率")

    p_bottom = _axis_point_at(axis.point, axis.direction, z_b)
    p_top = _axis_point_at(axis.point, axis.direction, z_t)
    dx = float(p_top[0]) - float(p_bottom[0])
    dy = float(p_top[1]) - float(p_bottom[1])
    delta_h = float(np.hypot(dx, dy))

    slope_ratio = delta_h / span
    tilt_permille = slope_ratio * 1000.0
    azimuth = None if delta_h <= EPS else float(np.degrees(np.arctan2(dy, dx))) % 360.0

    # 遮挡率：按"每层点密度（点数/该层实测高程厚度）"的中位数推算应有总数，
    # 再看实际保留点数缺了多少。这样末层只覆盖 0.1 m 时不会把缺口算大。
    kept = sum(layer.n_points for layer in valid)
    densities = [
        layer.n_points / (layer.z_data_max - layer.z_data_min)
        for layer in layers
        if layer.z_data_max - layer.z_data_min > EPS
    ]
    occlusion = 0.0
    if densities:
        expected = float(np.median(densities)) * (z_t - z_b)
        if expected > EPS:
            occlusion = max(0.0, min(1.0, 1.0 - float(kept) / expected))

    reasons: list[str] = []
    if len(valid) < MIN_VALID_LAYERS:
        reasons.append(f"有效层数不足({len(valid)}<{MIN_VALID_LAYERS})")
    reference = (
        float(total_height)
        if total_height is not None
        else float(cloud[:, 2].max() - cloud[:, 2].min())
    )
    if reference > EPS and span < MIN_SPAN_RATIO * reference:
        reasons.append(
            f"有效层跨度不足({span:.2f}m<{MIN_SPAN_RATIO * reference:.2f}m，占总高"
            f"{MIN_SPAN_RATIO:.0%})"
        )
    if len(valid) < MIN_VALID_LAYER_RATIO * len(layers):
        reasons.append(f"有效层占比不足({len(valid)}/{len(layers)}<60%)")

    return PlumbResult(
        tilt_permille=tilt_permille,
        slope_ratio=slope_ratio,
        horizontal_offset_mm=delta_h * 1000.0,
        azimuth_deg=azimuth,
        valid_layers=len(valid),
        total_layers=len(layers),
        skipped_layers=skipped,
        occlusion_ratio=occlusion,
        residual_rms_mm=axis.rms * 1000.0,
        degraded=bool(reasons),
        degrade_reason="；".join(reasons) if reasons else None,
        z_bottom=z_b,
        z_top=z_t,
        axis=axis,
    )
