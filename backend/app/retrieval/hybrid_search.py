"""混合检索：RRF 融合精确匹配 / 关键词 / 向量三路结果，可选 Redis 缓存。"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Protocol

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import RuleChunk
from app.retrieval.embeddings import generate_embedding
from app.tools.rule_tools import search_by_keyword, search_by_section_id

logger = get_logger(__name__)

# RRF 单路最大候选数
_RRF_PER_BRANCH = 30

# embedding 列即使是向量检索也无需 SELECT 出来：ORDER BY 在 PG 端用就够了，
# 否则单条 SELECT 会带回 30 行 × 1024 维 ≈ 600KB，到云端 PG 的网络往返让单条 SQL 跑 20+ 秒。
_DEFER_EMBEDDING = (defer(RuleChunk.embedding),)


class RuleSearcher(Protocol):
    """规则检索协议。Agent / Service 依赖这个抽象，方便测试 mock。"""

    async def search(
        self,
        *,
        query: str,
        section_id: str | None = None,
        document_types: list[str] | None = None,
        top_k: int = 10,
        vector_query: str | None = None,
    ) -> list[RuleChunk]:
        ...


def _cache_key(
    query: str,
    section_id: str | None,
    document_types: list[str] | None,
    top_k: int,
    vector_query: str | None,
) -> str:
    payload = json.dumps(
        {
            "q": query,
            "sid": section_id,
            "dt": sorted(document_types) if document_types else None,
            "k": top_k,
            "vq": vector_query,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"rule_search:{digest}"


def _rrf_fuse(
    branches: Iterable[list[RuleChunk]],
    *,
    top_k: int,
    k: int = 60,
) -> list[RuleChunk]:
    """Reciprocal Rank Fusion：每路按排名贡献 1/(k + rank)，分数累加后取 top_k。"""
    scores: dict[int, float] = {}
    chunks_by_id: dict[int, RuleChunk] = {}

    for branch in branches:
        for rank, chunk in enumerate(branch):
            if chunk.id is None:
                continue
            chunks_by_id[chunk.id] = chunk
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (k + rank + 1)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [chunks_by_id[cid] for cid, _ in ranked[:top_k]]


async def _vector_search(
    db: AsyncSession,
    query_embedding: list[float],
    document_types: list[str] | None,
    top_k: int,
) -> list[RuleChunk]:
    distance = RuleChunk.embedding.cosine_distance(query_embedding)
    stmt = (
        select(RuleChunk)
        .options(*_DEFER_EMBEDDING)
        .where(RuleChunk.embedding.is_not(None))
        .order_by(distance)
        .limit(top_k)
    )
    if document_types:
        stmt = stmt.where(RuleChunk.document_type.in_(document_types))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def hybrid_search(
    db: AsyncSession,
    query: str,
    section_id: str | None = None,
    document_types: list[str] | None = None,
    top_k: int = 10,
    vector_query: str | None = None,
    redis: aioredis.Redis | None = None,
) -> list[RuleChunk]:
    """混合检索（RRF 融合）。

    - 精确规则号匹配：作为最高优先级单独前置（不参与 RRF），避免被向量分数压下去。
    - 关键词 + 向量：跑满后 RRF 融合，去重后截断到 top_k。
    - Redis 缓存：命中直接返回 chunk id 列表，再去 DB 取全行（避免缓存大文本）。

    Args:
        query: 关键词检索用查询（LLM 提取的关键词）。
        vector_query: 向量检索用查询（用户原始问题），未传则降级到 query。
        redis: 可选 Redis 客户端，用于缓存。
    """
    cache_enabled = settings.retrieval_cache_enabled and redis is not None
    cache_key = _cache_key(query, section_id, document_types, top_k, vector_query) if cache_enabled else ""

    if cache_enabled:
        try:
            cached = await redis.get(cache_key)
            if cached:
                ids = json.loads(cached)
                if ids:
                    rows = await db.execute(
                        select(RuleChunk).options(*_DEFER_EMBEDDING).where(RuleChunk.id.in_(ids))
                    )
                    by_id = {r.id: r for r in rows.scalars().all()}
                    ordered = [by_id[i] for i in ids if i in by_id]
                    if ordered:
                        logger.info("规则检索缓存命中", key=cache_key, count=len(ordered))
                        return ordered
        except Exception:
            logger.warning("Redis 检索缓存读取失败")

    results: list[RuleChunk] = []

    # 1) 精确规则号 → 直接前置
    exact: list[RuleChunk] = []
    if section_id:
        exact = await search_by_section_id(db, section_id, document_types)
        logger.info("精确匹配", section_id=section_id, count=len(exact))
        results.extend(exact)

    # 2) 关键词 + 向量，RRF 融合剩余位置
    remaining = top_k - len(results)
    if remaining > 0:
        keyword_branch: list[RuleChunk] = []
        if query:
            keyword_branch = await search_by_keyword(db, query, document_types, limit=_RRF_PER_BRANCH)
            logger.info("关键词检索", query=query[:50], count=len(keyword_branch))

        vector_branch: list[RuleChunk] = []
        vq = vector_query or query
        if vq:
            try:
                emb = await generate_embedding(vq)
                vector_branch = await _vector_search(db, emb, document_types, top_k=_RRF_PER_BRANCH)
                logger.info("向量检索", query=vq[:50], count=len(vector_branch))
            except Exception:
                logger.warning("向量检索跳过（embedding 服务不可用或无数据）")

        fused = _rrf_fuse(
            [keyword_branch, vector_branch],
            top_k=remaining + len(exact),  # 多取一点以防与 exact 去重后不够
            k=settings.retrieval_rrf_k,
        )
        # 去重：已经在 exact 里的不再加入
        seen = {c.id for c in results}
        for chunk in fused:
            if chunk.id in seen:
                continue
            results.append(chunk)
            seen.add(chunk.id)
            if len(results) >= top_k:
                break

    final = results[:top_k]

    if cache_enabled and final:
        try:
            ids = [c.id for c in final if c.id is not None]
            await redis.set(cache_key, json.dumps(ids), ex=settings.retrieval_cache_ttl)
        except Exception:
            logger.warning("Redis 检索缓存写入失败")

    return final


class DefaultRuleSearcher:
    """RuleSearcher 默认实现：包装 hybrid_search + DI 进来的 db / redis。"""

    def __init__(self, db: AsyncSession, redis: aioredis.Redis | None = None) -> None:
        self.db = db
        self.redis = redis

    async def search(
        self,
        *,
        query: str,
        section_id: str | None = None,
        document_types: list[str] | None = None,
        top_k: int = 10,
        vector_query: str | None = None,
    ) -> list[RuleChunk]:
        return await hybrid_search(
            self.db,
            query=query,
            section_id=section_id,
            document_types=document_types,
            top_k=top_k,
            vector_query=vector_query,
            redis=self.redis,
        )
