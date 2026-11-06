"""M07 核心几何分析测试（设计文档 4.1.2 / 4.1.3 / 4.1.4 / 4.1.5）。

全部用例固定 seed，不用随机通过率。本轮实测（Windows py3.10 + numpy 1.26 + scipy 1.11，
200k 点、SOR k=20/2σ、11 层、10 m 墩）：

    正常工况 seed=42（含 SOR）：倾斜率 5.0623‰、水平偏移 50.873 mm、
    方向夹角误差 0.0052°、残差 RMS 3.170 mm、cover 0.991（代理口径）、
    有效层 11/11、SOR 剔除 3.921%（0.35 s）。

    10-seed 三组（单位 ‰，真值 5.0）：
        无 SOR            mean 4.8424  std 0.4627  max|err| 0.7649
        +SOR              mean 4.9798  std 0.2795  max|err| 0.4795
        +SOR+污染剔除      mean 4.9798  std 0.2795  max|err| 0.4795（干净云不触发剔除）

    任务书要求 max|err| ≤ 0.3‰，实测 0.4795‰ **不成立**；物理极限见
    ``test_ten_seed_bound_03_permille``（xfail strict，附误差分解与所需条件）。

    三面可见（删 -y 面）：无设计尺寸 → 11 层全部走 RANSAC 分支、degraded、
    中心偏移估计 10 mm 量级；有设计尺寸 2 m → 全部补边重建，倾斜率 4.9975‰，
    逐层中心 RMS 4.61 mm，而同一批层直接 MABR 的中心 RMS 103.03 mm（约 22 倍差）。
"""

from __future__ import annotations

import math
import time

import mock_data
import numpy as np
import pytest
from app.services.geometry import (
    InsufficientGeometryError,
    calculate_plumb,
    extract_centers,
    layer_coverage,
    mabr_center,
    min_area_rect,
    minimum_enclosing_circle,
    ransac_circle_fit,
    ransac_rect_center,
    reject_contaminated_layers,
    slice_layers,
    statistical_outlier_removal,
)
from scipy.spatial import cKDTree

TRUE_TILT = 0.005  # m/m → 5‰
TRUE_DIR = np.array([TRUE_TILT, 0.0, 1.0]) / float(np.linalg.norm([TRUE_TILT, 0.0, 1.0]))
TILT_TOL_PERMILLE = 0.5  # 任务书指定（占 5‰ 的 10%）
ANGLE_TOL_DEG = 0.1
OFFSET_TOL_MM = 5.0  # 由倾斜率容差换算：0.5‰ × 10 m = 5 mm
SQUARE = np.array([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]], dtype=np.float64)


def _angle_error(direction: np.ndarray) -> float:
    cos = min(1.0, abs(float(direction @ TRUE_DIR)))
    return float(np.degrees(np.arccos(cos)))


def _only(points: np.ndarray, lo: float, hi: float) -> np.ndarray:
    z = points[:, 2]
    return points[(z >= lo) & (z < hi)]


def _sor_keep_mask(points: np.ndarray, threshold: float, k: int = 20) -> np.ndarray:
    """用报告阈值复算保留掩码（仅测试用，避免为了测试改生产 API 返回值）。"""
    distances, _ = cKDTree(points).query(points, k=min(k, points.shape[0] - 1) + 1, workers=-1)
    mean_distance = distances[:, 1:].mean(axis=1)
    return np.asarray(mean_distance <= threshold, dtype=bool)


# ------------------------------------------------------------------ 10-seed 扫描


@pytest.fixture(scope="session")
def ten_seed_table() -> tuple[np.ndarray, float]:
    """三行对照：无 SOR / +SOR / +SOR+污染剔除。返回 (10×3 倾斜率, 总耗时 s)。"""
    started = time.perf_counter()
    rows: list[tuple[float, float, float]] = []
    for seed in range(10):
        base = mock_data.generate_pier_point_cloud(seed=seed)
        no_sor = calculate_plumb(base, sor=False, contamination_ratio=None)
        with_sor = calculate_plumb(base, sor=True, contamination_ratio=None)
        full = calculate_plumb(base, sor=True, contamination_ratio=1.3)
        rows.append((no_sor.tilt_permille, with_sor.tilt_permille, full.tilt_permille))
    return np.asarray(rows, dtype=np.float64), time.perf_counter() - started


