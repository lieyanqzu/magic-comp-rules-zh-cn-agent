"""规则裁判问答接口。"""

import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.judge_agent import JudgeAgent
from app.agent.schemas import JudgeRequest, JudgeResponse
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.db.models import JudgeQuery

router = APIRouter()
logger = get_logger(__name__)


@router.post("/ask", response_model=JudgeResponse)
async def ask_judge(request: JudgeRequest, db: AsyncSession = Depends(get_db)) -> JudgeResponse:
    """非流式接口，返回完整结构化回答。"""
    start = time.monotonic()
    agent = JudgeAgent()
    response = await agent.ask(question=request.question, language=request.language)
    response.latency_ms = round((time.monotonic() - start) * 1000, 1)

    # 记录日志
    db.add(JudgeQuery(
        question=request.question, answer=response.answer,
        model=settings.openai_model, confidence=response.confidence,
        used_rules=[r.model_dump() for r in response.rules],
        used_cards=[c.model_dump() for c in response.cards],
        latency_ms=response.latency_ms,
        reasoning_summary=response.reasoning_summary,
        needs_human_judge=response.needs_human_judge,
    ))
    await db.commit()
    return response


@router.post("/stream")
async def stream_judge(request: JudgeRequest, db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    """流式接口（SSE），逐步返回推理过程和工具调用。"""
    agent = JudgeAgent()

    async def event_generator():
        start = time.monotonic()
        final_response = None

        async for event in agent.ask_stream(question=request.question, language=request.language):
            # 发送 SSE 事件
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            # 保存最终回答
            if event["type"] == "answer":
                final_response = event["data"]
            elif event["type"] == "error":
                yield f"data: {json.dumps({'type': 'done', 'error': event['content']}, ensure_ascii=False)}\n\n"
                return

        elapsed = round((time.monotonic() - start) * 1000, 1)

        # 记录日志
        if final_response:
            db.add(JudgeQuery(
                question=request.question,
                answer=final_response.get("answer", ""),
                model=settings.openai_model,
                confidence=final_response.get("confidence", "medium"),
                used_rules=final_response.get("rules", []),
                used_cards=final_response.get("cards", []),
                latency_ms=elapsed,
                reasoning_summary=final_response.get("reasoning_summary", ""),
                needs_human_judge=final_response.get("needs_human_judge", False),
            ))
            await db.commit()

        yield f"data: {json.dumps({'type': 'done', 'latency_ms': elapsed}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
