"""真实点云数据集登记与加载。

设计要点：
- 业主真实 LAS 只在本机/内网存在，路径写在 `backend/config/pointcloud_sources.yaml`，
  文件与派生瓦片都在 `.gitignore` 内；本模块只做“构件编码 → 数据集”的解析。
- LAS 读取统一走 `pdal pipeline`（AGENTS.md 要求），不引入 laspy，也不手工解析 LAS 头。
- 坐标系：业主未提供投影带号/中央子午线，统一按局部工程坐标（LOCAL_ENGINEERING）处理。
  真实 CRS 接入点集中在 `backend/config/pointcloud_sources.yaml` 的 `crs` 字段与
  `backend/scripts/build_local_datasets.py` 的 `ANCHOR_*` 常量，见那里的 TODO(C-05)。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "backend" / "config" / "pointcloud_sources.yaml"

# 真实点云点数低于该阈值时判为“数据不可用”，按 BT-30005 明确失败，不用合成数据冒充。
MIN_REAL_POINTS = 50_000

# PDAL 可执行文件：优先 PATH，其次本机 OSGeo4W 默认安装位置。
_PDAL_CANDIDATES = ("pdal", r"C:\OSGeo4W\bin\pdal.exe")

# 2026-11 本机实测可用的抽稀输入（供 build_local_datasets.py 与本服务共用）
DEFAULT_PLY_DIMS = "X,Y,Z,Intensity"


class DatasetUnavailableError(RuntimeError):
    """真实点云缺失 / 点数不足 / PDAL 不可用。上层映射为 BT-30005。"""


@dataclass(frozen=True)
class DatasetSource:
    """一份真实点云数据集在系统中的登记信息。"""

    code: str
    label: str
    las_path: Path
    component_code: str
    analysis_type: str
    decimation_step: int
    source_points: int
    design_model_path: Path | None
    ply_dims: str = DEFAULT_PLY_DIMS

    @property
    def las_available(self) -> bool:
        return self.las_path.is_file()

    @property
    def design_available(self) -> bool:
        return self.design_model_path is not None and self.design_model_path.is_file()


def _resolve_path(raw: str | None) -> Path | None:
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else (REPO_ROOT / path)


@lru_cache(maxsize=1)
def load_dataset_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {"crs": "LOCAL_ENGINEERING", "crs_note": "", "datasets": {}}
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data


def list_datasets() -> list[DatasetSource]:
    """按配置文件顺序返回全部登记数据集。"""
    cfg = load_dataset_config()
    out: list[DatasetSource] = []
    for code, item in (cfg.get("datasets") or {}).items():
        out.append(
            DatasetSource(
                code=str(code),
                label=str(item.get("label", code)),
                las_path=_resolve_path(item.get("las")) or Path(),
                component_code=str(item.get("component_code", "")),
                analysis_type=str(item.get("analysis_type", "VERTICALITY")),
                decimation_step=int(item.get("decimation_step", 10)),
                source_points=int(item.get("source_points", 0)),
                design_model_path=_resolve_path(item.get("design_model")),
                ply_dims=str(item.get("ply_dims", DEFAULT_PLY_DIMS)),
            )
        )
    return out


def get_dataset(code: str) -> DatasetSource | None:
    return next((d for d in list_datasets() if d.code == code), None)


def get_dataset_for_component(component_code: str) -> DatasetSource | None:
    """构件编码 → 数据集。用于 analysis 任务识别“真实点云”构件。"""
    return next((d for d in list_datasets() if d.component_code == component_code), None)


def crs_info() -> tuple[str, str]:
    cfg = load_dataset_config()
    return (
        str(cfg.get("crs", "LOCAL_ENGINEERING")),
        str(cfg.get("crs_note", "")),
    )


def pdal_executable() -> str:
    for cand in _PDAL_CANDIDATES:
        if Path(cand).is_file():
            return cand
        found = shutil.which(cand)
        if found:
            return found
    raise DatasetUnavailableError("未找到 PDAL（pdal pipeline 不可用），无法读取真实 LAS")


def build_pdal_pipeline(
    las_path: Path, ply_path: Path, step: int, dims: str
) -> list[dict[str, Any]]:
    """LAS → 抽稀 PLY 的 PDAL pipeline。纯函数，便于单测。"""
    return [
        {"type": "readers.las", "filename": str(las_path)},
        {"type": "filters.decimation", "step": max(1, int(step))},
        {
            "type": "writers.ply",
            "filename": str(ply_path),
            "dims": dims,
            # PLY 存二进制小端：ASCII 默认只有 3 位有效精度，会把工程坐标精度丢掉。
            "storage_mode": "binary_little_endian",
        },
    ]


def run_pdal_pipeline(pipeline: list[dict[str, Any]]) -> None:
    exe = pdal_executable()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(pipeline, fh, ensure_ascii=False)
        pipeline_path = Path(fh.name)
    try:
        proc = subprocess.run(
            [exe, "pipeline", str(pipeline_path)],
            capture_output=True,
            text=True,
            timeout=900,
        )
    finally:
        pipeline_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise DatasetUnavailableError(
            f"PDAL 转换失败（exit={proc.returncode}）：{proc.stderr.strip()[-500:]}"
        )


def las_to_ply(source: DatasetSource, out_dir: Path | None = None) -> Path:
    """把真实 LAS 抽稀成临时 PLY；文件缺失或转换失败按 BT-30005 抛错。"""
    if not source.las_available:
        raise DatasetUnavailableError(f"真实点云缺失：{source.las_path}（数据集 {source.code}）")
    target_dir = out_dir or (REPO_ROOT / "backend" / "var" / "pointcloud")
    target_dir.mkdir(parents=True, exist_ok=True)
    ply_path = target_dir / f"{source.code}.ply"
    pipeline = build_pdal_pipeline(
        source.las_path, ply_path, source.decimation_step, source.ply_dims
    )
    run_pdal_pipeline(pipeline)
    return ply_path


# ---------------------------------------------------------------------------
# 设计模型（由 backend/scripts/build_local_datasets.py 从 DWG 提取，本地产物）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DesignModel:
    """隧道设计断面 + 配准状态。

    `section` 为断面轮廓的多边形，坐标是断面局部坐标 (u, v)，单位米，
    已按轮廓质心归零；v 向上，u 指向线路右侧（与点云断面基向量一致）。
    """

    section: np.ndarray  # (M, 2) float64，单位米，已质心归零
    registration: str  # DESIGN_OUTLINE_FIT / NOT_REGISTERED
    detail: dict[str, Any]


def load_design_model(path: Path) -> DesignModel | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    section = np.asarray(payload.get("section", []), dtype=np.float64)
    if section.ndim != 2 or section.shape[0] < 3 or section.shape[1] != 2:
        return None
    meta = payload.get("meta") or {}
    return DesignModel(
        section=section,
        registration=str(meta.get("registration", "NOT_REGISTERED")),
        detail=meta,
    )


def points_in_polygon(points_uv: np.ndarray, polygon_uv: np.ndarray) -> np.ndarray:
    """射线法（+u 方向）判点是否在多边形内，返回 bool 数组。"""
    pts = np.asarray(points_uv, dtype=np.float64)
    poly = np.asarray(polygon_uv, dtype=np.float64)
    if pts.size == 0:
        return np.zeros(0, dtype=bool)
    u, v = pts[:, 0], pts[:, 1]
    inside = np.zeros(len(pts), dtype=bool)
    n = len(poly)
    for i in range(n):
        u1, v1 = poly[i]
        u2, v2 = poly[(i + 1) % n]
        crosses = (v1 > v) != (v2 > v)
        with np.errstate(divide="ignore", invalid="ignore"):
            u_cross = u1 + (v - v1) * (u2 - u1) / (v2 - v1)
        inside ^= crosses & (u < u_cross)
    return inside


def penetration_inside_polygon(
    points_uv: np.ndarray, polygon_uv: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """点相对设计轮廓的有符号距离与"侵入深度"（米）。

    返回 (signed, penetration)：
      signed      轮廓外为正、轮廓内为负（到边界的最短距离）
      penetration 轮廓内的点到边界距离，轮廓外为 0

    净空口径关心的是"实测面是否侵入设计内轮廓"：衬砌表面本来就贴着轮廓，
    直接取"到轮廓的距离最小值"会恒等于 0（余量恒为 -安全值），没有工程含义；
    因此只用轮廓内侧的点算侵入深度。
    """
    dist = min_distance_to_polygon(points_uv, polygon_uv)
    inside = points_in_polygon(points_uv, polygon_uv)
    signed = np.where(inside, -dist, dist)
    penetration = np.where(inside, dist, 0.0)
    return signed, penetration


def min_distance_to_polygon(points_uv: np.ndarray, polygon_uv: np.ndarray) -> np.ndarray:
    """逐点到多边形边界的最短距离（二维，米）。

    点/多边形均在断面局部坐标；不做内外判定，只算到轮廓线的距离，
    因为净空口径关心的是“离设计内轮廓还有多远”。
    """
    pts = np.asarray(points_uv, dtype=np.float64)
    poly = np.asarray(polygon_uv, dtype=np.float64)
    if pts.size == 0:
        return np.zeros(0, dtype=np.float64)
    best = np.full(len(pts), np.inf, dtype=np.float64)
    for i in range(len(poly)):
        a = poly[i]
        b = poly[(i + 1) % len(poly)]
        ab = b - a
        denom = float(ab @ ab)
        if denom < 1e-9:
            d = np.linalg.norm(pts - a, axis=1)
        else:
            t = np.clip(((pts - a) @ ab) / denom, 0.0, 1.0)
            proj = a + t[:, None] * ab
            d = np.linalg.norm(pts - proj, axis=1)
        best = np.minimum(best, d)
    return best
