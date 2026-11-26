# -*- coding: utf-8 -*-
"""
后台工作线程：搜索与下载在 QThread 中执行，避免阻塞 GUI 主线程。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QThread, Signal, QMutex, QMutexLocker

from .api import search_books, get_book_info, ApiError
from .plugin.base import DownloaderBase, DownloadResult, DownloadCancelled


def _search_by_source(keyword: str, tab: str = "0", page: int = 1,
                      source: str = "qimao") -> list:
    """按书源分发搜索：七猫关键词接口 / 番茄官方搜索接口 / 文库8分类过滤。"""
    if source == "fanqie":
        from gui.fanqie_core.fanqie_search import search_fanqie
        return search_fanqie(keyword, page=page)
    if source == "wenku8":
        from gui.wenku8_core.wenku8_search import search_wenku8
        return search_wenku8(keyword, page=page)
    return search_books(keyword, tab=tab, page=page)


class SearchWorker(QThread):
    """搜索线程。

    取消采用 Qt 协作式 ``requestInterruption()``，绝不使用 ``terminate()``
    （后者强杀线程会丢失信号、偶发 "QThread: Destroyed while running"）。
    被取消的请求仍会自然跑完网络超时，但结束时丢弃结果、不发信号。
    """

    finished = Signal(list)   # List[Dict]
    failed = Signal(str)
    # 线程体真正结束时发出（无论成功/失败/取消），供 UI 安全回收对象。
    # 注意：QThread 内建的 finished 被本类同名信号遮蔽，故另起名字。
    finished_run = Signal()

    def __init__(self, keyword: str, parent=None, tab: str = "0",
                 page: int = 1, tag: str = "", source: str = "qimao") -> None:
        super().__init__(parent)
        self.keyword = keyword
        self.tab = tab
        self.page = page
        self.tag = tag  # 请求来源：search=用户搜索 / recommend=推荐（翻页会原样复用）
        self.source = source or "qimao"  # 书源：qimao / fanqie

    def run(self) -> None:
        try:
            results = _search_by_source(self.keyword, tab=self.tab,
                                        page=self.page, source=self.source)
            # 已在等待期间被取消（用户又发了新搜索）：丢弃旧结果
            if self.isInterruptionRequested():
                return
            self.finished.emit(results)
        except ApiError as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.failed.emit(f"搜索异常: {exc}")
        finally:
            # 线程体已走完，此时回收对象才安全（不会 "Destroyed while running"）
            self.finished_run.emit()


class DetailWorker(QThread):
    """详情加载线程（接口 401/超时时不阻塞界面）。"""

    finished = Signal(dict)
    failed = Signal(str)
    finished_run = Signal()  # 线程体结束后发出，供 UI 安全回收

    def __init__(self, book_id: str, fallback: Dict[str, Any], parent=None,
                 source: str = "qimao") -> None:
        super().__init__(parent)
        self.book_id = book_id
        self.fallback = fallback
        self.source = source or "qimao"

    def run(self) -> None:
        try:
            if self.source == "fanqie":
                # 番茄搜索结果已带解码后的完整信息，直接复用，避免七猫接口误查与 PUA 残留
                f = self.fallback or {}
                info = {
                    "id": str(self.book_id),
                    "title": f.get("title") or f"书 {self.book_id}",
                    "author": f.get("author") or "",
                    "words_num": f.get("words_num") or "未知",
                    "tags": f.get("tags") or "",
                    "img_url": f.get("cover") or "",
                    "intro": f.get("intro") or "",
                    "degraded": False,
                    "source": "fanqie",
                }
            elif self.source == "wenku8":
                # 文库8用自研内核实时抓详情，得到完整书名/作者/简介/封面。
                from gui.wenku8_core.wenku8_reader import fetch_detail
                d = fetch_detail(self.book_id)
                f = self.fallback or {}
                info = {
                    "id": str(self.book_id),
                    "title": d.get("title") or f.get("title") or f"书 {self.book_id}",
                    "author": d.get("author") or f.get("author") or "",
                    "words_num": f.get("words_num") or "未知",
                    "tags": f.get("tags") or "",
                    "img_url": d.get("cover") or f.get("cover") or "",
                    "intro": d.get("intro") or f.get("intro") or "",
                    "degraded": False,
                    "source": "wenku8",
                }
            else:
                info = get_book_info(self.book_id, fallback=self.fallback)
            if not self.isInterruptionRequested():
                self.finished.emit(info)
        except ApiError as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.failed.emit(f"详情异常: {exc}")
        finally:
            self.finished_run.emit()


class DownloadWorker(QThread):
    """下载线程，通过插件提供的下载内核执行；支持暂停/继续/取消。"""

    progress = Signal(int)        # 0-100
    finished = Signal(object)     # DownloadResult
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        downloader: DownloaderBase,
        book_info: Dict[str, Any],
        save_path: str,
        fmt: str = "txt",
        parent=None,
        options: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(parent)
        self.downloader = downloader
        self.book_info = book_info
        self.save_path = save_path
        self.fmt = fmt
        self.options: Dict[str, Any] = options or {}
        self._mutex = QMutex()
        self._paused = False
        self._cancelled = False
        self._resume_cond = None

    def pause(self) -> None:
        with QMutexLocker(self._mutex):
            self._paused = True

    def resume(self) -> None:
        with QMutexLocker(self._mutex):
            self._paused = False
            if self._resume_cond:
                self._resume_cond.wakeAll()

    def cancel(self) -> None:
        with QMutexLocker(self._mutex):
            self._cancelled = True
            self._paused = False
            if self._resume_cond:
                self._resume_cond.wakeAll()

    def is_cancelled(self) -> bool:
        with QMutexLocker(self._mutex):
            return self._cancelled

    def _check_pause(self) -> bool:
        """若处于暂停则阻塞等待，若取消则返回 True。"""
        from PySide6.QtCore import QWaitCondition
        while True:
            with QMutexLocker(self._mutex):
                if self._cancelled:
                    return True
                if not self._paused:
                    return False
                if self._resume_cond is None:
                    self._resume_cond = QWaitCondition()
                self._resume_cond.wait(self._mutex, 200)

    def run(self) -> None:
        try:
            # 下载前检查一次取消/暂停
            if self._check_pause():
                self.cancelled.emit()
                return

            def progress_cb(p):
                # 进度回调中处理暂停与取消
                if self._check_pause():
                    raise DownloadCancelled()
                self.progress.emit(int(p))

            # 下载选项（章节范围 / 文件名模板等）随请求透传给下载内核
            extra = dict(self.options)
            retry = int(extra.pop("_retry", 0) or 0)  # 失败自动重试次数（不传给内核）
            extra.setdefault("title", self.book_info.get("title", ""))
            extra.setdefault("author", self.book_info.get("author", ""))

            result = None
            for attempt in range(retry + 1):
                try:
                    result = self.downloader.download(
                        book_id=self.book_info.get("id", ""),
                        save_path=self.save_path,
                        fmt=self.fmt,
                        progress_cb=progress_cb,
                        **extra,
                    )
                except DownloadCancelled:
                    # 取消必须立即冒泡，不能被重试逻辑吞掉
                    raise
                except Exception:  # noqa: BLE001
                    if attempt >= retry:
                        raise
                    result = None
                if result is not None and getattr(result, "success", False):
                    break
                # 失败且还有重试次数：短暂退避后重试
                if attempt < retry:
                    import time
                    time.sleep(min(2 * (attempt + 1), 5))
                    progress_cb(0)
            if result is None:
                result = DownloadResult(
                    book_id=self.book_info.get("id", ""),
                    title=self.book_info.get("title", ""),
                    success=False,
                    message="下载内核未返回结果",
                )
            self.finished.emit(result)
        except DownloadCancelled:
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"下载异常: {exc}")


class ProtocolCheckWorker(QThread):
    """下载内核协议健康自检（后台执行，避免阻塞界面）。

    依次验证三路内核真正依赖的接口：
      1) 七猫搜索  qimao_core.search_books
      2) 七猫目录  qimao_core.get_catalog（顺带验证签名/请求头链路）
      3) 番茄目录  FanqieBookSource.fetch_catalog
      4) 文库8目录 Wenku8BookSource.fetch_catalog（带登录 Cookie）

    progress = Signal(str)              每一步的状态文字
    finished = Signal(dict)             最终结果 {ok, search, catalog, fanqie, wenku8, detail, message}
    """
    progress = Signal(str)
    finished = Signal(dict)

    # 固定自检样本（不依赖用户输入、不写任何数据）
    PROBE_KEYWORD = "斗破苍穹"
    PROBE_FANQIE_ID = "7546406625741917209"  # 公开可访问的番茄书，只取目录不写盘
    PROBE_WENKU8_ID = "2580"  # 文库8《关于邻家的天使大人…》，公开可访问，只取目录不写盘

    def run(self) -> None:
        from .qimao_core import search_books, book_source_for

        result = {"ok": False, "search": False, "catalog": False,
                  "fanqie": False, "wenku8": False, "detail": "", "message": ""}

        # 1) 搜索接口（结果复用给目录测试）
        self.progress.emit("正在检测搜索接口…")
        books = []
        try:
            books = search_books(self.PROBE_KEYWORD)
            if books:
                result["search"] = True
                result["detail"] += f"搜索正常（返回 {len(books)} 条）"
            else:
                result["detail"] += "搜索接口可访问但返回空结果"
        except Exception as exc:  # noqa: BLE001
            result["detail"] += f"搜索失败：{exc}"

        # 2) 目录接口（用搜索到的第一本书；搜索失败则退回固定 ID）
        self.progress.emit("正在检测目录接口…")
        book_id = str(books[0].get("id", "")) if books else ""
        book_id = book_id or "1784900"  # 斗破苍穹常见 ID 兜底
        try:
            chapters = book_source_for("qimao").fetch_catalog(book_id, None)
            if chapters:
                result["catalog"] = True
                result["detail"] += f"；七猫目录正常（{len(chapters)} 章）"
            else:
                result["detail"] += "；七猫目录可访问但返回空章节"
        except Exception as exc:  # noqa: BLE001
            result["detail"] += f"；七猫目录失败：{exc}"

        # 3) 番茄目录接口（走上游原样实现，只读目录不下载正文）
        self.progress.emit("正在检测番茄目录接口…")
        try:
            fanqie = book_source_for("fanqie")
            fc = fanqie.fetch_catalog(self.PROBE_FANQIE_ID, None)
            if fc:
                result["fanqie"] = True
                result["detail"] += f"；番茄目录正常（{len(fc)} 章）"
            else:
                result["detail"] += "；番茄目录返回空章节"
        except Exception as exc:  # noqa: BLE001
            result["detail"] += f"；番茄目录失败：{exc}"

        # 4) 文库8目录接口（带登录 Cookie，只读目录不下载正文）
        self.progress.emit("正在检测文库8目录接口…")
        try:
            wenku8 = book_source_for("wenku8")
            wc = wenku8.fetch_catalog(self.PROBE_WENKU8_ID, None)
            if wc:
                result["wenku8"] = True
                result["detail"] += f"；文库8目录正常（{len(wc)} 章）"
            else:
                result["detail"] += "；文库8目录返回空章节（可能是登录态失效或站点风控）"
        except Exception as exc:  # noqa: BLE001
            result["detail"] += f"；文库8目录失败：{exc}"

        result["ok"] = (result["search"] and result["catalog"]
                        and result["fanqie"] and result["wenku8"])
        if result["ok"]:
            result["message"] = "七猫 / 番茄 / 文库8 内核均正常"
        else:
            bad = []
            if not (result["search"] and result["catalog"]):
                bad.append("七猫")
            if not result["fanqie"]:
                bad.append("番茄")
            if not result["wenku8"]:
                bad.append("文库8")
            result["message"] = "、".join(bad) + "内核异常 · 详情见提示"
        self.finished.emit(result)


# 兼容旧引用：取消异常真源已移到 plugin.base，便于各内核 re-raise
_CancelledError = DownloadCancelled

