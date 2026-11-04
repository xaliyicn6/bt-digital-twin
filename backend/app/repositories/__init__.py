"""仓储层：只做 SQL，不含业务规则。"""

from app.repositories.component import ComponentRepository, StructRef

__all__ = ["ComponentRepository", "StructRef"]
