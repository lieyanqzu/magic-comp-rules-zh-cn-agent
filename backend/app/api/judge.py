"""规则裁判问答接口。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.schemas import JudgeRequest, JudgeResponse
from app.core.auth import verify_api_key
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.services.judge_service import JudgeService, LLMOverride

router = APIRouter(dependencies=[Depends(verify_api_key)])
logger = get_logger(__name__)


def get_redis(request: Request) -> aioredis.Redis | None:
    return getattr(request.app.state, "redis", None)


def _llm_override_from_headers(
    api_key: str | None,
    base_url: str | None,
    model: str | None,
    max_tokens: str | None,
) -> LLMOverride:
    """构造 LLMOverride 时去除空白；不打日志、不带 header 值。

    安全注意：返回的对象会传给 service 层；任何异常路径都不应让 header 值进 logger。
    max_tokens 是非敏感数值，但解析失败时静默忽略，避免恶意 header 造成 500。
    """

    def _clean(s: str | None) -> str | None:
        if s is None:
            return None
        s = s.strip()
        return s or None

    def _parse_int(s: str | None) -> int | None:
        cleaned = _clean(s)
        if cleaned is None:
            return None
        try:
            v = int(cleaned)
        except ValueError:
            return None
        # 合法范围：[1, 1_000_000]。上限给得很宽，由上游 LLM provider 自己 4xx 拒掉过大值
        if v < 1 or v > 1_000_000:
            return None
        return v

    return LLMOverride(
        api_key=_clean(api_key),
        base_url=_clean(base_url),
        model=_clean(model),
        max_tokens=_parse_int(max_tokens),
    )


@router.post("/ask", response_model=JudgeResponse)
async def ask_judge(
    request: JudgeRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis | None = Depends(get_redis),
    x_llm_api_key: str | None = Header(None, alias="X-LLM-Api-Key"),
    x_llm_base_url: str | None = Header(None, alias="X-LLM-Base-URL"),
    x_llm_model: str | None = Header(None, alias="X-LLM-Model"),
    x_llm_max_tokens: str | None = Header(None, alias="X-LLM-Max-Tokens"),
) -> JudgeResponse:
    """非流式接口，返回完整结构化回答。

    可选请求头（前端自带 LLM 配置，BYOK）：
    - X-LLM-Api-Key：覆盖服务器 OPENAI_API_KEY
    - X-LLM-Base-URL：覆盖 base url（如使用 azure / 第三方代理）
    - X-LLM-Model：覆盖模型名
    - X-LLM-Max-Tokens：覆盖单次响应 max_tokens（整数；非敏感参数）

    api_key/base_url/model 任意一项设置后，该次请求绕过共享 LLM 缓存；
    服务器不会记录这些字段任何明文。max_tokens 是数值参数，不影响缓存与日志策略。
    """
    request_id = req.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    override = _llm_override_from_headers(x_llm_api_key, x_llm_base_url, x_llm_model, x_llm_max_tokens)
    service = JudgeService(db, redis=redis, request_id=request_id, llm_override=override)
    history = [m.model_dump() for m in request.history] if request.history else None
    return await service.ask(question=request.question, language=request.language, history=history)


async def _heartbeat_wrapper(stream: AsyncIterator[dict], interval: float) -> AsyncIterator[str]:
    """将 agent 事件流包成 SSE。空闲超过 interval 秒就发心跳，避免反代切断长连接。"""
    queue: asyncio.Queue = asyncio.Queue()
    DONE = object()

    async def producer() -> None:
        try:
            async for event in stream:
                await queue.put(event)
        except Exception as exc:
            await queue.put({"type": "error", "content": str(exc)})
        finally:
            await queue.put(DONE)

    task = asyncio.create_task(producer())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                # SSE 注释行作心跳，浏览器/EventSource 会忽略
                yield ": heartbeat\n\n"
                continue
            if item is DONE:
                return
            yield f"data: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"
            if isinstance(item, dict) and item.get("type") == "error":
                return
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


@router.post("/stream")
async def stream_judge(
    request: JudgeRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis | None = Depends(get_redis),
    x_llm_api_key: str | None = Header(None, alias="X-LLM-Api-Key"),
    x_llm_base_url: str | None = Header(None, alias="X-LLM-Base-URL"),
    x_llm_model: str | None = Header(None, alias="X-LLM-Model"),
    x_llm_max_tokens: str | None = Header(None, alias="X-LLM-Max-Tokens"),
) -> StreamingResponse:
    """流式接口（SSE），逐步返回推理过程和工具调用。

    可选请求头同 /ask（X-LLM-Api-Key / X-LLM-Base-URL / X-LLM-Model / X-LLM-Max-Tokens）。
    """
    request_id = req.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    override = _llm_override_from_headers(x_llm_api_key, x_llm_base_url, x_llm_model, x_llm_max_tokens)
    service = JudgeService(db, redis=redis, request_id=request_id, llm_override=override)
    history = [m.model_dump() for m in request.history] if request.history else None

    async def event_stream() -> AsyncIterator[str]:
        async for chunk in _heartbeat_wrapper(
            service.ask_stream(question=request.question, language=request.language, history=history),
            interval=settings.sse_heartbeat_interval,
        ):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 关闭 nginx 缓冲，确保流式实时下发
            "X-Request-ID": request_id,
        },
    )
