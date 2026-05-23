"""规则资料入库脚本（增量化）。

用法：
    python -m app.retrieval.ingest_rules                  # 仅切片入库（不生 embedding）
    python -m app.retrieval.ingest_rules --embeddings     # 入库并生成 embedding
    python -m app.retrieval.ingest_rules --rebuild        # 全删全插（旧行为）
    python -m app.retrieval.ingest_rules --embeddings --rebuild
    python -m app.retrieval.ingest_rules --skip-cache-purge  # 跳过入库后的缓存清理

增量策略：
- 按 (source_path, section_id) UPSERT，旧行不再清空。
- 用 content_hash (sha256[:16]) 判等：未变化的 chunk 不重新生成 embedding，节省外部 API 费用。
- 旧的、本次切片不再产生的 (source_path, section_id) 行会被删除，避免规则改名后留下孤儿。

入库后处理（默认开启）：
- 任意 chunk 内容/embedding 变化或孤儿被删除时，自动清理 Redis 中的
  `rule_search:*` 和 `llm_answer:*`，避免新数据被旧分数污染。
- 跑一组自检 SQL 输出到日志（覆盖率、平均长度、扩展状态），便于上线后人工核对。
- card / card_autocomplete 缓存与规则数据无关，永不清理（避免首批用户无谓地打 mtgch API）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import redis.asyncio as aioredis
from sqlalchemy import Integer, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.models import Base, RuleChunk
from app.db.session import async_session_factory, engine
from app.retrieval.chunker import Chunk, chunk_file
from app.retrieval.embeddings import generate_embeddings_batch
from app.retrieval.text_cleaner import clean_for

logger = get_logger(__name__)

INGEST_DIRS: list[tuple[str, str]] = [
    ("magic-comp-rules-zh-cn/markdown", "cr"),
    ("skill/references", "reference"),
    ("skill/mtr", "mtr"),
    ("skill/ipg", "ipg"),
]

SKIP_FILES = {"credits.md", "index.md", "intro.md", "translatedterms.md", "README.md", "mtr.md", "ipg.md"}
GLOSSARY_FILES = {"glossary.md", "glossarycn.md"}


async def ensure_tables() -> None:
    async with engine.begin() as conn:
        import sqlalchemy
        # 必备：pgvector 用于向量检索
        await conn.execute(sqlalchemy.text("CREATE EXTENSION IF NOT EXISTS vector"))
        # 可选：pg_trgm 用于关键词检索的 word_similarity (%>)。
        # 部分 PaaS 的 PG 限制了非超级用户安装扩展，失败时记 warning 并降级到 ILIKE。
        try:
            await conn.execute(sqlalchemy.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        except Exception as exc:
            logger.warning(
                "pg_trgm 扩展安装失败，关键词检索将退化为 ILIKE 子串匹配",
                error=str(exc)[:200],
            )
        await conn.run_sync(Base.metadata.create_all)


async def clear_existing_chunks(db: AsyncSession) -> None:
    await db.execute(delete(RuleChunk))
    await db.commit()
    logger.info("已清空旧的规则切片数据")


async def ingest_file(file_path: Path, source_dir: str, document_type: str) -> list[Chunk]:
    relative_path = f"{source_dir}/{file_path.name}"
    content = file_path.read_text(encoding="utf-8")
    if settings.cleanup_text_on_ingest:
        content = clean_for(content, document_type, file_path.name)
    chunks = chunk_file(content, relative_path, document_type)
    logger.info("文件切片完成", path=relative_path, chunks=len(chunks))
    return chunks


async def _load_existing_hashes(db: AsyncSession) -> dict[tuple[str, str], tuple[int, str | None, bool]]:
    """加载现有 chunk 的 (source_path, section_id) → (id, content_hash, has_embedding) 映射。

    has_embedding 用于判断"内容没变但 embedding 缺失"的场景：
    上次跑 --rebuild 但没带 --embeddings 时，行存在、hash 一致，但 embedding 为 NULL。
    后续单独跑 --embeddings 必须能补上这些行，否则会被 hash 判等错过。
    """
    result = await db.execute(
        select(
            RuleChunk.id,
            RuleChunk.source_path,
            RuleChunk.section_id,
            RuleChunk.content_hash,
            RuleChunk.embedding.is_not(None).label("has_embedding"),
        )
    )
    return {
        (row.source_path, row.section_id): (row.id, row.content_hash, bool(row.has_embedding))
        for row in result
    }


async def _upsert_chunk(
    db: AsyncSession,
    chunk: Chunk,
    embedding: list[float] | None,
    *,
    update_embedding: bool,
) -> None:
    """按 (source_path, section_id) UPSERT。

    update_embedding=False 时保留旧的 embedding（content 未变就不浪费一次 API 调用）。
    """
    dialect = db.bind.dialect.name if db.bind else ""
    # PG 路径用列名（注意 metadata_ 在 DB 是 metadata），ORM 兜底路径用属性名
    pg_payload = {
        "document_type": chunk.document_type,
        "source_path": chunk.source_path,
        "section_id": chunk.section_id,
        "title": chunk.title,
        "content": chunk.content,
        "content_hash": chunk.content_hash,
        "metadata": chunk.metadata,
    }
    if update_embedding:
        pg_payload["embedding"] = embedding

    if dialect == "postgresql":
        stmt = pg_insert(RuleChunk.__table__).values(**pg_payload)
        update_cols = {k: stmt.excluded[k] for k in pg_payload}
        stmt = stmt.on_conflict_do_update(
            constraint="uq_rule_chunks_source_section",
            set_=update_cols,
        )
        await db.execute(stmt)
        return

    # 兜底：select-then-update/insert（用 ORM 属性名）
    orm_payload = {**pg_payload}
    orm_payload["metadata_"] = orm_payload.pop("metadata")
    existing = await db.execute(
        select(RuleChunk).where(
            RuleChunk.source_path == chunk.source_path,
            RuleChunk.section_id == chunk.section_id,
        )
    )
    row = existing.scalar_one_or_none()
    if row is None:
        db.add(RuleChunk(**orm_payload))
    else:
        for k, v in orm_payload.items():
            setattr(row, k, v)


async def _purge_search_caches() -> dict[str, int]:
    """清理与规则数据强相关的 Redis 缓存。

    清掉：
    - `rule_search:*`：缓存的是 (chunk_id, score)。chunk_id 不变但分数基于旧文本，
      切换到清洗后内容时分数应当重新计算。
    - `llm_answer:*`：基于旧文本/旧 prompt 的回答可能不再正确，统一作废。

    保留：
    - `card:*` / `card_autocomplete:*`：与规则文本无关，清了反而让用户多打 mtgch API。
    - `rate_limit:*`：限流计数器，清了等于给所有 IP 重置。

    Redis 不可达时记 warning 不抛，调用方自行决定要不要继续。
    """
    counts: dict[str, int] = {"rule_search": 0, "llm_answer": 0}
    redis: aioredis.Redis | None = None
    try:
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        for prefix in counts:
            keys: list[str] = []
            async for k in redis.scan_iter(f"{prefix}:*"):
                keys.append(k)
                # 防止单次列表过大占内存：每攒够一批就批量删
                if len(keys) >= 500:
                    await redis.delete(*keys)
                    counts[prefix] += len(keys)
                    keys = []
            if keys:
                await redis.delete(*keys)
                counts[prefix] += len(keys)
        logger.info("入库后清理 Redis 缓存", **counts)
    except Exception as exc:
        logger.warning("Redis 缓存清理失败（不影响入库结果）", error=str(exc)[:200])
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                pass
    return counts


async def _run_self_check(db: AsyncSession) -> None:
    """入库后自检：把核心健康指标输出到日志。

    包含：
    - 每个 document_type 的 chunk 数 / 平均长度
    - embedding 缺失数（应为 0；非 0 说明有部分 chunk 没成功生 embedding）
    - pgvector / pg_trgm 扩展是否安装

    任何 SQL 失败吞掉，避免影响主流程。生产环境可以把这些日志接入告警。
    """
    try:
        rows = await db.execute(
            select(
                RuleChunk.document_type,
                func.count().label("total"),
                func.sum(
                    (RuleChunk.embedding.is_(None)).cast(Integer)
                ).label("missing_emb"),
                func.avg(func.length(RuleChunk.content)).label("avg_len"),
            ).group_by(RuleChunk.document_type)
        )
        for row in rows:
            logger.info(
                "自检：document_type 统计",
                document_type=row.document_type,
                total=int(row.total),
                missing_embedding=int(row.missing_emb or 0),
                avg_len=int(row.avg_len or 0),
            )
    except Exception as exc:
        logger.warning("自检 chunk 统计失败", error=str(exc)[:200])

    # 扩展状态（仅 PG）
    try:
        dialect = db.bind.dialect.name if db.bind else ""
        if dialect == "postgresql":
            ext = await db.execute(
                text(
                    "SELECT extname, extversion FROM pg_extension "
                    "WHERE extname IN ('vector', 'pg_trgm')"
                )
            )
            installed = {row.extname: row.extversion for row in ext}
            logger.info(
                "自检：PG 扩展状态",
                vector=installed.get("vector", "MISSING"),
                pg_trgm=installed.get("pg_trgm", "MISSING (关键词检索退化为 ILIKE)"),
            )
    except Exception as exc:
        logger.warning("自检 PG 扩展状态失败", error=str(exc)[:200])


async def ingest_all(
    generate_embeddings: bool = False,
    rebuild: bool = False,
    skip_cache_purge: bool = False,
) -> None:
    root = settings.rules_root_path
    logger.info("规则资料根目录", path=str(root), rebuild=rebuild)
    setup_logging()
    await ensure_tables()

    all_chunks: list[Chunk] = []
    for dir_name, doc_type in INGEST_DIRS:
        dir_path = root / dir_name
        if not dir_path.exists():
            logger.warning("目录不存在，跳过", path=str(dir_path))
            continue
        for md_file in sorted(dir_path.glob("*.md")):
            if md_file.name in SKIP_FILES:
                continue
            effective_type = "reference" if md_file.name in GLOSSARY_FILES else doc_type
            chunks = await ingest_file(md_file, dir_name, effective_type)
            all_chunks.extend(chunks)

    logger.info("切片总数", total=len(all_chunks))

    # 跟踪本次入库是否有任何 chunk 内容/embedding/孤儿变化。
    # 全无变化时跳过缓存清理，避免每次启动都吹掉刚预热好的搜索缓存。
    new_count = 0
    changed_count = 0
    orphan_count = 0
    embedding_updated = 0

    async with async_session_factory() as db:
        if rebuild:
            await clear_existing_chunks(db)
            existing_hashes: dict[tuple[str, str], tuple[int, str | None, bool]] = {}
        else:
            existing_hashes = await _load_existing_hashes(db)

        # 决定哪些 chunk 需要新 embedding：rebuild 全要，否则
        # （a）行不存在 / 是新增；（b）content_hash 变了；（c）行存在但 embedding 为 NULL（旧入库漏跑）
        needs_embedding_idx: list[int] = []
        if generate_embeddings:
            for i, chunk in enumerate(all_chunks):
                key = (chunk.source_path, chunk.section_id)
                old = existing_hashes.get(key)
                if rebuild or old is None:
                    needs_embedding_idx.append(i)
                elif old[1] != chunk.content_hash:
                    needs_embedding_idx.append(i)
                elif not old[2]:  # has_embedding=False — 补漏
                    needs_embedding_idx.append(i)
            logger.info(
                "需要重新 embedding 的 chunk 数",
                changed=len(needs_embedding_idx),
                total=len(all_chunks),
                skipped=len(all_chunks) - len(needs_embedding_idx),
            )

        # 顺便记录 chunk 内容变更（不必生 embedding 也算变化，比如 --embeddings 关闭时）
        for chunk in all_chunks:
            key = (chunk.source_path, chunk.section_id)
            old = existing_hashes.get(key)
            if old is None:
                new_count += 1
            elif old[1] != chunk.content_hash:
                changed_count += 1

        new_embeddings: list[list[float]] = []
        if generate_embeddings and needs_embedding_idx:
            texts = [all_chunks[i].content for i in needs_embedding_idx]
            new_embeddings = await generate_embeddings_batch(texts)
            logger.info("向量嵌入生成完成", count=len(new_embeddings))

        # 索引到 chunk 列表的位置
        emb_by_idx = {idx: new_embeddings[i] for i, idx in enumerate(needs_embedding_idx)}

        for i, chunk in enumerate(all_chunks):
            update_embedding = generate_embeddings and i in emb_by_idx
            embedding = emb_by_idx.get(i)
            if update_embedding and not embedding:
                # 单条 embedding 失败时跳过 embedding 字段
                update_embedding = False
            if update_embedding:
                embedding_updated += 1
            await _upsert_chunk(db, chunk, embedding, update_embedding=update_embedding)

        # 删除孤儿：本次没出现、且不在 rebuild 模式下的旧 (source_path, section_id)
        if not rebuild:
            current_keys = {(c.source_path, c.section_id) for c in all_chunks}
            orphan_keys = [k for k in existing_hashes if k not in current_keys]
            for source_path, section_id in orphan_keys:
                await db.execute(
                    delete(RuleChunk).where(
                        RuleChunk.source_path == source_path,
                        RuleChunk.section_id == section_id,
                    )
                )
            orphan_count = len(orphan_keys)
            if orphan_keys:
                logger.info("清理孤儿 chunk", count=len(orphan_keys))

        await db.commit()
        logger.info(
            "规则入库完成",
            total=len(all_chunks),
            new=new_count,
            changed=changed_count,
            orphans_removed=orphan_count,
            embeddings_updated=embedding_updated,
        )

        # 自检放在 commit 之后，能看到最新的 chunk 状态
        await _run_self_check(db)

    # 缓存清理：rebuild 强制清；否则只在有真实变化时才清。
    # 全无变化的常规重启不清，避免吹掉刚预热的搜索缓存。
    had_changes = bool(rebuild or new_count or changed_count or orphan_count or embedding_updated)
    if skip_cache_purge:
        logger.info("--skip-cache-purge 已设置，跳过 Redis 缓存清理")
    elif had_changes:
        await _purge_search_caches()
    else:
        logger.info("入库无任何变化，跳过 Redis 缓存清理")


def main() -> None:
    generate_embeddings = "--embeddings" in sys.argv
    rebuild = "--rebuild" in sys.argv
    skip_cache_purge = "--skip-cache-purge" in sys.argv
    asyncio.run(
        ingest_all(
            generate_embeddings=generate_embeddings,
            rebuild=rebuild,
            skip_cache_purge=skip_cache_purge,
        )
    )


if __name__ == "__main__":
    main()
