"""规则检索服务。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas import RuleResult
from app.retrieval.hybrid_search import hybrid_search


class RuleService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def hybrid_search(
        self, query: str, section_id: str | None = None,
        document_types: list[str] | None = None, top_k: int = 10,
    ) -> list[RuleResult]:
        chunks = await hybrid_search(db=self.db, query=query, section_id=section_id, document_types=document_types, top_k=top_k)
        return [
            RuleResult(
                section_id=chunk.section_id, title=chunk.title,
                content=chunk.content, source_path=chunk.source_path,
                document_type=chunk.document_type,
            )
            for chunk in chunks
        ]
