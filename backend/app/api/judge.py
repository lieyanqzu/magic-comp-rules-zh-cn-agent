"""规则裁判问答接口。"""

import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.judge_agent import JudgeAgent
from app.agent.schemas import JudgeRequest, JudgeResponse
from app.core.auth import verify_api_key
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import async_session_factory, get_db
from app.db.models import JudgeQuery

router = APIRouter(dependencies=[Depends(verify_api_key)])
logger = get_logger(__name__)


async def _log_query(question: str, response: JudgeResponse, elapsed_ms: float) -> None:
    """记录问答日志，失败不影响返回。"""
    try:
        async with async_session_factory() as db:
            db.add(JudgeQuery(
                question=question, answer=response.answer,
                model=settings.openai_model, confidence=response.confidence,
                used_rules=[r.model_dump() for r in response.rules],
                used_cards=[c.model_dump() for c in response.cards],
                latency_ms=elapsed_ms,
                reasoning_summary=response.reasoning_summary,
                needs_human_judge=response.needs_human_judge,
            ))
            await db.commit()
    except Exception:
        logger.warning("问答日志写入失败", question=question[:50])


@router.post("/ask", response_model=JudgeResponse)
async def ask_judge(request: JudgeRequest, db: AsyncSession = Depends(get_db)) -> JudgeResponse:
    """非流式接口，返回完整结构化回答。"""
    start = time.monotonic()
    agent = JudgeAgent()
    response = await agent.ask(question=request.question, language=request.language)
    elapsed = round((time.monotonic() - start) * 1000, 1)
    response.latency_ms = elapsed

    await _log_query(request.question, response, elapsed)
    return response


@router.post("/stream")
async def stream_judge(request: JudgeRequest) -> StreamingResponse:
    """流式接口（SSE），逐步返回推理过程和工具调用。"""
    agent = JudgeAgent()

    async def event_generator():
        start = time.monotonic()
        final_response = None

        try:
            async for event in agent.ask_stream(question=request.question, language=request.language):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] == "answer":
                    final_response = event["data"]
                elif event["type"] == "error":
                    yield f"data: {json.dumps({'type': 'done', 'error': event['content']}, ensure_ascii=False)}\n\n"
                    return
        except Exception as e:
            logger.exception("流式回答异常")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"
            return

        elapsed = round((time.monotonic() - start) * 1000, 1)

        if final_response:
            try:
                resp = JudgeResponse(**final_response)
                resp.latency_ms = elapsed
                await _log_query(request.question, resp, elapsed)
            except Exception:
                logger.warning("流式日志写入失败")

        yield f"data: {json.dumps({'type': 'done', 'latency_ms': elapsed}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
