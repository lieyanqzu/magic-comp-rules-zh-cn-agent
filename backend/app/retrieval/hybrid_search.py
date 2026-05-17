"""混合检索策略：精确匹配 → 关键词 → 向量检索。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import RuleChunk
from app.retrieval.embeddings import generate_embedding
from app.tools.rule_tools import search_by_keyword, search_by_section_id

logger = get_logger(__name__)


async def hybrid_search(
    db: AsyncSession,
    query: str,
    section_id: str | None = None,
    document_types: list[str] | None = None,
    top_k: int = 10,
) -> list[RuleChunk]:
    results: list[RuleChunk] = []
    seen_ids: set[int] = set()

    def _add_unique(chunks: list[RuleChunk]) -> None:
        for chunk in chunks:
            if chunk.id not in seen_ids:
                results.append(chunk)
                seen_ids.add(chunk.id)

    # 精确规则编号匹配
    if section_id:
        exact_results = await search_by_section_id(db, section_id, document_types)
        _add_unique(exact_results)
        logger.info("精确匹配结果", section_id=section_id, count=len(exact_results))
        if len(results) >= top_k:
            return results[:top_k]

    # 关键词检索
    keyword_results = await search_by_keyword(db, query, document_types, limit=top_k)
    _add_unique(keyword_results)
    if len(results) >= top_k:
        return results[:top_k]

    # 向量检索
    try:
        query_embedding = await generate_embedding(query)
        vector_results = await _vector_search(db, query_embedding, document_types, top_k=top_k - len(results))
        _add_unique(vector_results)
    except Exception:
        logger.warning("向量检索跳过（embedding 服务不可用或无数据）")

    return results[:top_k]


async def _vector_search(
    db: AsyncSession,
    query_embedding: list[float],
    document_types: list[str] | None,
    top_k: int = 5,
) -> list[RuleChunk]:
    from sqlalchemy import select

    distance = RuleChunk.embedding.cosine_distance(query_embedding)
    query_stmt = select(RuleChunk).where(RuleChunk.embedding.is_not(None)).order_by(distance).limit(top_k)
    if document_types:
        query_stmt = query_stmt.where(RuleChunk.document_type.in_(document_types))
    result = await db.execute(query_stmt)
    return list(result.scalars().all())
