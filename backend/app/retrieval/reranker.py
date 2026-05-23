"""Cross-encoder reranker：调用兼容 OpenAI 风格的 /rerank 接口对召回做精排。

设计要点：
- 默认使用 SiliconFlow / Jina / Cohere 兼容的 /v1/rerank 端点（POST JSON，
  return {"results":[{"index":i,"relevance_score":s}, ...]}）。
- 全部失败降级：返回原顺序 + 平均分（不让 reranker 故障导致检索整体崩）。
- 进程内 LRU 缓存：同一次对话里 LLM 多轮 search_rules 用同一 query 的概率很高。
- 不依赖 openai SDK：避免 reranker 端点和 chat 端点的 base_url 共用 SDK 时奇怪的路由问题。
- API 调用带 tenacity 退避：429/5xx/瞬时网络错误最多重试 2 次（短退避）。
- 返回结果同时携带状态枚举，让上层可以暴露"reranker 是否在工作"给前端调试。
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal, Sequence

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import RuleChunk

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RerankedChunk:
    """重排结果：原 chunk + reranker 给出的相关度分数（0~1）。"""

    chunk: RuleChunk
    score: float


# 重排状态：让上层（agent / 前端 trace）知道排序信号是真精排还是兜底，便于排查质量问题
RerankStatus = Literal["ok", "cached", "fallback", "disabled", "no_input"]


@dataclass(frozen=True, slots=True)
class RerankResult:
    """重排返回值：带状态的 TopK。

    status 让调用方判断分数信号是否可信：
    - ok       : 至少一次真实 API 调用成功（可能掺有缓存命中），分数可信
    - cached   : 全部命中 LRU，没打 API，分数仍来自历史真实调用
    - fallback : API 失败 / 未配置 key，走线性递减兜底，分数仅作排序占位
    - disabled : reranker_enabled=False，主动关闭精排
    - no_input : 输入 chunks 为空
    """

    items: list[RerankedChunk]
    status: RerankStatus

    def __iter__(self):
        # 兼容旧调用方 `for r in reranked: ...`
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx: int | slice):
        return self.items[idx]


# 进程内 LRU：(query_hash, doc_hash) → score
_score_cache: OrderedDict[str, float] = OrderedDict()


def _cache_key(query: str, doc: str) -> str:
    h = hashlib.sha1(f"{query}{doc}".encode("utf-8")).hexdigest()[:24]
    return h


def _cache_get(key: str) -> float | None:
    if key in _score_cache:
        _score_cache.move_to_end(key)
        return _score_cache[key]
    return None


def _cache_put(key: str, value: float) -> None:
    _score_cache[key] = value
    _score_cache.move_to_end(key)
    while len(_score_cache) > settings.reranker_cache_size:
        _score_cache.popitem(last=False)


def _resolve_credentials() -> tuple[str, str]:
    """决定 reranker 实际使用的 api_key / base_url。"""
    api_key = (
        settings.reranker_api_key
        or settings.embedding_api_key
        or settings.openai_api_key
    )
    base_url = (
        settings.reranker_base_url
        or settings.embedding_base_url
        or settings.openai_base_url
    )
    return api_key, base_url


def _build_doc_text(chunk: RuleChunk) -> str:
    """喂给 reranker 的文档文本：标题 + 内容前 800 字（避免长 chunk 拖慢精排）。"""
    title = (chunk.title or "").strip()
    content = (chunk.content or "").strip()
    if title and not content.startswith(title):
        text = f"{title}\n{content}"
    else:
        text = content or title
    return text[:800]


_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=settings.reranker_timeout,
            headers={"User-Agent": "mtg-judge-reranker/1.0"},
        )
    return _http_client


async def _call_rerank_api(
    query: str,
    documents: Sequence[str],
) -> list[float] | None:
    """调用 /v1/rerank 接口，带 tenacity 退避重试。

    重试范围：
    - 网络瞬时错误（httpx.TimeoutException / ConnectError）
    - 5xx / 429（解析 status_code 决定）
    - JSON 解析异常 / 4xx（业务错误）：直接返回 None，不重试

    最多 3 次（含首次），指数退避 0.5s → 2s。失败统一返回 None 让上层走 fallback。
    """
    api_key, base_url = _resolve_credentials()
    if not api_key:
        return None

    url = f"{base_url.rstrip('/')}/rerank"
    payload = {
        "model": settings.reranker_model,
        "query": query,
        "documents": list(documents),
        # SiliconFlow 默认按 score 排序返回，但我们要按 input index 对齐回原 chunk，
        # 所以保留 return_documents=False 并手动按 index 取分。
        "return_documents": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async def _do_call() -> list[float] | None:
        client = _get_http_client()
        resp = await client.post(url, json=payload, headers=headers)
        if 500 <= resp.status_code < 600 or resp.status_code == 429:
            # 由 tenacity 接管重试
            raise httpx.HTTPStatusError(
                f"reranker {resp.status_code}", request=resp.request, response=resp
            )
        if resp.status_code >= 400:
            # 4xx 业务错误（401/400 等），不重试，记 warning 后让上层 fallback
            logger.warning(
                "Reranker API 返回 4xx",
                status=resp.status_code,
                body=resp.text[:200],
            )
            return None
        data = resp.json()
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            return None
        scores = [0.0] * len(documents)
        for item in results:
            idx = item.get("index")
            score = item.get("relevance_score")
            if isinstance(idx, int) and 0 <= idx < len(scores) and isinstance(score, (int, float)):
                scores[idx] = float(score)
        return scores

    def _is_retryable(exc: BaseException) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            sc = exc.response.status_code
            return 500 <= sc < 600 or sc == 429
        return False

    attempt = 0
    try:
        async for retry in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=0.5, max=2.0),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with retry:
                attempt += 1
                try:
                    return await _do_call()
                except Exception as exc:
                    if _is_retryable(exc):
                        logger.warning(
                            "Reranker 瞬时错误，将重试",
                            attempt=attempt,
                            error=type(exc).__name__,
                        )
                    raise
    except Exception as exc:
        # 重试用尽 / 不可重试错误：记 warning，让上层走 fallback
        logger.warning("Reranker API 调用失败", error=str(exc)[:200], attempts=attempt)
        return None
    return None


async def rerank(
    query: str,
    chunks: Sequence[RuleChunk],
    *,
    top_k: int | None = None,
) -> RerankResult:
    """对召回结果做精排，返回带状态的 TopK。

    - 关闭或失败时降级：保留原顺序，分数按 1 - rank/N 线性递减（仍能排序但分布塌缩）。
    - 命中缓存的 (query, doc) 对不重复请求 API。
    - top_k=None 表示不截断，调用方自行决定保留多少。
    - status 字段标识本次的分数信号是否可信，便于上层暴露给前端调试。
    """
    if not chunks:
        return RerankResult(items=[], status="no_input")

    if not settings.reranker_enabled:
        return RerankResult(items=_fallback_items(chunks, top_k), status="disabled")

    docs = [_build_doc_text(c) for c in chunks]

    # 缓存命中收集
    cache_keys = [_cache_key(query, d) for d in docs]
    cached_scores: dict[int, float] = {}
    pending_idx: list[int] = []
    pending_docs: list[str] = []
    for i, key in enumerate(cache_keys):
        s = _cache_get(key)
        if s is not None:
            cached_scores[i] = s
        else:
            pending_idx.append(i)
            pending_docs.append(docs[i])

    fresh_scores: list[float] | None = None
    if pending_docs:
        fresh_scores = await _call_rerank_api(query, pending_docs)
        if fresh_scores is None:
            # API 失败：不让局部命中的缓存把整体打散；直接走 fallback
            return RerankResult(items=_fallback_items(chunks, top_k), status="fallback")
        for j, idx in enumerate(pending_idx):
            cached_scores[idx] = fresh_scores[j]
            _cache_put(cache_keys[idx], fresh_scores[j])

    ranked = [RerankedChunk(chunk=c, score=cached_scores.get(i, 0.0)) for i, c in enumerate(chunks)]
    ranked.sort(key=lambda r: r.score, reverse=True)
    if top_k is not None:
        ranked = ranked[:top_k]

    # 全命中缓存（pending_docs 为空）→ status=cached；否则只要打过 API 一次就是 ok
    status: RerankStatus = "cached" if not pending_docs else "ok"
    return RerankResult(items=ranked, status=status)


def _fallback_items(chunks: Sequence[RuleChunk], top_k: int | None) -> list[RerankedChunk]:
    """Reranker 不可用时的兜底：保留原召回顺序，分数线性递减。

    分数范围 [0.5, 1.0]，避免 0 分让上层把它当成 low confidence
    （召回阶段已经过滤过，本来就是合理候选）。
    """
    n = len(chunks)
    if n == 0:
        return []
    out = [RerankedChunk(chunk=c, score=1.0 - 0.5 * (i / max(n - 1, 1))) for i, c in enumerate(chunks)]
    if top_k is not None:
        out = out[:top_k]
    return out


# 向后兼容旧签名（hybrid_search 的 fallback 路径用 _fallback_rank 包成 RerankResult）
def _fallback_rank(chunks: Sequence[RuleChunk], top_k: int | None) -> list[RerankedChunk]:
    return _fallback_items(chunks, top_k)


def confidence_hint_from(scores: Sequence[float]) -> str:
    """根据 top score 给出 high/medium/low 提示，喂给 LLM 决策是否继续检索。"""
    if not scores:
        return "low"
    top = max(scores)
    if top >= settings.retrieval_high_threshold:
        return "high"
    if top >= settings.retrieval_low_threshold:
        return "medium"
    return "low"
