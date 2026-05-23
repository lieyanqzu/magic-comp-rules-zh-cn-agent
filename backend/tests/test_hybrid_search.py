"""hybrid_search 与 rule_tools 单元测试。"""

from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import RuleChunk
from app.retrieval.hybrid_search import _rrf_fuse, hybrid_search
from app.tools.rule_tools import _split_keywords, extract_rule_numbers


def _chunk(id_: int, section_id: str = "", title: str = "", content: str = "") -> RuleChunk:
    c = RuleChunk(
        id=id_,
        document_type="cr",
        source_path="test.md",
        section_id=section_id or f"s{id_}",
        title=title or f"t{id_}",
        content=content or f"c{id_}",
    )
    return c


def test_rrf_fuse_combines_branches() -> None:
    """两路结果都包含的 chunk，分数应该高于只在单路出现的。"""
    a = [_chunk(1), _chunk(2), _chunk(3)]
    b = [_chunk(2), _chunk(4), _chunk(5)]

    fused = _rrf_fuse([a, b], top_k=5)
    ids = [c.id for c in fused]
    # id=2 在两路都排第 1 / 第 1，应该排在最前
    assert ids[0] == 2


def test_rrf_fuse_top_k_cuts() -> None:
    a = [_chunk(i) for i in range(10)]
    fused = _rrf_fuse([a], top_k=3)
    assert len(fused) == 3


def test_rrf_fuse_handles_empty() -> None:
    fused = _rrf_fuse([[], []], top_k=5)
    assert fused == []


def test_extract_rule_numbers_finds_inline() -> None:
    """规则号埋在长句里也要能识别。"""
    nums = extract_rule_numbers("根据 613.1a 和 100.1 的规定，请参照 707.4。")
    assert "613.1a" in nums
    assert "100.1" in nums
    assert "707.4" in nums


def test_extract_rule_numbers_ignores_random_digits() -> None:
    """非规则号格式的数字串不应误命中。"""
    nums = extract_rule_numbers("有 12 张牌和 1234 点生命")
    assert nums == []


def test_split_keywords() -> None:
    assert _split_keywords("层系统 持续效应") == ["层系统", "持续效应"]
    assert _split_keywords("消灭，坟墓场、流放") == ["消灭", "坟墓场", "流放"]
    assert _split_keywords("") == []


@pytest.mark.asyncio
async def test_hybrid_search_section_id_priority(db) -> None:
    """精确规则号匹配应当前置，不依赖关键词或向量分数。"""
    db.add_all([
        _chunk(1, section_id="613.1a", title="层系统 6a", content="持续效应在层 6a 移除异能"),
        _chunk(2, section_id="100.1", title="一般规则", content="毫不相关的内容"),
    ])
    await db.commit()

    ranked = await hybrid_search(db, query="完全不相关", section_id="613.1a", top_k=3)
    assert len(ranked) >= 1
    assert ranked[0].chunk.section_id == "613.1a"
    # reranker 关闭时 fallback 给 0.5~1.0 区间分数
    assert 0.0 <= ranked[0].score <= 1.0


@pytest.mark.asyncio
async def test_hybrid_search_keyword_returns_results(db) -> None:
    """关键词检索能命中 title/content。"""
    db.add_all([
        _chunk(10, section_id="200.1", title="层系统说明", content="持续效应分七层"),
        _chunk(11, section_id="300.1", title="无关规则", content="叠回应规则"),
    ])
    await db.commit()

    ranked = await hybrid_search(db, query="层系统", top_k=5)
    ids = [r.chunk.id for r in ranked]
    assert 10 in ids


@pytest.mark.asyncio
async def test_hybrid_search_empty_query(db) -> None:
    """空 query + 无 section_id 应返回空结果。"""
    ranked = await hybrid_search(db, query="", top_k=5)
    assert len(ranked) == 0
    assert ranked.rerank_status == "no_input"


@pytest.mark.asyncio
async def test_hybrid_search_returns_scores_in_descending_order(db) -> None:
    """无 reranker 时 fallback 给的分数也应保持降序，方便上层判断 best_score。"""
    db.add_all([
        _chunk(20, section_id="400.1", title="层", content="相关内容 1"),
        _chunk(21, section_id="400.2", title="层", content="相关内容 2"),
        _chunk(22, section_id="400.3", title="层", content="相关内容 3"),
    ])
    await db.commit()

    ranked = await hybrid_search(db, query="层", top_k=10)
    if len(ranked) >= 2:
        scores = [r.score for r in ranked]
        assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_safe_db_branch_isolates_failure(db) -> None:
    """一个分支内部抛异常时，hybrid_search 整体仍能继续，不会让 session 卡死。

    模拟方式：mock search_by_keyword 让它抛异常，确认 hybrid_search 仍返回
    HybridSearchResult（即便结果为空），且后续调用不被污染。
    """
    db.add(_chunk(30, section_id="500.1", title="层系统", content="层 1 内容"))
    await db.commit()

    with patch(
        "app.retrieval.hybrid_search.search_by_keyword",
        new=AsyncMock(side_effect=RuntimeError("模拟 SQL 失败")),
    ):
        result = await hybrid_search(db, query="层", top_k=5)
    # 关键词分支挂了，整体仍应返回（向量分支因 embedding 服务不可用也是 0）
    assert hasattr(result, "rerank_status")
    # 同一 session 之后还能正常用：再发一次不挂的请求
    result2 = await hybrid_search(db, query="完全不相关 xyz", top_k=5)
    assert hasattr(result2, "rerank_status")

