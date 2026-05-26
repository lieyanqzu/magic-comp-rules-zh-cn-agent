"""牌张工具单元测试。"""

from unittest.mock import AsyncMock, patch

import pytest

from app.tools.card_tools import _parse_pt, _strip_html, search_mtgch


def test_strip_html() -> None:
    assert _strip_html("<p>Hello</p>") == "Hello"
    assert _strip_html('<b id="test">text</b>') == "text"
    assert _strip_html("no tags") == "no tags"
    assert _strip_html("") == ""


def test_parse_pt_creature() -> None:
    power, toughness, defense = _parse_pt("4/4")
    assert power == "4"
    assert toughness == "4"
    assert defense is None


def test_parse_pt_defense() -> None:
    power, toughness, defense = _parse_pt("5")
    assert power is None
    assert toughness is None
    assert defense == "5"


def test_parse_pt_empty() -> None:
    power, toughness, defense = _parse_pt("")
    assert power is None
    assert toughness is None
    assert defense is None


def test_parse_pt_html() -> None:
    power, toughness, defense = _parse_pt('<i class="ms ms-defense ms-defense-5">5</i>')
    assert defense == "5"
    assert power is None
    assert toughness is None


@pytest.mark.asyncio
async def test_search_mtgch_prefers_exact_match_over_substring() -> None:
    """精确匹配的牌张应当优先于 substring 命中。

    实测：mtgch 默认按相关度排序，搜「谦卑」时 items[0] 是「谦卑皈宗者」，
    items[3] 才是真正的「谦卑」(Humility)。旧逻辑用 substring 命中第一项，
    导致同名牌张被长名牌张抢走。

    另外要兼容老牌的字段不规整：Humility 这种早期牌 mtgch 返回的 zhs_name 是 None，
    实际中文名在 atomic_official_name。candidate_names 必须覆盖这种回退路径。
    """
    fake_response = {
        "items": [
            {"zhs_name": "谦卑皈宗者", "name": "Humble Defector"},
            {"zhs_name": "韦达肯谦卑师", "name": "Vedalken Humiliator"},
            {"zhs_name": "谦卑自然师", "name": "Humble Naturalist"},
            # 模拟 Humility 的真实数据：zhs_name=None，名字落在 atomic_official_name
            {"zhs_name": None, "atomic_official_name": "谦卑", "name": "Humility"},
        ]
    }
    with patch("app.tools.card_tools._get", new=AsyncMock(return_value=fake_response)):
        item = await search_mtgch("谦卑")
    assert item is not None
    assert item["name"] == "Humility"


@pytest.mark.asyncio
async def test_search_mtgch_falls_back_to_substring() -> None:
    """没有精确命中时，回退到 substring 匹配。"""
    fake_response = {
        "items": [
            {"zhs_name": "韦达肯谦卑师", "name": "Vedalken Humiliator"},
            {"zhs_name": "谦卑自然师", "name": "Humble Naturalist"},
        ]
    }
    with patch("app.tools.card_tools._get", new=AsyncMock(return_value=fake_response)):
        item = await search_mtgch("谦卑自然师")
    assert item is not None
    assert item["name"] == "Humble Naturalist"


@pytest.mark.asyncio
async def test_search_mtgch_english_exact_match_case_insensitive() -> None:
    """英文牌名输入时按 lowercase 比较，避免大小写差异错过精确命中。"""
    fake_response = {
        "items": [
            {"zhs_name": "谦卑皈宗者", "name": "Humble Defector"},
            {"zhs_name": "谦卑", "name": "Humility"},
        ]
    }
    with patch("app.tools.card_tools._get", new=AsyncMock(return_value=fake_response)):
        item = await search_mtgch("HUMILITY")
    assert item is not None
    assert item["name"] == "Humility"
