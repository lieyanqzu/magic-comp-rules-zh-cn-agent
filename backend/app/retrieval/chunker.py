"""规则文档切片器。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger(__name__)
# 匹配纯文本规则编号（如 100.1a）和 HTML 包裹的规则编号（如 <b id='cr100-1a'>100.1a</b>）
_RULE_NUMBER_RE = re.compile(
    r"^(?:<b[^>]*>)?(\d{3}(?:\.\d+[a-z]?)?)(?:\.</b>|</b>)?\s*(.+)$"
)
# 清理残留的 HTML 标签
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")

# 单个 chunk 的目标最大字符数。embedding API 截断阈值是 4000，
# 这里设 1800 留出余量，并允许 RAG 检索后拼上下文。
MAX_CHUNK_CHARS = 1800
# 子片段间的重叠字符数，避免句子被一刀两断时丢失上下文
CHUNK_OVERLAP_CHARS = 200


@dataclass
class Chunk:
    section_id: str
    title: str
    content: str
    document_type: str
    source_path: str
    metadata: dict | None = None
    content_hash: str | None = None


def _clean_html(text: str) -> str:
    """移除 HTML 标签。"""
    return _HTML_TAG_RE.sub("", text).strip()


def _content_hash(content: str) -> str:
    """计算 content 的 sha256 前 16 位，用于增量入库判等。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _split_long_text(text: str, max_chars: int = MAX_CHUNK_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """把超长文本按段落 / 句子切成多个不超过 max_chars 的片段，相邻片段保留 overlap 字符。

    优先按 `\\n\\n` 段落切，单段还超长就按句号 / 换行二次切，最坏退化到硬切。
    """
    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []
    paragraphs = re.split(r"\n\n+", text)
    buf = ""

    def _flush() -> None:
        nonlocal buf
        if buf.strip():
            pieces.append(buf.strip())
        buf = ""

    for para in paragraphs:
        para = para.strip("\n")
        if not para:
            continue
        # 段落本身就过长 → 二次拆
        if len(para) > max_chars:
            _flush()
            for piece in _split_by_sentence(para, max_chars):
                pieces.append(piece)
            continue
        # 拼上当前段落会超 → 先 flush
        if buf and len(buf) + 2 + len(para) > max_chars:
            _flush()
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    _flush()

    if overlap > 0 and len(pieces) > 1:
        overlapped: list[str] = [pieces[0]]
        for i in range(1, len(pieces)):
            tail = pieces[i - 1][-overlap:]
            overlapped.append(f"{tail}\n\n{pieces[i]}")
        return overlapped
    return pieces


def _split_by_sentence(text: str, max_chars: int) -> list[str]:
    """段落级再切：按中英文句号 / 换行切，每片不超过 max_chars。"""
    sentences = re.split(r"(?<=[。！？!?；;])\s*|\n", text)
    pieces: list[str] = []
    buf = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if buf and len(buf) + len(sent) + 1 > max_chars:
            pieces.append(buf.strip())
            buf = sent
            # 单句仍超长 → 硬切
            while len(buf) > max_chars:
                pieces.append(buf[:max_chars])
                buf = buf[max_chars:]
            continue
        buf = f"{buf} {sent}" if buf else sent
    if buf.strip():
        pieces.append(buf.strip())
    return pieces


def _maybe_split(chunk: Chunk) -> list[Chunk]:
    """对超长 chunk 做二次切分，section_id 后追加 -part1/-part2 区分。"""
    if len(chunk.content) <= MAX_CHUNK_CHARS:
        chunk.content_hash = _content_hash(chunk.content)
        return [chunk]

    parts = _split_long_text(chunk.content)
    if len(parts) <= 1:
        chunk.content_hash = _content_hash(chunk.content)
        return [chunk]

    out: list[Chunk] = []
    for i, content in enumerate(parts, start=1):
        sub = Chunk(
            section_id=f"{chunk.section_id}#p{i}",
            title=chunk.title,
            content=content,
            document_type=chunk.document_type,
            source_path=chunk.source_path,
            metadata={**(chunk.metadata or {}), "split_part": i, "split_total": len(parts)},
        )
        sub.content_hash = _content_hash(content)
        out.append(sub)
    logger.info(
        "长 section 拆分", section_id=chunk.section_id, parts=len(parts), total_chars=len(chunk.content)
    )
    return out


def chunk_cr_file(content: str, source_path: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    current_section_id = ""
    current_title = ""
    current_lines: list[str] = []

    def _flush() -> None:
        if current_section_id and current_lines:
            base = Chunk(
                section_id=current_section_id,
                title=_clean_html(current_title),
                content="\n".join(current_lines).strip(),
                document_type="cr",
                source_path=source_path,
            )
            chunks.extend(_maybe_split(base))

    for line in content.splitlines():
        match = _RULE_NUMBER_RE.match(line.strip())
        if match:
            matched_id = match.group(1)
            # CR 中文文档每条规则中英对照排版，两行用同一 section_id：
            #   <b id='cr613-1f'>613.1f</b> 层6：...        ← 中文
            #   <b>613.1f</b> Layer 6: ...                  ← 英文
            # 这两行必须合并成一个 chunk，否则后写入的会按 UNIQUE 覆盖前者。
            if matched_id == current_section_id and current_lines:
                current_lines.append(line.strip())
                continue
            _flush()
            current_section_id = matched_id
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
            base = Chunk(
                section_id=section_id,
                title=current_title,
                content="\n".join(current_lines).strip(),
                document_type="reference",
                source_path=source_path,
            )
            chunks.extend(_maybe_split(base))

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
            base = Chunk(
                section_id=f"{document_type}-{section_counter}",
                title=current_title,
                content="\n".join(current_lines).strip(),
                document_type=document_type,
                source_path=source_path,
            )
            chunks.extend(_maybe_split(base))

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
