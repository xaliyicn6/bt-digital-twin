"""报警点局部精载瓦片生成：几何选择、tileset 口径与幂等性。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from app.services.pointcloud_datasets import DatasetSource
from scripts import build_alarm_tiles as bat

TRANSFORM = [
    -0.86,
    -0.5,
    0.0,
    0.0,
    0.29,
    -0.5,
    0.8,
    0.0,
    -0.4,
    0.69,
    0.58,
    0.0,
    -2610601.3,
    4452759.9,
    3734212.5,
    1.0,
]
LOCAL_CENTER = {"x": 1000.0, "y": 2000.0, "z": -50.0}


def _source(tmp_path: Path) -> DatasetSource:
    return DatasetSource(
        code="tunnel",
        label="隧道",
        las_path=tmp_path / "dummy.las",
        component_code="LNX",
        analysis_type="CLEARANCE",
        decimation_step=30,
        source_points=1000,
        design_model_path=None,
        chunk_length=150.0,
    )


def _write_meta(tmp_path: Path, alarms: list[dict]) -> Path:
    (tmp_path / "tileset.json").write_text(
        json.dumps({"root": {"transform": TRANSFORM}}), encoding="utf-8"
    )
    meta = {
        "localCenter": LOCAL_CENTER,
        "bounds": {
            "min": {"x": 900.0, "y": 1900.0, "z": -60.0},
            "max": {"x": 1100.0, "y": 2100.0, "z": -40.0},
        },
        "alarms": alarms,
    }
    path = tmp_path / "cloud-meta.json"
    path.write_text(json.dumps(meta), encoding="utf-8")
    return path


def test_crop_pipeline_uses_cube_bounds() -> None:
    pipeline = bat.build_crop_pipeline(Path("a.las"), Path("b.ply"), (10, 20, 30), 4.0)
    assert pipeline[0]["type"] == "readers.las"
    assert pipeline[1]["type"] == "filters.crop"
    assert pipeline[1]["bounds"] == "([6.000,14.000],[16.000,24.000],[26.000,34.000])"
    # 不抽稀：pipeline 里不能出现 decimation/thin
    assert all("decimation" not in step["type"] for step in pipeline)


def test_pick_radius_prefers_largest_fitting() -> None:
    distances = np.array([0.5, 1.0, 3.5, 5.5, 7.0])
    assert bat.pick_radius(distances, (3.0, 4.0, 6.0), max_points=2) == (3.0, 2)
    assert bat.pick_radius(distances, (3.0, 4.0), max_points=100) == (4.0, 3)
    # 全部超限时退回最小半径，由调用方再做步长抽稀
    assert bat.pick_radius(distances, (3.0, 4.0), max_points=0)[0] == 3.0


def test_tileset_payload_reuses_transform_and_has_no_rtc_center() -> None:
    points = np.array([[0.0, 0.0, 0.0], [4.0, 6.0, 2.0]])
    payload = bat.alarm_tileset_payload("alarm-1.pnts", TRANSFORM, points)
    root = payload["root"]
    assert root["transform"] == TRANSFORM
    assert root["content"]["uri"] == "alarm-1.pnts"
    assert root["boundingVolume"]["box"][:3] == [2.0, 3.0, 1.0]
    assert "RTC_CENTER" not in json.dumps(payload)
    # 单瓦片没有子节点，必须给大 geometricError 才会无条件加载
    assert root["geometricError"] > 1000


def test_cleanup_stale_removes_only_unexpected(tmp_path: Path) -> None:
    keep = tmp_path / "alarm-1.pnts"
    stale = tmp_path / "alarm-9.pnts"
    stale_ts = tmp_path / "tileset-alarm-9.json"
    for path in (keep, stale, stale_ts):
        path.write_bytes(b"x")
    bat._cleanup_stale(tmp_path, {"alarm-1.pnts"})
    assert keep.is_file()
    assert not stale.exists()
    assert not stale_ts.exists()


def test_build_alarm_tiles_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta_path = _write_meta(
        tmp_path,
        [{"id": "alarm-1", "label": "示例", "x": 1.0, "y": 2.0, "z": -1.0}],
    )
    # 源坐标 = 局部坐标 + localCenter；造 3000 个点，其中 2400 个落在球内
    local = np.random.default_rng(7).normal(scale=2.0, size=(3000, 3))
    local[:2400] = np.random.default_rng(8).uniform(-4.0, 4.0, size=(2400, 3))
    local[:, :2] += np.array([1.0, 2.0])
    local[:, 2] += -1.0
    source_pts = local + np.array(
        [LOCAL_CENTER["x"], LOCAL_CENTER["y"], LOCAL_CENTER["z"]], dtype=np.float64
    )

    monkeypatch.setattr(bat, "run_pdal_pipeline", lambda pipeline: None)
    monkeypatch.setattr(bat, "read_ply", lambda path: (source_pts, None, None))

    records = bat.build_alarm_tiles(
        _source(tmp_path), tmp_path, max_points=2000, radii=(5.0, 4.0, 3.0)
    )
    assert set(records) == {"alarm-1"}
    first = records["alarm-1"]
    assert first["points"] <= 2000
    assert (tmp_path / "alarm-1.pnts").is_file()
    assert (tmp_path / "tileset-alarm-1.json").is_file()

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    alarm = meta["alarms"][0]
    assert alarm["localTile"] == first
    assert alarm["zoneRadiusM"] == first["radiusM"]
    assert "全精度局部瓦片" in meta["alarmsNote"]

    # 再跑一次：产物点数一致（覆盖写），陈旧文件被清掉
    (tmp_path / "alarm-9.pnts").write_bytes(b"stale")
    again = bat.build_alarm_tiles(
        _source(tmp_path), tmp_path, max_points=2000, radii=(5.0, 4.0, 3.0)
    )
    assert again["alarm-1"]["points"] == first["points"]
    assert not (tmp_path / "alarm-9.pnts").exists()


def test_build_alarm_tiles_rejects_missing_meta(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        bat.build_alarm_tiles(_source(tmp_path), tmp_path)
