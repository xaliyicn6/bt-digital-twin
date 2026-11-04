"""结构化日志：每行必须是 JSON，且必带 traceId（AGENTS.md 强制规则 6）。"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

TRACE_ID: ContextVar[str] = ContextVar("trace_id", default="-")


def bind_trace_id(trace_id: str) -> None:
    TRACE_ID.set(trace_id)


def current_trace_id() -> str:
    return TRACE_ID.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            # 本地开发机为 3.10，暂不能用 datetime.UTC；CI 与生产为 3.11
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),  # noqa: UP017
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "traceId": TRACE_ID.get(),
        }
        extra = getattr(record, "bt_extra", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_configured = False


def get_logger(name: str = "app") -> logging.Logger:
    global _configured
    logger = logging.getLogger(name)
    if not _configured:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root = logging.getLogger("app")
        root.handlers.clear()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        root.propagate = False
        _configured = True
    return logger
