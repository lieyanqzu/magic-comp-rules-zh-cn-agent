"""向量嵌入生成模块。"""

from openai import AsyncOpenAI

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)
_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    """懒加载 embedding 客户端，使用独立的 embedding API 配置。"""
    global _client
    if _client is None:
        api_key = settings.embedding_api_key or settings.openai_api_key
        base_url = settings.embedding_base_url or settings.openai_base_url
        _client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    return _client


MAX_CHARS = 4000  # 约 8192 token 的安全上限


def _truncate(text: str) -> str:
    """截断文本到安全长度。"""
    return text[:MAX_CHARS] if len(text) > MAX_CHARS else text


async def generate_embedding(text: str) -> list[float]:
    """为文本生成向量嵌入。"""
    client = _get_client()
    response = await client.embeddings.create(
        model=settings.embedding_model,
        input=_truncate(text),
    )
    return response.data[0].embedding


async def generate_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """批量生成向量嵌入。逐条处理，遇到错误跳过。"""
    client = _get_client()
    all_embeddings: list[list[float]] = []
    batch_size = 32

    for i in range(0, len(texts), batch_size):
        batch = [_truncate(t) for t in texts[i : i + batch_size]]
        batch_num = i // batch_size + 1
        logger.info("生成 embedding", batch=batch_num, size=len(batch))
        try:
            response = await client.embeddings.create(
                model=settings.embedding_model,
                input=batch,
            )
            all_embeddings.extend(item.embedding for item in response.data)
        except Exception as e:
            logger.warning("batch embedding 失败，逐条重试", batch=batch_num, error=str(e)[:100])
            for text in batch:
                try:
                    resp = await client.embeddings.create(
                        model=settings.embedding_model,
                        input=text,
                    )
                    all_embeddings.append(resp.data[0].embedding)
                except Exception:
                    logger.warning("单条 embedding 跳过", text_len=len(text))
                    all_embeddings.append([])

    return all_embeddings
