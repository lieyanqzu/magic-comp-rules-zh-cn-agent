"""FastAPI 应用入口。"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.api import cards, health, judge, rules
from app.core.config import settings
from app.core.errors import AppError, app_error_handler, generic_error_handler
from app.core.logging import setup_logging
from app.core.rate_limit import RateLimitMiddleware
from app.db.session import engine

# 安全响应头
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers[key] = value
        return response


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    _app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    yield
    await _app.state.redis.close()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="万智牌中文规则裁判 API",
        description="用于回答中文万智牌规则问题的 AI Agent 后端服务",
        version="0.1.0",
        lifespan=lifespan,
    )

    # 异常处理
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, generic_error_handler)

    # 中间件（后注册的先执行）
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware)

    # CORS
    origins = [o.strip() for o in settings.cors_origins.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 路由
    app.include_router(health.router, tags=["健康检查"])
    app.include_router(judge.router, prefix="/v1/judge", tags=["规则裁判"])
    app.include_router(rules.router, prefix="/v1/rules", tags=["规则检索"])
    app.include_router(cards.router, prefix="/v1/cards", tags=["牌张查询"])

    return app


app = create_app()
