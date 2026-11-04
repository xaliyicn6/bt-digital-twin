"""业务异常统一出口：problem+json（RFC 9457）由 main.py 的异常处理器渲染。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BizError(Exception):
    code: str
    status: int
    detail: str
    title: str = "业务校验失败"
    type_uri: str = "https://bt.local/problem/validation"
    errors: list[dict[str, str]] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"