def test_ten_seed_scan_three_rows(ten_seed_table: tuple[np.ndarray, float]) -> None:
    """三行表：打印每层贡献；SOR 必须把 std 降下来，且全流程仍在 0.5‰ 内。"""
    table, elapsed = ten_seed_table
    names = ("无SOR", "+SOR", "+SOR+污染剔除")
    print(f"\n[10-seed] 耗时 {elapsed:.2f}s（10 seeds × 3 配置 × 200k 点）")
    for column, name in enumerate(names):
        values = table[:, column]
        print(
            f"  {name:<14s} mean={values.mean():.4f}‰ std={values.std(ddof=1):.4f}‰ "
            f"max|err|={np.abs(values - 5.0).max():.4f}‰ values={np.round(values, 3)}"
        )
    assert np.abs(table[:, 1] - 5.0).max() <= TILT_TOL_PERMILLE
    # SOR 的贡献：std 从 0.4627‰ 降到 0.2795‰（实测）
    assert table[:, 1].std(ddof=1) < table[:, 0].std(ddof=1)
    # 干净云上污染剔除不应触发，第二、三行必须一致
    assert np.allclose(table[:, 1], table[:, 2], atol=1e-9)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "任务书要求 max|err|<=0.3‰。实测 10-seed 加 SOR 后 std=0.2795‰、max|err|=0.4795‰"
        "（seed 3/9 超出）。物理下界：层中心 σ≈2.0 mm，11 层、10 m 跨度时 "
        "slope_std≈σ/sqrt(Σ(z-z̄)²)≈0.22‰，10 seed 的最大值约 2.4σ≈0.48‰。"
        "要压到 0.3‰ 需 σ_center≤1.1 mm（≈3 倍点数/层），或把层数加到 0.5 m 切片，"
        "不是靠调 SOR 参数或挑 seed 能解决的。"
    ),
)
def test_ten_seed_bound_03_permille(ten_seed_table: tuple[np.ndarray, float]) -> None:
    table, _ = ten_seed_table
    worst = float(np.abs(table[:, 2] - 5.0).max())
    print(f"\n[0.3‰ 断言] 实测 max|err|={worst:.4f}‰")
    assert worst <= 0.3


# ------------------------------------------------------------------ 正常工况


def test_plumb_normal_case() -> None:
    points = mock_data.generate_pier_point_cloud(seed=42)
    result = calculate_plumb(points)
    assert result.sor is not None

    print(
        f"\n[normal] tilt={result.tilt_permille:.4f}‰ "
        f"offset={result.horizontal_offset_mm:.3f}mm "
        f"angle_err={_angle_error(result.axis.direction):.4f}deg "
        f"rms={result.residual_rms_mm:.3f}mm "
        f"cover={result.cover_mean:.3f}(proxy={result.cover_is_proxy}) "
        f"layers={result.valid_layers}/{result.total_layers} "
        f"sor_removed={result.sor.removed_ratio:.3%} sor_time={result.sor.elapsed_s:.2f}s"
    )

    assert abs(result.tilt_permille - 5.0) <= TILT_TOL_PERMILLE
    assert abs(result.horizontal_offset_mm - 50.0) <= OFFSET_TOL_MM
    assert _angle_error(result.axis.direction) < ANGLE_TOL_DEG
    assert result.slope_ratio * 1000.0 == pytest.approx(result.tilt_permille, abs=1e-9)
    assert result.degraded is False
    assert result.degrade_reason is None
    assert result.valid_layers == result.total_layers
    assert result.axis.converged is True
    assert abs(float(result.axis.direction[2])) > 0.99
    # 正常工况走 MABR 分支，不触发 RANSAC；SOR 只剔噪，不误删整层
    assert result.ransac_layers == 0
    assert result.skipped_layers == ()
    assert 0.0 < result.sor.removed_ratio < 0.05
    assert result.cover_mean >= 0.7


