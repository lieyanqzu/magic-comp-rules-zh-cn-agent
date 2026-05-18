"""牌张查询接口。"""

import hashlib
import json

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_api_key
from app.core.logging import get_logger
from app.db.session import get_db
from app.schemas import CardInfo
from app.services.card_service import CardService
from app.tools.card_tools import autocomplete_cards

router = APIRouter(dependencies=[Depends(verify_api_key)])
logger = get_logger(__name__)

# autocomplete 是公开数据，按 q 缓存。短 TTL 控制 mtgch 数据更新滞后
_AUTOCOMPLETE_CACHE_TTL = 3600  # 1 小时


def _autocomplete_cache_key(q: str, limit: int) -> str:
    digest = hashlib.sha256(f"{q.strip()}|{limit}".encode("utf-8")).hexdigest()[:16]
    return f"card_autocomplete:{digest}"


class CardResolveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class CardResolveResponse(BaseModel):
    card: CardInfo | None = None
    found: bool
    message: str | None = None


@router.post("/resolve", response_model=CardResolveResponse)
async def resolve_card(
    request: CardResolveRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
) -> CardResolveResponse:
    redis = getattr(req.app.state, "redis", None)
    service = CardService(db, redis=redis)
    card_info = await service.resolve_and_get(request.name)
    if card_info is None:
        return CardResolveResponse(
            card=CardInfo(input_name=request.name),
            found=False,
            message=f"未找到牌张：{request.name}",
        )
    return CardResolveResponse(card=card_info, found=True)


class AutocompleteItem(BaseModel):
    """autocomplete 单条建议。"""
    name_en: str
    name_zh: str
    type_zh: str = ""
    mana_cost: str = ""
    set: str = ""
    collector_number: str = ""
    rarity: str = ""


class AutocompleteResponse(BaseModel):
    items: list[AutocompleteItem]


@router.get("/autocomplete", response_model=AutocompleteResponse)
async def autocomplete(
    req: Request,
    q: str = Query(..., min_length=1, max_length=100, description="牌名前缀（中英文均可）"),
    limit: int = Query(10, ge=1, le=20),
) -> AutocompleteResponse:
    """牌名自动补全。代理 mtgch /autocomplete，加 Redis 短缓存（1h）。"""
    redis: aioredis.Redis | None = getattr(req.app.state, "redis", None)
    cache_key = _autocomplete_cache_key(q, limit)

    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached:
                items = json.loads(cached)
                return AutocompleteResponse(items=[AutocompleteItem(**i) for i in items])
        except Exception:
            logger.warning("autocomplete 缓存读取失败")

    items = await autocomplete_cards(q, limit=limit)

    if redis is not None and items:
        try:
            await redis.set(
                cache_key,
                json.dumps(items, ensure_ascii=False, default=str),
                ex=_AUTOCOMPLETE_CACHE_TTL,
            )
        except Exception:
            logger.warning("autocomplete 缓存写入失败")

    return AutocompleteResponse(items=[AutocompleteItem(**i) for i in items])
