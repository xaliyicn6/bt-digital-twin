"""隧道断面几何分析（M07.2）：PLY 读取、体素抽稀、沿轴切片、圆弧 RANSAC + 净空参考。

数据与口径声明
==============

1. **数据来源**：本轮的真实点云是 WHU-TLS 11-Tunnel。仓库里**原本没有**
   ``backend/tests/data/tunnel_sample.ply``；实际存在的是
   ``backend/tests/tunnel_sample.las``（LAS 1.2 / point format 3，22,418,039 点，
   727 MiB）。测试用的 ``tunnel_sample.ply`` 是**由该 LAS 按 stride=40 抽样转出的
   二进制 PLY**（560,451 点，binary_little_endian，≈6.4 MiB），头注释里带
   ``source_file`` / ``source_points`` / ``sampled_stride`` 三个 comment 行，
   可追溯到原始 LAS。它属于真实数据派生样本，按 AGENTS.md §6 与 ``.gitignore``
   的 ``data/`` 规则**不入库**；文件缺失时真实用例 skip（并打印解析到的绝对路径）。
   本模块只解析 PLY。
2. **圆定义的取舍**（与"最大内切圆"区分）：用户原话"半径最大、且 95% 点在圆内"
   若逐字实现是不可判定的——半径足够大时所有点都在圆内，最优解为 r→∞。本模块采用
   唯一可判定的读法：**支持点最多的圆弧**（内点 = 到圆周距离 ≤ ``inlier_band``），
   再按残差绝对值 95% 分位裁剪（并以 ``inlier_band`` 为上限，理由见
   ``ransac_circle_fit``）做最小二乘精修。要"包住 95% 点的最小覆盖圆"请另用
   ``minimum_enclosing_circle`` 的分位变体，那是覆盖问题，不是本函数。
3. **净空口径**：``min_clearance_mm`` = 该切片点到**拟合圆弧**的最小距离（mm，3 位
   小数，估算值）。它是"相对拟合圆弧的净空参考"，**不是**规范意义上的建筑限界
   侵限值。传入 ``design_section`` 时额外给出 ``design_margin_mm``（点到设计轮廓
   距离 − 0.3 m 安全余量，负值即侵限）；该设计轮廓只做了"水平按线路轴对位 +
   竖向以点云断面中心拟合"，**未做控制点校核**，仍属示例口径，不能当真值参与验收。
4. **坐标系**：轴向量只表示**方向**、没有起终点；按投影参数 ``t`` 排序切片。断面
   坐标用与轴正交的局部基 ``(e1, e2)``，不硬编码 Y-Z（缓坡/斜置隧道上硬投 Y-Z
   会把断面拉斜）。
5. **切片厚度**：沿轴每 ``step`` 米取一层，法向厚度 ``thickness``（默认 0.5 m），
   两个参数都可配。

PLY 解析为纯 numpy 实现（无 ``plyfile`` 依赖）：支持 ``format ascii 1.0`` 与
``format binary_little_endian 1.0``，只取 x/y/z，跳过其余 scalar property 与后续
element（如 face）；``binary_big_endian`` 明确报错，不静默错读。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal, overload

import numpy as np
import numpy.typing as npt

from app.services.geometry import InsufficientGeometryError, ransac_circle_fit
from app.services.pointcloud_datasets import penetration_inside_polygon

VOXEL_SIZE = 0.2
SECTION_STEP = 1.0
SECTION_THICKNESS = 0.5
INLIER_BAND = 0.05
MIN_SECTION_POINTS = 30

_PLY_SCALAR_TYPES = {
    "char": "i1",
    "int8": "i1",
    "uchar": "u1",
    "uint8": "u1",
    "short": "i2",
    "int16": "i2",
    "ushort": "u2",
    "uint16": "u2",
    "int": "i4",
    "int32": "i4",
    "uint": "u4",
    "uint32": "u4",
    "float": "f4",
    "float32": "f4",
    "double": "f8",
    "float64": "f8",
}


@dataclass(frozen=True)
class PlyLoadReport:
    """PLY 读取与抽稀的可追溯统计。

    ``downsample_ratio`` 是体素抽稀的**保留率** = ``downsampled_count / valid_count``。
    """

    path: str
    ply_format: str
    vertex_count: int
    valid_count: int
    dropped_non_finite: int
    downsampled_count: int
    downsample_ratio: float
    voxel_size: float
    comments: tuple[str, ...]


@dataclass(frozen=True)
class VoxelReport:
    """体素抽稀统计。``ratio`` 是**保留率** = output/input（不是剔除率）。"""

    input_points: int
    output_points: int
    ratio: float
    voxel_size: float


@dataclass(frozen=True)
class TunnelSection:
    """一个轴向切片的圆弧拟合结果（``center_u/v`` 在局部基下，单位 m）。"""

    index: int
    t_center: float
    n_points: int
    center_u: float
    center_v: float
    radius: float
    inlier_ratio: float
    min_clearance_mm: float
    # 相对设计轮廓的余量（mm）：点到设计轮廓距离 − 安全余量；负值即侵限。
    # 只有传入 design_section 时才有值。
    design_margin_mm: float | None = None


@dataclass(frozen=True)
class TunnelResult:
    axis: tuple[float, float, float]
    sections: tuple[TunnelSection, ...]
    total_slices: int
    skipped_slices: int
    skipped_reasons: tuple[str, ...]
    points: int
    # 净空参照来源：DESIGN_OUTLINE（设计轮廓，示意拟合）或 FITTED_CIRCLE（拟合圆弧）
    clearance_reference: str = "FITTED_CIRCLE"


def _read_ply_header(
    fh: IO[bytes],
) -> tuple[str, int, np.dtype[np.void], tuple[str, ...]]:
    """读 PLY 头部，返回 (format, vertex 数, vertex 记录 dtype, comment 行)。

    只解析 ``element vertex`` 的 scalar property；``property list`` 与后续 element
    （face 等）一律跳过。未知标量类型直接报错，避免用错误宽度错读二进制。
    """
    ply_format = ""
    vertex_count = 0
    saw_vertex = False
    current_element = ""
    fields: list[tuple[str, str]] = []
    comments: list[str] = []

    while True:
        raw = fh.readline()
        if not raw:
            raise ValueError("PLY 头未以 end_header 结束（文件可能截断）")
        line = raw.decode("ascii", errors="strict").strip()
        if not line:
            continue
        parts = line.split()
        keyword = parts[0]
        if keyword == "ply":
            continue
        if keyword == "format":
            ply_format = parts[1]
        elif keyword == "comment":
            comments.append(line[len("comment ") :] if len(line) > 7 else "")
        elif keyword == "element":
            current_element = parts[1]
            if current_element == "vertex":
                saw_vertex = True
                vertex_count = int(parts[2])
        elif keyword == "property" and current_element == "vertex":
            if parts[1] == "list":
                raise ValueError("vertex element 含 list property，本解析器不支持")
            name = parts[2]
            if parts[1] not in _PLY_SCALAR_TYPES:
                raise ValueError(f"未知 PLY property 类型：{parts[1]}")
            fields.append((name, _PLY_SCALAR_TYPES[parts[1]]))
        elif keyword == "end_header":
            break

    if not ply_format:
        raise ValueError("PLY 头缺少 format 行")
    if ply_format == "binary_big_endian":
        raise ValueError("不支持 binary_big_endian PLY（只支持 ascii / binary_little_endian）")
    if ply_format not in ("ascii", "binary_little_endian"):
        raise ValueError(f"未知 PLY format：{ply_format}")
    if not saw_vertex:
        raise ValueError("PLY 缺少 element vertex 声明")
    if vertex_count <= 0:
        raise InsufficientGeometryError("PLY 无顶点（element vertex 0）")
    if not fields:
        raise ValueError("PLY vertex 未声明任何 property")

    dtype = np.dtype([(name, f"<{code}") for name, code in fields])
    return ply_format, vertex_count, dtype, tuple(comments)


def _ply_xyz(
    fh: IO[bytes], ply_format: str, vertex_count: int, dtype: np.dtype[np.void]
) -> npt.NDArray[np.float64]:
    raw_names = list(dtype.names or ())
    lower = [name.lower() for name in raw_names]
    if not {"x", "y", "z"} <= set(lower):
        raise ValueError(f"PLY vertex 缺少 x/y/z property，实际为 {lower}")
    columns = (lower.index("x"), lower.index("y"), lower.index("z"))

    if ply_format == "ascii":
        data = np.loadtxt(fh, max_rows=vertex_count, usecols=columns, dtype=np.float64)
        return np.ascontiguousarray(np.atleast_2d(data), dtype=np.float64)

    records = np.fromfile(fh, dtype=dtype, count=vertex_count)
    if records.shape[0] != vertex_count:
        raise ValueError(f"PLY 二进制记录不完整：声明 {vertex_count}，实读 {records.shape[0]}")
    xyz = np.column_stack([records[raw_names[i]] for i in columns])
    return np.ascontiguousarray(xyz, dtype=np.float64)


def voxel_downsample(
    points: npt.ArrayLike, voxel_size: float = VOXEL_SIZE
) -> tuple[npt.NDArray[np.float64], VoxelReport]:
    """体素质心抽稀：``floor(p / voxel)`` → 唯一化 → 每体素取质心，O(n log n)。"""
    if voxel_size <= 0.0:
        raise ValueError("voxel_size 必须为正")
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError(f"点云必须是 (n, 3) 数组，当前形状 {pts.shape}")
    if pts.shape[0] == 0:
        return np.zeros((0, 3), dtype=np.float64), VoxelReport(0, 0, 0.0, voxel_size)

    ijk = np.floor(pts / float(voxel_size)).astype(np.int64)
    origin = ijk.min(axis=0)
    extent = ijk.max(axis=0) - origin + 1
    key = (
        (ijk[:, 0] - origin[0]) * int(extent[1]) * int(extent[2])
        + (ijk[:, 1] - origin[1]) * int(extent[2])
        + (ijk[:, 2] - origin[2])
    )
    _, inverse = np.unique(key, return_inverse=True)
    inverse = np.asarray(inverse, dtype=np.int64)
    counts = np.bincount(inverse, minlength=int(inverse.max()) + 1).astype(np.float64)

    centroids = np.empty((counts.size, 3), dtype=np.float64)
    for dim in range(3):
        centroids[:, dim] = (
            np.bincount(inverse, weights=pts[:, dim], minlength=counts.size) / counts
        )

    report = VoxelReport(
        input_points=int(pts.shape[0]),
        output_points=int(centroids.shape[0]),
        ratio=float(centroids.shape[0]) / float(pts.shape[0]),
        voxel_size=float(voxel_size),
    )
    return centroids, report


@overload
def load_tunnel_ply(
    filepath: str | Path,
    voxel_size: float = VOXEL_SIZE,
    *,
    return_report: Literal[True],
) -> tuple[npt.NDArray[np.float64], PlyLoadReport]: ...


@overload
def load_tunnel_ply(
    filepath: str | Path,
    voxel_size: float = VOXEL_SIZE,
    *,
    return_report: Literal[False] = False,
) -> npt.NDArray[np.float64]: ...


def load_tunnel_ply(
    filepath: str | Path,
    voxel_size: float = VOXEL_SIZE,
    *,
    return_report: bool = False,
) -> tuple[npt.NDArray[np.float64], PlyLoadReport] | npt.NDArray[np.float64]:
    """读取隧道 PLY（ascii / binary_little_endian），剔除非有限值后体素抽稀。

    返回 (N, 3) float64；``return_report=True`` 时额外返回 :class:`PlyLoadReport`
    （含原始顶点数、剔除的 NaN/Inf 条数、抽稀后点数与比例、comment 行）。
    空文件或过滤后为空抛 :class:`InsufficientGeometryError`。
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"PLY 不存在：{path}")

    with path.open("rb") as fh:
        ply_format, vertex_count, dtype, comments = _read_ply_header(fh)
        raw = _ply_xyz(fh, ply_format, vertex_count, dtype)

    finite = np.all(np.isfinite(raw), axis=1)
    dropped = int(raw.shape[0] - int(np.count_nonzero(finite)))
    valid = np.ascontiguousarray(raw[finite], dtype=np.float64)
    if valid.shape[0] == 0:
        raise InsufficientGeometryError(f"PLY 无有效点（{path}）")

    downsampled, voxel_report = voxel_downsample(valid, voxel_size)
    report = PlyLoadReport(
        path=str(path),
        ply_format=ply_format,
        vertex_count=int(vertex_count),
        valid_count=int(valid.shape[0]),
        dropped_non_finite=dropped,
        downsampled_count=int(downsampled.shape[0]),
        downsample_ratio=voxel_report.ratio,
        voxel_size=float(voxel_size),
        comments=comments,
    )
    return (downsampled, report) if return_report else downsampled