# ------------------------------------------------------------------ 遮挡/降级


def test_two_valid_layers_degrades() -> None:
    base = mock_data.generate_pier_point_cloud(seed=42)
    z = base[:, 2]
    pts = base[((z >= 0.0) & (z < 1.0)) | ((z >= 9.0) & (z < 9.95))]
    result = calculate_plumb(pts, sor=False)
    assert result.valid_layers == 2
    assert result.total_layers == 10
    assert result.degraded is True
    assert result.degrade_reason is not None
    assert "有效层数不足(2<3)" in result.degrade_reason


def test_mid_band_occlusion_degrades_by_layer_ratio() -> None:
    """中部大范围缺失：跨度仍占满，缺的是层数 → 命中 4.1.7 的 60% 占比判据。"""
    base = mock_data.generate_pier_point_cloud(seed=42)
    z = base[:, 2]
    pts = base[((z >= 0.0) & (z < 2.0)) | ((z >= 8.0) & (z < 9.95))]
    result = calculate_plumb(pts, sor=False)
    assert result.valid_layers == 4
    assert result.degraded is True
    assert result.degrade_reason is not None
    assert "有效层占比不足(4/" in result.degrade_reason


def test_short_span_degrades_with_design_height() -> None:
    base = mock_data.generate_pier_point_cloud(seed=42)
    pts = _only(base, 0.0, 4.0)
    result = calculate_plumb(pts, sor=False, total_height=10.0)
    assert result.degraded is True
    assert result.degrade_reason is not None
    assert "有效层跨度不足" in result.degrade_reason
    assert result.z_top - result.z_bottom < 6.0


def test_empty_cloud_raises() -> None:
    with pytest.raises(InsufficientGeometryError, match="点云为空"):
        calculate_plumb(np.zeros((0, 3)), sor=False)


def test_all_layers_skipped_raises() -> None:
    sparse = np.array([[0.0, 0.0, z] for z in np.linspace(0.0, 10.0, 10)], dtype=np.float64)
    with pytest.raises(InsufficientGeometryError, match="均被跳过"):
        calculate_plumb(sparse, sor=False)


def test_single_valid_layer_raises() -> None:
    base = mock_data.generate_pier_point_cloud(seed=42)
    pts = _only(base, 0.0, 1.0)
    with pytest.raises(InsufficientGeometryError, match="至少需要 2 层"):
        calculate_plumb(pts, sor=False)


def test_skipped_layers_are_traceable() -> None:
    base = mock_data.generate_pier_point_cloud(seed=42)
    pts = _only(base, 0.0, 1.0)
    layers = slice_layers(pts, step=1.0, min_points=30)
    skipped = [layer for layer in layers if layer.skipped]
    assert len(skipped) == len(layers) - 1
    assert all(layer.skip_reason == "点数不足(0<30)" for layer in skipped)
    kept = [layer for layer in layers if not layer.skipped]
    assert len(kept) == 1 and kept[0].n_points > 1000


# ------------------------------------------------------------------ SOR


def test_sor_plane_lattice_removes_nothing() -> None:
    """无噪声规则格网（内部）→ 剔除 0。任何一个被删都说明距离口径有问题。"""
    grid = np.arange(-1.0, 1.0001, 0.02)
    xx, yy = np.meshgrid(grid, grid)
    plane = np.column_stack([xx.ravel(), yy.ravel(), np.zeros(xx.size)])
    _, report = statistical_outlier_removal(plane, k=20, std_ratio=2.0)
    keep = _sor_keep_mask(plane, report.threshold)
    interior = (np.abs(plane[:, 0]) <= 0.9) & (np.abs(plane[:, 1]) <= 0.9)
    removed_interior = int(np.count_nonzero(~keep[interior]))
    print(
        f"\n[sor-plane] n={plane.shape[0]} interior={interior.sum()} "
        f"removed_interior={removed_interior} global_removed={report.removed_ratio:.4%}"
    )
    assert removed_interior == 0
    # 全局剔除只来自格网物理边界（半径 r 邻域被截断），不应超过 5%
    assert report.removed_ratio < 0.05


