"""构件仓储层：SQL 与几何转换集中在此，不含业务规则。

几何约定：库内是 ``geometry(PointZ, 4547)``；对外一律
``ST_AsGeoJSON(ST_Transform(geom, 4490))``，即在 SQL 层完成投影转换，Python 侧不做任何几何运算。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, bindparam, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.errors import api_error
from app.models.component import INPUT_SRID, STORAGE_SRID, Component
from app.schemas.component import ComponentCreate


@dataclass(frozen=True)
class StructRef:
    """结构（桥或隧）的定位信息。"""

    id: uuid.UUID
    project_id: uuid.UUID


class ComponentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------ 读取
    def get_project_calc_srid(self, project_id: uuid.UUID) -> int | None:
        row = self.db.execute(
            text("SELECT calc_srid FROM project WHERE id = :pid AND deleted_at IS NULL"),
            {"pid": str(project_id)},
        ).first()
        return None if row is None else int(row[0])

    def find_struct(self, struct_code: str, project_id: uuid.UUID | None = None) -> list[StructRef]:
        """按结构编码（bridge_code / tunnel_code）反查结构 id。

        契约 /components 只有 struct_code，不带 project_id；同名跨项目时由服务层要求显式指定。
        """
        sql = text(
            """
            SELECT id, project_id FROM bridge
             WHERE bridge_code = :code
               AND deleted_at IS NULL
               AND (CAST(:pid AS uuid) IS NULL OR project_id = CAST(:pid AS uuid))
            UNION ALL
            SELECT id, project_id FROM tunnel
             WHERE tunnel_code = :code
               AND deleted_at IS NULL
               AND (CAST(:pid AS uuid) IS NULL OR project_id = CAST(:pid AS uuid))
            """
        )
        rows = self.db.execute(
            sql, {"code": struct_code, "pid": None if project_id is None else str(project_id)}
        ).all()
        return [
            StructRef(id=uuid.UUID(str(row[0])), project_id=uuid.UUID(str(row[1]))) for row in rows
        ]

    def get_by_code(self, code: str, *, include_deleted: bool = False) -> Component | None:
        stmt = select(Component).where(Component.component_code == code)
        if not include_deleted:
            stmt = stmt.where(Component.deleted_at.is_(None))
        stmt = stmt.order_by(Component.created_at.desc()).limit(1)
        return self.db.execute(stmt).scalars().first()

    def get_by_id(self, comp_id: uuid.UUID) -> Component | None:
        stmt = select(Component).where(Component.id == comp_id, Component.deleted_at.is_(None))
        return self.db.execute(stmt).scalar_one_or_none()

    def list_components(
        self,
        struct_id: uuid.UUID,
        level: int | None = None,
        comp_type: str | None = None,
        *,
        parent_id: uuid.UUID | None = None,
        after: tuple[int, str] | None = None,
        limit: int | None = None,
    ) -> list[Component]:
        """过滤 + 稳定排序（level asc, component_code asc）；排除软删除。

        ``after`` 是 keyset 游标 (level, component_code)，用于游标分页。
        """
        stmt = select(Component).where(
            Component.deleted_at.is_(None),
            or_(Component.bridge_id == struct_id, Component.tunnel_id == struct_id),
        )
        if level is not None:
            stmt = stmt.where(Component.level == level)
        if comp_type is not None:
            stmt = stmt.where(Component.component_type == comp_type)
        if parent_id is not None:
            stmt = stmt.where(Component.parent_id == parent_id)
        if after is not None:
            after_level, after_code = after
            stmt = stmt.where(
                or_(
                    Component.level > after_level,
                    and_(
                        Component.level == after_level,
                        Component.component_code > after_code,
                    ),
                )
            )
        stmt = stmt.order_by(Component.level.asc(), Component.component_code.asc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.execute(stmt).scalars().all())

    def list_codes_with_prefix(self, project_id: uuid.UUID, prefix: str) -> list[str]:
        stmt = select(Component.component_code).where(
            Component.project_id == project_id,
            Component.deleted_at.is_(None),
            Component.component_code.startswith(f"{prefix}-", autoescape=True),
        )
        return [str(code) for code in self.db.execute(stmt).scalars().all()]

    def next_sequence(self, project_id: uuid.UUID, prefix: str) -> int:
        """原子取号：返回该 (project, prefix) 的下一个流水号（首条为 1）。

        放弃方案及原因：
        - 全局 Sequence：无法按前缀各自计数，跨前缀共享号段并跳号，破坏编码可读性；
        - UUID 后缀：违背文档 3.1.1 编码规范与台账人工核对习惯。

        本方案两步都在 SQL 内完成：
        1) 首次使用以账本里同前缀的最大流水号做种子（兼容历史导入绕过计数表的数据），
           ``ON CONFLICT DO NOTHING`` 保证并发下只有一个线程播种；
        2) ``UPDATE ... RETURNING`` 单条语句原子自增，无竞态、无需重试。
        前缀仅允许 ``[A-Za-z0-9-]``（Schema 已校验），可安全用于正则。
        """
        params = {
            "pid": str(project_id),
            "prefix": prefix,
            "head": f"{prefix}-",
            "regex": f"^{prefix}-[0-9]+$",
        }
        self.db.execute(
            text(
                """
                INSERT INTO component_code_seq (project_id, prefix, last_seq)
                SELECT CAST(:pid AS uuid),
                       CAST(:prefix AS varchar),
                       COALESCE((
                         SELECT MAX((substring(c.component_code FROM char_length(:head) + 1))::int)
                           FROM component c
                          WHERE c.project_id = CAST(:pid AS uuid)
                            AND c.deleted_at IS NULL
                            AND c.component_code ~ CAST(:regex AS text)
                       ), 0)
                ON CONFLICT (project_id, prefix) DO NOTHING
                """
            ),
            params,
        )
        next_val = self.db.execute(
            text(
                """
                UPDATE component_code_seq
                   SET last_seq = last_seq + 1, updated_at = now()
                 WHERE project_id = CAST(:pid AS uuid)
                   AND prefix = CAST(:prefix AS varchar)
                RETURNING last_seq
                """
            ),
            params,
        ).scalar_one()
        return int(next_val)

    def geoms_by_codes(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        """一次 SQL 取回多个构件的 4490 GeoJSON（点 + 实体几何）。

        实测（PostGIS 3.5.2）：``ST_AsGeoJSON`` 不支持 ``PolyhedralSurface``，
        故 geom_solid 走 ``ST_Dump → ST_Multi(ST_Collect(…)) → ST_Transform(…,4490)
        → ST_AsGeoJSON``，输出恒为 MultiPolygon；为空时返回 null。
        """
        if not codes:
            return {}
        stmt = text(
            """
            SELECT c.component_code,
                   ST_AsGeoJSON(ST_Transform(c.geom, 4490)) AS geom,
                   (
                     SELECT ST_AsGeoJSON(ST_Transform(ST_Multi(ST_Collect(d.geom)), 4490))
                       FROM ST_Dump(c.geom_solid) AS d
                   ) AS geom_solid
              FROM component c
             WHERE c.deleted_at IS NULL
               AND c.component_code IN :codes
            """
        ).bindparams(bindparam("codes", expanding=True))
        result: dict[str, dict[str, Any]] = {}
        for code, raw_geom, raw_solid in self.db.execute(stmt, {"codes": codes}).all():
            entry: dict[str, Any] = {}
            if raw_geom:
                entry["geom"] = json.loads(raw_geom)
            if raw_solid:
                entry["geom_solid"] = json.loads(raw_solid)
            result[str(code)] = entry
        return result

    # ------------------------------------------------------------------ 写入
    def create_component(self, comp_in: ComponentCreate) -> Component:
        """同一事务内写入构件；调用方负责 commit（事务边界在 service）。"""
        if comp_in.project_id is None:
            raise api_error("BT-30003", "缺少 project_id，无法确定构件归属项目")
        calc_srid = self.get_project_calc_srid(comp_in.project_id)
        if calc_srid is None:
            raise api_error("BT-30001", "项目不存在或已删除")
        if calc_srid != STORAGE_SRID:
            raise api_error(
                "BT-40001",
                f"项目 calc_srid={calc_srid} 与构件几何列 SRID={STORAGE_SRID} 不一致，"
                f"C-05 未确认前只接受 {STORAGE_SRID}",
            )
        if not comp_in.component_code:
            raise api_error("BT-30003", "缺少 component_code（应由服务层生成）")

        comp = Component(
            project_id=comp_in.project_id,
            struct_kind=comp_in.struct_kind,
            bridge_id=comp_in.bridge_id,
            tunnel_id=comp_in.tunnel_id,
            parent_id=comp_in.parent_id,
            component_code=comp_in.component_code,
            component_name=comp_in.component_name,
            level=comp_in.level,
            component_type=comp_in.component_type,
            pier_type=comp_in.pier_type,
            design_axis_ref=comp_in.design_axis_ref,
            ext_json=comp_in.ext_json,
        )
        if comp_in.lon is not None and comp_in.lat is not None:
            height = 0.0 if comp_in.z is None else comp_in.z
            comp.geom = func.ST_Transform(
                func.ST_SetSRID(func.ST_MakePoint(comp_in.lon, comp_in.lat, height), INPUT_SRID),
                STORAGE_SRID,
            )
        elif comp_in.lon is not None or comp_in.lat is not None:
            raise api_error("BT-30003", "lon 与 lat 必须成对提供")
        self.db.add(comp)
        self.db.flush()
        return comp

    def soft_delete(self, comp: Component) -> None:
        """软删除：只置 deleted_at，不物理删除；已是删除态则原样返回（幂等）。"""
        if comp.deleted_at is None:
            comp.deleted_at = datetime.now(timezone.utc)  # noqa: UP017  本地为 3.10
            self.db.flush()
