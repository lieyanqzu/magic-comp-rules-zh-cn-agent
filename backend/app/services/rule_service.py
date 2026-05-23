"""规则检索服务。"""

from __future__ import annotations

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.hybrid_search import DefaultRuleSearcher
from app.schemas import RuleResult


class RuleService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis | None = None) -> None:
        self.db = db
        self.searcher = DefaultRuleSearcher(db, redis)

    async def hybrid_search(
        self,
        query: str,
        section_id: str | None = None,
        document_types: list[str] | None = None,
        top_k: int = 10,
        vector_query: str | None = None,
    ) -> list[RuleResult]:
        ranked = await self.searcher.search(
            query=query,
            section_id=section_id,
            document_types=document_types,
            top_k=top_k,
            vector_query=vector_query,
        )
        return [
            RuleResult(
                section_id=r.chunk.section_id,
                title=r.chunk.title,
                content=r.chunk.content,
                source_path=r.chunk.source_path,
                document_type=r.chunk.document_type,
                score=round(r.score, 4),
            )
            for r in ranked
        ]
