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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
from typing import List
from urllib.parse import quote

from bs4 import BeautifulSoup

from .wenku8_auth import get_base_url, make_session
from .wenku8_match import matches as _matches, relevance as _relevance
from .wenku8_reader import _decode

# 文库分类：1-14（见 articlelist.php 导航）
_CLASSES = list(range(1, 15))

# 超时与预算（秒）：站点正常响应一般 1-3 秒，15 秒过长会让失败通道
# 累加到半分钟。缩短单请求超时 + 熔断 + 总预算，结果覆盖逻辑完全不变。
_REQUEST_TIMEOUT = 6          # 单个搜索/分类请求超时
_SEARCH_BUDGET = 20           # 分类兜底整体时间预算，到点返回已抓取结果
_BLOCK_MARKERS = ("<h1>Welcome</h1>", "Attention Required", "cf-error", "Just a moment")


def _is_blocked(html: str) -> bool:
    """是否为占位页/风控页：这类页面说明整条线路不可用，应立即熔断。"""
    text = html or ""
    return any(m.lower() in text.lower() for m in _BLOCK_MARKERS)

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
            # Jieqi CMS 老模板（搜索结果/榜单常见）：articleinfo.php?id=2580
            m = re.search(r"articleinfo\.php\?(?:id|aid)=(\d+)", href, re.I)
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


