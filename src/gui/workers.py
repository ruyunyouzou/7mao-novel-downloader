# -*- coding: utf-8 -*-
"""
后台工作线程：搜索与下载在 QThread 中执行，避免阻塞 GUI 主线程。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QThread, Signal, QMutex, QMutexLocker

from .api import search_books, get_book_info, ApiError
from .plugin.base import DownloaderBase, DownloadResult


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
                 page: int = 1, tag: str = "") -> None:
        super().__init__(parent)
        self.keyword = keyword
        self.tab = tab
        self.page = page
        self.tag = tag  # 标记请求来源：""=用户搜索 / "recommend"=推荐换一批

    def run(self) -> None:
        try:
            results = search_books(self.keyword, tab=self.tab, page=self.page)
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

    def __init__(self, book_id: str, fallback: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.book_id = book_id
        self.fallback = fallback

    def run(self) -> None:
        try:
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
                    raise _CancelledError()
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
        except _CancelledError:
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"下载异常: {exc}")


class ProtocolCheckWorker(QThread):
    """下载内核协议健康自检（后台执行，避免阻塞界面）。

    依次验证下载真正依赖的两级接口：
      1) 搜索  qimao_core.search_books
      2) 目录  qimao_core.get_catalog（顺带验证签名/请求头链路）

    progress = Signal(str)              每一步的状态文字
    finished = Signal(dict)             最终结果 {ok, search, catalog, detail, message}
    """
    progress = Signal(str)
    finished = Signal(dict)

    # 固定自检样本（不依赖用户输入、不写任何数据）
    PROBE_KEYWORD = "斗破苍穹"

    def run(self) -> None:
        from .qimao_core import search_books, get_catalog

        result = {"ok": False, "search": False, "catalog": False,
                  "detail": "", "message": ""}

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
            chapters = get_catalog(book_id)
            if chapters:
                result["catalog"] = True
                result["detail"] += f"；目录正常（{len(chapters)} 章）"
            else:
                result["detail"] += "；目录可访问但返回空章节"
        except Exception as exc:  # noqa: BLE001
            result["detail"] += f"；目录失败：{exc}"

        result["ok"] = result["search"] and result["catalog"]
        if result["ok"]:
            result["message"] = "下载内核正常 · 搜索与目录接口均可用"
        elif result["search"] != result["catalog"]:
            result["message"] = "部分异常 · 下载可能受影响，建议关注上游协议变更"
        else:
            result["message"] = "下载内核可能已失效 · 上游协议或接口可能已变更"
        self.finished.emit(result)


class _CancelledError(Exception):
    """内部信号：用户取消下载。"""

