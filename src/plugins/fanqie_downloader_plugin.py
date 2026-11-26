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
        self._harden_handler(self._handler)
        # 多源框架：默认只注册官网源（行为与单源时代一致）；
        # 配置 fanqie_custom_api 后可追加自建 API 源，自动测速/故障转移。
        self._source_manager = None  # 惰性构造，避免无谓开销

    def _manager(self):
        """惰性构造/刷新源管理器（设置变化后自动重建）。"""
        from gui.fanqie_core.fanqie_sources import build_manager
        if self._source_manager is None:
            self._source_manager = build_manager()
        return self._source_manager

    @staticmethod
    def _harden_handler(handler) -> None:
        """给上游 RequestHandler 实例打「限时」补丁，不改上游源码一行。

        上游 request_handler.py 被 __init__.py 明确标注「禁止手改，同步会覆盖」，
        但它有两处会拖死整本书的系统性缺陷：
          1) get_cookie() 在 cookie 文件缺失/损坏时循环 10 次 × 10 秒 = 最坏 100 秒；
          2) get_book_info()/extract_chapters() 的 session.get() 完全没有 timeout，
             网络挂起时可无限等待（正是用户「卡在 2%」的元凶）。
        这里在实例层打补丁：固定内存 cookie + 所有 GET 注入超时上限，
        既保留上游目录/详情解析逻辑，又杜绝任何单点无限等待。
        """
        import random as _random

        # 1) 固定 cookie：番茄只需一个 novel_web_id 即可正常抓目录/详情，
        #    实测固定值同样返回 243 章。省掉 get_cookie 的 10 次网络循环。
        handler.get_cookie = lambda: (
            f"novel_web_id={_random.randint(10**18, 10**19 - 1)}")

        # 2) 给 session.get 注入默认超时（requests.Session 无默认 timeout 属性）。
        #    上游调用若已显式传 timeout（如 down_text），尊重原值不再覆盖。
        _orig_get = handler.session.get
        _default_timeout = 10

        def _get_with_timeout(url, **kwargs):
            kwargs.setdefault("timeout", _default_timeout)
            return _orig_get(url, **kwargs)

        handler.session.get = _get_with_timeout

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
        # 优先用带卷层级的解析（番茄 chapterListWithVolume 显式给出 volume_name）。
        # 失败则回退到上游扁平目录，保证任何情况下都不会因层级解析导致目录为空。
        from gui.fanqie_core.fanqie_reader import fetch_catalog_with_volumes
        raw = fetch_catalog_with_volumes(str(book_id))

        if not raw:
            # 智能加固：番茄偶发对目录请求返回验证/拦截页，extract_chapters 解析不到
            # div.chapter-item 就返回空列表，导致「目录为空，无法下载」偶发失败。
            # 换 UA（重建 handler 的 session 头）+ 递增延时重试，最多 3 次。
            import time as _time
            for attempt in range(3):
                raw = self._handler.extract_chapters(str(book_id)) or []
                if raw:
                    break
                if attempt < 2:
                    try:
                        from gui.fanqie_core.fanqie_reader import _UA_POOL
                        uas = self._handler.config.get("user_agents")
                        if isinstance(uas, list) and _UA_POOL:
                            uas.insert(0, _UA_POOL[(attempt + 1) % len(_UA_POOL)])
                    except Exception:  # noqa: BLE001
                        pass
                    _time.sleep(0.8 * (attempt + 1))
        self._total = len(raw)
        catalog = []
        for i, ch in enumerate(raw):
            item = {
                "id": str(ch.get("id", "")),
                "title": str(ch.get("title", f"第{i + 1}章")),
                "index": int(ch.get("index", i)),
            }
            if ch.get("vol"):
                item["vol"] = str(ch.get("vol"))
            catalog.append(item)
        # 归一为「卷/集/章」：title 精简为纯章名，vol/sub 记录分组
        from gui.hierarchy import normalize_catalog_safe
        return normalize_catalog_safe(catalog)

    def chapter_text(self, ctx, chapter: dict, session, report) -> str:
        """逐章取正文；把 2%~40% 的进度段分摊给章节下载阶段。

        默认走番茄官网读者页 + 字体解密（上游第三方 API 域名已失效）；
        设置 fanqie_text_backend=upstream 可切回上游原实现。
        """
        backend = "official"
        try:
            from gui.datastore import DataStore
            backend = str(DataStore().settings_get("fanqie_text_backend", "official") or "official")
        except Exception:  # noqa: BLE001
            pass

        text = ""
        cid = str(chapter["id"])
        if backend == "upstream":
            try:
                text = self._handler.down_text(cid) or ""
            except Exception:  # noqa: BLE001
                text = ""
        if not text:
            # 经多源框架取正文：默认只有官网源（内部仍是 decode_chapter），
            # 行为与改动前完全一致；若配置了额外源，框架自动测速选最快、失败切换。
            # 官网源的 fetch_page 已有完整浏览器头 + 换 UA 重试 + 24s 硬限时。
            try:
                text = self._manager().fetch(cid)
            except Exception:  # noqa: BLE001
                text = ""

        if self._total and report is not None:
            idx = int(chapter.get("index", 0)) + 1
            report(2 + min(38, int(idx / self._total * 38)))
        return text

    def prefetch_texts(self, ctx, catalog, session, on_progress=None):
        """并发预取番茄全书正文（番茄无整本 ZIP 接口，只能逐章请求）。

        智能自适应并发：
        - 默认 12 线程高速抓取（可通过 fanqie_concurrent_workers 调整 1~16）；
        - 收集后识别失败章节（空串/异常），若失败数 > 0，自动降到半并发对失败章
          补抓重试（带退避），重试成功则回填——速度优先但保证成功率；
        - 工作线程仅调 chapter_text 做纯网络抓取，report 传 None 避免线程内上报；
        - 主线程用 as_completed 收集结果，on_progress 在主线程回调 → 安全触发进度/取消。
        """
        import time as _time
        from concurrent.futures import ThreadPoolExecutor, as_completed
        try:
            from gui.datastore import DataStore
            workers = int(DataStore().settings_get("fanqie_concurrent_workers", 12) or 12)
        except Exception:  # noqa: BLE001
            workers = 12
        workers = max(1, min(16, workers))
        total = len(catalog)
        if total <= 1:
            return [self.chapter_text(ctx, ch, session, None) for ch in catalog]

        # 累计完成数：保证两轮进度单调递增，不因补抓重新计数而倒退
        state = {"done": 0}

        def _grab(indices, pool):
            """对给定 index 列表并发抓取，返回 {idx: text}；on_progress 每章回调。"""
            got: dict = {}
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
                    # 补抓阶段最多报到 total，避免超过 40% 分段
                    on_progress(min(state["done"], total), total)
            return got

        # 第一轮：默认并发高速抓取。用显式 executor，取消时 cancel_futures=True
        # 不强等待在途任务，避免用户点取消后还要等上千个请求跑完（秒级响应）。
        results: dict = {}
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            results.update(_grab(range(total), pool))
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            pool.shutdown(wait=True)

        # 第二轮：自适应补抓失败章节（空串），降半并发 + 退避重试
        failed = [i for i in range(total) if not results.get(i)]
        if failed:
            retry_workers = max(2, workers // 2)
            _time.sleep(0.6)
            pool2 = ThreadPoolExecutor(max_workers=retry_workers)
            try:
                retried = _grab(failed, pool2)
            except BaseException:
                pool2.shutdown(wait=False, cancel_futures=True)
                raise
            else:
                pool2.shutdown(wait=True)
            for i, txt in retried.items():
                if txt:
                    results[i] = txt  # 重试成功回填

        return [results.get(i, "") for i in range(total)]


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
        off_cover_path = str(kwargs.get("official_cover_path", "") or "").strip()
        try:
            from gui.fanqie_core.meta_override import (
                set_override, remove_override, save_cover_file,
            )
            if kwargs.get("official_clear"):
                remove_override(bid)
                off_title, off_cover = "", ""
            elif off_title or off_cover or off_cover_path:
                # 本地封面：对话框选图时已落盘覆盖表，这里再按 options 传来的
                # 路径兜底保存一次，确保即使覆盖表被清也能恢复用户所选原图。
                cover_path = ""
                if off_cover_path and os.path.isfile(off_cover_path):
                    cover_path = save_cover_file(bid, off_cover_path) or ""
                set_override(bid, off_title, off_cover, cover_path=cover_path)
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
