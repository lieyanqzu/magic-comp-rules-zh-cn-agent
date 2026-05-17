"""裁判 Agent：使用 OpenAI Chat Completions API 编排工具调用和回答生成。"""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from openai import AsyncOpenAI

from app.agent.schemas import CardRef, JudgeResponse, RuleRef
from app.core.config import settings
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.tools.card_tools import resolve_card_name, search_cards
from app.tools.rule_tools import extract_rule_numbers

logger = get_logger(__name__)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "resolve_card",
            "description": "解析牌名并获取完整牌张信息（中文名、英文名、Oracle text、类别、费用、攻防、双面信息、FAQ）。当回答涉及具体牌张时必须调用此工具。注意：使用用户问题中提到的确切牌名，不要自行替换。",
            "parameters": {
                "type": "object",
                "properties": {"card_name": {"type": "string", "description": "用户提到的确切牌名（中文或英文），不要修改或猜测"}},
                "required": ["card_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_rules",
            "description": "检索万智牌规则文档。支持精确规则编号匹配和关键词检索。可以多次调用，每次用不同的关键词覆盖不同机制。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "空格分隔的精炼中文术语（如'运土'、'消灭 坟墓场'、'层 持续效应'），不要用长句"},
                    "section_id": {"type": "string", "description": "精确规则编号（如 613.1a）"},
                    "document_types": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["cr", "reference", "mtr", "ipg"]},
                        "description": "限定文档类型：cr=完整规则, reference=专题参考, mtr=比赛规则, ipg=违规处理指南",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_cards",
            "description": "按条件搜索牌库。支持 Scryfall 风格语法，如 pow=2 tou=3 c:ug t:creature o:trample mv=3 e:mom lang:zhs。当用户要求按属性筛选牌张时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索条件，如 pow=2 tou=3 mv=3 c:ug t:creature"},
                },
                "required": ["query"],
            },
        },
    },
]


def _load_system_prompt() -> str:
    prompt_path = Path(__file__).parent / "prompts" / "mtg_judge_zh.md"
    return prompt_path.read_text(encoding="utf-8")


def _event(event_type: str, **data: object) -> dict:
    """构建 SSE 事件。"""
    return {"type": event_type, **data}


