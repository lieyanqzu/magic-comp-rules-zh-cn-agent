"""流式 SSE 端点测试。"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient

from app.agent.schemas import JudgeResponse

MOCK_EVENTS = [
    {"type": "start", "question": "测试问题"},
    {"type": "thinking", "content": "分析中..."},
    {"type": "tool_call", "tool": "resolve_card", "args": {"card_name": "闪电击"}},
    {"type": "tool_result", "tool": "resolve_card", "status": "found", "name": "闪电击"},
    {"type": "answer", "data": {
        "answer": "闪电击造成3点伤害。",
        "summary": "闪电击造成3点任意目标伤害。",
        "confidence": "high",
        "cards": [{"name": "闪电击", "oracle_name": "Lightning Bolt"}],
        "rules": [{"section_id": "120.5", "title": "伤害", "content_snippet": "伤害不消灭", "source_path": "markdown/1.md"}],
        "reasoning_summary": "根据 Oracle text",
        "needs_human_judge": False,
    }},
]


async def _mock_ask_stream(question, language="zh-CN", max_tool_rounds=5):
    for event in MOCK_EVENTS:
        yield event


async def test_stream_returns_sse_events(client: AsyncClient) -> None:
    """POST /v1/judge/stream 应返回 SSE 事件流。"""
    mock_agent = MagicMock()
    mock_agent.ask_stream = _mock_ask_stream

    with patch("app.api.judge.JudgeAgent", return_value=mock_agent):
        async with client.stream(
            "POST",
            "/v1/judge/stream",
            json={"question": "测试问题"},
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")

            events = []
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    events.append(event)

            # 应包含 start、thinking、tool_call、tool_result、answer、done
            types = [e["type"] for e in events]
            assert "start" in types
            assert "thinking" in types
            assert "tool_call" in types
            assert "tool_result" in types
            assert "answer" in types
            assert "done" in types


async def test_stream_answer_has_correct_structure(client: AsyncClient) -> None:
    """流式回答应包含完整结构化数据。"""
    mock_agent = MagicMock()
    mock_agent.ask_stream = _mock_ask_stream

    with patch("app.api.judge.JudgeAgent", return_value=mock_agent):
        async with client.stream(
            "POST",
            "/v1/judge/stream",
            json={"question": "测试问题"},
        ) as resp:
            events = []
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

            answer_events = [e for e in events if e["type"] == "answer"]
            assert len(answer_events) == 1
            data = answer_events[0]["data"]
            assert "answer" in data
            assert "summary" in data
            assert "confidence" in data
            assert data["confidence"] == "high"
