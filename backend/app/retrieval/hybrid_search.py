"""混合检索：召回(精确编号 + 关键词 + 向量) → reranker 精排 → 带分数 TopK。

设计要点：
- 召回阶段不追求精度，追求覆盖：单路放大到 retrieval_recall_per_branch (默认 50)。
- RfF 仅作为 reranker 不可用时的兜底融合。reranker 启用时直接把召回去重后整批喂进去。
- 输出 HybridSearchResult：上层（rule_service / judge_agent）能拿到分数 + 重排状态，
  从而判断 confidence_hint 与排序信号是否可信，避免"top1 不沾边就盲目重试"的循环。
- query 自动扩展：关键词分支前用同义词字典做 OR 展开。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Protocol

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import RuleChunk
from app.retrieval.embeddings import generate_embedding
from app.retrieval.query_expand import expand_query
from app.retrieval.reranker import (
    RerankedChunk,
    RerankStatus,
    _fallback_items,
    rerank,
)
from app.tools.rule_tools import search_by_keyword, search_by_section_id

logger = get_logger(__name__)

# embedding 列即使是向量检索也无需 SELECT 出来：ORDER BY 在 PG 端用就够了，
# 否则单条 SELECT 会带回 30+ 行 × 1024 维 ≈ 600KB，到云端 PG 的网络往返让单条 SQL 跑 20+ 秒。
_DEFER_EMBEDDING = (defer(RuleChunk.embedding),)


@dataclass(frozen=True, slots=True)
class HybridSearchResult:
    """混合检索结果：TopK + 重排状态信号。

    rerank_status 让 agent 把"分数信号是否可信"暴露给前端 trace，便于排查质量问题：
    - ok       : 至少一次 reranker API 调用成功，分数可信
    - cached   : 命中重排 LRU 或 Redis 检索缓存，分数仍来自真实重排
    - fallback : reranker API 失败，走线性递减兜底，分数仅作排序占位
    - disabled : reranker_enabled=False，主动关闭精排（也走兜底）
    - no_input : 无候选 chunk
    """

    items: list[RerankedChunk]
    rerank_status: RerankStatus

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx: int | slice):
        return self.items[idx]


class RuleSearcher(Protocol):
    """规则检索协议。Agent / Service 依赖这个抽象，方便测试 mock。

    返回 HybridSearchResult：上层从 .items 拿带分数的 chunk，从 .rerank_status 判断
    分数信号是否可信。
    """

    async def search(
        self,
        *,
        query: str,
        section_id: str | None = None,
        document_types: list[str] | None = None,
        top_k: int = 10,
        vector_query: str | None = None,
    ) -> HybridSearchResult:
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
            # reranker 配置变了缓存要失效（不同模型分数不可比）
            "rk": settings.reranker_model if settings.reranker_enabled else "off",
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
    """Reciprocal Rank Fusion：每路按排名贡献 1/(k + rank)，分数累加后取 top_k。

    仅在 reranker 不可用 / 关闭时作为兜底融合策略使用。
    """
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


async def _safe_db_branch(
    db: AsyncSession,
    branch_name: str,
    coro_factory,
) -> list[RuleChunk]:
    """运行一个 SQL 检索分支，失败时回滚 session 并返回空。

    asyncpg / SQLAlchemy 在 PG 上的语义：一旦某次查询抛异常，
    连接进入 transaction-error 状态，**所有后续查询都会失败**直到 rollback。
    FastAPI 一个请求共享一个 session，意味着 vector 分支挂了会让后续 keyword
    检索甚至下一次 search_rules 工具调用全部连锁失败。

    所以每个分支独立 try/except，PG 异常时主动 rollback 让 session 干净，
    再返回空让上层继续走其他分支。SQLite 等其他方言无此连锁卡死问题，
    rollback 失败也吞掉，避免测试环境的 greenlet 上下文问题。
    """
    try:
        return await coro_factory()
    except Exception as exc:
        logger.warning(
            f"{branch_name}失败，将尝试回滚 session",
            error=str(exc)[:200],
        )
        # 仅 PG 真正需要 rollback 解卡；其他方言失败 rollback 自身可能抛
        # MissingGreenlet 等无关异常，统一吞掉
        dialect = db.bind.dialect.name if db.bind else ""
        if dialect == "postgresql":
            try:
                await db.rollback()
            except Exception as rb_exc:
                logger.warning(
                    "session rollback 失败",
                    branch=branch_name,
                    error=str(rb_exc)[:200],
                )
        return []


async def hybrid_search(
    db: AsyncSession,
    query: str,
    section_id: str | None = None,
    document_types: list[str] | None = None,
    top_k: int = 10,
    vector_query: str | None = None,
    redis: aioredis.Redis | None = None,
) -> HybridSearchResult:
    """混合召回 + 精排，返回带分数的 TopK 与重排状态。

    流程：
        1) 精确规则号匹配 → 单独前置（不参与重排，保证用户明确指名的条目永远在前）
        2) 关键词召回（query 自动同义词扩展）
        3) 向量召回（vector_query → embedding → cosine 距离）
        4) 去重合并 → reranker 精排 → 取 top_k
        5) reranker 不可用时降级到 RRF 融合（status=fallback / disabled）

    Args:
        query: 关键词检索用查询（LLM 提取的关键词）。会被 expand_query 扩展。
        vector_query: 向量检索用查询（用户原始问题），未传则降级到 query。
        redis: 可选 Redis 客户端，用于缓存。
    """
    cache_enabled = settings.retrieval_cache_enabled and redis is not None
    cache_key = _cache_key(query, section_id, document_types, top_k, vector_query) if cache_enabled else ""

    if cache_enabled:
        cached = await _read_cache(redis, cache_key, db)
        if cached is not None:
            logger.info("规则检索缓存命中", key=cache_key, count=len(cached))
            # 命中检索缓存意味着上次结果（分数）来自当时的真实重排，标记为 cached
            return HybridSearchResult(items=cached, rerank_status="cached")

    # ---- 1) 精确编号前置 ----
    exact: list[RuleChunk] = []
    if section_id:
        exact = await _safe_db_branch(
            db,
            "精确匹配",
            lambda: search_by_section_id(db, section_id, document_types),
        )
        logger.info("精确匹配", section_id=section_id, count=len(exact))

    # ---- 2) 关键词召回（带同义词扩展） ----
    expansion = expand_query(query) if query else None
    keyword_branch: list[RuleChunk] = []
    expanded_keywords = ""
    if expansion and expansion.keywords:
        expanded_keywords = " ".join(expansion.keywords)
        keyword_branch = await _safe_db_branch(
            db,
            "关键词检索",
            lambda: search_by_keyword(
                db,
                expanded_keywords,
                document_types,
                limit=settings.retrieval_recall_per_branch,
            ),
        )
        logger.info(
            "关键词检索",
            query=query[:50],
            expanded=expanded_keywords[:80],
            hit_groups=len(expansion.hit_groups),
            count=len(keyword_branch),
        )

    # ---- 3) 向量召回 ----
    # 拆成两段：embedding（外部 HTTP）失败不该污染 DB session；
    # _vector_search（DB SQL）失败要走 _safe_db_branch 回滚。
    vector_branch: list[RuleChunk] = []
    vq = vector_query or query
    if vq:
        emb: list[float] | None = None
        try:
            emb = await generate_embedding(vq)
        except Exception:
            logger.warning("embedding 生成失败，跳过向量分支")
        if emb is not None:
            vector_branch = await _safe_db_branch(
                db,
                "向量检索",
                lambda: _vector_search(
                    db, emb, document_types, top_k=settings.retrieval_recall_per_branch
                ),
            )
            logger.info("向量检索", query=vq[:50], count=len(vector_branch))

    # ---- 4) 合并去重 ----
    candidates: list[RuleChunk] = []
    seen: set[int] = set()
    for c in exact:
        if c.id is not None and c.id not in seen:
            candidates.append(c)
            seen.add(c.id)
    fallback_id_counter = -1
    for c in keyword_branch + vector_branch:
        cid = c.id if c.id is not None else fallback_id_counter
        if c.id is None:
            fallback_id_counter -= 1
        if cid in seen:
            continue
        candidates.append(c)
        seen.add(cid)

    if not candidates:
        if cache_enabled:
            await _write_cache(redis, cache_key, [])
        return HybridSearchResult(items=[], rerank_status="no_input")

    # ---- 5) Reranker 精排 ----
    rerank_query = vq or query or ""
    status: RerankStatus
    if settings.reranker_enabled and rerank_query:
        rerank_result = await rerank(rerank_query, candidates, top_k=None)
        ranked = list(rerank_result.items)
        status = rerank_result.status
    else:
        # reranker 关闭：用 RRF 融合 keyword + vector 分支，exact 已经在 candidates 头部
        fused = _rrf_fuse(
            [keyword_branch, vector_branch],
            top_k=settings.retrieval_recall_per_branch,
            k=settings.retrieval_rrf_k,
        )
        ordered: list[RuleChunk] = list(exact)
        ordered_ids = {c.id for c in ordered if c.id is not None}
        for c in fused:
            if c.id not in ordered_ids:
                ordered.append(c)
                ordered_ids.add(c.id)
        ranked = _fallback_items(ordered, top_k=None)
        status = "disabled"

    # exact 永远在前：reranker 给的分数可能让其他条目排在 exact 前面，
    # 但用户明确报了 section_id 时这是反直觉的。把 exact 的 chunk 提到最前。
    if exact:
        exact_ids = {c.id for c in exact if c.id is not None}
        exact_ranked = [r for r in ranked if r.chunk.id in exact_ids]
        rest_ranked = [r for r in ranked if r.chunk.id not in exact_ids]
        ranked = exact_ranked + rest_ranked

    final = ranked[:top_k]

    if cache_enabled and final:
        await _write_cache(redis, cache_key, final)

    return HybridSearchResult(items=final, rerank_status=status)


async def _read_cache(
    redis: aioredis.Redis,
    cache_key: str,
    db: AsyncSession,
) -> list[RerankedChunk] | None:
    """从 Redis 读 (chunk_id, score) 列表，再从 DB 取全行。"""
    try:
        cached = await redis.get(cache_key)
        if not cached:
            return None
        payload = json.loads(cached)
        items = payload.get("items") if isinstance(payload, dict) else None
        if not items:
            return None
        ids = [int(it["id"]) for it in items if "id" in it]
        if not ids:
            return None
    except Exception:
        logger.warning("Redis 检索缓存读取失败")
        return None

    # DB 查询单独保护：失败时 rollback 让 session 干净，避免污染后续分支
    try:
        rows = await db.execute(
            select(RuleChunk).options(*_DEFER_EMBEDDING).where(RuleChunk.id.in_(ids))
        )
        by_id = {r.id: r for r in rows.scalars().all()}
    except Exception as exc:
        logger.warning("缓存命中后 DB 取行失败，已回滚 session", error=str(exc)[:200])
        dialect = db.bind.dialect.name if db.bind else ""
        if dialect == "postgresql":
            try:
                await db.rollback()
            except Exception:
                pass
        return None

    ordered: list[RerankedChunk] = []
    for it in items:
        cid = int(it.get("id", -1))
        chunk = by_id.get(cid)
        if chunk is not None:
            ordered.append(RerankedChunk(chunk=chunk, score=float(it.get("s", 0.0))))
    return ordered or None


async def _write_cache(
    redis: aioredis.Redis,
    cache_key: str,
    final: list[RerankedChunk],
) -> None:
    try:
        payload = {
            "items": [
                {"id": r.chunk.id, "s": round(r.score, 4)}
                for r in final
                if r.chunk.id is not None
            ]
        }
        await redis.set(cache_key, json.dumps(payload), ex=settings.retrieval_cache_ttl)
    except Exception:
        logger.warning("Redis 检索缓存写入失败")


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
    ) -> HybridSearchResult:
        return await hybrid_search(
            self.db,
            query=query,
            section_id=section_id,
            document_types=document_types,
            top_k=top_k,
            vector_query=vector_query,
            redis=self.redis,
        )
