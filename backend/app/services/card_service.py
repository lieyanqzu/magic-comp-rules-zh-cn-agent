"""牌张查询服务：Redis 缓存 → DB 缓存 → 外部 API。"""

import json
from datetime import UTC, datetime

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import CardCache
from app.schemas import CardFace, CardInfo, CardRuling
from app.tools.card_tools import resolve_card_name

logger = get_logger(__name__)

# Redis 缓存 TTL（秒）
REDIS_TTL = 3600 * 24 * 7  # 7 天
DB_CACHE_TTL_DAYS = 7
REDIS_PREFIX = "card:"


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
    def __init__(self, db: AsyncSession, redis: aioredis.Redis | None = None) -> None:
        self.db = db
        self.redis = redis

    # ---- Redis 缓存层 ----

    async def _redis_get(self, key: str) -> dict | None:
        if not self.redis:
            return None
        try:
            data = await self.redis.get(key)
            if data:
                return json.loads(data)
        except Exception:
            logger.warning("Redis 读取失败", key=key)
        return None

    async def _redis_set(self, key: str, value: dict) -> None:
        if not self.redis:
            return
        try:
            await self.redis.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=REDIS_TTL)
        except Exception:
            logger.warning("Redis 写入失败", key=key)

    # ---- DB 缓存层 ----

    async def _db_get(self, input_name: str) -> CardCache | None:
        result = await self.db.execute(select(CardCache).where(CardCache.input_name == input_name))
        card = result.scalar_one_or_none()
        if card and card.last_fetched_at:
            age = datetime.now(UTC) - card.last_fetched_at
            if age.days < DB_CACHE_TTL_DAYS:
                return card
        return None

    async def _db_save(self, card_data: dict) -> None:
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

    async def _db_update(self, card: CardCache, card_data: dict) -> None:
        card.resolved_zh_name = card_data.get("resolved_zh_name")
        card.oracle_name = card_data.get("oracle_name")
        card.oracle_text = card_data.get("oracle_text")
        card.type_line = card_data.get("type_line")
        card.mana_cost = card_data.get("mana_cost")
        card.scryfall_id = card_data.get("scryfall_id")
        card.raw_json = card_data
        card.last_fetched_at = datetime.now(UTC)
        await self.db.commit()

    # ---- 主查询逻辑 ----

    async def resolve_and_get(self, input_name: str) -> CardInfo | None:
        """解析牌名并返回牌张信息。优先级：Redis → DB → API。"""
        redis_key = f"{REDIS_PREFIX}{input_name}"

        # 1. Redis 缓存
        cached = await self._redis_get(redis_key)
        if cached:
            logger.info("Redis 缓存命中", name=input_name)
            return _card_data_to_info(cached)

        # 2. DB 缓存
        db_card = await self._db_get(input_name)
        if db_card and db_card.oracle_text:
            logger.info("DB 缓存命中", name=input_name)
            card_data = {
                "input_name": db_card.input_name,
                "resolved_zh_name": db_card.resolved_zh_name,
                "oracle_name": db_card.oracle_name,
                "oracle_text": db_card.oracle_text,
                "type_line": db_card.type_line,
                "mana_cost": db_card.mana_cost,
                "scryfall_id": db_card.scryfall_id,
            }
            await self._redis_set(redis_key, card_data)
            return CardInfo(**card_data)

        # 3. 外部 API
        logger.info("缓存未命中，查询 API", name=input_name)
        card_data = await resolve_card_name(input_name)
        if not card_data:
            return None

        # 写入缓存
        await self._redis_set(redis_key, card_data)
        if db_card:
            await self._db_update(db_card, card_data)
        else:
            await self._db_save(card_data)

        return _card_data_to_info(card_data)
