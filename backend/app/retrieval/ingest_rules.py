"""规则资料入库脚本（增量化）。

用法：
    python -m app.retrieval.ingest_rules                  # 仅切片入库（不生 embedding）
    python -m app.retrieval.ingest_rules --embeddings     # 入库并生成 embedding
    python -m app.retrieval.ingest_rules --rebuild        # 全删全插（旧行为）
    python -m app.retrieval.ingest_rules --embeddings --rebuild

增量策略：
- 按 (source_path, section_id) UPSERT，旧行不再清空。
- 用 content_hash (sha256[:16]) 判等：未变化的 chunk 不重新生成 embedding，节省外部 API 费用。
- 旧的、本次切片不再产生的 (source_path, section_id) 行会被删除，避免规则改名后留下孤儿。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.models import Base, RuleChunk
from app.db.session import async_session_factory, engine
from app.retrieval.chunker import Chunk, chunk_file
from app.retrieval.embeddings import generate_embeddings_batch

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
        await conn.execute(sqlalchemy.text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)


async def clear_existing_chunks(db: AsyncSession) -> None:
    await db.execute(delete(RuleChunk))
    await db.commit()
    logger.info("已清空旧的规则切片数据")


async def ingest_file(file_path: Path, source_dir: str, document_type: str) -> list[Chunk]:
    relative_path = f"{source_dir}/{file_path.name}"
    content = file_path.read_text(encoding="utf-8")
    chunks = chunk_file(content, relative_path, document_type)
    logger.info("文件切片完成", path=relative_path, chunks=len(chunks))
    return chunks


async def _load_existing_hashes(db: AsyncSession) -> dict[tuple[str, str], tuple[int, str | None]]:
    """加载现有 chunk 的 (source_path, section_id) → (id, content_hash) 映射。"""
    result = await db.execute(
        select(RuleChunk.id, RuleChunk.source_path, RuleChunk.section_id, RuleChunk.content_hash)
    )
    return {(row.source_path, row.section_id): (row.id, row.content_hash) for row in result}


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


async def ingest_all(generate_embeddings: bool = False, rebuild: bool = False) -> None:
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

    async with async_session_factory() as db:
        if rebuild:
            await clear_existing_chunks(db)
            existing_hashes: dict[tuple[str, str], tuple[int, str | None]] = {}
        else:
            existing_hashes = await _load_existing_hashes(db)

        # 决定哪些 chunk 需要新 embedding：rebuild 全要，否则只要 hash 变了的
        needs_embedding_idx: list[int] = []
        if generate_embeddings:
            for i, chunk in enumerate(all_chunks):
                key = (chunk.source_path, chunk.section_id)
                old = existing_hashes.get(key)
                if rebuild or old is None or old[1] != chunk.content_hash:
                    needs_embedding_idx.append(i)
            logger.info(
                "需要重新 embedding 的 chunk 数",
                changed=len(needs_embedding_idx),
                total=len(all_chunks),
                skipped=len(all_chunks) - len(needs_embedding_idx),
            )

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
            if orphan_keys:
                logger.info("清理孤儿 chunk", count=len(orphan_keys))

        await db.commit()
        logger.info("规则入库完成", total=len(all_chunks))


def main() -> None:
    generate_embeddings = "--embeddings" in sys.argv
    rebuild = "--rebuild" in sys.argv
    asyncio.run(ingest_all(generate_embeddings=generate_embeddings, rebuild=rebuild))


if __name__ == "__main__":
    main()
