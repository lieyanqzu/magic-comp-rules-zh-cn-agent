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
from dataclasses import dataclass

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.judge_agent import JudgeAgent, _build_client
from app.agent.schemas import JudgeResponse
from app.core.config import settings
from app.core.logging import get_logger
from app.core.safety import check_input_safety
from app.db.models import JudgeQuery
from app.db.session import async_session_factory
from app.retrieval.hybrid_search import DefaultRuleSearcher

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class LLMOverride:
    """前端自带的 LLM 配置：来自请求头 X-LLM-Api-Key / X-LLM-Base-URL / X-LLM-Model / X-LLM-Max-Tokens。

    安全性：
    - api_key / base_url / model 任何字段都不会写日志或入库。
    - max_tokens 是非敏感的数值参数，但与上面三项同源，统一用 LLMOverride 管理。
    - __repr__ 强制脱敏，防止误入 traceback / structlog 上下文。
    - 任意字段为 None 表示该字段沿用服务器默认值。
    """

    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    max_tokens: int | None = None

    def is_active(self) -> bool:
        return any([self.api_key, self.base_url, self.model, self.max_tokens])

    def is_byok(self) -> bool:
        """仅判断是否带了用户自带 LLM 凭证（影响缓存与日志策略）。max_tokens 不算 BYOK。"""
        return any([self.api_key, self.base_url, self.model])

    def __repr__(self) -> str:  # pragma: no cover - safety guard
        # 永远不暴露任何字段值，即使是 base_url / model 也属于用户自定义信息
        return f"LLMOverride(byok={self.is_byok()}, max_tokens={'set' if self.max_tokens else 'default'})"


# 用户启用 BYOK 时写入 judge_queries.model 的占位值，不存任何用户自定义内容
_BYOK_MARKER = "(byok)"


def _normalize_question(question: str) -> str:
    """归一化问题用于缓存 key：去前后空白、压缩内部空白。"""
    return " ".join(question.split())


def _llm_cache_key(question: str, language: str) -> str:
    norm = _normalize_question(question)
    digest = hashlib.sha256(f"{norm}|{language}".encode("utf-8")).hexdigest()[:24]
    return f"llm_answer:{digest}"


def _build_refusal_response(reason: str) -> JudgeResponse:
    """L1 拦截时构造的固定拒绝回复。绕过 LLM，零 token 成本。"""
    return JudgeResponse(
        answer=reason,
        summary="拒绝该请求",
        confidence="high",
        cards=[],
        rules=[],
        reasoning_summary="输入未通过安全检查，未调用 LLM。",
        needs_human_judge=False,
        latency_ms=0.0,
    )


class JudgeService:
    """裁判服务。每次请求构造一个实例，持有 db/redis 和一个 agent。"""

    def __init__(
        self,
        db: AsyncSession,
        redis: aioredis.Redis | None = None,
        *,
        request_id: str | None = None,
        llm_override: LLMOverride | None = None,
    ) -> None:
        self.db = db
        self.redis = redis
        self.request_id = request_id or uuid.uuid4().hex[:16]
        self.searcher = DefaultRuleSearcher(db, redis)
        self.llm_override = llm_override or LLMOverride()
        # 用户自带 key/base_url 时构造一次性 client，否则复用模块单例（连接池更高效）
        client = _build_client(self.llm_override.api_key, self.llm_override.base_url)
        self.agent = JudgeAgent(
            self.searcher,
            client=client,
            model=self.llm_override.model,
            max_tokens=self.llm_override.max_tokens,
            request_id=self.request_id,
        )

    async def _read_llm_cache(self, question: str, language: str) -> JudgeResponse | None:
        # 用户自带 LLM 时跳过缓存：不同 key/model 的回答可能不同，且不应让一个用户读到另一个用户的结果
        if self.llm_override.is_byok():
            return None
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
        # 同上，用户自带 LLM 时不写共享缓存
        if self.llm_override.is_byok():
            return
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

    async def ask(
        self,
        question: str,
        language: str = "zh-CN",
        history: list[dict] | None = None,
    ) -> JudgeResponse:
        """非流式：返回完整结构化回答。"""
        # L1 输入预过滤：明显的滥用 / injection 在 LLM 调用前直接拦截，零 token 成本
        verdict = check_input_safety(question)
        if not verdict.allowed:
            logger.info(
                "L1 输入被拦截",
                request_id=self.request_id,
                question_prefix=question[:50],
            )
            response = _build_refusal_response(verdict.reason)
            await self._log_query(question, response, 0.0)
            return response

        # 多轮对话不命中共享缓存：相同问题在不同上下文里回答可能不同
        has_history = bool(history)
        if not has_history:
            cached = await self._read_llm_cache(question, language)
            if cached is not None:
                cached.latency_ms = 0.0
                return cached

        start = time.monotonic()
        response = await self.agent.ask(question=question, language=language, history=history)
        elapsed = round((time.monotonic() - start) * 1000, 1)
        response.latency_ms = elapsed

        await self._log_query(question, response, elapsed)
        if not has_history:
            await self._write_llm_cache(question, language, response)
        return response

    async def ask_stream(
        self,
        question: str,
        language: str = "zh-CN",
        history: list[dict] | None = None,
    ) -> AsyncIterator[dict]:
        """流式：把 agent 事件原样转发，并在结束时记录日志。"""
        # L1 输入预过滤：拦截后发一个 answer + done 事件，前端无需特殊处理
        verdict = check_input_safety(question)
        if not verdict.allowed:
            logger.info(
                "L1 输入被拦截",
                request_id=self.request_id,
                question_prefix=question[:50],
            )
            yield {"type": "start", "question": question, "request_id": self.request_id}
            yield {"type": "thinking", "content": "输入未通过安全检查，已拒绝。"}
            response = _build_refusal_response(verdict.reason)
            yield {"type": "answer", "data": response.model_dump()}
            await self._log_query(question, response, 0.0)
            yield {"type": "done", "latency_ms": 0.0, "request_id": self.request_id}
            return

        has_history = bool(history)
        start = time.monotonic()
        final_payload: dict | None = None

        async for event in self.agent.ask_stream(question=question, language=language, history=history):
            yield event
            if event["type"] == "answer":
                final_payload = event["data"]

        elapsed = round((time.monotonic() - start) * 1000, 1)

        if final_payload is not None:
            try:
                response = JudgeResponse(**final_payload)
                response.latency_ms = elapsed
                await self._log_query(question, response, elapsed)
                if not has_history:
                    await self._write_llm_cache(question, language, response)
            except Exception:
                logger.warning("流式日志写入失败", request_id=self.request_id)

        yield {"type": "done", "latency_ms": elapsed, "request_id": self.request_id}

    async def _log_query(
        self, question: str, response: JudgeResponse, elapsed_ms: float
    ) -> None:
        """记录问答日志。失败不影响主流程。

        使用独立 session，因为请求 session 可能在响应返回后就被关掉。
        安全：用户自带 LLM (BYOK) 时，model 字段写占位符；
        永不入库 api_key / base_url / 用户提供的 model 名。
        """
        # BYOK 启用时一律写占位符，不区分用户具体覆盖了哪些字段（任意字段都视为隐私）
        # max_tokens 是非敏感数值，不会触发占位符
        model_for_log = _BYOK_MARKER if self.llm_override.is_byok() else settings.openai_model
        try:
            async with async_session_factory() as db:
                db.add(
                    JudgeQuery(
                        request_id=self.request_id,
                        question=question,
                        answer=response.answer,
                        model=model_for_log,
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