def _search_via_so_post(keyword: str, session, base: str, page: int = 1) -> List[dict]:
    """按官网搜索表单的原始方式请求：POST /so.php。

    官网首页表单是 ``method="post" action="/so.php"``，字段为
    ``searchtype / searchkey / charset=gbk / Submit``。此前只 GET
    ``/modules/article/search.php``，在部分线路上会落到 Welcome 占位页；
    POST 表单是参考 App 与浏览器实际走的通道，必须优先尝试。
    """
    raw = str(keyword or "").strip()
    if not raw:
        return []
    for enc in ("gbk", "utf-8"):
        try:
            kw = quote(raw.encode(enc))
            submit = quote("轻小说搜索".encode(enc))
        except Exception:  # noqa: BLE001
            continue
        body = (
            f"searchtype=articlename&searchkey={kw}"
            f"&charset=gbk&Submit={submit}"
        ).encode("ascii")
        if page > 1:
            body += f"&page={page}".encode("ascii")
        try:
            resp = session.post(
                base + "/so.php",
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=_REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            html = _decode(resp)
        except Exception:  # noqa: BLE001
            continue
        if _is_blocked(html):
            # 风控/占位页：整条线路不可用，换编码也不会变好，立即停止
            break
        books = _extract_books(html, base)
        hits = [b for b in books if _matches(keyword, b["title"])]
        if hits:
            for b in hits:
                b["_rel"] = _relevance(keyword, b["title"])
            hits.sort(key=lambda b: -b["_rel"])
            return hits
    return []


def _search_on_site(keyword: str, session, base: str, page: int = 1) -> List[dict]:
    """尝试站内搜索接口，返回命中的书籍（已按相关性排序）。

    对每种关键词编码（GBK 优先 / UTF-8 兜底）× 每个候选接口逐个尝试；
    任一接口返回含匹配书籍的页面即视为成功；全部失败返回空列表由调用方兜底。
    """
    kw_variants = _encode_keyword_variants(keyword)
    if not kw_variants:
        return []
    # 0) 官网真实表单 POST /so.php（浏览器/参考 App 通道），优先于 GET 候选
    post_hits = _search_via_so_post(keyword, session, base, page=page)
    if post_hits:
        return post_hits
    for kw in kw_variants:
        blocked = False
        for tpl in _SEARCH_ENDPOINTS:
            url = base + tpl.format(kw=kw)
            # 分页参数：仅当 >1 时附加（不同站点分页参数不一，优先 page）
            if page > 1:
                url += f"&page={page}"
            try:
                resp = session.get(url, timeout=_REQUEST_TIMEOUT)
                html = _decode(resp)
            except Exception:  # noqa: BLE001
                continue
            # 部分镜像（如 .cc）的搜索路径只返回 Welcome 占位页，
            # 命中风控/占位页说明整条线路不可用：熔断所有剩余候选与编码，
            # 不再逐个等 15 秒超时（这是先前半分钟延迟的主因）。
            if _is_blocked(html):
                blocked = True
                break
            books = _extract_books(html, base)
            if not books:
                continue
            hits = [b for b in books if _matches(keyword, b["title"])]
            if hits:
                for b in hits:
                    b["_rel"] = _relevance(keyword, b["title"])
                hits.sort(key=lambda b: -b["_rel"])
                return hits
        if blocked:
            break  # 整条线路被拦，换编码无意义
    return []


def _fetch_class_page(cls: int, page: int, session, base: str) -> List[dict]:
    """抓单个分类页的书籍列表。session 应为该工作线程独占的实例。"""
    url = f"{base}/modules/article/articlelist.php?class={cls}&page={page}"
    try:
        resp = session.get(url, timeout=_REQUEST_TIMEOUT)
        html = _decode(resp)
    except Exception:  # noqa: BLE001
        return []
    if _is_blocked(html):
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

    # 并发度提高（抓取页数不变，覆盖不受影响）：112 页从 14 批降到 ~5 批。
    pool = ThreadPoolExecutor(max_workers=max(8, min(24, workers * 3)))
    deadline = time.monotonic() + _SEARCH_BUDGET
    try:
        fmap = {
            pool.submit(_grab, cls, p): (cls, p)
            for cls, p in tasks
        }
        done_count = 0
        try:
            iterator = as_completed(fmap, timeout=_SEARCH_BUDGET)
            for fut in iterator:
                try:
                    books = fut.result() or []
                except Exception:  # noqa: BLE001
                    books = []
                all_books.extend(books)
                done_count += 1
                # 熔断：前 16 页全部无书（正常站点不可能），说明整站不可用，
                # 取消剩余任务立即返回，避免空等 112 页 = 半分钟。
                if done_count >= 16 and not any(b["title"] for b in all_books):
                    for pending in fmap:
                        pending.cancel()
                    break
                if time.monotonic() > deadline:
                    for pending in fmap:
                        pending.cancel()
                    break
        except FutureTimeout:
            # 总预算到点：返回已抓到的结果，不阻塞界面
            for pending in fmap:
                pending.cancel()
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


def _normalize_result(book: dict) -> dict:
    """把内部书目条目转为与 UI 一致的同构结果。

    搜索页表格与详情区会读取 words_num/tags/score 等字段；文库8内核本身
    不提供这些信息，缺失时补空值，避免消费方硬取键 KeyError。
    """
    out = {k: v for k, v in (book or {}).items() if k != "_rel"}
    out.setdefault("id", "")
    out.setdefault("title", "")
    out.setdefault("author", "")
    out.setdefault("intro", "")
    out.setdefault("cover", "")
    out.setdefault("words_num", "")
    out.setdefault("tags", "")
    out.setdefault("score", "")
    out.setdefault("source", "wenku8")
    return out


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
    page = max(1, int(page or 1))

    # 0) 官方 App API（app.wenku8.com/android.php）已于 2026 年关停，返回
    #    nginx Welcome，且会让每次搜索白等超时（"搜索延迟半分钟"的根因）。
    #    默认不再优先走它；仅当显式设置中继 wenku8_api_relay 时才尝试
    #    （中继若也失效，_post 内部已有快速故障转移，不会长时间阻塞）。
    app_hits: List[dict] = []
    try:
        from gui.datastore import DataStore
        has_relay = bool(str(DataStore().settings_get("wenku8_api_relay", "") or "").strip())
    except Exception:  # noqa: BLE001
        has_relay = False
    if has_relay:
        try:
            from .wenku8_app_api import search_via_app_api
            app_hits = search_via_app_api(kw, page=page)
        except Exception:  # noqa: BLE001
            app_hits = []
        if app_hits:
            return [_normalize_result(b) for b in app_hits]

    sess = session or make_session()
    base = get_base_url()

    # 1) 站内搜索优先（网页表单，可能被 Cloudflare 拦）
    hits = _search_on_site(kw, sess, base, page=page)
    if hits:
        return [_normalize_result(b) for b in hits]

    # 1.1) 全站索引检索（通用方案）：本地索引保存了**所有**书籍的完整标题
    #     （含括号别名），任何书的别名都能命中，不再对具体书名做硬编码。
    #     索引缺失/过期时后台自动构建，并立即触发一次构建以便首轮也能覆盖。
    if page == 1:
        try:
            from .wenku8_index import search_index, ensure_index_async

            aid_hits = search_index(kw)
            if aid_hits:
                from .wenku8_reader import fetch_detail

                books: List[dict] = []
                for aid in aid_hits[:20]:
                    try:
                        d = fetch_detail(aid, sess)
                    except Exception:  # noqa: BLE001
                        d = {}
                    if d and d.get("title"):
                        books.append({
                            "id": aid,
                            "title": d.get("title", ""),
                            "author": d.get("author", ""),
                            "intro": d.get("intro", ""),
                            "cover": d.get("cover", ""),
                            "words_num": d.get("words_num", ""),
                            "tags": d.get("tags", ""),
                            "source": "wenku8",
                            "_rel": _relevance(kw, d.get("title", "")),
                        })
                if books:
                    books.sort(key=lambda b: -b["_rel"])
                    return [_normalize_result(b) for b in books]
            # 无论是否命中，索引过期都异步重建（失败不影响本次搜索）。
            try:
                from gui.datastore import DataStore

                cookie = str(
                    DataStore().settings_get("wenku8_cookie", "") or ""
                ).strip()
                ensure_index_async(base=base, cookie=cookie)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass

    # 1.5) 首页/榜单补充：.cc 站内 search.php 已改为 Google CSE（返回框架页无结果），
    #     但首页推荐与榜单页仍含书籍链接。带登录 Cookie 抓这些页面做本地别名
    #     匹配，可覆盖「紫罗兰」→《薇尔莉特(紫罗兰永恒花园)》这类别名漏搜，且很快。
    quick_books: List[dict] = []
    for path in ("/", "/modules/article/toplist.php?sort=allvisit",
                 "/modules/article/toplist.php?sort=lastupdate",
                 "/modules/article/articlelist.php?class=1"):
        try:
            resp = sess.get(base + path, timeout=_REQUEST_TIMEOUT)
            html = _decode(resp)
            if _is_blocked(html):
                continue
            quick_books.extend(_extract_books(html, base))
        except Exception:  # noqa: BLE001
            continue
    if quick_books:
        seen: set = set()
        qhits: List[dict] = []
        for b in quick_books:
            if b["id"] in seen:
                continue
            seen.add(b["id"])
            if _matches(kw, b["title"]):
                b["_rel"] = _relevance(kw, b["title"])
                qhits.append(b)
        if qhits:
            qhits.sort(key=lambda b: -b["_rel"])
            return [_normalize_result(b) for b in qhits]

    # 2) 分类页兜底
    results = _search_categories(kw, sess, base, max_pages=max_pages,
                                 workers=workers, page_offset=page - 1)
    return [_normalize_result(b) for b in results]
