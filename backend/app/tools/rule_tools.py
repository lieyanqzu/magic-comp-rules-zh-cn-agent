"""规则查询工具。"""

import re

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import RuleChunk

logger = get_logger(__name__)
RULE_NUMBER_PATTERN = re.compile(r"^\d{3}(?:\.\d+[a-z]?)?$")
RULE_NUMBER_FINDALL = re.compile(r"\b\d{3}(?:\.\d+[a-z]?)?\b")


def extract_rule_numbers(text_input: str) -> list[str]:
    return RULE_NUMBER_FINDALL.findall(text_input)


async def search_by_section_id(db: AsyncSession, section_id: str, document_types: list[str] | None = None) -> list[RuleChunk]:
    query = select(RuleChunk).where(RuleChunk.section_id == section_id)
    if document_types:
        query = query.where(RuleChunk.document_type.in_(document_types))
    result = await db.execute(query)
    return list(result.scalars().all())


def _split_keywords(keyword: str) -> list[str]:
    return [kw.strip() for kw in re.split(r"[\s,，、;；]+", keyword) if len(kw.strip()) >= 1]


async def search_by_keyword(
    db: AsyncSession, keyword: str, document_types: list[str] | None = None, limit: int = 10
) -> list[RuleChunk]:
    """关键词检索。

    Postgres 路径：使用 pg_trgm 的 `%>` 操作符（命中 GIN 索引）+ similarity() 排序。
    其他方言（SQLite 测试）：降级到 ILIKE 子串 + Python 端排序。
    """
    keywords = _split_keywords(keyword)
    if not keywords:
        return []

    dialect = db.bind.dialect.name if db.bind else ""

    if dialect == "postgresql":
        return await _search_by_keyword_pg(db, keywords, document_types, limit)
    return await _search_by_keyword_fallback(db, keywords, document_types, limit)


async def _search_by_keyword_pg(
    db: AsyncSession, keywords: list[str], document_types: list[str] | None, limit: int
) -> list[RuleChunk]:
    """Postgres + pg_trgm 实现：%> 走 GIN 索引，similarity() 排序。"""
    # 1) 过滤条件：任一关键词在 title 或 content 上 trigram 相似度超阈值
    conds = []
    params: dict[str, str] = {}
    for i, kw in enumerate(keywords):
        kw_param = f"kw_{i}"
        params[kw_param] = kw
        # %> 是 word_similarity_op，比 % 更适合短关键词（比如中文 2~3 字术语）
        conds.append(
            text(
                f"(title %> :{kw_param} OR content %> :{kw_param} "
                f"OR title ILIKE '%' || :{kw_param} || '%' OR content ILIKE '%' || :{kw_param} || '%')"
            )
        )

    # 2) 排序：所有关键词的 similarity(content, kw) + similarity(title, kw) 求和，越大越靠前
    order_terms = []
    for i in range(len(keywords)):
        order_terms.append(
            f"GREATEST(similarity(content, :kw_{i}), similarity(title, :kw_{i}))"
        )
    order_expr = " + ".join(order_terms)

    stmt = select(RuleChunk).where(or_(*conds))
    if document_types:
        stmt = stmt.where(RuleChunk.document_type.in_(document_types))
    stmt = stmt.order_by(text(f"({order_expr}) DESC")).limit(limit)
    stmt = stmt.params(**params)

    try:
        result = await db.execute(stmt)
        return list(result.scalars().all())
    except Exception as e:
        # pg_trgm 扩展未启用时降级，避免线上完全无返回
        logger.warning("pg_trgm 检索失败，降级到 ILIKE", error=str(e)[:100])
        return await _search_by_keyword_fallback(db, keywords, document_types, limit)


async def _search_by_keyword_fallback(
    db: AsyncSession, keywords: list[str], document_types: list[str] | None, limit: int
) -> list[RuleChunk]:
    """SQLite/未装 pg_trgm 时的兜底实现：ILIKE 子串 + Python 端排序。"""
    conditions = []
    for kw in keywords:
        term = f"%{kw}%"
        conditions.append(RuleChunk.title.ilike(term))
        conditions.append(RuleChunk.content.ilike(term))

    stmt = select(RuleChunk).where(or_(*conditions))
    if document_types:
        stmt = stmt.where(RuleChunk.document_type.in_(document_types))
    stmt = stmt.limit(limit * 3)

    result = await db.execute(stmt)
    chunks = list(result.scalars().all())

    def _match_count(chunk: RuleChunk) -> int:
        merged = f"{chunk.title} {chunk.content}"
        return sum(1 for kw in keywords if kw in merged)

    chunks.sort(key=_match_count, reverse=True)
    return chunks[:limit]
