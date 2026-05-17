"""牌张查询服务：整合缓存和外部 API。"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import CardCache
from app.schemas import CardFace, CardInfo, CardRuling
from app.tools.card_tools import resolve_card_name

logger = get_logger(__name__)
CACHE_TTL_DAYS = 7


def _card_data_to_info(card_data: dict) -> CardInfo:
    """将工具返回的牌张数据转为 CardInfo。"""
    faces = [CardFace(**f) for f in card_data.get("faces", []) if isinstance(f, dict)]
    rulings = [CardRuling(**r) for r in card_data.get("rulings", []) if isinstance(r, dict)]
    return CardInfo(
        input_name=card_data["input_name"],
        resolved_zh_name=card_data.get("resolved_zh_name"),
        oracle_name=card_data.get("oracle_name"),
        oracle_text=card_data.get("oracle_text"),
        translated_text=card_data.get("translated_text"),
        translated_type=card_data.get("translated_type"),
        type_line=card_data.get("type_line"),
        mana_cost=card_data.get("mana_cost"),
        power=str(card_data["power"]) if card_data.get("power") is not None else None,
        toughness=str(card_data["toughness"]) if card_data.get("toughness") is not None else None,
        defense=str(card_data["defense"]) if card_data.get("defense") is not None else None,
        layout=card_data.get("layout"),
        scryfall_id=card_data.get("scryfall_id"),
        faces=faces,
        rulings=rulings,
    )


class CardService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _get_from_cache(self, input_name: str) -> CardCache | None:
        result = await self.db.execute(select(CardCache).where(CardCache.input_name == input_name))
        card = result.scalar_one_or_none()
        if card and card.last_fetched_at:
            age = datetime.now(UTC) - card.last_fetched_at
            if age.days < CACHE_TTL_DAYS:
                return card
        return None

    async def _save_to_cache(self, card_data: dict) -> CardCache:
        card = CardCache(
            input_name=card_data["input_name"],
            resolved_zh_name=card_data.get("resolved_zh_name"),
            oracle_name=card_data.get("oracle_name"),
            oracle_text=card_data.get("oracle_text"),
            type_line=card_data.get("type_line"),
            mana_cost=card_data.get("mana_cost"),
            scryfall_id=card_data.get("scryfall_id"),
            raw_json=card_data,
            last_fetched_at=datetime.now(UTC),
        )
        self.db.add(card)
        await self.db.commit()
        await self.db.refresh(card)
        return card

    async def resolve_and_get(self, input_name: str) -> CardInfo | None:
        cached = await self._get_from_cache(input_name)
        if cached and cached.oracle_text:
            logger.info("牌张缓存命中", name=input_name)
            return CardInfo(
                input_name=cached.input_name,
                resolved_zh_name=cached.resolved_zh_name,
                oracle_name=cached.oracle_name,
                oracle_text=cached.oracle_text,
                type_line=cached.type_line,
                mana_cost=cached.mana_cost,
                scryfall_id=cached.scryfall_id,
            )

        logger.info("牌张缓存未命中，查询 API", name=input_name)
        card_data = await resolve_card_name(input_name)
        if not card_data:
            return None

        if cached:
            cached.resolved_zh_name = card_data.get("resolved_zh_name")
            cached.oracle_name = card_data.get("oracle_name")
            cached.oracle_text = card_data.get("oracle_text")
            cached.type_line = card_data.get("type_line")
            cached.mana_cost = card_data.get("mana_cost")
            cached.scryfall_id = card_data.get("scryfall_id")
            cached.raw_json = card_data
            cached.last_fetched_at = datetime.now(UTC)
            await self.db.commit()
        else:
            await self._save_to_cache(card_data)

        return _card_data_to_info(card_data)
