"""裁判 Agent：使用 OpenAI Chat Completions API 编排工具调用和回答生成。

设计要点：
- 不直接持有 DB session：通过 RuleSearcher 协议注入检索能力，便于测试与解耦。
- AsyncOpenAI client 单例复用：避免每次请求构造新连接池。
- LLM 调用 tenacity 重试：429/5xx 自动指数退避。
- 输出 token 用量、tool 轮次到事件流，便于 service 层入库统计。
- 工具结果带 confidence_hint / rounds_left / 已查询历史摘要，让 LLM 自主决策是否继续检索，
  避免"top1 不沾边就盲目重试"的循环。
"""

from __future__ import annotations

import json
import re
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
from app.retrieval.query_expand import expand_query, section_hints_for
from app.retrieval.reranker import confidence_hint_from
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
            "description": (
                "检索万智牌规则文档。一次调用即可拿到带相关度分数的 TopK，"
                "不要为同一意图重复调用。返回结果包含："
                "matches[].score (0~1 reranker 分数)、best_score、"
                "confidence_hint (high≥0.7 / medium 0.4~0.7 / low<0.4)、"
                "rounds_left (剩余可用工具调用次数)、expanded_terms (后端自动扩展的同义词)、"
                "search_history (本对话里已发起过的查询)。"
                "决策原则：confidence_hint=high 时直接基于结果作答，不要再搜；"
                "medium 时若需补充其他角度可继续搜；low 时换关键词或文档类型再试一次，"
                "但相同 (query, section_id) 会被去重，重复调用属于浪费。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "空格分隔的精炼中文术语（如 '层 持续效应'、'消灭 坟墓场'），不要用长句。后端会自动做同义词扩展。"},
                    "section_id": {"type": "string", "description": "精确规则编号（如 613.1a）。已知编号时优先用此参数，置信度直接拉满。"},
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
            "description": "按条件搜索牌张。支持 Scryfall 风格语法，如 pow=2 tou=3 c:ug t:creature o:trample mv=3 e:mom lang:zhs。当用户要求按属性筛选牌张时调用。",
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

# 部分第三方兼容网关的 WAF 会按 SDK 默认 UA（OpenAI/Python、AsyncOpenAI/Python）
# 直接 403。用中性 UA 绕过，对官方端点也无副作用。
_NEUTRAL_USER_AGENT = "mtg-judge-agent/1.0"


def get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=httpx.Timeout(
                connect=10, read=settings.llm_request_timeout, write=10, pool=10
            ),
            default_headers={"User-Agent": _NEUTRAL_USER_AGENT},
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
        default_headers={"User-Agent": _NEUTRAL_USER_AGENT},
    )


def _load_system_prompt() -> str:
    prompt_path = Path(__file__).parent / "prompts" / "mtg_judge_zh.md"
    return prompt_path.read_text(encoding="utf-8")


def _event(event_type: str, **data: object) -> dict:
    """构建 SSE 事件。"""
    return {"type": event_type, **data}


