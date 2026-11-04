"""应用装配：只做配置、中间件、异常处理与路由注册。"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import Settings
from app.core.errors import BizError
from app.core.health import router as health_router
from app.core.logging import bind_trace_id, current_trace_id, get_logger

log = get_logger()

PROBLEM_JSON = "application/problem+json"


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings.from_env()
    app = FastAPI(
        title="公路桥隧结构智能检测与数字孪生运维平台 API",
        version=cfg.api_version,
        description="契约事实源见 contracts/openapi.yaml",
    )

    @app.middleware("http")
    async def trace_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[JSONResponse]]
    ) -> JSONResponse:
        trace_id = request.headers.get("X-Request-Id") or os.urandom(8).hex()
        bind_trace_id(trace_id)
        response = await call_next(request)
        response.headers["X-Request-Id"] = trace_id
        return response

    @app.exception_handler(BizError)
    async def biz_error_handler(request: Request, exc: BizError) -> JSONResponse:
        body = {
            "type": exc.type_uri,
            "title": exc.title,
            "status": exc.status,
            "code": exc.code,
            "traceId": current_trace_id(),
            "detail": exc.detail,
            "instance": str(request.url.path),
        }
        return JSONResponse(status_code=exc.status, content=body, media_type=PROBLEM_JSON)

    app.include_router(health_router, prefix=f"/api/{cfg.api_version}")
    app.include_router(health_router)

    log.info("app assembled", extra={"api_version": cfg.api_version})
    return app


app = create_app()
