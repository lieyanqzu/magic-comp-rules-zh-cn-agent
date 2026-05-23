"""text_cleaner 单元测试。"""

from app.retrieval.text_cleaner import (
    _ascii_letter_ratio,
    _is_english_mirror,
    clean_cr_text,
    clean_for,
)


# ---- 启发式 ----

def test_ascii_ratio_pure_english() -> None:
    line = "To cast a spell is to take it from where it is."
    assert _ascii_letter_ratio(line) > 0.8


def test_ascii_ratio_pure_chinese() -> None:
    line = "施放咒语指将其从当前区域放进堆叠并支付其费用。"
    assert _ascii_letter_ratio(line) < 0.05


def test_ascii_ratio_chinese_with_a_few_english_terms() -> None:
    """中文里夹少量英文术语（如 Oracle、ETB），ASCII 占比仍应低于阈值。"""
    line = "这些用词印刷的卡牌均已经在Oracle牌张参考文献中勘误为'施放'该咒语或该牌。"
    assert _ascii_letter_ratio(line) < 0.55


# ---- 镜像行判别 ----

def test_is_mirror_pure_english_paragraph_with_rule_number() -> None:
    """带规则编号的英文镜像行也应被识别为镜像（CR 双语镜像编号一致，不能因为编号就放过）。"""
    line = "<b>601.2.</b> To cast a spell is to take it from where it is, put it on the stack."
    assert _is_english_mirror(line) is True


def test_is_mirror_keeps_chinese_with_rule_number() -> None:
    """中文规则行（即使带规则编号）必须保留。"""
    line = "<b id='cr601-2'>601.2.</b> 施放咒语指将其从当前区域放进堆叠并支付其费用。"
    assert _is_english_mirror(line) is False


def test_is_mirror_english_without_rule_number() -> None:
    """英文 Example 行没规则编号 → 应被识别为镜像。"""
    line = "Example: If a spell says \"Tap two target creatures,\" then the same creature can't be chosen twice."
    assert _is_english_mirror(line) is True


def test_is_mirror_keeps_chinese() -> None:
    line = "施放咒语指将其从当前区域放进堆叠并支付其费用。"
    assert _is_english_mirror(line) is False


def test_is_mirror_keeps_short_lines() -> None:
    """短行（如 markdown heading 'A'）一律保留。"""
    assert _is_english_mirror("## A") is False
    assert _is_english_mirror("---") is False
    assert _is_english_mirror("") is False


def test_is_mirror_keeps_table_rows() -> None:
    line = "| Library | 牌库 |"
    assert _is_english_mirror(line) is False


def test_is_mirror_detects_example_with_html() -> None:
    line = "<b>Example:</b> The activation cost of an ability."
    assert _is_english_mirror(line) is True


# ---- 整体清洗 ----

def test_clean_cr_text_removes_navigation() -> None:
    src = (
        "[返回完整规则目录](/cr/) | [第五章](/cr/5/) | [第七章](/cr/7/)\n"
        "\n"
        "# 6. 咒语、异能和效应\n"
    )
    out = clean_cr_text(src)
    assert "返回完整规则目录" not in out
    assert "# 6. 咒语、异能和效应" in out


def test_clean_cr_text_strips_html_tags() -> None:
    src = "<b id='cr601-1'>601.1.</b> 多年以来，施放咒语...\n"
    out = clean_cr_text(src)
    assert "<b" not in out
    assert "</b>" not in out
    assert "601.1." in out
    assert "多年以来，施放咒语" in out


def test_clean_cr_text_simplifies_internal_links() -> None:
    src = "参见规则[601.2a-d](/cr/6/#cr601-2a)的说明。"
    out = clean_cr_text(src)
    assert "(/cr/6/#cr601-2a)" not in out
    assert "601.2a-d" in out


def test_clean_cr_text_drops_english_mirror_paragraphs() -> None:
    src = (
        "<b id='cr601-1'>601.1.</b> 多年以来，施放咒语或作为咒语施放一张牌这个动作，在牌上被称为\"使用\"该咒语或该牌。\n"
        "<b>601.1.</b> Previously, the action of casting a spell, or casting a card as a spell, was referred to on cards as \"playing\" that spell or that card.\n"
        "\n"
        "<b id='cr601-1a'>601.1a</b> 一些效应依然\"使用\"一张牌。\n"
        "<b>601.1a</b> Some effects still refer to \"playing\" a card.\n"
    )
    out = clean_cr_text(src)
    # 中文行保留
    assert "多年以来" in out
    assert "一些效应依然" in out
    # 英文镜像行（即使带规则编号）整段被删
    assert "Previously, the action" not in out
    assert "Some effects still refer" not in out


