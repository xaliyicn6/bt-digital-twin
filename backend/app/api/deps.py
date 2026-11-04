"""路由依赖：会话、分页参数、当前用户。

当前用户：JWT 校验属 T-03，未落地前只允许显式开发旁路（``BT_DEV_ANONYMOUS=1``），
否则一律 401 BT-10001，避免“默认放行”变成事实上的免鉴权。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Query
from sqlalchemy.orm import Session

from app.core.errors import api_error
from app.db.session import get_session_factory

DEV_ANONYMOUS_ENV = "BT_DEV_ANONYMOUS"


@dataclass(frozen=True)
class PageParams:
    limit: int
    cursor: str | None


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: tuple[str, ...] = ()


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def page_params(
    limit: Annotated[
        int, Query(ge=1, le=100, description="每页条数，默认 20、上限 100；超出上限返回 422")
    ] = 20,
    cursor: Annotated[
        str | None, Query(max_length=256, description="游标分页，取上一页返回的 next_cursor")
    ] = None,
) -> PageParams:
    return PageParams(limit=limit, cursor=cursor)


def get_current_user(
    authorization: Annotated[str | None, Header(description="Bearer <access token>")] = None,
) -> Principal:
    if os.environ.get(DEV_ANONYMOUS_ENV) == "1":
        return Principal(subject="dev-anonymous", roles=("SYSTEM_ADMIN",))
    if authorization is None or not authorization.lower().startswith("bearer "):
        raise api_error("BT-10001", "缺少 Authorization: Bearer <access token>")
    raise api_error(
        "BT-10001",
        "JWT 校验未实现（T-03 未落地）；开发环境可显式设置 BT_DEV_ANONYMOUS=1",
    )


DbSession = Annotated[Session, Depends(get_db)]
PageQuery = Annotated[PageParams, Depends(page_params)]
CurrentUser = Annotated[Principal, Depends(get_current_user)]
