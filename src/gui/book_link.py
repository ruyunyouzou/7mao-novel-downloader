# -*- coding: utf-8 -*-
"""
书链接 / 书 ID 解析：从用户粘贴的多种输入中提取七猫 book_id。

支持：
- 纯数字 ID：  "12345678"
- 分享链接：  "https://m.qimao.com/shares/12345678?..."
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
# URL 路径里的数字段
_PATH_PATTERN = r"qimao\.com/(?:book|shares?|chapter)/(\d{4,})"
# 兜底：任意独立长数字（7-12 位）
_BARE_PATTERN = r"(?<!\d)(\d{7,12})(?!\d)"


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


