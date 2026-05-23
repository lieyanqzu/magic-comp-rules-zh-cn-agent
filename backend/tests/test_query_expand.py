"""query_expand 单元测试。"""

from app.retrieval.query_expand import (
    detect_terms,
    expand_query,
    section_hints_for,
)


def test_expand_query_empty() -> None:
    r = expand_query("")
    assert r.keywords == []
    assert r.hit_groups == []
    assert r.section_hints == []


def test_expand_query_keeps_base_terms() -> None:
    """没命中同义词组的纯粹关键词也应保留。"""
    r = expand_query("消灭 坟墓场")
    assert "消灭" in r.keywords
    # "坟墓场" 命中同义词组，"墓地" 应该被加进来
    assert "墓地" in r.keywords


def test_expand_query_synonym_expansion() -> None:
    """命中同义词组时，整组词都要进入 keywords。"""
    r = expand_query("层")
    assert "层" in r.keywords
    assert "层系统" in r.keywords
    assert "layer" in r.keywords
    # 应该提示 613 章节
    assert "613" in r.section_hints


def test_expand_query_does_not_introduce_unrelated_groups() -> None:
    """命中一个组不应同时拉进无关组的词。"""
    r = expand_query("飞行")
    assert "flying" in r.keywords
    assert "trample" not in r.keywords
    assert "deathtouch" not in r.keywords


def test_expand_query_section_hints_for_keywords() -> None:
    """关键字异能 → 对应规则号前缀。"""
    r = expand_query("不灭")
    assert "702.12" in r.section_hints


def test_detect_terms_in_question() -> None:
    """从用户问题里识别已知机制术语。"""
    terms = detect_terms("如果谦卑和层 6 的效应同时存在")
    assert "层" in terms


def test_detect_terms_no_match() -> None:
    assert detect_terms("一个完全无关的问题") == []


def test_section_hints_for_question() -> None:
    hints = section_hints_for("替代式效应和优先权的关系是什么")
    # 替代式效应 → 614, 优先权 → 117
    assert "614" in hints
    assert "117" in hints


def test_no_yunton_pollution() -> None:
    """运土相关词不应在字典中污染检索（应交给检索而非手工字典）。"""
    r = expand_query("地变成生物")
    # 不应被自动扩展为不相关的"运土/Awaken"
    assert "Awaken" not in r.keywords
    assert "运土" not in r.keywords
