"""牌张查询工具：与 mtgch 和 Scryfall API 交互。"""

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.errors import ExternalAPIError
from app.core.logging import get_logger

logger = get_logger(__name__)
REQUEST_TIMEOUT = 10.0


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5))
async def query_mtgch(name: str) -> dict | None:
    url = f"{settings.mtgch_api_url}/v1/cards/search"
    params = {"q": name, "limit": 5}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            cards = data if isinstance(data, list) else data.get("data", [])
            if not cards:
                return None
            for card in cards:
                zh_name = card.get("name_zh") or card.get("zhName") or card.get("name", "")
                if zh_name == name or name in zh_name:
                    return card
            return cards[0]
        except httpx.HTTPStatusError as e:
            logger.warning("mtgch API 请求失败", status=e.response.status_code, name=name)
            raise ExternalAPIError(message=f"mtgch API 请求失败: {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.warning("mtgch API 连接失败", error=str(e), name=name)
            raise ExternalAPIError(message="mtgch API 连接失败") from e


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5))
async def query_scryfall(card_name: str) -> dict | None:
    url = f"{settings.scryfall_api_url}/cards/named"
    params = {"exact": card_name}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 404:
                resp = await client.get(url, params={"fuzzy": card_name})
                if resp.status_code == 404:
                    return None
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning("Scryfall API 请求失败", status=e.response.status_code)
            raise ExternalAPIError(message=f"Scryfall API 请求失败: {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.warning("Scryfall API 连接失败", error=str(e))
            raise ExternalAPIError(message="Scryfall API 连接失败") from e


async def resolve_card_name(input_name: str) -> dict | None:
    logger.info("解析牌名", input_name=input_name)
    mtgch_result = await query_mtgch(input_name)
    english_name = None
    zh_name = None
    if mtgch_result:
        english_name = mtgch_result.get("name_en") or mtgch_result.get("enName") or mtgch_result.get("name")
        zh_name = mtgch_result.get("name_zh") or mtgch_result.get("zhName") or mtgch_result.get("name")

    scryfall_name = english_name or input_name
    scryfall_result = await query_scryfall(scryfall_name)

    if not scryfall_result and not mtgch_result:
        return None

    result: dict = {"input_name": input_name}
    if zh_name:
        result["resolved_zh_name"] = zh_name
    if scryfall_result:
        result["oracle_name"] = scryfall_result.get("name")
        result["oracle_text"] = scryfall_result.get("oracle_text") or scryfall_result.get("card_faces", [{}])[0].get("oracle_text", "")
        result["type_line"] = scryfall_result.get("type_line")
        result["mana_cost"] = scryfall_result.get("mana_cost")
        result["scryfall_id"] = scryfall_result.get("id")
        result["raw_scryfall"] = scryfall_result
    return result


async def get_oracle_text(card_name: str) -> str | None:
    result = await resolve_card_name(card_name)
    return result.get("oracle_text") if result else None
