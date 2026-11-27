# -*- coding: utf-8 -*-
"""文库8 搜索：站内搜索接口优先 + 分类页遍历兜底 + 归一化模糊匹配。

搜索策略（v5.0.7 重写，借鉴 mikufoxxx/Wenku8_Flutter 的匹配算法）：
1. 先尝试站内搜索接口（多候选 URL/参数形式，带登录 Cookie）；
2. 站内无结果/失败时，回退分类页遍历（扩大页数），本地归一化匹配；
3. 匹配用 wenku8_match.matches（全角/标点/虚词归一化 + 多词 AND + 子序列兜底），
   解决「邻家天使」搜不到「邻家的天使」这类问题。

若需精确下载，用户也可直接粘贴书页 URL / 书号（resolve_book_id 归一化）。
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from .wenku8_auth import get_base_url, make_session
from .wenku8_match import matches as _matches, relevance as _relevance
from .wenku8_reader import _decode

# 文库分类：1-14（见 articlelist.php 导航）
_CLASSES = list(range(1, 15))

# 站内搜索候选：不同站点版本参数名不同，逐个尝试直到拿到结果。
# 真实书源配置（JackiuDeepInkSource / 开源阅读器）用的是
#   /modules/article/search.php?searchtype=articlename&searchkey={kw}  (charset=gbk)
# 参数 searchtype=articlename 是老接口必需，缺失时部分站点返回 Welcome 空页。
_SEARCH_ENDPOINTS = (
    # 与官网搜索表单（及 mikufoxxx/Wenku8_Flutter）完全一致：charset=gbk 必带
    "/modules/article/search.php?searchtype=articlename&searchkey={kw}&charset=gbk",
    "/modules/article/search.php?searchtype=articlename&searchkey={kw}",
    "/modules/article/search.php?searchkey={kw}",
    "/search.php?keyword={kw}",
    "/so.php?keyword={kw}",
    "/so.php?searchkey={kw}",
)


def _extract_books(html: str, base: str) -> List[dict]:
    """从搜索/列表页 HTML 提取书籍链接（兼容 /book/ 与 /novel/ 两种形式）。"""
    soup = BeautifulSoup(html or "", "html.parser")
    books: List[dict] = []
    seen = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        m = re.search(r"/book/(\d+)\.htm", href)
        if not m:
            m = re.search(r"/novel/\d+/(\d+)/", href)
        if not m:
            continue
        bid = m.group(1)
        if bid in seen:
            continue
        name = a.get_text(" ", strip=True)
        if not name or name in ("我要阅读", "返回书页", "点击阅读"):
            continue
        seen.add(bid)
        books.append({
            "id": bid, "title": name, "author": "", "intro": "",
            "cover": "", "source": "wenku8", "_rel": 0,
        })
    return books


def _encode_keyword_variants(keyword: str) -> List[str]:
    """生成关键词的 URL 编码候选项（GBK 优先，UTF-8 兜底，去重）。

    文库8全站 GBK，浏览器表单提交中文时用 GBK 编码（searchkey=%C1%DA...）。
    旧代码 quote() 默认 UTF-8（%E9%82%BB...），站点按 GBK 解码成乱码 → 搜不到。
    两种都试可兼容不同站点版本/声明，任一命中即可。
    """
    raw = str(keyword or "").strip()
    if not raw:
        return []
    out: List[str] = []
    for enc in ("gbk", "utf-8"):
        try:
            k = quote(raw.encode(enc))
        except Exception:  # noqa: BLE001
            k = quote(raw)
        if k not in out:
            out.append(k)
    return out


def _search_on_site(keyword: str, session, base: str, page: int = 1) -> List[dict]:
    """尝试站内搜索接口，返回命中的书籍（已按相关性排序）。

    对每种关键词编码（GBK 优先 / UTF-8 兜底）× 每个候选接口逐个尝试；
    任一接口返回含匹配书籍的页面即视为成功；全部失败返回空列表由调用方兜底。
    """
    kw_variants = _encode_keyword_variants(keyword)
    if not kw_variants:
        return []
    for kw in kw_variants:
        for tpl in _SEARCH_ENDPOINTS:
            url = base + tpl.format(kw=kw)
            # 分页参数：仅当 >1 时附加（不同站点分页参数不一，优先 page）
            if page > 1:
                url += f"&page={page}"
            try:
                resp = session.get(url, timeout=15)
                html = _decode(resp)
            except Exception:  # noqa: BLE001
                continue
            # 部分镜像（如 .cc）的搜索路径只返回 Welcome 占位页，
            # 不是「没有结果」，必须视为该通道失效并继续下一个候选/兜底。
            if "<h1>Welcome</h1>" in html or "Attention Required" in html:
                continue
            books = _extract_books(html, base)
            if not books:
                continue
            hits = [b for b in books if _matches(keyword, b["title"])]
            if hits:
                for b in hits:
                    b["_rel"] = _relevance(keyword, b["title"])
                hits.sort(key=lambda b: -b["_rel"])
                return hits
    return []


def _fetch_class_page(cls: int, page: int, session, base: str) -> List[dict]:
    """抓单个分类页的书籍列表。session 应为该工作线程独占的实例。"""
    url = f"{base}/modules/article/articlelist.php?class={cls}&page={page}"
    try:
        resp = session.get(url, timeout=15)
        html = _decode(resp)
    except Exception:  # noqa: BLE001
        return []
    return _extract_books(html, base)


def _search_categories(keyword: str, session, base: str, max_pages: int,
                       workers: int, page_offset: int = 0) -> List[dict]:
    """分类页遍历兜底：并发抓各分类页，本地归一化匹配。

    page_offset 用于翻页：第 N 页对应分类页的 (N-1)*max_pages+1 起，
    让 UI 翻页对文库8真正生效（原实现页码被丢弃）。

    requests.Session 非线程安全（连接池/CookieJar 共享会有竞争），并发抓取时
    为每个工作线程克隆一份带相同 Cookie 的 session，避免竞态导致偶发失败。
    """
    start = page_offset * max_pages + 1
    tasks = [(cls, p) for cls in _CLASSES
             for p in range(start, start + max_pages)]
    all_books: List[dict] = []

    # 提取调用方 session 的 Cookie，供各工作线程克隆
    cookie_str = ""
    try:
        cookie_str = "; ".join(f"{k}={v}" for k, v in
                               session.cookies.get_dict().items())
    except Exception:  # noqa: BLE001
        pass
    import threading
    tls = threading.local()

    def _grab(cls: int, p: int) -> List[dict]:
        """在工作线程内创建/复用线程专属 session（沿用调用方 Cookie）。"""
        s = getattr(tls, "session", None)
        if s is None:
            s = make_session(cookie_str, base=base)
            tls.session = s
        return _fetch_class_page(cls, p, s, base)

    pool = ThreadPoolExecutor(max_workers=max(1, min(16, workers)))
    try:
        fmap = {
            pool.submit(_grab, cls, p): (cls, p)
            for cls, p in tasks
        }
        for fut in as_completed(fmap):
            try:
                all_books.extend(fut.result() or [])
            except Exception:  # noqa: BLE001
                pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    seen = set()
    results: List[dict] = []
    for b in all_books:
        bid = b["id"]
        if bid in seen:
            continue
        seen.add(bid)
        if _matches(keyword, b["title"]):
            b["_rel"] = _relevance(keyword, b["title"])
            results.append(b)
    results.sort(key=lambda b: -b["_rel"])
    return results


def search_wenku8(keyword: str, session=None, max_pages: int = 8,
                  workers: int = 8, page: int = 1) -> List[dict]:
    """按关键词搜索文库8书籍。

    :param keyword: 书名/作者关键词
    :param max_pages: 分类页兜底时每个分类抓的页数（默认 8，原为 3，扩大覆盖）
    :param workers: 并发线程数
    :param page: UI 页码（>1 时站内接口附加 page、分类页跳过前 N-1 组）
    :return: [{id, title, author, intro, cover, source}]
    """
    kw = str(keyword or "").strip()
    if not kw:
        return []
    sess = session or make_session()
    base = get_base_url()
    page = max(1, int(page or 1))

    # 1) 站内搜索优先
    hits = _search_on_site(kw, sess, base, page=page)
    if hits:
        return [{k: v for k, v in b.items() if k != "_rel"} for b in hits]

    # 2) 分类页兜底
    results = _search_categories(kw, sess, base, max_pages=max_pages,
                                 workers=workers, page_offset=page - 1)
    return [{k: v for k, v in b.items() if k != "_rel"} for b in results]