class JudgeAgent:
    def __init__(self) -> None:
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=httpx.Timeout(connect=10, read=120, write=10, pool=10),
        )
        self.system_prompt = _load_system_prompt()
        self._original_question: str = ""

    async def _execute_tool(self, name: str, arguments: dict) -> tuple[str, dict | None]:
        """执行工具，返回 (原始结果字符串, 额外元数据)。"""
        if name == "resolve_card":
            card_name = arguments.get("card_name", "")
            result = await resolve_card_name(card_name)
            if result:
                data = {
                    "found": True,
                    "name": result.get("resolved_zh_name", card_name),
                    "oracle_name": result.get("oracle_name"),
                    "oracle_text": result.get("oracle_text"),
                    "translated_text": result.get("translated_text"),
                    "translated_type": result.get("translated_type"),
                    "type_line": result.get("type_line"),
                    "mana_cost": result.get("mana_cost"),
                    "power": result.get("power"),
                    "toughness": result.get("toughness"),
                    "defense": result.get("defense"),
                    "layout": result.get("layout"),
                    "faces": result.get("faces", []),
                    "rulings": result.get("rulings", []),
                }
                return json.dumps(data, ensure_ascii=False, default=str), data
            return json.dumps({"found": False, "name": card_name}, ensure_ascii=False), None
        elif name == "search_rules":
            query = arguments.get("query", "")
            section_id = arguments.get("section_id")
            doc_types = arguments.get("document_types")
            # 同时用 LLM 关键词和用户原始问题做混合检索
            from app.db.session import async_session_factory
            from app.retrieval.hybrid_search import hybrid_search
            async with async_session_factory() as db:
                # 向量检索用原始问题，关键词检索用 LLM 提取的关键词
                chunks = await hybrid_search(
                    db, query=query, section_id=section_id,
                    document_types=doc_types, top_k=10,
                    vector_query=self._original_question,
                )
                results = [{"section_id": c.section_id, "title": c.title[:100], "content": c.content[:300], "source_path": c.source_path, "document_type": c.document_type} for c in chunks]
            return json.dumps(results, ensure_ascii=False), {"query": query, "section_id": section_id, "results_count": len(results)}
        elif name == "search_cards":
            query = arguments.get("query", "")
            result = await search_cards(query)
            if result:
                return json.dumps(result, ensure_ascii=False, default=str), result
            return json.dumps({"count": 0, "items": [], "query": query}, ensure_ascii=False), None
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False), None

    async def ask_stream(self, question: str, language: str = "zh-CN", max_tool_rounds: int = 5) -> AsyncIterator[dict]:
        """流式执行裁判问答，逐事件 yield。"""
        self._original_question = question
        yield _event("start", question=question)

        rule_numbers = extract_rule_numbers(question)
        if rule_numbers:
            yield _event("thinking", content=f"检测到规则编号：{', '.join(rule_numbers)}，将优先查询。")

        rule_hint = f"\n\n[系统提示] 检测到规则编号：{', '.join(rule_numbers)}，请优先查询。" if rule_numbers else ""

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"{question}{rule_hint}\n\n请严格按照 JSON 格式返回结构化结果。"},
        ]

        collected_cards: list[dict] = []
        collected_rules: list[dict] = []

        for round_idx in range(max_tool_rounds):
            yield _event("thinking", content=f"第 {round_idx + 1} 轮推理...")

            try:
                response = await self.client.chat.completions.create(
                    model=settings.openai_model, messages=messages, tools=TOOLS,
                    response_format={"type": "json_object"}, temperature=0.1,
                )
            except Exception as e:
                logger.exception("LLM 调用失败")
                yield _event("error", content=f"LLM 调用失败: {e}")
                raise LLMError(f"LLM 调用失败: {e}") from e

            choice = response.choices[0]

            # 没有工具调用 → 最终回答
            if choice.finish_reason == "stop" or not choice.message.tool_calls:
                yield _event("thinking", content="推理完成，生成最终回答...")
                parsed = self._parse_response(choice.message.content or "{}", collected_cards, collected_rules)
                yield _event("answer", data=parsed.model_dump())
                return

            # 处理工具调用
            messages.append(choice.message)
            for tool_call in choice.message.tool_calls:
                try:
                    func_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    func_args = {}

                func_name = tool_call.function.name
                yield _event("tool_call", tool=func_name, args=func_args)

                try:
                    result_str, meta = await self._execute_tool(func_name, func_args)
                except Exception as e:
                    logger.warning("工具调用失败", tool=func_name, error=str(e)[:100])
                    result_str = json.dumps({"error": f"工具调用失败: {e}"}, ensure_ascii=False)
                    meta = None
                    yield _event("tool_result", tool=func_name, status="error", error=str(e)[:100])

                # 构建工具结果摘要
                if func_name == "resolve_card":
                    if meta and meta.get("found"):
                        collected_cards.append(meta)
                        display_text = meta.get("translated_text") or meta.get("oracle_text") or ""
                        display_type = meta.get("translated_type") or meta.get("type_line") or ""
                        yield _event("tool_result", tool=func_name, status="found",
                                     name=meta.get("name"),
                                     oracle_name=meta.get("oracle_name"),
                                     display_text=display_text[:300],
                                     display_type=display_type,
                                     mana_cost=meta.get("mana_cost"),
                                     has_faces=bool(meta.get("faces")),
                                     has_rulings=bool(meta.get("rulings")))
                    elif meta is not None:
                        yield _event("tool_result", tool=func_name, status="not_found", name=func_args.get("card_name"))
                    # meta=None 表示上面已经发了 error 事件
                elif func_name == "search_rules":
                    count = meta.get("results_count", 0) if meta else 0
                    yield _event("tool_result", tool=func_name, query=func_args.get("query"), section_id=func_args.get("section_id"), results_count=count)
                elif func_name == "search_cards":
                    if meta:
                        items = meta.get("items", [])
                        yield _event("tool_result", tool=func_name, status="found", count=meta.get("count", 0), items=items[:10])
                    else:
                        yield _event("tool_result", tool=func_name, status="empty", query=func_args.get("query"))
                else:
                    yield _event("tool_result", tool=func_name, status="ok")

                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result_str})

        # 达到最大轮次
        yield _event("thinking", content="达到最大工具调用轮次，生成最终回答...")
        try:
            response = await self.client.chat.completions.create(
                model=settings.openai_model, messages=messages,
                response_format={"type": "json_object"}, temperature=0.1,
            )
            parsed = self._parse_response(response.choices[0].message.content or "{}", collected_cards, collected_rules)
            yield _event("answer", data=parsed.model_dump())
        except Exception as e:
            yield _event("error", content=f"最终回答生成失败: {e}")
            raise LLMError(f"最终回答生成失败: {e}") from e

    async def ask(self, question: str, language: str = "zh-CN", max_tool_rounds: int = 5) -> JudgeResponse:
        """非流式接口，兼容旧代码。"""
        async for event in self.ask_stream(question, language, max_tool_rounds):
            if event["type"] == "answer":
                return JudgeResponse(**event["data"])
            if event["type"] == "error":
                raise LLMError(event["content"])
        raise LLMError("未生成回答")

    def _parse_response(self, raw_content: str, tool_cards: list[dict], tool_rules: list[dict]) -> JudgeResponse:
        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError:
            logger.warning("LLM 返回非 JSON 内容", content=raw_content[:200])
            return JudgeResponse(answer=raw_content, summary="无法解析为结构化回答", confidence="low", reasoning_summary="模型返回了非 JSON 格式的内容", needs_human_judge=True)

        cards = [self._build_card_ref(c) for c in data.get("cards", [])]
        existing_names = {c.name for c in cards}
        for tc in tool_cards:
            if tc.get("name") and tc["name"] not in existing_names:
                cards.append(self._build_card_ref(tc))

        rules = [RuleRef(section_id=r.get("section_id", ""), title=r.get("title", ""), content_snippet=r.get("content_snippet", ""), source_path=r.get("source_path", "")) for r in data.get("rules", [])]

        return JudgeResponse(
            answer=data.get("answer", ""), summary=data.get("summary", ""),
            confidence=data.get("confidence", "medium"), cards=cards, rules=rules,
            reasoning_summary=data.get("reasoning_summary", ""),
            needs_human_judge=data.get("needs_human_judge", False),
        )

    @staticmethod
    def _build_card_ref(data: dict) -> CardRef:
        """从工具结果或 LLM 回答构建 CardRef。"""
        from app.agent.schemas import CardFace, CardRuling

        faces = [CardFace(**f) for f in data.get("faces", []) if isinstance(f, dict)]
        rulings = [CardRuling(**r) for r in data.get("rulings", []) if isinstance(r, dict)]

        oracle_text = data.get("oracle_text") or ""
        translated_text = data.get("translated_text") or ""
        type_line = data.get("type_line") or ""
        translated_type = data.get("translated_type") or ""

        # display 字段：中文优先，降级英文
        display_text = translated_text or oracle_text
        display_type = translated_type or type_line

        return CardRef(
            name=data.get("name", ""),
            oracle_name=data.get("oracle_name"),
            oracle_text=translated_text or oracle_text,
            oracle_text_en=oracle_text if translated_text else None,
            translated_text=translated_text or None,
            translated_type=translated_type or None,
            type_line=display_type or None,
            type_line_en=type_line if translated_type else None,
            mana_cost=data.get("mana_cost"),
            power=str(data["power"]) if data.get("power") is not None else None,
            toughness=str(data["toughness"]) if data.get("toughness") is not None else None,
            defense=str(data["defense"]) if data.get("defense") is not None else None,
            layout=data.get("layout"),
            display_text=display_text or None,
            display_type=display_type or None,
            faces=faces,
            rulings=rulings,
        )
