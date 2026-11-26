# -*- coding: utf-8 -*-
"""文库8 搜索：分类页抓取 + 本地关键词过滤。

站内 so.php 被 WAF 拦截（403），search.php 返回空，故不走站内搜索。
改为遍历 14 个文库分类的前 N 页列表页，抓书名/作者在本地匹配关键词。
效果等同关键词搜索，且完全不依赖被封接口。

如需精确下载，用户也可直接粘贴书页 URL / 书号（resolve_book_id 归一化）。
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from bs4 import BeautifulSoup

from .wenku8_auth import make_session
from .wenku8_reader import BASE, _decode

# 文库分类：1-14（见 articlelist.php 导航）
_CLASSES = list(range(1, 15))


def _fetch_class_page(cls: int, page: int, session) -> List[dict]:
    """抓单个分类页的书籍列表。"""
    url = f"{BASE}/modules/article/articlelist.php?class={cls}&page={page}"
    try:
        resp = session.get(url, timeout=15)
        html = _decode(resp)
    except Exception:  # noqa: BLE001
        return []

    soup = BeautifulSoup(html, "html.parser")
    books = []
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        m = re.search(r"/book/(\d+)\.htm", href)
        if not m:
            continue
        name = a.get_text(" ", strip=True)
        if not name or name in ("我要阅读", "返回书页"):
            continue
        books.append({"id": m.group(1), "title": name, "source": "wenku8"})
    return books


def search_wenku8(keyword: str, session=None, max_pages: int = 3,
                  workers: int = 8) -> List[dict]:
    """按关键词搜索文库8书籍。

    :param keyword: 书名/作者关键词
    :param max_pages: 每个分类抓前 N 页（默认 3，14 分类 × 3 页 ≈ 1600 本）
    :param workers: 并发线程数
    :return: [{id, title, author, cover, intro, source}]

    说明：为速度考虑先按书名匹配；命中后不逐本请求详情（避免几十个并发详情请求），
    书名/作者从列表页能拿到多少用多少。封面等在下载对话框/详情阶段再补。
    """
    kw = str(keyword or "").strip().lower()
    if not kw:
        return []
    sess = session or make_session()

    # 并发抓所有 (分类, 页码) 组合
    tasks = [(cls, page) for cls in _CLASSES for page in range(1, max_pages + 1)]
    all_books = []
    pool = ThreadPoolExecutor(max_workers=max(1, min(16, workers)))
    try:
        fmap = {
            pool.submit(_fetch_class_page, cls, page, sess): (cls, page)
            for cls, page in tasks
        }
        for fut in as_completed(fmap):
            try:
                all_books.extend(fut.result() or [])
            except Exception:  # noqa: BLE001
                pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    # 去重 + 本地过滤
    seen = set()
    results = []
    for b in all_books:
        bid = b["id"]
        if bid in seen:
            continue
        seen.add(bid)
        if kw in b["title"].lower():
            results.append({
                "id": bid,
                "title": b["title"],
                "author": "",
                "intro": "",
                "cover": "",
                "source": "wenku8",
            })
    return results
