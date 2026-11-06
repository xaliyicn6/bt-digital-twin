"""C1：``component.tech_grade``（构件技术状况等级 1-5 类）。

- L1/L2/L3 仍是缺陷严重度（``defect_level``），与 1-5 类**并存且互不冲突**。
- 新增列默认 NULL（未评定），带 1-5 范围约束。
- 不修改 ``db/schema.sql`` 既有表定义；本迁移是唯一事实源。

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_component_tech_grade_range"


def upgrade() -> None:
    op.add_column(
        "component",
        sa.Column(
            "tech_grade",
            sa.SmallInteger(),
            nullable=True,
            comment="构件技术状况等级（1-5类）；NULL=未评定；与 defect_level(L1-L3) 无映射关系",
        ),
    )
    op.create_check_constraint(
        CONSTRAINT,
        "component",
        "tech_grade IS NULL OR (tech_grade >= 1 AND tech_grade <= 5)",
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "component", type_="check")
    op.drop_column("component", "tech_grade")
