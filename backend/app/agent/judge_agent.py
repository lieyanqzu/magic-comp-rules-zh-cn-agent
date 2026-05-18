"""裁判 Agent：使用 OpenAI Chat Completions API 编排工具调用和回答生成。

设计要点：
- 不直接持有 DB session：通过 RuleSearcher 协议注入检索能力，便于测试与解耦。
- AsyncOpenAI client 单例复用：避免每次请求构造新连接池。
- LLM 调用 tenacity 重试：429/5xx 自动指数退避。
- 输出 token 用量、tool 轮次到事件流，便于 service 层入库统计。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.agent.schemas import CardRef, JudgeResponse, RuleRef
from app.core.config import settings
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.retrieval.hybrid_search import RuleSearcher
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


# 模块级单例：复用 httpx 连接池，避免每个请求都构造新 client
_openai_client: AsyncOpenAI | None = None


def get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=httpx.Timeout(
                connect=10, read=settings.llm_request_timeout, write=10, pool=10
            ),
        )
    return _openai_client


def _build_client(api_key: str | None, base_url: str | None) -> AsyncOpenAI:
    """请求级 LLM 客户端：当用户带了自己的 key/base_url 时构造一次性 client。

    没传任何覆盖参数则复用模块单例（连接池更高效）。
    """
    if not api_key and not base_url:
        return get_openai_client()
    return AsyncOpenAI(
        api_key=api_key or settings.openai_api_key,
        base_url=base_url or settings.openai_base_url,
        timeout=httpx.Timeout(
            connect=10, read=settings.llm_request_timeout, write=10, pool=10
        ),
    )


def _load_system_prompt() -> str:
    prompt_path = Path(__file__).parent / "prompts" / "mtg_judge_zh.md"
    return prompt_path.read_text(encoding="utf-8")


def _event(event_type: str, **data: object) -> dict:
    """构建 SSE 事件。"""
    return {"type": event_type, **data}


# 哪些异常需要重试（瞬时错误）
_RETRYABLE_TYPES = (RateLimitError, APIConnectionError, httpx.TimeoutException)


def _should_retry(exc: BaseException) -> bool:
    """判断 LLM 调用异常是否值得重试。

    - RateLimitError / APIConnectionError / httpx.TimeoutException：瞬时网络/限流，重试。
    - APIStatusError：仅 5xx 重试；4xx（如 400 prompt 过长、401 auth 失败）属于业务错误，
      重试只会浪费配额并延长用户等待。
    """
    if isinstance(exc, _RETRYABLE_TYPES):
        return True
    if isinstance(exc, APIStatusError):
        return 500 <= exc.status_code < 600
    return False


class JudgeAgent:
    """裁判 Agent。

    依赖注入：
        searcher: 规则检索协议，由 service 层用 db/redis 构造好传入。
        client: OpenAI 客户端，可注入 mock。

    Args:
        max_tool_rounds: 最大工具调用轮次。0 / None 时使用 settings.llm_max_tool_rounds。
        request_id: 用于贯穿日志的 trace id。
    """

    def __init__(
        self,
        searcher: RuleSearcher,
        *,
        client: AsyncOpenAI | None = None,
        max_tool_rounds: int | None = None,
        temperature: float | None = None,
        model: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.searcher = searcher
        self.client = client or get_openai_client()
        self.system_prompt = _load_system_prompt()
        self.max_tool_rounds = max_tool_rounds or settings.llm_max_tool_rounds
        self.temperature = temperature if temperature is not None else settings.llm_temperature
        # 允许 service 层注入用户自带 model 覆盖默认值
        self.model = model or settings.openai_model
        self.request_id = request_id

        # 累计 token / 轮次，service 层可读取入库
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.tool_rounds = 0

    async def _llm_call(self, *, messages: list, tools: list | None = None, force_no_tools: bool = False):
        """LLM 调用 + tenacity 指数退避重试。

        force_no_tools: 达到最大轮次时调用，禁止再发起工具，强制收尾。
        """
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": self.temperature,
        }
        if tools and not force_no_tools:
            kwargs["tools"] = tools

        attempt = 0
        async for retry in AsyncRetrying(
            stop=stop_after_attempt(settings.llm_max_retries),
            wait=wait_exponential(
                multiplier=1,
                min=settings.llm_retry_min_wait,
                max=settings.llm_retry_max_wait,
            ),
            retry=retry_if_exception(_should_retry),
            reraise=True,
        ):
            with retry:
                attempt += 1
                try:
                    response = await self.client.chat.completions.create(**kwargs)
                except Exception as e:
                    if _should_retry(e):
                        logger.warning(
                            "LLM 瞬时错误，将重试",
                            attempt=attempt,
                            error=type(e).__name__,
                            status=getattr(e, "status_code", None),
                            request_id=self.request_id,
                        )
                    raise

                # 累加 token 用量
                if response.usage:
                    self.prompt_tokens += response.usage.prompt_tokens
                    self.completion_tokens += response.usage.completion_tokens
                    self.total_tokens += response.usage.total_tokens
                return response
        # 不会走到这里
        raise LLMError("LLM 调用未返回响应")

    async def _execute_tool(
        self, name: str, arguments: dict, original_question: str = ""
    ) -> tuple[str, dict | None]:
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
        if name == "search_rules":
            query = arguments.get("query", "")
            section_id = arguments.get("section_id")
            doc_types = arguments.get("document_types")
            chunks = await self.searcher.search(
                query=query,
                section_id=section_id,
                document_types=doc_types,
                top_k=10,
                vector_query=original_question,
            )
            results = [
                {
                    "section_id": c.section_id,
                    "title": c.title[:100],
                    "content": c.content[:300],
                    "source_path": c.source_path,
                    "document_type": c.document_type,
                }
                for c in chunks
            ]
            # 完整版（不截断）保留给最终结构化回答用
            full_chunks = [
                {
                    "section_id": c.section_id,
                    "title": c.title,
                    "content": c.content,
                    "source_path": c.source_path,
                    "document_type": c.document_type,
                }
                for c in chunks
            ]
            return (
                json.dumps(results, ensure_ascii=False),
                {
                    "query": query,
                    "section_id": section_id,
                    "results_count": len(results),
                    "chunks": full_chunks,
                },
            )
        if name == "search_cards":
            query = arguments.get("query", "")
            result = await search_cards(query)
            if result:
                return json.dumps(result, ensure_ascii=False, default=str), result
            return json.dumps({"count": 0, "items": [], "query": query}, ensure_ascii=False), None
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False), None

    async def ask_stream(
        self,
        question: str,
        language: str = "zh-CN",
        history: list[dict] | None = None,
    ) -> AsyncIterator[dict]:
        """流式执行裁判问答，逐事件 yield。

        history: [{"role": "user"|"assistant", "content": str}, ...]
            过往轮次，由前端从对话历史中抽取（assistant 只传 answer 文本，省 token）。
            当前 question 不应包含在内 —— 它会单独作为最后一条 user message 追加。
        """
        yield _event("start", question=question, request_id=self.request_id)

        rule_numbers = extract_rule_numbers(question)
        if rule_numbers:
            yield _event("thinking", content=f"检测到规则编号：{', '.join(rule_numbers)}，将优先查询。")

        rule_hint = (
            f"\n\n[系统提示] 检测到规则编号：{', '.join(rule_numbers)}，请优先查询。"
            if rule_numbers
            else ""
        )

        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]
        # 历史以 user/assistant 朴素文本注入；只保证 role 合法、内容非空
        if history:
            for msg in history:
                role = msg.get("role")
                content = (msg.get("content") or "").strip()
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
        messages.append(
            {"role": "user", "content": f"{question}{rule_hint}\n\n请严格按照 JSON 格式返回结构化结果。"}
        )

        collected_cards: list[dict] = []
        collected_rules: list[dict] = []

        for round_idx in range(self.max_tool_rounds):
            self.tool_rounds = round_idx + 1
            yield _event("thinking", content=f"第 {round_idx + 1} 轮推理...")

            try:
                response = await self._llm_call(messages=messages, tools=TOOLS)
            except Exception as e:
                logger.exception("LLM 调用失败", request_id=self.request_id)
                yield _event("error", content=f"LLM 调用失败: {e}")
                raise LLMError(f"LLM 调用失败: {e}") from e

            choice = response.choices[0]

            if choice.finish_reason == "stop" or not choice.message.tool_calls:
                yield _event("thinking", content="推理完成，生成最终回答...")
                parsed = self._parse_response(
                    choice.message.content or "{}", collected_cards, collected_rules
                )
                yield _event("answer", data=parsed.model_dump())
                return

            messages.append(choice.message)
            for tool_call in choice.message.tool_calls:
                try:
                    func_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    func_args = {}

                func_name = tool_call.function.name
                yield _event("tool_call", tool=func_name, args=func_args)

                meta: dict | None = None
                try:
                    result_str, meta = await self._execute_tool(
                        func_name, func_args, original_question=question
                    )
                except Exception as e:
                    logger.warning(
                        "工具调用失败",
                        tool=func_name,
                        error=str(e)[:100],
                        request_id=self.request_id,
                    )
                    result_str = json.dumps({"error": f"工具调用失败: {e}"}, ensure_ascii=False)
                    yield _event("tool_result", tool=func_name, status="error", error=str(e)[:100])

                if func_name == "resolve_card":
                    if meta and meta.get("found"):
                        collected_cards.append(meta)
                        display_text = meta.get("translated_text") or meta.get("oracle_text") or ""
                        display_type = meta.get("translated_type") or meta.get("type_line") or ""
                        yield _event(
                            "tool_result",
                            tool=func_name,
                            status="found",
                            name=meta.get("name"),
                            oracle_name=meta.get("oracle_name"),
                            display_text=display_text[:300],
                            display_type=display_type,
                            mana_cost=meta.get("mana_cost"),
                            has_faces=bool(meta.get("faces")),
                            has_rulings=bool(meta.get("rulings")),
                        )
                    elif meta is not None:
                        yield _event(
                            "tool_result", tool=func_name, status="not_found", name=func_args.get("card_name")
                        )
                elif func_name == "search_rules":
                    count = meta.get("results_count", 0) if meta else 0
                    if meta:
                        # 累加真实检索结果到 collected_rules，供 _parse_response 用中文回填
                        for chunk in meta.get("chunks", []):
                            collected_rules.append(chunk)
                    yield _event(
                        "tool_result",
                        tool=func_name,
                        query=func_args.get("query"),
                        section_id=func_args.get("section_id"),
                        results_count=count,
                    )
                elif func_name == "search_cards":
                    if meta:
                        items = meta.get("items", [])
                        yield _event(
                            "tool_result",
                            tool=func_name,
                            status="found",
                            count=meta.get("count", 0),
                            items=items[:10],
                        )
                    else:
                        yield _event(
                            "tool_result", tool=func_name, status="empty", query=func_args.get("query")
                        )
                else:
                    yield _event("tool_result", tool=func_name, status="ok")

                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result_str})

        # 达到最大轮次：明确告知模型不能再调工具
        yield _event(
            "thinking",
            content=f"已达最大工具调用轮次（{self.max_tool_rounds}），将基于已有信息生成最终回答。",
        )
        messages.append(
            {
                "role": "system",
                "content": (
                    "已达到最大工具调用轮次，禁止再发起任何工具调用。"
                    "请仅依据上文已收集的信息给出最终 JSON 回答。"
                    "若信息不足，请将 confidence 标为 low 且在 reasoning_summary 中说明。"
                ),
            }
        )
        try:
            response = await self._llm_call(messages=messages, force_no_tools=True)
            parsed = self._parse_response(
                response.choices[0].message.content or "{}", collected_cards, collected_rules
            )
            yield _event("answer", data=parsed.model_dump())
        except Exception as e:
            yield _event("error", content=f"最终回答生成失败: {e}")
            raise LLMError(f"最终回答生成失败: {e}") from e

    async def ask(
        self,
        question: str,
        language: str = "zh-CN",
        history: list[dict] | None = None,
    ) -> JudgeResponse:
        """非流式接口，兼容旧代码。"""
        async for event in self.ask_stream(question, language, history=history):
            if event["type"] == "answer":
                return JudgeResponse(**event["data"])
            if event["type"] == "error":
                raise LLMError(event["content"])
        raise LLMError("未生成回答")

    def _parse_response(self, raw_content: str, tool_cards: list[dict], tool_rules: list[dict]) -> JudgeResponse:
        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError:
            logger.warning(
                "LLM 返回非 JSON 内容", content=raw_content[:200], request_id=self.request_id
            )
            return JudgeResponse(
                answer=raw_content,
                summary="无法解析为结构化回答",
                confidence="low",
                reasoning_summary="模型返回了非 JSON 格式的内容",
                needs_human_judge=True,
            )

        # ---- 牌张：以工具数据为权威源 ----
        # 工具数据按 name (中文) 和 oracle_name (英文) 双索引，便于 LLM 用任一形式引用都能匹配
        tool_by_name: dict[str, dict] = {}
        for tc in tool_cards:
            for key in (tc.get("name"), tc.get("oracle_name")):
                if key:
                    tool_by_name.setdefault(key, tc)

        merged_cards: list[dict] = []
        seen_keys: set[str] = set()

        def _card_keys(c: dict) -> list[str]:
            return [k for k in (c.get("name"), c.get("oracle_name")) if k]

        # 1) 优先按 LLM 引用顺序输出，但用工具数据覆盖空字段
        for llm_card in data.get("cards", []):
            tool_match: dict | None = None
            for key in _card_keys(llm_card):
                if key in tool_by_name:
                    tool_match = tool_by_name[key]
                    break
            if tool_match:
                # 工具数据为底，LLM 的非空字段做兜底（极少触发，防止 oracle_text 仍缺时丢失）
                merged = {**llm_card, **{k: v for k, v in tool_match.items() if v}}
            else:
                merged = llm_card
            for k in _card_keys(merged):
                seen_keys.add(k)
            merged_cards.append(merged)

        # 2) 工具拿到但 LLM 没引用的，追加到末尾
        for tc in tool_cards:
            if not any(k in seen_keys for k in _card_keys(tc)):
                merged_cards.append(tc)
                for k in _card_keys(tc):
                    seen_keys.add(k)

        cards = [self._build_card_ref(c) for c in merged_cards]

        # ---- 规则：按 section_id 回填真实中文 title/content ----
        # 同一 section_id 可能在多次检索中重复出现，保留首次（或更长内容）
        rule_by_sid: dict[str, dict] = {}
        for tr in tool_rules:
            sid = tr.get("section_id")
            if not sid:
                continue
            existing = rule_by_sid.get(sid)
            if existing is None or len(tr.get("content") or "") > len(existing.get("content") or ""):
                rule_by_sid[sid] = tr

        rules: list[RuleRef] = []
        for r in data.get("rules", []):
            sid = r.get("section_id", "")
            tool_rule = rule_by_sid.get(sid)
            if tool_rule:
                # 工具数据胜出：title/content 用真实中文规则文本
                content = tool_rule.get("content") or ""
                snippet = content[:400] if content else (r.get("content_snippet") or "")
                rules.append(
                    RuleRef(
                        section_id=sid,
                        title=tool_rule.get("title") or r.get("title", ""),
                        content_snippet=snippet,
                        source_path=tool_rule.get("source_path") or r.get("source_path", ""),
                    )
                )
            else:
                # LLM 引用了一条没检索到的规则号 — 保留 LLM 写法但标记可信度
                rules.append(
                    RuleRef(
                        section_id=sid,
                        title=r.get("title", ""),
                        content_snippet=r.get("content_snippet", ""),
                        source_path=r.get("source_path", ""),
                    )
                )

        return JudgeResponse(
            answer=data.get("answer", ""),
            summary=data.get("summary", ""),
            confidence=data.get("confidence", "medium"),
            cards=cards,
            rules=rules,
            reasoning_summary=data.get("reasoning_summary", ""),
            needs_human_judge=data.get("needs_human_judge", False),
        )

    @staticmethod
    def _build_card_ref(data: dict) -> CardRef:
        from app.agent.schemas import CardFace, CardRuling

        faces = [CardFace(**f) for f in data.get("faces", []) if isinstance(f, dict)]
        rulings = [CardRuling(**r) for r in data.get("rulings", []) if isinstance(r, dict)]

        oracle_text = data.get("oracle_text") or ""
        translated_text = data.get("translated_text") or ""
        type_line = data.get("type_line") or ""
        translated_type = data.get("translated_type") or ""

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
