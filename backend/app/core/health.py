"""健康检查：/healthz 进程存活，/readyz 依赖就绪。响应与 openapi.yaml 的 Readiness 一致。"""

from __future__ import annotations

import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.logging import current_trace_id, get_logger

router = APIRouter(tags=["health"])
log = get_logger()


@router.get("/healthz", summary="存活探针")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _check_db() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return "not_configured"
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=1) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return "ok"
    except Exception as exc:  # 依赖不可用时必须报告，不得吞掉
        log.warning("db check failed", extra={"bt_extra": {"error": str(exc)[:120]}})
        return "down"


@router.get("/readyz", summary="就绪探针")
def readyz() -> JSONResponse:
    db = _check_db()
    if db == "down":
        body = {
            "type": "https://bt.local/problem/unavailable",
            "title": "依赖未就绪",
            "status": 503,
            "code": "BT-90001",
            "traceId": current_trace_id(),
            "detail": "数据库不可达",
            "checks": {"db": db, "storage": "not_configured", "cache": "not_configured"},
        }
        return JSONResponse(
            status_code=503,
            content=body,
            media_type="application/problem+json",
            headers={"Retry-After": "10"},
        )
    return JSONResponse(
        status_code=200,
        content={
            "status": "ready",
            "checks": {"db": db, "storage": "not_configured", "cache": "not_configured"},
            "traceId": current_trace_id(),
        },
    )