def test_clean_cr_text_drops_example_pairs() -> None:
    """中文 Example 后紧跟的英文 Example: 行应被删除。"""
    src = (
        "例如：如果一个咒语为\"横置两个目标生物\"，则同一个目标不能被选择两次。\n"
        "Example: If a spell says \"Tap two target creatures,\" then the same creature can't be chosen twice.\n"
    )
    out = clean_cr_text(src)
    assert "例如" in out
    assert "Example:" not in out
    assert "Tap two target creatures" not in out


def test_clean_cr_text_size_reduction() -> None:
    """完整的中英镜像段落，清洗后体积应该明显减少。"""
    src = (
        "<b id='cr601-1'>601.1.</b> 多年以来，施放咒语或作为咒语施放一张牌这个动作，在牌上被称为\"使用\"该咒语或该牌。\n"
        "<b>601.1.</b> Previously, the action of casting a spell was referred to on cards as \"playing\" that spell.\n"
        "\n"
        "例如：如果一个咒语为\"横置两个目标生物\"，则同一个目标不能被选择两次。\n"
        "Example: If a spell says \"Tap two target creatures,\" then the same creature can't be chosen twice.\n"
    )
    out = clean_cr_text(src)
    # 至少减少 30%（HTML 剥离 + 英文 Example 行 + 部分英文段落删除）
    assert len(out) < len(src) * 0.7


def test_clean_cr_text_collapses_blank_lines() -> None:
    src = "段落一。\n\n\n\n段落二。"
    out = clean_cr_text(src)
    # 三个以上空行压成两个
    assert "\n\n\n" not in out
    assert "段落一" in out
    assert "段落二" in out


# ---- 派发器 ----

def test_clean_for_reference_unchanged() -> None:
    """skill/references/ 下的人工 markdown 已经干净，不动。"""
    src = "## 613. 持续性效应\n\n**613.1.** 确定物件特征首先从该物件本身开始。"
    assert clean_for(src, "reference", file_name="continuous-effects.md") == src


def test_clean_for_glossary_reclassified_to_reference_still_cleaned() -> None:
    """CR 目录里的 glossary*.md 被 ingest 重分类成 reference，但内容是 CR 风格，应走 CR 清洗。"""
    src = (
        "### <span id='Ability'>Ability</span> / <span id='异能'>异能</span>\n"
        "1. 一个物件上解释这个物件作什么或可以作什么的叙述。\n"
        "1. Text on an object that explains what that object does or can do.\n"
    )
    out = clean_for(src, "reference", file_name="glossary.md")
    assert "<span" not in out
    assert "Text on an object that explains" not in out
    assert "异能" in out


def test_clean_for_mtr_unchanged() -> None:
    """mtr 是 DokuWiki 语法，先保留不清洗。"""
    src = "[[:MTR|返回MTR目录]] | [[:MTR:2|第二章]]\n====== MTR 1. ======\n"
    assert clean_for(src, "mtr") == src


def test_clean_for_cr_runs() -> None:
    """cr 文档应触发清洗。"""
    src = "<b id='cr1-1'>1.1.</b> 中文内容\n<b>1.1.</b> English mirror line text content here.\n"
    out = clean_for(src, "cr")
    assert out != src
    assert "<b" not in out


# ---- chunker 兼容性 ----

def test_cleaned_text_still_chunkable() -> None:
    """清洗后的文本能被现有 chunker 正确切片（规则号正则同时支持 HTML 与 plain text）。"""
    from app.retrieval.chunker import chunk_cr_file

    src = (
        "<b id='cr601-1'>601.1.</b> 多年以来，施放咒语...\n"
        "<b>601.1.</b> Previously, the action of casting a spell.\n"
        "<b id='cr601-2'>601.2.</b> 施放咒语指将其...\n"
        "<b>601.2.</b> To cast a spell is to take it from where it is.\n"
    )
    cleaned = clean_cr_text(src)
    chunks = chunk_cr_file(cleaned, "test.md")
    section_ids = {c.section_id for c in chunks}
    assert "601.1" in section_ids
    assert "601.2" in section_ids
