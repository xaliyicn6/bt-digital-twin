"""构件台账路由（契约 contracts/openapi.yaml: /components 与 /components/{code}）。

参数名以契约为准：``struct_code``、``type``、``level``、``parent_id``、``limit``、``cursor``。
几何一律在 SQL 层经 ``ST_AsGeoJSON(ST_Transform(geom, 4490))`` 输出经纬度，Python 不做转换。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status

from app.api.deps import CurrentUser, DbSession, PageQuery
from app.schemas.component import ComponentCreate, ComponentList, ComponentOut
from app.services.component import ComponentService

router = APIRouter(prefix="/components", tags=["asset"])


@router.get(
    "",
    response_model=ComponentList,
    summary="构件查询（按结构/类型/层级）",
    responses={
        400: {"description": "参数缺失或格式错误"},
        403: {"description": "无权限或越出数据范围"},
    },
)
def list_components(
    db: DbSession,
    page: PageQuery,
    _user: CurrentUser,
    struct_code: Annotated[
        str, Query(min_length=1, max_length=32, description="结构编码，如 B001")
    ],
    component_type: Annotated[
        str | None, Query(alias="type", max_length=32, description="构件类型，如 PIR")
    ] = None,
    level: Annotated[int | None, Query(ge=1, le=4, description="构件层级 L1–L4")] = None,
    parent_id: Annotated[uuid.UUID | None, Query(description="父构件 id")] = None,
    project_id: Annotated[
        uuid.UUID | None, Query(description="扩展参数：结构编码跨项目重名时用于消歧")
    ] = None,
) -> ComponentList:
    return ComponentService(db).list_components(
        struct_code=struct_code,
        component_type=component_type,
        level=level,
        parent_id=parent_id,
        project_id=project_id,
        limit=page.limit,
        cursor=page.cursor,
    )


@router.post(
    "",
    response_model=ComponentOut,
    status_code=status.HTTP_201_CREATED,
    summary="新建构件（L1 带 project/结构归属；L2–L4 带 parent_id）",
    responses={
        400: {"description": "参数缺失或格式错误"},
        401: {"description": "未认证"},
        403: {"description": "无权限或越出数据范围"},
        404: {"description": "父节点或项目不存在"},
        409: {"description": "构件编码重复（BT-30002）"},
        422: {"description": "层级/父节点或几何参数非法"},
    },
)
def create_component(
    db: DbSession,
    _user: CurrentUser,
    payload: ComponentCreate,
) -> ComponentOut:
    service = ComponentService(db)
    comp = service.create_component(payload)
    # 复用详情读路径：几何一律在 SQL 层转 4490，避免 Python 解析 WKT
    return service.get_component(comp.component_code)


@router.get(
    "/{code}",
    response_model=ComponentOut,
    summary="构件详情（含 geom / geom_solid / pier_type）",
    responses={404: {"description": "构件不存在（含软删除）"}},
)
def get_component(
    db: DbSession,
    _user: CurrentUser,
    code: Annotated[str, Path(min_length=1, max_length=64, description="构件编码")],
) -> ComponentOut:
    return ComponentService(db).get_component(code)


@router.delete(
    "/{code}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="软删除构件（幂等）",
    responses={404: {"description": "构件不存在"}},
)
def delete_component(
    db: DbSession,
    _user: CurrentUser,
    code: Annotated[str, Path(min_length=1, max_length=64, description="构件编码")],
) -> Response:
    ComponentService(db).delete_component(code)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
