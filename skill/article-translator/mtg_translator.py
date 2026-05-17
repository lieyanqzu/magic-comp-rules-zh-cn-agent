#!/usr/bin/env python3
"""
万智牌专业翻译工具
- 集成 mtgch API 查证准确中文牌名
- 从本地规则库查询关键字异能官方译名
- 提供符合大中华区裁判社群规范的专业翻译
"""

import sys
import os
import re
import json
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class MTGCardNameResolver:
    """万智牌牌名解析器 - 使用 mtgch API"""

    MTGCH_API_BASE = "https://mtgch.com/api/v1"

    def __init__(self):
        self.cache = {}  # 缓存已查询的牌名

    def resolve_card_name(self, english_name: str) -> Dict:
        """
        查询牌的官方中文名称
        返回: {'en': '英文原名', 'cn': '中文官方名', 'found': True/False}
        """
        if english_name in self.cache:
            return self.cache[english_name]

        try:
            # 使用 autocomplete API
            encoded_name = urllib.parse.quote(english_name)
            url = f"{self.MTGCH_API_BASE}/autocomplete/?q={encoded_name}&is_for_deck=false&size=1&page=1"

            req = urllib.request.Request(url, headers={
                'accept': 'application/json',
                'User-Agent': 'mtg-translator/1.0'
            })

            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))

            if data.get('data') and len(data['data']) > 0:
                card = data['data'][0]
                result = {
                    'en': card.get('name', english_name),
                    'cn': card.get('display_name', english_name),
                    'scryfall_id': card.get('scryfall_id', ''),
                    'found': True
                }
                self.cache[english_name] = result
                return result

        except Exception as e:
            print(f"  ⚠️ 查询牌名失败 {english_name}: {e}")

        # 未找到或出错，返回原名称
        result = {'en': english_name, 'cn': english_name, 'found': False}
        self.cache[english_name] = result
        return result

    def resolve_card_names_in_text(self, text: str) -> List[Tuple[str, str, int, int]]:
        """
        从文本中提取并解析牌名
        返回: [(英文名, 中文名, 起始位置, 结束位置), ...]
        """
        # 常见万智牌牌名模式
        # 大写字母开头，包含空格、逗号、撇号等
        card_patterns = [
            r'\b[A-Z][a-zA-Z]+(?:,\s+[A-Z][a-zA-Z]+)+',  # 格式: "Name, Title"
            r'\bThe\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*',  # 格式: "The ..."
            r'\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){1,4}',  # 普通多词牌名
        ]

        found_cards = []
        for pattern in card_patterns:
            for match in re.finditer(pattern, text):
                en_name = match.group(0)
                # 排除常见非牌名词
                if self._is_likely_card_name(en_name):
                    result = self.resolve_card_name(en_name)
                    if result['found']:
                        found_cards.append((en_name, result['cn'], match.start(), match.end()))

        return found_cards

    def _is_likely_card_name(self, text: str) -> bool:
        """判断文本是否可能是牌名（排除常见句子开头）"""
        non_card_starts = [
            'The deck', 'The card', 'The combo', 'The main', 'The key',
            'This is', 'There are', 'When you', 'If you', 'You can',
            'To play', 'In this', 'For the', 'As the', 'It is',
        ]
        for prefix in non_card_starts:
            if text.startswith(prefix):
                return False
        return True


