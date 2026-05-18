"""Agent 请求与响应的 Pydantic 模型。"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas import CardFace, CardRuling


class HistoryMessage(BaseModel):
    """对话历史中的一条消息。前端把过往轮次以这种形式回传，
    后端注入到 LLM messages 里以保留上下文。"""
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=8000)


# 最多回传多少条历史。10 轮 (=20 条) 已经远超合理对话长度
MAX_HISTORY_MESSAGES = 20


class CardRef(BaseModel):
    """回答中引用的牌张。"""
    name: str
    oracle_name: str | None = None
    oracle_text: str | None = None
    oracle_text_en: str | None = None
    translated_text: str | None = None
    translated_type: str | None = None
    type_line: str | None = None
    type_line_en: str | None = None
    mana_cost: str | None = None
    power: str | None = None
    toughness: str | None = None
    defense: str | None = None
    layout: str | None = None
    display_text: str | None = None
    display_type: str | None = None
    faces: list[CardFace] = Field(default_factory=list)
    rulings: list[CardRuling] = Field(default_factory=list)


class RuleRef(BaseModel):
    """回答中引用的规则。"""
    section_id: str
    title: str
    content_snippet: str
    source_path: str


class JudgeRequest(BaseModel):
    """裁判问答请求。"""
    question: str = Field(..., min_length=1, max_length=5000)
    language: str = Field("zh-CN")
    history: list[HistoryMessage] = Field(
        default_factory=list,
        max_length=MAX_HISTORY_MESSAGES,
        description="过往对话历史（user/assistant 交替）。当前问题不应包含在内。",
    )


class JudgeResponse(BaseModel):
    """裁判问答响应。"""
    answer: str
    summary: str
    confidence: str = Field(..., pattern="^(high|medium|low)$")
    cards: list[CardRef] = Field(default_factory=list)
    rules: list[RuleRef] = Field(default_factory=list)
    reasoning_summary: str
    needs_human_judge: bool = False
    latency_ms: float | None = None
