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


async def search_by_keyword(db: AsyncSession, keyword: str, document_types: list[str] | None = None, limit: int = 10) -> list[RuleChunk]:
    search_term = f"%{keyword}%"
    query = select(RuleChunk).where(or_(RuleChunk.title.ilike(search_term), RuleChunk.content.ilike(search_term))).limit(limit)
    if document_types:
        query = query.where(RuleChunk.document_type.in_(document_types))
    result = await db.execute(query)
    return list(result.scalars().all())
