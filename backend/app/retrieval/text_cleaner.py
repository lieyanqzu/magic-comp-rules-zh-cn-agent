"""规则文档清洗：CR / glossary 文件中的英文镜像、HTML 标签、内部链接等噪声。

设计要点：
- 仅清洗 CR 主规则与 glossary（它们是行级中英镜像，token 浪费最严重）。
- mtr / ipg / reference 不动：mtr/ipg 是 DokuWiki 复杂语法，reference 已经干净。
- 清洗后所有 chunk 的 content_hash 都会变化，启动时增量入库会触发全量
  重新生成 embedding。这是预期行为，不是 bug。
- 清洗在切片之前完成。chunker 的规则号正则同时支持 HTML 与 plain text，
  清洗后仍能正确切片，无需联动改动 chunker。

为什么用启发式而不是严格模式：
- CR 中英行的"判别线索"在不同小节里不一致：有些英文行用 `<b>` 无 id，
  有些 Example: 在 markdown 普通段落里。一刀切的 ASCII 占比启发式比维护
  多套结构化规则更鲁棒，误删风险也低（中文规则里夹的英文术语都是少数字符，
  整行 ASCII 占比仍 < 阈值）。
"""

from __future__ import annotations

import re

# HTML 标签：<b ...>、</b>、<span ...>、</span>、<br>、<i>...</i> 等
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# 内部规则链接： [显示文本](/cr/任意路径)  →  显示文本
# 仅匹配 /cr/ 开头的相对链接，避免误伤外部链接（如 wizards.com）
_CR_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(/cr/[^\)\s]*\)")

# 顶部导航行：以 [...](/cr/) 开头，整行用 | 串联多个目录链接
_NAV_LINE_RE = re.compile(r"^\s*\[[^\]]+\]\(/cr/?\)\s*\|")

# 一行的 ASCII 字母占比阈值。> 阈值视为英文镜像行。
# 0.55 实测对 CR 有较好分辨力：纯英文段落 > 0.7，
# 中文段落即便夹少量术语（如卡名 "Garruk's Horde"）通常 < 0.3。
_ENGLISH_ASCII_RATIO = 0.55

# 一行至少要有这么多字符才走 ASCII 占比判断；
# 太短的行（如 `## A`、`---`、列表标题）容易误判，整行保留。
_MIN_LINE_LEN_FOR_LANG_DETECT = 12

# 可能被误删的英文行白名单：仅保留 markdown 结构性元素，不再"含规则编号就保留" —
# CR 的英文镜像行也带相同规则编号（如 `<b>601.1.</b> Previously, ...`），
# 一旦放进白名单会让镜像行全部漏过，token 节省效果就没了。
# 含规则编号的判别交给 ASCII 占比启发式：英文镜像行整行 ASCII 占比高，自然命中。
_ALWAYS_KEEP_PATTERNS = (
    re.compile(r"^\s*#{1,6}\s+"),  # markdown heading
    re.compile(r"^\s*[-*+]\s"),  # 列表项
    re.compile(r"^\s*\|"),  # 表格
    re.compile(r"^\s*```"),  # 代码块
    re.compile(r"^\s*-{3,}\s*$"),  # 分隔线 ---
)


def _ascii_letter_ratio(line: str) -> float:
    """一行中 ASCII 字母字符 / 非空白字符 的占比。

    标点、空白不计入分母，避免长串空格干扰判断。
    """
    stripped = line.strip()
    if not stripped:
        return 0.0
    non_space = [c for c in stripped if not c.isspace()]
    if not non_space:
        return 0.0
    ascii_letters = sum(1 for c in non_space if c.isascii() and c.isalpha())
    return ascii_letters / len(non_space)


