"""共享测试夹具：真实 PostGIS 会话 + Alembic 增量迁移。

运行方式：
    $env:BT_TEST_DATABASE_URL = "postgresql+psycopg://<user>:<pwd>@<host>:<port>/bt_test_m03"
    python -m pytest -q

约定：
- 不设 ``BT_TEST_DATABASE_URL`` 时，数据库相关用例整体 skip（不回落到默认库，也不连生产库）。
- 该库会被自动执行仓库内幂等的 ``db/schema.sql``（只在 ``public.component`` 不存在时执行），
  随后执行 ``alembic upgrade head`` 补齐增量对象（geom_solid 列、component_code_seq 表）。
- 除并发用例外，每个用例跑在外层事务里，用例结束 rollback，测试数据不落库。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.api.deps import get_db
from app.db.session import normalize_url
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.orm import Session

TEST_URL = os.environ.get("BT_TEST_DATABASE_URL")
BACKEND_DIR = Path(__file__).resolve().parents[1]
SCHEMA_SQL = Path(__file__).resolve().parents[2] / "db" / "schema.sql"


def _require_url() -> str:
    if not TEST_URL:
        pytest.skip("未设置 BT_TEST_DATABASE_URL（PostGIS 测试库），跳过数据库相关用例")
    return normalize_url(TEST_URL)


def _ensure_schema(dsn: str) -> None:
    import psycopg

    schema = SCHEMA_SQL.read_text(encoding="utf-8")
    with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as conn:
        exists = conn.execute("SELECT to_regclass('public.component')").fetchone()
        if exists is not None and exists[0] is not None:
            return
        conn.execute(schema)


def _apply_migrations(url: str) -> None:
    """跑 alembic upgrade head：env.py 只认 DATABASE_URL，这里按用例库临时注入。

    不读取 alembic.ini：该文件含中文注释，Windows 下 configparser 按 locale(cp936)
    解码会抛 UnicodeDecodeError；env.py 需要的 script_location 直接注入即可。
    """
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    try:
        command.upgrade(cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = _require_url()
    _ensure_schema(url.replace("+psycopg", ""))
    _apply_migrations(url)
    eng = create_engine(url, future=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def db_connection(engine: Engine) -> Iterator[Connection]:
    conn = engine.connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()


@pytest.fixture()
def session(db_connection: Connection) -> Iterator[Session]:
    db = Session(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    yield db
    db.close()


@pytest.fixture()
def client(db_connection: Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("BT_DEV_ANONYMOUS", "1")

    def _override_get_db() -> Iterator[Session]:
        db = Session(
            bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def seeded(session: Session) -> dict[str, uuid.UUID]:
    """最小主数据：1 个项目（calc_srid=4547）+ 1 座桥（B001）。"""
    project_id = uuid.uuid4()
    bridge_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO project (id, project_code, project_name, calc_srid) "
            "VALUES (:pid, 'P001', '测试项目', 4547)"
        ),
        {"pid": str(project_id)},
    )
    session.execute(
        text(
            "INSERT INTO bridge (id, project_id, bridge_code, bridge_name, struct_type) "
            "VALUES (:bid, :pid, 'B001', '测试桥', 'BEAM')"
        ),
        {"bid": str(bridge_id), "pid": str(project_id)},
    )
    return {"project_id": project_id, "bridge_id": bridge_id}
