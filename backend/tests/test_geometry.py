"""M07 核心几何分析测试（设计文档 4.1.3 / 4.1.4 / 4.1.5）。

全部用例固定 seed，不用随机通过率。本轮实测裕度（Windows py3.10 + numpy 1.26 + scipy 1.11）：

    正常工况 seed=42：倾斜率 4.9317‰（真值 5‰，误差 0.068‰，容差 0.5‰）
    轴线方向夹角误差 0.0102°（容差 0.1°，实测裕度约 10 倍）
    水平偏移 49.850 mm（真值 50 mm，容差 5 mm）
    残差 RMS 5.774 mm，有效层 11/11，遮挡率 5.9%

瓶颈定位（实测，见回答）：噪声是唯一来源。σ=20 mm、每层约 2 万点时，凸包极值抖动
使单层中心抖动 std_x=4.17 mm / std_y=3.51 mm，换算到约 10 m 高柱的斜率标准差约
0.45‰；遮挡（无偏随机丢弃 30%）对倾斜率的影响只有 0.003‰。所以 ±0.5‰ 容差
（约 1.1σ）偏薄：seed=42 恰好落在 0.15σ 内，其余 seed 可能超差。
"""

from __future__ import annotations

import mock_data
import numpy as np
import pytest
from app.services.geometry import (
    InsufficientGeometryError,
    calculate_plumb,
    mabr_center,
    min_area_rect,
    slice_layers,
)

TRUE_TILT = 0.005  # m/m → 5‰
TRUE_DIR = np.array([TRUE_TILT, 0.0, 1.0]) / float(np.linalg.norm([TRUE_TILT, 0.0, 1.0]))
TILT_TOL_PERMILLE = 0.5  # 任务书指定（占 5‰ 的 10%）
ANGLE_TOL_DEG = 0.1
OFFSET_TOL_MM = 5.0  # 由倾斜率容差换算：0.5‰ × 10 m = 5 mm，同一条公式的另一种单位表达


def _angle_error(direction: np.ndarray) -> float:
    cos = min(1.0, abs(float(direction @ TRUE_DIR)))
    return float(np.degrees(np.arccos(cos)))


# ------------------------------------------------------------------ 正常工况


def test_plumb_normal_case() -> None:
    points = mock_data.generate_pier_point_cloud(seed=42)
    result = calculate_plumb(points)

    print(
        f"\n[normal] tilt={result.tilt_permille:.4f}‰ "
        f"offset={result.horizontal_offset_mm:.3f}mm "
        f"angle_err={_angle_error(result.axis.direction):.4f}deg "
        f"rms={result.residual_rms_mm:.3f}mm "
        f"layers={result.valid_layers}/{result.total_layers} occ={result.occlusion_ratio:.4f}"
    )

    assert abs(result.tilt_permille - 5.0) <= TILT_TOL_PERMILLE
    assert abs(result.horizontal_offset_mm - 50.0) <= OFFSET_TOL_MM
    assert _angle_error(result.axis.direction) < ANGLE_TOL_DEG
    # i = slope_ratio × 1000，两者必须自洽（文档 4.1.5 的两种单位表达）
    assert result.slope_ratio * 1000.0 == pytest.approx(result.tilt_permille, abs=1e-9)
    assert result.degraded is False
    assert result.degrade_reason is None
    assert result.valid_layers == result.total_layers
    assert result.axis.converged is True
    # 轴线是"方向"而非有向射线，允许整体取反，但必须竖直（|d_z| 接近 1）
    assert abs(float(result.axis.direction[2])) > 0.99


# ------------------------------------------------------------------ 遮挡/降级


def _only(points: np.ndarray, lo: float, hi: float, lo2: float | None = None) -> np.ndarray:
    z = points[:, 2]
    mask = (z >= lo) & (z < hi)
    if lo2 is not None:
        mask = mask | (z >= lo2)
    return points[mask]


def test_two_valid_layers_degrades() -> None:
    base = mock_data.generate_pier_point_cloud(seed=42)
    z = base[:, 2]
    pts = base[((z >= 0.0) & (z < 1.0)) | ((z >= 9.0) & (z < 9.95))]
    result = calculate_plumb(pts)
    assert result.valid_layers == 2
    assert result.total_layers == 10
    assert result.degraded is True
    assert result.degrade_reason is not None
    assert "有效层数不足(2<3)" in result.degrade_reason


def test_mid_band_occlusion_degrades_by_layer_ratio() -> None:
    """中部大范围缺失：跨度仍占满，缺的是层数 → 命中 4.1.7 的 60% 占比判据。

    注意：这是"中部遮挡"的真实后果。中段挖空不会缩短 z_b→z_t 跨度（首末层还在），
    所以命中的是"有效层占比不足"，不是"跨度不足"；跨度判据见下一条用例。
    """
    base = mock_data.generate_pier_point_cloud(seed=42)
    z = base[:, 2]
    pts = base[((z >= 0.0) & (z < 2.0)) | ((z >= 8.0) & (z < 9.95))]
    result = calculate_plumb(pts)
    assert result.valid_layers == 4
    assert result.degraded is True
    assert result.degrade_reason is not None
    assert "有效层占比不足(4/" in result.degrade_reason