def _is_english_mirror(line: str) -> bool:
    """判断一行是否是中文规则的英文镜像行。

    判别策略：
    1. 空行、过短的行：保留
    2. markdown 结构行（heading、列表、表格、代码块、分隔线）：保留
    3. 显式 "Example:" 开头：英文镜像
    4. ASCII 字母占比 > 阈值：英文镜像（含规则编号的英文行也走这里）
    """
    if not line.strip():
        return False
    # 太短：保守保留（避免误删 `## A` 这类）
    if len(line.strip()) < _MIN_LINE_LEN_FOR_LANG_DETECT:
        return False
    # markdown 结构行 永远保留
    for pat in _ALWAYS_KEEP_PATTERNS:
        if pat.search(line):
            return False
    # 剥掉 HTML 标签后再判别 ASCII 占比，避免 `<b id='cr601-1'>` 把分子打高
    # 但对 plain text 行不会有副作用
    stripped_html = _HTML_TAG_RE.sub("", line)
    # 显式 "Example:" 开头的英文示例行
    if re.match(r"^\s*Example\s*:", stripped_html):
        return True
    # ASCII 占比启发式
    return _ascii_letter_ratio(stripped_html) > _ENGLISH_ASCII_RATIO


def _strip_html(line: str) -> str:
    return _HTML_TAG_RE.sub("", line)


def _simplify_cr_links(line: str) -> str:
    """`[规则 601.2a-d](/cr/6/#cr601-2a)` → `规则 601.2a-d`。"""
    return _CR_LINK_RE.sub(r"\1", line)


def _drop_navigation(content: str) -> str:
    """删除顶部导航行（仅删第一段连续的导航行）。"""
    lines = content.splitlines()
    out: list[str] = []
    started = False
    for line in lines:
        if not started and _NAV_LINE_RE.match(line):
            # 跳过导航行
            continue
        # 一旦遇到非空非导航的行，就不再尝试删除（避免误伤后续可能的链接）
        if line.strip():
            started = True
        out.append(line)
    return "\n".join(out)


def _collapse_blank_lines(content: str) -> str:
    """超过两个的连续空行压成两个，保留段落分隔。"""
    return re.sub(r"\n{3,}", "\n\n", content)


def clean_cr_text(content: str) -> str:
    """清洗 CR 规则文本（main markdown 文件 1.md ~ 9.md）。

    - 删除导航行
    - 删除英文镜像行
    - 剥离 HTML 标签
    - 简化内部链接
    - 压缩多余空行
    """
    if not content:
        return content

    content = _drop_navigation(content)

    out: list[str] = []
    for line in content.splitlines():
        if _is_english_mirror(line):
            continue
        cleaned = _simplify_cr_links(_strip_html(line))
        # 行尾原本可能有 markdown 强制换行的两空格，保留
        out.append(cleaned.rstrip() + ("  " if line.endswith("  ") else ""))
    return _collapse_blank_lines("\n".join(out))


def clean_glossary_text(content: str) -> str:
    """清洗 CR 词汇表（glossary.md / glossarycn.md）。

    与 CR 主规则同样需要去英文镜像 + 剥 HTML，但：
    - heading 行 `### <span id='X'>X</span> / <span id='中文'>中文</span>`
      被 strip 后变成 `### X / 中文`，是合法的标题格式，保留
    - `----` 分隔行保留
    """
    return clean_cr_text(content)


def clean_for(content: str, document_type: str, file_name: str = "") -> str:
    """清洗派发器：按文档类型选择清洗策略。

    Args:
        content: 原始文件内容
        document_type: cr / reference / mtr / ipg
        file_name: 文件名（区分 CR 目录里的 glossary —— 它被 ingest 重分类为
            "reference" 但内容仍是 CR 风格的 span/HTML，需要走 CR 清洗规则）
    """
    if document_type == "cr":
        return clean_cr_text(content)
    if document_type == "reference":
        # CR 目录下的 glossary*.md 被重分类成 reference，但实际格式是 CR 风格
        # （`### <span id='X'>X</span> / <span id='中文'>中文</span>` + 中英镜像段落）
        # 这种仍要走 CR 清洗。判别用文件名 + 内容启发式：含较多 `<span id=` 的视为 CR 风格。
        if file_name.lower().startswith("glossary") or content.count("<span id=") > 5:
            return clean_cr_text(content)
        # skill/references/ 下的人工 markdown，已干净
        return content
    if document_type in ("mtr", "ipg"):
        # DokuWiki 复杂语法，等专门做格式适配再清洗
        return content
    return content
