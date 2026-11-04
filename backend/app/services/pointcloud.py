"""M04 点云服务（准备阶段）：3D Tiles 入口 Mock 与 HTTP Range 分块读取。

本模块不含任何真实 COPC 解析：本轮只有合成占位文件，用于验证 Range 语义与
3D Tiles 结构。真实 ``.copc.laz`` 由独立 PDAL worker 预转 3D Tiles 后再替换
``mock_root`` 下的占位文件（见 docker-compose.dev.yml 中 pdal-worker 说明）。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.core.errors import api_error

# 单次读取块大小：256 KiB。禁止 read() 整文件，避免 1 GiB 级 COPC 打爆内存。
CHUNK_SIZE = 256 * 1024

MOCK_DIR_ENV = "POINTCLOUD_MOCK_DIR"
ARTIFACT_SUFFIX = ".copc.laz"
BACKEND_DIR = Path(__file__).resolve().parents[2]


class RangeNotSatisfiableError(Exception):
    """HTTP Range 不可满足（越界、区间非法、多区间、空文件）。"""


def mock_root() -> Path:
    """占位文件根目录；只来自环境变量或默认 backend/var/mock_pointcloud。"""
    raw = os.environ.get(MOCK_DIR_ENV, "").strip()
    root = Path(raw) if raw else BACKEND_DIR / "var" / "mock_pointcloud"
    return root.resolve()


def resolve_artifact(component_id: str, root: Path | None = None) -> Path:
    """把 componentId 映射到占位文件路径，并做白名单根目录校验。

    不使用用户输入直接拼绝对路径；``resolve()`` 后必须仍在根目录内，
    否则视为路径穿越，直接 404 BT-30005（不暴露真实文件系统结构）。
    """
    base = (root or mock_root()).resolve()
    candidate = (base / f"{component_id}{ARTIFACT_SUFFIX}").resolve()
    if not candidate.is_relative_to(base):
        raise api_error("BT-30005", "点云文件路径非法")
    return candidate


def parse_range(value: str, total: int) -> tuple[int, int]:
    """解析单区间 ``Range`` 头，返回闭区间 ``(start, end)``（字节偏移）。

    支持 ``bytes=start-end``、``bytes=start-``、``bytes=-suffix``；
    多区间、语法非法、越界、start > end、空文件一律抛
    :class:`RangeNotSatisfiableError`，由路由转 416 并带 ``Content-Range: bytes */total``。
    """
    if total <= 0:
        raise RangeNotSatisfiableError("空文件没有可满足的区间")
    unit, _, spec = value.partition("=")
    if unit.strip().lower() != "bytes" or not spec:
        raise RangeNotSatisfiableError("Range 单位必须是 bytes")
    spec = spec.strip()
    if "," in spec:
        raise RangeNotSatisfiableError("只支持单区间请求")
    if "-" not in spec:
        raise RangeNotSatisfiableError("Range 缺少 '-' 分隔符")
    start_raw, end_raw = (part.strip() for part in spec.split("-", 1))
    if start_raw == "":
        # bytes=-suffix：最后 N 字节
        if not end_raw.isdigit():
            raise RangeNotSatisfiableError("后缀区间非法")
        suffix = int(end_raw)
        if suffix == 0:
            raise RangeNotSatisfiableError("后缀长度必须大于 0")
        start = max(0, total - suffix)
        return start, total - 1

    if not start_raw.isdigit() or (end_raw and not end_raw.isdigit()):
        raise RangeNotSatisfiableError("区间偏移非法")
    start = int(start_raw)
    end = total - 1 if end_raw == "" else int(end_raw)
    if start >= total:
        raise RangeNotSatisfiableError("起始偏移超出文件长度")
    if start > end:
        raise RangeNotSatisfiableError("start 大于 end")
    return start, min(end, total - 1)


def iter_file_chunks(path: Path, start: int, length: int) -> Iterator[bytes]:
    """从 ``start`` 起分块读取 ``length`` 字节；只顺序 seek + read(CHUNK_SIZE)。"""
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def build_tileset(component_id: str, center: tuple[float, float, float]) -> dict[str, Any]:
    """3D Tiles 1.1 tileset.json 的 Mock。

    结构按规范写满（asset.version / geometricError / root.refine /
    root.boundingVolume.box 12 个数 / root.content.uri），只有“内容”是假的：
    box 用构件站位点为中心、局部 ENU 轴 10 m × 10 m × 5 m 的包围盒，
    content.uri 指向 API-08 的 Range 接口（预转 3D Tiles 路线的入口约定）。
    """
    cx, cy, cz = center
    return {
        "asset": {"version": "1.1", "generator": "bt-platform-mock"},
        "geometricError": 4.0,
        "root": {
            "boundingVolume": {
                # 12 个数 = 中心 xyz + 3 个半轴向量
                "box": [cx, cy, cz, 10.0, 0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 5.0]
            },
            "geometricError": 1.0,
            "refine": "ADD",
            "content": {"uri": f"/api/v1/pointcloud/{component_id}/range"},
        },
    }