def test_short_span_degrades_with_design_height() -> None:
    """只有底部 4 m 有点：需要调用方给设计墩高，跨度判据才有意义。"""
    base = mock_data.generate_pier_point_cloud(seed=42)
    pts = _only(base, 0.0, 4.0)
    result = calculate_plumb(pts, total_height=10.0)
    assert result.degraded is True
    assert result.degrade_reason is not None
    assert "有效层跨度不足" in result.degrade_reason
    assert result.z_top - result.z_bottom < 6.0


def test_empty_cloud_raises() -> None:
    with pytest.raises(InsufficientGeometryError, match="点云为空"):
        calculate_plumb(np.zeros((0, 3)))


def test_all_layers_skipped_raises() -> None:
    sparse = np.array([[0.0, 0.0, z] for z in np.linspace(0.0, 10.0, 10)], dtype=np.float64)
    with pytest.raises(InsufficientGeometryError, match="均被跳过"):
        calculate_plumb(sparse)


def test_single_valid_layer_raises() -> None:
    base = mock_data.generate_pier_point_cloud(seed=42)
    pts = _only(base, 0.0, 1.0)
    with pytest.raises(InsufficientGeometryError, match="至少需要 2 层"):
        calculate_plumb(pts)


def test_skipped_layers_are_traceable() -> None:
    """低于阈值的层不静默丢弃：保留在列表里，带原因。"""
    base = mock_data.generate_pier_point_cloud(seed=42)
    pts = _only(base, 0.0, 1.0)  # 只有 1 层有点
    layers = slice_layers(pts, step=1.0, min_points=30)
    skipped = [layer for layer in layers if layer.skipped]
    assert len(skipped) == len(layers) - 1
    assert all(layer.skip_reason == "点数不足(0<30)" for layer in skipped)
    kept = [layer for layer in layers if not layer.skipped]
    assert len(kept) == 1 and kept[0].n_points > 1000


# ------------------------------------------------------------------ 稳健性


def test_outliers_do_not_break_tilt() -> None:
    """约 1% 点位于 6–8 m 两个测站范围内被粗差污染（模拟单站配准漂移）。

    若把 1% 粗差均匀撒在整根柱上，每一层的凸包都会被抬起（4.1.3 明确 RECT 分支
    对离群点无鲁棒性），此时容差会被冲破；集中在少数层才是 IRLS 能处理的情形。
    """
    base = mock_data.generate_pier_point_cloud(seed=42)
    band = (base[:, 2] >= 6.0) & (base[:, 2] < 8.0)
    dirty = mock_data.add_outliers(base[band], ratio=0.05, magnitude=0.15, seed=7)
    pts = np.vstack([base[~band], dirty])
    displaced = int(np.count_nonzero(np.any(dirty != base[band], axis=1)))

    result = calculate_plumb(pts)
    print(
        f"\n[outliers] 替换点数={displaced}（占 {displaced / pts.shape[0]:.3%}） "
        f"tilt={result.tilt_permille:.4f}‰ rms={result.residual_rms_mm:.3f}mm "
        f"inliers={result.axis.inliers}/{result.valid_layers}"
    )
    assert abs(result.tilt_permille - 5.0) <= TILT_TOL_PERMILLE
    # 内点数少于总层数，说明 Huber 权重真的降了权（否则等于普通最小二乘）
    assert result.axis.inliers < result.valid_layers
    # 残差被粗差抬高，但倾斜率仍在容差内
    assert result.residual_rms_mm > 8.0


# ------------------------------------------------------------------ MABR 单元级

SQUARE = np.array([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]], dtype=np.float64)


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
    """卡壳结果不劣于角度扫描（等价标准实现的正确性校验）。"""
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
    assert calipers <= best + 1e-9  # 卡壳取边方向，不会劣于 0.01° 网格
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
    """z 恰好落在边界上：下闭上开 → 归入上一层，不会被两层重复计数。"""
    pts = np.array(
        [[0.0, 0.0, 0.0]] * 40 + [[0.0, 0.0, 1.0]] * 40 + [[0.0, 0.0, 2.0]] * 40,
        dtype=np.float64,
    )
    layers = slice_layers(pts, step=1.0, min_points=1)
    assert [layer.n_points for layer in layers] == [40, 40, 40]
    assert sum(layer.n_points for layer in layers) == pts.shape[0]


def test_slice_non_integer_z_extent() -> None:
    """z 范围不是 1 m 整数倍：末层只覆盖半层，层边界与实测极值要分开记录。"""
    rng = np.random.default_rng(0)
    z = rng.uniform(0.0, 10.5, 400)
    pts = np.column_stack([rng.uniform(-1, 1, 400), rng.uniform(-1, 1, 400), z])
    layers = slice_layers(pts, step=1.0, min_points=1)
    assert len(layers) == 11
    last = layers[-1]
    assert last.z_max - last.z_min == pytest.approx(1.0)
    assert last.z_data_max - last.z_data_min < 1.0  # 实测覆盖不足一整层
    assert last.n_points < layers[0].n_points
