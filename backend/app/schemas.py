"""共享的 Pydantic 模型。"""

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


class CardInfo(BaseModel):
    """牌张完整信息。"""
    input_name: str
    resolved_zh_name: str | None = None
    oracle_name: str | None = None
    oracle_text: str | None = None
    translated_text: str | None = None
    translated_type: str | None = None
    type_line: str | None = None
    mana_cost: str | None = None
    power: str | None = None
    toughness: str | None = None
    defense: str | None = None
    layout: str | None = None
    scryfall_id: str | None = None
    faces: list[CardFace] = Field(default_factory=list)
    rulings: list[CardRuling] = Field(default_factory=list)


class RuleResult(BaseModel):
    """规则检索结果。"""
    section_id: str
    title: str
    content: str
    source_path: str
    document_type: str
    score: float | None = None
