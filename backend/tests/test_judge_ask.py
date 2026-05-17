"""裁判问答接口测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient

from app.agent.schemas import JudgeResponse

MOCK_RESPONSE = JudgeResponse(
    answer="谦卑会移除蛋白玛珂的所有异能，使其变成 1/1。",
    summary="谦卑移除蛋白玛珂的异能，使其变为 1/1。",
    confidence="high",
    cards=[{"name": "谦卑", "oracle_name": "Humility", "oracle_text": "All creatures lose all abilities and are 1/1."}],
    rules=[{"section_id": "613.1a", "title": "层系统", "content_snippet": "在层 6 中...", "source_path": "magic-comp-rules-zh-cn/markdown/6.md"}],
    reasoning_summary="谦卑的持续效应在层 6a 移除所有生物的异能。",
    needs_human_judge=False,
)


async def test_judge_ask_returns_structured_response(client: AsyncClient) -> None:
    mock_agent = MagicMock()
    mock_agent.ask = AsyncMock(return_value=MOCK_RESPONSE)
    with patch("app.services.judge_service.JudgeAgent", return_value=mock_agent):
        resp = await client.post("/v1/judge/ask", json={"question": "如果我操控谦卑和蛋白玛珂，生物会怎样？"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == MOCK_RESPONSE.answer
        assert data["confidence"] == "high"
        assert isinstance(data["cards"], list)
        assert isinstance(data["rules"], list)
        assert isinstance(data["needs_human_judge"], bool)
        assert "latency_ms" in data


async def test_judge_ask_no_fabrication(client: AsyncClient) -> None:
    no_rule_response = JudgeResponse(
        answer="未找到相关规则。", summary="无法确定。", confidence="low",
        rules=[], reasoning_summary="未找到匹配的规则文档。", needs_human_judge=True,
    )
    mock_agent = MagicMock()
    mock_agent.ask = AsyncMock(return_value=no_rule_response)
    with patch("app.services.judge_service.JudgeAgent", return_value=mock_agent):
        resp = await client.post("/v1/judge/ask", json={"question": "一个完全虚构的问题"})
        data = resp.json()
        assert data["needs_human_judge"] is True
        assert data["rules"] == []
