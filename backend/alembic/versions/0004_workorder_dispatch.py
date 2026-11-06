"""维修工单派发：priority / 班组表 / 分析快照 / defect_type 新增 TILT。

本迁移只新增对象与列，不改 ``db/schema.sql`` 既有表结构定义（除 P0 枚举 TILT 的同步登记）。

- ``work_order.priority`` 复用既有 ``notify_level`` 枚举（P1/P2/P3），不新建重复枚举。
- ``maintenance_team`` / ``maintenance_team_member``：班组名称、负责人、联系电话、
  负责线路范围；成员通过成员表关联 ``sys_user``。
- ``work_order.source_task_id`` / ``source_snapshot``：派发时把分析结论快照落库。
- ``defect_type`` 新增 ``TILT``（倾斜/变形），属 P0 枚举变更，已同步 ``db/schema.sql``。

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # P0 枚举：缺陷类型新增 TILT。PG 12+ 允许事务内 ADD VALUE，但同一事务内不可使用该值，
    # 因此本迁移不写入任何 defect_type='TILT' 的业务行。
    op.execute("ALTER TYPE defect_type ADD VALUE IF NOT EXISTS 'TILT'")
    op.execute(
        """
        INSERT INTO dict_item (type_code, item_code, item_name, sort_no)
        VALUES ('defect_type', 'TILT', '倾斜/变形', 5)
        ON CONFLICT (type_code, item_code) DO NOTHING
        """
    )

    op.execute(
        """
        CREATE TABLE maintenance_team (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          project_id    uuid NOT NULL REFERENCES project(id) ON DELETE RESTRICT,
          team_code     varchar(32)  NOT NULL,
          team_name     varchar(128) NOT NULL,
          leader_id     uuid REFERENCES sys_user(id) ON DELETE SET NULL,
          contact_phone varchar(32),
          line_scope    varchar(256),
          data_class    data_class NOT NULL DEFAULT 'INTERNAL',
          created_at    timestamptz NOT NULL DEFAULT now(),
          created_by    uuid,
          updated_at    timestamptz NOT NULL DEFAULT now(),
          updated_by    uuid,
          deleted_at    timestamptz
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_mt_code ON maintenance_team (project_id, team_code) "
        "WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_mt_project ON maintenance_team (project_id) WHERE deleted_at IS NULL"
    )
    op.execute(
        "COMMENT ON COLUMN maintenance_team.line_scope IS '负责的线路/路段范围，用于数据范围过滤'"
    )

    op.execute(
        """
        CREATE TABLE maintenance_team_member (
          team_id       uuid NOT NULL REFERENCES maintenance_team(id) ON DELETE CASCADE,
          user_id       uuid NOT NULL REFERENCES sys_user(id) ON DELETE CASCADE,
          role_in_team  varchar(32) NOT NULL DEFAULT 'MEMBER',
          joined_at     timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_maintenance_team_member PRIMARY KEY (team_id, user_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_mtm_user ON maintenance_team_member (user_id)")

    op.execute(
        "ALTER TABLE work_order ADD COLUMN IF NOT EXISTS priority "
        "notify_level NOT NULL DEFAULT 'P2'"
    )
    op.execute(
        "ALTER TABLE work_order ADD COLUMN IF NOT EXISTS team_id uuid "
        "REFERENCES maintenance_team(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE work_order ADD COLUMN IF NOT EXISTS source_task_id uuid "
        "REFERENCES async_task(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE work_order ADD COLUMN IF NOT EXISTS source_snapshot "
        "jsonb NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute(
        "COMMENT ON COLUMN work_order.source_snapshot IS "
        "'派发时的分析结论快照：task_id/component_code/analysis_type/tilt_permille/"
        "algorithm_version/degraded/degrade_reason'"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_wo_priority ON work_order (priority, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wo_source_task ON work_order (source_task_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wo_team ON work_order (team_id, status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_wo_team")
    op.execute("DROP INDEX IF EXISTS ix_wo_source_task")
    op.execute("DROP INDEX IF EXISTS ix_wo_priority")
    op.execute("ALTER TABLE work_order DROP COLUMN IF EXISTS source_snapshot")
    op.execute("ALTER TABLE work_order DROP COLUMN IF EXISTS source_task_id")
    op.execute("ALTER TABLE work_order DROP COLUMN IF EXISTS team_id")
    op.execute("ALTER TABLE work_order DROP COLUMN IF EXISTS priority")
    op.execute("DROP TABLE IF EXISTS maintenance_team_member")
    op.execute("DROP TABLE IF EXISTS maintenance_team")
    # PostgreSQL 不支持删除枚举值：TILT 保留（降级只回退结构，不回退枚举）。
    op.execute("DELETE FROM dict_item WHERE type_code = 'defect_type' AND item_code = 'TILT'")
