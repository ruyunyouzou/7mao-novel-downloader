# -*- coding: utf-8 -*-
"""
书链接 / 书 ID 解析：从用户粘贴的多种输入中提取七猫 book_id。

支持：
- 纯数字 ID：  "12345678"
- 分享链接：  "https://m.qimao.com/shares/12345678?..."
- APP 分享短链："https://app.share.wztxw.com/app-h5/freebook/article_detail/214789"
- 带参数：    "https://www.qimao.com/book/12345678" / "?id=12345678" / "?bookId=..."
- 手机号/口令文本里嵌入的 ID

解析失败返回空串，由 UI 提示用户。
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

# 明确的 key（优先级高）
_KEY_PATTERNS = [
    r"[?&](?:id|book[_-]?id|bookId|bid)=(\d{4,})",
]
# URL 路径里的数字段。七猫系域名不止 qimao.com，APP 分享常用 wztxw.com 短链，
# 路径形态也有 book/shares/chapter/article_detail 等多种，统一宽松匹配。
_PATH_PATTERN = (
    r"(?:qimao\.com|wztxw\.com)"          # 已知七猫系域名
    r"/(?:[^/]+/)*?"                       # 允许中间任意层级（如 app-h5/freebook/）
    r"(?:book|shares?|chapter|article_detail)/(\d{4,})"
)
# 兜底：任意独立数字（6-12 位）。七猫存在 6 位书 ID（如 APP 分享的 214789），
# 原先下限 7 位会漏掉，故下调到 6。
_BARE_PATTERN = r"(?<!\d)(\d{6,12})(?!\d)"


def extract_book_id(text: str) -> str:
    """从任意输入提取 book_id；失败返回空串。"""
    text = (text or "").strip()
    if not text:
        return ""
    # 1) 查询参数 key
    for pat in _KEY_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1)
    # 2) 标准 URL 解析 query
    if "://" in text:
        try:
            q = parse_qs(urlparse(text).query)
            for key in ("id", "bookId", "book_id", "bid"):
                for v in q.get(key, []):
                    if str(v).isdigit() and len(str(v)) >= 4:
                        return str(v)
        except Exception:  # noqa: BLE001
            pass
    # 3) URL 路径
    m = re.search(_PATH_PATTERN, text, re.I)
    if m:
        return m.group(1)
    # 4) 纯数字（整个输入就是 ID）
    if re.fullmatch(r"\d{4,}", text):
        return text
    # 5) 兜底：文本里第一个长数字
    m = re.search(_BARE_PATTERN, text)
    if m:
        return m.group(1)
    return ""


