"""牌张工具单元测试。"""

from app.tools.card_tools import _parse_pt, _strip_html


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
