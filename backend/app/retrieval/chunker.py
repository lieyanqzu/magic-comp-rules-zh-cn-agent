"""规则文档切片器。"""

import re
from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger(__name__)
_RULE_NUMBER_RE = re.compile(r"^(\d{3}(?:\.\d+[a-z]?)?)\s*[.．。]?\s*(.+)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


@dataclass
class Chunk:
    section_id: str
    title: str
    content: str
    document_type: str
    source_path: str
    metadata: dict | None = None


def chunk_cr_file(content: str, source_path: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    current_section_id = ""
    current_title = ""
    current_lines: list[str] = []

    def _flush() -> None:
        if current_section_id and current_lines:
            chunks.append(Chunk(section_id=current_section_id, title=current_title, content="\n".join(current_lines).strip(), document_type="cr", source_path=source_path))

    for line in content.splitlines():
        match = _RULE_NUMBER_RE.match(line.strip())
        if match:
            _flush()
            current_section_id = match.group(1)
            current_title = line.strip()
            current_lines = [line.strip()]
            continue
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            _flush()
            current_section_id = ""
            current_title = heading_match.group(2).strip()
            current_lines = [line.strip()]
            continue
        current_lines.append(line)
    _flush()
    return chunks


def chunk_reference_file(content: str, source_path: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    current_title = ""
    current_lines: list[str] = []
    section_counter = 0

    def _flush() -> None:
        if current_lines:
            nonlocal section_counter
            section_counter += 1
            rule_match = _RULE_NUMBER_RE.match(current_title)
            section_id = rule_match.group(1) if rule_match else f"ref-{section_counter}"
            chunks.append(Chunk(section_id=section_id, title=current_title, content="\n".join(current_lines).strip(), document_type="reference", source_path=source_path))

    for line in content.splitlines():
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            _flush()
            current_title = heading_match.group(2).strip()
            current_lines = [line.strip()]
            continue
        current_lines.append(line)
    _flush()
    return chunks


def chunk_mtr_or_ipg_file(content: str, source_path: str, document_type: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    current_title = ""
    current_lines: list[str] = []
    section_counter = 0

    def _flush() -> None:
        if current_lines:
            nonlocal section_counter
            section_counter += 1
            chunks.append(Chunk(section_id=f"{document_type}-{section_counter}", title=current_title, content="\n".join(current_lines).strip(), document_type=document_type, source_path=source_path))

    for line in content.splitlines():
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            _flush()
            current_title = heading_match.group(2).strip()
            current_lines = [line.strip()]
            continue
        current_lines.append(line)
    _flush()
    return chunks


def chunk_file(content: str, source_path: str, document_type: str) -> list[Chunk]:
    if document_type == "cr":
        return chunk_cr_file(content, source_path)
    elif document_type == "reference":
        return chunk_reference_file(content, source_path)
    elif document_type in ("mtr", "ipg"):
        return chunk_mtr_or_ipg_file(content, source_path, document_type)
    return chunk_reference_file(content, source_path)
