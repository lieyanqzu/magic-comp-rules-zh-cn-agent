"""裁判问答服务。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.judge_agent import JudgeAgent
from app.agent.schemas import JudgeRequest, JudgeResponse
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import JudgeQuery

logger = get_logger(__name__)


class JudgeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.agent = JudgeAgent()

    async def ask(self, request: JudgeRequest) -> JudgeResponse:
        logger.info("收到裁判问答", question=request.question[:100])
        response = await self.agent.ask(question=request.question, language=request.language)

        self.db.add(JudgeQuery(
            question=request.question, answer=response.answer,
            model=settings.openai_model, confidence=response.confidence,
            used_rules=[r.model_dump() for r in response.rules],
            used_cards=[c.model_dump() for c in response.cards],
            latency_ms=response.latency_ms,
            reasoning_summary=response.reasoning_summary,
            needs_human_judge=response.needs_human_judge,
        ))
        await self.db.commit()
        return response
