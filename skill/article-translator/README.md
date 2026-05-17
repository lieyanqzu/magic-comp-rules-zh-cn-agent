# MTG 文章翻译工具

翻译万智牌套牌指南(primer)和攻略文章。

## 用法

```bash
python3 mtg_translator.py translate-manual "标题" "作者" "来源URL"
# 粘贴文章内容，输入 EOF 结束
```

翻译后的文档保存到 `articles/` 目录。

## 功能

- 自动查证牌名（mtgch API）
- 术语标准化（规则库对照）
- Markdown 输出（含牌名/术语对照表）

## 说明

- `articles/` 目录被 git 忽略
- 翻译成果保留在本地
