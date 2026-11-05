"""M07 核心几何分析：SOR 去噪 → 切片 → 污染剔除 → 截面中心 → 稳健轴线 → 垂直度（‰）。

算法依据（《公路桥隧结构智能检测与数字孪生运维平台详细设计说明书 V1.2》4.1）：

4.1.2 预处理 原文（379–391 行）：
    2. 统计滤波去噪（SOR，k=20，阈值 2σ）；
    4. 计算每层有效点比例 valid_ratio = 有效点数 /（密度 × 截面周长 × 切片厚度）；
    5. 若整墩有效点比例 < 60%，判"数据不足"。
    切片污染剔除：若某层点云在水平投影上的外接圆半径超过相邻层中位数的 1.3 倍，
    判定该层混入盖梁或系梁，剔除该层并记录。
本模块 ``statistical_outlier_removal`` 与 ``reject_contaminated_layers`` 即此两条；
污染剔除用的是**最小包围圆**（Welzl），不是"最远点到质心"。

4.1.3 分层截面中心估计（矩形墩 RECT 分支）原文（397–403 行）：
    H = 二维凸包(P_layer)
    R = 旋转卡壳最小面积外接矩形(H)
    center = R 的中心；width = R 短边；length = R 长边
    质量判据：覆盖率 cover = 有效点数 / (密度 × R 周长 × 切片厚度) ≥ 0.7
    若 cover < 0.7：改用"四边 RANSAC 直线拟合 + 交点求中心"
本模块 ``min_area_rect`` / ``extract_centers`` 即 MABR 分支，
``ransac_rect_center`` 即四边 RANSAC 分支。

4.1.4 稳健轴线拟合 原文（429–435 行）：PCA 初值 → IRLS（Huber）正交距离（TLS），
终止"方向夹角 < 0.01° 或残差变化 < 1e-4"。原文明确"普通最小二乘（沿某一坐标轴回归）
会产生方向偏置，不采用"。``fit_robust_axis`` 严格遵守。

4.1.5 垂直度定义式 原文（443–447 行，**文档已给出可直接编码的公式**）：
    以设计高程区间 [z_b, z_t]（默认承台顶面至墩顶）为准：
        Δ_h = 拟合轴线在 z = z_t 处的水平坐标 − 在 z = z_b 处的水平坐标
        i   = |Δ_h| / (z_t − z_b) × 1000   （单位：‰，即倾斜率）
        最大局部偏离 δ_max = max_i(第 i 层圆心/中心到拟合轴线的水平垂距)
倾斜率单位是 ‰，分子是水平偏移（m），分母是高差（m）。

4.1.8（482 行）："遮挡导致的中心偏移可达 10 mm 以上"。本模块在"缺边且无设计截面"
时只给这个量级的估计，不编精确上界。

与文档的差异 / 口径声明（均显式标注，不把自造规则当成文档要求）：

1. ``[z_b, z_t]``：纯算法层拿不到设计承台顶/墩顶高程，默认取有效层实测 z 极值；
   调用方可传 ``total_height`` 供降级判定，设计资料接入后应改传设计高程区间。
2. **覆盖率 cover**：文档 4.1.3 的公式需要点云密度。本模块提供两条口径：
   (a) 传入 ``density``（点/㎡）时，严格按文档公式算，``cover_is_proxy=False``；
   (b) 不传时用**四边沿边长度方向的采样占空比**（最小边占空比）作代理，
       ``cover_is_proxy=True``。代理只衡量"四条边是否都被采到"，**不是**文档公式，
       报告中带标志，不冒充 density 口径。分支判定取 ``min(cover, 占空比)``，
       因为整面缺失时文档公式可能仍有 0.75（>0.7），只有占空比能识别。
3. **SOR 的距离口径**：每个点取到最近 k 个邻居（不含自身）的平均距离 d，
   阈值 ``mean(d) + std_ratio·std(d)``（文档 4.1.2"k=20，阈值 2σ"）。
   边角点跨两个垂直面，单位面积邻居数与平面相同（90° 凸角内半径 r 的半球与
   平面半圆盘计数一致），因此 k=20 不会系统性剔除边角；实测见测试。
4. **切片污染剔除的邻层定义**：按层序号排序，取当前层**前后各最多 2 个**"有点且未被
   跳过"的层（不含自身）作邻层，取半径中位数；首/末层只有单侧邻层时只用该侧；
   可用邻层少于 2 个时不判定（数据不足，不臆断）。阈值 1.3 可参数化。
5. RANSAC 分支对"整面缺失"有物理限制：四边中有一条没有任何点时，几何上无法定出
   该边位置，只能靠设计截面尺寸补边（``reconstructed_from_design=True``）；
   没有设计尺寸时只能给降级结论 + 4.1.8 量级的中心偏移估计（≈10 mm 以上）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal, overload

import numpy as np
import numpy.typing as npt
from scipy.spatial import ConvexHull, QhullError, cKDTree

EPS = 1e-12
# 每层最小点数：实现参数（文档按"覆盖率 ≥ 0.7"判定，但需要密度输入），可由调用方覆盖
MIN_LAYER_POINTS = 30
HUBER_K = 1.345
MAX_ITERATIONS = 50
TOL_ANGLE_DEG = 0.01  # 文档 4.1.4 第 3 条
TOL_RESIDUAL = 1e-4  # 文档 4.1.4 第 3 条，单位 m
MAD_TO_SIGMA = 1.4826
MIN_VALID_LAYERS = 3
MIN_SPAN_RATIO = 0.6
MIN_VALID_LAYER_RATIO = 0.6  # 文档 4.1.7："有效层数 < 总层数 60% → 数据不足"
SOR_K = 20  # 文档 4.1.2
SOR_STD_RATIO = 2.0  # 文档 4.1.2
CONTAMINATION_RATIO = 1.3  # 文档 4.1.2 第 389 行
COVER_THRESHOLD = 0.7  # 文档 4.1.3
CENTER_OFFSET_HINT_MM = 10.0  # 文档 4.1.8 量级估计（"10 mm 以上"）
SIDE_NAMES = ("u_min", "u_max", "v_min", "v_max")


class InsufficientGeometryError(Exception):
    """几何量不可计算：点云为空、全部切片被跳过、有效层不足以定线。"""


@dataclass(frozen=True)
class SorReport:
    """SOR 统计量（便于审计"到底删了多少、阈值在哪"）。"""

    input_points: int
    output_points: int
    removed_ratio: float
    mean_distance: float
    std_distance: float
    threshold: float
    k_effective: int
    elapsed_s: float


@dataclass
class Layer:
    """一个 z 切片。

    ``z_min``/``z_max`` 是切片边界（下闭上开）；``z_data_min``/``z_data_max``/``z_mean``
    是该层点的实测高程统计，用于计算 z_b/z_t 与轴线上的点，避免按边界外推。
    ``mec_radius`` 是水平投影的最小包围圆半径（污染剔除用）。
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
    valid_ratio: float = 0.0  # 层点数 / 中位数层点数（仅质量提示）
    rect_width: float = 0.0
    rect_length: float = 0.0
    rect_angle_deg: float = 0.0
    mec_radius: float = 0.0
    # cover 与分支
    cover: float = 0.0
    cover_is_proxy: bool = True
    side_occupancy: float = 0.0
    branch: str = "MABR"
    missing_sides: tuple[str, ...] = ()
    reconstructed_from_design: bool = False
    center_offset_bound_mm: float | None = None

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
class RansacRect:
    """四边 RANSAC 重建结果。

    ``missing_sides`` 是"没有足够沿边采样"的边；``reconstructed_from_design`` 表示
    缺失边由设计截面补出；``center_offset_bound_mm`` 仅在无设计尺寸补边时给出
    （4.1.8 量级估计，不是精确上界）。
    """

    center: tuple[float, float]
    width: float
    length: float
    angle_deg: float
    inlier_counts: tuple[int, int, int, int]
    occupancies: tuple[float, float, float, float]
    missing_sides: tuple[str, ...]
    reconstructed_from_design: bool
    center_offset_bound_mm: float | None


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
    sor: SorReport | None = None
    layer_centers: npt.NDArray[np.float64] | None = None  # (m, 3) 有效层中心
    cover_mean: float = 0.0
    cover_is_proxy: bool = True
    ransac_layers: int = 0
    reconstructed_layers: int = 0
    center_offset_bound_mm: float | None = None


