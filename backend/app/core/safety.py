"""LLM 输入安全过滤：防止 prompt injection 与滥用为通用 LLM。

设计原则：
- 在 LLM 调用前完成所有过滤，避免消耗 token、降低延迟、防止能力暴露。
- 优先用宽松而精准的规则，宁可放过也不要误伤合法 MTG 问题。
- 真正可疑的请求直接返回固定拒绝消息（不走 LLM），断绝注入触达模型的路径。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyVerdict:
    allowed: bool
    reason: str = ""  # 拒绝时给前端的原因


# 经典 prompt injection 模式（中英）。这些短语在合法 MTG 问题里几乎不出现，
# 一旦命中就直接拒绝，不进 LLM。
# 注意：每条规则都要尽量精准，避免误伤——比如不要拦截"系统"两字（万智牌 CR 也常出现"层系统"）。
_INJECTION_PATTERNS: tuple[re.Pattern, ...] = (
    # 英文 jailbreak 套话
    re.compile(r"(?i)\bignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)"),
    re.compile(r"(?i)\bdisregard\s+(all\s+)?(previous|prior|the\s+system)"),
    re.compile(r"(?i)\b(you\s+are\s+now|you\s+will\s+now\s+act\s+as|act\s+as\s+(if\s+you\s+were|a))\b"),
    re.compile(r"(?i)\bdeveloper\s+mode\b|\bDAN\b|\bjailbreak\b"),
    re.compile(r"(?i)\bsystem\s*(prompt|message|role)\s*[:=]"),
    re.compile(r"(?i)\bnew\s+(system\s+)?instructions?\s*[:=]"),
    re.compile(r"(?i)\bprint\s+(your|the)\s+(system\s+)?(prompt|instructions)"),
    re.compile(r"(?i)\brepeat\s+(your|the)\s+(system\s+)?(prompt|instructions)"),
    re.compile(r"(?i)<\s*\|?\s*(system|im_start|im_end|s)\s*\|?\s*>"),
    # 中文 jailbreak 套话
    re.compile(r"忽略\s*(以上|之前|前面|上述)?\s*(所有)?\s*(的)?\s*(指令|提示|规则|系统)"),
    re.compile(r"(从现在起|从此刻起|现在起)\s*你(是|扮演)"),
    re.compile(r"假装你?(是|为)(?!.*万智牌)"),  # 假装你是 X，但 X 不能含"万智牌"
    re.compile(r"扮演\s*[一个]?\s*(?!.*(裁判|万智牌|MTG))"),
    re.compile(r"输出\s*(你的|完整的?)?\s*(系统\s*提示|系统\s*指令|提示词)"),
    re.compile(r"重复\s*(你的|完整的?)?\s*(系统\s*提示|系统\s*指令|提示词)"),
    re.compile(r"(开发者|调试|debug|超级)\s*模式"),
    re.compile(r"越狱\s*模式|jailbreak"),
    # 角色扮演开头
    re.compile(r"^\s*###\s*(system|系统)\s*[:：]", re.MULTILINE),
)


# 明显跑题的请求模式：用户根本不是来问 MTG 的。命中即拒绝。
# 同样要求精准——"翻译"在万智牌里是合法术语（translated_text），但"帮我翻译这段"几乎肯定是滥用。
_OFF_TOPIC_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"(帮|请|给).{0,4}(写|生成|创作).{0,4}(代码|程序|脚本|python|java|sql|shell)"),
    re.compile(r"(?i)\b(write|generate|create)\s+(a\s+)?(code|program|script|function|python|java)"),
    re.compile(r"(帮|请|给).{0,4}(写|做).{0,3}(首|篇|个|段|份).{0,10}(诗|歌|文章|作文|小说|故事|报告|总结)"),
    re.compile(r"(帮|请|给).{0,4}翻译(这|下|一下|以下)"),
    re.compile(r"(?i)\btranslate\s+(this|the\s+following)\b"),
    re.compile(r"(帮|请|给).{0,4}(算|计算|解).{0,4}(数学|方程|题目)"),
    re.compile(r"(?i)what'?s?\s+the\s+weather"),
    re.compile(r"(今天|明天|后天).{0,6}(天气|气温)"),
)


# 长度上限（用户 schema 已限 5000，这里再做一次保险）
MAX_QUESTION_CHARS = 5000


REFUSAL_OFF_TOPIC = (
    "我是万智牌规则裁判助手，只回答与万智牌（Magic: The Gathering）规则、牌张互动、"
    "比赛规则、违规处理相关的问题。请提交一个万智牌相关的问题。"
)
REFUSAL_INJECTION = (
    "检测到不被允许的请求格式。请直接描述你的万智牌规则问题，"
    "不要包含修改助手身份或行为的指令。"
)


def check_input_safety(question: str) -> SafetyVerdict:
    """L1 输入预过滤。在 LLM 调用前执行，零成本拦截明显滥用。

    返回 (allowed, reason)：allowed=False 时 reason 是给前端的中文拒绝消息。
    通过此过滤的请求仍会经过 system prompt 加固（L2）和输出校验（L3）。
    """
    if not question or not question.strip():
        return SafetyVerdict(allowed=False, reason="问题不能为空。")

    if len(question) > MAX_QUESTION_CHARS:
        return SafetyVerdict(
            allowed=False,
            reason=f"问题超过 {MAX_QUESTION_CHARS} 字上限。",
        )

    for pat in _INJECTION_PATTERNS:
        if pat.search(question):
            return SafetyVerdict(allowed=False, reason=REFUSAL_INJECTION)

    for pat in _OFF_TOPIC_PATTERNS:
        if pat.search(question):
            return SafetyVerdict(allowed=False, reason=REFUSAL_OFF_TOPIC)

    return SafetyVerdict(allowed=True)
