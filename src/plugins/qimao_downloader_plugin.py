# -*- coding: utf-8 -*-
"""
七猫真实下载内核插件。

基于用户提供的 v4.0.4 源码还原的完整下载链路：
    目录 -> 缓存ZIP -> AES解密每章 -> 写出 txt / 分章 / epub

本插件提供 provide_downloader 挂载点，接管 GUI 的下载行为。
示例插件的模拟内核保留用于演示，但本插件优先级更高（加载顺序靠前）。
"""

import os
import sys

# 保证能 import 到 gui 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.plugin.base import (
    Plugin, PluginMeta, DownloaderBase, DownloadResult, DownloadCancelled,
)
from gui.qimao_core import build_txt


class QimaoRealDownloader(DownloaderBase):
    """真实七猫下载内核。"""

    name = "七猫真实内核 v4.0.4"
    priority = 100  # 高于示例插件的演示内核
    source_key = "qimao"

    def supports(self, book_info):
        # 未标注来源的书默认仍走七猫；显式标成番茄的书不接管。
        return str((book_info or {}).get("source", "") or "qimao") != "fanqie"

    def download(self, book_id, save_path, fmt="txt", progress_cb=None, **kwargs):
        title = kwargs.get("title", "") or ""
        # EPUB 排版为全局设置：选一次，之后每本书下载都自动带上。
        # 章节字体与分卷字体各自独立；下载对话框可临时覆盖（kwargs）。
        from gui.datastore import DataStore
        store = DataStore()
        chapter_font = kwargs.get("chapter_font") or store.settings_get(
            "epub_chapter_font", store.settings_get("epub_font_scheme", "builtin:mashan")) or "builtin:mashan"
        volume_font = kwargs.get("volume_font") or store.settings_get(
            "epub_volume_font", "builtin:zhimang") or "builtin:zhimang"
        # 标题高亮色：下载对话框本次选择优先，其次全局设置；都为空则核心回退默认色。
        chapter_color = kwargs.get("chapter_color") or store.settings_get(
            "epub_chapter_color", "") or ""
        volume_color = kwargs.get("volume_color") or store.settings_get(
            "epub_volume_color", "") or ""
        colorize = kwargs.get("colorize_dialogue")
        if colorize is None:
            colorize = bool(store.settings_get("epub_colorize_dialogue", True))
        # 章首题图（原神祈愿立绘）：全局设置，可由下载对话框本次覆盖
        chapter_hero = kwargs.get("chapter_hero")
        if chapter_hero is None:
            chapter_hero = bool(store.settings_get("epub_chapter_hero", False))
        hero_mode = kwargs.get("hero_mode") or store.settings_get("epub_hero_mode", "rotate") or "rotate"
        hero_fixed = kwargs.get("hero_fixed") or store.settings_get("epub_hero_fixed", "") or ""
        hero_library = "genshin"  # 题图固定用原神祈愿立绘库
        # 章末尾图（原神地理志风景图）：全局设置，可由下载对话框本次覆盖
        chapter_tail = kwargs.get("chapter_tail")
        if chapter_tail is None:
            chapter_tail = bool(store.settings_get("epub_chapter_tail", False))
        tail_mode = kwargs.get("tail_mode") or store.settings_get("epub_tail_mode", "rotate") or "rotate"
        tail_fixed = kwargs.get("tail_fixed") or store.settings_get("epub_tail_fixed", "") or ""
        # 智能选题预设（题图每本随机30张含7神明 / 尾图地理志按地区均衡抽10张）
        smart_art = kwargs.get("smart_art")
        if smart_art is None:
            smart_art = bool(store.settings_get("epub_smart_art", True))
        try:
            result = build_txt(
                book_id=book_id,
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
                hero_library=str(hero_library),
                chapter_tail=bool(chapter_tail),
                tail_mode=str(tail_mode),
                tail_fixed=str(tail_fixed),
                smart_art=bool(smart_art),
            )
        except DownloadCancelled:
            raise  # 取消必须冒泡，否则队列会误判为失败
        except Exception as exc:  # noqa: BLE001
            return DownloadResult(
                book_id=book_id,
                title=title or f"book_{book_id}",
                success=False,
                message=f"下载失败: {exc}",
            )

        fmt_label = {"txt": "TXT", "chapter": "分章TXT", "epub": "EPUB"}.get(fmt, fmt)
        start, end = result.get("start_chapter", 1), result.get("end_chapter", result["total"])
        scoped = (start > 1 or end < result.get("total_chapters", 0))
        scope_text = f"第{start}-{end}章" if scoped else "全本"
        return DownloadResult(
            book_id=book_id,
            title=result["title"],
            success=True,
            file_path=result["file_path"],
            message=f"已下载{scope_text} {result['chapters']}/{result['total']} 章（{fmt_label}）",
            extra={
                "chapters": result["chapters"],
                "total": result["total"],
                "format": fmt,
                "real": True,
                "start_chapter": start,
                "end_chapter": end,
            },
        )


class QimaoDownloaderPlugin(Plugin):
    meta = PluginMeta(
        name="七猫真实下载内核",
        version="1.0.0",
        author="WorkBuddy（基于星隅 v4.0.4 协议）",
        description="提供完整的七猫小说下载能力：缓存ZIP+AES解密，支持 txt/分章/epub",
        enabled=True,
    )

    def on_load(self, manager):
        self.log("七猫真实下载内核已就绪")

    def provide_downloader(self):
        return QimaoRealDownloader()
