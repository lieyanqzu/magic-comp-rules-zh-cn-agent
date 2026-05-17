"""裁判 Agent：使用 OpenAI Chat Completions API 编排工具调用和回答生成。"""

import json
from pathlib import Path

from openai import AsyncOpenAI

from app.agent.schemas import CardRef, JudgeResponse, RuleRef
from app.core.config import settings
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.tools.card_tools import resolve_card_name
from app.tools.rule_tools import extract_rule_numbers

logger = get_logger(__name__)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "resolve_card",
            "description": "解析牌名并获取 Oracle text。当回答涉及具体牌张时必须调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {"card_name": {"type": "string", "description": "牌名"}},
                "required": ["card_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_rules",
            "description": "检索万智牌规则文档。支持精确规则编号匹配和关键词检索。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词"},
                    "section_id": {"type": "string", "description": "精确规则编号（如 613.1a）"},
                    "document_types": {"type": "array", "items": {"type": "string"}, "description": "限定文档类型"},
                },
                "required": ["query"],
            },
        },
    },
]


def _load_system_prompt() -> str:
    prompt_path = Path(__file__).parent / "prompts" / "mtg_judge_zh.md"
    return prompt_path.read_text(encoding="utf-8")


class JudgeAgent:
    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
        self.system_prompt = _load_system_prompt()

    async def _execute_tool(self, name: str, arguments: dict) -> str:
        if name == "resolve_card":
            card_name = arguments.get("card_name", "")
            result = await resolve_card_name(card_name)
            if result:
                return json.dumps({"found": True, "name": result.get("resolved_zh_name", card_name), "oracle_name": result.get("oracle_name"), "oracle_text": result.get("oracle_text"), "type_line": result.get("type_line"), "mana_cost": result.get("mana_cost")}, ensure_ascii=False)
            return json.dumps({"found": False, "name": card_name}, ensure_ascii=False)
        elif name == "search_rules":
            return json.dumps({"note": "请在回答中引用相关规则编号。", "query": arguments.get("query", ""), "section_id": arguments.get("section_id"), "document_types": arguments.get("document_types")}, ensure_ascii=False)
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)

    async def ask(self, question: str, language: str = "zh-CN", max_tool_rounds: int = 5) -> JudgeResponse:
        rule_numbers = extract_rule_numbers(question)
        rule_hint = f"\n\n[系统提示] 检测到规则编号：{', '.join(rule_numbers)}，请优先查询。" if rule_numbers else ""

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"{question}{rule_hint}\n\n请严格按照 JSON 格式返回结构化结果。"},
        ]

        collected_cards: list[dict] = []
        collected_rules: list[dict] = []

        for _round in range(max_tool_rounds):
            try:
                response = await self.client.chat.completions.create(
                    model=settings.openai_model, messages=messages, tools=TOOLS,
                    response_format={"type": "json_object"}, temperature=0.1,
                )
            except Exception as e:
                logger.exception("LLM 调用失败")
                raise LLMError(f"LLM 调用失败: {e}") from e

            choice = response.choices[0]
            if choice.finish_reason == "stop" or not choice.message.tool_calls:
                return self._parse_response(choice.message.content or "{}", collected_cards, collected_rules)

            messages.append(choice.message)
            for tool_call in choice.message.tool_calls:
                try:
                    func_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    func_args = {}
                logger.info("执行工具调用", tool=tool_call.function.name, args=func_args)
                result = await self._execute_tool(tool_call.function.name, func_args)

                if tool_call.function.name == "resolve_card":
                    card_data = json.loads(result)
                    if card_data.get("found"):
                        collected_cards.append(card_data)

                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})

        # 达到最大轮次
        logger.warning("达到最大工具调用轮次")
        try:
            response = await self.client.chat.completions.create(
                model=settings.openai_model, messages=messages,
                response_format={"type": "json_object"}, temperature=0.1,
            )
            return self._parse_response(response.choices[0].message.content or "{}", collected_cards, collected_rules)
        except Exception as e:
            raise LLMError(f"最终回答生成失败: {e}") from e

    def _parse_response(self, raw_content: str, tool_cards: list[dict], tool_rules: list[dict]) -> JudgeResponse:
        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError:
            logger.warning("LLM 返回非 JSON 内容", content=raw_content[:200])
            return JudgeResponse(answer=raw_content, summary="无法解析为结构化回答", confidence="low", reasoning_summary="模型返回了非 JSON 格式的内容", needs_human_judge=True)

        cards = [CardRef(name=c.get("name", ""), oracle_name=c.get("oracle_name"), oracle_text=c.get("oracle_text")) for c in data.get("cards", [])]
        existing_names = {c.name for c in cards}
        for tc in tool_cards:
            if tc.get("name") and tc["name"] not in existing_names:
                cards.append(CardRef(name=tc["name"], oracle_name=tc.get("oracle_name"), oracle_text=tc.get("oracle_text")))

        rules = [RuleRef(section_id=r.get("section_id", ""), title=r.get("title", ""), content_snippet=r.get("content_snippet", ""), source_path=r.get("source_path", "")) for r in data.get("rules", [])]

        return JudgeResponse(
            answer=data.get("answer", ""), summary=data.get("summary", ""),
            confidence=data.get("confidence", "medium"), cards=cards, rules=rules,
            reasoning_summary=data.get("reasoning_summary", ""),
            needs_human_judge=data.get("needs_human_judge", False),
        )
