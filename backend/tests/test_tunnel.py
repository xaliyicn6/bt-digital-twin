"""M07.2 隧道断面测试：合成兜底（CI 永远绿）+ 真实点云探测（缺失时 skip 但打印路径）。

坐标约定（合成数据）：轴向 ``+x``，断面 ``u=y``、``v=z``，半径 3 m 的半圆 + 拱顶缺口
+ 6% 电缆沟凸起 + 2 cm 高斯噪声（见 ``mock_data.generate_tunnel_section_synthetic``）。

真实数据路径解析（本文件的关键点）：
用户给的判断路径是字面量 ``backend/tests/data/tunnel_sample.ply``。pytest 在
``backend/`` 下执行时，该相对路径会解析成 ``backend/backend/tests/...``，永远命中不了，
真实用例会被**静默跳过**（假绿）。因此这里以 ``Path(__file__).resolve().parents[1]``
（= ``backend/``）为准，同时接受仓库根相对的 ``backend/tests/data/...``，并在模块导入
时无条件打印两条候选路径与是否存在（``pytest -s`` 直接可见）。
"""

from __future__ import annotations

import math
from pathlib import Path

import mock_data
import numpy as np
import pytest
from app.services.geometry import InsufficientGeometryError
from app.services.tunnel import (
    analyze_tunnel,
    estimate_tunnel_axis,
    load_tunnel_ply,
    section_basis,
    voxel_downsample,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
TUNNEL_PLY_PRIMARY = BACKEND_DIR / "tests" / "data" / "tunnel_sample.ply"
TUNNEL_PLY_FALLBACK = REPO_ROOT / "backend" / "tests" / "data" / "tunnel_sample.ply"
TUNNEL_PLY = TUNNEL_PLY_PRIMARY if TUNNEL_PLY_PRIMARY.exists() else TUNNEL_PLY_FALLBACK

print(
    f"[tunnel] 候选路径 1（backend 相对）= {TUNNEL_PLY_PRIMARY} 存在={TUNNEL_PLY_PRIMARY.exists()}"
)
print(
    f"[tunnel] 候选路径 2（仓库根相对）= {TUNNEL_PLY_FALLBACK} 存在={TUNNEL_PLY_FALLBACK.exists()}"
)
print(f"[tunnel] 实际采用路径 = {TUNNEL_PLY} 存在={TUNNEL_PLY.exists()}")

SYNTHETIC_RADIUS = 3.0
SYNTHETIC_TOL_M = 0.05


def _write_ascii_ply(path: Path, rows: list[tuple[float, float, float, float]]) -> Path:
    """最小 ASCII PLY：x/y/z + 一个非坐标属性 intensity，用于验证"跳过其余 property"。"""
    lines = [
        "ply",
        "format ascii 1.0",
        "comment synthetic fixture for tunnel parser",
        f"element vertex {len(rows)}",
        "property float x",
        "property float y",
        "property float z",
        "property float intensity",
        "element face 0",
        "property list uchar int vertex_indices",
        "end_header",
    ]
    lines += [f"{x!r} {y!r} {z!r} {i!r}" for x, y, z, i in rows]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def test_tunnel_real_data_path_probe() -> None:
    """无论真实文件在不在都打印解析结果；这条用例永不 skip，避免"假绿"。"""
    print(f"[tunnel] probe: primary={TUNNEL_PLY_PRIMARY} exists={TUNNEL_PLY_PRIMARY.exists()}")
    print(f"[tunnel] probe: fallback={TUNNEL_PLY_FALLBACK} exists={TUNNEL_PLY_FALLBACK.exists()}")
    assert TUNNEL_PLY.is_absolute()
    assert Path(__file__).resolve().parent == (BACKEND_DIR / "tests")


def test_load_tunnel_ply_ascii_skips_extra_properties(tmp_path: Path) -> None:
    rows = [
        (0.0, 1.0, 2.0, 7.0),
        (float("nan"), 4.0, 5.0, 8.0),
        (6.0, float("inf"), 8.0, 9.0),
        (9.0, 10.0, 11.0, 10.0),
    ]
    path = _write_ascii_ply(tmp_path / "sample_ascii.ply", rows)
    cloud, report = load_tunnel_ply(path, voxel_size=100.0, return_report=True)
    assert report.ply_format == "ascii"
    assert report.vertex_count == 4
    assert report.valid_count == 2
    assert report.dropped_non_finite == 2
    assert cloud.shape == (1, 3)  # 体素 100 m：两个有效点并成一个质心
    assert cloud[0] == pytest.approx([(0.0 + 9.0) / 2.0, (1.0 + 10.0) / 2.0, (2.0 + 11.0) / 2.0])


def test_load_tunnel_ply_binary_little_endian(tmp_path: Path) -> None:
    dtype = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("intensity", "<u2")])
    records = np.array([(0.0, 0.0, 0.0, 7), (1.0, 1.0, 1.0, 9), (2.0, 2.0, 2.0, 11)], dtype=dtype)
    path = tmp_path / "sample_bin.ply"
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {records.shape[0]}\n"
        "property float x\nproperty float y\nproperty float z\nproperty ushort intensity\n"
        "end_header\n"
    )
    with path.open("wb") as fh:
        fh.write(header.encode("ascii"))
        records.tofile(fh)

    cloud, report = load_tunnel_ply(path, voxel_size=0.001, return_report=True)
    assert report.ply_format == "binary_little_endian"
    assert report.vertex_count == 3
    assert report.dropped_non_finite == 0
    assert cloud.shape == (3, 3)
    np.testing.assert_allclose(cloud, np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [2.0, 2.0, 2.0]]))


