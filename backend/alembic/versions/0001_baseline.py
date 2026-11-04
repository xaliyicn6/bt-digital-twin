"""基线：对齐 db/schema.sql 已建出的结构。

upgrade 为空——db/schema.sql 是既有结构的事实源，Alembic 不得重复建表。
已有库处理方式：
    alembic stamp 0001      # 把版本表对齐到基线（不要 stamp head，否则会跳过后续增量）
    alembic upgrade head    # 应用 0002 起的增量

Revision ID: 0001
Revises:
Create Date: 2026-11-04
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """空迁移：既有 29 张表由 db/schema.sql 创建。"""


def downgrade() -> None:
    """空迁移：不删除任何既有对象。"""