def test_sor_gaussian_tail_ratio() -> None:
    """纯高斯点云：2σ 单侧尾部实测约 4.1%，而不是正态假设的 2.3%。"""
    rng = np.random.default_rng(1)
    cloud = rng.normal(0.0, 1.0, (50_000, 3))
    _, report = statistical_outlier_removal(cloud, k=20, std_ratio=2.0)
    print(f"\n[sor-gauss] removed={report.removed_ratio:.3%} threshold={report.threshold:.4f}")
    # kNN 距离是右偏分布（非正态），同一阈值下的实际尾部更厚
    assert 0.02 <= report.removed_ratio <= 0.06


def test_sor_lattice_does_not_kill_corners() -> None:
    """规则格网墩身：只允许物理边界（z=0/10 行）被删，竖向棱边不被系统性剔除。"""
    lattice = mock_data.generate_pier_lattice()
    _, report = statistical_outlier_removal(lattice, k=20, std_ratio=2.0)
    keep = _sor_keep_mask(lattice, report.threshold)
    edge = (np.abs(np.abs(lattice[:, 0]) - 1.0) < 1e-3) | (
        np.abs(np.abs(lattice[:, 1]) - 1.0) < 1e-3
    )
    z_boundary = (lattice[:, 2] < 1e-3) | (lattice[:, 2] > 9.999)
    inner = ~edge & ~z_boundary
    print(
        f"\n[sor-lattice] removed={report.removed_ratio:.4%} "
        f"edge={np.mean(~keep[edge & ~z_boundary]):.4%} inner={np.mean(~keep[inner]):.4%} "
        f"z_boundary={np.mean(~keep[z_boundary]):.4%}"
    )
    assert report.removed_ratio < 0.01
    # 竖向棱边（两垂直面相交）与平面内部的剔除率同量级，不是"边角误杀"
    assert np.mean(~keep[edge & ~z_boundary]) <= 3.0 * np.mean(~keep[inner])
    # z=0/10 边界行失去半空间邻域，全部剔除是可解释的物理边界效应
    assert np.mean(~keep[z_boundary]) > 0.9


def test_sor_report_and_determinism() -> None:
    points = mock_data.generate_pier_point_cloud(seed=7, n=20_000)
    first, report = statistical_outlier_removal(points)
    second, _ = statistical_outlier_removal(points)
    assert first.shape == second.shape
    assert np.array_equal(first, second)
    assert report.input_points == points.shape[0]
    assert report.output_points == first.shape[0]
    assert report.k_effective == 20
    assert report.threshold == pytest.approx(report.mean_distance + 2.0 * report.std_distance)
    assert report.elapsed_s > 0.0


# ------------------------------------------------------------------ 外接圆 / 污染剔除


def test_min_enclosing_circle_units() -> None:
    center, radius = minimum_enclosing_circle(SQUARE)
    assert center == pytest.approx((0.0, 0.0), abs=1e-9)
    assert radius == pytest.approx(math.sqrt(2.0), abs=1e-9)

    center, radius = minimum_enclosing_circle(np.array([[0.5, 0.5]]))
    assert center == pytest.approx((0.5, 0.5))
    assert radius == 0.0

    center, radius = minimum_enclosing_circle(np.array([[0.0, 0.0], [2.0, 0.0]]))
    assert center == pytest.approx((1.0, 0.0))
    assert radius == pytest.approx(1.0)


def test_min_enclosing_circle_is_not_centroid_proxy() -> None:
    """三角形的最小包围圆半径必须等于外接圆半径，质心近似会偏小。"""
    tri = np.array([[0.0, 0.0], [2.0, 0.0], [0.0, 2.0]])
    center, radius = minimum_enclosing_circle(tri)
    assert center == pytest.approx((1.0, 1.0), abs=1e-9)
    assert radius == pytest.approx(math.sqrt(2.0), abs=1e-9)


