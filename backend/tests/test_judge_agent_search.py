"""JudgeAgent 的搜索历史去重与预算注入。"""

from unittest.mock import AsyncMock

import pytest

from app.agent.judge_agent import JudgeAgent
from app.db.models import RuleChunk
from app.retrieval.reranker import RerankedChunk


def _ranked(id_: int, section_id: str, content: str = "内容", score: float = 0.8) -> RerankedChunk:
    return RerankedChunk(
        chunk=RuleChunk(
            id=id_,
            document_type="cr",
            source_path="test.md",
            section_id=section_id,
            title=f"{section_id} 标题",
            content=content,
        ),
        score=score,
    )


def _make_agent(searcher_results: list[RerankedChunk]) -> JudgeAgent:
    searcher = AsyncMock()
    searcher.search = AsyncMock(return_value=searcher_results)
    return JudgeAgent(searcher=searcher, client=AsyncMock(), model="test-model")


@pytest.mark.asyncio
async def test_search_rules_returns_score_metadata() -> None:
    """工具结果应带 best_score / confidence_hint / rounds_left / matches[].score。"""
    import json

    agent = _make_agent([_ranked(1, "613.1a", "层系统内容", 0.92)])
    raw, meta = await agent._execute_tool(
        "search_rules", {"query": "层"}, original_question="层是怎么应用的", rounds_left=3
    )
    payload = json.loads(raw)
    assert payload["best_score"] >= 0.9
    assert payload["confidence_hint"] == "high"
    assert payload["rounds_left"] == 3
    assert payload["matches"][0]["score"] == 0.92
    assert payload["duplicated_call"] is False
    assert meta is not None
    assert "chunks" in meta  # internal meta 带 chunks 供回填


@pytest.mark.asyncio
async def test_search_rules_dedup_returns_cached_with_flag() -> None:
    """同 (query, section_id, doc_types) 第二次调用应被识别为重复，复用缓存。"""
    import json

    agent = _make_agent([_ranked(1, "613.1a", "层系统内容", 0.85)])
    await agent._execute_tool(
        "search_rules", {"query": "层"}, original_question="层", rounds_left=3
    )
    # searcher 已经调用过一次
    assert agent.searcher.search.call_count == 1

    raw, meta = await agent._execute_tool(
        "search_rules", {"query": "层"}, original_question="层", rounds_left=2
    )
    # 第二次不应再触发 searcher
    assert agent.searcher.search.call_count == 1

    payload = json.loads(raw)
    assert payload["duplicated_call"] is True
    assert payload["rounds_left"] == 2  # rounds_left 是调用时态，不该被缓存
    assert "note" in payload  # 提示 LLM 别再重复


@pytest.mark.asyncio
async def test_search_history_summary_grows_per_unique_call() -> None:
    """每次新查询都应在 _search_summary 中新增一条；重复查询不增加。

    用 medium 分数（0.5）避免触发高置信短路 — 那是另一条独立路径。
    """
    agent = _make_agent([_ranked(1, "100.1", "内容", 0.5)])
    await agent._execute_tool("search_rules", {"query": "A"}, original_question="q", rounds_left=3)
    await agent._execute_tool("search_rules", {"query": "B"}, original_question="q", rounds_left=2)
    assert len(agent._search_summary) == 2
    # 重复 "A"
    await agent._execute_tool("search_rules", {"query": "A"}, original_question="q", rounds_left=1)
    assert len(agent._search_summary) == 2


@pytest.mark.asyncio
async def test_search_rules_includes_search_history_in_payload() -> None:
    """LLM 看到的 payload 必须带 search_history，让它能感知已搜过什么。"""
    import json

    agent = _make_agent([_ranked(1, "100.1", "内容", 0.6)])
    await agent._execute_tool("search_rules", {"query": "first"}, original_question="q", rounds_left=4)
    raw, _ = await agent._execute_tool(
        "search_rules", {"query": "second"}, original_question="q", rounds_left=3
    )
    payload = json.loads(raw)
    history = payload.get("search_history", [])
    assert len(history) == 1  # 第二次调用时只能看到第一次的历史
    assert history[0]["query"] == "first"


@pytest.mark.asyncio
async def test_search_rules_normalizes_query_for_dedup() -> None:
    """空白/大小写归一化后等价的 query 视作重复。"""
    agent = _make_agent([_ranked(1, "100.1", "内容", 0.6)])
    await agent._execute_tool("search_rules", {"query": "Layer 系统"}, original_question="q", rounds_left=3)
    await agent._execute_tool(
        "search_rules", {"query": "  layer   系统  "}, original_question="q", rounds_left=2
    )
    # 归一化后等价，searcher 只应被调用一次
    assert agent.searcher.search.call_count == 1


@pytest.mark.asyncio
async def test_search_rules_short_circuits_after_high_hit() -> None:
    """累计达到 _HIGH_HIT_LIMIT 次 high 命中后，下一次 search_rules 直接短路。

    防御 LLM 在 prompt 规则下仍反复换措辞调用同主题搜索的情况。
    """
    import json as _json

    agent = _make_agent([_ranked(1, "613.1a", "层 1 内容", 0.95)])
    # 第一次：high 命中（best=0.95），写入 _search_summary
    await agent._execute_tool("search_rules", {"query": "层"}, original_question="q", rounds_left=4)
    assert agent.searcher.search.call_count == 1
    assert any(s.get("confidence_hint") == "high" for s in agent._search_summary)

    # 第二次：换关键词但已经累计 1 次 high → 短路
    raw, meta = await agent._execute_tool(
        "search_rules", {"query": "完全不同的查询"}, original_question="q", rounds_left=3
    )
    payload = _json.loads(raw)
    assert payload["high_hit_satisfied"] is True
    assert payload["high_hit_count"] >= 1
    assert payload["matches"] == []
    assert "note" in payload  # 提示 LLM 立即收尾
    # 短路时不应再触发 searcher
    assert agent.searcher.search.call_count == 1
    # meta 也带上短路标记，事件流可以渲染
    assert meta is not None and meta.get("high_hit_satisfied") is True


