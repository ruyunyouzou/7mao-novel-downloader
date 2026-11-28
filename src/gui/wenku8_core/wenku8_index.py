# -*- coding: utf-8 -*-
"""文库8 全站书籍索引（本地缓存）。

通用方案，不针对任何具体书名做硬编码：

* 抓取全站 14 个分类的全部列表页，提取每本书的 ``aid`` 与**完整标题**
  （含括号别名，如 ``薇尔莉特·伊芙加登(紫罗兰永恒花园)``）；
* 索引持久化到 ``~/.qimao_gui/wenku8_index.json``，带 TTL 自动刷新；
* 搜索时在本地索引里做归一化模糊匹配（复用 wenku8_match），
  因此任何书的**主标题、括号别名、作者别名**都能被搜到，
  不再需要维护 ``紫罗兰 -> 2353`` 这类一次性特判。

索引构建在后台线程进行，不阻塞界面；构建期间搜索仍走站内接口 +
分类页兜底，索引就绪后自动启用全站覆盖。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from .wenku8_auth import get_base_url, make_session
from .wenku8_match import matches as _matches, relevance as _relevance
from .wenku8_reader import _decode

#: 索引文件与 DataStore 同目录，避免另建用户目录。
_INDEX_FILE = os.path.join(
    os.path.expanduser("~"), ".qimao_gui", "wenku8_index.json"
)
#: 随包分发的全站目录（来自 mojimoon/wenku8，MIT）。自抓 5000+ 页不现实，
#: 也无法保证不被风控，因此公开目录作为全站检索的第一选择。
_BUNDLED_CATALOG = os.path.join(os.path.dirname(__file__), "data", "wenku_catalog.json")
_catalog_cache: Optional[Dict[str, dict]] = None
#: 索引有效期：7 天。过期后后台自动重建。
_TTL_SECONDS = 7 * 24 * 3600
#: 挑刺 #24：随包目录存在时，增量索引的有效期更长（30 天）——目录是基线快照，
#: 增量只用于补新书，无需高频重抓，兼顾「新书能进来」与「不制造风控流量」。
_TTL_WITH_CATALOG = 30 * 24 * 3600
#: 文库分类 1-14。
_CLASSES = tuple(range(1, 15))
#: 单分类抓取页数上限（防御异常站点无限分页）。
_DEFAULT_PAGES_PER_CLASS = 400
#: 后台构建并发度。
_DEFAULT_WORKERS = 8

_lock = threading.RLock()
_building = False


# --------------------------------------------------------------------------- 路径 / 读写


def index_path() -> str:
    """返回索引文件路径（便于测试与诊断）。"""
    return _INDEX_FILE


def catalog_path() -> str:
    """返回随包分发的全站目录 JSON 路径（便于测试与诊断）。"""
    return _BUNDLED_CATALOG


def load_catalog() -> Dict[str, dict]:
    """加载全站目录 ``{aid: 完整元数据}``；失败返回空字典，不抛异常。

    条目来自 mojimoon/wenku8 的公开目录，包含 aid/title/author/tags/
    description/cover_url，正好覆盖「按别名搜全站」所需的全部字段。
    """
    global _catalog_cache
    # 目录随包分发、运行期不热更；缓存一旦加载即固定。特意尊重外部
    # （含单测）注入的 _catalog_cache——它是公开的可覆盖契约。
    if _catalog_cache is not None:
        return _catalog_cache
    books: Dict[str, dict] = {}
    try:
        with open(_BUNDLED_CATALOG, "r", encoding="utf-8") as f:
            data = json.load(f)
        for page in (data.get("pages") or {}).values():
            if not isinstance(page, list):
                continue
            for item in page:
                aid = str((item or {}).get("aid", "") or "").strip()
                title = str((item or {}).get("title", "") or "").strip()
                if not aid or not title:
                    continue
                books[aid] = {
                    "id": aid,
                    "title": title,
                    "author": str(item.get("author", "") or ""),
                    "tags": str(item.get("tags", "") or ""),
                    "intro": str(item.get("description", "") or ""),
                    "cover": str(item.get("cover_url", "") or "").replace("http://", "https://", 1),
                    "length": str(item.get("length", "") or ""),
                    "status": str(item.get("status", "") or ""),
                    "publisher": str(item.get("publisher", "") or ""),
                    "source": "wenku8",
                }
    except Exception:  # noqa: BLE001
        books = {}
    _catalog_cache = books
    return books


def search_index_book(aid: str) -> Optional[dict]:
    """取单本目录元数据；目录里没有时返回 None。"""
    return load_catalog().get(str(aid or "").strip())


def load_index() -> Dict[str, str]:
    """加载 ``{aid: 完整标题}``；文件不存在或损坏时返回空字典。"""
    try:
        with open(_INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        books = data.get("books") if isinstance(data, dict) else None
        if isinstance(books, dict):
            return {str(k): str(v) for k, v in books.items() if v}
    except Exception:  # noqa: BLE001
        pass
    return {}


def save_index(books: Dict[str, str]) -> None:
    """原子写入索引，避免后台构建中途被读拿到半截文件。"""
    os.makedirs(os.path.dirname(_INDEX_FILE), exist_ok=True)
    tmp = _INDEX_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            {"built_at": time.time(), "books": books},
            f,
            ensure_ascii=False,
        )
    os.replace(tmp, _INDEX_FILE)


def _index_stale(ttl: int) -> bool:
    """按给定 TTL 判断索引是否缺失/过期（挑刺 #24：支持目录模式长 TTL）。"""
    try:
        with open(_INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        built_at = float(data.get("built_at", 0))
        return (time.time() - built_at) > ttl
    except Exception:  # noqa: BLE001
        return True


def is_stale() -> bool:
    """索引不存在或超过默认 TTL 时返回 True（保留给测试/外部调用）。"""
    return _index_stale(_TTL_SECONDS)


# --------------------------------------------------------------------------- 解析


def extract_books(html: str, base: str = "") -> Dict[str, str]:
    """从列表页 HTML 提取 ``{aid: 标题}``。

    标题取链接文本，Jieqi 模板里就是完整书名（含括号别名）。
    兼容新版 ``/book/{aid}.htm``、``/novel/{n}/{aid}/`` 与老版
    ``articleinfo.php?id={aid}`` 三种链接形式。
    """
    soup = BeautifulSoup(html or "", "html.parser")
    out: Dict[str, str] = {}
    for a in soup.select("a[href]"):
        href = str(a.get("href", "")).strip()
        aid = ""
        for pattern in (
            r"/book/(\d+)\.htm",
            r"/novel/\d+/(\d+)/",
            r"articleinfo\.php\?(?:id|aid)=(\d+)",
        ):
            m = re.search(pattern, href, re.I)
            if m:
                aid = m.group(1)
                break
        if not aid:
            continue
        title = a.get_text(" ", strip=True)
        if not title or title in ("我要阅读", "返回书页", "点击阅读"):
            continue
        out[aid] = title
    return out


# --------------------------------------------------------------------------- 构建


def build_index(
    session=None,
    base: Optional[str] = None,
    pages_per_class: int = _DEFAULT_PAGES_PER_CLASS,
    workers: int = _DEFAULT_WORKERS,
) -> Dict[str, str]:
    """抓取全部分类页构建索引并落盘。

    逐分类顺序翻页，连续两页无书即认为该分类到底；分类之间并发。
    返回构建得到的 ``{aid: 标题}``。
    """
    base = base or get_base_url()
    sess = session or make_session()
    books: Dict[str, str] = {}
    import threading

    tls = threading.local()
    cookie_str = ""
    try:
        cookie_str = "; ".join(
            f"{k}={v}" for k, v in sess.cookies.get_dict().items()
        )
    except Exception:  # noqa: BLE001
        pass

    def _grab(cls: int, page: int) -> Dict[str, str]:
        # httpcloak Session 不是线程安全对象：每个抓取线程自建一份会话。
        s = getattr(tls, "session", None)
        if s is None:
            if session is not None and not hasattr(session, "owner_thread"):
                # 单测注入的假会话仍按原样复用，便于断言请求次数
                s = session
            else:
                s = make_session(cookie_str, base=base)
            tls.session = s
        try:
            resp = s.get(
                f"{base}/modules/article/articlelist.php?class={cls}&page={page}",
                timeout=6,
            )
            html = _decode(resp)
        except Exception:  # noqa: BLE001
            return {}
        # 拦截页/关站页/登录跳转：绝不能当「空列表」处理，否则会被误判为
        # 分类到底，进而把空索引落盘并盖新时间戳，7 天内都不再重建。
        low = (html or "").lower()
        if ("<h1>welcome</h1>" in low or "attention required" in low
                or "just a moment" in low or "cf-error" in low
                or "本站正式关闭" in html or "正式关闭" in html
                or "login.php" in getattr(resp, "url", "").lower()):
            return {}
        return extract_books(html, base)

    def _build_class(cls: int) -> Dict[str, str]:
        result: Dict[str, str] = {}
        empty = 0
        for page in range(1, max(1, int(pages_per_class)) + 1):
            got = _grab(cls, page)
            if not got:
                empty += 1
                if empty >= 2:
                    break
            else:
                empty = 0
                result.update(got)
        return result

    pool = ThreadPoolExecutor(max_workers=max(1, min(16, int(workers))))
    try:
        futures = [pool.submit(_build_class, cls) for cls in _CLASSES]
        for fut in as_completed(futures):
            try:
                books.update(fut.result() or {})
            except Exception:  # noqa: BLE001
                pass
    finally:
        pool.shutdown(wait=True)
    # 全站被拦/关站：不落盘空索引，也不盖新时间戳。保留可能存在的旧索引，
    # 并让它保持「过期」状态，待线路恢复后后台能立即重建。
    if not books:
        return {}
    save_index(books)
    return books


def ensure_index_async(
    session=None,
    base: Optional[str] = None,
    cookie: str = "",
) -> None:
    """若索引缺失或过期，在后台线程增量构建（重复调用安全）。

    挑刺 #24：过去这里 `if load_catalog(): return` 使 build_index/is_stale/
    load_index 整条自抓链成为死代码——随包目录是静态快照，新书永远进不来。
    现在改为：

    * 目录存在时，按更长 TTL（30 天）低频补抓**最新页**（每类 40 页），
      结果合并进本机索引，新书可被搜到，又不至于 14×400 页制造风控流量；
    * 目录缺失时，退回原 7 天 TTL 的全量抓取。

    ``Session`` 非线程安全，后台线程始终自建 session。
    """
    has_catalog = bool(load_catalog())
    ttl = _TTL_WITH_CATALOG if has_catalog else _TTL_SECONDS
    if not _index_stale(ttl):
        return
    global _building
    with _lock:
        if _building or not _index_stale(ttl):
            return
        _building = True

    def _run() -> None:
        try:
            s = make_session(cookie or "", base=base or get_base_url())
            # 目录存在时只补抓最新页，降低请求量与风控概率。
            pages = 40 if has_catalog else _DEFAULT_PAGES_PER_CLASS
            build_index(session=s, base=base, pages_per_class=pages)
        except Exception:  # noqa: BLE001
            pass
        finally:
            global _building
            with _lock:
                _building = False

    threading.Thread(target=_run, name="wenku8-index-builder", daemon=True).start()


# --------------------------------------------------------------------------- 检索


def search_index(keyword: str, limit: int = 40, offset: int = 0) -> List[str]:
    """在本地目录/索引里检索关键词，返回按相关性排序的 aid 列表。

    优先搜随包全站目录（含括号别名、作者、简介），再补充用户本机自抓索引。
    两者都为空时返回空列表，由调用方回退到其他通道。

    挑刺 #18：新增 offset 支持 UI 翻页，使第 2 页及以后仍来自全站目录，
    而非突然切换到分类页数据源。
    """
    from .wenku8_match import normalize as _norm

    # 单字查询在站内搜索阶段处理；索引兜底限制 >=2 字，避免返回海量弱命中。
    if len(_norm(keyword)) < 2:
        return []
    scored: List[tuple] = []
    seen = set()
    for aid, book in load_catalog().items():
        title = str(book.get("title", "") or "")
        if not title:
            continue
        author = str(book.get("author", "") or "")
        intro = str(book.get("intro", "") or "")
        if _matches(keyword, title, author=author, summary=intro):
            scored.append((_relevance(keyword, title, author=author, summary=intro), aid))
            seen.add(aid)
    for aid, title in load_index().items():
        if aid in seen:
            continue
        if _matches(keyword, title):
            scored.append((_relevance(keyword, title), aid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    start = max(0, int(offset))
    end = start + max(1, int(limit))
    return [aid for _, aid in scored[start:end]]