def test_contaminated_layer_rejected() -> None:
    """单层混入远处点：外接圆半径超邻层中位数 1.3 倍 → 剔除并给可追溯原因。"""
    base = mock_data.generate_pier_point_cloud(seed=42)
    dirty = np.vstack([base, np.array([[2.6, 2.6, 3.5]])])
    result = calculate_plumb(dirty, sor=False)
    rejected = [layer for layer in result.skipped_layers if layer.skip_reason]
    assert len(rejected) == 1
    reason = rejected[0].skip_reason or ""
    print(
        f"\n[contam] skipped={rejected[0].index} "
        f"radius={rejected[0].mec_radius:.3f} reason={reason}"
    )
    assert "污染剔除(半径" in reason and "1.30倍" in reason
    assert result.degraded is True
    assert result.degrade_reason is not None and "污染剔除" in result.degrade_reason
    assert abs(result.tilt_permille - 5.0) <= TILT_TOL_PERMILLE


def test_demo_cloud_fixed_seed_is_deterministic_and_l2() -> None:
    """演示点云：固定种子 → 重复调用完全一致；4.5‰ 由 calculate_plumb 真实算出（非硬编码）。"""
    first = mock_data.generate_pier_point_cloud(
        seed=mock_data.DEFAULT_SEED, tilt_ratio=mock_data.DEMO_TILT_RATIO
    )
    second = mock_data.generate_pier_point_cloud(
        seed=mock_data.DEFAULT_SEED, tilt_ratio=mock_data.DEMO_TILT_RATIO
    )
    assert np.array_equal(first, second)
    result = calculate_plumb(first)
    print(
        f"\n[demo] seed={mock_data.DEFAULT_SEED} tilt={result.tilt_permille:.4f}‰ "
        f"offset={result.horizontal_offset_mm:.3f}mm"
    )
    # 实测固定值约 4.562‰（容差仅覆盖浮点/依赖版本差异）
    assert result.tilt_permille == pytest.approx(4.562, abs=0.01)
    assert result.degraded is False

    from app.services.defect_levels import load_thresholds

    assert load_thresholds().suggest(result.tilt_permille) == "L2"


def test_clean_cloud_has_no_contamination() -> None:
    points = mock_data.generate_pier_point_cloud(seed=42)
    result = calculate_plumb(points)
    assert result.skipped_layers == ()
    assert all("污染剔除" not in (layer.skip_reason or "") for layer in result.skipped_layers)


def test_contamination_neighbor_rule_first_last() -> None:
    """首/末层只有单侧邻层：用可用的那一侧（各 2 层）中位数，且不误判正常层。"""
    points = mock_data.generate_pier_point_cloud(seed=42, n=60_000)
    layers = slice_layers(points)
    rejected = reject_contaminated_layers(layers, ratio=1.3, neighbor_span=2, min_neighbors=2)
    assert rejected == []
    radii = [layer.mec_radius for layer in layers if layer.mec_radius > 0]
    assert len(radii) == len(layers)
    assert max(radii) / min(radii) < 1.3


# ------------------------------------------------------------------ RANSAC 四边


