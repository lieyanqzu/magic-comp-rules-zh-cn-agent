"""FastAPI 应用入口。"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.api import cards, health, judge, rules
from app.core.config import settings
from app.core.errors import AppError, app_error_handler, generic_error_handler
from app.core.logging import get_logger, setup_logging
from app.core.rate_limit import RateLimitMiddleware
from app.db.session import engine

logger = get_logger(__name__)

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


async def _run_auto_ingest() -> None:
    """启动时增量入库。

    在后台运行：
    - 不阻塞应用启动；旧数据在新数据 commit 前依然可查。
    - 跑完会按 content_hash 仅更新变化的 chunk，并删除孤儿（git 子模块更新后规则改名/删除时清理）。
    - 任何异常吞掉记 warning，不影响在线请求。
    """
    try:
        # 延迟 import，避免环依赖与 ingest 模块的副作用在导入期触发
        from app.retrieval.ingest_rules import ingest_all

        logger.info(
            "启动时自动入库开始",
            embeddings=settings.auto_ingest_embeddings,
        )
        await ingest_all(
            generate_embeddings=settings.auto_ingest_embeddings,
            rebuild=False,
        )
        logger.info("启动时自动入库完成")
    except Exception as exc:
        logger.warning("启动时自动入库失败", error=str(exc)[:200])


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    _app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    ingest_task: asyncio.Task | None = None
    if settings.auto_ingest_on_startup:
        # 后台异步执行，不阻塞 startup
        ingest_task = asyncio.create_task(_run_auto_ingest(), name="auto_ingest_on_startup")

    try:
        yield
    finally:
        if ingest_task is not None and not ingest_task.done():
            logger.info("关闭中：等待 auto_ingest 任务结束")
            ingest_task.cancel()
            try:
                await ingest_task
            except (asyncio.CancelledError, Exception):
                pass
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

    # 前端静态资源（生产部署单镜像方案）。必须在所有 API 路由之后挂载：
    # StaticFiles 会捕获所有未匹配 /v1/* 与 /health 的请求并返回 index.html。
    # 设置为空时不挂载（开发环境前端跑独立 Vite dev server）。
    if settings.frontend_dist_dir:
        dist_path = Path(settings.frontend_dist_dir)
        if dist_path.is_dir() and (dist_path / "index.html").is_file():
            # html=True 让 / 返回 index.html，子路径找不到时也回落到 index.html，
            # 适配未来加 client-side router（比如分享链接 /q/abc）
            app.mount("/", StaticFiles(directory=dist_path, html=True), name="frontend")
            logger.info("已挂载前端静态资源", path=str(dist_path))
        else:
            logger.warning("frontend_dist_dir 配置但目录不存在或缺 index.html", path=str(dist_path))

    return app


app = create_app()
