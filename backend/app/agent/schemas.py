"""Agent 请求与响应的 Pydantic 模型。"""

from pydantic import BaseModel, Field


class CardRef(BaseModel):
    name: str
    oracle_name: str | None = None
    oracle_text: str | None = None


class RuleRef(BaseModel):
    section_id: str
    title: str
    content_snippet: str
    source_path: str


class JudgeRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=5000)
    language: str = Field("zh-CN")


class JudgeResponse(BaseModel):
    answer: str
    summary: str
    confidence: str = Field(..., pattern="^(high|medium|low)$")
    cards: list[CardRef] = Field(default_factory=list)
    rules: list[RuleRef] = Field(default_factory=list)
    reasoning_summary: str
    needs_human_judge: bool = False
    latency_ms: float | None = None
