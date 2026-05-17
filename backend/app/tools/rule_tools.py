"""规则查询工具。"""

import re

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import RuleChunk

logger = get_logger(__name__)
RULE_NUMBER_PATTERN = re.compile(r"^\d{3}(?:\.\d+[a-z]?)?$")


def extract_rule_numbers(text: str) -> list[str]:
    return RULE_NUMBER_PATTERN.findall(text)


async def search_by_section_id(db: AsyncSession, section_id: str, document_types: list[str] | None = None) -> list[RuleChunk]:
    query = select(RuleChunk).where(RuleChunk.section_id == section_id)
    if document_types:
        query = query.where(RuleChunk.document_type.in_(document_types))
    result = await db.execute(query)
    return list(result.scalars().all())


async def search_by_keyword(
    db: AsyncSession, keyword: str, document_types: list[str] | None = None, limit: int = 10
) -> list[RuleChunk]:
    """关键词检索：拆分关键词，单次查询合并 OR 条件，按匹配数排序。"""
    keywords = [kw.strip() for kw in re.split(r"[\s,，、;；]+", keyword) if len(kw.strip()) >= 1]
    if not keywords:
        return []

    # 构建 OR 条件：任意关键词命中 title 或 content 即匹配
    conditions = []
    for kw in keywords:
        term = f"%{kw}%"
        conditions.append(RuleChunk.title.ilike(term))
        conditions.append(RuleChunk.content.ilike(term))

    query = select(RuleChunk).where(or_(*conditions))
    if document_types:
        query = query.where(RuleChunk.document_type.in_(document_types))
    query = query.limit(limit * 3)  # 多取一些，排序后截断

    result = await db.execute(query)
    chunks = list(result.scalars().all())

    # 按匹配关键词数降序排序
    def _match_count(chunk: RuleChunk) -> int:
        text = f"{chunk.title} {chunk.content}"
        return sum(1 for kw in keywords if kw in text)

    chunks.sort(key=_match_count, reverse=True)
    return chunks[:limit]
