"""现场照片直传：落 attachment 表，派单时再改挂到工单。

为什么用 multipart 直传而不是 ``/files/init`` 预签名：
- 一期没有对象存储客户端（MinIO 未接入），预签名流程无法闭环；
- 直传实现简单、可幂等（同一构件下 sha256 相同直接复用已有附件）；
- 附件写入 ``attachment`` 表时先挂 ``COMPONENT``，派单事务内改挂 ``WORK_ORDER``，
  这样 T01 SUBMIT 的 ``attachments_count >= 1`` 守卫读到的就是真实关联行。

限制：仅 image/jpeg、image/png，单张 <= 10 MB，单次派单 <= 9 张；
Content-Type 以魔数为准，不信任客户端声明。
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.core.errors import api_error
from app.services.workorder import component_of, operator_uuid

MAX_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS = 9

# Content-Type -> (文件头魔数, 扩展名)
ALLOWED: dict[str, tuple[bytes, str]] = {
    "image/jpeg": (b"\xff\xd8\xff", ".jpg"),
    "image/png": (b"\x89PNG\r\n\x1a\n", ".png"),
}


def attachment_root() -> Path:
    configured = os.environ.get("ATTACHMENT_DIR")
    root = (
        Path(configured)
        if configured
        else Path(__file__).resolve().parents[2] / "var" / "attachments"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _result(row: Any) -> dict[str, Any]:
    return {
        "attachment_id": row.id,
        "file_name": row.file_name,
        "content_type": row.content_type,
        "size_bytes": int(row.size_bytes),
        "sha256": row.sha256,
    }


async def create_attachment(
    session: Session,
    *,
    upload: UploadFile,
    component_code: str,
    principal: Principal,
) -> dict[str, Any]:
    component = component_of(session, component_code)
    if component is None:
        raise api_error("BT-30001", f"构件不存在：{component_code}")

    content_type = (upload.content_type or "").lower()
    spec = ALLOWED.get(content_type)
    if spec is None:
        raise api_error(
            "BT-70010",
            f"不支持的附件类型：{content_type or '未知'}（仅支持 image/jpeg、image/png）",
        )

    chunks = bytearray()
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        chunks.extend(chunk)
        if len(chunks) > MAX_BYTES:
            raise api_error("BT-70010", f"附件超过大小上限 {MAX_BYTES // (1024 * 1024)} MB")
    if not chunks:
        raise api_error("BT-70010", "附件为空")

    magic, suffix = spec
    if not bytes(chunks[: len(magic)]) == magic:
        raise api_error("BT-70010", "附件内容与声明的 Content-Type 不符（魔数校验失败）")

    sha = hashlib.sha256(bytes(chunks)).hexdigest()

    existing = session.execute(
        text(
            "SELECT id, file_name, content_type, size_bytes, sha256 FROM attachment "
            "WHERE sha256 = :sha AND object_type = 'COMPONENT' AND object_id = :cid "
            "AND deleted_at IS NULL ORDER BY created_at LIMIT 1"
        ),
        {"sha": sha, "cid": component["id"]},
    ).first()
    if existing is not None:
        return _result(existing)

    relative = f"{datetime.now(UTC).strftime('%Y/%m')}/{uuid.uuid4().hex}{suffix}"
    target = attachment_root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(bytes(chunks))

    file_name = (upload.filename or f"photo{suffix}")[:255]
    row = session.execute(
        text(
            "INSERT INTO attachment "
            "(project_id, object_type, object_id, file_name, content_type, size_bytes, "
            " sha256, object_key, data_class, uploaded_by) "
            "VALUES (:project_id, 'COMPONENT', :object_id, :file_name, :content_type, "
            " :size_bytes, :sha256, :object_key, 'INTERNAL', :uploaded_by) "
            "RETURNING id, file_name, content_type, size_bytes, sha256"
        ),
        {
            "project_id": component["project_id"],
            "object_id": component["id"],
            "file_name": file_name,
            "content_type": content_type,
            "size_bytes": len(chunks),
            "sha256": sha,
            "object_key": relative,
            "uploaded_by": operator_uuid(principal),
        },
    ).one()
    return _result(row)


def link_attachments(
    session: Session,
    *,
    attachment_ids: list[uuid.UUID],
    order_id: uuid.UUID,
    component_id: uuid.UUID,
    project_id: uuid.UUID,
) -> int:
    """把预上传的构件附件改挂到工单；返回挂载数量（即 SUBMIT 守卫读到的附件数）。"""
    ids = list(dict.fromkeys(attachment_ids))
    if len(ids) > MAX_ATTACHMENTS:
        raise api_error("BT-70009", f"单次派单最多挂 {MAX_ATTACHMENTS} 张现场照片")
    if not ids:
        return 0
    found = session.execute(
        text(
            "SELECT id FROM attachment WHERE id = ANY(:ids) AND deleted_at IS NULL "
            "AND object_type = 'COMPONENT' AND object_id = :component_id "
            "AND project_id = :project_id"
        ),
        {"ids": ids, "component_id": component_id, "project_id": project_id},
    ).all()
    if len(found) != len(ids):
        raise api_error(
            "BT-70009",
            f"附件不可用：请求 {len(ids)} 个，可挂载 {len(found)} 个"
            "（须为同一构件预上传且未被其他工单占用）",
        )
    session.execute(
        text(
            "UPDATE attachment SET object_type = 'WORK_ORDER', object_id = :order_id "
            "WHERE id = ANY(:ids)"
        ),
        {"order_id": order_id, "ids": ids},
    )
    return len(ids)
