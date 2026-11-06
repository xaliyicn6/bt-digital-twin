"""``component`` 表模型（四级构件树）。

约束来源：``db/schema.sql`` 第 3 节主数据，表名/字段/枚举均为 P0 冻结项，本文件只做映射。
几何列在库内是 ``geometry(PointZ, 4547)``（C-05 确认前的占位 SRID），禁止改成 4490。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, SmallInteger, String, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

STORAGE_SRID = 4547
INPUT_SRID = 4490  # 外部输入/输出用 CGCS2000 经纬度；库内存储仍是 STORAGE_SRID


class Base(DeclarativeBase):
    pass


class StructKind(str, Enum):
    BRIDGE = "BRIDGE"
    TUNNEL = "TUNNEL"


class PierType(str, Enum):
    RECT = "RECT"
    CIRCLE = "CIRCLE"
    ROUND_END = "ROUND_END"
    OTHER = "OTHER"


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    return [str(member.value) for member in enum_cls]


def _pg_enum(enum_cls: type[Enum], name: str) -> SAEnum:
    """复用库里已存在的枚举类型，create_type=False 防止 Alembic/建表时重复 CREATE TYPE。"""
    return SAEnum(enum_cls, name=name, create_type=False, values_callable=_enum_values)


class Component(Base):
    __tablename__ = "component"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # 外键由 db/schema.sql 在库侧强制；此处不声明 ForeignKey，避免为 project/bridge/tunnel
    # 造“影子表”而掩盖真实 DDL。跨表关系一律走显式 SQL。
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    struct_kind: Mapped[StructKind] = mapped_column(_pg_enum(StructKind, "struct_kind"))
    bridge_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    tunnel_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    component_code: Mapped[str] = mapped_column(String(64))
    component_name: Mapped[str] = mapped_column(String(128))
    level: Mapped[int] = mapped_column(SmallInteger)
    component_type: Mapped[str] = mapped_column(String(32))
    pier_type: Mapped[PierType | None] = mapped_column(
        _pg_enum(PierType, "pier_type"), nullable=True
    )
    # C1：构件技术状况等级 1-5（Alembic 0005 增量列）；NULL 表示尚未评定。
    tech_grade: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    design_axis_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 几何只在 SQL 层做 ST_Transform，Python 侧不持有可用坐标
    geom: Mapped[Any | None] = mapped_column(Geometry("POINTZ", srid=STORAGE_SRID), nullable=True)
    # 实体几何：PolyhedralSurfaceZ；ST_AsGeoJSON 不支持该类型，读取走 ST_Dump 链路
    geom_solid: Mapped[Any | None] = mapped_column(
        Geometry("POLYHEDRALSURFACEZ", srid=STORAGE_SRID), nullable=True
    )
    ext_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
