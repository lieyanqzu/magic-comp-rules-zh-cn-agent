"""规则检索的中文 query 扩展与术语预扫。

设计要点：
- 用户和 LLM 给的关键词经常和规则文档里的官方术语错位
  （"地变成生物" vs "运土"、"死了" vs "进入坟墓场"），
  这里维护一份双向同义词字典，在关键词分支前自动做 OR 展开。
- 字典只关心"高频且容易错位"的术语，不追求大而全 — 大词典反而稀释精度。
- 同时给 agent 用的术语预扫：从问题里识别已知机制名 / 关键字异能，
  返回可能相关的规则号 / 章节提示，用于"快路径"预检索。

字典是数据，不是代码 — 后续可移到 yaml/json 但目前规模小，inline 更易维护。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---- 同义词字典 ----
# 每组同义词代表"指向同一规则术语簇"。展开时把每组里命中的任一词
# 替换成这组所有词的 OR，丢给关键词分支。
# 设计原则：
#   1. 仅放确实容易错位的词。常用词如"生物""牌手"不放，避免炸召回。
#   2. 双向：用户用 A，文档用 B；用户用 B，文档用 A — 都要能映射。
#   3. 短词优先放在前面，长词在后，避免被短词子串误匹配。
SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    # 机制类
    ("替代式效应", "替代效应", "改为", "如果...则改为"),
    ("防止式效应", "防止效应", "防止伤害"),
    ("持续性效应", "持续效应"),
    ("复制效应", "复制咒语", "复制永久物", "克隆"),
    ("层", "层系统", "layer"),
    ("从属关系", "从属", "依赖关系", "dependency"),
    ("时间印记", "timestamp"),
    # 区域 / 状态
    ("堆叠", "叠区", "stack"),
    ("坟墓场", "墓地"),
    ("放逐区", "流放区"),
    ("战场", "场上"),
    ("进战场", "进入战场", "ETB", "进场"),
    ("离开战场", "离场", "LTB"),
    ("死亡", "死掉", "进入坟墓场", "被消灭"),
    # 时机
    ("优先权", "priority"),
    ("APNAP", "主动牌手", "非主动牌手", "主动玩家", "非主动玩家"),
    ("回合", "回合结构"),
    ("维持", "维持开始时", "维持步骤"),
    ("结束步骤", "结束阶段", "回合结束"),
    ("清除步骤", "清理步骤"),
    ("行动阶段", "主阶段"),
    # 异能类型
    ("触发式异能", "触发异能", "触发"),
    ("启动式异能", "启动异能"),
    ("静止式异能", "静止异能"),
    # 关键字异能（高频混淆中英文）
    ("不灭", "indestructible"),
    ("死触", "deathtouch"),
    ("先攻", "first strike"),
    ("连击", "double strike"),
    ("飞行", "flying"),
    ("延势", "reach"),
    ("践踏", "trample"),
    ("敏捷", "haste"),
    ("警戒", "vigilance"),
    ("系命", "lifelink"),
    ("保护", "protection"),
    ("辟邪", "hexproof"),
    ("守护", "ward"),
    ("闪现", "flash"),
    ("延缓", "suspend"),
    ("增幅", "kicker"),
    ("返照", "flashback"),
    ("召集", "convoke"),
    ("掘穴", "delve"),
    ("倾曳", "cascade"),
    # 数值 / 状态
    ("攻击力", "力量", "power"),
    ("防御力", "韧性", "toughness"),
    ("法术力", "mana"),
    ("法术力库", "法术力池", "储备池", "mana pool"),
    ("横置", "tap"),
    ("重置", "untap"),
    ("召唤失调", "行动不能", "summoning sickness"),
    # 比赛 / 违规
    ("比赛规则", "MTR"),
    ("违规处理", "IPG", "处罚"),
)


# ---- 术语 → 章节提示 ----
# 已知机制 → 可能相关的 CR 规则号前缀，命中时由 agent 用作 section_id 候选。
# 只放高确定度的映射；模糊场景留给关键词检索。
TERM_TO_SECTIONS: dict[str, tuple[str, ...]] = {
    "层": ("613",),
    "层系统": ("613",),
    "持续效应": ("611", "613"),
    "持续性效应": ("611", "613"),
    "替代式效应": ("614",),
    "替代效应": ("614",),
    "防止式效应": ("615",),
    "防止效应": ("615",),
    "复制效应": ("707",),
    "时间印记": ("613.7",),
    "从属关系": ("613.8",),
    "优先权": ("117",),
    "堆叠": ("405",),
    "APNAP": ("101.4",),
    "触发式异能": ("603",),
    "启动式异能": ("602",),
    "静止式异能": ("604",),
    "维持": ("503",),
    "重置步骤": ("502",),
    "抓牌步骤": ("504",),
    "战斗开始步骤": ("507",),
    "宣告攻击者": ("508",),
    "宣告阻挡者": ("509",),
    "战斗伤害": ("510",),
    "战斗结束步骤": ("511",),
    "结束步骤": ("513",),
    "清除步骤": ("514",),
    "清理步骤": ("514",),
    "不灭": ("702.12",),
    "死触": ("702.2",),
    "先攻": ("702.7",),
    "连击": ("702.4",),
    "飞行": ("702.9",),
    "延势": ("702.17",),
    "践踏": ("702.19",),
    "敏捷": ("702.10",),
    "警戒": ("702.20",),
    "系命": ("702.15",),
    "保护": ("702.16",),
    "辟邪": ("702.11",),
    "守护": ("702.21",),
    "闪现": ("702.8",),
    "延缓": ("702.62",),
    "增幅": ("702.33",),
    "返照": ("702.34",),
    "召集": ("702.51",),
    "掘穴": ("702.66",),
    "倾曳": ("702.85",),
}


@dataclass(frozen=True, slots=True)
class QueryExpansion:
    """关键词扩展结果。

    - keywords: 展开后的关键词列表（每个 group 命中后会把整组都加进来），
        关键词分支用 OR 拼起来跑一次；保留原顺序避免排序抖动。
    - hit_groups: 命中的同义词组（用于喂给 LLM 让它知道"我替你扩展了什么"）。
    - section_hints: 命中术语对应的规则号前缀候选。
    """

    keywords: list[str]
    hit_groups: list[tuple[str, ...]]
    section_hints: list[str]


def _normalize(text: str) -> str:
    """把全角标点 / 多余空白压平，便于子串匹配。"""
    return re.sub(r"\s+", " ", text.strip())


def expand_query(query: str) -> QueryExpansion:
    """展开 query：每组同义词若有任一命中，整组都加进 keywords。

    例：query="运土" → keywords=["运土", "地变成生物", "地成为生物", "manland"]
    """
    if not query or not query.strip():
        return QueryExpansion(keywords=[], hit_groups=[], section_hints=[])

    normalized = _normalize(query)
    # 原始 query 拆分出的词（按现有分隔符），保证它们一定在结果里
    base_terms = [t.strip() for t in re.split(r"[\s,，、;；]+", normalized) if t.strip()]
    out: list[str] = list(base_terms)
    seen = {t for t in out}
    hit_groups: list[tuple[str, ...]] = []
    section_hints: list[str] = []
    section_seen: set[str] = set()

    for group in SYNONYM_GROUPS:
        matched = any(term in normalized for term in group)
        if not matched:
            continue
        hit_groups.append(group)
        for term in group:
            if term not in seen:
                out.append(term)
                seen.add(term)
            for sec in TERM_TO_SECTIONS.get(term, ()):
                if sec not in section_seen:
                    section_hints.append(sec)
                    section_seen.add(sec)

    # 即便没命中同义词，也尝试用原始词做 section 提示（如用户直接打"层"）
    for term in base_terms:
        for sec in TERM_TO_SECTIONS.get(term, ()):
            if sec not in section_seen:
                section_hints.append(sec)
                section_seen.add(sec)

    return QueryExpansion(keywords=out, hit_groups=hit_groups, section_hints=section_hints)


def detect_terms(question: str) -> list[str]:
    """从用户问题里识别已知机制术语，返回命中的术语列表（保留首次出现顺序）。

    用于 agent 的快路径预扫：拿到术语就能查 TERM_TO_SECTIONS 得到候选规则号。
    """
    if not question:
        return []
    normalized = _normalize(question)
    hits: list[str] = []
    seen: set[str] = set()
    for term in TERM_TO_SECTIONS:
        if term in normalized and term not in seen:
            hits.append(term)
            seen.add(term)
    return hits


def section_hints_for(question: str) -> list[str]:
    """问题里识别到的术语对应的规则号前缀。"""
    out: list[str] = []
    seen: set[str] = set()
    for term in detect_terms(question):
        for sec in TERM_TO_SECTIONS.get(term, ()):
            if sec not in seen:
                out.append(sec)
                seen.add(sec)
    return out
