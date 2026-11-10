"""build_potree.py 的纯函数单测：不依赖 PotreeConverter 二进制与真实 LAS。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_potree.py"


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_potree", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_command_uses_potree2_options(tmp_path: Path) -> None:
    mod = load_module()
    cmd = mod.build_command(
        converter=tmp_path / "PotreeConverter.exe",
        source=tmp_path / "a.las",
        outdir=tmp_path / "out",
    )
    assert cmd[0].endswith("PotreeConverter.exe")
    assert cmd[1].endswith("a.las")
    assert cmd[2:4] == ["-o", str(tmp_path / "out")]
    assert "--encoding" in cmd
    # 1.7 的 -s / -l / --store-size 在 2.x 中不存在，必须不出现
    for legacy in ("-s", "-l", "--store-size"):
        assert legacy not in cmd


def test_build_command_appends_aabb_only_when_given(tmp_path: Path) -> None:
    mod = load_module()
    aabb = "0 0 0 10 20 30"
    cmd = mod.build_command(tmp_path / "c.exe", tmp_path / "a.las", tmp_path / "o", aabb=aabb)
    assert cmd[-2:] == ["--aabb", aabb]


def test_summarize_reads_metadata_and_counts_bins(tmp_path: Path) -> None:
    mod = load_module()
    outdir = tmp_path / "tunnel"
    outdir.mkdir()
    meta = {
        "version": "2.0",
        "points": 1234,
        "octreeDir": "data",
        "spacing": 0.05,
        "scale": [0.001, 0.001, 0.001],
        "boundingBox": {"min": [0, 0, 0], "max": [1, 1, 1]},
    }
    (outdir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    (outdir / "octree.bin").write_bytes(b"\0" * 16)
    (outdir / "hierarchy.bin").write_bytes(b"\0" * 8)

    info = mod.summarize(outdir)
    assert info["version"] == "2.0"
    assert info["points"] == 1234
    assert info["nodeFiles"] == 2
    assert info["bytes"] == 24 + (outdir / "metadata.json").stat().st_size
    assert info["hierarchy"] == ["hierarchy.bin"]
