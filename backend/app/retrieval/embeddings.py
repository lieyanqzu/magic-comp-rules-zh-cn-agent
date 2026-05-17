"""向量嵌入生成模块。"""

from openai import AsyncOpenAI
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)
_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
    return _client


async def generate_embedding(text: str) -> list[float]:
    client = _get_client()
    response = await client.embeddings.create(model=settings.embedding_model, input=text)
    return response.data[0].embedding


async def generate_embeddings_batch(texts: list[str]) -> list[list[float]]:
    client = _get_client()
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), 2048):
        batch = texts[i : i + 2048]
        response = await client.embeddings.create(model=settings.embedding_model, input=batch)
        all_embeddings.extend(item.embedding for item in response.data)
    return all_embeddings