def _extract_json(raw: str) -> str:
    """从模型输出里提取 JSON 字符串。

    即使开了 response_format=json_object，部分上游（DeepSeek、豆包某些版本）
    仍会返回 markdown 围栏或前后多余文字。剥掉常见包装后再交给 json.loads，
    解析成功率比 strict 模式高一截，且不会误改本身合法的 JSON。
    """
    s = raw.strip()
    # 去掉 ```json ... ``` 或 ``` ... ``` 围栏
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        s = s.lstrip("\r\n ")
        if s.endswith("```"):
            s = s[:-3].rstrip()
    # 取第一个 { 到最后一个 }，丢弃前后说明文字
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start : end + 1]
    return s


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

    # 累计高置信度命中次数达到此值后，下一次 search_rules 调用直接短路返回提示。
    # 实测中 LLM 在 prompt 层规则下仍可能反复换措辞调用同主题搜索，机制级保护更稳。
    _HIGH_HIT_LIMIT: int = 1

    def __init__(
        self,
        searcher: RuleSearcher,
        *,
        client: AsyncOpenAI | None = None,
        max_tool_rounds: int | None = None,
        temperature: float | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        request_id: str | None = None,
    ) -> None:
        self.searcher = searcher
        self.client = client or get_openai_client()
        self.system_prompt = _load_system_prompt()
        self.max_tool_rounds = max_tool_rounds or settings.llm_max_tool_rounds
        self.temperature = temperature if temperature is not None else settings.llm_temperature
        # 允许 service 层注入用户自带 model 覆盖默认值
        self.model = model or settings.openai_model
        # max_tokens：前端可通过 X-LLM-Max-Tokens 覆盖；None 时用服务器默认
        self.max_tokens = max_tokens or settings.llm_max_tokens
        self.request_id = request_id

        # 累计 token / 轮次，service 层可读取入库
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.tool_rounds = 0

        # 检索去重：(query_norm, section_id, doc_types_key) → 上次工具结果 dict
        # 命中时直接返回缓存 + 标记 duplicated_call=True，告诉 LLM 别再重复同一意图
        self._search_history: dict[tuple[str, str, str], dict] = {}
        # 摘要清单（保留发起顺序）：每条 {"query","section_id","doc_types","best_score","top_section_ids"}
        self._search_summary: list[dict] = []

    @staticmethod
    def _search_key(query: str, section_id: str | None, doc_types: list[str] | None) -> tuple[str, str, str]:
        norm_query = " ".join((query or "").split()).lower()
        norm_sid = (section_id or "").strip()
        norm_dt = ",".join(sorted(doc_types)) if doc_types else ""
        return norm_query, norm_sid, norm_dt

    async def _llm_call(self, *, messages: list, tools: list | None = None, force_no_tools: bool = False):
        """LLM 调用 + tenacity 指数退避重试。

        force_no_tools: 达到最大轮次时调用，禁止再发起工具，强制收尾。
        """
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
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
        self, name: str, arguments: dict, original_question: str = "", *, rounds_left: int = 0
    ) -> tuple[str, dict | None]:
        """执行工具，返回 (原始结果字符串, 额外元数据)。

        rounds_left: 当前 tool_call 完成后 LLM 还能调几次工具（不含本次）。
            会被注入到 search_rules 的返回里，让 LLM 据此判断是否继续检索。
        """
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
            return await self._execute_search_rules(
                arguments, original_question=original_question, rounds_left=rounds_left
            )
        if name == "search_cards":
            query = arguments.get("query", "")
            result = await search_cards(query)
            if result:
                return json.dumps(result, ensure_ascii=False, default=str), result
            return json.dumps({"count": 0, "items": [], "query": query}, ensure_ascii=False), None
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False), None

    async def _execute_search_rules(
        self,
        arguments: dict,
        *,
        original_question: str,
        rounds_left: int,
    ) -> tuple[str, dict | None]:
        """search_rules 工具实现：召回 + 重排 + 预算/置信度元信息。

        返回给 LLM 的 JSON 包含：
        - matches: TopK 命中（含 score）
        - best_score / confidence_hint: 用来判断"是否还需要继续检索"
        - rounds_left: 剩余工具调用次数
        - expanded_terms: 后端自动扩展的同义词
        - search_history: 已发起过的查询摘要（让 LLM 自己看到自己搜过什么）
        - duplicated_call: 同 (query, section_id, doc_types) 重复时为 True
        - high_hit_satisfied: 已累计 N 次 high 命中触发的早停短路
        """
        query = arguments.get("query", "") or ""
        section_id = arguments.get("section_id")
        doc_types = arguments.get("document_types")

        history_summary = list(self._search_summary)  # 拷贝防止下游突变
        key = self._search_key(query, section_id, doc_types)

        # 重复检索优先于 high 短路：用户/LLM 用了完全相同的 query，
        # 返回带内容的缓存比一句"够了"对下游更有用。
        if key in self._search_history:
            cached = self._search_history[key]
            payload = {
                **cached,
                "duplicated_call": True,
                "rounds_left": rounds_left,
                "search_history": history_summary,
                "note": "本次调用与之前的查询参数完全相同，已返回缓存结果。请基于已有信息作答或换关键词。",
            }
            return json.dumps(payload, ensure_ascii=False), {**cached, "duplicated_call": True}

        # 高置信早停：累计 high 次数达到 _HIGH_HIT_LIMIT 后，对**新意图**的 search_rules 调用短路。
        # 实测中即便 prompt 里写了"high 不要再搜"，LLM 仍可能把已搜过的换措辞再来一次，
        # 让 plateau 早停在 round 5 才介入并触发 force_no_tools 路径，反而吐空 JSON。
        # 在工具入口处直接拒绝，让 LLM 在下一轮收尾时看到充分的"够了"信号。
        high_hit_count = sum(
            1 for s in self._search_summary if s.get("confidence_hint") == "high"
        )
        if high_hit_count >= self._HIGH_HIT_LIMIT:
            note = (
                f"已累计 {high_hit_count} 次高置信度（high）检索结果，本次 search_rules 调用被短路。"
                "请立即基于已有 matches 与你的 MTG 知识给出最终 JSON 回答，"
                "answer 字段必须非空、引用具体规则编号。禁止再调 search_rules。"
            )
            payload = {
                "query": query,
                "section_id": section_id,
                "document_types": doc_types,
                "matches": [],
                "results_count": 0,
                "high_hit_satisfied": True,
                "high_hit_count": high_hit_count,
                "rounds_left": rounds_left,
                "search_history": history_summary,
                "note": note,
            }
            meta = {
                "high_hit_satisfied": True,
                "high_hit_count": high_hit_count,
                "results_count": 0,
            }
            return json.dumps(payload, ensure_ascii=False), meta

        ranked = await self.searcher.search(
            query=query,
            section_id=section_id,
            document_types=doc_types,
            top_k=10,
            vector_query=original_question,
        )
        # 兼容 mock：测试里的 mock 直接返回 list[RerankedChunk]
        rerank_status = getattr(ranked, "rerank_status", "ok")

        # query 扩展信息，喂给 LLM 看到"我替你扩了什么"
        expansion = expand_query(query) if query else None
        expanded_terms = list(expansion.keywords) if expansion else []

        matches: list[dict] = []
        full_chunks: list[dict] = []
        for r in ranked:
            c = r.chunk
            score = round(r.score, 4)
            matches.append(
                {
                    "section_id": c.section_id,
                    "title": c.title[:100],
                    "snippet": c.content[:300],
                    "source_path": c.source_path,
                    "document_type": c.document_type,
                    "score": score,
                }
            )
            full_chunks.append(
                {
                    "section_id": c.section_id,
                    "title": c.title,
                    "content": c.content,
                    "source_path": c.source_path,
                    "document_type": c.document_type,
                }
            )

        scores = [r.score for r in ranked]
        best_score = round(max(scores), 4) if scores else 0.0
        confidence_hint = confidence_hint_from(scores)

        tool_payload = {
            "query": query,
            "section_id": section_id,
            "document_types": doc_types,
            "expanded_terms": expanded_terms,
            "matches": matches,
            "results_count": len(matches),
            "best_score": best_score,
            "confidence_hint": confidence_hint,
            "rerank_status": rerank_status,
            "rounds_left": rounds_left,
            "search_history": history_summary,
            "duplicated_call": False,
        }

        # 写入历史，下次同 key 直接命中
        # 注意：缓存里不带 rounds_left / search_history（这两个是"调用时态"）
        cacheable = {
            "query": query,
            "section_id": section_id,
            "document_types": doc_types,
            "expanded_terms": expanded_terms,
            "matches": matches,
            "results_count": len(matches),
            "best_score": best_score,
            "confidence_hint": confidence_hint,
            "rerank_status": rerank_status,
        }
        self._search_history[key] = cacheable
        self._search_summary.append(
            {
                "query": query,
                "section_id": section_id,
                "doc_types": doc_types,
                "best_score": best_score,
                "confidence_hint": confidence_hint,
                "rerank_status": rerank_status,
                "top_section_ids": [m["section_id"] for m in matches[:3]],
            }
        )

        # meta 给 agent 内部用：除 matches/expanded_terms 外，还要带 chunks（不截断的完整版）供回填
        meta = {
            **cacheable,
            "chunks": full_chunks,
        }
        return json.dumps(tool_payload, ensure_ascii=False), meta

    async def _prefetch_by_rule_numbers(self, rule_numbers: list[str]) -> list[dict]:
        """规则号快路径：用户直接报了 613.1a 这种编号时，第一次 LLM 调用前就预拉相关条文。

        每个编号最多取 top 3，并去重；返回与 collected_rules 同形态的 chunk dict。
        失败 / 空结果时返回空列表，不影响后续流程。
        """
        seen_ids: set[str] = set()
        out: list[dict] = []
        # 限制最多查 3 个编号，避免有人构造大量编号刷预拉
        for sid in rule_numbers[:3]:
            try:
                ranked = await self.searcher.search(
                    query="",
                    section_id=sid,
                    top_k=3,
                )
            except Exception:
                continue
            for r in ranked:
                c = r.chunk
                key = f"{c.source_path}#{c.section_id}"
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                out.append(
                    {
                        "section_id": c.section_id,
                        "title": c.title,
                        "content": c.content,
                        "source_path": c.source_path,
                        "document_type": c.document_type,
                    }
                )
        return out

    @staticmethod
    def _render_prefetch_block(chunks: list[dict]) -> str:
        """把预检索结果渲染成一段 system message。"""
        if not chunks:
            return ""
        lines = [
            "[预检索] 已根据用户问题中的规则编号自动加载以下条文，可直接在最终回答中引用，",
            "通常无需再调用 search_rules（除非需要补充其他角度的规则）。",
            "",
        ]
        for c in chunks:
            sid = c.get("section_id", "")
            title = (c.get("title") or "").strip()[:120]
            content = (c.get("content") or "").strip()[:500]
            lines.append(f"- {sid} {title}".rstrip())
            if content:
                lines.append(f"  {content}")
        return "\n".join(lines)

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
        term_section_hints = section_hints_for(question)
        if rule_numbers:
            yield _event("thinking", content=f"检测到规则编号：{', '.join(rule_numbers)}，将优先查询。")

        # 快路径预检索：用户直接报了规则号时，第一轮 LLM 调用前就把相关条文塞进上下文，
        # 通常能省一整轮 search_rules 的 tool call。命中失败也不影响后续流程。
        prefetched_chunks: list[dict] = []
        if rule_numbers:
            try:
                prefetched_chunks = await self._prefetch_by_rule_numbers(rule_numbers)
                if prefetched_chunks:
                    yield _event(
                        "thinking",
                        content=f"已预加载 {len(prefetched_chunks)} 条相关规则，可直接引用。",
                    )
            except Exception:
                logger.warning("规则号预检索失败", request_id=self.request_id)

        # 把预检索结果塞进第一条 system message 后面，避免污染 user 消息（user 内容是用户原话）
        prefetch_block = self._render_prefetch_block(prefetched_chunks)
        # 术语 → 章节提示：不预拉数据（避免误导），仅作为 hint 让 LLM 第一次 tool_call 就用对 section_id
        hint_lines: list[str] = []
        if rule_numbers:
            hint_lines.append(f"用户问题中的规则编号：{', '.join(rule_numbers)}")
        if term_section_hints:
            hint_lines.append(f"问题涉及的章节候选：{', '.join(term_section_hints)}")
        rule_hint = ("\n\n[系统提示]\n" + "\n".join(hint_lines)) if hint_lines else ""

        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]
        if prefetch_block:
            messages.append({"role": "system", "content": prefetch_block})
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
        collected_rules: list[dict] = list(prefetched_chunks)  # 让 _parse_response 也能回填这些

        # 早停哨兵：连续两次 search_rules 的 best_score 差距 < 阈值时，下一轮强制收尾。
        # 实测中 LLM 经常无视 prompt 里的"换关键词没用就停"指令，反复刷同分。
        # 用机制级保护避免 5 轮全跑满后 force_no_tools 触发空答案问题。
        last_best_score: float | None = None
        plateau_count = 0
        PLATEAU_THRESHOLD = 0.05
        PLATEAU_LIMIT = 2  # 连续 N 次 best_score 无明显提升即触发

        for round_idx in range(self.max_tool_rounds):
            self.tool_rounds = round_idx + 1
            yield _event("thinking", content=f"第 {round_idx + 1} 轮推理...")

            force_collapse = plateau_count >= PLATEAU_LIMIT
            if force_collapse:
                # 提示模型，告诉它必须基于已有信息收尾
                yield _event(
                    "thinking",
                    content=f"检测到检索分数停滞（连续 {plateau_count} 次无提升），强制基于已有信息收尾。",
                )
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "检测到连续多轮检索的 best_score 没有明显提升，说明继续搜索不会带来新信息。"
                            "请立即基于已有 matches + 你的 MTG 知识给出最终 JSON 回答，"
                            "禁止再调用 search_rules。answer 字段必须非空。"
                        ),
                    }
                )

            try:
                response = await self._llm_call(
                    messages=messages, tools=TOOLS, force_no_tools=force_collapse
                )
            except Exception as e:
                logger.exception("LLM 调用失败", request_id=self.request_id)
                yield _event("error", content=f"LLM 调用失败: {e}")
                raise LLMError(f"LLM 调用失败: {e}") from e

            choice = response.choices[0]

            # force_collapse 时不论 LLM 是否还返回 tool_calls 都视为最终回答。
            # 实测中 Claude 在 force_no_tools=True 时偶发仍返回 tool_calls（content 为空），
            # 不防御会再走一轮工具循环白白消耗预算。
            if force_collapse or choice.finish_reason == "stop" or not choice.message.tool_calls:
                yield _event("thinking", content="推理完成，生成最终回答...")
                parsed = await self._parse_response_with_repair(
                    choice.message.content or "{}",
                    messages,
                    collected_cards,
                    collected_rules,
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

                # 本次 tool_call 完成后 LLM 还能调几次工具（不含本次）
                # round_idx 从 0 起，max_tool_rounds 是总轮数，最后一轮调用后剩 0
                rounds_left_after_this = max(0, self.max_tool_rounds - round_idx - 1)

                meta: dict | None = None
                try:
                    result_str, meta = await self._execute_tool(
                        func_name,
                        func_args,
                        original_question=question,
                        rounds_left=rounds_left_after_this,
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
                    duplicated = bool(meta and meta.get("duplicated_call"))
                    high_hit_short_circuit = bool(meta and meta.get("high_hit_satisfied"))
                    if meta and not duplicated and not high_hit_short_circuit:
                        # 累加真实检索结果到 collected_rules，供 _parse_response 用中文回填
                        # duplicated_call / high_hit_satisfied 时 chunks 不存在或已被前次记录
                        for chunk in meta.get("chunks", []):
                            collected_rules.append(chunk)
                    # 早停哨兵：跟踪 best_score 的提升幅度
                    # short-circuit 路径没有真实分数，跳过 plateau 跟踪避免被误判
                    if meta and not duplicated and not high_hit_short_circuit:
                        cur_best = meta.get("best_score") or 0.0
                        if last_best_score is not None and (cur_best - last_best_score) < PLATEAU_THRESHOLD:
                            plateau_count += 1
                        else:
                            plateau_count = 0
                        last_best_score = cur_best
                    yield _event(
                        "tool_result",
                        tool=func_name,
                        query=func_args.get("query"),
                        section_id=func_args.get("section_id"),
                        results_count=count,
                        best_score=meta.get("best_score") if meta else None,
                        confidence_hint=meta.get("confidence_hint") if meta else None,
                        rerank_status=meta.get("rerank_status") if meta else None,
                        expanded_terms=meta.get("expanded_terms") if meta else None,
                        duplicated_call=duplicated,
                        high_hit_satisfied=high_hit_short_circuit or None,
                        rounds_left=rounds_left_after_this,
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
                    "**强制要求**："
                    "1) answer 字段必须非空 —— 哪怕只是承认信息不足，也要写出原因和建议（"
                    "比如\"基于已搜索内容，本助手认为...\"或\"该问题超出当前检索能力，建议咨询人工裁判...\"）。"
                    "2) summary 字段必须非空（一句话裁判结论）。"
                    "3) 严禁返回 `{}` 或字段全空的 JSON。"
                    "4) 若确实信息不足，将 confidence 标为 low，并把 needs_human_judge 设为 true。"
                ),
            }
        )
        try:
            response = await self._llm_call(messages=messages, force_no_tools=True)
            parsed = await self._parse_response_with_repair(
                response.choices[0].message.content or "{}",
                messages,
                collected_cards,
                collected_rules,
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

    def _try_parse_json(self, raw_content: str) -> dict | None:
        """尝试把 LLM 返回的字符串解析成 dict。

        返回 None 触发 salvage：
        - 空 / 空白输入
        - JSON 解析失败
        - 解析成功但**没有任何关键字段**（answer 为空 + summary 为空）—— Claude 在
          force_no_tools 路径下偶尔会返回 `{}` 或空对象，此时不该当成"空答案"接受。
        """
        if not raw_content or not raw_content.strip():
            return None
        try:
            data = json.loads(_extract_json(raw_content))
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        # 关键字段全空视为无效响应
        if not (data.get("answer") or "").strip() and not (data.get("summary") or "").strip():
            return None
        return data

    @staticmethod
    def _salvage_json(broken_raw: str) -> dict | None:
        """确定性的 JSON 抢救：当主解析 + extract 都失败时，尝试用正则把关键字段挖出来。

        典型失败模式：
        - LLM 输出被 max_tokens 截断（JSON 结尾的 `}` 没了）
        - LLM 在 JSON 之外加了 markdown 段落或多个 `{}` 块
        - 嵌入的 markdown 富文本里的 \" 或换行没正确转义

        策略：
        1) 先用宽松正则提取 answer / summary 字段（容忍未闭合引号）
        2) 提不到就把整个 raw 包成 answer 字段；置信度强制降级到 medium
        """
        if not broken_raw or not broken_raw.strip():
            return None

        # 去掉外层 markdown 围栏（最常见的 ```json ... ```）
        text = broken_raw.strip()
        if text.startswith("```"):
            text = text[3:]
            if text[:4].lower() == "json":
                text = text[4:]
            text = text.lstrip("\r\n ")
            if text.endswith("```"):
                text = text[:-3].rstrip()

        # 截断恢复：如果文本看起来是被截断的 JSON（缺末尾 `}`），强行补齐再尝试一次
        if text.startswith("{") and not text.rstrip().endswith("}"):
            # 数一下未闭合的 { 与未结束的 "
            # 这是粗糙但实用的启发式 — 不追求 100% 重建合法 JSON
            for repair_suffix in ('"}', '"]}', '"]"}', '"}]}'):
                trial = text + repair_suffix
                try:
                    parsed = json.loads(trial)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    continue

        # 提取 answer 字段：宽松正则容忍内部转义
        # 匹配 `"answer"\s*:\s*"(.*?)"\s*,` 或 `,\s*"summary"`
        answer_match = re.search(
            r'"answer"\s*:\s*"((?:\\.|[^"\\])*)"',
            text,
            flags=re.DOTALL,
        )
        summary_match = re.search(
            r'"summary"\s*:\s*"((?:\\.|[^"\\])*)"',
            text,
            flags=re.DOTALL,
        )
        confidence_match = re.search(
            r'"confidence"\s*:\s*"(high|medium|low)"',
            text,
        )

        answer_text = ""
        if answer_match:
            try:
                # 把匹配到的字符串当 JSON 字符串字面量解析，处理 \n / \"
                answer_text = json.loads(f'"{answer_match.group(1)}"')
            except Exception:
                answer_text = answer_match.group(1)

        summary_text = summary_match.group(1).strip() if summary_match else ""

        # input 看起来像 JSON 对象（带大括号）但 answer/summary 都是空 →
        # LLM 实际吐了一个空响应（如 `{}` 或 `{"answer":"","summary":""}`），不该当成成功
        looks_like_json_object = bool(re.match(r"^\s*\{", text))
        if looks_like_json_object and not answer_text.strip() and not summary_text:
            return None

        if not answer_text:
            # 最后兜底：纯文本输入（如 markdown 散文）整段塞进 answer
            answer_text = text

        return {
            "answer": answer_text,
            "summary": summary_match.group(1) if summary_match else "",
            "confidence": (confidence_match.group(1) if confidence_match else "medium"),
            "cards": [],
            "rules": [],
            "reasoning_summary": "原始输出未通过 JSON 解析，已自动抢救为结构化结果。",
            "needs_human_judge": False,
        }

    def _parse_response(self, raw_content: str, tool_cards: list[dict], tool_rules: list[dict]) -> JudgeResponse:
        """从 LLM 原始字符串解析 + 构造 JudgeResponse（同步路径，无修复重试）。"""
        data = self._try_parse_json(raw_content)
        if data is None:
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
        return self._build_response_from_data(data, tool_cards, tool_rules)

    async def _parse_response_with_repair(
        self,
        raw_content: str,
        messages: list,
        tool_cards: list[dict],
        tool_rules: list[dict],
    ) -> JudgeResponse:
        """JSON 契约保护版：主解析失败时走确定性 salvage（正则抢救），不再调 LLM。

        实测多次发现：LLM 修复请求容易把"修复格式"误解为"重新作答"导致幻觉，
        且原始输出经常已经被 max_tokens 截断，再交给 LLM 也补不回来。
        改用确定性正则抢救，把 answer 字段挖出来包成最小 envelope。
        """
        data = self._try_parse_json(raw_content)
        if data is None:
            logger.warning(
                "LLM 返回非 JSON，启用确定性 salvage",
                content=raw_content[:200],
                request_id=self.request_id,
            )
            data = self._salvage_json(raw_content)
        if data is None:
            return JudgeResponse(
                answer=raw_content,
                summary="无法解析为结构化回答",
                confidence="low",
                reasoning_summary="模型返回非 JSON 格式且无法抢救。",
                needs_human_judge=True,
            )
        return self._build_response_from_data(data, tool_cards, tool_rules)

    def _build_response_from_data(
        self, data: dict, tool_cards: list[dict], tool_rules: list[dict]
    ) -> JudgeResponse:
        """从已解析的 dict 构造 JudgeResponse，做工具数据回填。"""

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
