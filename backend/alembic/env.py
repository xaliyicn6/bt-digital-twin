"""Alembic 环境。

连接串只来自 ``DATABASE_URL``，复用 ``app.db.session.normalize_url`` 统一到 psycopg3。
数据库结构的“既有事实源”仍是 ``db/schema.sql``：基线 revision 0001 是空迁移，
0002 起才是本仓库新增对象的增量迁移。
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig  # noqa: F401  预留：需要时再启用日志配置
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.session import normalize_url  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "未设置 DATABASE_URL。迁移连接串只允许通过环境变量注入，禁止写入 alembic.ini。"
        )
    return normalize_url(url)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
