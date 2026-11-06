"""当前登录主体：供前端判断审批权限（角色）与自审禁用。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.schemas.workorder import CurrentUserInfo

router = APIRouter(prefix="/auth", tags=["auth"])


def _as_uuid(subject: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(subject)
    except ValueError:
        return None


@router.get(
    "/me",
    response_model=CurrentUserInfo,
    summary="当前用户（subject / user_id / roles）",
)
def me(user: CurrentUser) -> CurrentUserInfo:
    return CurrentUserInfo(
        subject=user.subject, user_id=_as_uuid(user.subject), roles=list(user.roles)
    )
