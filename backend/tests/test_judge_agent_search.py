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


# ---- rerank_query 注入：让 cross-encoder 拿到牌张 oracle 文本而非 LLM 蒸馏关键词 ----


def test_build_rerank_query_with_cards_includes_oracle_text() -> None:
    """已 resolve 的牌张 oracle 文本应被拼进 rerank query，作为 cross-encoder 的同义信号。"""
    cards = [
        {
            "name": "谦卑",
            "oracle_name": "Humility",
            "translated_text": "所有生物失去所有异能，且基础力量与防御力为1/1。",
            "oracle_text": "All creatures lose all abilities and have base power and toughness 1/1.",
        }
    ]
    rq = JudgeAgent._build_rerank_query("谦卑和库多怎么交互？", cards)
    assert rq is not None
    assert "失去所有异能" in rq
    # 用户原句也保留，提供语义上下文
    assert "谦卑和库多怎么交互" in rq


def test_build_rerank_query_prefers_chinese_oracle() -> None:
    """中英文都有时优先用中文，中文规则库的 chunk 是中文，匹配更准。"""
    cards = [{
        "name": "谦卑",
        "translated_text": "中文 oracle",
        "oracle_text": "english oracle",
    }]
    rq = JudgeAgent._build_rerank_query("Q", cards)
    assert rq is not None and "中文 oracle" in rq and "english oracle" not in rq


def test_build_rerank_query_falls_back_to_english() -> None:
    """中文 oracle 缺失时回退到英文，不应丢失信号。"""
    cards = [{"name": "X", "translated_text": "", "oracle_text": "english only"}]
    rq = JudgeAgent._build_rerank_query("Q", cards)
    assert rq is not None and "english only" in rq


def test_build_rerank_query_returns_none_without_cards() -> None:
    """没解析到牌时返回 None，让下游回退到 query / vector_query 默认链路。"""
    assert JudgeAgent._build_rerank_query("纯规则问题", []) is None
    assert JudgeAgent._build_rerank_query("纯规则问题", [{"name": "X"}]) is None  # 无 oracle 文本


def test_build_rerank_query_truncates_to_limit() -> None:
    """oracle 文本可能很长，拼接后必须截到 _RERANK_QUERY_MAX_LEN 以内，避免拖慢精排。"""
    cards = [{"name": "Big", "translated_text": "啊" * 10_000}]
    rq = JudgeAgent._build_rerank_query("Q", cards)
    assert rq is not None
    assert len(rq) <= JudgeAgent._RERANK_QUERY_MAX_LEN


@pytest.mark.asyncio
async def test_search_rules_passes_rerank_query_with_collected_cards() -> None:
    """_execute_tool 应把 collected_cards 拼装后通过 rerank_query 传给 searcher。"""
    agent = _make_agent([_ranked(1, "613.1f", "层 6 移除异能", 0.9)])
    cards = [{
        "name": "谦卑",
        "translated_text": "所有生物失去所有异能，且基础力量与防御力为1/1。",
    }]
    await agent._execute_tool(
        "search_rules",
        {"query": "层 6 失去 异能"},
        original_question="谦卑和库多怎么交互？",
        rounds_left=3,
        collected_cards=cards,
    )
    kwargs = agent.searcher.search.call_args.kwargs
    assert kwargs["rerank_query"] is not None
    assert "失去所有异能" in kwargs["rerank_query"]
    assert kwargs["query"] == "层 6 失去 异能"  # query 仍传入，作为关键词召回信号


@pytest.mark.asyncio
async def test_search_rules_omits_rerank_query_without_cards() -> None:
    """裸规则问题（没解析到牌）时 rerank_query 为 None，让 hybrid_search 走默认链路。"""
    agent = _make_agent([_ranked(1, "100.1", "内容", 0.6)])
    await agent._execute_tool(
        "search_rules",
        {"query": "层"},
        original_question="层系统怎么应用？",
        rounds_left=3,
        collected_cards=[],
    )
    kwargs = agent.searcher.search.call_args.kwargs
    assert kwargs["rerank_query"] is None


# ---- DeepSeek V4 内部模板 token 污染抢救 ----


@pytest.mark.asyncio
async def test_salvage_strips_deepseek_dsml_tokens() -> None:
    """DeepSeek V4 在 force_no_tools 收尾轮可能把 <｜DSML｜tool_calls> 等模板 token
    漏到 content 里。salvage 必须先剥掉它们，否则 answer 会变成一坨 token 字符串。"""
    from app.agent.judge_agent import _strip_provider_template_tokens
    polluted = (
        '["cr","reference"]</｜｜DSML｜｜parameter>\n'
        '</｜｜DSML｜｜invoke>\n</｜｜DSML｜｜tool_calls>'
    )
    cleaned = _strip_provider_template_tokens(polluted)
    assert "DSML" not in cleaned
    assert "｜" not in cleaned

    agent = _make_agent([])
    parsed = await agent._parse_response_with_repair(polluted, [], [], [])
    # 剥掉 token 后没有可救的字段，进入兜底；answer 不应再含 DSML 字样
    assert "DSML" not in parsed.answer


def test_strip_provider_template_tokens_keeps_normal_html_like_text() -> None:
    """普通 HTML / 角括号文本（不含全角竖线）不应被误删。"""
    from app.agent.judge_agent import _strip_provider_template_tokens
    text = "答：<b>层 7b</b> 中 P/T 设定生效。"
    assert _strip_provider_template_tokens(text) == text

