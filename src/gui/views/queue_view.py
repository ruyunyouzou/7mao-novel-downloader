# -*- coding: utf-8 -*-
"""
任务队列标签页：管理下载任务，支持并发、暂停/继续、取消、重试。

设计：
- 队列视图持有 DataStore 引用，任务描述持久化到 queue.json。
- 运行时任务对象（含 QThread）保存在内存字典 self._runners。
- 调度器按 max_concurrent 限制并发，从 waiting 状态的任务中自动启动。
- 主线程通过信号与视图交互，线程安全。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QAbstractItemView, QMenu, QMessageBox, QLabel,
    QSpinBox, QFrame, QStackedWidget,
)

from ..datastore import DataStore
from ..i18n import tr
from ..plugin.base import DownloadResult
from ..workers import DownloadWorker
from ..style import ACCENT, TEXT_DIM
from ..widgets import apply_nation_icon



# 状态值 -> i18n key（展示时经 tr() 翻译）
STATUS_TEXT = {
    "waiting": "queue.status.waiting",
    "downloading": "queue.status.downloading",
    "paused": "queue.status.paused",
    "done": "queue.status.done",
    "failed": "queue.status.failed",
    "cancelled": "queue.status.cancelled",
    "interrupted": "queue.status.interrupted",
}


class QueueView(QWidget):
    """任务队列视图。"""

    task_finished = Signal(dict)   # 任务完成时通知主窗口（传任务 dict）

    def __init__(self, store: DataStore, get_downloader, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.get_downloader = get_downloader  # 可调用，返回 DownloaderBase 或 None
        self._runners: Dict[str, DownloadWorker] = {}
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._schedule)
        self._timer.start(300)
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 8)
        layout.setSpacing(8)

        # ---- 顶部工具条：并发数卡片 + 操作按钮（带国徽 / 危险色）----
        bar = QHBoxLayout()
        bar.setSpacing(8)

        card = QFrame()
        card.setObjectName("queueConcurrentCard")
        cc = QHBoxLayout(card)
        cc.setContentsMargins(6, 2, 6, 2)
        cc.setSpacing(6)
        self.concurrent_label = QLabel(tr("queue.concurrent"))
        self.concurrent_label.setStyleSheet(f"color: {TEXT_DIM};")
        cc.addWidget(self.concurrent_label)
        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 8)
        self.concurrent_spin.setValue(3)
        cc.addWidget(self.concurrent_spin)
        bar.addWidget(card)

        bar.addStretch()

        # (属性名, 文案 key, 国徽, 是否危险按钮, 回调)
        btn_specs = [
            ("btn_pause", "queue.pause", "snezhnaya", False, self.pause_selected),
            ("btn_resume", "queue.resume", "mondstadt", False, self.resume_selected),
            ("btn_cancel", "queue.cancel", "inazuma", True, self.cancel_selected),
            ("btn_retry", "queue.retry", "liyue", False, self.retry_selected),
            ("btn_clear", "queue.clear_done", "sumeru", True, self.clear_finished),
        ]
        for attr, key, nation, danger, cb in btn_specs:
            b = QPushButton(tr(key))
            b.setObjectName("queueDanger" if danger else "queueAction")
            b.setProperty("danger", "true" if danger else "false")
            apply_nation_icon(b, nation, ACCENT, 15)
            b.clicked.connect(cb)
            setattr(self, attr, b)
            bar.addWidget(b)

        layout.addLayout(bar)

        # ---- 表格 / 空状态 堆叠 ----
        self.COLUMNS = [
            "queue.col.title", "queue.col.format", "queue.col.status",
            "queue.col.progress", "hist.col.time", "queue.col.elapsed",
            "queue.col.message",
        ]
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([tr(k) for k in self.COLUMNS])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, 7):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        # 空状态引导
        self.empty_widget = QWidget()
        ev = QVBoxLayout(self.empty_widget)
        ev.setAlignment(Qt.AlignCenter)
        ev.setSpacing(8)
        from ..widgets import nation_icon
        em = QLabel()
        em.setPixmap(nation_icon("mondstadt", ACCENT, 52).pixmap(52, 52))
        em.setAlignment(Qt.AlignCenter)
        ev.addWidget(em)
        self.empty_title = QLabel(tr("queue.empty_title"))
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_title.setStyleSheet("font-size:16px; font-weight:bold;")
        ev.addWidget(self.empty_title)
        self.empty_hint = QLabel(tr("queue.empty_hint"))
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setStyleSheet(f"color: {TEXT_DIM}; font-size:12px;")
        ev.addWidget(self.empty_hint)
        self.btn_go_search = QPushButton(tr("queue.go_search"))
        self.btn_go_search.setObjectName("queueGoSearch")
        self.btn_go_search.setStyleSheet(
            f"QPushButton#queueGoSearch {{ color: {ACCENT}; border: 1px solid {ACCENT}; "
            f"border-radius: 14px; padding: 5px 18px; background: transparent; }}")
        self.btn_go_search.clicked.connect(self._go_search)
        ev.addWidget(self.btn_go_search, 0, Qt.AlignCenter)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty_widget)
        layout.addWidget(self.stack, 1)

        self.count_label = QLabel()
        layout.addWidget(self.count_label)

    # ------------------------------------------------------------------ 调度

    def _schedule(self) -> None:
        """按并发数自动启动等待中的任务。"""
        running = sum(
            1 for t in self.store.queue_all()
            if t.get("status") == "downloading"
        )
        capacity = self.concurrent_spin.value() - running
        if capacity <= 0:
            return
        for task in self.store.queue_all():
            if capacity <= 0:
                break
            if task.get("status") != "waiting":
                continue
            self._start_task(task)
            capacity -= 1

    def _start_task(self, task: Dict[str, Any]) -> None:
        downloader = self.get_downloader()
        if downloader is None:
            self.store.queue_update(task["id"], {
                "status": "failed", "message": "没有可用的下载内核插件",
            })
            self.refresh()
            return

        self.store.queue_update(task["id"], {
            "status": "downloading", "progress": 0,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "start_ts": time.time(),
        })
        worker = DownloadWorker(
            downloader, task.get("book", {}), task.get("save_path", ""),
            task.get("format", "txt"), self,
            options=task.get("options", {}),
        )
        worker.progress.connect(lambda p: self._on_progress(task["id"], p))
        worker.finished.connect(lambda r: self._on_finished(task["id"], r))
        worker.failed.connect(lambda msg: self._on_failed(task["id"], msg))
        worker.cancelled.connect(lambda: self._on_cancelled(task["id"]))
        self._runners[task["id"]] = worker
        worker.start()
        self.refresh()

    def _cleanup_runner(self, task_id: str) -> None:
        self._runners.pop(task_id, None)

    def _on_progress(self, task_id: str, progress: int) -> None:
        self.store.queue_update(task_id, {"progress": progress})
        self.refresh()

    def _on_finished(self, task_id: str, result: DownloadResult) -> None:
        task = self.store.queue_find(task_id) or {}
        elapsed = time.time() - task.get("start_ts", time.time())
        self.store.queue_update(task_id, {
            "status": "done" if result.success else "failed",
            "progress": 100 if result.success else task.get("progress", 0),
            "message": result.message,
            "file_path": result.file_path or "",
            "elapsed": round(elapsed, 1),
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        self._cleanup_runner(task_id)
        self.refresh()
        self.task_finished.emit(self.store.queue_find(task_id) or task)

    def _on_failed(self, task_id: str, message: str) -> None:
        task = self.store.queue_find(task_id) or {}
        elapsed = time.time() - task.get("start_ts", time.time())
        self.store.queue_update(task_id, {
            "status": "failed", "message": message, "elapsed": round(elapsed, 1),
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        self._cleanup_runner(task_id)
        self.refresh()
        self.task_finished.emit(self.store.queue_find(task_id) or task)

    def _on_cancelled(self, task_id: str) -> None:
        self.store.queue_update(task_id, {
            "status": "cancelled", "message": "用户取消",
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        self._cleanup_runner(task_id)
        self.refresh()

    # ------------------------------------------------------------------ 操作

    def add_task(self, book_info: Dict[str, Any], save_path: str, fmt: str = "txt",
                 options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """入队一个下载任务。options 可含 start_chapter/end_chapter/filename_template。"""
        task = self.store.queue_add({
            "book": dict(book_info),
            "save_path": save_path,
            "format": fmt,
            "options": dict(options or {}),
        })
        self.refresh()
        return task

    def _current_task(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= self.table.rowCount():
            return None
        task_id = self.table.item(row, 0).data(Qt.UserRole)
        return self.store.queue_find(task_id)

    def pause_selected(self) -> None:
        task = self._current_task()
        if not task or task.get("status") != "downloading":
            QMessageBox.information(self, "提示", "请选中一个正在下载的任务")
            return
        runner = self._runners.get(task["id"])
        if runner:
            runner.pause()
        self.store.queue_update(task["id"], {"status": "paused"})
        self.refresh()

    def resume_selected(self) -> None:
        task = self._current_task()
        if not task or task.get("status") != "paused":
            QMessageBox.information(self, "提示", "请选中一个已暂停的任务")
            return
        runner = self._runners.get(task["id"])
        if runner:
            runner.resume()
            self.store.queue_update(task["id"], {"status": "downloading"})
        else:
            # 线程已不存在，重新排队
            self.store.queue_update(task["id"], {"status": "waiting"})
        self.refresh()

    def cancel_selected(self) -> None:
        task = self._current_task()
        if not task or task.get("status") not in ("downloading", "paused", "waiting"):
            QMessageBox.information(self, "提示", "请选中一个可取消的任务")
            return
        runner = self._runners.get(task["id"])
        if runner:
            runner.cancel()
        else:
            self.store.queue_update(task["id"], {"status": "cancelled"})
        self.refresh()

    def retry_selected(self) -> None:
        task = self._current_task()
        if not task or task.get("status") not in ("failed", "cancelled", "interrupted"):
            QMessageBox.information(self, "提示", "请选中一个失败/取消的任务")
            return
        self.store.queue_update(task["id"], {
            "status": "waiting", "progress": 0, "message": "",
        })
        self.refresh()

    def clear_finished(self) -> None:
        n = self.store.queue_clear_finished()
        self.refresh()
        self.count_label.setText(tr("queue.count_cleared", n=n))

    def _go_search(self) -> None:
        """空状态「去搜索」：切到搜索页。"""
        win = self.window()
        if win and hasattr(win, "tabs"):
            for i in range(win.tabs.count()):
                if "搜索" in win.tabs.tabText(i) or "search" in win.tabs.tabText(i).lower():
                    win.tabs.setCurrentIndex(i)
                    break

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        task = self._current_task()
        if task:
            status = task.get("status")
            if status == "downloading":
                a = menu.addAction(tr("queue.pause"))
                a.triggered.connect(self.pause_selected)
                a = menu.addAction(tr("queue.cancel"))
                a.triggered.connect(self.cancel_selected)
            elif status == "paused":
                a = menu.addAction(tr("queue.resume"))
                a.triggered.connect(self.resume_selected)
                a = menu.addAction(tr("queue.cancel"))
                a.triggered.connect(self.cancel_selected)
            elif status in ("failed", "cancelled", "interrupted"):
                a = menu.addAction(tr("queue.retry"))
                a.triggered.connect(self.retry_selected)
        a = menu.addAction(tr("queue.clear_done"))
        a.triggered.connect(self.clear_finished)
        menu.exec(self.table.mapToGlobal(pos))

    # ------------------------------------------------------------------ 刷新

    # 状态 → (圆点颜色, 文字色)；浅色主题下也能看清
    _STATUS_STYLE = {
        "done": ("#66bb6a", "#2e7d32"),
        "failed": ("#ef5350", "#c62828"),
        "downloading": ("#42a5f5", "#1565c0"),
        "cancelled": ("#9e9e9e", "#757575"),
        "paused": ("#ffb74d", "#e65100"),
        "waiting": ("#bdbdbd", "#777777"),
        "interrupted": ("#8d6e63", "#6d4c41"),
    }

    def refresh(self) -> None:
        tasks = self.store.queue_all()
        # 空状态切换
        self.stack.setCurrentIndex(0 if tasks else 1)
        self.table.setRowCount(len(tasks))
        for r, t in enumerate(tasks):
            book = t.get("book", {})
            status = t.get("status", "waiting")
            elapsed = t.get("elapsed", "")
            if status == "downloading" and t.get("start_ts"):
                elapsed = round(time.time() - t["start_ts"], 1)

            status_text = tr(STATUS_TEXT[status]) if status in STATUS_TEXT else status
            dot_color, text_color = self._STATUS_STYLE.get(status, ("#9e9e9e", "#777777"))
            values = [
                book.get("title", "未知"), t.get("format", "txt"),
                f"● {status_text}",  # 状态徽章：圆点 + 文字
                f"{t.get('progress', 0)}%",
                t.get("created_at", ""), str(elapsed), t.get("message", ""),
            ]
            for c, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setData(Qt.UserRole, t.get("id", ""))
                if c == 2:
                    item.setForeground(QColor(text_color))
                    item.setToolTip(status_text)
                self.table.setItem(r, c, item)

        active = sum(
            1 for t in tasks if t.get("status") in ("waiting", "downloading", "paused")
        )
        self.count_label.setText(tr(
            "queue.count", n=len(tasks), active=active,
            limit=self.concurrent_spin.value(),
        ))

    def retranslate(self) -> None:
        """语言切换后刷新文案。"""
        self.concurrent_label.setText(tr("queue.concurrent"))
        self.btn_pause.setText(tr("queue.pause"))
        self.btn_resume.setText(tr("queue.resume"))
        self.btn_cancel.setText(tr("queue.cancel"))
        self.btn_retry.setText(tr("queue.retry"))
        self.btn_clear.setText(tr("queue.clear_done"))
        self.btn_go_search.setText(tr("queue.go_search"))
        self.empty_title.setText(tr("queue.empty_title"))
        self.empty_hint.setText(tr("queue.empty_hint"))
        self.table.setHorizontalHeaderLabels([tr(k) for k in self.COLUMNS])
        self.refresh()
