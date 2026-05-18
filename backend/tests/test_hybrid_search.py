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

    chunks = await hybrid_search(db, query="完全不相关", section_id="613.1a", top_k=3)
    assert len(chunks) >= 1
    assert chunks[0].section_id == "613.1a"


@pytest.mark.asyncio
async def test_hybrid_search_keyword_returns_results(db) -> None:
    """关键词检索能命中 title/content。"""
    db.add_all([
        _chunk(10, section_id="200.1", title="层系统说明", content="持续效应分七层"),
        _chunk(11, section_id="300.1", title="无关规则", content="叠回应规则"),
    ])
    await db.commit()

    chunks = await hybrid_search(db, query="层系统", top_k=5)
    ids = [c.id for c in chunks]
    assert 10 in ids


@pytest.mark.asyncio
async def test_hybrid_search_empty_query(db) -> None:
    """空 query + 无 section_id 应返回空。"""
    chunks = await hybrid_search(db, query="", top_k=5)
    assert chunks == []
