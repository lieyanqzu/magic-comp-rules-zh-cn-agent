"""牌张查询接口。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_api_key
from app.db.session import get_db
from app.schemas import CardInfo
from app.services.card_service import CardService

router = APIRouter(dependencies=[Depends(verify_api_key)])


class CardResolveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class CardResolveResponse(BaseModel):
    card: CardInfo | None = None
    found: bool
    message: str | None = None


@router.post("/resolve", response_model=CardResolveResponse)
async def resolve_card(request: CardResolveRequest, db: AsyncSession = Depends(get_db)) -> CardResolveResponse:
    service = CardService(db)
    card_info = await service.resolve_and_get(request.name)
    if card_info is None:
        return CardResolveResponse(card=CardInfo(input_name=request.name), found=False, message=f"未找到牌张：{request.name}")
    return CardResolveResponse(card=card_info, found=True)
