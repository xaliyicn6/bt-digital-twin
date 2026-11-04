"""component.geom_solid：墩身/构件实体几何（PolyhedralSurfaceZ, 4547）。

实测（PostGIS 3.5.2）：
- ST_Transform 支持 PolyhedralSurfaceZ（返回 POLYHEDRALSURFACE Z，坐标已转经纬度）；
- ST_AsGeoJSON 不支持 PolyhedralSurface（报 lwgeom_to_geojson: 'PolyhedralSurface'
  geometry type not supported），必须
  ST_Dump → ST_Multi(ST_Collect(…)) → ST_Transform → ST_AsGeoJSON。
因此读取路径见 repositories/component.py 的 geoms_by_codes。

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 新增列属于“新对象”，不改任何既有表定义；类型沿用 DDL 的占位 SRID 4547
    op.execute("ALTER TABLE component ADD COLUMN geom_solid geometry(PolyhedralSurfaceZ, 4547)")
    op.execute("CREATE INDEX ix_component_geom_solid ON component USING GIST (geom_solid)")


def downgrade() -> None:
    # 先删索引再删列，保证干净回滚
    op.execute("DROP INDEX IF EXISTS ix_component_geom_solid")
    op.execute("ALTER TABLE component DROP COLUMN IF EXISTS geom_solid")
