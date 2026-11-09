"""真实点云数据集 / 设计模型 / 设计轮廓净空口径测试。

这些用例只依赖仓库内的代码与小规模合成数据，不读取业主真实 LAS/DWG；
真实数据缺失环境（CI）也能全绿。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from app.services import geometry_task
from app.services import pointcloud_datasets as pcd
from app.services.pointcloud_datasets import (
    DatasetSource,
    DatasetUnavailableError,
    build_pdal_pipeline,
    get_dataset,
    get_dataset_for_component,
    list_datasets,
    load_design_model,
    min_distance_to_polygon,
)
from app.services.tunnel import analyze_tunnel


def test_config_registers_tunnel_and_bridge_datasets() -> None:
    datasets = list_datasets()
    codes = {d.code for d in datasets}
    assert {"tunnel", "bridge"} <= codes

    tunnel = get_dataset("tunnel")
    assert tunnel is not None
    assert tunnel.component_code == "LNX 轮小区间"
    assert tunnel.analysis_type == "CLEARANCE"
    assert tunnel.decimation_step >= 1

    bridge = get_dataset("bridge")
    assert bridge is not None
    assert bridge.component_code == "BD 梁体（beam_disc）"
    assert bridge.analysis_type == "VERTICALITY"

    # 构件编码 → 数据集 反查两条都要命中
    assert get_dataset_for_component("LNX 轮小区间") is not None
    assert get_dataset_for_component("BD 梁体（beam_disc）") is not None
    assert get_dataset_for_component("PIR 1号桥墩") is None


def test_pdal_pipeline_uses_las_reader_and_decimation(tmp_path: Path) -> None:
    pipeline = build_pdal_pipeline(
        tmp_path / "in.las", tmp_path / "out.ply", step=30, dims="X,Y,Z,Intensity"
    )
    assert pipeline[0]["type"] == "readers.las"
    assert pipeline[1]["type"] == "filters.decimation"
    assert pipeline[1]["step"] == 30
    assert pipeline[2]["type"] == "writers.ply"
    # 二进制小端：ASCII PLY 会丢工程坐标精度
    assert pipeline[2]["storage_mode"] == "binary_little_endian"


def test_min_distance_to_polygon_unit_square() -> None:
    square = np.array([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]])
    pts = np.array([[0.0, 0.0], [2.0, 0.0], [1.0, 0.0]])
    dist = min_distance_to_polygon(pts, square)
    assert dist[0] == pytest.approx(1.0, abs=1e-9)  # 中心到边
    assert dist[1] == pytest.approx(1.0, abs=1e-9)  # 外侧到边
    assert dist[2] == pytest.approx(0.0, abs=1e-9)  # 边界上


def _cylinder_cloud(
    radius: float = 3.0, length: float = 20.0, n: int = 4000, seed: int = 0
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    theta = rng.uniform(0.0, 2.0 * math.pi, n)
    x = rng.uniform(0.0, length, n)
    y = radius * np.cos(theta) + rng.normal(0.0, 0.01, n)
    z = radius * np.sin(theta) + rng.normal(0.0, 0.01, n)
    return np.column_stack([x, y, z])


def test_analyze_tunnel_reports_design_outline_reference() -> None:
    cloud = _cylinder_cloud(radius=3.0, length=20.0)
    # 设计轮廓：边长 6m 的正方形，与点云圆周外切 → 点几乎贴轮廓，
    # 余量 = 距离 - 300mm ≈ -300mm（负值即侵限）
    design = np.array([[-3.0, -3.0], [3.0, -3.0], [3.0, 3.0], [-3.0, 3.0]])
    result = analyze_tunnel(cloud, design_section=design, step=2.0, thickness=1.0)

    assert result.clearance_reference == "DESIGN_OUTLINE"
    assert result.sections, "应至少切出一个断面"
    margins = [s.design_margin_mm for s in result.sections if s.design_margin_mm is not None]
    assert margins, "设计轮廓存在时每片都应有 design_margin_mm"
    worst = min(margins)
    assert -320.0 <= worst <= -200.0, f"设计余量应接近 -300mm，实际 {worst}"


def test_analyze_tunnel_without_design_falls_back_to_fitted_circle() -> None:
    cloud = _cylinder_cloud(radius=3.0, length=20.0)
    result = analyze_tunnel(cloud, step=2.0, thickness=1.0)
    assert result.clearance_reference == "FITTED_CIRCLE"
    assert all(s.design_margin_mm is None for s in result.sections)


def test_design_model_loader(tmp_path: Path) -> None:
    payload = {
        "meta": {"registration": "DESIGN_OUTLINE_FIT", "sectionCount": 2},
        "section": [[-1.0, 0.0], [1.0, 0.0], [1.0, 2.0], [-1.0, 2.0]],
        "rings": [],
    }
    path = tmp_path / "design-model.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    model = load_design_model(path)
    assert model is not None
    assert model.registration == "DESIGN_OUTLINE_FIT"
    assert model.section.shape == (4, 2)

    assert load_design_model(tmp_path / "missing.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text('{"section": [[0, 0], [1, 1]]}', encoding="utf-8")
    assert load_design_model(bad) is None


def test_extract_design_section_from_minimal_dxf(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    from scripts.build_local_datasets import extract_design_section

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_text("A型衬砌断面图", dxfattribs={"insert": (0.0, 0.0), "height": 300})
    # 内层轮廓 10.4m × 5.0m（二衬内轮廓），外层 12.6m 用来验证“取最内层”
    msp.add_lwpolyline([(0, 0), (10400, 0), (10400, 5000), (0, 5000)], close=True)
    msp.add_lwpolyline([(-1100, -1100), (11500, -1100), (11500, 5000), (-1100, 5000)], close=True)
    dxf_path = tmp_path / "05.dxf"
    doc.saveas(dxf_path)

    section = extract_design_section(dxf_path)
    width = float(section[:, 0].max() - section[:, 0].min())
    height = float(section[:, 1].max() - section[:, 1].min())
    assert width == pytest.approx(10.4, abs=0.01)
    assert height == pytest.approx(5.0, abs=0.01)
    # 质心归零
    assert float(np.abs(section.mean(axis=0)).max()) < 1e-9


def test_real_dataset_missing_las_raises_dataset_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = DatasetSource(
        code="tunnel",
        label="隧道（缺失样本）",
        las_path=tmp_path / "missing.las",
        component_code="LNX 轮小区间",
        analysis_type="CLEARANCE",
        decimation_step=30,
        source_points=0,
        design_model_path=None,
    )
    monkeypatch.setattr(geometry_task, "get_dataset_for_component", lambda _code: missing)
    with pytest.raises(DatasetUnavailableError) as exc:
        geometry_task.execute_analysis(
            {"component_code": "LNX 轮小区间", "analysis_type": "CLEARANCE"},
            tmp_path / "unused.ply",
        )
    assert "真实点云缺失" in str(exc.value)
    # PDAL 不可用/文件缺失都属于 BT-30005 的明确失败路径，不允许回退合成点云
    assert pcd.MIN_REAL_POINTS > 0
