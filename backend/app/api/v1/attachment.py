"""现场照片上传：POST /attachments（multipart 直传）。

与 ``/files/init`` 预签名流程的取舍：一期没有对象存储客户端，预签名无法闭环；
这里用 multipart 直传 + 本地目录（``ATTACHMENT_DIR``，默认 backend/var/attachments），
返回 attachment_id 供 ``/workorders/dispatch`` 的 ``attachment_ids`` 使用。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Header, Query, UploadFile, status

from app.api.deps import CurrentUser, DbSession
from app.api.v1.geometry import _require_idempotency_key
from app.schemas.workorder import AttachmentUploadResult
from app.services.attachment import create_attachment

router = APIRouter(prefix="/attachments", tags=["workorder"])


@router.post(
    "",
    response_model=AttachmentUploadResult,
    status_code=status.HTTP_201_CREATED,
    summary="上传现场照片（选填，派单前调用；同一构件同图复用）",
    responses={
        400: {"description": "缺少或非法的 Idempotency-Key（BT-10002）"},
        404: {"description": "构件不存在（BT-30001）"},
        422: {"description": "类型/大小不合规（BT-70010）"},
    },
)
async def upload_attachment(
    db: DbSession,
    user: CurrentUser,
    file: Annotated[UploadFile, File(description="仅 image/jpeg、image/png，<= 10 MB")],
    component_code: Annotated[str, Query(min_length=1, max_length=64)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AttachmentUploadResult:
    _require_idempotency_key(idempotency_key)
    result = await create_attachment(db, upload=file, component_code=component_code, principal=user)
    db.commit()
    return AttachmentUploadResult(**result)
