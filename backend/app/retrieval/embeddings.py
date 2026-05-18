"""向量嵌入生成模块。"""

from collections import OrderedDict

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

# 进程内 LRU 缓存：同一次请求里 LLM 多轮 search_rules 都用 original_question 当 vector_query，
# 不缓存会导致每轮重复打一次 embedding API（~1-2s/次）。embedding 是确定性的，可安全缓存。
_EMBED_CACHE_MAX = 256
_embed_cache: OrderedDict[str, list[float]] = OrderedDict()


def _truncate(text: str) -> str:
    """截断文本到安全长度。"""
    return text[:MAX_CHARS] if len(text) > MAX_CHARS else text


def _cache_get(text: str) -> list[float] | None:
    if text in _embed_cache:
        _embed_cache.move_to_end(text)
        return _embed_cache[text]
    return None


def _cache_put(text: str, vec: list[float]) -> None:
    _embed_cache[text] = vec
    _embed_cache.move_to_end(text)
    if len(_embed_cache) > _EMBED_CACHE_MAX:
        _embed_cache.popitem(last=False)


async def generate_embedding(text: str) -> list[float]:
    """为文本生成向量嵌入（带进程内缓存）。"""
    truncated = _truncate(text)
    cached = _cache_get(truncated)
    if cached is not None:
        return cached
    client = _get_client()
    response = await client.embeddings.create(
        model=settings.embedding_model,
        input=truncated,
    )
    vec = response.data[0].embedding
    _cache_put(truncated, vec)
    return vec


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
