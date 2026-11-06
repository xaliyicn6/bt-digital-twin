"""构件出入参。

契约对齐说明（contracts/openapi.yaml ``Component``）：
- 契约字段：id / component_code / parent_id / level / component_type / pier_type /
  tech_grade / design_axis_ref / srid / updated_at，本文件同名输出。
- 契约未定义 geom 与 geom_solid：本文件作为扩展字段输出，坐标一律是 ST_Transform(geom,4490)
  的结果；契约未声明 additionalProperties=false，故不违反契约。
- ``component_name`` 是建库必填字段，但契约未暴露，故只入不出。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.component import PierType, StructKind


class ComponentCreate(BaseModel):
    """创建入参。

    L1 必须自带 project_id + struct_kind + (bridge_id xor tunnel_id)；
    L2–L4 只需 parent_id + level，其余从父节点继承（显式传入时必须与父节点一致）。
    """

    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID | None = None
    struct_kind: StructKind | None = None
    bridge_id: uuid.UUID | None = None
    tunnel_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    level: int = Field(ge=1, le=4)

    component_type: str = Field(min_length=1, max_length=32)
    component_name: str = Field(min_length=1, max_length=128)
    # 前缀段，如 B001 / B001-K01 / B001-K01-PIR；流水号由服务层追加
    code_prefix: str = Field(
        min_length=1,
        max_length=52,
        pattern=r"^[A-Za-z0-9-]+$",
        description="编码前缀段，如 B001-K01-PIR；只允许字母数字与连字符",
    )
    # 历史数据导入允许显式指定完整编码；缺省时由服务自动生成
    component_code: str | None = Field(default=None, min_length=1, max_length=64)

    pier_type: PierType | None = None
    design_axis_ref: str | None = Field(default=None, max_length=255)

    # 输入坐标固定按 EPSG:4490（经纬度）解释，写入时在 SQL 层转成库内 SRID
    lon: float | None = Field(default=None, ge=-180, le=180)
    lat: float | None = Field(default=None, ge=-90, le=90)
    z: float | None = None

    ext_json: dict[str, Any] = Field(default_factory=dict)


class GeoJSONPoint(BaseModel):
    """RFC 7946 Point；coordinates 为 [lon, lat, z]，固定 EPSG:4490。"""

    type: Literal["Point"] = "Point"
    coordinates: list[float] = Field(min_length=2, max_length=3)


class ComponentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    component_code: str
    parent_id: uuid.UUID | None = None
    level: int = Field(ge=1, le=4)
    component_type: str
    pier_type: PierType | None = None
    # C1：构件技术状况等级（1-5），NULL 表示尚未评定；资产树/徽标读该字段。
    tech_grade: int | None = None
    design_axis_ref: str | None = None
    # 库内几何列的 SRID（DDL 冻结值 4547，C-05 前占位）
    srid: int = 4547
    updated_at: datetime

    # 扩展字段：几何由 ST_AsGeoJSON(ST_Transform(geom, 4490)) 产出
    geom: GeoJSONPoint | None = None
    geom_srid: int | None = None
    # 实体几何 4490 GeoJSON：读写走 ST_Dump→ST_Multi(ST_Collect)→ST_Transform(4490)，
    # 恒为 MultiPolygon；库内无数据时为 null
    geom_solid: dict[str, Any] | None = None


class ComponentList(BaseModel):
    items: list[ComponentOut] = Field(default_factory=list)
    next_cursor: str | None = None
