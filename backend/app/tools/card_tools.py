"""牌张查询工具：mtgch 为主数据源，Scryfall 补充 rulings。"""

import re

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)
REQUEST_TIMEOUT = 15.0
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# 模块级 httpx 客户端，复用连接池
_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT, follow_redirects=True,
            headers={"User-Agent": "mtg-judge-api/0.1"},
        )
    return _http_client


def _strip_html(text: str) -> str:
    """移除 HTML 标签。"""
    return _HTML_TAG_RE.sub("", text).strip()


async def _get(url: str, params: dict | None = None) -> dict | list | None:
    """通用 GET 请求，返回 JSON 或 None。"""
    try:
        client = _get_client()
        resp = await client.get(url, params=params)
        if resp.status_code == 404 or not resp.content:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("API 请求失败", url=url, error=str(e)[:100])
        return None


async def search_mtgch(name: str) -> dict | None:
    """通过 mtgch 搜索 API 查牌。"""
    data = await _get(f"{settings.mtgch_api_url}/result", {"q": name, "page": 1})
    if not data or not isinstance(data, dict):
        return None
    items = data.get("items", [])
    if not items:
        return None
    for item in items:
        item_name = item.get("zhs_name") or item.get("atomic_translated_name") or item.get("face_name") or ""
        if name in item_name or item_name in name:
            return item
    return items[0]


async def get_card_detail(set_code: str, collector_number: str) -> dict | None:
    """通过 mtgch 获取牌张完整详情（view=2，含中文 Oracle、双面、FAQ）。"""
    return await _get(f"{settings.mtgch_api_url}/card/{set_code}/{collector_number}/", {"view": 2})


def _parse_pt(pt_str: str) -> tuple[str | None, str | None, str | None]:
    """解析攻防/防御值字符串。返回 (power, toughness, defense)。"""
    if not pt_str:
        return None, None, None
    clean = _strip_html(pt_str)
    # P/T 格式: "4/4"
    if "/" in clean:
        parts = clean.split("/", 1)
        return parts[0].strip(), parts[1].strip(), None
    # 纯数字 = defense（战役牌）
    if clean.isdigit():
        return None, None, clean
    return None, None, None


def _extract_view2_face(face: dict) -> dict:
    """从 view=2 格式的 face 数据中提取结构化信息。"""
    oracle_en = _strip_html(face.get("oracle_text_en_html") or face.get("oracle_text_zhs_html") or "")
    oracle_zh = _strip_html(face.get("oracle_text_atomic_html") or face.get("oracle_text_zhs_html") or "")
    type_en = face.get("type_line_en") or face.get("type_line_atomic") or ""
    type_zh = face.get("type_line_atomic") or face.get("type_line_zhs") or ""
    name_zh = face.get("name_atomic") or face.get("name_zhs") or ""
    name_en = face.get("name", "")
    # mana_cost 在 view=2 中可能是空的，从 mana_cost_html 提取
    mana_cost = face.get("mana_cost") or ""
    if not mana_cost and face.get("mana_cost_html"):
        # 从 HTML 中提取 {X} 格式
        raw = _strip_html(face["mana_cost_html"])
        mana_cost = raw if raw.startswith("{") else ""

    power, toughness, defense = _parse_pt(face.get("power_toughness_loyalty_defense", ""))

    return {
        "face_name": name_en,
        "face_name_zh": name_zh,
        "oracle_text": oracle_en,
        "translated_text": oracle_zh,
        "type_line": type_en,
        "translated_type": type_zh,
        "mana_cost": mana_cost,
        "power": power,
        "toughness": toughness,
        "defense": defense,
    }


async def resolve_card_name(input_name: str) -> dict | None:
    """解析牌名并获取完整信息。

    流程：mtgch 搜索 → mtgch 详情(view=2) → 返回完整数据。
    """
    logger.info("解析牌张", input_name=input_name)

    search_result = await search_mtgch(input_name)
    if not search_result:
        logger.warning("牌张未找到", name=input_name)
        return None

    set_code = search_result.get("set", "")
    collector_number = search_result.get("collector_number", "")
    if not set_code or not collector_number:
        return None

    detail = await get_card_detail(set_code, collector_number)
    if not detail:
        detail = search_result

    # view=2 格式解析
    primary_name = detail.get("primary_name", "")
    rulings_raw = detail.get("rulings", [])
    faces_raw = detail.get("faces", [])

    # 从 faces 提取信息
    faces = [_extract_view2_face(f) for f in faces_raw] if faces_raw else []

    # 主面信息（取第一个 face）
    main = faces[0] if faces else {}

    # 英文名从 faces[0].name 获取（view=2 不返回卡级 name）
    en_name = detail.get("name") or (faces_raw[0].get("name", "") if faces_raw else "")

    # 如果没有 view=2 的 faces 数据，降级到旧格式
    if not main:
        main = {
            "oracle_text": detail.get("oracle_text", ""),
            "translated_text": detail.get("atomic_translated_text") or detail.get("zhs_text") or "",
            "type_line": detail.get("type_line") or "",
            "translated_type": detail.get("atomic_translated_type") or detail.get("zhs_type_line") or "",
            "mana_cost": detail.get("mana_cost", ""),
            "power": detail.get("power"),
            "toughness": detail.get("toughness"),
            "defense": detail.get("defense"),
        }

    result: dict = {
        "input_name": input_name,
        "resolved_zh_name": primary_name or detail.get("zhs_name") or detail.get("atomic_translated_name") or input_name,
        "oracle_name": en_name,
        "oracle_text": main.get("oracle_text", ""),
        "translated_text": main.get("translated_text", ""),
        "type_line": main.get("type_line", ""),
        "translated_type": main.get("translated_type", ""),
        "mana_cost": main.get("mana_cost") or detail.get("mana_cost", ""),
        "power": main.get("power"),
        "toughness": main.get("toughness"),
        "defense": main.get("defense"),
        "layout": detail.get("transformation_type") or detail.get("layout") or "",
    }

    if faces:
        result["faces"] = faces
    if rulings_raw:
        result["rulings"] = [
            {"date": r.get("published_at", ""), "text": r.get("comment", "")}
            for r in rulings_raw[:10]
        ]

    return result


async def search_cards(query: str, page: int = 1) -> dict | None:
    """按条件搜索牌库。

    支持 Scryfall 风格语法：
    - pow=2 tou=3 力量=2 防御力=3
    - mv=3 cmc=3 法术力值=3
    - c:ug 颜色=蓝绿
    - t:creature 类别=生物
    - o:trample 异能=践踏
    - is:dfc 双面牌
    - e:mom 系列=邪军压境
    - lang:zhs 中文版

    返回 {count, items: [{name, face_name, set, collector_number, ...}]}
    """
    data = await _get(f"{settings.mtgch_api_url}/result", {"q": query, "page": page})
    if not data or not isinstance(data, dict):
        return None

    items = data.get("items", [])
    # 精简返回字段
    results = []
    for item in items[:20]:
        results.append({
            "name": item.get("name", ""),
            "face_name": item.get("face_name", ""),
            "zhs_name": item.get("zhs_name") or item.get("atomic_translated_name") or "",
            "set": item.get("set", ""),
            "collector_number": item.get("collector_number", ""),
            "mana_cost": item.get("mana_cost", ""),
            "type_line": item.get("type_line") or item.get("atomic_translated_type") or "",
            "power": item.get("power"),
            "toughness": item.get("toughness"),
            "rarity": item.get("rarity", ""),
        })

    return {
        "count": data.get("total_count", len(items)),
        "page": page,
        "items": results,
    }
