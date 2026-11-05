"""应用装配：只做配置、中间件、异常处理与路由注册。"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.components import router as components_router
from app.api.v1.geometry import router as geometry_router
from app.api.v1.pointcloud import router as pointcloud_router
from app.core.config import Settings
from app.core.errors import ERRORS, BizError
from app.core.health import router as health_router
from app.core.logging import bind_trace_id, current_trace_id, get_logger

log = get_logger()

PROBLEM_JSON = "application/problem+json"

# 参数校验失败错误码。刻意不复用 BT-10001（401 未认证）：否则前端会把 422 当登录失效。
# 若确需回滚成 BT-10001，只改这一行（同时把 ERRORS["BT-10001"] 的语义扩为 401/422 兼容）。
VALIDATION_ERROR_CODE = "BT-10003"

# 前端开发服地址。默认只放行 Vite 的 5173（localhost 与 127.0.0.1 两种写法），
# 生产环境用 CORS_ALLOW_ORIGINS（逗号分隔）显式覆盖，禁止默认放开 "*"。
CORS_ORIGINS_ENV = "CORS_ALLOW_ORIGINS"
DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def cors_origins() -> list[str]:
    raw = os.environ.get(CORS_ORIGINS_ENV)
    if raw is None:
        return list(DEFAULT_CORS_ORIGINS)
    return [item.strip() for item in raw.split(",") if item.strip()]


def json_safe(value: Any) -> Any:
    """把任意对象转成可被 ``json.dumps`` 序列化的形态。

    ``RequestValidationError.errors()`` 的 ``ctx`` 里可能是 ``ValueError`` 实例、
    ``bytes`` 或正则对象；直接塞进 JSONResponse 会让 handler 自身 500，故先清洗。
    """
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [json_safe(item) for item in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def format_loc(loc: tuple[Any, ...] | list[Any]) -> str:
    """把 pydantic 的 loc 拼成人能读的路径：("body","level") → ``body.level``。

    列表下标用方括号：("body","items",0,"x") → ``body.items[0].x``。
    """
    text = ""
    for part in loc:
        if isinstance(part, int):
            text += f"[{part}]"
            continue
        text += ("" if not text else ".") + str(part)
    return text or "request"


def sanitize_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """只保留 field/reason/type，并整体做一次 JSON 可序列化清洗。

    刻意不输出 pydantic 原始 ``ctx``：里面可能是异常实例或大对象，
    既不可序列化，也不适合直接暴露给前端。
    """
    items: list[dict[str, Any]] = []
    for err in exc.errors():
        item: dict[str, Any] = {
            "field": format_loc(err.get("loc") or ()),
            "reason": str(err.get("msg", "参数非法")),
        }
        if err.get("type"):
            item["type"] = str(err["type"])
        items.append(json_safe(item))
    return items


def validation_detail(errors: list[dict[str, Any]]) -> str:
    if not errors:
        return "请求参数未通过校验"
    if len(errors) == 1:
        return f"{errors[0]['field']}: {errors[0]['reason']}"
    fields = ", ".join(str(item["field"]) for item in errors[:5])
    more = "" if len(errors) <= 5 else f" 等 {len(errors)} 项"
    return f"请求参数未通过校验：{fields}{more}"


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings.from_env()
    app = FastAPI(
        title="公路桥隧结构智能检测与数字孪生运维平台 API",
        version=cfg.api_version,
        description="契约事实源见 contracts/openapi.yaml",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # 前端要读的响应头：任务 Location、点云 Range 取数、追踪 id
        expose_headers=["Location", "Content-Range", "Accept-Ranges", "ETag", "X-Request-Id"],
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
        if exc.errors:
            body["errors"] = exc.errors
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse(
            status_code=exc.status, content=body, media_type=PROBLEM_JSON, headers=headers
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """query/path/body/header 的校验失败统一转 problem+json。

        与 BizError 处理器互不覆盖：FastAPI 按异常类型精确分派，BizError 不是
        ValidationError 的子类，两条链路彼此独立。校验失败一律 422（不用 400），
        错误码用 BT-10003，避免与 401 的 BT-10001 混淆。
        """
        spec = ERRORS[VALIDATION_ERROR_CODE]
        errors = sanitize_errors(exc)
        status_code = getattr(exc, "status_code", None) or spec.status
        body = {
            "type": spec.type_uri,
            "title": spec.title,
            "status": status_code,
            "code": spec.code,
            "traceId": current_trace_id(),
            "detail": validation_detail(errors),
            "instance": str(request.url.path),
            "errors": errors,
        }
        return JSONResponse(status_code=status_code, content=body, media_type=PROBLEM_JSON)

    app.include_router(health_router, prefix=f"/api/{cfg.api_version}")
    app.include_router(health_router)
    app.include_router(components_router, prefix=f"/api/{cfg.api_version}")
    app.include_router(pointcloud_router, prefix=f"/api/{cfg.api_version}")
    app.include_router(geometry_router, prefix=f"/api/{cfg.api_version}")

    log.info("app assembled", extra={"api_version": cfg.api_version})
    return app


app = create_app()