def test_cover_proxy_detects_missing_face() -> None:
    """整面缺失：文档公式口径可能仍 >0.7，四边占空比代理能识别（0.15）。"""
    base = mock_data.generate_pier_point_cloud(seed=42)
    three = mock_data.remove_face(base, axis="y", sign=-1)
    filtered, _ = statistical_outlier_removal(three)
    layers = slice_layers(filtered)
    valid = [layer for layer in layers if not layer.skipped]
    layer = valid[len(valid) // 2]
    rect = min_area_rect(layer.points)
    cover, occupancy, is_proxy = layer_coverage(layer.points, rect)
    print(f"\n[cover] cover={cover:.3f} occupancy={occupancy:.3f} proxy={is_proxy} rect={rect}")
    assert is_proxy is True
    assert cover < 0.7
    assert occupancy < 0.7


def test_cover_density_branch_uses_document_formula() -> None:
    """传入 density 时 cover 是 4.1.3 原文公式，不再标记代理。"""
    points = mock_data.generate_pier_point_cloud(seed=42, n=60_000)
    layers = slice_layers(points)
    layer = [item for item in layers if not item.skipped][0]
    rect = min_area_rect(layer.points)
    thickness = layer.z_data_max - layer.z_data_min
    cover, _, is_proxy = layer_coverage(layer.points, rect, density=2000.0, thickness=thickness)
    expected = layer.n_points / (2000.0 * 2.0 * (rect.width + rect.length) * thickness)
    assert is_proxy is False
    assert cover == pytest.approx(expected, rel=1e-9)


def test_three_face_without_design_degrades() -> None:
    base = mock_data.generate_pier_point_cloud(seed=42)
    three = mock_data.remove_face(base, axis="y", sign=-1)
    result = calculate_plumb(three, design_section=None)
    print(
        f"\n[three-face/no-design] tilt={result.tilt_permille:.3f}‰ cover={result.cover_mean:.3f} "
        f"ransac={result.ransac_layers}/{result.valid_layers} bound={result.center_offset_bound_mm}"
    )
    assert result.ransac_layers == result.valid_layers
    assert result.degraded is True
    assert result.degrade_reason is not None
    assert "缺边且无设计截面" in result.degrade_reason
    assert result.center_offset_bound_mm == pytest.approx(10.0)
    # 没有设计尺寸时结果明显不可信（实测 51.66‰），不能当成正常出数
    assert abs(result.tilt_permille - 5.0) > 10.0


def test_three_face_with_design_recovers_center() -> None:
    base = mock_data.generate_pier_point_cloud(seed=42)
    three = mock_data.remove_face(base, axis="y", sign=-1)
    result = calculate_plumb(three, design_section=2.0)
    print(
        f"\n[three-face/design] tilt={result.tilt_permille:.4f}‰ cover={result.cover_mean:.3f} "
        f"ransac={result.ransac_layers} recon={result.reconstructed_layers} "
        f"degraded={result.degraded}"
    )
    assert result.degraded is False
    assert result.degrade_reason is None
    assert result.ransac_layers == result.valid_layers
    assert result.reconstructed_layers == result.valid_layers
    assert abs(result.tilt_permille - 5.0) <= TILT_TOL_PERMILLE

    filtered, _ = statistical_outlier_removal(three)
    layers = slice_layers(filtered)
    valid = [layer for layer in layers if not layer.skipped]
    centers_ransac = extract_centers(valid, design_section=2.0)
    centers_mabr = np.asarray([min_area_rect(layer.points).center for layer in valid])
    z_mean = np.asarray([layer.z_mean for layer in valid])
    truth = np.column_stack([TRUE_TILT * z_mean, np.zeros_like(z_mean)])
    rms_ransac = float(np.sqrt(np.mean(np.sum((centers_ransac - truth) ** 2, axis=1))))
    rms_mabr = float(np.sqrt(np.mean(np.sum((centers_mabr - truth) ** 2, axis=1))))
    print(f"[three-face] center rms RANSAC={rms_ransac * 1000:.2f}mm MABR={rms_mabr * 1000:.2f}mm")
    assert rms_ransac * 1000.0 < 10.0
    assert rms_ransac < 0.2 * rms_mabr


def test_ransac_is_deterministic() -> None:
    base = mock_data.generate_pier_point_cloud(seed=42)
    three = mock_data.remove_face(base, axis="y", sign=-1)
    layers = slice_layers(three)
    layer = [item for item in layers if not item.skipped][3]
    first = ransac_rect_center(layer.points, design_section=2.0, seed=11)
    second = ransac_rect_center(layer.points, design_section=2.0, seed=11)
    assert first.center == pytest.approx(second.center)
    assert first.width == pytest.approx(second.width)
    assert first.length == pytest.approx(second.length)
    assert first.missing_sides == second.missing_sides


# ------------------------------------------------------------------ 稳健性


def test_outliers_do_not_break_tilt() -> None:
    """6–8 m 两个测站范围掺 5% 粗差（占全云 1.064%）。关闭 SOR 以单独验证 IRLS。"""
    base = mock_data.generate_pier_point_cloud(seed=42)
    band = (base[:, 2] >= 6.0) & (base[:, 2] < 8.0)
    dirty = mock_data.add_outliers(base[band], ratio=0.05, magnitude=0.15, seed=7)
    pts = np.vstack([base[~band], dirty])
    displaced = int(np.count_nonzero(np.any(dirty != base[band], axis=1)))

    result = calculate_plumb(pts, sor=False)
    print(
        f"\n[outliers] 替换点数={displaced}（占 {displaced / pts.shape[0]:.3%}） "
        f"tilt={result.tilt_permille:.4f}‰ rms={result.residual_rms_mm:.3f}mm "
        f"inliers={result.axis.inliers}/{result.valid_layers}"
    )
    assert abs(result.tilt_permille - 5.0) <= TILT_TOL_PERMILLE
    assert result.axis.inliers < result.valid_layers
    assert result.residual_rms_mm > 8.0


# ------------------------------------------------------------------ MABR 单元级


def test_mabr_axis_aligned_square() -> None:
    rect = min_area_rect(SQUARE)
    assert abs(rect.center[0]) < 1e-9
    assert abs(rect.center[1]) < 1e-9
    assert rect.length == pytest.approx(2.0, abs=1e-9)
    assert rect.width == pytest.approx(2.0, abs=1e-9)
    assert rect.area == pytest.approx(4.0, abs=1e-9)
    assert mabr_center(SQUARE) == pytest.approx((0.0, 0.0), abs=1e-9)


def test_mabr_rotated_square() -> None:
    theta = np.radians(30.0)
    rot = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    pts = SQUARE @ rot.T
    rect = min_area_rect(pts)
    assert mabr_center(pts) == pytest.approx((0.0, 0.0), abs=1e-9)
    assert rect.width == pytest.approx(2.0, abs=1e-9)
    assert rect.length == pytest.approx(2.0, abs=1e-9)
    assert rect.angle_deg == pytest.approx(30.0, abs=1e-6)


def test_mabr_2x4_at_20deg() -> None:
    theta = np.radians(20.0)
    rot = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    pts = np.array([[-2.0, -1.0], [2.0, -1.0], [2.0, 1.0], [-2.0, 1.0]]) @ rot.T
    rect = min_area_rect(pts)
    assert rect.width == pytest.approx(2.0, abs=1e-6)
    assert rect.length == pytest.approx(4.0, abs=1e-6)
    assert rect.angle_deg == pytest.approx(20.0, abs=1e-6)
    assert rect.area == pytest.approx(8.0, abs=1e-6)


def test_mabr_matches_bruteforce_area() -> None:
    rng = np.random.default_rng(11)
    angles = np.sort(rng.uniform(0.0, 2.0 * np.pi, 9))
    radii = rng.uniform(1.0, 3.0, 9) * np.array([1.0, 1.0, 0.8, 0.8, 0.9, 0.9, 1.1, 1.1, 1.0])
    poly = np.column_stack([radii * np.cos(angles), radii * np.sin(angles)])
    calipers = min_area_rect(poly).area

    best = float("inf")
    for deg in np.arange(0.0, 90.0, 0.01):
        rad = np.radians(deg)
        u = np.array([np.cos(rad), np.sin(rad)])
        v = np.array([-u[1], u[0]])
        pu = poly @ u
        pv = poly @ v
        best = min(best, float((pu.max() - pu.min()) * (pv.max() - pv.min())))
    assert calipers <= best + 1e-9
    assert abs(calipers - best) < 1e-3


def test_mabr_degenerate_inputs() -> None:
    two = np.array([[0.0, 0.0], [2.0, 0.0]])
    assert mabr_center(two) == pytest.approx((1.0, 0.0))
    collinear = np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]])
    assert mabr_center(collinear) == pytest.approx((1.0, 1.0))
    duplicates = np.array([[1.0, 1.0], [1.0, 1.0], [1.0, 1.0], [1.0, 1.0]])
    assert mabr_center(duplicates) == pytest.approx((1.0, 1.0))
    single = np.array([[0.5, 0.5]])
    rect = min_area_rect(single)
    assert rect.center == (0.5, 0.5)
    assert rect.area == 0.0


