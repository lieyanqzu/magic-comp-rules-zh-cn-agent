"""规则资料入库脚本。

用法：
    python -m app.retrieval.ingest_rules
    python -m app.retrieval.ingest_rules --embeddings
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import delete

from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.models import Base, RuleChunk
from app.db.session import async_session_factory, engine
from app.retrieval.chunker import Chunk, chunk_file
from app.retrieval.embeddings import generate_embeddings_batch

logger = get_logger(__name__)

# 入库目录配置：(目录名, 文档类型)
INGEST_DIRS: list[tuple[str, str]] = [
    ("magic-comp-rules-zh-cn/markdown", "cr"),
    ("skill/references", "reference"),
    ("skill/mtr", "mtr"),
    ("skill/ipg", "ipg"),
]

SKIP_FILES = {"credits.md", "index.md", "intro.md", "translatedterms.md", "README.md", "mtr.md", "ipg.md"}
GLOSSARY_FILES = {"glossary.md", "glossarycn.md"}


async def ensure_tables() -> None:
    async with engine.begin() -> conn:
        import sqlalchemy
        await conn.execute(sqlalchemy.text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)


async def clear_existing_chunks(db: object) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession
    assert isinstance(db, AsyncSession)
    await db.execute(delete(RuleChunk))
    await db.commit()
    logger.info("已清空旧的规则切片数据")


async def ingest_file(file_path: Path, source_dir: str, document_type: str) -> list[Chunk]:
    relative_path = f"{source_dir}/{file_path.name}"
    content = file_path.read_text(encoding="utf-8")
    chunks = chunk_file(content, relative_path, document_type)
    logger.info("文件切片完成", path=relative_path, chunks=len(chunks))
    return chunks


async def ingest_all(generate_embeddings: bool = False) -> None:
    root = settings.rules_root_path
    logger.info("规则资料根目录", path=str(root))
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

    embeddings: list[list[float]] | None = None
    if generate_embeddings:
        logger.info("开始生成向量嵌入...")
        texts = [c.content for c in all_chunks]
        embeddings = await generate_embeddings_batch(texts)
        logger.info("向量嵌入生成完成", count=len(embeddings))

    async with async_session_factory() as db:
        await clear_existing_chunks(db)
        for i, chunk in enumerate(all_chunks):
            db.add(RuleChunk(
                document_type=chunk.document_type,
                source_path=chunk.source_path,
                section_id=chunk.section_id,
                title=chunk.title,
                content=chunk.content,
                embedding=embeddings[i] if embeddings else None,
                metadata_=chunk.metadata,
            ))
        await db.commit()
        logger.info("规则入库完成", total=len(all_chunks))


def main() -> None:
    generate_embeddings = "--embeddings" in sys.argv
    asyncio.run(ingest_all(generate_embeddings=generate_embeddings))


if __name__ == "__main__":
    main()