def estimate_tunnel_axis(points: npt.ArrayLike) -> tuple[float, float, float]:
    """PCA 第一主成分作走向。**只有方向、没有起终点**，按投影参数 t 排序切片。"""
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError(f"点云必须是 (n, 3) 数组，当前形状 {pts.shape}")
    if pts.shape[0] < 3:
        raise InsufficientGeometryError("点云点数不足，无法估计走向")
    centered = pts - pts.mean(axis=0)
    values, vectors = np.linalg.eigh(np.cov(centered, rowvar=False))
    axis = vectors[:, int(np.argmax(values))]
    norm = float(np.linalg.norm(axis))
    if norm <= 0.0:
        raise InsufficientGeometryError("点云协方差退化，无法估计走向")
    axis = axis / norm
    dominant = int(np.argmax(np.abs(axis)))
    if axis[dominant] < 0.0:
        axis = -axis
    return (float(axis[0]), float(axis[1]), float(axis[2]))


def section_basis(
    axis: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """构造与轴正交的断面基 ``(e1, e2)``（e2 尽量竖直），替代硬编码 Y-Z。"""
    a = np.asarray(axis, dtype=np.float64)
    a = a / float(np.linalg.norm(a))
    reference = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    if abs(float(a @ reference)) > 0.9:  # 轴近乎竖直，换参考向量避免叉积退化
        reference = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    e1 = np.cross(reference, a)
    e1 = e1 / float(np.linalg.norm(e1))
    e2 = np.cross(a, e1)
    return (float(e1[0]), float(e1[1]), float(e1[2])), (
        float(e2[0]),
        float(e2[1]),
        float(e2[2]),
    )


def _slice_arrays(
    points: npt.NDArray[np.float64],
    axis: tuple[float, float, float],
    e1: tuple[float, float, float],
    e2: tuple[float, float, float],
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """把点云投影到轴向参数 t 与断面基 (u, v)，并按 t 升序排列。"""
    a = np.asarray(axis, dtype=np.float64)
    b1 = np.asarray(e1, dtype=np.float64)
    b2 = np.asarray(e2, dtype=np.float64)
    t = points @ a
    order = np.argsort(t)
    t_sorted = t[order]
    uv = np.column_stack([points[order] @ b1, points[order] @ b2])
    return t_sorted, uv


def analyze_tunnel(
    points: npt.ArrayLike,
    *,
    step: float = SECTION_STEP,
    thickness: float = SECTION_THICKNESS,
    inlier_band: float = INLIER_BAND,
    iterations: int = 300,
    seed: int = 0,
    min_points: int = MIN_SECTION_POINTS,
    max_sections: int | None = None,
    design_section: npt.ArrayLike | None = None,
    safety_margin_mm: float = 300.0,
) -> TunnelResult:
    """沿 PCA 主方向切片并逐片做圆弧 RANSAC，返回每片中心/半径/内点率/净空参考。

    ``max_sections`` 只用于把切片数均匀限流（调试/长隧道时用），不改变切片口径。
    每片的 ``min_clearance_mm`` 是"相对拟合圆弧"的净空参考（mm，估算），
    不是建筑限界侵限值。

    传入 ``design_section``（断面局部坐标 (u, v)，单位米，已按轮廓质心归零）时，
    额外按"水平对位线路轴（u=0）+ 竖向以点云断面中心拟合（v=切片中位数）"的
    方式叠放设计轮廓，给出 ``design_margin_mm = 点到轮廓距离 − safety_margin_mm``。
    该设计轮廓未做控制点校核，结果面板必须标注为示意口径。
    """
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError(f"点云必须是 (n, 3) 数组，当前形状 {pts.shape}")
    if pts.shape[0] == 0:
        raise InsufficientGeometryError("点云为空")
    if step <= 0.0 or thickness <= 0.0:
        raise ValueError("step/thickness 必须为正")

    design_poly = (
        np.asarray(design_section, dtype=np.float64) if design_section is not None else None
    )
    if design_poly is not None and (
        design_poly.ndim != 2 or design_poly.shape[0] < 3 or design_poly.shape[1] != 2
    ):
        raise ValueError("design_section 必须是 (M>=3, 2) 的断面轮廓多边形")

    axis = estimate_tunnel_axis(pts)
    e1, e2 = section_basis(axis)
    t_sorted, uv = _slice_arrays(pts, axis, e1, e2)

    t0, t1 = float(t_sorted[0]), float(t_sorted[-1])
    centers = np.arange(t0 + step / 2.0, t1, step)
    if max_sections is not None and centers.size > int(max_sections):
        pick = np.linspace(0, centers.size - 1, int(max_sections)).round().astype(int)
        centers = centers[np.unique(pick)]

    half = thickness / 2.0
    sections: list[TunnelSection] = []
    reasons: list[str] = []
    for index, center_t in enumerate(centers):
        lo = int(np.searchsorted(t_sorted, center_t - half, side="left"))
        hi = int(np.searchsorted(t_sorted, center_t + half, side="right"))
        n_slice = hi - lo
        if n_slice < min_points:
            reasons.append(f"切片 {index}(t={center_t:.2f}m) 点数不足({n_slice}<{min_points})")
            continue
        uv_slice = uv[lo:hi]
        try:
            fit = ransac_circle_fit(
                uv_slice, inlier_band=inlier_band, iterations=iterations, seed=seed
            )
        except InsufficientGeometryError as exc:
            reasons.append(f"切片 {index}(t={center_t:.2f}m) 拟合失败：{exc}")
            continue
        distance = np.abs(
            np.hypot(uv_slice[:, 0] - fit.center[0], uv_slice[:, 1] - fit.center[1]) - fit.radius
        )

        design_margin: float | None = None
        if design_poly is not None:
            # 只拿"主导衬砌面"点比设计轮廓：切片里混着另一孔、路面与周边山体，
            # 全量点会让侵入深度虚高数米；RANSAC 拟合圆的内点才是本断面衬砌。
            keep = distance <= inlier_band
            ref_uv = uv_slice[keep] if int(keep.sum()) >= min_points else uv_slice
            # 断面基 (e1, e2) 是绝对点积，u/v 带着整条线路的量级（数万米）；
            # 设计轮廓是质心归零的局部坐标，必须先归零再比距离。
            # 归零基准用拟合圆心（几何中心），不能用坐标中位数：圆环的
            # 单坐标中位数受弧段分布影响可达 0.5m，会把侵入深度抬高到 1.4m 量级。
            shifted = ref_uv - fit.center
            # 只看"侵入设计内轮廓的深度"：衬砌表面本来就贴着轮廓，
            # 取到轮廓的最小距离会恒等于 0（余量恒为 -安全值），没有工程含义。
            _, penetration = penetration_inside_polygon(shifted, design_poly)
            design_margin = safety_margin_mm - float(np.max(penetration)) * 1000.0

        sections.append(
            TunnelSection(
                index=index,
                t_center=float(center_t),
                n_points=int(n_slice),
                center_u=float(fit.center[0]),
                center_v=float(fit.center[1]),
                radius=float(fit.radius),
                inlier_ratio=float(fit.inlier_ratio),
                min_clearance_mm=float(np.min(distance)) * 1000.0,
                design_margin_mm=design_margin,
            )
        )

    return TunnelResult(
        axis=axis,
        sections=tuple(sections),
        total_slices=int(centers.size),
        skipped_slices=int(len(reasons)),
        skipped_reasons=tuple(reasons),
        points=int(pts.shape[0]),
        clearance_reference="DESIGN_OUTLINE" if design_poly is not None else "FITTED_CIRCLE",
    )
