"""牌张查询服务：Redis 缓存 → DB 缓存 → 外部 API。"""

import json
from datetime import UTC, datetime

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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

    async def _db_get(self, input_name: str) -> tuple[CardCache | None, bool]:
        """读取 DB 行并判断是否新鲜。返回 (row, is_fresh)。

        即使过期也要把行带回来，调用方决定走 UPDATE 而不是 INSERT，
        避免 input_name 累积重复行。
        """
        result = await self.db.execute(select(CardCache).where(CardCache.input_name == input_name))
        card = result.scalar_one_or_none()
        if card is None:
            return None, False
        if card.last_fetched_at:
            age = datetime.now(UTC) - card.last_fetched_at
            return card, age.days < DB_CACHE_TTL_DAYS
        return card, False

    async def _db_upsert(self, card_data: dict) -> None:
        """按 input_name UPSERT 到 card_cache。

        依赖迁移 002 中的 UNIQUE(input_name) 约束。SQLite 测试环境通过相同列名也能工作，
        因为 Postgres dialect 的 ON CONFLICT 在 PG 才生效；这里用 select-then-update/insert 兜底
        以保证测试也能跑。
        """
        payload = {
            "input_name": card_data["input_name"],
            "resolved_zh_name": card_data.get("resolved_zh_name"),
            "oracle_name": card_data.get("oracle_name"),
            "oracle_text": card_data.get("oracle_text"),
            "type_line": card_data.get("type_line"),
            "mana_cost": card_data.get("mana_cost"),
            "scryfall_id": card_data.get("scryfall_id"),
            "raw_json": card_data,
            "last_fetched_at": datetime.now(UTC),
        }

        dialect = self.db.bind.dialect.name if self.db.bind else ""
        if dialect == "postgresql":
            stmt = pg_insert(CardCache).values(**payload)
            update_cols = {k: stmt.excluded[k] for k in payload if k != "input_name"}
            stmt = stmt.on_conflict_do_update(
                index_elements=["input_name"], set_=update_cols
            )
            await self.db.execute(stmt)
            await self.db.commit()
            return

        # 非 PG（测试环境 SQLite）走 select-then-update/insert
        existing = await self.db.execute(
            select(CardCache).where(CardCache.input_name == payload["input_name"])
        )
        row = existing.scalar_one_or_none()
        if row is None:
            self.db.add(CardCache(**payload))
        else:
            for k, v in payload.items():
                setattr(row, k, v)
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

        # 2. DB 缓存（即使过期也保留行引用，避免 UPSERT 时累积脏数据）
        db_card, is_fresh = await self._db_get(input_name)
        if is_fresh and db_card and db_card.oracle_text:
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

        # 写入缓存：UPSERT 一次搞定，旧行会被覆盖而不是堆积
        await self._redis_set(redis_key, card_data)
        await self._db_upsert(card_data)

        return _card_data_to_info(card_data)
