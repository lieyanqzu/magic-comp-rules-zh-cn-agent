"""规则检索接口。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_api_key
from app.db.session import get_db
from app.schemas import RuleResult
from app.services.rule_service import RuleService

router = APIRouter(dependencies=[Depends(verify_api_key)])


class RuleSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    section_id: str | None = None
    document_types: list[str] | None = None
    top_k: int = Field(10, ge=1, le=50)


class RuleSearchResponse(BaseModel):
    results: list[RuleResult]
    total: int


@router.post("/search", response_model=RuleSearchResponse)
async def search_rules(request: RuleSearchRequest, db: AsyncSession = Depends(get_db)) -> RuleSearchResponse:
    service = RuleService(db)
    results = await service.hybrid_search(
        query=request.query, section_id=request.section_id,
        document_types=request.document_types, top_k=request.top_k,
    )
    return RuleSearchResponse(results=results, total=len(results))
