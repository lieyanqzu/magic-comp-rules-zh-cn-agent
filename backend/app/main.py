"""FastAPI 应用入口。"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import cards, health, judge, rules
from app.core.config import settings
from app.core.errors import AppError, app_error_handler, generic_error_handler
from app.core.logging import setup_logging
from app.db.session import engine

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


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
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, generic_error_handler)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    # API 路由
    app.include_router(health.router, tags=["健康检查"])
    app.include_router(judge.router, prefix="/v1/judge", tags=["规则裁判"])
    app.include_router(rules.router, prefix="/v1/rules", tags=["规则检索"])
    app.include_router(cards.router, prefix="/v1/cards", tags=["牌张查询"])

    # 前端页面
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
