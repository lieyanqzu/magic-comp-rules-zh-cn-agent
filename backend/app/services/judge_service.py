"""裁判问答服务：编排 Agent、检索、持久化与缓存。

把过去散落在 API 层 / Agent 内部的 DB 写入、Redis 客户端获取、Agent 构造逻辑
都收拢到这里，让 API 路由保持精简，Agent 只关心 LLM 编排。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.judge_agent import JudgeAgent
from app.agent.schemas import JudgeResponse
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import JudgeQuery
from app.db.session import async_session_factory
from app.retrieval.hybrid_search import DefaultRuleSearcher

logger = get_logger(__name__)


def _normalize_question(question: str) -> str:
    """归一化问题用于缓存 key：去前后空白、压缩内部空白。"""
    return " ".join(question.split())


def _llm_cache_key(question: str, language: str) -> str:
    norm = _normalize_question(question)
    digest = hashlib.sha256(f"{norm}|{language}".encode("utf-8")).hexdigest()[:24]
    return f"llm_answer:{digest}"


class JudgeService:
    """裁判服务。每次请求构造一个实例，持有 db/redis 和一个 agent。"""

    def __init__(
        self,
        db: AsyncSession,
        redis: aioredis.Redis | None = None,
        *,
        request_id: str | None = None,
    ) -> None:
        self.db = db
        self.redis = redis
        self.request_id = request_id or uuid.uuid4().hex[:16]
        self.searcher = DefaultRuleSearcher(db, redis)
        self.agent = JudgeAgent(self.searcher, request_id=self.request_id)

    async def _read_llm_cache(self, question: str, language: str) -> JudgeResponse | None:
        if not (settings.llm_cache_enabled and self.redis):
            return None
        try:
            cached = await self.redis.get(_llm_cache_key(question, language))
            if cached:
                data = json.loads(cached)
                logger.info("LLM 响应缓存命中", request_id=self.request_id)
                return JudgeResponse(**data)
        except Exception:
            logger.warning("LLM 响应缓存读取失败", request_id=self.request_id)
        return None

    async def _write_llm_cache(self, question: str, language: str, response: JudgeResponse) -> None:
        if not (settings.llm_cache_enabled and self.redis):
            return
        # confidence=low 或 needs_human_judge 不缓存（可能是错误回答）
        if response.confidence == "low" or response.needs_human_judge:
            return
        try:
            payload = response.model_dump()
            payload.pop("latency_ms", None)
            await self.redis.set(
                _llm_cache_key(question, language),
                json.dumps(payload, ensure_ascii=False, default=str),
                ex=settings.llm_cache_ttl,
            )
        except Exception:
            logger.warning("LLM 响应缓存写入失败", request_id=self.request_id)

    async def ask(self, question: str, language: str = "zh-CN") -> JudgeResponse:
        """非流式：返回完整结构化回答。"""
        cached = await self._read_llm_cache(question, language)
        if cached is not None:
            cached.latency_ms = 0.0
            return cached

        start = time.monotonic()
        response = await self.agent.ask(question=question, language=language)
        elapsed = round((time.monotonic() - start) * 1000, 1)
        response.latency_ms = elapsed

        await self._log_query(question, response, elapsed)
        await self._write_llm_cache(question, language, response)
        return response

    async def ask_stream(
        self, question: str, language: str = "zh-CN"
    ) -> AsyncIterator[dict]:
        """流式：把 agent 事件原样转发，并在结束时记录日志。"""
        start = time.monotonic()
        final_payload: dict | None = None

        async for event in self.agent.ask_stream(question=question, language=language):
            yield event
            if event["type"] == "answer":
                final_payload = event["data"]

        elapsed = round((time.monotonic() - start) * 1000, 1)

        if final_payload is not None:
            try:
                response = JudgeResponse(**final_payload)
                response.latency_ms = elapsed
                await self._log_query(question, response, elapsed)
                await self._write_llm_cache(question, language, response)
            except Exception:
                logger.warning("流式日志写入失败", request_id=self.request_id)

        yield {"type": "done", "latency_ms": elapsed, "request_id": self.request_id}

    async def _log_query(
        self, question: str, response: JudgeResponse, elapsed_ms: float
    ) -> None:
        """记录问答日志。失败不影响主流程。

        使用独立 session，因为请求 session 可能在响应返回后就被关掉。
        """
        try:
            async with async_session_factory() as db:
                db.add(
                    JudgeQuery(
                        request_id=self.request_id,
                        question=question,
                        answer=response.answer,
                        model=settings.openai_model,
                        confidence=response.confidence,
                        used_rules=[r.model_dump() for r in response.rules],
                        used_cards=[c.model_dump() for c in response.cards],
                        latency_ms=elapsed_ms,
                        prompt_tokens=self.agent.prompt_tokens or None,
                        completion_tokens=self.agent.completion_tokens or None,
                        total_tokens=self.agent.total_tokens or None,
                        tool_rounds=self.agent.tool_rounds or None,
                        reasoning_summary=response.reasoning_summary,
                        needs_human_judge=response.needs_human_judge,
                    )
                )
                await db.commit()
        except Exception:
            logger.warning(
                "问答日志写入失败",
                question=question[:50],
                request_id=self.request_id,
            )
