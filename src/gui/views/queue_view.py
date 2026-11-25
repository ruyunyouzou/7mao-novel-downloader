# -*- coding: utf-8 -*-
"""
任务队列标签页：管理下载任务，支持并发、暂停/继续、取消、重试。

界面（B2 任务卡片流）：
- 每张任务一张卡片，卡片左侧是「书名首字」封面位（不加国徽），
  其余每一项信息（格式 / 状态 / 进度 / 章节 / 耗时 / 时间）前都带一个国徽。
- 信息行整体右对齐，进度条与信息行之间有一条细分隔线。

设计：
- 队列视图持有 DataStore 引用，任务描述持久化到 queue.json。
- 运行时任务对象（含 QThread）保存在内存字典 self._runners。
- 调度器按 max_concurrent 限制并发，从 waiting 状态的任务中自动启动。
- 主线程通过信号与视图交互，线程安全。
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, Optional

from PySide6.QtCore import (
    Qt, Signal, QTimer, QRectF, QPropertyAnimation, QEasingCurve,
    Property,
)
from PySide6.QtGui import QColor, QPainter, QLinearGradient
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QFrame, QLabel,
    QPushButton, QSpinBox, QStackedWidget, QMenu, QMessageBox,
)

from ..datastore import DataStore
from ..i18n import tr
from ..plugin.base import DownloadResult
from ..workers import DownloadWorker
from ..style import ACCENT, TEXT_DIM, BG_INPUT
from .. import render_quality
from ..widgets import apply_nation_icon, nation_icon, nation_pixmap, goto_search_page
from ._card_base import _CardBase


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


def _chapter_text(message: str) -> str:
    """从下载消息里摘出章节进度，摘不到则显示简短消息。"""
    text = str(message or "")
    m = re.search(r"(\d+\s*/\s*\d+\s*章|\d+\s*章)", text)
    if m:
        return m.group(1)
    return text[:18] if text else "—"


class GlowProgressBar(QWidget):
    """圆滑高亮进度条：胶囊轨道 + 主题色渐变填充 + 柔光外发光 +
    顶部高光细带 + 斜向流光循环扫过（仅进行中时跑）+ 数值平滑过渡。

    对外保留 setValue / value 接口，可原位替换 QProgressBar。
    颜色通过 apply_colors() 从当前主题色重取，深浅主题自适应。
    """

    def __init__(self, accent: str = ACCENT, dark: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._display = 0.0          # 动画显示值
        self._accent = QColor(accent)
        self._dark = dark
        self._flowing = False        # 是否进行中（决定是否跑流光）
        self._phase = 0.0            # 流光相位 0..1

        self.setFixedHeight(11)
        self.setMinimumWidth(40)

        # 数值平滑过渡动画
        self._anim = QPropertyAnimation(self, b"displayValue", self)
        self._anim.setDuration(360)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        # 流光相位动画：0→1 循环，仅 downloading 时启动
        self._flow = QPropertyAnimation(self, b"flowPhase", self)
        self._flow.setDuration(1400)
        self._flow.setStartValue(0.0)
        self._flow.setEndValue(1.0)
        self._flow.setLoopCount(-1)

    # ---- Qt 属性（供动画驱动）----
    def getDisplayValue(self) -> float:
        return self._display

    def setDisplayValue(self, v: float) -> None:
        self._display = float(v)
        self.update()

    displayValue = Property(float, getDisplayValue, setDisplayValue)

    def getFlowPhase(self) -> float:
        return self._phase

    def setFlowPhase(self, v: float) -> None:
        self._phase = float(v)
        self.update()

    flowPhase = Property(float, getFlowPhase, setFlowPhase)

    # ---- 对外接口 ----
    def setMaximum(self, _maximum: int) -> None:
        pass  # 固定按 0..100，保留接口兼容

    def maximum(self) -> int:
        return 100

    def setValue(self, value: int) -> None:
        v = max(0.0, min(100.0, float(value)))
        if abs(v - self._value) < 0.01:
            return
        self._value = v
        self._anim.stop()
        self._anim.setStartValue(self._display)
        self._anim.setEndValue(v)
        self._anim.start()

    def value(self) -> int:
        return int(round(self._value))

    def setFlowing(self, flowing: bool) -> None:
        """进行中时启动流光动画；完成/暂停停止以省资源。"""
        if flowing == self._flowing:
            return
        self._flowing = flowing
        if flowing:
            if self._flow.state() != QPropertyAnimation.Running:
                self._flow.start()
        else:
            self._flow.stop()
            self.update()

    def apply_colors(self, accent: str, dark: bool) -> None:
        self._accent = QColor(accent)
        self._dark = dark
        self.update()

    # ---- 绘制 ----
    def paintEvent(self, _ev) -> None:
        w, h = self.width(), self.height()
        if w < 8 or h < 5:
            return
        p = QPainter(self)
        render_quality.apply_painter(p)
        p.setPen(Qt.NoPen)
        r = QRectF(0.5, 0.5, w - 1, h - 1)
        rad = h / 2.0
        base = self._accent

        # 轨道：深色用略亮的灰紫、浅色用浅灰，两端全圆角
        if self._dark:
            track_top = QColor(BG_INPUT).darker(112)
            track_bot = QColor(BG_INPUT).lighter(120)
        else:
            track_top = QColor("#eceaf2")
            track_bot = QColor("#f6f5f9")
        track = QLinearGradient(0, 0, 0, h)
        track.setColorAt(0.0, track_top)
        track.setColorAt(1.0, track_bot)
        p.setBrush(track)
        p.drawRoundedRect(r, rad, rad)

        ratio = self._display / 100.0
        if ratio <= 0.001:
            return
        fw = (w - 1) * ratio
        fr = QRectF(0.5, 0.5, fw, h - 1)

        # 柔光外发光：半透明主题色铺底、略大一圈
        glow = QColor(base)
        glow.setAlpha(80)
        p.setBrush(glow)
        p.drawRoundedRect(fr.adjusted(-1.5, -1.5, 1.5, 1.5), rad + 1, rad + 1)

        # 填充：横向三段渐变，胶囊圆头
        fill = QLinearGradient(0, 0, max(fw, 1), 0)
        fill.setColorAt(0.0, QColor(base).darker(116))
        fill.setColorAt(0.5, base)
        fill.setColorAt(1.0, QColor(base).lighter(118))
        p.setBrush(fill)
        p.drawRoundedRect(fr, rad, rad)

        # 顶部高光细带：沿填充上半部一条半透明白色带
        hi = QLinearGradient(0, 0, 0, h * 0.6)
        hi.setColorAt(0.0, QColor(255, 255, 255, 120))
        hi.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(hi)
        p.drawRoundedRect(
            QRectF(fr.x() + 1, fr.y() + 1, max(fw - 2, 1), h * 0.55),
            rad, rad)

        # 流光扫过：斜向白色高光块，随相位在填充区内移动（仅进行中）
        if self._flowing and fw > 12:
            span = max(28.0, fw * 0.35)
            x = -span + (fw + span) * self._phase
            beam = QLinearGradient(x, 0, x + span, 0)
            beam.setColorAt(0.0, QColor(255, 255, 255, 0))
            beam.setColorAt(0.5, QColor(255, 255, 255, 85))
            beam.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(beam)
            p.drawRoundedRect(
                QRectF(max(fr.x(), 0.5), fr.y() + 1,
                       min(x + span, fw) - max(fr.x(), 0.5), h - 2),
                rad, rad)


class TaskCard(_CardBase):
    """B2 任务卡片。封面位不用国徽，其余每项信息前带一个国徽。"""

    CARD_OBJECT = "queueTaskCard"
    COVER_OBJECT = "queueCover"
    TITLE_OBJECT = "queueCardTitle"
    LINE_OBJECT = "queueCardLine"
    META_OBJECT = "queueMetaText"
    PILL_OBJECT = "queueStatusPill"
    STATUS_OBJECT = "queueStatusText"

    def __init__(self, view, parent=None) -> None:
        super().__init__(view, "", parent)

    def _build_fields(self) -> None:
        # 标签行：格式
        self.fmt = self._chip(self.tags)
        self._add_tag(self.fmt)
        self._finish_tags()

        # 卡片中部：进度条（前面带国徽）+ 百分比
        prog = QHBoxLayout()
        prog.setSpacing(7)
        self.prog_icon = QLabel()
        self.prog_icon.setFixedSize(self.ICON_SIZE, self.ICON_SIZE)
        accent, dark = self.view._bar_colors()
        self.prog_bar = GlowProgressBar(accent, dark)
        self.pct = QLabel()
        self.pct.setObjectName("queuePct")
        self.pct.setFixedWidth(40)
        self.pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        prog.addWidget(self.prog_icon)
        prog.addWidget(self.prog_bar, 1)
        prog.addWidget(self.pct)
        self.body.addLayout(prog)

        # 右对齐元信息行：章节 / 耗时 / 时间
        self.chapter = self._chip(self.meta)
        self.elapsed = self._chip(self.meta)
        self.time = self._chip(self.meta)

    def update_data(self, task: Dict[str, Any], selected: bool = False) -> None:
        self.item_id = task.get("id", "")
        status = task.get("status", "waiting")
        book = task.get("book", {}) or {}
        title = book.get("title", "未知")
        self._set_title(title)
        # 队列任务的书可能还没下载完，file_path 为空；此时优先用 book 里的 cover URL
        self._set_cover(title, str(task.get("file_path", "") or ""),
                        str(book.get("cover", "") or ""))

        self.fmt.set_icon(self.nations["fmt"])
        self.fmt.set_text(str(task.get("format", "txt")).upper())

        status_text = tr(STATUS_TEXT[status]) if status in STATUS_TEXT else status
        dot_color, text_color = _STATUS_STYLE.get(status, ("#9e9e9e", "#777777"))
        self._apply_status(status_text, dot_color, text_color, self.nations["status"])

        progress = int(task.get("progress", 0) or 0)
        self.prog_icon.setPixmap(
            nation_pixmap(self.nations["progress"], ACCENT, self.ICON_SIZE))
        accent, dark = self.view._bar_colors()
        self.prog_bar.apply_colors(accent, dark)
        self.prog_bar.setValue(progress)
        # 仅下载中跑流光动效；完成/失败/暂停静止，省资源
        self.prog_bar.setFlowing(status == "downloading")
        self.pct.setText(f"{progress}%")

        elapsed = task.get("elapsed", "")
        if status == "downloading" and task.get("start_ts"):
            elapsed = round(time.time() - task["start_ts"], 1)

        self.chapter.set_icon(self.nations["chapter"])
        self.chapter.set_text(_chapter_text(task.get("message", "")),
                              str(task.get("message", "")))

        self.elapsed.set_icon(self.nations["elapsed"])
        self.elapsed.set_text(f"{tr('queue.col.elapsed')} {elapsed}s")

        self.time.set_icon(self.nations["time"])
        self.time.set_text(str(task.get("created_at", ""))[:16])

        self.set_selected(selected)


class QueueView(QWidget):
    """任务队列视图。"""

    task_finished = Signal(dict)   # 任务完成时通知主窗口（传任务 dict）

    def __init__(self, store: DataStore, get_downloader, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.get_downloader = get_downloader  # 可调用，返回 DownloaderBase 或 None
        self._runners: Dict[str, DownloadWorker] = {}
        self._cards: Dict[str, TaskCard] = {}
        self._selected_id = ""
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._schedule)
        self._timer.start(300)
        self.refresh()

    def _bar_colors(self):
        """返回进度条当前 (主题色, 是否深色)。主题色取自持久化设置。"""
        try:
            from ..style import accent_color
            key = str(self.store.settings_get("accent_color", "purple") or "purple")
            accent = accent_color(key)
        except Exception:  # noqa: BLE001
            accent = ACCENT
        dark = True
        win = self.window()
        if win is not None and hasattr(win, "_dark_theme"):
            dark = bool(win._dark_theme)
        else:
            # 兜底：按调色板窗口色亮度判断，避免嵌入式容器无属性时误判
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                dark = app.palette().window().color().lightness() < 128
        return accent, dark

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

        # ---- 卡片流 / 空状态 堆叠 ----
        self.card_area = QScrollArea()
        self.card_area.setObjectName("queueCardArea")
        self.card_area.setWidgetResizable(True)
        self.card_area.setFrameShape(QFrame.NoFrame)
        self.card_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.card_list = QWidget()
        self.card_list.setObjectName("queueCardList")
        self.card_layout = QVBoxLayout(self.card_list)
        self.card_layout.setContentsMargins(0, 0, 4, 0)
        self.card_layout.setSpacing(9)
        self.card_layout.addStretch(1)
        self.card_area.setWidget(self.card_list)

        # 空状态引导
        self.empty_widget = QWidget()
        ev = QVBoxLayout(self.empty_widget)
        ev.setAlignment(Qt.AlignCenter)
        ev.setSpacing(8)
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
        self.stack.addWidget(self.card_area)
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
        book = task.get("book", {}) or {}
        downloader = self.get_downloader(book)
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

    def _current_task(self) -> Optional[Dict[str, Any]]:
        if not self._selected_id:
            return None
        return self.store.queue_find(self._selected_id)

    def _on_card_clicked(self, task_id: str) -> None:
        self._selected_id = task_id
        for tid, card in self._cards.items():
            card.set_selected(tid == task_id)

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
        goto_search_page(self)

    def _show_context_menu_for(self, task_id: str, pos) -> None:
        menu = QMenu(self)
        task = self.store.queue_find(task_id) if task_id else None
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
        menu.exec(pos)

    def _show_context_menu(self, pos) -> None:
        """兼容旧调用：以当前选中卡片为准。"""
        self._show_context_menu_for(self._selected_id, pos)

    # ------------------------------------------------------------------ 刷新

    def refresh(self) -> None:
        tasks = self.store.queue_all()
        self.stack.setCurrentIndex(0 if tasks else 1)

        ids = {t.get("id") for t in tasks}
        # 移除已不存在的卡片
        for tid, card in list(self._cards.items()):
            if tid not in ids:
                self.card_layout.removeWidget(card)
                card.deleteLater()
                self._cards.pop(tid, None)
        if self._selected_id not in ids:
            self._selected_id = ""

        # 复用 / 新建卡片，只更新内容，避免频繁重建造成闪烁
        for t in tasks:
            tid = t.get("id", "")
            card = self._cards.get(tid)
            if card is None:
                card = TaskCard(self, self.card_list)
                card.clicked.connect(self._on_card_clicked)
                self._cards[tid] = card
                self.card_layout.insertWidget(self.card_layout.count() - 1, card)
            card.update_data(t, tid == self._selected_id)

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
        # 卡片内的状态 / 耗时文案由 update_data 重新 tr() 生成
        self.refresh()