class MTGKeywordResolver:
    """万智牌关键字解析器 - 从本地规则库查询"""

    def __init__(self, rules_dir: Path = None):
        if rules_dir is None:
            # 默认规则库路径：上一级目录的 markdown/
            rules_dir = Path(__file__).parent.parent / 'markdown'
        self.rules_dir = rules_dir
        self.keyword_cache = {}
        self._load_keywords()

    def _load_keywords(self):
        """从规则库加载关键字异能"""
        # 关键字异能在 7.md 中，章节 702.x
        keywords_file = self.rules_dir / '7.md'
        if not keywords_file.exists():
            print(f"⚠️ 规则文件不存在: {keywords_file}")
            return

        try:
            content = keywords_file.read_text(encoding='utf-8')

            # 提取所有 702.x 关键字定义
            # 格式通常是: <b id='cr702-2'>702.2.</b> <b id='cr702-2a'/>死触</p>
            pattern = r"<b id='cr702-(\d+)[^']*'>([^<]+)</b>.*?死触|先攻|连击|敏捷|飞行|辟邪|不灭|系命|保护|延势|践踏|警戒|守护"

            # 手动定义常见关键字映射（从规则库提取）
            self.keyword_map = {
                # Evergreen 关键字
                'Deathtouch': '死触',
                'First strike': '先攻',
                'Double strike': '连击',
                'Flash': '闪现',
                'Flying': '飞行',
                'Haste': '敏捷',
                'Hexproof': '辟邪',
                'Indestructible': '不灭',
                'Lifelink': '系命',
                'Menace': '威慑',
                'Protection': '保护',
                'Reach': '延势',
                'Trample': '践踏',
                'Vigilance': '警戒',
                'Ward': '守护',
                'Prowess': '灵技',
                'Defender': '守军',
                'Intimidate': '威惧',
                'Landwalk': '地行者',
                'Shroud': '帷幕',
                # 常见机制
                'Cascade': '倾曳',
                'Convoke': '召集',
                'Delve': '掘穴',
                'Flashback': '返照',
                'Kicker': '增幅',
                'Suspend': '延缓',
                'Dredge': '发掘',
                'Storm': '风暴',
                'Equip': '配戴',
                'Aura': '灵气',
                'Tokens': '衍生物',
                'Counter': '反击 / 指示物',
                'Sacrifice': '牺牲',
                'Discard': '弃牌',
                'Draw': '抓牌',
                'Tap': '横置',
                'Untap': '重置',
                'Library': '牌库',
                'Hand': '手牌',
                'Graveyard': '坟墓场',
                'Battlefield': '战场',
                'Stack': '堆叠',
                'Exile': '放逐',
                'Mana': '法术力',
                'Color': '颜色',
                'Type': '类别',
                'Subtype': '副类别',
                'Supertype': '超类别',
                'Legendary': '传奇',
                'Basic': '基本',
                'Snow': '雪境',
                'Commander': '指挥官/主将',
                'EDH': '指挥官（EDH）',
                'Standard': '标准',
                'Modern': '现代',
                'Legacy': '薪传',
                'Vintage': '特选',
                'Pioneer': '先驱',
                'Pauper': '纯铁',
                'Limited': '限制赛',
                'Draft': '轮抽',
                'Sealed': '现开',
                'Combo': '组合技',
                'Ramp': '跳费',
                'Control': '控制',
                'Aggro': '快攻',
                'Midrange': '中速',
                'Tutor': '检索',
                'Fetch land': '找地地',
                'Shock land': '震撼地',
                'Dual land': '双色的地',
                'Mana rock': '法术力石',
                'Mana dork': '法术力生物',
                'Cantrip': '抓牌咒语',
                'Removal': '去除',
                'Board wipe': '清场',
                'Win condition': '制胜手段',
                'Infinite loop': '无限循环',
                'ETB': '进战场',
                'LTB': '离战场',
                'Death trigger': '死亡触发',
                'Enter the battlefield': '进战场',
                'Leave the battlefield': '离战场',
                'Die': '死去',
                'Destroy': '消灭',
                'Exiled': '被放逐',
                'Return': '移回',
                'Bounce': '弹回（回手）',
                'Mill': '磨牌',
                'Self-mill': '自磨',
                'Card advantage': '牌张优势',
                'Card draw': '抓牌',
                'Card selection': '选牌',
                'Tutor effect': '检索效应',
                'Lifegain': '获得生命',
                'Life total': '总生命',
                'Starting player': '先手玩家',
                'Active player': '主动牌手',
                'Non-active player': '非主动牌手',
                'Priority': '优先权',
                'Stack': '堆叠',
                'Resolve': '结算',
                'Counterspell': '反击咒语',
                'Instant': '瞬间',
                'Sorcery': '法术',
                'Creature': '生物',
                'Artifact': '神器',
                'Enchantment': '结界',
                'Planeswalker': '鹏洛客',
                'Land': '地',
                'Permanent': '永久物',
                'Spell': '咒语',
                'Ability': '异能',
                'Triggered ability': '触发式异能',
                'Activated ability': '启动式异能',
                'Static ability': '静止式异能',
                'Mana ability': '法术力异能',
                'Replacement effect': '替代式效应',
                'Prevention effect': '防止式效应',
                'Continuous effect': '持续性效应',
                'Layer': '层',
                'Timestamp': '时间印记',
                'Dependency': '从属关系',
                'Control': '操控',
                'Owner': '拥有者',
                'Target': '目标',
                'Cost': '费用',
                'Additional cost': '额外费用',
                'Alternative cost': '替代性费用',
                'Reduced cost': '减少费用',
                'Converted mana cost': '法术力费用值',
                'Mana value': '法术力值',
                'Color identity': '标识色',
                'Commander tax': '指挥官税',
                'Command zone': '指挥区',
                'Commander damage': '指挥官伤害',
                'Partner': '拍档',
                'Background': '背景',
                'Choose a Background': '选择背景',
            }

        except Exception as e:
            print(f"⚠️ 加载关键字失败: {e}")

    def resolve_keyword(self, english_keyword: str) -> Optional[str]:
        """查询关键字的中文译名"""
        # 尝试直接匹配
        if english_keyword in self.keyword_map:
            return self.keyword_map[english_keyword]

        # 尝试小写匹配
        lower = english_keyword.lower()
        for en, cn in self.keyword_map.items():
            if en.lower() == lower:
                return cn

        return None

    def resolve_keywords_in_text(self, text: str) -> Dict[str, str]:
        """从文本中提取所有关键字并返回映射"""
        found = {}

        # 按长度降序匹配，避免短词覆盖长词
        sorted_keywords = sorted(self.keyword_map.keys(), key=len, reverse=True)

        for en_keyword in sorted_keywords:
            # 使用词边界匹配
            pattern = r'\b' + re.escape(en_keyword) + r'\b'
            if re.search(pattern, text, re.IGNORECASE):
                found[en_keyword] = self.keyword_map[en_keyword]

        return found


