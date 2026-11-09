"""本地点云数据集构建：LAS → 抽稀 PLY → pnts 瓦片 + 设计模型。

只在本机/内网运行（需要 OSGeo4W 的 PDAL 与 AutoCAD 2014 的 accoreconsole）。
产物全部落在 **已 .gitignore** 的本地目录：

    backend/var/local_tiles/{tunnel,bridge}.ply      中间 PLY
    backend/var/design/{02,03,05}.dxf                 DWG→DXF 转换产物
    frontend/public/tiles/local/{tunnel,bridge}/      前端瓦片 + cloud-meta.json
    frontend/public/tiles/local/tunnel/design-model.json

数据安全：业主真实 LAS / DWG 与上述派生文件一律不入库；脚本只读源文件，绝不改写原图。
坐标系：业主未提供投影带号/中央子午线 → 局部工程坐标（LOCAL_ENGINEERING）。

运行：
    backend/.venv/Scripts/python.exe backend/scripts/build_local_datasets.py
    # 或只重建设计模型： --design-only
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.pointcloud_datasets import (  # noqa: E402
    DatasetSource,
    build_pdal_pipeline,
    list_datasets,
    run_pdal_pipeline,
)

# ---------------------------------------------------------------------------
# 坐标系锚点（仅用于把 ENU 局部原点放到地球上；底图仅作背景）
# ---------------------------------------------------------------------------
# TODO(C-05)：拿到投影带号/中央子午线后，用 pyproj 由工程坐标反算真实 lon/lat
# 并替换下面三个常量；在此之前不编造带号，界面统一标注“局部工程坐标 · 未做大地基准转换”。
ANCHOR_LON = 120.3826
ANCHOR_LAT = 36.0671
ANCHOR_HEIGHT = 0.0
CRS_CODE = "LOCAL_ENGINEERING"
CRS_NOTE = "局部工程坐标 · 未做大地基准转换；底图仅作背景，不声明位置精度"

# AutoCAD 2014 headless（只读源 DWG，转换产物落 backend/var/design/）
ACCORECONSOLE_CANDIDATES = (
    r"C:\Program Files\Autodesk\AutoCAD 2014\accoreconsole.exe",
    "accoreconsole",
)

DESIGN_SOURCE_DIR = REPO_ROOT / "backend" / "tests" / "轮小区间设计数据"
DESIGN_WORK_DIR = REPO_ROOT / "backend" / "var" / "design"
LOCAL_TILES_DIR = REPO_ROOT / "frontend" / "public" / "tiles" / "local"

# 总平面图里线路轴线的图层（02 与 LAS XY 同坐标系）
AXIS_LAYER = "00-XL-线路"
# 05 图里 A 型衬砌断面图的标题文字，作为断面轮廓的定位锚点
SECTION_LABEL = "A型衬砌断面图"
RING_SPACING_M = 10.0


@dataclass(frozen=True)
class RouteAxis:
    points: np.ndarray  # (N, 2) 工程坐标 (x=easting, y=northing)

    @property
    def length_m(self) -> float:
        seg = np.diff(self.points, axis=0)
        return float(np.sum(np.hypot(seg[:, 0], seg[:, 1])))


def _log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# DWG → DXF
# ---------------------------------------------------------------------------


def find_accoreconsole() -> str:
    for cand in ACCORECONSOLE_CANDIDATES:
        if Path(cand).is_file():
            return cand
    raise RuntimeError("未找到 AutoCAD 2014 accoreconsole（DWG→DXF 需要本机 AutoCAD）")


def convert_dwg_to_dxf(dwg: Path, dxf: Path) -> Path:
    """用 accoreconsole 把 DWG 转成 ASCII DXF；源 DWG 只读。"""
    if not dwg.is_file():
        raise FileNotFoundError(f"设计源文件缺失：{dwg}")
    dxf.parent.mkdir(parents=True, exist_ok=True)
    if dxf.exists() and dxf.stat().st_mtime >= dwg.stat().st_mtime:
        _log(f"    跳过转换（DXF 已是最新）：{dxf.name}")
        return dxf
    scr = DESIGN_WORK_DIR / f"conv_{dwg.stem}.scr"
    scr.write_text(f"FILEDIA\n0\nDXFOUT\n{dxf}\n16\n", encoding="ascii")
    exe = find_accoreconsole()
    _log(f"    accoreconsole 转换：{dwg.name} → {dxf.name}")
    proc = subprocess.run(
        [exe, "/i", str(dwg), "/s", str(scr)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if proc.returncode != 0 or not dxf.is_file():
        raise RuntimeError(
            f"DWG→DXF 失败（exit={proc.returncode}）：{proc.stdout[-300:]}{proc.stderr[-300:]}"
        )
    return dxf


# ---------------------------------------------------------------------------
# DXF 解析
# ---------------------------------------------------------------------------


def _require_ezdxf() -> Any:
    try:
        import ezdxf
    except ImportError as exc:  # pragma: no cover - 本机工具链缺失
        raise RuntimeError(
            "缺少 ezdxf（仅设计提取脚本使用）；请 `uv pip install ezdxf` 后重试"
        ) from exc
    return ezdxf


def extract_route_axis(dxf_path: Path) -> RouteAxis:
    """取 02 总平面图 `00-XL-线路` 图层上节点最多的一条多段线作为线路轴线。"""
    ezdxf = _require_ezdxf()
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()

    best: list[tuple[float, float]] = []
    for entity in msp:
        if entity.dxftype() != "LWPOLYLINE":
            continue
        if entity.dxf.layer != AXIS_LAYER:
            continue
        pts = [(float(x), float(y)) for x, y in entity.get_points("xy")]
        if len(pts) > len(best):
            best = pts
    if len(best) < 2:
        raise RuntimeError(f"{dxf_path.name}: 图层 {AXIS_LAYER} 未找到线路轴线多段线")
    arr = np.asarray(best, dtype=np.float64)
    _log(f"    线路轴线：{len(arr)} 节点，长 {np.sum(np.hypot(*np.diff(arr, axis=0).T)):.0f} m")
    return RouteAxis(points=arr)


def extract_design_section(dxf_path: Path) -> np.ndarray:
    """取 05 图 A 型衬砌断面图的最内层闭合轮廓，转成米并质心归零。

    做法：以 “A型衬砌断面图” 标题文字为锚点，在其上方窗口内找闭合 LWPOLYLINE
    （4~12 节点、宽度 8~15 m），取宽度最小的一条（最内层 = 二衬内轮廓）。
    返回 (M, 2)，单位米，u 横 / v 上。
    """
    ezdxf = _require_ezdxf()
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()

    anchor: tuple[float, float] | None = None
    for entity in msp:
        if entity.dxftype() not in ("TEXT", "MTEXT"):
            continue
        text = entity.dxf.text if entity.dxftype() == "TEXT" else entity.text
        if SECTION_LABEL in str(text):
            insert = entity.dxf.insert
            anchor = (float(insert.x), float(insert.y))
            break
    if anchor is None:
        raise RuntimeError(f"{dxf_path.name}: 未找到标题 “{SECTION_LABEL}”")

    ax, ay = anchor
    candidates: list[np.ndarray] = []
    for entity in msp:
        if entity.dxftype() != "LWPOLYLINE" or not entity.closed:
            continue
        pts = np.asarray(
            [(float(x), float(y)) for x, y in entity.get_points("xy")], dtype=np.float64
        )
        if not (4 <= len(pts) <= 12):
            continue
        w = float(pts[:, 0].max() - pts[:, 0].min())
        h = float(pts[:, 1].max() - pts[:, 1].min())
        cx = float(pts[:, 0].mean())
        cy = float(pts[:, 1].mean())
        if not (8000.0 <= w <= 15000.0 and 4000.0 <= h <= 8000.0):
            continue
        # 断面标题在轮廓下方；窗口 ±15 m 内
        if abs(cx - ax) > 15000.0 or not (-2000.0 <= cy - ay <= 12000.0):
            continue
        candidates.append(pts)

    if not candidates:
        raise RuntimeError(f"{dxf_path.name}: 未在 “{SECTION_LABEL}” 附近找到断面闭合轮廓")

    def width(p: np.ndarray) -> float:
        return float(p[:, 0].max() - p[:, 0].min())

    outline = min(candidates, key=width)
    # mm → m，并按轮廓质心归零
    section = outline * 0.001
    section = section - section.mean(axis=0)
    _log(
        f"    设计断面：{len(section)} 节点，宽 "
        f"{float(section[:, 0].max() - section[:, 0].min()):.2f} m，"
        f"高 {float(section[:, 1].max() - section[:, 1].min()):.2f} m"
    )
    return section


# ---------------------------------------------------------------------------
# 沿线路轴摆放断面环（竖向以点云断面中心拟合）
# ---------------------------------------------------------------------------


def resample_axis(axis: RouteAxis, spacing: float) -> tuple[np.ndarray, np.ndarray]:
    """按弧长等距重采样轴线，返回 (站点 (K,2), 单位切向 (K,2))。"""
    pts = axis.points
    seg = np.diff(pts, axis=0)
    seg_len = np.hypot(seg[:, 0], seg[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    total = float(cum[-1])
    distances = np.arange(spacing * 0.5, total, spacing)
    stations = np.empty((distances.size, 2), dtype=np.float64)
    tangents = np.empty((distances.size, 2), dtype=np.float64)
    for i, d in enumerate(distances):
        j = int(np.searchsorted(cum, d, side="right") - 1)
        j = min(max(j, 0), len(seg_len) - 1)
        k = (d - cum[j]) / max(seg_len[j], 1e-9)
        stations[i] = pts[j] + k * seg[j]
        tangents[i] = seg[j] / max(seg_len[j], 1e-9)
    return stations, tangents


def fit_ground_z(
    cloud_xy: np.ndarray,
    cloud_z: np.ndarray,
    stations: np.ndarray,
    radius: float = 6.0,
    min_points: int = 50,
) -> np.ndarray:
    """每个站点的点云竖向中心（邻域 z 中位数）；无有效覆盖的站点为 NaN。

    只在**严格半径**内取邻域，且要求至少 ``min_points`` 个点：
    不做“扩大半径兜底”，否则线路轴线上没有隧道点云的区段会拿远处山体/地形的
    z 拟合出悬空的假断面环。无点云支撑的站位直接跳过，设计层只覆盖实测区间。
    """
    from scipy.spatial import cKDTree

    tree = cKDTree(cloud_xy)
    z_fit = np.full(len(stations), np.nan, dtype=np.float64)
    for i, (x, y) in enumerate(stations):
        idx = tree.query_ball_point((x, y), radius)
        if len(idx) >= min_points:
            z_fit[i] = float(np.median(cloud_z[idx]))
    return z_fit


def build_design_model(
    axis: RouteAxis,
    section: np.ndarray,
    cloud_xyz: np.ndarray,
    local_center: np.ndarray,
    *,
    spacing: float = RING_SPACING_M,
) -> dict[str, Any]:
    """沿轴生成断面环（pnts 局部坐标），并给出设计层元信息。"""
    stations, tangents = resample_axis(axis, spacing)
    # 站点沿轴弧长（里程），用于 meta 的站位标注
    seg = np.diff(stations, axis=0)
    cum = np.concatenate([[0.0], np.cumsum(np.hypot(seg[:, 0], seg[:, 1]))])
    z_fit = fit_ground_z(cloud_xyz[:, :2], cloud_xyz[:, 2], stations)

    # 断面基：right = (-ty, tx)（与 tunnel.py 的 e1 = Z×axis 一致），up = 世界 Z
    rings: list[list[list[float]]] = []
    ring_stations: list[float] = []
    for i, ((sx, sy), (tx, ty), zc) in enumerate(zip(stations, tangents, z_fit, strict=True)):
        if not np.isfinite(zc):
            continue
        right = np.array([-ty, tx], dtype=np.float64)
        world = np.empty((len(section), 3), dtype=np.float64)
        world[:, 0] = sx + section[:, 0] * right[0]
        world[:, 1] = sy + section[:, 0] * right[1]
        world[:, 2] = zc + section[:, 1]
        rings.append((world - local_center).round(3).tolist())
        ring_stations.append(float(cum[i]) - float(cum[0]))

    if not rings:
        raise RuntimeError("设计断面无法落位：点云线路范围与设计轴线无重叠")

    longitudinal: list[list[list[float]]] = []
    stacked = np.asarray(rings, dtype=np.float64)  # (R, M, 3)
    for vertex_idx in range(stacked.shape[1]):
        longitudinal.append(stacked[:, vertex_idx, :].round(3).tolist())

    return {
        "meta": {
            "source": "05衬砌断面图等.dwg → 05.dxf · A型衬砌断面图（最内层闭合轮廓）",
            "axisSource": "02 总平面图.dwg → 02.dxf · 图层 00-XL-线路",
            "sectionVertices": int(len(section)),
            "sectionWidthM": round(float(section[:, 0].max() - section[:, 0].min()), 3),
            "sectionHeightM": round(float(section[:, 1].max() - section[:, 1].min()), 3),
            "sectionCount": len(rings),
            "ringSpacingM": spacing,
            "registration": "DESIGN_OUTLINE_FIT",
            "registrationNote": "设计轮廓 · 竖向基准拟合（示意） · 未做控制点校核",
            "coverageNote": (
                "仅在与点云有效断面（6 m 邻域 ≥ 50 点）重叠的站位生成断面环；"
                "其余站位无实测支撑，不生成（避免悬空假断面）"
            ),
            "stationRangeM": [round(ring_stations[0], 1), round(ring_stations[-1], 1)],
            "crs": CRS_CODE,
            "crsNote": CRS_NOTE,
            "anchor": {"lon": ANCHOR_LON, "lat": ANCHOR_LAT, "height": ANCHOR_HEIGHT},
            "localCenter": {
                "x": round(float(local_center[0]), 3),
                "y": round(float(local_center[1]), 3),
                "z": round(float(local_center[2]), 3),
            },
        },
        "section": section.round(4).tolist(),
        "rings": rings,
        "longitudinal": longitudinal,
        "stationOffsetsM": [round(v, 1) for v in ring_stations],
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _read_ply_xyz(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """用 generate_3dtiles 的 PLY 读取器拿 xyz / intensity。"""
    sys.path.insert(0, str(SCRIPT_DIR))
    from generate_3dtiles import read_ply  # noqa: PLC0415

    pts, _rgb, intensity = read_ply(path)
    if intensity is None:
        intensity = np.zeros(len(pts), dtype=np.float32)
    return pts, intensity


def build_tiles(source: DatasetSource, ply_path: Path) -> Path:
    out_dir = LOCAL_TILES_DIR / source.code
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "generate_3dtiles.py"),
        "--input",
        str(ply_path),
        "--output-dir",
        str(out_dir),
        "--dataset-code",
        source.code,
        "--label",
        source.label,
        "--source-file",
        source.las_path.relative_to(REPO_ROOT).as_posix(),
        "--source-points",
        str(source.source_points),
        "--crs",
        CRS_CODE,
        "--crs-note",
        CRS_NOTE,
        "--lon",
        str(ANCHOR_LON),
        "--lat",
        str(ANCHOR_LAT),
        "--height",
        str(ANCHOR_HEIGHT),
    ]
    _log(f"  → 生成瓦片：{out_dir}")
    subprocess.run(cmd, check=True)
    return out_dir


def build_design(local_center: np.ndarray, cloud_xyz: np.ndarray) -> Path:
    _log("[design] DWG → DXF")
    dwgs = {
        "02": DESIGN_SOURCE_DIR / "02 总平面图.dwg",
        "05": DESIGN_SOURCE_DIR / "05衬砌断面图等.dwg",
    }
    actual_02 = next((p for p in DESIGN_SOURCE_DIR.glob("02*.dwg")), dwgs["02"])
    actual_05 = next((p for p in DESIGN_SOURCE_DIR.glob("05*.dwg")), dwgs["05"])
    dxf_02 = convert_dwg_to_dxf(actual_02, DESIGN_WORK_DIR / "02.dxf")
    dxf_05 = convert_dwg_to_dxf(actual_05, DESIGN_WORK_DIR / "05.dxf")

    _log("[design] 解析线路轴线与 A 型衬砌断面")
    axis = extract_route_axis(dxf_02)
    section = extract_design_section(dxf_05)

    _log("[design] 沿轴按点云竖向拟合生成断面环")
    model = build_design_model(axis, section, cloud_xyz, local_center)
    out = LOCAL_TILES_DIR / "tunnel" / "design-model.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    _log(f"    design-model.json → {out}（{model['meta']['sectionCount']} 个断面环）")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="本地真实点云数据集构建")
    ap.add_argument("--design-only", action="store_true", help="只重建设计模型")
    ap.add_argument("--skip-design", action="store_true", help="只生成点云瓦片")
    args = ap.parse_args()

    datasets = list_datasets()
    if not datasets:
        raise SystemExit("pointcloud_sources.yaml 未登记任何数据集")

    for source in datasets:
        _log(f"===== {source.code} / {source.label} =====")
        if not source.las_available:
            _log(f"  ✘ 真实点云缺失：{source.las_path}")
            raise SystemExit(1)
        ply_path = BACKEND_DIR / "var" / "local_tiles" / f"{source.code}.ply"
        if not args.design_only:
            ply_path.parent.mkdir(parents=True, exist_ok=True)
            _log(f"  PDAL 抽稀 step={source.decimation_step} → {ply_path}")
            run_pdal_pipeline(
                build_pdal_pipeline(
                    source.las_path, ply_path, source.decimation_step, source.ply_dims
                )
            )
            build_tiles(source, ply_path)

    if args.skip_design:
        return

    tunnel = next((d for d in datasets if d.code == "tunnel"), None)
    if tunnel is None:
        return
    meta_path = LOCAL_TILES_DIR / "tunnel" / "cloud-meta.json"
    if not meta_path.is_file():
        raise SystemExit("缺少 tunnel/cloud-meta.json，请先生成瓦片")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    local_center = np.array(
        [meta["localCenter"]["x"], meta["localCenter"]["y"], meta["localCenter"]["z"]],
        dtype=np.float64,
    )
    cloud_xyz, _intensity = _read_ply_xyz(BACKEND_DIR / "var" / "local_tiles" / "tunnel.ply")
    build_design(local_center, cloud_xyz)
    _log("✔ 本地数据集构建完成")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
