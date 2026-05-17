"""Agent 请求与响应的 Pydantic 模型。"""

from pydantic import BaseModel, Field


class CardFace(BaseModel):
    """双面牌的单面信息。"""
    face_name: str = ""
    oracle_text: str = ""
    translated_text: str = ""
    translated_type: str = ""
    mana_cost: str = ""
    power: str | None = None
    toughness: str | None = None
    defense: str | None = None


class CardRuling(BaseModel):
    """FAQ/ruling 条目。"""
    date: str = ""
    text: str = ""


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