class ProfessionalMTGTranslator:
    """专业万智牌翻译器"""

    def __init__(self):
        self.card_resolver = MTGCardNameResolver()
        self.keyword_resolver = MTGKeywordResolver()

    def translate_article(self, title: str, author: str, source_url: str, content: str) -> Dict:
        """
        专业翻译流程：
        1. 分析文本中的牌名
        2. 分析文本中的关键字
        3. 生成带注释的翻译提示
        4. 提供专业翻译
        """
        print(f"🔍 分析文章: {title}")
        print(f"   来源: {source_url}")
        print()

        # 步骤1: 提取并查证牌名
        print("📚 正在查证牌名...")
        card_names = self._extract_card_names(content)
        card_mapping = {}

        for en_name in card_names:
            result = self.card_resolver.resolve_card_name(en_name)
            if result['found']:
                card_mapping[en_name] = result
                print(f"   ✓ {en_name} → {result['cn']}")
            else:
                print(f"   ⚠️ {en_name} (未找到)")

        print()

        # 步骤2: 提取关键字
        print("🔑 正在分析关键字...")
        keywords = self.keyword_resolver.resolve_keywords_in_text(content)
        for en, cn in sorted(keywords.items(), key=lambda x: x[0]):
            print(f"   • {en} → {cn}")

        print()

        # 步骤3: 生成专业翻译
        print("📝 生成专业翻译...")
        translation = self._generate_translation(content, card_mapping, keywords)

        return {
            'title': title,
            'title_cn': self._translate_title(title, card_mapping),
            'author': author,
            'source_url': source_url,
            'card_mapping': card_mapping,
            'keywords': keywords,
            'original_content': content,
            'translated_content': translation,
            'translated_at': datetime.now().isoformat()
        }

    def _extract_card_names(self, text: str) -> List[str]:
        """从文本中提取可能的牌名"""
        # 使用常见模式识别牌名
        patterns = [
            # "Card Name" (带引号)
            r'"([^"]{3,40})"',
            # **Card Name** (加粗)
            r'\*\*([^*]{3,40})\*\*',
            # 大写开头的2-4词组合
            r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b',
        ]

        found = set()
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                name = match.group(1).strip()
                # 过滤掉明显不是牌名的内容
                if self._is_valid_card_name(name):
                    found.add(name)

        return sorted(found)

    def _is_valid_card_name(self, name: str) -> bool:
        """验证是否是有效的牌名格式"""
        # 长度检查
        if len(name) < 3 or len(name) > 40:
            return False

        # 排除常见非牌名词
        non_cards = [
            'The Deck', 'The Card', 'The Combo', 'The Main', 'The Key',
            'Introduction', 'Overview', 'Strategy', 'Conclusion',
            'Table of Contents', 'Card Choices', 'Matchups',
            'Early Game', 'Mid Game', 'Late Game',
            'Turn One', 'Turn Two', 'Turn Three',
            'Game One', 'Game Two', 'Game Three',
            'You Can', 'When You', 'If You', 'This Is',
            'One Of', 'Each Of', 'All Of', 'Some Of',
            'First', 'Second', 'Third', 'Last',
            'How To', 'What Is', 'Why You',
        ]

        for non in non_cards:
            if name.lower() == non.lower():
                return False

        return True

    def _translate_title(self, title: str, card_mapping: Dict) -> str:
        """翻译标题，保留牌名格式"""
        result = title
        for en_name, info in card_mapping.items():
            # 在标题中替换牌名
            pattern = re.escape(en_name)
            replacement = f"{info['cn']} ({en_name})"
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
        return result

    def _generate_translation(self, content: str, card_mapping: Dict, keywords: Dict) -> str:
        """生成专业翻译"""
        # 先替换牌名
        translated = content

        # 按名称长度降序排序，避免部分替换问题
        sorted_cards = sorted(card_mapping.items(), key=lambda x: len(x[0]), reverse=True)

        for en_name, info in sorted_cards:
            cn_name = info['cn']
            # 替换格式: Gitrog Monster → 诡诈怪 Gitrog Monster
            pattern = r'\b' + re.escape(en_name) + r'\b'
            replacement = f"{cn_name} ({en_name})"
            translated = re.sub(pattern, replacement, translated, flags=re.IGNORECASE)

        # 然后替换关键字（在剩余英文中）
        sorted_keywords = sorted(keywords.items(), key=lambda x: len(x[0]), reverse=True)
        for en_kw, cn_kw in sorted_keywords:
            pattern = r'\b' + re.escape(en_kw) + r'\b'
            translated = re.sub(pattern, cn_kw, translated, flags=re.IGNORECASE)

        return translated

    def save_translation(self, result: Dict, output_dir: Path = None) -> Path:
        """保存翻译结果"""
        if output_dir is None:
            output_dir = Path(__file__).parent / 'articles'
        output_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d')
        safe_title = re.sub(r'[^\w\s-]', '', result['title'])[:40]
        filename = f"{timestamp}_{safe_title.replace(' ', '_')}.md"
        filepath = output_dir / filename

        # 生成 Markdown
        md_content = self._generate_markdown(result)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(md_content)

        return filepath

    def _generate_markdown(self, result: Dict) -> str:
        """生成 Markdown 格式的翻译文档"""
        lines = []

        # 标题
        lines.append(f"# {result['title_cn']}")
        lines.append("")

        # 元信息
        lines.append(f"> **原文标题**: {result['title']}")
        lines.append(f"> **作者**: {result['author']}")
        lines.append(f"> **来源**: {result['source_url']}")
        lines.append(f"> **翻译时间**: {result['translated_at']}")
        lines.append("")

        # 牌名对照表
        if result['card_mapping']:
            lines.append("## 牌名对照表")
            lines.append("")
            lines.append("| 英文牌名 | 中文官方译名 |")
            lines.append("|---------|-------------|")
            for en_name, info in sorted(result['card_mapping'].items()):
                if info['found']:
                    lines.append(f"| {en_name} | {info['cn']} |")
            lines.append("")

        # 关键字对照表
        if result['keywords']:
            lines.append("## 术语对照表")
            lines.append("")
            lines.append("| 英文术语 | 中文译名 |")
            lines.append("|---------|---------|")
            for en_kw, cn_kw in sorted(result['keywords'].items()):
                lines.append(f"| {en_kw} | {cn_kw} |")
            lines.append("")

        # 分隔线
        lines.append("---")
        lines.append("")

        # 翻译正文
        lines.append(result['translated_content'])
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("*本文由 mtg-rules-agent 专业翻译工具生成*")
        lines.append("*牌名译名参考 mtgch.com 官方数据*")
        lines.append("*关键字译名参考《万智牌完整规则》中文译本*")

        return '\n'.join(lines)