# ------------------------------------------------------------------ 切片边界


def test_slice_boundary_goes_to_upper_layer() -> None:
    pts = np.array(
        [[0.0, 0.0, 0.0]] * 40 + [[0.0, 0.0, 1.0]] * 40 + [[0.0, 0.0, 2.0]] * 40,
        dtype=np.float64,
    )
    layers = slice_layers(pts, step=1.0, min_points=1)
    assert [layer.n_points for layer in layers] == [40, 40, 40]
    assert sum(layer.n_points for layer in layers) == pts.shape[0]


def test_slice_non_integer_z_extent() -> None:
    rng = np.random.default_rng(0)
    z = rng.uniform(0.0, 10.5, 400)
    pts = np.column_stack([rng.uniform(-1, 1, 400), rng.uniform(-1, 1, 400), z])
    layers = slice_layers(pts, step=1.0, min_points=1)
    assert len(layers) == 11
    last = layers[-1]
    assert last.z_max - last.z_min == pytest.approx(1.0)
    assert last.z_data_max - last.z_data_min < 1.0
    assert last.n_points < layers[0].n_points


# ----------------------------------------------------------- 圆弧 RANSAC（M07.2 隧道断面）

CIRCLE_TRUE_RADIUS = 3.0
CIRCLE_TOL_M = 0.05  # 任务书：圆心误差 < 5 cm、半径误差 < 5 cm


