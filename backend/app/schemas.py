"""共享的 Pydantic 模型。"""

from pydantic import BaseModel


class CardInfo(BaseModel):
    input_name: str
    resolved_zh_name: str | None = None
    oracle_name: str | None = None
    oracle_text: str | None = None
    type_line: str | None = None
    mana_cost: str | None = None
    scryfall_id: str | None = None


class RuleResult(BaseModel):
    section_id: str
    title: str
    content: str
    source_path: str
    document_type: str
    score: float | None = None
