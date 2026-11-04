"""构件服务：编码生成、层级/父节点校验、软删除与事务边界。

契约：``contracts/openapi.yaml`` 的 /components 与 /components/{code}；
DDL：``db/schema.sql`` 的 component 表（冻结，不得改）。
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import api_error
from app.models.component import INPUT_SRID, STORAGE_SRID, Component
from app.repositories.component import ComponentRepository
from app.schemas.component import ComponentCreate, ComponentList, ComponentOut, GeoJSONPoint

MAX_SEQUENCE = 999


def build_component_code(prefix: str, seq: int) -> str:
    """前缀段 + 3 位流水号：("B001-K01-PIR", 1) → "B001-K01-PIR-001"。

    边界：seq 必须 1–999（定长三位补零）。超出 999 不扩位——扩位会破坏按编码排序
    与人工核对习惯，调用方应转 409 BT-30004。
    """
    if not 1 <= seq <= MAX_SEQUENCE:
        raise ValueError(f"流水号必须在 1–{MAX_SEQUENCE} 之间，当前 {seq}")
    return f"{prefix}-{seq:03d}"


def next_sequence(existing_codes: Iterable[str], prefix: str) -> int:
    """同前缀内取最大值 + 1（首条为 1）。

    仅保留作离线估算/校验工具，**不再作为生产取号路径**：
    生产取号由 ``ComponentRepository.next_sequence`` 用计数表原子自增完成。
    只把“前缀后紧跟的纯数字段”当作流水号，带部位段的编码前缀必须显式含部位。
    """
    head = f"{prefix}-"
    max_seq = 0
    for code in existing_codes:
        if not code.startswith(head):
            continue
        tail = code[len(head) :]
        if tail.isdigit():
            max_seq = max(max_seq, int(tail))
    return max_seq + 1


def encode_cursor(level: int, component_code: str) -> str:
    raw = f"{level}|{component_code}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[int, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        level_str, code = raw.split("|", 1)
        level = int(level_str)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise api_error("BT-10002", "cursor 非法，请使用上一页返回的 next_cursor") from exc
    if not code or not 1 <= level <= 4:
        raise api_error("BT-10002", "cursor 非法，请使用上一页返回的 next_cursor")
    return level, code


def _constraint_name(exc: IntegrityError) -> str:
    diag = getattr(exc.orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return "" if name is None else str(name)


class ComponentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = ComponentRepository(db)

    # ------------------------------------------------------------------ 创建
    def create_component(self, comp_in: ComponentCreate) -> Component:
        payload = self._resolve_parent_and_scope(comp_in)
        auto_code = payload.component_code is None
        attempts = 3 if auto_code else 1
        last_error: IntegrityError | None = None

        for _ in range(attempts):
            if auto_code:
                prefix = payload.code_prefix
                assert payload.project_id is not None  # 由 _resolve_parent_and_scope 保证
                seq = self.repo.next_sequence(payload.project_id, prefix)
                try:
                    code = build_component_code(prefix, seq)
                except ValueError as exc:
                    raise api_error(
                        "BT-30004", f"前缀 {prefix} 的流水号已用尽（上限 {MAX_SEQUENCE}）"
                    ) from exc
                payload = payload.model_copy(update={"component_code": code})

            try:
                comp = self.repo.create_component(payload)
                self.db.commit()
            except IntegrityError as exc:
                self.db.rollback()
                if "uq_component_code" in _constraint_name(exc) or "component_code" in str(exc):
                    last_error = exc
                    # 正常路径撞不到唯一索引：只有计数表与台账漂移（如历史导入绕过计数表）
                    # 才可能发生，最多重试 3 次重新取号；显式指定编码的直接 409。
                    if auto_code:
                        continue
                    raise api_error(
                        "BT-30002",
                        f"构件编码 {payload.component_code} 已存在",
                        errors=[{"field": "component_code", "message": "编码重复"}],
                    ) from exc
                raise api_error("BT-30003", "构件写入违反数据库约束（结构归属或层级）") from exc
            self.db.refresh(comp)
            return comp

        raise api_error(
            "BT-30002",
            "构件编码连续 3 次冲突，请检查 component_code_seq 与台账是否漂移",
        ) from last_error

    def _resolve_parent_and_scope(self, comp_in: ComponentCreate) -> ComponentCreate:
        if comp_in.level == 1:
            if comp_in.parent_id is not None:
                raise api_error(
                    "BT-30003",
                    "L1 构件不允许有父节点",
                    errors=[{"field": "parent_id", "message": "L1 必须为空"}],
                )
            if comp_in.project_id is None or comp_in.struct_kind is None:
                raise api_error("BT-30003", "L1 构件必须提供 project_id 与 struct_kind")
            if (comp_in.bridge_id is None) == (comp_in.tunnel_id is None):
                raise api_error("BT-30003", "L1 构件必须且只能提供 bridge_id 或 tunnel_id 之一")
            return comp_in

        if comp_in.parent_id is None:
            raise api_error(
                "BT-30003",
                f"L{comp_in.level} 构件必须提供 parent_id",
                errors=[{"field": "parent_id", "message": "缺失"}],
            )
        parent = self.repo.get_by_id(comp_in.parent_id)
        if parent is None:
            raise api_error("BT-30001", f"父节点 {comp_in.parent_id} 不存在或已删除")
        if parent.level + 1 != comp_in.level:
            raise api_error(
                "BT-30003",
                f"层级非法：父节点是 L{parent.level}，子节点应为 L{parent.level + 1}",
                errors=[{"field": "level", "message": "必须比父级大 1"}],
            )
        inherited: dict[str, Any] = {
            "project_id": parent.project_id,
            "struct_kind": parent.struct_kind,
            "bridge_id": parent.bridge_id,
            "tunnel_id": parent.tunnel_id,
        }
        for field, expected in inherited.items():
            given = getattr(comp_in, field)
            if given is not None and given != expected:
                raise api_error(
                    "BT-30003",
                    f"{field} 与父节点不一致，子节点必须继承父节点归属",
                    errors=[{"field": field, "message": "与父节点不一致"}],
                )
        return comp_in.model_copy(update=inherited)

    # ------------------------------------------------------------------ 查询
    def list_components(
        self,
        *,
        struct_code: str,
        component_type: str | None,
        level: int | None,
        parent_id: uuid.UUID | None,
        project_id: uuid.UUID | None,
        limit: int,
        cursor: str | None,
    ) -> ComponentList:
        refs = self.repo.find_struct(struct_code, project_id)
        if not refs:
            raise api_error("BT-30001", f"结构编码 {struct_code} 不存在或已删除")
        if len(refs) > 1:
            raise api_error(
                "BT-30003",
                f"结构编码 {struct_code} 在多个项目下重复，请显式传 project_id",
                errors=[{"field": "project_id", "message": "必填"}],
            )
        struct_id = refs[0].id
        after = decode_cursor(cursor) if cursor else None

        rows = self.repo.list_components(
            struct_id,
            level,
            component_type,
            parent_id=parent_id,
            after=after,
            limit=limit + 1,  # 多取一条判断是否还有下一页
        )
        page = rows[:limit]
        geoms = self.repo.geoms_by_codes([comp.component_code for comp in page])
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = encode_cursor(last.level, last.component_code)
        return ComponentList(
            items=[self._to_out(comp, geoms.get(comp.component_code)) for comp in page],
            next_cursor=next_cursor,
        )

    def get_component(self, code: str) -> ComponentOut:
        comp = self.repo.get_by_code(code)
        if comp is None:
            raise api_error("BT-30001", f"构件 {code} 不存在或已删除")
        entry = self.repo.geoms_by_codes([code]).get(code)
        return self._to_out(comp, entry)

    # ------------------------------------------------------------------ 删除
    def delete_component(self, code: str) -> None:
        """软删除；重复删除幂等（第二次不再改 deleted_at，也不报 404）。"""
        comp = self.repo.get_by_code(code, include_deleted=True)
        if comp is None:
            raise api_error("BT-30001", f"构件 {code} 不存在")
        self.repo.soft_delete(comp)
        self.db.commit()

    # ------------------------------------------------------------------ 映射
    @staticmethod
    def _to_out(comp: Component, entry: dict[str, Any] | None) -> ComponentOut:
        point = entry.get("geom") if entry else None
        solid = entry.get("geom_solid") if entry else None
        return ComponentOut(
            id=comp.id,
            component_code=comp.component_code,
            parent_id=comp.parent_id,
            level=comp.level,
            component_type=comp.component_type,
            pier_type=comp.pier_type,
            design_axis_ref=comp.design_axis_ref,
            srid=STORAGE_SRID,
            updated_at=comp.updated_at,
            geom=GeoJSONPoint.model_validate(point) if point else None,
            geom_srid=INPUT_SRID if point else None,
            geom_solid=solid,
        )