def _circle_errors(pts: np.ndarray, seed: int) -> tuple[float, float, float]:
    fit = ransac_circle_fit(pts[:, 1:3], inlier_band=0.05, iterations=300, seed=seed)
    center_err = math.hypot(fit.center[0], fit.center[1])
    radius_err = abs(fit.radius - CIRCLE_TRUE_RADIUS)
    return center_err, radius_err, fit.inlier_ratio


def test_ransac_circle_fit_synthetic_tunnel_section() -> None:
    """半径 3 m 半圆 + 拱顶缺口 + 6% 电缆沟 + 2 cm 高斯噪声，不读任何外部文件。"""
    pts = mock_data.generate_tunnel_section_synthetic(n=120_000, seed=0)
    dc, dr, ratio = _circle_errors(pts, seed=0)
    print(
        f"\n[圆弧RANSAC] seed=0  center_err={dc * 1000:.3f} mm  "
        f"radius_err={dr * 1000:.3f} mm  inlier_ratio={ratio:.4f}"
    )
    assert dc < CIRCLE_TOL_M, f"圆心误差 {dc:.4f} m 超 5 cm"
    assert dr < CIRCLE_TOL_M, f"半径误差 {dr:.4f} m 超 5 cm"
    assert 0.5 <= ratio <= 1.0


def test_ransac_circle_fit_five_seeds_max_error_within_5cm() -> None:
    """5 个 seed 全部断言 max 误差 ≤ 5 cm（不挑 seed），并把分布打印出来。"""
    rows = []
    for seed in range(5):
        pts = mock_data.generate_tunnel_section_synthetic(n=120_000, seed=seed)
        dc, dr, ratio = _circle_errors(pts, seed)
        rows.append((seed, dc, dr, ratio))
        print(
            f"[圆弧RANSAC] seed={seed}  center_err={dc * 1000:.3f} mm  "
            f"radius_err={dr * 1000:.3f} mm  inlier_ratio={ratio:.4f}"
        )
    worst_center = max(r[1] for r in rows)
    worst_radius = max(r[2] for r in rows)
    mean_center = float(np.mean([r[1] for r in rows]))
    mean_radius = float(np.mean([r[2] for r in rows]))
    print(
        f"[圆弧RANSAC] 5 seed  mean_center={mean_center * 1000:.3f} mm  "
        f"mean_radius={mean_radius * 1000:.3f} mm  "
        f"max_center={worst_center * 1000:.3f} mm  max_radius={worst_radius * 1000:.3f} mm"
    )
    assert worst_center <= CIRCLE_TOL_M
    assert worst_radius <= CIRCLE_TOL_M


def test_ransac_circle_fit_rejects_collinear() -> None:
    collinear = np.column_stack([np.linspace(0.0, 1.0, 10), np.linspace(0.0, 1.0, 10)])
    with pytest.raises(InsufficientGeometryError):
        ransac_circle_fit(collinear, iterations=50, seed=0)