@pytest.mark.asyncio
async def test_search_rules_no_short_circuit_when_only_medium_low() -> None:
    """仅有 medium / low 命中时，第二次 search_rules 仍会执行（不应被误短路）。"""
    agent = _make_agent([_ranked(1, "100.1", "内容", 0.55)])
    await agent._execute_tool("search_rules", {"query": "A"}, original_question="q", rounds_left=4)
    await agent._execute_tool("search_rules", {"query": "B"}, original_question="q", rounds_left=3)
    # 两次都不是 high，第二次应正常执行
    assert agent.searcher.search.call_count == 2


# ---- JSON 契约保护 ----


@pytest.mark.asyncio
async def test_parse_response_with_repair_accepts_valid_json() -> None:
    """合法 JSON 走 happy path，不应触发 salvage。"""
    agent = _make_agent([])
    valid_json = '{"answer":"OK","summary":"S","confidence":"high","cards":[],"rules":[],"reasoning_summary":"R","needs_human_judge":false}'
    parsed = await agent._parse_response_with_repair(valid_json, [], [], [])
    assert parsed.answer == "OK"
    assert parsed.confidence == "high"


@pytest.mark.asyncio
async def test_salvage_recovers_truncated_json() -> None:
    """LLM 输出被 max_tokens 截断时，salvage 应能补尾巴恢复出 dict。"""
    agent = _make_agent([])
    truncated = (
        '{"answer":"先攻和死触的交互核心是两个伤害步骤","summary":"先攻先打死触","'
        'confidence":"high","cards":[],"rules":[],"reasoning_summary":"R","needs_human_judge":false'
    )
    parsed = await agent._parse_response_with_repair(truncated, [], [], [])
    # truncated 末尾少了 } - salvage 应能补回
    assert "先攻和死触" in parsed.answer
    assert parsed.confidence == "high"


@pytest.mark.asyncio
async def test_salvage_extracts_answer_from_markdown_wrapped() -> None:
    """LLM 输出 ```json ... ``` 围栏内带有合法 JSON 时，salvage 应能抽出。"""
    agent = _make_agent([])
    wrapped = (
        '```json\n{"answer":"层7生效","summary":"层7","confidence":"high","cards":[],"rules":[],'
        '"reasoning_summary":"R","needs_human_judge":false}\n```'
    )
    parsed = await agent._parse_response_with_repair(wrapped, [], [], [])
    assert parsed.answer == "层7生效"


@pytest.mark.asyncio
async def test_salvage_falls_back_to_full_text_when_only_markdown() -> None:
    """LLM 完全没出 JSON、只有 markdown 散文时，salvage 把整段文本塞进 answer。"""
    agent = _make_agent([])
    raw = "## 这是 markdown 散文\n\n这里没有任何 JSON。"
    parsed = await agent._parse_response_with_repair(raw, [], [], [])
    assert "markdown 散文" in parsed.answer
    # 没结构化字段时 confidence 默认 medium
    assert parsed.confidence == "medium"


@pytest.mark.asyncio
async def test_salvage_handles_empty_input() -> None:
    """完全空输入回到 fallback：confidence=low + needs_human_judge=true。"""
    agent = _make_agent([])
    parsed = await agent._parse_response_with_repair("", [], [], [])
    assert parsed.confidence == "low"
    assert parsed.needs_human_judge is True


@pytest.mark.asyncio
async def test_parse_response_rejects_empty_field_dict() -> None:
    """LLM 在 force_no_tools 路径偶发返回 `{}` 或所有字段为空的 JSON，
    应被识别为无效响应触发 salvage，而不是当作"成功的空答案"接受。"""
    agent = _make_agent([])
    # 完全空的 JSON 对象
    p1 = await agent._parse_response_with_repair("{}", [], [], [])
    assert p1.confidence == "low"
    assert p1.needs_human_judge is True
    # 字段都在但全是空字符串
    empty_fields = '{"answer":"","summary":"","confidence":"medium","cards":[],"rules":[],"reasoning_summary":"","needs_human_judge":false}'
    p2 = await agent._parse_response_with_repair(empty_fields, [], [], [])
    assert p2.confidence == "low"
    assert p2.needs_human_judge is True


@pytest.mark.asyncio
async def test_parse_response_accepts_summary_only_response() -> None:
    """summary 非空就视为有效响应（answer 可能因 max_tokens 截断为空字符串）。"""
    agent = _make_agent([])
    summary_only = '{"answer":"","summary":"先攻先打死触","confidence":"high","cards":[],"rules":[],"reasoning_summary":"R","needs_human_judge":false}'
    parsed = await agent._parse_response_with_repair(summary_only, [], [], [])
    assert parsed.summary == "先攻先打死触"
    assert parsed.confidence == "high"