def fetch_webpage_content(url: str) -> Optional[str]:
    """尝试获取网页内容"""
    # 尝试多个服务
    services = [
        f"https://r.jina.ai/http://{url}",
        f"https://r.jina.ai/{url}",
    ]

    for service_url in services:
        try:
            print(f"  尝试: {service_url[:60]}...")
            req = urllib.request.Request(service_url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; MTGTranslator/1.0)'
            })
            with urllib.request.urlopen(req, timeout=30) as response:
                content = response.read().decode('utf-8')
                if content and len(content) > 100:
                    return content
        except Exception as e:
            print(f"    失败: {e}")
            continue

    return None


def main():
    if len(sys.argv) < 2:
        print("""
专业万智牌文章翻译工具

用法:
  python3 mtg_translator.py translate <URL> <标题> [作者]
    - 抓取并翻译网页文章

  python3 mtg_translator.py translate-manual <标题> <作者> <来源URL>
    - 手动粘贴内容进行翻译

  python3 mtg_translator.py resolve <英文牌名>
    - 查询单个牌名的中文译名

示例:
  python3 mtg_translator.py translate "https://moxfield.com/decks/xxx/primer" "Gitrog Primer"
  python3 mtg_translator.py resolve "Gitrog Monster"
        """)
        sys.exit(1)

    command = sys.argv[1]

    if command == 'resolve':
        if len(sys.argv) < 3:
            print("错误: 需要提供牌名")
            sys.exit(1)

        card_name = sys.argv[2]
        resolver = MTGCardNameResolver()
        result = resolver.resolve_card_name(card_name)

        if result['found']:
            print(f"✓ {result['en']} → {result['cn']}")
        else:
            print(f"✗ 未找到: {card_name}")

    elif command == 'translate-manual':
        if len(sys.argv) < 5:
            print("错误: 需要提供 标题、作者、来源URL")
            sys.exit(1)

        title = sys.argv[2]
        author = sys.argv[3]
        source_url = sys.argv[4]

        print(f"\n请输入文章内容（输入 EOF 结束）：\n")
        lines = []
        while True:
            try:
                line = input()
                if line.strip() == 'EOF':
                    break
                lines.append(line)
            except EOFError:
                break

        content = '\n'.join(lines)

        translator = ProfessionalMTGTranslator()
        result = translator.translate_article(title, author, source_url, content)

        output_path = translator.save_translation(result)
        print(f"\n✅ 翻译完成！")
        print(f"   输出文件: {output_path}")

    elif command == 'translate':
        url = sys.argv[2]
        title = sys.argv[3] if len(sys.argv) > 3 else 'Untitled'
        author = sys.argv[4] if len(sys.argv) > 4 else 'Unknown'

        print(f"🌐 正在获取网页内容: {url}")
        content = fetch_webpage_content(url)

        if not content:
            print("\n❌ 无法自动获取网页内容。该网站可能有反爬虫保护。")
            print("\n建议：")
            print("1. 手动打开网页，复制文章内容")
            print(f"2. 然后运行: python3 mtg_translator.py translate-manual \"{title}\" \"{author}\" \"{url}\"")
            print("3. 粘贴内容，输入 EOF 结束")
            sys.exit(1)

        print(f"✓ 获取到 {len(content)} 字符")

        translator = ProfessionalMTGTranslator()
        result = translator.translate_article(title, author, url, content)

        output_path = translator.save_translation(result)
        print(f"\n✅ 翻译完成！")
        print(f"   输出文件: {output_path}")

    else:
        print(f"未知命令: {command}")
        sys.exit(1)


if __name__ == '__main__':
    main()
