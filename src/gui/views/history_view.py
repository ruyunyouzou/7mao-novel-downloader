# -*- coding: utf-8 -*-
"""历史记录标签页：查看下载历史、筛选、重试、清空、导出 CSV。"""

from __future__ import annotations

import csv
from typing import Any, Dict, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QLineEdit, QPushButton, QComboBox, QAbstractItemView,
    QMenu, QMessageBox, QLabel, QFileDialog, QStackedWidget,
)

from ..datastore import DataStore
from .. import filedialogs as _fd
from ..i18n import tr
from ..style import ACCENT, TEXT_DIM
from ..widgets import apply_nation_icon, nation_icon


class HistoryView(QWidget):
    """历史记录视图。"""

    request_retry = Signal(dict)  # 请求重试某本书

    COLUMNS = [
        "hist.col.time", "hist.col.title", "hist.col.author",
        "hist.col.format", "hist.col.result", "hist.col.elapsed",
        "hist.col.note",
    ]

    def __init__(self, store: DataStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self._records: List[Dict[str, Any]] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 8)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("hist.filter"))
        self.search_edit.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search_edit)

        self.result_combo = QComboBox()
        self.result_combo.addItems([
            tr("hist.all_results"), tr("hist.success"), tr("hist.failed"),
        ])
        # 用索引判断筛选，避免受翻译后的文字影响
        self.result_combo.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.result_combo)

        bar.addStretch()

        # (属性名, 文案 key, 国徽, 是否危险, 回调)
        btn_specs = [
            ("btn_retry", "hist.retry_failed", "liyue", False, self.retry_selected_failed),
            ("btn_export", "hist.export_csv", "fontaine", False, self.export_csv),
            ("btn_clear", "hist.clear", "inazuma", True, self.clear_all),
        ]
        for attr, key, nation, danger, cb in btn_specs:
            b = QPushButton(tr(key))
            b.setObjectName("histDanger" if danger else "histAction")
            b.setProperty("danger", "true" if danger else "false")
            apply_nation_icon(b, nation, ACCENT, 15)
            b.clicked.connect(cb)
            setattr(self, attr, b)
            bar.addWidget(b)

        layout.addLayout(bar)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(k) for k in self.COLUMNS])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        # 空状态引导
        self.empty_widget = QWidget()
        ev = QVBoxLayout(self.empty_widget)
        ev.setAlignment(Qt.AlignCenter)
        ev.setSpacing(8)
        em = QLabel()
        em.setPixmap(nation_icon("mondstadt", ACCENT, 52).pixmap(52, 52))
        em.setAlignment(Qt.AlignCenter)
        ev.addWidget(em)
        self.empty_title = QLabel(tr("hist.empty_title"))
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_title.setStyleSheet("font-size:16px; font-weight:bold;")
        ev.addWidget(self.empty_title)
        self.empty_hint = QLabel(tr("hist.empty_hint"))
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setStyleSheet(f"color: {TEXT_DIM}; font-size:12px;")
        ev.addWidget(self.empty_hint)
        self.btn_go_search = QPushButton(tr("hist.go_search"))
        self.btn_go_search.setObjectName("histGoSearch")
        self.btn_go_search.setStyleSheet(
            f"QPushButton#histGoSearch {{ color: {ACCENT}; border: 1px solid {ACCENT}; "
            f"border-radius: 14px; padding: 5px 18px; background: transparent; }}")
        self.btn_go_search.clicked.connect(self._go_search)
        ev.addWidget(self.btn_go_search, 0, Qt.AlignCenter)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty_widget)
        layout.addWidget(self.stack, 1)

        # 底部统计胶囊
        stat_bar = QHBoxLayout()
        stat_bar.setSpacing(6)
        self.stat_total = QLabel()
        self.stat_success = QLabel()
        self.stat_failed = QLabel()
        for lab in (self.stat_total, self.stat_success, self.stat_failed):
            lab.setObjectName("histStatPill")
            stat_bar.addWidget(lab)
        stat_bar.addStretch()
        self.count_label = QLabel()
        self.count_label.setStyleSheet(f"color: {TEXT_DIM};")
        stat_bar.addWidget(self.count_label)
        layout.addLayout(stat_bar)

    def refresh(self) -> None:
        self._records = self.store.history_all()
        self._apply_filter()

    def _apply_filter(self) -> None:
        keyword = self.search_edit.text().strip().lower()
        result_idx = self.result_combo.currentIndex()  # 0=全部 1=成功 2=失败
        rows = []
        for r in self._records:
            if result_idx == 1 and not r.get("success"):
                continue
            if result_idx == 2 and r.get("success"):
                continue
            if keyword and keyword not in (
                f"{r.get('title','')} {r.get('author','')} {r.get('book_id','')}".lower()
            ):
                continue
            rows.append(r)

        # 空状态：仅在「总记录数」为 0 时显示引导；筛选后无结果显示空表格
        self.stack.setCurrentIndex(0 if self._records else 1)
        self.table.setRowCount(len(rows))
        for i, rec in enumerate(rows):
            ok = bool(rec.get("success"))
            result_text = tr("hist.success") if ok else tr("hist.failed")
            values = [
                rec.get("time", ""), rec.get("title", ""), rec.get("author", ""),
                rec.get("format", ""),
                f"● {result_text}",  # 结果徽章：圆点 + 文字
                str(rec.get("elapsed", "")), rec.get("message", ""),
            ]
            for c, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setData(Qt.UserRole, rec.get("id", ""))
                if c == 4:
                    item.setForeground(QColor("#2e7d32" if ok else "#c62828"))
                    item.setToolTip(result_text)
                self.table.setItem(i, c, item)

        # 统计胶囊：基于全部记录
        total = len(self._records)
        n_ok = sum(1 for r in self._records if r.get("success"))
        n_fail = total - n_ok
        self.stat_total.setText(tr("hist.stat_total", n=total))
        self.stat_success.setText(tr("hist.stat_success", n=n_ok))
        self.stat_failed.setText(tr("hist.stat_failed", n=n_fail))

        count_text = tr("hist.count", n=len(rows))
        if keyword or result_idx != 0:
            count_text += tr("hist.count_filtered", n=total)
        self.count_label.setText(count_text)

    def retry_selected_failed(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= self.table.rowCount():
            QMessageBox.information(self, "提示", "请先选中一条失败记录")
            return
        record_id = self.table.item(row, 0).data(Qt.UserRole)
        rec = next((r for r in self._records if r.get("id") == record_id), None)
        if not rec:
            return
        if rec.get("success"):
            QMessageBox.information(self, "提示", "该记录是成功的下载，无需重试")
            return
        self.request_retry.emit({
            "id": rec.get("book_id", ""),
            "title": rec.get("title", ""),
            "author": rec.get("author", ""),
        })

    def export_csv(self) -> None:
        path = _fd.save_file(
            self, "导出历史记录", "下载历史.csv", "CSV 文件 (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([tr(k) for k in self.COLUMNS])
                for r in self._records:
                    writer.writerow([
                        r.get("time", ""), r.get("title", ""), r.get("author", ""),
                        r.get("format", ""),
                        tr("hist.success") if r.get("success") else tr("hist.failed"),
                        r.get("elapsed", ""), r.get("message", ""),
                    ])
            QMessageBox.information(self, "导出成功", f"历史记录已导出到：\n{path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "导出失败", str(exc))

    def clear_all(self) -> None:
        if QMessageBox.question(
            self, "确认", "确定清空所有历史记录？此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            self.store.history_clear()
            self.refresh()

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
        a = menu.addAction(tr("hist.retry_failed"))
        a.triggered.connect(self.retry_selected_failed)
        a = menu.addAction(tr("hist.export_csv"))
        a.triggered.connect(self.export_csv)
        menu.exec(self.table.mapToGlobal(pos))

    def add_record(self, book_info: Dict[str, Any], fmt: str, success: bool,
                   message: str = "", elapsed: float = 0.0, file_path: str = "") -> None:
        """供主程序在下载完成时调用，写入历史。"""
        self.store.history_add({
            "book_id": book_info.get("id", ""),
            "title": book_info.get("title", ""),
            "author": book_info.get("author", ""),
            "format": fmt,
            "success": success,
            "message": message,
            "elapsed": elapsed,
            "file_path": file_path,
        })
        self.refresh()

    def retranslate(self) -> None:
        """语言切换后刷新文案。"""
        self.search_edit.setPlaceholderText(tr("hist.filter"))
        result_idx = self.result_combo.currentIndex()
        self.result_combo.blockSignals(True)
        self.result_combo.clear()
        self.result_combo.addItems([
            tr("hist.all_results"), tr("hist.success"), tr("hist.failed"),
        ])
        self.result_combo.setCurrentIndex(max(0, result_idx))
        self.result_combo.blockSignals(False)
        self.btn_retry.setText(tr("hist.retry_failed"))
        self.btn_export.setText(tr("hist.export_csv"))
        self.btn_clear.setText(tr("hist.clear"))
        self.btn_go_search.setText(tr("hist.go_search"))
        self.empty_title.setText(tr("hist.empty_title"))
        self.empty_hint.setText(tr("hist.empty_hint"))
        self.table.setHorizontalHeaderLabels([tr(k) for k in self.COLUMNS])
        self._apply_filter()
