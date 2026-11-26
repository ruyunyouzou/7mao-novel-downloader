# -*- coding: utf-8 -*-
"""
书链接 / 书 ID 解析：从用户粘贴的任意输入中提取目标 book_id。

七猫（纯解析即可用）：
- 纯数字 ID：  "12345678"
- 分享链接：  "https://m.qimao.com/shares/12345678?..."
- APP 分享短链："https://app.share.wztxw.com/app-h5/freebook/article_detail/214789"
- 带参数：    "https://www.qimao.com/book/12345678" / "?id=12345678" / "?bookId=..."
- 口令文本里嵌入的 ID

番茄（解析出 ID 后需二次归一化，见 gui.fanqie_core.fanqie_reader.resolve_book_id）：
- 书籍页：    "https://fanqienovel.com/page/{bookId}"
- 章节页：    "https://fanqienovel.com/reader/{chapterId}"（章节 ID ≠ 书籍 ID）
- 多域名：    fanqienovel.com / m. / www. / 任意 *.fanqienovel.com / 无 scheme
- 多路径：    page / reader / book / chapter / discovery/book / shares/share / detail
- query：     ?book_id= / ?item_id= / ?bookId= / ?bid= / ?id=
- 口令文本：   "8.86 复制打开番茄小说，看看《书名》 https://fanqienovel.com/page/... 复制此链接…"
- 纯数字 ID：  bookId 或 chapterId（13~24 位）

解析失败返回空串，由 UI 提示用户。
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

# 明确的 query key（优先级高，兼容七猫与番茄）
_KEY_PATTERNS = [
    r"[?&](?:id|book[_-]?id|bookId|bid)=(\d{4,})",
]
# 番茄专用 query key（书籍 ID / 章节 ID 都接受，后续统一归一化）
_FANQIE_QUERY_KEYS = (
    "book_id", "bookId", "book-id", "item_id", "itemId", "item-id",
    "chapter_id", "chapterId", "bid", "id",
)

# 七猫系域名不止 qimao.com，APP 分享常用 wztxw.com 短链；
# 路径形态也有 book/shares/chapter/article_detail 等多种，统一宽松匹配。
_PATH_PATTERN = (
    r"(?:qimao\.com|wztxw\.com)"          # 已知七猫系域名
    r"/(?:[^/]+/)*?"                       # 允许中间任意层级（如 app-h5/freebook/）
    r"(?:book|shares?|chapter|article_detail)/(\d{4,})"
)

# 番茄：任意子域 + 任意路径层级，抓 host 之后 ?# 之前的第一个独立长数字。
# 这样 page / reader / discovery/book / shares/share / detail 等形态一律覆盖，
# 且不依赖写死的路径白名单（番茄时常新增分享路径）。
_FANQIE_PATH_PATTERN = (
    r"(?:[A-Za-z0-9-]+\.)*fanqienovel\.com"   # 任意子域
    r"/[^\s?#]*?"                              # 路径任意层级
    r"(?<!\d)(\d{6,24})(?!\d)"                 # 目标 ID
)

# 兜底：任意独立数字（6-24 位）。七猫存在 6 位书 ID（如 APP 分享的 214789），
# 番茄 ID 更长，故上限放宽到 24。
_BARE_PATTERN = r"(?<!\d)(\d{6,24})(?!\d)"

# 番茄识别特征：官网域名 + App 分享短链域名（changdunovel / fqnovel）
_FANQIE_HINT = re.compile(
    r"fanqienovel\.com|changdunovel\.com|fqnovel\.com|fanqie\s*novel|番茄", re.I)
# 文库8（轻小说文库）识别特征：域名 + /book/{id}.htm 或 /novel/{n}/{id}/ 路径
_WENKU8_HINT = re.compile(
    r"wenku8\.(?:cc|com|net)|轻小说文库|文库8", re.I)
_WENKU8_BOOK = re.compile(r"/book/(\d+)\.htm", re.I)
_WENKU8_NOVEL = re.compile(r"/novel/\d+/(\d+)/", re.I)
# 番茄 /t/<token> 分享短链（token 为字母数字混合，非书籍 ID，需联网还原）
_FANQIE_SHORT_LINK = re.compile(r"https?://[^/\s]+/t/[A-Za-z0-9_-]+/?", re.I)
# 纯数字书 ID：番茄 ID 通常 13~24 位，七猫多为 6~12 位
_FANQIE_PURE_ID = re.compile(r"\d{13,24}\Z")


def detect_source(text: str) -> str:
    """识别输入文本的书源：番茄 / 文库8 / 七猫（默认）。"""
    text = (text or "").strip()
    if _FANQIE_HINT.search(text):
        return "fanqie"
    if _WENKU8_HINT.search(text):
        return "wenku8"
    if _FANQIE_PURE_ID.match(text):
        return "fanqie"
    return "qimao"


def extract_book_title(text: str) -> str:
    """从口令文本里提取《书名》，供短链失效时按书名回退搜索。

    例："推荐一部好书《邻家妹妹恋上我》https://changdunovel.com/t/xxx/"
    → "邻家妹妹恋上我"。找不到返回空串。
    """
    m = re.search(r"《([^》]{1,80})》", str(text or ""))
    return m.group(1).strip() if m else ""


def _query_id(text: str, keys) -> str:
    """从 URL（含无 scheme 形态）的 query 中提取数字 ID。"""
    candidate = text if "://" in text else "https://" + text
    try:
        q = parse_qs(urlparse(candidate).query)
    except Exception:  # noqa: BLE001
        return ""
    for key in keys:
        for value in q.get(key, []):
            value = str(value)
            if value.isdigit() and len(value) >= 4:
                return value
    return ""


def extract_book_id(text: str) -> str:
    """从任意输入提取目标 ID；失败返回空串。

    番茄 reader 链接返回的是章节 ID，调用方需经
    ``gui.fanqie_core.fanqie_reader.resolve_book_id`` 归一化为书籍 ID。
    """
    text = (text or "").strip()
    if not text:
        return ""
    is_fanqie = bool(_FANQIE_HINT.search(text))

    # 0a) 文库8：/book/{id}.htm 或 /novel/{n}/{id}/xxx.htm，优先于通用数字兜底
    if _WENKU8_HINT.search(text):
        m = _WENKU8_BOOK.search(text) or _WENKU8_NOVEL.search(text)
        if m:
            return m.group(1)

    # 0) 番茄 /t/<token> 分享短链：token 非书籍 ID，须联网跟随重定向还原。
    #    放在最前，避免被口令文本里的其它数字（如时间戳）干扰。
    short = _FANQIE_SHORT_LINK.search(text)
    if short:
        try:
            from gui.fanqie_core.fanqie_reader import resolve_short_link
            real = resolve_short_link(short.group(0))
            if real:
                return real
        except Exception:  # noqa: BLE001 - 离线/网络异常时继续走其它规则
            pass

    # 1) 通用 query key（七猫 ?id= / ?bookId= 等）
    for pat in _KEY_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1)

    # 2) 番茄 query key（page?id=... 这类路径无数字的形态）
    if is_fanqie:
        found = _query_id(text, _FANQIE_QUERY_KEYS)
        if found:
            return found

    # 3) 番茄 URL 路径（在通用兜底之前，避免被口令文本里的其它数字干扰）
    m = re.search(_FANQIE_PATH_PATTERN, text, re.I)
    if m:
        return m.group(1)

    # 4) 标准 URL query（非番茄）
    if "://" in text:
        found = _query_id(text, ("id", "bookId", "book_id", "bid"))
        if found:
            return found

    # 5) 七猫 URL 路径
    m = re.search(_PATH_PATTERN, text, re.I)
    if m:
        return m.group(1)

    # 6) 纯数字（整个输入就是 ID）
    if re.fullmatch(r"\d{4,}", text):
        return text

    # 7) 兜底：文本里第一个长数字
    m = re.search(_BARE_PATTERN, text)
    if m:
        return m.group(1)
    return ""
