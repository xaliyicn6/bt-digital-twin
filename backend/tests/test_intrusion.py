"""设计断面侵限判定：已知半径差、凹多边形、自交报错、余量红线与参数化断面。

全部用例不依赖数据库。
"""

from __future__ import annotations

import numpy as np
import pytest
from app.services.geometry import InsufficientGeometryError
from app.services.intrusion import (
    ArcSpec,
    DesignSectionParams,
    build_design_section_polygon,
    check_intrusion,
)


def _circle_points(radius: float, count: int = 720) -> np.ndarray:
    angle = np.linspace(0.0, 2.0 * np.pi, count, endpoint=False)
    return np.column_stack([radius * np.cos(angle), radius * np.sin(angle)])


def _circle_polygon(radius: float, count: int = 720) -> np.ndarray:
    return _circle_points(radius, count)


def _grid(step: float = 0.5, upper: float = 6.0) -> np.ndarray:
    coords = np.arange(step / 2, upper, step)
    xs, ys = np.meshgrid(coords, coords)
    return np.column_stack([xs.ravel(), ys.ravel()])


def test_known_radius_difference_depth_and_area() -> None:
    """设计 R=3.0 m、实测 R=2.9 m：侵限深度≈100 mm，面积≈π(3²-2.9²)=0.911 m²。"""
    design = _circle_polygon(3.0)
    points = _circle_points(2.9)
    result = check_intrusion(points, design, clearance_margin_m=0.0)

    assert result.intrusion_count == points.shape[0]
    assert 98.0 <= result.intrusion_depth_mm <= 102.0
    assert result.intrusion_area_sqm is not None
    assert (
        abs(result.intrusion_area_sqm - np.pi * (3.0**2 - 2.9**2)) < 0.02
    ), result.intrusion_area_sqm
    assert result.area_unavailable_reason is None


def test_concave_polygon_signed_distance() -> None:
    """U 形（凹）多边形：开口区为外（正），两翼与底梁为内（负）。"""
    design = np.array(
        [
            [0.0, 0.0],
            [6.0, 0.0],
            [6.0, 6.0],
            [4.0, 6.0],
            [4.0, 2.0],
            [2.0, 2.0],
            [2.0, 6.0],
            [0.0, 6.0],
        ]
    )
    points = _grid(0.5, 6.0)
    result = check_intrusion(points, design, clearance_margin_m=0.0)

    def distance_at(x: float, y: float) -> float:
        index = int(np.argmin(np.linalg.norm(points - np.array([x, y]), axis=1)))
        return float(result.signed_distance_mm[index])

    assert distance_at(1.0, 1.0) < 0.0  # 左翼内部
    assert distance_at(5.0, 1.0) < 0.0  # 右翼内部
    assert distance_at(3.0, 4.0) > 0.0  # 凹口（实体之外）
    assert distance_at(3.0, 6.5) > 0.0  # 顶部之外


def test_self_intersecting_polygon_raises() -> None:
    """蝴蝶结多边形自交 → ValueError（对应 BT-60004 语义），不能静默给结果。"""
    bowtie = np.array([[0.0, 0.0], [4.0, 4.0], [4.0, 0.0], [0.0, 4.0]])
    with pytest.raises(ValueError, match="设计断面多边形非法"):
        check_intrusion(_grid(0.25, 4.0), bowtie, clearance_margin_m=0.0)


def test_margin_is_included_in_alarm_boundary() -> None:
    """余量计入限界：轮廓外 30 mm 的点在 50 mm 红线内 → 深度≈20 mm。"""
    design = _circle_polygon(3.0)
    points = _circle_points(3.03)
    with_margin = check_intrusion(points, design, clearance_margin_m=0.05)
    without_margin = check_intrusion(points, design, clearance_margin_m=0.0)

    assert with_margin.intrusion_count == points.shape[0]
    assert 18.0 <= with_margin.intrusion_depth_mm <= 22.0
    assert with_margin.clearance_margin_m == 0.05
    assert without_margin.intrusion_count == 0
    assert without_margin.intrusion_depth_mm == 0.0


def test_build_polygon_snaps_floor_by_quadratic_root() -> None:
    """三段圆弧 + 直线底板：底板端点由二次方程求得，且多边形为 CCW、合法。"""
    arc_left = ArcSpec(0.0, 0.0, 3.0, 210.0, 90.0)
    arc_crown = ArcSpec(0.0, 0.0, 3.0, 90.0, 0.0)
    arc_right = ArcSpec(0.0, 0.0, 3.0, 0.0, -90.0)  # 越过底板 → 必须截断到 -30°
    params = DesignSectionParams(
        crown=arc_crown,
        left_haunch=arc_left,
        right_haunch=arc_right,
        invert_v=-1.5,
        clearance_margin_m=0.05,
        step_deg=2.0,
    )
    ring = build_design_section_polygon(params)

    area = 0.5 * float(
        np.sum(ring[:, 0] * np.roll(ring[:, 1], -1) - np.roll(ring[:, 0], -1) * ring[:, 1])
    )
    assert area > 0.0, "多边形必须为逆时针（CCW）"
    assert ring.shape[0] > 20
    # 圆弧越过底板时必须被截断，底部才是直线 v=invert_v
    assert float(ring[:, 1].min()) == pytest.approx(-1.5, abs=1e-9)
    assert float(ring[:, 1].max()) == pytest.approx(3.0, abs=1e-3)
    # 二次方程两根：u = ±sqrt(r² - (invert_v)²) = ±sqrt(9 - 2.25) = ±2.598
    expected = float(np.sqrt(3.0**2 - 1.5**2))
    floor_u = sorted([float(ring[0, 0]), float(ring[-1, 0])])
    assert floor_u[0] == pytest.approx(-expected, abs=1e-6)
    assert floor_u[1] == pytest.approx(expected, abs=1e-6)


def test_build_polygon_rejects_floor_without_intersection() -> None:
    params = DesignSectionParams(
        crown=ArcSpec(0.0, 0.0, 3.0, 90.0, 0.0),
        left_haunch=ArcSpec(0.0, 0.0, 2.0, 180.0, 90.0),
        right_haunch=ArcSpec(0.0, 0.0, 2.0, 0.0, -90.0),
        invert_v=-5.0,  # 底板低于圆底，|dv|=5 > r=2 → 判别式 < 0
    )
    with pytest.raises(ValueError, match="无交点"):
        build_design_section_polygon(params)


def test_insufficient_points_raises() -> None:
    design = _circle_polygon(3.0)
    with pytest.raises(InsufficientGeometryError, match="断面点数不足"):
        check_intrusion(_circle_points(2.9, count=50), design, clearance_margin_m=0.0)
