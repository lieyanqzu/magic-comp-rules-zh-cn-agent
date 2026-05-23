"""reranker 单元测试：失败/关闭时降级，开启时按 score 排序。"""

from unittest.mock import patch

import pytest

from app.core.config import settings
from app.db.models import RuleChunk
from app.retrieval.reranker import (
    RerankedChunk,
    _fallback_rank,
    confidence_hint_from,
    rerank,
)


def _chunk(id_: int, content: str = "") -> RuleChunk:
    return RuleChunk(
        id=id_,
        document_type="cr",
        source_path="test.md",
        section_id=f"s{id_}",
        title=f"t{id_}",
        content=content or f"c{id_}",
    )


def test_fallback_rank_preserves_order_and_assigns_descending_scores() -> None:
    chunks = [_chunk(1), _chunk(2), _chunk(3)]
    ranked = _fallback_rank(chunks, top_k=None)
    assert [r.chunk.id for r in ranked] == [1, 2, 3]
    # 分数应该是降序的，且都在 [0.5, 1.0]
    scores = [r.score for r in ranked]
    assert scores == sorted(scores, reverse=True)
    assert all(0.5 <= s <= 1.0 for s in scores)


def test_fallback_rank_top_k_truncates() -> None:
    chunks = [_chunk(i) for i in range(10)]
    ranked = _fallback_rank(chunks, top_k=3)
    assert len(ranked) == 3


def test_fallback_rank_empty() -> None:
    assert _fallback_rank([], top_k=None) == []


@pytest.mark.asyncio
async def test_rerank_disabled_uses_fallback() -> None:
    """reranker_enabled=False 时不应触发 API 调用。"""
    chunks = [_chunk(1, "层系统"), _chunk(2, "无关")]
    with patch("app.retrieval.reranker._call_rerank_api") as mock_api:
        with patch.object(settings, "reranker_enabled", False):
            ranked = await rerank("层", chunks, top_k=None)
    mock_api.assert_not_called()
    assert len(ranked) == 2


@pytest.mark.asyncio
async def test_rerank_api_failure_falls_back() -> None:
    """API 返回 None 时降级，保持原顺序。"""
    chunks = [_chunk(1), _chunk(2)]

    async def _fail(*args, **kwargs):
        return None

    with patch("app.retrieval.reranker._call_rerank_api", _fail):
        with patch.object(settings, "reranker_enabled", True):
            ranked = await rerank("query", chunks, top_k=None)
    assert [r.chunk.id for r in ranked] == [1, 2]


@pytest.mark.asyncio
async def test_rerank_api_success_sorts_by_score() -> None:
    """API 返回分数后应按 score 重排。"""
    chunks = [_chunk(1, "无关内容"), _chunk(2, "高度相关"), _chunk(3, "中等相关")]

    async def _scored(query, docs):
        # 按位置返回分数：第二个最高
        scores = [0.1, 0.9, 0.5]
        return scores[: len(docs)]

    with patch("app.retrieval.reranker._call_rerank_api", _scored):
        with patch.object(settings, "reranker_enabled", True):
            ranked = await rerank("query", chunks, top_k=None)
    assert [r.chunk.id for r in ranked] == [2, 3, 1]
    assert ranked[0].score == 0.9


def test_confidence_hint_thresholds() -> None:
    assert confidence_hint_from([0.9, 0.4, 0.1]) == "high"
    assert confidence_hint_from([0.5, 0.3]) == "medium"
    assert confidence_hint_from([0.2, 0.1]) == "low"
    assert confidence_hint_from([]) == "low"
