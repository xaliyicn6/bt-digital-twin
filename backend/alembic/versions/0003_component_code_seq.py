"""构件编码计数表 component_code_seq：按 (项目, 前缀) 原子取号。

替代 max(component_code)+1：单条 UPSERT 原子自增，无竞态、无需重试；
uq_component_code 唯一索引继续保留作最后兜底。

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE component_code_seq (
          project_id  uuid        NOT NULL REFERENCES project(id) ON DELETE CASCADE,
          prefix      varchar(52) NOT NULL,
          last_seq    integer     NOT NULL DEFAULT 0,
          updated_at  timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_component_code_seq PRIMARY KEY (project_id, prefix),
          CONSTRAINT ck_component_code_seq_last_seq CHECK (last_seq >= 0)
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE component_code_seq IS "
        "'构件编码流水号计数表：按(project_id,prefix)原子自增，替代 max+1 的竞态方案'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS component_code_seq")
