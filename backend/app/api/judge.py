"""规则裁判问答接口。"""

import time

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.schemas import JudgeRequest, JudgeResponse
from app.db.session import get_db
from app.services.judge_service import JudgeService

router = APIRouter()


@router.post("/ask", response_model=JudgeResponse)
async def ask_judge(request: JudgeRequest, db: AsyncSession = Depends(get_db)) -> JudgeResponse:
    start = time.monotonic()
    service = JudgeService(db)
    response = await service.ask(request)
    response.latency_ms = round((time.monotonic() - start) * 1000, 1)
    return response