def test_load_tunnel_ply_big_endian_raises(tmp_path: Path) -> None:
    path = tmp_path / "big_endian.ply"
    path.write_text(
        "ply\nformat binary_big_endian 1.0\nelement vertex 1\n"
        "property float x\nproperty float y\nproperty float z\nend_header\n",
        encoding="ascii",
    )
    with pytest.raises(ValueError, match="binary_big_endian"):
        load_tunnel_ply(path)


def test_load_tunnel_ply_all_non_finite_raises(tmp_path: Path) -> None:
    rows = [(float("nan"), 0.0, 0.0, 1.0), (0.0, float("inf"), 0.0, 2.0)]
    path = _write_ascii_ply(tmp_path / "nan_only.ply", rows)
    with pytest.raises(InsufficientGeometryError):
        load_tunnel_ply(path)


def test_load_tunnel_ply_zero_vertex_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.ply"
    path.write_text(
        "ply\nformat ascii 1.0\nelement vertex 0\n"
        "property float x\nproperty float y\nproperty float z\nend_header\n",
        encoding="ascii",
    )
    with pytest.raises(InsufficientGeometryError):
        load_tunnel_ply(path)


def test_voxel_downsample_reports_and_bounds() -> None:
    rng = np.random.default_rng(0)
    pts = rng.uniform(-1.0, 1.0, (10_000, 3))
    down, report = voxel_downsample(pts, voxel_size=0.2)
    assert report.input_points == 10_000
    assert 0 < report.output_points < 10_000
    assert 0.0 < report.ratio < 1.0
    assert float(down.min()) >= -1.0
    assert float(down.max()) <= 1.0


def test_section_basis_is_orthonormal_and_not_hardcoded_yz() -> None:
    axis = np.array([0.3, 0.9, 0.05])
    axis = axis / np.linalg.norm(axis)
    e1, e2 = section_basis((float(axis[0]), float(axis[1]), float(axis[2])))
    b1, b2 = np.asarray(e1), np.asarray(e2)
    assert abs(float(b1 @ axis)) < 1e-12
    assert abs(float(b2 @ axis)) < 1e-12
    assert abs(float(b1 @ b2)) < 1e-12
    assert abs(float(np.linalg.norm(b1)) - 1.0) < 1e-12
    assert abs(float(np.linalg.norm(b2)) - 1.0) < 1e-12
    # 斜置轴：断面基不应等于世界 y/z（说明没有硬编码 Y-Z）
    assert not np.allclose(np.abs(b1), np.array([0.0, 1.0, 0.0]), atol=1e-6)


