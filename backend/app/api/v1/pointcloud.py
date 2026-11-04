"""M04 点云路由（准备阶段，Mock 版）。

契约：``contracts/openapi.yaml`` 的 ``/pointcloud/{componentId}/tileset`` 与
``/pointcloud/{componentId}/range``。本轮只做结构合法、语义正确的 Mock：
- tileset 返回 3D Tiles 1.1 结构，内容（几何范围/瓦片）是占位；
- range 用合成占位文件验证 HTTP Range 语义，尚未接真实 COPC。
"""

from __future__ import annotations

import uuid
from email.utils import formatdate
from typing import Annotated, Any

from fastapi import APIRouter, Header, Path, Response
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, DbSession
from app.core.errors import api_error
from app.repositories.component import ComponentRepository
from app.services.pointcloud import (
    RangeNotSatisfiableError,
    build_tileset,
    iter_file_chunks,
    mock_root,
    parse_range,
    resolve_artifact,
)

router = APIRouter(prefix="/pointcloud", tags=["pointcloud"])

OCTET_STREAM = "application/octet-stream"

ComponentId = Annotated[
    uuid.UUID,
    Path(alias="componentId", description="构件 id（UUID）；非 UUID 直接 422"),
]


def _ensure_component(db: DbSession, component_id: uuid.UUID) -> None:
    if ComponentRepository(db).get_by_id(component_id) is None:
        raise api_error("BT-30001", f"构件 {component_id} 不存在或已删除")


def _component_center(db: DbSession, component_id: uuid.UUID) -> tuple[float, float, float]:
    comp = ComponentRepository(db).get_by_id(component_id)
    if comp is None:
        raise api_error("BT-30001", f"构件 {component_id} 不存在或已删除")
    entry = ComponentRepository(db).geoms_by_codes([comp.component_code]).get(comp.component_code)
    coords = (entry or {}).get("geom", {}).get("coordinates") or []
    if len(coords) < 2:
        return (0.0, 0.0, 0.0)
    z = float(coords[2]) if len(coords) > 2 else 0.0
    return (float(coords[0]), float(coords[1]), z)


@router.get(
    "/{componentId}/tileset",
    summary="取 3D Tiles 入口（Mock：结构合法、内容占位）",
    responses={
        404: {"description": "构件不存在（BT-30001）"},
        422: {"description": "componentId 非 UUID（BT-10003）"},
    },
)
def get_tileset(
    db: DbSession,
    _user: CurrentUser,
    component_id: ComponentId,
) -> dict[str, Any]:
    return build_tileset(str(component_id), _component_center(db, component_id))


@router.get(
    "/{componentId}/range",
    summary="按 HTTP Range 取点云字节（Mock 占位文件）",
    responses={
        200: {"description": "无 Range 时返回全量（分块流式）"},
        206: {"description": "部分内容，带 Content-Range"},
        404: {"description": "构件或点云文件不存在（BT-30001 / BT-30005）"},
        416: {"description": "区间不可满足，带 Content-Range: bytes */total"},
        422: {"description": "componentId 非 UUID（BT-10003）"},
    },
)
def get_range(
    db: DbSession,
    _user: CurrentUser,
    component_id: ComponentId,
    range_header: Annotated[
        str | None,
        Header(alias="Range", description="单区间，如 bytes=0-65535 / bytes=1024- / bytes=-4096"),
    ] = None,
) -> Response:
    _ensure_component(db, component_id)
    path = resolve_artifact(str(component_id), mock_root())
    if not path.is_file():
        raise api_error("BT-30005", f"构件 {component_id} 的点云文件不存在")

    stat = path.stat()
    total = stat.st_size
    common_headers = {
        "Accept-Ranges": "bytes",
        "ETag": f'"{stat.st_size:x}-{stat.st_mtime_ns:x}"',
        "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
    }

    if total <= 0:
        return Response(
            status_code=416,
            headers={**common_headers, "Content-Range": "bytes */0"},
        )

    if range_header is None:
        # 无 Range：200 全量，仍然分块流式，绝不 read() 整个文件
        return StreamingResponse(
            iter_file_chunks(path, 0, total),
            media_type=OCTET_STREAM,
            headers={**common_headers, "Content-Length": str(total)},
        )

    try:
        start, end = parse_range(range_header, total)
    except RangeNotSatisfiableError:
        return Response(
            status_code=416,
            headers={**common_headers, "Content-Range": f"bytes */{total}"},
        )

    length = end - start + 1
    return StreamingResponse(
        iter_file_chunks(path, start, length),
        status_code=206,
        media_type=OCTET_STREAM,
        headers={
            **common_headers,
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Content-Length": str(length),
        },
    )
