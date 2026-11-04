"""数据库引擎与会话工厂。

连接串只来自 ``DATABASE_URL`` 环境变量，禁止在代码或提交文件里写死口令（AGENTS.md 强制规则 7）。
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def normalize_url(url: str) -> str:
    """统一使用 psycopg3 驱动：``postgresql://`` → ``postgresql+psycopg://``。"""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        from app.core.config import Settings

        _engine = create_engine(
            normalize_url(Settings.from_env().database_url),
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _session_factory


def reset_state() -> None:
    """仅测试/热重载使用：丢弃已缓存的引擎与会话工厂。"""
    global _engine, _session_factory
    _engine = None
    _session_factory = None