def test_analyze_tunnel_synthetic_sections() -> None:
    pts = mock_data.generate_tunnel_section_synthetic(n=120_000, seed=0)
    result = analyze_tunnel(pts, seed=0)
    print(
        f"\n[合成隧道] axis=({result.axis[0]:.6f}, {result.axis[1]:.6f}, {result.axis[2]:.6f}) "
        f"总切片={result.total_slices} 有效={len(result.sections)} 抽样后点数={result.points}"
    )
    for section in result.sections[:3]:
        print(
            f"[合成隧道] 切片 {section.index} t={section.t_center:.3f} m "
            f"r={section.radius:.3f} m center=({section.center_u:+.3f}, {section.center_v:+.3f}) "
            f"inlier_ratio={section.inlier_ratio:.3f} "
            f"min_clearance={section.min_clearance_mm:.3f} mm"
        )
    assert abs(result.axis[0]) > 0.99
    assert result.total_slices >= 5
    assert len(result.sections) >= 5
    assert result.skipped_slices == result.total_slices - len(result.sections)
    for section in result.sections:
        assert abs(section.radius - SYNTHETIC_RADIUS) < SYNTHETIC_TOL_M
        center_error = math.hypot(section.center_u, section.center_v)
        assert center_error < SYNTHETIC_TOL_M
        assert math.isfinite(section.min_clearance_mm)
        assert section.min_clearance_mm >= 0.0
        assert 0.5 <= section.inlier_ratio <= 1.0


@pytest.mark.skipif(
    not TUNNEL_PLY.exists(),
    reason=f"真实点云缺失（实际解析到的绝对路径：{TUNNEL_PLY}）",
)
def test_tunnel_real_cloud_sections() -> None:
    """真实点云（WHU-TLS 11-Tunnel 抽样转出的 binary_little_endian PLY）。

    断言只做健壮性检查（不卡毫米）：有效切片 ≥ 5、中心落在数据自身包围盒内、
    净空为有限非负值、内点率在合理区间。
    """
    cloud, report = load_tunnel_ply(TUNNEL_PLY, voxel_size=0.2, return_report=True)
    print(
        f"\n[真实隧道] 文件={report.path} 格式={report.ply_format} "
        f"原始点数={report.vertex_count} 有效点数={report.valid_count} "
        f"剔除非有限={report.dropped_non_finite} 抽稀后={report.downsampled_count} "
        f"抽稀保留率={report.downsample_ratio:.4f} voxel={report.voxel_size} m"
    )
    # min_points=200：真云里含洞口外地形，稀疏切片的"圆弧"只是零星点凑出来的，
    # 不是断面。用点数门槛把有效切片限到真正有断面支撑的那些（不是几何阈值）。
    result = analyze_tunnel(cloud, min_points=200)
    print(
        f"[真实隧道] axis=({result.axis[0]:.6f}, {result.axis[1]:.6f}, {result.axis[2]:.6f}) "
        f"总切片={result.total_slices} 有效切片={len(result.sections)} "
        f"跳过={result.skipped_slices} 点云={result.points}"
    )
    for section in result.sections[:3]:
        print(
            f"[真实隧道] 切片 {section.index} t={section.t_center:.3f} m "
            f"r={section.radius:.3f} m center=({section.center_u:+.3f}, {section.center_v:+.3f}) "
            f"inlier_ratio={section.inlier_ratio:.3f} "
            f"min_clearance={section.min_clearance_mm:.3f} mm 点数={section.n_points}"
        )

    assert report.vertex_count > 0
    assert len(result.sections) >= 5, f"有效切片只有 {len(result.sections)} 个，少于 5"

    e1, e2 = section_basis(result.axis)
    b1, b2 = np.asarray(e1), np.asarray(e2)
    u = cloud @ b1
    v = cloud @ b2
    u_lo, u_hi = float(u.min()), float(u.max())
    v_lo, v_hi = float(v.min()), float(v.max())
    for section in result.sections:
        assert u_lo <= section.center_u <= u_hi
        assert v_lo <= section.center_v <= v_hi
        assert math.isfinite(section.min_clearance_mm)
        assert section.min_clearance_mm >= 0.0
        # 真实隧道是马蹄形断面，不是圆：拟合圆只覆盖部分拱圈，内点率天然低于合成用例。
        assert 0.2 <= section.inlier_ratio <= 1.0


def test_estimate_tunnel_axis_synthetic() -> None:
    pts = mock_data.generate_tunnel_section_synthetic(n=20_000, seed=1)
    axis = estimate_tunnel_axis(pts)
    assert abs(axis[0]) > 0.99
