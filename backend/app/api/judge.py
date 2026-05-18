"""规则裁判问答接口。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.schemas import JudgeRequest, JudgeResponse
from app.core.auth import verify_api_key
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.services.judge_service import JudgeService

router = APIRouter(dependencies=[Depends(verify_api_key)])
logger = get_logger(__name__)


def get_redis(request: Request) -> aioredis.Redis | None:
    return getattr(request.app.state, "redis", None)


@router.post("/ask", response_model=JudgeResponse)
async def ask_judge(
    request: JudgeRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis | None = Depends(get_redis),
) -> JudgeResponse:
    """非流式接口，返回完整结构化回答。"""
    request_id = req.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    service = JudgeService(db, redis=redis, request_id=request_id)
    return await service.ask(question=request.question, language=request.language)


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
) -> StreamingResponse:
    """流式接口（SSE），逐步返回推理过程和工具调用。"""
    request_id = req.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    service = JudgeService(db, redis=redis, request_id=request_id)

    async def event_stream() -> AsyncIterator[str]:
        async for chunk in _heartbeat_wrapper(
            service.ask_stream(question=request.question, language=request.language),
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
