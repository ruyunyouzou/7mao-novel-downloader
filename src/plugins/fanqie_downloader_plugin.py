# -*- coding: utf-8 -*-
"""番茄小说真实下载内核插件。

内置核心内核：xiaocse/Fanqie-novel-Downloader（MIT，commit 见 UPSTREAM_INFO.md）。
番茄内核已提升为 GUI 层一等核心（src/gui/fanqie_core/），与 qimao_core.py 平级；
本文件只保留插件薄适配层：把核心的「书籍信息 / 目录 / 正文」包装成本项目的 BookSource，
再交给 qimao_core.build_txt 复用全部导出能力（TXT / 分章 / EPUB、
章节范围、文件名模板、题图尾图、字体颜色、对话着色）。

上游更新时运行：
    python3 src/gui/fanqie_core/fanqie_upstream/sync_upstream.py
通常不需要改动本文件。
"""

import os
import sys

# 保证能 import 到 gui 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.plugin.base import (
    Plugin, PluginMeta, DownloaderBase, DownloadResult, DownloadCancelled,
)
from gui.qimao_core import BookSource, build_txt
from gui.fanqie_core.fanqie_upstream import RequestHandler


class FanqieBookSource(BookSource):
    """番茄章节源：薄包装上游 RequestHandler，不改上游一行代码。"""

    source_key = "fanqie"
    name = "番茄小说"

    def __init__(self) -> None:
        self._handler = RequestHandler()
        self._total = 0

    def fetch_detail(self, book_id: str, session) -> dict:
        bid = str(book_id or "").strip()
        # 覆盖表优先：下载对话框里用户粘贴的 App 官方书名/封面网址，
        # 覆盖 Web 端滞后的旧书名/旧封面。只影响落盘产物，不改搜索展示。
        override = {}
        try:
            from gui.fanqie_core.meta_override import get_override
            override = get_override(bid)
        except Exception:  # noqa: BLE001
            override = {}

        name, author, intro, cover = "", "", "", ""
        # 封面供 EPUB 内嵌；惰性导入避免启动期加载字体映射大表，失败不影响下载。
        has_local_cover = bool(override.get("cover_path"))
        if not (override.get("title") and (override.get("cover_url") or has_local_cover)):
            # 没有完整覆盖时才请求 Web 详情，避免无谓网络开销
            try:
                name, author, intro = self._handler.get_book_info(bid)
            except Exception:  # noqa: BLE001
                name, author, intro = None, None, None
            try:
                from gui.fanqie_core.fanqie_reader import fetch_cover
                cover = fetch_cover(bid)
            except Exception:  # noqa: BLE001
                cover = ""

        # 用覆盖值逐字段覆盖（空字段仍回退 Web）
        # 封面优先级：本地选中的官方原图 > 封面网址 > Web 抓取值
        if override.get("title"):
            name = override["title"]
        if has_local_cover:
            cover = override["cover_path"]
        elif override.get("cover_url"):
            cover = override["cover_url"]
        return {
            "title": str(name or ""),
            "author": str(author or ""),
            "intro": str(intro or ""),
            "cover": str(cover or ""),
        }

    def fetch_catalog(self, book_id: str, session) -> list:
        raw = self._handler.extract_chapters(str(book_id)) or []
        self._total = len(raw)
        catalog = []
        for i, ch in enumerate(raw):
            catalog.append({
                "id": str(ch.get("id", "")),
                "title": str(ch.get("title", f"第{i + 1}章")),
                "index": int(ch.get("index", i)),
            })
        return catalog

    def chapter_text(self, ctx, chapter: dict, session, report) -> str:
        """逐章取正文；把 2%~40% 的进度段分摊给章节下载阶段。

        默认走番茄官网读者页 + 字体解密（上游第三方 API 域名已失效）；
        设置 fanqie_text_backend=upstream 可切回上游原实现。
        """
        from gui.fanqie_core.fanqie_reader import decode_chapter

        backend = "official"
        try:
            from gui.datastore import DataStore
            backend = str(DataStore().settings_get("fanqie_text_backend", "official") or "official")
        except Exception:  # noqa: BLE001
            pass

        text = ""
        if backend == "upstream":
            try:
                text = self._handler.down_text(str(chapter["id"])) or ""
            except Exception:  # noqa: BLE001
                text = ""
        if not text:
            text = decode_chapter(str(chapter["id"]))

        if self._total and report is not None:
            idx = int(chapter.get("index", 0)) + 1
            report(2 + min(38, int(idx / self._total * 38)))
        return text


class FanqieDownloader(DownloaderBase):
    """番茄真实下载内核。"""

    name = "番茄真实内核（xiaocse）"
    source_key = "fanqie"
    # 默认内核仍是七猫（priority=100）；番茄通过书源识别 / 显式选择生效。
    priority = 50

    def supports(self, book_info):
        return str((book_info or {}).get("source", "") or "") == "fanqie"

    def download(self, book_id, save_path, fmt="txt", progress_cb=None, **kwargs):
        title = kwargs.get("title", "") or ""
        bid = str(book_id or "").strip()

        # 下载对话框即时修正：把用户粘贴的官方书名/封面网址写入覆盖表并持久化，
        # 之后同书再下载自动沿用，无需重复填写。
        off_title = str(kwargs.get("official_title", "") or "").strip()
        off_cover = str(kwargs.get("official_cover_url", "") or "").strip()
        # 本地封面文件路径：对话框选图时已写入覆盖表，这里只补写标题（避免覆盖已存的 cover_path）
        try:
            from gui.fanqie_core.meta_override import set_override, remove_override
            if kwargs.get("official_clear"):
                remove_override(bid)
                off_title, off_cover = "", ""
            elif off_title or off_cover:
                # 不传 cover_path，set_override 空字段不覆盖已有本地封面
                set_override(bid, off_title, off_cover)
        except Exception:  # noqa: BLE001
            pass
        # 官方书名覆盖搜索页的旧别名（文件名 / EPUB 标题 / 题图种子均据此）
        try:
            from gui.fanqie_core.meta_override import get_override
            override = get_override(bid)
            if override.get("title"):
                title = override["title"]
        except Exception:  # noqa: BLE001
            pass

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
                source=FanqieBookSource(),
            )
        except DownloadCancelled:
            raise  # 取消必须冒泡，否则队列会误判为失败
        except Exception as exc:  # noqa: BLE001
            return DownloadResult(
                book_id=str(book_id),
                title=title or f"book_{book_id}",
                success=False,
                message=f"番茄下载失败: {exc}",
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
            message=f"已下载{scope_text} {result['chapters']}/{result['total']} 章（{fmt_label}·番茄）",
            extra={
                "chapters": result["chapters"],
                "total": result["total"],
                "format": fmt,
                "real": True,
                "start_chapter": start,
                "end_chapter": end,
                "source": "fanqie",
            },
        )


class FanqieDownloaderPlugin(Plugin):
    meta = PluginMeta(
        name="番茄真实下载内核",
        version="1.0.0",
        author="WorkBuddy（基于 xiaocse/Fanqie-novel-Downloader, MIT）",
        description="番茄小说下载：目录/正文由上游内核提供，导出复用本项目全部功能",
        enabled=True,
    )

    def on_load(self, manager):
        self.log("番茄真实下载内核已就绪（上游隔离可同步）")

    def provide_downloader(self):
        return FanqieDownloader()
