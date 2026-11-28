# -*- coding: utf-8 -*-
"""文库8 搜索匹配：归一化模糊匹配（借鉴 mikufoxxx/Wenku8_Flutter）。

设计要点（对齐参考项目 `lib/book_search.dart`，并针对中文轻小说书名扩展）：
- 全角转半角（U+FF01–FF5E → 减 0xFEE0，U+3000 → 半角空格）；
- 去分隔符 / 标点（空格、·・_-—、，。:：()（）[]【】「」《》等）；
- 转小写；
- **扩展**：忽略中文虚词「的 / 之」，使「邻家天使」能命中「邻家的天使」；
- 多关键词按空格切分、AND 组合（参考项目的多词语义）；
- 子序列兜底：归一化后关键词字符按序出现在标题中即命中（弱匹配，
  仅当长度 >= 2 时启用，避免单字误命中）。

纯函数、无网络依赖，便于单元测试严格验证。
"""

from __future__ import annotations

import re
import unicodedata

# 参考项目的分隔符集合 + 常见中文标点
_SPACING = re.compile(
    r"[\s·・_\-—–，,。.:：()（）\[\]【】「」『』《》〈〉、;；!！?？'\"“”‘’/\\|]+"
)
# 中文虚词：轻小说书名里「的 / 之」高频且无检索价值
_FILLER = str.maketrans("", "", "的之")


def to_halfwidth(value: str) -> str:
    """全角转半角（等价 Dart `_width`）。"""
    out = []
    for ch in str(value or ""):
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def normalize(value: str, drop_filler: bool = True) -> str:
    """归一化：全角→半角、去分隔符与标点、转小写、可选去中文虚词。"""
    text = to_halfwidth(value)
    text = _SPACING.sub("", text).lower()
    if drop_filler:
        text = text.translate(_FILLER)
    # 兼容 NFKC 仍未覆盖的兼容字符
    text = unicodedata.normalize("NFKC", text)
    return _SPACING.sub("", text).lower()


def terms_of(query: str) -> list[str]:
    """按空白切分查询词并逐个归一化，返回非空词（参考项目多词 AND 语义）。"""
    raw = to_halfwidth(query).strip()
    parts = re.split(r"\s+", raw)
    return [n for p in parts if p and (n := normalize(p))]


def _is_subsequence(needle: str, haystack: str) -> bool:
    """needle 的字符是否按序出现在 haystack 中。"""
    if not needle:
        return False
    it = iter(haystack)
    return all(ch in it for ch in needle)


def matches(query: str, title: str, *, author: str = "", summary: str = "") -> bool:
    """判断查询是否命中书籍元数据：所有词都要在「书名/作者/简介」任一字段命中。

    单字段内用「子串 → 子序列」两级匹配；子序列仅对长度 >= 2 的词启用，
    且不跨虚词差异（归一化阶段已去虚词），避免单字或乱序误命中。
    """
    terms = terms_of(query)
    if not terms:
        return True
    fields = (normalize(title), normalize(author), normalize(summary))
    for term in terms:
        ok = False
        for field in fields:
            if not field:
                continue
            if term in field or (len(term) >= 2 and _is_subsequence(term, field)):
                ok = True
                break
        if not ok:
            return False
    return True


def relevance(query: str, title: str, *, author: str = "", summary: str = "") -> int:
    """命中强度：子串记 2 分、子序列记 1 分，用于排序（高者靠前）。

    挑刺 #26：title 命中权重最高；作者命中记 1 分、简介命中记 0.5 分
    （取整后相加），这样「作者名搜到书」不会因 0 分被排到末尾。
    默认 author/summary 为空，保持既有调用（含单测）行为不变。
    """
    terms = terms_of(query)
    t = normalize(title)
    a = normalize(author)
    s = normalize(summary)
    score = 0
    for term in terms:
        if term in t:
            score += 2
        elif len(term) >= 2 and _is_subsequence(term, t):
            score += 1
        elif a and (term in a or (len(term) >= 2 and _is_subsequence(term, a))):
            score += 1
        elif s and term in s:
            score += 1
    return score