# ---------------------------------------------------------------------------- 输入校验


def _as_points(points: npt.ArrayLike) -> npt.NDArray[np.float64]:
    arr = np.asarray(points, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError(f"点云必须是 (N, 3) 数组，当前形状 {arr.shape}")
    return arr


# ---------------------------------------------------------------------------- 0. SOR 去噪


@overload
def statistical_outlier_removal(
    points: npt.ArrayLike,
    k: int = SOR_K,
    std_ratio: float = SOR_STD_RATIO,
    *,
    return_report: Literal[True] = True,
) -> tuple[npt.NDArray[np.float64], SorReport]: ...


@overload
def statistical_outlier_removal(
    points: npt.ArrayLike,
    k: int = SOR_K,
    std_ratio: float = SOR_STD_RATIO,
    *,
    return_report: Literal[False],
) -> npt.NDArray[np.float64]: ...


def statistical_outlier_removal(
    points: npt.ArrayLike,
    k: int = SOR_K,
    std_ratio: float = SOR_STD_RATIO,
    *,
    return_report: bool = True,
) -> tuple[npt.NDArray[np.float64], SorReport] | npt.NDArray[np.float64]:
    """SOR：剔除"到最近 k 个邻居的平均距离"超过 mean + std_ratio·std 的点。

    距离不含自身（``k+1`` 查询后丢掉第 0 列）；用 ``cKDTree`` 批量查询，不做 O(n²)。
    阈值是全局单侧阈值：若点云本身无噪声但采样间距均匀（规则格网），d 的方差≈0，
    不会误删；若为随机均匀采样，d 必有分布，单侧 2σ 尾部理论上会删约 2.3%
    （这是 SOR 的定义，不是边角错杀）。
    """
    cloud = _as_points(points)
    n = int(cloud.shape[0])
    started = time.perf_counter()
    if n == 0:
        raise InsufficientGeometryError("点云为空，无法执行 SOR")
    if k < 1:
        raise ValueError("k 必须 >= 1")
    if std_ratio < 0.0:
        raise ValueError("std_ratio 不能为负")

    k_eff = min(int(k), n - 1)
    if k_eff <= 0:
        report = SorReport(n, n, 0.0, 0.0, 0.0, 0.0, 0, time.perf_counter() - started)
        return (cloud, report) if return_report else cloud

    tree = cKDTree(cloud)
    distances, _ = tree.query(cloud, k=k_eff + 1, workers=-1)
    mean_distance = np.asarray(distances[:, 1:].mean(axis=1), dtype=np.float64)
    mean_d = float(mean_distance.mean())
    std_d = float(mean_distance.std())
    threshold = mean_d + float(std_ratio) * std_d
    keep = mean_distance <= threshold
    filtered = np.ascontiguousarray(cloud[keep], dtype=np.float64)

    report = SorReport(
        input_points=n,
        output_points=int(filtered.shape[0]),
        removed_ratio=1.0 - float(filtered.shape[0]) / float(n),
        mean_distance=mean_d,
        std_distance=std_d,
        threshold=threshold,
        k_effective=k_eff,
        elapsed_s=time.perf_counter() - started,
    )
    return (filtered, report) if return_report else filtered


# ---------------------------------------------------------------------------- 1. 切片


def slice_layers(
    points: npt.ArrayLike, step: float = 1.0, min_points: int = MIN_LAYER_POINTS
) -> list[Layer]:
    """沿 Z 按 ``step`` 切片，返回全部层（含被跳过的层，便于追溯）。

    边界规则：从实测最低点 z0 起算，第 b 层为 [z0 + b·step, z0 + (b+1)·step)。
    恰好落在边界上的点归入**上一层**（下闭上开），因此每个点只属于一层。
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


# ---------------------------------------------------------------------------- 2. 最小外接圆


def _circle_from_two(
    a: npt.NDArray[np.float64], b: npt.NDArray[np.float64]
) -> tuple[npt.NDArray[np.float64], float]:
    center = np.asarray((a + b) * 0.5, dtype=np.float64)
    return center, float(np.linalg.norm(a - b)) * 0.5


def _circle_from_three(
    a: npt.NDArray[np.float64], b: npt.NDArray[np.float64], c: npt.NDArray[np.float64]
) -> tuple[npt.NDArray[np.float64], float]:
    """三点外接圆；共线时退化为最远两点圆（不抛异常）。"""
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    cx, cy = float(c[0]), float(c[1])
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) <= EPS:
        pairs = ((a, b), (a, c), (b, c))
        return max((_circle_from_two(p, q) for p, q in pairs), key=lambda item: item[1])
    aa = ax * ax + ay * ay
    bb = bx * bx + by * by
    cc = cx * cx + cy * cy
    ux = (aa * (by - cy) + bb * (cy - ay) + cc * (ay - by)) / d
    uy = (aa * (cx - bx) + bb * (ax - cx) + cc * (bx - ax)) / d
    center = np.asarray((ux, uy), dtype=np.float64)
    radius = float(max(np.linalg.norm(a - center), np.linalg.norm(b - center)))
    return center, max(radius, float(np.linalg.norm(c - center)))


def minimum_enclosing_circle(
    points: npt.ArrayLike, *, seed: int = 0
) -> tuple[tuple[float, float], float]:
    """最小包围圆（Welzl 随机增量，期望 O(n)；输入先用凸包顶点压缩）。

    不用"最远点到质心"近似：那会随点云偏斜明显偏大，无法作为污染判据。
    退化：单点半径 0；两点/共线为半长；空集报 :class:`InsufficientGeometryError`。
    """
    xy = np.asarray(points, dtype=np.float64)
    if xy.ndim != 2 or xy.shape[1] != 2:
        raise ValueError(f"最小包围圆输入必须是 (n, 2) 数组，当前形状 {xy.shape}")
    if xy.shape[0] == 0:
        raise InsufficientGeometryError("无点可求最小包围圆")
    hull = _convex_hull_2d(xy)
    if hull.shape[0] == 0:
        raise InsufficientGeometryError("无点可求最小包围圆")
    if hull.shape[0] == 1:
        p = hull[0]
        return (float(p[0]), float(p[1])), 0.0

    rng = np.random.default_rng(seed)
    ordered = hull[rng.permutation(int(hull.shape[0]))]
    center = ordered[0].copy()
    radius = 0.0
    for i in range(1, ordered.shape[0]):
        if float(np.linalg.norm(ordered[i] - center)) - radius <= 1e-9:
            continue
        center = ordered[i].copy()
        radius = 0.0
        for j in range(i):
            if float(np.linalg.norm(ordered[j] - center)) - radius <= 1e-9:
                continue
            center, radius = _circle_from_two(ordered[i], ordered[j])
            for k in range(j):
                if float(np.linalg.norm(ordered[k] - center)) - radius <= 1e-9:
                    continue
                center, radius = _circle_from_three(ordered[i], ordered[j], ordered[k])
    return (float(center[0]), float(center[1])), float(radius)


# ---------------------------------------------------------------------------- 3. 污染剔除


def reject_contaminated_layers(
    layers: list[Layer],
    *,
    ratio: float = CONTAMINATION_RATIO,
    neighbor_span: int = 2,
    min_neighbors: int = 2,
) -> list[Layer]:
    """按文档第 389 行剔除"外接圆半径 > 邻层半径中位数 × ratio"的层（原地标记）。

    邻层定义（本实现口径，文档只说"相邻层"）：按 layer.index 排序，取当前层
    前后各最多 ``neighbor_span``（默认 2）个"有点且未被跳过"的层，不含自身；
    首/末层只有单侧邻层时只用该侧；可用邻层少于 ``min_neighbors``（默认 2）时
    不做判定（样本不足，不臆断）。

    返回被判污染的层列表；这些层 ``skipped=True``、``skip_reason`` 带实测半径、
    邻层中位数与倍数，并会进入 ``PlumbResult.skipped_layers``。
    """
    if ratio <= 1.0:
        raise ValueError("ratio 必须 > 1")
    if neighbor_span < 1:
        raise ValueError("neighbor_span 必须 >= 1")

    eligible = [layer for layer in layers if not layer.skipped and layer.n_points > 0]
    for layer in eligible:
        _, radius = minimum_enclosing_circle(layer.points, seed=layer.index)
        layer.mec_radius = radius

    rejected: list[Layer] = []
    for pos, layer in enumerate(eligible):
        neighbors: list[float] = []
        for offset in range(1, neighbor_span + 1):
            if pos - offset >= 0:
                neighbors.append(eligible[pos - offset].mec_radius)
            if pos + offset < len(eligible):
                neighbors.append(eligible[pos + offset].mec_radius)
        if len(neighbors) < min_neighbors:
            continue
        median = float(np.median(neighbors))
        if median <= EPS:
            continue
        if layer.mec_radius > ratio * median:
            layer.skipped = True
            layer.skip_reason = (
                f"污染剔除(半径{layer.mec_radius:.3f}m > 邻层中位数{median:.3f}m"
                f"的{ratio:.2f}倍)"
            )
            rejected.append(layer)
    return rejected


# ---------------------------------------------------------------------------- 4. 最小外接矩形


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
    每个指针至多走 h 步，故整体 O(h)。退化（0/1/2 点、共线、重复点）不抛异常。
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

    # 初始化：只对第 0 条边做一次全扫描（一次性 O(h)），之后三个指针随边号单调前进。
    # 若把指针置为索引 1，首条边的极值点可能落在"后方"，指针立刻停住（正方形返回零宽度）。
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


# ---------------------------------------------------------------------------- 5. 覆盖率


def _side_metrics(
    xy: npt.NDArray[np.float64],
    center: npt.NDArray[np.float64],
    u: npt.NDArray[np.float64],
    v: npt.NDArray[np.float64],
    half_u: float,
    half_v: float,
    band: float,
    n_bins: int = 10,
) -> tuple[tuple[int, int, int, int], tuple[float, float, float, float]]:
    """四条边各自的内点数与沿边采样占空比（0–1）。

    占空比只看向边线法向的带内点沿边长方向铺开了多少格。整面缺失时，相邻面在角点
    附近仍会贡献少量带内点，但只占两端几格，占空比会明显低于 0.7——这是识别"缺一整面"
    的关键，单看点数是识别不出来的。
    """
    rel = xy - center
    pu = rel @ u
    pv = rel @ v
    specs = (
        (pu + half_u, pv, half_v),
        (pu - half_u, pv, half_v),
        (pv + half_v, pu, half_u),
        (pv - half_v, pu, half_u),
    )
    counts: list[int] = []
    occupancies: list[float] = []
    for residual, along, edge_half in specs:
        mask = np.abs(residual) <= band
        count = int(np.count_nonzero(mask))
        counts.append(count)
        if count == 0 or edge_half <= EPS:
            occupancies.append(0.0)
            continue
        bins = np.linspace(-edge_half, edge_half, n_bins + 1)
        idx = np.clip(np.digitize(along[mask], bins) - 1, 0, n_bins - 1)
        occupancies.append(float(np.unique(idx).size) / float(n_bins))
    return (
        (counts[0], counts[1], counts[2], counts[3]),
        (occupancies[0], occupancies[1], occupancies[2], occupancies[3]),
    )


def _frame(angle_deg: float) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    theta = np.radians(float(angle_deg))
    u = np.asarray([np.cos(theta), np.sin(theta)], dtype=np.float64)
    v = np.asarray([-u[1], u[0]], dtype=np.float64)
    return u, v


def layer_coverage(
    xy: npt.ArrayLike,
    rect: OrientedRect,
    *,
    density: float | None = None,
    thickness: float | None = None,
    band: float | None = None,
    n_bins: int = 10,
) -> tuple[float, float, bool]:
    """返回 (cover, side_occupancy, cover_is_proxy)。

    ``density`` 给定 → 严格按 4.1.3 公式：``n / (density × 2(w+l) × thickness)``，
    否则用四边占空比的最小值作代理（``cover_is_proxy=True``）。两者都返回，
    分支判定由调用方取 ``min``（原因见模块 docstring 差异 2）。
    """
    pts = np.asarray(xy, dtype=np.float64)
    u, v = _frame(rect.angle_deg)
    half_u = rect.length / 2.0
    half_v = rect.width / 2.0
    band_used = band if band is not None else max(0.02, 0.05 * min(rect.width, rect.length))
    center = np.asarray(rect.center, dtype=np.float64)
    counts, occ = _side_metrics(pts, center, u, v, half_u, half_v, band_used, n_bins)
    occupancy = min(occ)

    if density is not None and density > 0.0 and thickness is not None:
        perimeter = 2.0 * (rect.width + rect.length)
        expected = float(density) * perimeter * max(thickness, EPS)
        cover = float(pts.shape[0]) / expected if expected > EPS else 0.0
        return cover, occupancy, False
    return occupancy, occupancy, True


# ---------------------------------------------------------------------------- 6. RANSAC 四边


def _ransac_constant(
    values: npt.NDArray[np.float64], band: float, rng: np.random.Generator, iterations: int = 20
) -> tuple[float, int]:
    """一维稳健直线（常量）拟合：RANSAC 选假设 + 内点均值重拟合。"""
    if values.size == 0:
        raise InsufficientGeometryError("空集合无法做一维 RANSAC")
    best = float(values[0])
    best_count = -1
    for _ in range(iterations):
        candidate = float(values[int(rng.integers(0, values.size))])
        count = int(np.count_nonzero(np.abs(values - candidate) <= band))
        if count > best_count:
            best, best_count = candidate, count
    for _ in range(2):  # 内点重拟合，两轮足够（常量模型，不会漂移）
        inliers = values[np.abs(values - best) <= band]
        if inliers.size == 0:
            break
        best = float(inliers.mean())
    count = int(np.count_nonzero(np.abs(values - best) <= band))
    return best, count


def _ransac_orientation(
    xy: npt.NDArray[np.float64], rng: np.random.Generator, band: float
) -> float:
    """RANSAC 估计矩形主方向（按 90° 折叠，处理 4 重对称）。

    假设由随机点对给出，评分 = 落在四条边线带内的点数（数据只在截面轮廓上，
    方向对时几乎所有点都贴边；方向错时包围盒变大，贴边点显著减少）。
    """
    n = int(xy.shape[0])
    subset = xy if n <= 3000 else xy[rng.choice(n, size=3000, replace=False)]
    scale = float(min(subset.max(axis=0) - subset.min(axis=0)))
    if scale <= EPS:
        raise InsufficientGeometryError("截面点云退化，无法估计主方向")

    def score(theta_deg: float) -> int:
        u, v = _frame(theta_deg)
        pu = subset @ u
        pv = subset @ v
        du = np.minimum(pu - pu.min(), pu.max() - pu)
        dv = np.minimum(pv - pv.min(), pv.max() - pv)
        return int(np.count_nonzero(np.minimum(du, dv) <= band))

    best_theta = 0.0
    best_score = -1
    for _ in range(120):
        i = int(rng.integers(0, subset.shape[0]))
        j = int(rng.integers(0, subset.shape[0]))
        if i == j:
            continue
        delta = subset[j] - subset[i]
        if float(np.hypot(delta[0], delta[1])) <= EPS:
            continue
        theta = float(np.degrees(np.arctan2(delta[1], delta[0]))) % 90.0
        current = score(theta)
        if current > best_score:
            best_theta, best_score = theta, current

    for theta in np.linspace(best_theta - 3.0, best_theta + 3.0, 31):
        current = score(float(theta))
        if current > best_score:
            best_theta, best_score = float(theta), current

    # 第二阶段在全量点上细搜 ±1°（0.05° 步长）：0.1° 的方向误差在 2 m 半宽上就是 3.5 mm
    full_u = xy[:, 0]
    full_v = xy[:, 1]

    def score_full(theta_deg: float) -> int:
        u, v = _frame(theta_deg)
        proj_u = full_u * u[0] + full_v * u[1]
        proj_v = full_u * v[0] + full_v * v[1]
        du = np.minimum(proj_u - proj_u.min(), proj_u.max() - proj_u)
        dv = np.minimum(proj_v - proj_v.min(), proj_v.max() - proj_v)
        return int(np.count_nonzero(np.minimum(du, dv) <= band))

    for theta in np.linspace(best_theta - 1.0, best_theta + 1.0, 41):
        current = score_full(float(theta))
        if current > best_score:
            best_theta, best_score = float(theta), current
    return _fold_angle(best_theta)


def ransac_rect_center(
    xy: npt.ArrayLike,
    *,
    design_section: float | tuple[float, float] | None = None,
    seed: int = 0,
    band_ratio: float = 0.05,
    min_side_ratio: float = 0.03,
    min_side_occupancy: float = 0.6,
    n_bins: int = 10,
) -> RansacRect:
    """四边 RANSAC 重建截面中心（文档 4.1.3 的 cover < 0.7 分支）。

    步骤：① 方向 RANSAC（90° 折叠）；② 在 (u, v) 局部系下对 u、v 各做两次一维
    RANSAC 得到两条平行边的位置（两两垂直由共用正交基保证）；③ 四边求交得角点，
    中心取角点均值；④ 按占空比识别缺边：
    - 四边都有足够沿边采样 → 纯几何重建；
    - 缺边且给了 ``design_section`` → 用设计尺寸补出缺失边并标
      ``reconstructed_from_design=True``（``design_section`` 为标量表示正方形，
      二元组按 (u 向尺寸, v 向尺寸) 解释）；
    - 缺边且无设计尺寸 → ``center_offset_bound_mm=10.0``（4.1.8 量级估计），
      并由上层置 ``degraded=True``；不会假装这是可信中心。
    同一轴两条边都缺失时几何上无解（设计尺寸也只能补一条），抛
    :class:`InsufficientGeometryError`，由调用方回退到 MABR 并标记。
    """
    pts = np.asarray(xy, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 2:
        raise ValueError(f"RANSAC 输入必须是 (n, 2) 数组，当前形状 {pts.shape}")
    n = int(pts.shape[0])
    if n < 10:
        raise InsufficientGeometryError("点数不足，无法做四边 RANSAC")

    rng = np.random.default_rng(seed)
    span = pts.max(axis=0) - pts.min(axis=0)
    band = max(0.02, float(band_ratio) * float(min(span)))
    theta = _ransac_orientation(pts, rng, band)
    u, v = _frame(theta)
    pu = pts @ u
    pv = pts @ v

    u1, _ = _ransac_constant(pu, band, rng)
    rest = pu[np.abs(pu - u1) > band]
    if rest.size == 0:
        raise InsufficientGeometryError("u 向只有一个边带，无法确定矩形宽度")
    u2, _ = _ransac_constant(rest, band, rng)
    v1, _ = _ransac_constant(pv, band, rng)
    rest_v = pv[np.abs(pv - v1) > band]
    if rest_v.size == 0:
        raise InsufficientGeometryError("v 向只有一个边带，无法确定矩形高度")
    v2, _ = _ransac_constant(rest_v, band, rng)

    u_min, u_max = sorted((u1, u2))
    v_min, v_max = sorted((v1, v2))
    center = ((u_min + u_max) / 2.0) * u + ((v_min + v_max) / 2.0) * v
    counts, occ = _side_metrics(
        pts, center, u, v, (u_max - u_min) / 2.0, (v_max - v_min) / 2.0, band, n_bins
    )

    min_points = max(10, int(min_side_ratio * n))
    missing = tuple(
        name
        for name, count, occupancy in zip(SIDE_NAMES, counts, occ, strict=True)
        if count < min_points or occupancy < min_side_occupancy
    )
    reconstructed = False
    bound: float | None = None
    if missing:
        design_pair: tuple[float, float] | None = None
        if design_section is not None:
            if isinstance(design_section, int | float):
                design_pair = (float(design_section), float(design_section))
            else:
                design_pair = (float(design_section[0]), float(design_section[1]))
            if design_pair[0] <= 0.0 or design_pair[1] <= 0.0:
                raise ValueError("design_section 尺寸必须为正")
        if design_pair is None:
            bound = CENTER_OFFSET_HINT_MM
        else:
            missing_u = [name for name in missing if name.startswith("u_")]
            missing_v = [name for name in missing if name.startswith("v_")]
            if len(missing_u) >= 2 or len(missing_v) >= 2:
                raise InsufficientGeometryError("同一轴向两条边均缺失，无法用设计尺寸补齐")
            if "u_min" in missing:
                u_min = u_max - design_pair[0]
            if "u_max" in missing:
                u_max = u_min + design_pair[0]
            if "v_min" in missing:
                v_min = v_max - design_pair[1]
            if "v_max" in missing:
                v_max = v_min + design_pair[1]
            reconstructed = True
            center = ((u_min + u_max) / 2.0) * u + ((v_min + v_max) / 2.0) * v

    u_extent = u_max - u_min
    v_extent = v_max - v_min
    if u_extent >= v_extent:
        length, width = u_extent, v_extent
        angle = _fold_angle(theta)
    else:
        length, width = v_extent, u_extent
        angle = _fold_angle(theta + 90.0)

    return RansacRect(
        center=(float(center[0]), float(center[1])),
        width=float(width),
        length=float(length),
        angle_deg=float(angle),
        inlier_counts=counts,
        occupancies=occ,
        missing_sides=missing,
        reconstructed_from_design=reconstructed,
        center_offset_bound_mm=bound,
    )


# ---------------------------------------------------------------------------- 7. 分层中心


def extract_centers(
    layers: list[Layer],
    *,
    density: float | None = None,
    cover_threshold: float = COVER_THRESHOLD,
    design_section: float | tuple[float, float] | None = None,
    ransac_seed: int = 0,
    enable_ransac: bool = True,
) -> npt.NDArray[np.float64]:
    """RECT 分支：逐层 MABR 取中心；cover 不达标时走四边 RANSAC（文档 4.1.3）。

    质量指标回填到 ``Layer``：``center``、``rect_*``、``cover``、``side_occupancy``、
    ``branch``、``missing_sides``、``reconstructed_from_design``、
    ``center_offset_bound_mm``、``valid_ratio``。被跳过的层不参与。
    """
    active = [layer for layer in layers if not layer.skipped]
    if not active:
        raise InsufficientGeometryError("没有可用层，无法提取中心")
    median_count = float(np.median([layer.n_points for layer in active]))
    centers: list[tuple[float, float]] = []

    for layer in active:
        rect = min_area_rect(layer.points)
        layer.rect_width = rect.width
        layer.rect_length = rect.length
        layer.rect_angle_deg = rect.angle_deg
        layer.valid_ratio = float(layer.n_points) / median_count if median_count > EPS else 0.0

        thickness = layer.z_data_max - layer.z_data_min
        if thickness <= EPS:
            thickness = layer.z_max - layer.z_min
        cover, occupancy, is_proxy = layer_coverage(
            layer.points, rect, density=density, thickness=thickness
        )
        layer.cover = cover
        layer.side_occupancy = occupancy
        layer.cover_is_proxy = is_proxy
        effective = min(cover, occupancy)
        center = rect.center

        if enable_ransac and effective < cover_threshold:
            try:
                rebuilt = ransac_rect_center(
                    layer.points, design_section=design_section, seed=ransac_seed + layer.index
                )
            except InsufficientGeometryError:
                layer.branch = "MABR(回退)"
                layer.center_offset_bound_mm = CENTER_OFFSET_HINT_MM
            else:
                center = rebuilt.center
                layer.branch = "RANSAC"
                layer.missing_sides = rebuilt.missing_sides
                layer.reconstructed_from_design = rebuilt.reconstructed_from_design
                layer.center_offset_bound_mm = rebuilt.center_offset_bound_mm
                layer.rect_width = rebuilt.width
                layer.rect_length = rebuilt.length
                layer.rect_angle_deg = rebuilt.angle_deg

        layer.center = (float(center[0]), float(center[1]))
        centers.append(layer.center)
    return np.asarray(centers, dtype=np.float64)


# ---------------------------------------------------------------------------- 8. 稳健轴线


def _orthogonal_residuals(
    pts: npt.NDArray[np.float64],
    point: npt.NDArray[np.float64],
    direction: npt.NDArray[np.float64],
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


# ---------------------------------------------------------------------------- 9. 垂直度


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
    sor: bool = True,
    sor_k: int = SOR_K,
    sor_std_ratio: float = SOR_STD_RATIO,
    density: float | None = None,
    cover_threshold: float = COVER_THRESHOLD,
    contamination_ratio: float | None = CONTAMINATION_RATIO,
    design_section: float | tuple[float, float] | None = None,
    enable_ransac: bool = True,
    ransac_seed: int = 0,
) -> PlumbResult:
    """串起「SOR → 切片 → 污染剔除 → 中心 → 稳健轴线 → 倾斜率（‰）」，按 4.1.5 出数。

    ``total_height``：设计墩高（m），仅用于"跨度占总高比例"的降级判定；
    不传则用实测 z 跨度（此时跨度判据恒不触发，因为分母同源）。
    """
    cloud = _as_points(points)
    if cloud.shape[0] == 0:
        raise InsufficientGeometryError("点云为空，无法计算垂直度")

    sor_report: SorReport | None = None
    if sor:
        cloud, sor_report = statistical_outlier_removal(
            cloud, k=sor_k, std_ratio=sor_std_ratio, return_report=True
        )
        if cloud.shape[0] == 0:
            raise InsufficientGeometryError("SOR 后点云为空，无法计算垂直度")

    layers = slice_layers(cloud, step=step, min_points=min_points)
    rejected: list[Layer] = []
    if contamination_ratio is not None:
        rejected = reject_contaminated_layers(layers, ratio=contamination_ratio)

    valid = [layer for layer in layers if not layer.skipped]
    skipped = tuple(layer for layer in layers if layer.skipped)
    if not valid:
        raise InsufficientGeometryError(
            f"全部 {len(layers)} 个切片均被跳过（遮挡、污染或点数不足）"
        )
    if len(valid) == 1:
        raise InsufficientGeometryError("有效层数仅 1，无法拟合轴线（至少需要 2 层）")

    centers = extract_centers(
        valid,
        density=density,
        cover_threshold=cover_threshold,
        design_section=design_section,
        ransac_seed=ransac_seed,
        enable_ransac=enable_ransac,
    )
    z_mid = np.asarray([layer.z_mean for layer in valid], dtype=np.float64)
    centers3d = np.column_stack([centers, z_mid])
    axis = fit_robust_axis(centers3d)

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

    ransac_layers = sum(1 for layer in valid if layer.branch == "RANSAC")
    reconstructed_layers = sum(1 for layer in valid if layer.reconstructed_from_design)
    unreconstructed = [
        layer
        for layer in valid
        if layer.branch == "RANSAC" and layer.missing_sides and not layer.reconstructed_from_design
    ]
    fallback = [layer for layer in valid if layer.branch == "MABR(回退)"]
    bounds = [
        layer.center_offset_bound_mm for layer in valid if layer.center_offset_bound_mm is not None
    ]
    bound = max(bounds) if bounds else None
    cover_mean = float(np.mean([layer.cover for layer in valid])) if valid else 0.0
    cover_is_proxy = any(layer.cover_is_proxy for layer in valid)

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
    if rejected:
        reasons.append(f"污染剔除({len(rejected)}层，见 skipped_layers)")
    if unreconstructed:
        reasons.append(
            f"缺边且无设计截面({len(unreconstructed)}层，中心偏移≥"
            f"{CENTER_OFFSET_HINT_MM:.0f}mm 量级，4.1.8)"
        )
    if fallback:
        reasons.append(f"RANSAC 回退 {len(fallback)} 层（同轴双边缺失，中心不可信）")

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
        sor=sor_report,
        layer_centers=centers3d,
        cover_mean=cover_mean,
        cover_is_proxy=cover_is_proxy,
        ransac_layers=ransac_layers,
        reconstructed_layers=reconstructed_layers,
        center_offset_bound_mm=bound,
    )
