"""规则切片器单元测试。"""

from app.retrieval.chunker import chunk_cr_file, chunk_mtr_or_ipg_file, chunk_reference_file

SAMPLE_CR = """100. 一般规则
100.1. 这些规则适用于所有万智牌游戏。
100.1a 如果一条规则说玩家"可以"做某事，这意味着该玩家可以选择是否做。
101. 最高原则
101.1. 如果牌上说的和规则说的不一致，以牌上的文字为准。
"""

SAMPLE_REFERENCE = """# 层系统 613.x

持续效应按照以下层顺序应用。

## 层 1 - 复制效应

复制效应在层 1 应用（规则 707）。

## 层 2 - 控制权变更

控制权变更效应在层 2 应用。
"""


def test_chunk_cr_file() -> None:
    chunks = chunk_cr_file(SAMPLE_CR, "markdown/1.md")
    assert len(chunks) >= 3
    assert chunks[0].section_id == "100"
    assert chunks[0].document_type == "cr"
    assert chunks[1].section_id == "100.1"
    assert chunks[2].section_id == "100.1a"


def test_chunk_reference_file() -> None:
    chunks = chunk_reference_file(SAMPLE_REFERENCE, "references/continuous-effects.md")
    assert len(chunks) >= 2
    assert all(c.document_type == "reference" for c in chunks)


def test_chunk_preserves_content() -> None:
    chunks = chunk_cr_file(SAMPLE_CR, "markdown/1.md")
    chunk_1001a = next(c for c in chunks if c.section_id == "100.1a")
    assert "可以选择" in chunk_1001a.content


def test_chunk_empty_content() -> None:
    chunks = chunk_cr_file("", "markdown/empty.md")
    assert chunks == []
