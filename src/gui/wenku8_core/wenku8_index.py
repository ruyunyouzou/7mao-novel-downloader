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
#: 索引有效期：7 天。过期后后台自动重建。
_TTL_SECONDS = 7 * 24 * 3600
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


def is_stale() -> bool:
    """索引不存在或超过 TTL 时返回 True。"""
    try:
        with open(_INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        built_at = float(data.get("built_at", 0))
        return (time.time() - built_at) > _TTL_SECONDS
    except Exception:  # noqa: BLE001
        return True


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

    def _grab(cls: int, page: int) -> Dict[str, str]:
        try:
            resp = sess.get(
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
    """若索引缺失或过期，在后台线程构建（重复调用安全）。

    ``requests.Session`` 非线程安全，后台线程始终自建 session；
    如需登录态，把 Cookie 字符串传进来由后台 session 使用。
    """
    global _building
    with _lock:
        if _building or not is_stale():
            return
        _building = True

    def _run() -> None:
        try:
            s = make_session(cookie or "", base=base or get_base_url())
            build_index(session=s, base=base)
        except Exception:  # noqa: BLE001
            pass
        finally:
            global _building
            with _lock:
                _building = False

    threading.Thread(target=_run, name="wenku8-index-builder", daemon=True).start()


# --------------------------------------------------------------------------- 检索


def search_index(keyword: str, limit: int = 40) -> List[str]:
    """在本地索引里检索关键词，返回按相关性排序的 aid 列表。

    索引为空时返回空列表，由调用方回退到其他通道。
    """
    from .wenku8_match import normalize as _norm

    # 单字查询在站内搜索阶段处理；索引兜底限制 >=2 字，避免返回海量弱命中。
    if len(_norm(keyword)) < 2:
        return []
    books = load_index()
    if not books:
        return []
    scored: List[tuple] = []
    for aid, title in books.items():
        if _matches(keyword, title):
            scored.append((_relevance(keyword, title), aid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [aid for _, aid in scored[: max(1, int(limit))]]
