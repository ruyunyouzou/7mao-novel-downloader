# -*- coding: utf-8 -*-
"""文库8（轻小说文库）真实下载内核插件。

内核为本项目自研（src/gui/wenku8_core/），不依赖未开源的上游实现。
本文件是插件薄适配层：把核心的「书籍信息 / 目录 / 正文」包装成
本项目的 BookSource，再交给 qimao_core.build_txt 复用全部导出能力
（TXT / 分章 / EPUB、章节范围、文件名模板、题图尾图、字体颜色、对话着色）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.plugin.base import (
    Plugin, PluginMeta, DownloaderBase, DownloadResult, DownloadCancelled,
)
from gui.qimao_core import BookSource, build_txt


class Wenku8BookSource(BookSource):
    """文库8章节源：自研内核，逐章明文抓取（无字体混淆）。"""

    source_key = "wenku8"
    name = "文库8"

    def __init__(self) -> None:
        self._total = 0
        self._prefix = ""
        self._aid = ""
        self._app_mode = False

    def _session(self, session=None):
        """文库8必须带登录 Cookie；追更等调用方传入的是裸 Session，不可直接用。"""
        from gui.wenku8_core.wenku8_auth import make_session, get_cookie
        # 传入的 session 若已带文库8 Cookie 则复用，否则用自己的登录态 Session
        if session is not None and getattr(session, "cookies", None) is not None \
                and "PHPSESSID" in session.cookies.get_dict():
            return session
        return make_session(get_cookie())

    def fetch_detail(self, book_id: str, session) -> dict:
        from gui.wenku8_core.wenku8_reader import fetch_detail
        sess = self._session(session)
        d = fetch_detail(book_id, sess)
        return {
            "title": str(d.get("title") or ""),
            "author": str(d.get("author") or ""),
            "intro": str(d.get("intro") or ""),
            "cover": str(d.get("cover") or ""),
        }

    def fetch_catalog(self, book_id: str, session) -> list:
        from gui.wenku8_core.wenku8_reader import (
            resolve_book_id, fetch_catalog, fetch_book_meta,
        )
        sess = self._session(session)
        self._aid = resolve_book_id(book_id) or str(book_id or "").strip()
        raw = fetch_catalog(book_id, sess)
        # App API 目录自带 app_cid/vol，走 do=text 正文，无需网页 novel 前缀
        self._app_mode = any(str(ch.get("app_cid") or "").isdigit() for ch in raw)
        self._prefix = ""
        if not self._app_mode:
            # 仅网页目录通道可用时才需要前缀（兼容旧线路）
            meta = fetch_book_meta(book_id, sess)
            self._prefix = meta.get("novel_prefix", "")
        # 归一为「卷/集/章」层级：title 精简为纯章名，vol/sub 记录分组。
        from gui.hierarchy import normalize_catalog_safe
        raw = normalize_catalog_safe(raw)
        self._total = len(raw)
        return raw

    def chapter_text(self, ctx, chapter: dict, session, report) -> str:
        from gui.wenku8_core.wenku8_reader import (
            fetch_chapter_text, fetch_chapter_text_app,
        )
        sess = self._session(session)
        text = ""
        cid = str(chapter.get("app_cid") or "") if self._app_mode else ""
        if cid:
            text = fetch_chapter_text_app(self._aid, cid, sess)
        if not text:
            # App 正文失败时回退网页通道（若网页可用），不丢章
            url = chapter.get("url") or ""
            text = fetch_chapter_text(
                url or chapter["id"], sess,
                novel_prefix=self._prefix,
                book_id=str(chapter.get("bid", "")),
            )
        if self._total and report is not None:
            idx = int(chapter.get("index", 0)) + 1
            report(2 + min(38, int(idx / self._total * 38)))
        return text

    def prefetch_texts(self, ctx, catalog, session, on_progress=None):
        """并发预取全书正文（文库8逐章请求，20 章小书可直接并发）。"""
        import time as _time
        from concurrent.futures import ThreadPoolExecutor, as_completed
        try:
            from gui.datastore import DataStore
            workers = int(DataStore().settings_get("wenku8_concurrent_workers", 8) or 8)
        except Exception:  # noqa: BLE001
            workers = 8
        workers = max(1, min(16, workers))
        total = len(catalog)
        if total <= 1:
            return [self.chapter_text(ctx, ch, session, None) for ch in catalog]

        state = {"done": 0}

        def _grab(indices, pool):
            got = {}
            fmap = {
                pool.submit(self.chapter_text, ctx, catalog[i], session, None): i
                for i in indices
            }
            for fut in as_completed(fmap):
                idx = fmap[fut]
                try:
                    got[idx] = fut.result() or ""
                except Exception:  # noqa: BLE001
                    got[idx] = ""
                state["done"] += 1
                if on_progress is not None:
                    on_progress(min(state["done"], total), total)
            return got

        results = {}
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            results.update(_grab(range(total), pool))
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            pool.shutdown(wait=True)

        failed = [i for i in range(total) if not results.get(i)]
        if failed:
            pool2 = ThreadPoolExecutor(max_workers=max(2, workers // 2))
            _time.sleep(0.5)
            try:
                retried = _grab(failed, pool2)
            except BaseException:
                pool2.shutdown(wait=False, cancel_futures=True)
                raise
            else:
                pool2.shutdown(wait=True)
            for i, txt in retried.items():
                if txt:
                    results[i] = txt
        return [results.get(i, "") for i in range(total)]


class Wenku8Downloader(DownloaderBase):
    """文库8真实下载内核。"""

    name = "文库8真实内核（自研）"
    source_key = "wenku8"
    priority = 50

    def supports(self, book_info):
        return str((book_info or {}).get("source", "") or "") == "wenku8"

    def download(self, book_id, save_path, fmt="txt", progress_cb=None, **kwargs):
        title = kwargs.get("title", "") or ""
        from gui.datastore import DataStore
        store = DataStore()

        def opt(key, default=None):
            return kwargs.get(key) or store.settings_get(f"epub_{key}", default)

        chapter_font = opt("chapter_font", "builtin:mashan") or "builtin:mashan"
        volume_font = opt("volume_font", "builtin:zhimang") or "builtin:zhimang"
        chapter_color = opt("chapter_color", "") or ""
        volume_color = opt("volume_color", "") or ""
        colorize = kwargs.get("colorize_dialogue")
        if colorize is None:
            colorize = bool(store.settings_get("epub_colorize_dialogue", True))
        chapter_hero = kwargs.get("chapter_hero")
        if chapter_hero is None:
            chapter_hero = bool(store.settings_get("epub_chapter_hero", False))
        hero_mode = opt("hero_mode", "rotate") or "rotate"
        hero_fixed = opt("hero_fixed", "") or ""
        chapter_tail = kwargs.get("chapter_tail")
        if chapter_tail is None:
            chapter_tail = bool(store.settings_get("epub_chapter_tail", False))
        tail_mode = opt("tail_mode", "rotate") or "rotate"
        tail_fixed = opt("tail_fixed", "") or ""
        smart_art = kwargs.get("smart_art")
        if smart_art is None:
            smart_art = bool(store.settings_get("epub_smart_art", True))

        try:
            result = build_txt(
                book_id=str(book_id),
                save_path=save_path,
                fmt=fmt,
                progress_cb=progress_cb,
                title_hint=title,
                author_hint=kwargs.get("author", "") or "",
                start_chapter=int(kwargs.get("start_chapter") or 0),
                end_chapter=int(kwargs.get("end_chapter") or 0),
                filename_template=kwargs.get("filename_template", "") or "",
                colorize_dialogue=bool(colorize),
                chapter_font=str(chapter_font or ""),
                volume_font=str(volume_font or ""),
                chapter_color=str(chapter_color or ""),
                volume_color=str(volume_color or ""),
                chapter_hero=bool(chapter_hero),
                hero_mode=str(hero_mode),
                hero_fixed=str(hero_fixed),
                hero_library="genshin",
                chapter_tail=bool(chapter_tail),
                tail_mode=str(tail_mode),
                tail_fixed=str(tail_fixed),
                smart_art=bool(smart_art),
                source=Wenku8BookSource(),
            )
        except DownloadCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "login" in msg.lower() or "cookie" in msg.lower() or not str(Wenku8CookieProbe.ok()):
                msg = ("文库8下载失败：登录态无效，请在「文库8登录」中"
                       "用账号密码或官网登录重新登录（无需手动填写 Cookie）。原因：" + msg)
            return DownloadResult(
                book_id=str(book_id),
                title=title or f"book_{book_id}",
                success=False,
                message=f"文库8下载失败: {msg}",
            )

        fmt_label = {"txt": "TXT", "chapter": "分章TXT", "epub": "EPUB"}.get(fmt, fmt)
        start, end = result.get("start_chapter", 1), result.get("end_chapter", result["total"])
        scoped = (start > 1 or end < result.get("total_chapters", 0))
        scope_text = f"第{start}-{end}章" if scoped else "全本"
        return DownloadResult(
            book_id=str(book_id),
            title=result["title"],
            success=True,
            file_path=result["file_path"],
            message=f"已下载{scope_text} {result['chapters']}/{result['total']} 章（{fmt_label}·文库8）",
            extra={
                "chapters": result["chapters"],
                "total": result["total"],
                "format": fmt,
                "real": True,
                "start_chapter": start,
                "end_chapter": end,
                "source": "wenku8",
            },
        )


class Wenku8CookieProbe:
    """登录态探测缓存（仅用于失败提示，不影响下载主流程）。"""

    @staticmethod
    def ok() -> bool:
        try:
            from gui.wenku8_core.wenku8_auth import is_logged_in
            return is_logged_in()
        except Exception:  # noqa: BLE001
            return False


class Wenku8DownloaderPlugin(Plugin):
    meta = PluginMeta(
        name="文库8真实下载内核",
        version="1.0.0",
        author="WorkBuddy（自研内核）",
        description="文库8小说下载：目录/正文由自研内核提供，导出复用本项目全部功能",
        enabled=True,
    )

    def on_load(self, manager):
        self.log("文库8真实下载内核已就绪（自研内核）")

    def provide_downloader(self):
        return Wenku8Downloader()
