"""测试配置。"""

from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, TypeDecorator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base
from app.db.session import get_db
from app.main import create_app

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


# 用 JSON 替换 JSONB，跳过 Vector 类型，使 SQLite 兼容
class JsonFallback(TypeDecorator):
    impl = JSON
    cache_ok = True


@pytest.fixture(autouse=True)
def _disable_external_retrieval_apis(monkeypatch):
    """测试不应触发真实 reranker / embedding 网络请求。

    即便环境里有 .env 配了真 key，也强制走 fallback 路径，
    保证测试用例对依赖外部服务零耦合。
    """
    monkeypatch.setattr(settings, "reranker_enabled", False)


@pytest.fixture(autouse=True)
async def _patch_types():
    """将 PostgreSQL 特有类型替换为 SQLite 兼容类型。"""
    from app.db import models
    patches = []
    for model_cls in [models.RuleChunk, models.CardCache, models.JudgeQuery]:
        for col in model_cls.__table__.columns:
            col_type = type(col.type)
            if col_type.__name__ == "JSONB":
                patches.append(patch.object(col, "type", JsonFallback()))
            elif col_type.__name__ == "Vector":
                patches.append(patch.object(col, "type", JsonFallback()))
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with test_session_factory() as session:
        yield session


@pytest.fixture(autouse=True)
async def setup_db() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with test_session_factory() as session:
        yield session
