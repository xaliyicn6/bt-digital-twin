"""报警点周边高精度局部瓦片（从原始 LAS 全精度抽取）。

为什么需要这个脚本：
全局瓦片为了首屏体积做了 30:1 抽稀（3063 万 → 102 万点），进入就地漫游后
近处点间距被放大，看起来“不清晰”。这里按报警点位置从原始 LAS 直接裁一个
小球范围，不做全局抽稀，只在这个小范围里保留全精度点，单块控制在 20 万点内。

口径（与主瓦片严格一致）：
- 局部坐标 = 源工程坐标 − cloud-meta.json 的 ``localCenter``；
- tileset 根 ``transform`` 直接复用主 ``tileset.json`` 的 ``root.transform``
  （ENU→ECEF），因此局部瓦片与全局分块落在同一位置；
- **不写 RTC_CENTER**：POSITION 已是同一套 ENU 局部坐标，二者叠加会漂移。

幂等：每次都覆盖同名产物，并清理本轮没有生成的旧 ``alarm-*.pnts`` /
``tileset-alarm-*.json``，重复执行结果一致。产物在 .gitignore 覆盖的
``frontend/public/tiles/local/<dataset>/`` 下，不入库。

运行：
    python backend/scripts/build_alarm_tiles.py --dataset tunnel
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(SCRIPT_DIR))

from app.services.pointcloud_datasets import (  # noqa: E402
    DatasetSource,
    get_dataset,
    run_pdal_pipeline,
)
from generate_3dtiles import (  # noqa: E402
    _normalize01,
    read_ply,
    synthesize_intensity,
    synthesize_rgb,
    write_pnts,
)

# 单块上限：20 万点 ≈ 6.2 MB（POSITION 12B + RGB 3B + BatchTable 16B / 点）
MAX_LOCAL_POINTS = 200_000
# 候选半径（米），从大到小挑第一个不超过点数上限的
DEFAULT_RADII: tuple[float, ...] = (6.0, 5.5, 5.0, 4.5, 4.0, 3.5, 3.0)
# 从 LAS 裁剪时的探针半径（略大于最大候选半径，多取一点再按球筛）
PROBE_RADIUS_M = 6.5
DEFAULT_OUT_DIR = REPO_ROOT / "frontend" / "public" / "tiles" / "local" / "tunnel"


def build_crop_pipeline(
    las_path: Path,
    ply_path: Path,
    center: Sequence[float],
    radius: float,
) -> list[dict[str, Any]]:
    """LAS → 立方体裁剪 → PLY（不抽稀）。纯函数，便于单测。

    用 ``filters.crop`` 的 bounds 形式（PDAL 各版本都支持），
    球面筛选在 numpy 里做，避免依赖 point/distance 语法差异。
    """
    x, y, z = (float(v) for v in center)
    r = float(radius)
    bounds = (
        f"([{x - r:.3f},{x + r:.3f}]," f"[{y - r:.3f},{y + r:.3f}]," f"[{z - r:.3f},{z + r:.3f}])"
    )
    return [
        {"type": "readers.las", "filename": str(las_path)},
        {"type": "filters.crop", "bounds": bounds},
        {
            "type": "writers.ply",
            "filename": str(ply_path),
            "dims": "X,Y,Z,Intensity",
            "storage_mode": "binary_little_endian",
        },
    ]


def pick_radius(
    distances: np.ndarray,
    radii: Sequence[float],
    max_points: int,
) -> tuple[float, int]:
    """从大到小选第一个点数不超过上限的半径；都超限时返回最小半径。"""
    ordered = sorted((float(r) for r in radii), reverse=True)
    for radius in ordered:
        count = int(np.count_nonzero(distances <= radius))
        if count <= max_points:
            return radius, count
    smallest = ordered[-1]
    return smallest, int(np.count_nonzero(distances <= smallest))


def alarm_tileset_payload(
    pnts_uri: str,
    transform: Sequence[float],
    local_points: np.ndarray,
) -> dict[str, Any]:
    """单瓦片 tileset.json：复用主瓦片的 root.transform，包围盒用局部 ENU box。"""
    lo = local_points.min(axis=0)
    hi = local_points.max(axis=0)
    center = (lo + hi) / 2.0
    half = (hi - lo) / 2.0 + 0.5
    # geometricError 给大值：单瓦片没有子节点，任何视角都要直接加载
    giant = 10_000_000.0
    return {
        "asset": {"version": "1.0", "gltfUpAxis": "Y"},
        "geometricError": giant,
        "root": {
            "transform": [float(v) for v in transform],
            "geometricError": giant,
            "boundingVolume": {
                "box": [
                    round(float(center[0]), 3),
                    round(float(center[1]), 3),
                    round(float(center[2]), 3),
                    round(float(half[0]), 3),
                    0.0,
                    0.0,
                    0.0,
                    round(float(half[1]), 3),
                    0.0,
                    0.0,
                    0.0,
                    round(float(half[2]), 3),
                ]
            },
            "content": {"uri": pnts_uri},
            "refine": "ADD",
        },
        "extensionsUsed": [],
        "extensionsRequired": [],
    }


def _prepare_attributes(
    pts: np.ndarray,
    rgb: np.ndarray | None,
    intensity: np.ndarray | None,
    y_span: tuple[float, float] | None,
) -> tuple[np.ndarray, np.ndarray]:
    """补齐强度/颜色，口径与主瓦片一致（含沿隧道轴向的正弦相位）。"""
    if intensity is None:
        intensity = synthesize_intensity(pts, axis_span=y_span)
    else:
        intensity = _normalize01(intensity)
        if float(intensity.max() - intensity.min()) < 1e-6:
            intensity = synthesize_intensity(pts, axis_span=y_span)
    if rgb is None:
        rgb = synthesize_rgb(intensity)
    return rgb, intensity


def _cleanup_stale(out_dir: Path, keep: set[str]) -> None:
    for pattern in ("alarm-*.pnts", "tileset-alarm-*.json"):
        for path in out_dir.glob(pattern):
            if path.name not in keep:
                path.unlink()


def build_alarm_tiles(
    source: DatasetSource,
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    max_points: int = MAX_LOCAL_POINTS,
    radii: Sequence[float] = DEFAULT_RADII,
) -> dict[str, dict[str, Any]]:
    """为 meta 里的每个报警点生成一份全精度局部 pnts，并回写 meta。

    返回 {alarm_id: localTile 记录}；无报警点/无 eye 时返回空字典。
    """
    meta_path = out_dir / "cloud-meta.json"
    tileset_path = out_dir / "tileset.json"
    if not meta_path.is_file() or not tileset_path.is_file():
        raise SystemExit(f"缺少 {meta_path} 或 {tileset_path}，请先生成主瓦片")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    center = meta.get("localCenter") or {}
    local_center = np.array(
        [float(center["x"]), float(center["y"]), float(center["z"])], dtype=np.float64
    )
    transform = json.loads(tileset_path.read_text(encoding="utf-8"))["root"]["transform"]
    alarms = meta.get("alarms") or []
    if not alarms:
        print("      cloud-meta 无报警点，跳过局部精载")
        return {}

    bounds = meta.get("bounds") or {}
    y_span: tuple[float, float] | None = None
    if bounds.get("min") and bounds.get("max"):
        y_span = (
            float(bounds["min"]["y"]) - float(local_center[1]),
            float(bounds["max"]["y"]) - float(local_center[1]),
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    records: dict[str, dict[str, Any]] = {}
    keep: set[str] = set()

    for alarm in alarms:
        alarm_id = str(alarm.get("id", ""))
        if not alarm_id:
            continue
        # 源工程坐标 = 局部坐标 + localCenter
        src_center = local_center + np.array(
            [float(alarm["x"]), float(alarm["y"]), float(alarm["z"])], dtype=np.float64
        )
        with tempfile.TemporaryDirectory(prefix="alarm_crop_") as tmp:
            ply_path = Path(tmp) / f"{alarm_id}.ply"
            run_pdal_pipeline(
                build_crop_pipeline(source.las_path, ply_path, src_center, PROBE_RADIUS_M)
            )
            pts, rgb, intensity = read_ply(ply_path)

        if len(pts) == 0:
            print(f"      {alarm_id}: 裁剪范围内无点，跳过")
            continue
        local = pts.astype(np.float64) - local_center
        alarm_local = np.array([alarm["x"], alarm["y"], alarm["z"]], dtype=np.float64)
        dist = np.linalg.norm(local - alarm_local, axis=1)
        radius, _ = pick_radius(dist, radii, max_points)
        mask = dist <= radius
        sub = local[mask]
        sub_rgb = None if rgb is None else rgb[mask]
        sub_intensity = None if intensity is None else intensity[mask]
        if len(sub) > max_points:
            # 最小半径仍超限：等步长抽到上限（仍是该范围内的最高密度近似）
            stride = int(math.ceil(len(sub) / max_points))
            sub = sub[::stride]
            sub_rgb = None if sub_rgb is None else sub_rgb[::stride]
            sub_intensity = None if sub_intensity is None else sub_intensity[::stride]

        sub_rgb, sub_intensity = _prepare_attributes(sub, sub_rgb, sub_intensity, y_span)

        pnts_name = f"{alarm_id}.pnts"
        tileset_name = f"tileset-{alarm_id}.json"
        pnts_path = out_dir / pnts_name
        n_written, *_ = write_pnts(
            pnts_path,
            sub.astype(np.float32),
            sub_rgb,
            sub_intensity,
            np.zeros(3, dtype=np.float64),
        )
        payload = alarm_tileset_payload(pnts_name, transform, sub)
        (out_dir / tileset_name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        size = pnts_path.stat().st_size
        keep.update({pnts_name, tileset_name})
        alarm["localTile"] = {
            "uri": tileset_name,
            "pnts": pnts_name,
            "points": int(n_written),
            "bytes": int(size),
            "radiusM": round(float(radius), 2),
        }
        # 范围圈与局部瓦片实际覆盖半径一致（前端画圈用）
        alarm["zoneRadiusM"] = round(float(radius), 2)
        records[alarm_id] = alarm["localTile"]
        print(
            f"      {alarm_id}: 半径 {radius:.1f} m → {n_written} 点, "
            f"{size / 1024 / 1024:.2f} MB"
        )

    _cleanup_stale(out_dir, keep)
    meta["alarms"] = alarms
    meta["alarmsNote"] = (
        "示例口径 · 由各区段点云最低 1% 点派生，非 CLEARANCE 侵限结论；"
        "“就地内部漫游”入点位于该报警点附近的实测点云内（眼高 1.6 m），"
        "进入后加载该点周边全精度局部瓦片，退出即释放"
    )
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return records


def main() -> None:
    ap = argparse.ArgumentParser(description="报警点周边高精度局部瓦片")
    ap.add_argument("--dataset", default="tunnel", help="数据集编码（默认 tunnel）")
    ap.add_argument(
        "--tiles-dir",
        type=Path,
        default=None,
        help="瓦片输出目录（默认 frontend/public/tiles/local/<dataset>）",
    )
    ap.add_argument("--max-points", type=int, default=MAX_LOCAL_POINTS)
    args = ap.parse_args()

    source = get_dataset(args.dataset)
    if source is None:
        raise SystemExit(f"pointcloud_sources.yaml 未登记数据集 {args.dataset}")
    if not source.las_available:
        raise SystemExit(f"真实点云缺失：{source.las_path}")
    out_dir = args.tiles_dir or (
        REPO_ROOT / "frontend" / "public" / "tiles" / "local" / args.dataset
    )
    print(f"===== {args.dataset} 报警点局部精载 → {out_dir} =====")
    records = build_alarm_tiles(source, out_dir, max_points=args.max_points)
    if not records:
        print("      （无产物）")


if __name__ == "__main__":
    main()
