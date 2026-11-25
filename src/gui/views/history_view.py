# -*- coding: utf-8 -*-
"""历史记录标签页：查看下载历史、筛选、重试、清空、导出 CSV。

界面（B2 任务卡片流）：
- 每条记录一张卡片，卡片左侧是「书名首字」封面位（不加国徽），
  其余每一项信息（作者 / 格式 / 结果 / 章节 / 耗时 / 时间）前都带一个国徽。
- 信息行整体右对齐，书名与元信息之间有一条细分隔线。
"""

from __future__ import annotations

import csv
import re
from typing import Any, Dict, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QFrame, QLabel,
    QLineEdit, QPushButton, QComboBox, QMenu, QMessageBox, QStackedWidget,
)

from ..datastore import DataStore
from .. import filedialogs as _fd
from ..i18n import tr
from ..style import ACCENT, TEXT_DIM
from ..widgets import apply_nation_icon, nation_icon, goto_search_page
from ._card_base import _CardBase


COLUMNS = [
    "hist.col.time", "hist.col.title", "hist.col.author",
    "hist.col.format", "hist.col.result", "hist.col.elapsed",
    "hist.col.note",
]


def _note_text(rec: Dict[str, Any]) -> str:
    """历史卡片的章节/备注：优先章节数，其次消息，最后兜底。"""
    msg = str(rec.get("message", "") or "")
    note = str(rec.get("note", "") or "")
    m = re.search(r"(\d+\s*/\s*\d+\s*章|\d+\s*章)", msg + " " + note)
    if m:
        return m.group(1)
    if msg:
        return msg[:18]
    if note:
        return note[:18]
    return "—"


class HistoryCard(_CardBase):
    """B2 历史卡片。封面位不用国徽，其余每项信息前带一个国徽。"""

    CARD_OBJECT = "histTaskCard"
    COVER_OBJECT = "histCover"
    TITLE_OBJECT = "histCardTitle"
    LINE_OBJECT = "histCardLine"
    META_OBJECT = "histMetaText"
    PILL_OBJECT = "histStatusPill"
    STATUS_OBJECT = "histStatusText"

    def __init__(self, view, parent=None) -> None:
        super().__init__(view, "", parent)

    def _build_fields(self) -> None:
        # 标签行：作者 + 格式
        self.author = self._chip(self.tags)
        self._add_tag(self.author)
        self.fmt = self._chip(self.tags)
        self._add_tag(self.fmt)
        self._finish_tags()

        # 右对齐元信息行：章节 / 耗时 / 时间
        self.chapter = self._chip(self.meta)
        self.elapsed = self._chip(self.meta)
        self.time = self._chip(self.meta)

    def update_data(self, rec: Dict[str, Any], selected: bool = False) -> None:
        self.item_id = rec.get("id", "")
        ok = bool(rec.get("success"))
        self._set_title(rec.get("title", ""))

        self.author.set_icon(self.nations["author"])
        self.author.set_text(str(rec.get("author", "") or "—"))

        self.fmt.set_icon(self.nations["fmt"])
        self.fmt.set_text(str(rec.get("format", "") or "—").upper())

        result_text = tr("hist.success") if ok else tr("hist.failed")
        dot_color, text_color = ("#66bb6a", "#2e7d32") if ok else ("#ef5350", "#c62828")
        self._apply_status(result_text, dot_color, text_color, self.nations["status"])

        self.chapter.set_icon(self.nations["chapter"])
        note = _note_text(rec)
        self.chapter.set_text(note, str(rec.get("message", "") or note))

        self.elapsed.set_icon(self.nations["elapsed"])
        # hist.col.elapsed 三语均已带单位（耗时(s)/Elapsed(s)/Время(с)），不再重复拼 s
        self.elapsed.set_text(f"{tr('hist.col.elapsed')} {rec.get('elapsed', '')}")

        self.time.set_icon(self.nations["time"])
        self.time.set_text(str(rec.get("time", ""))[:16])

        self.set_selected(selected)


class HistoryView(QWidget):
    """历史记录视图。"""

    request_retry = Signal(dict)  # 请求重试某本书

    def __init__(self, store: DataStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self._records: List[Dict[str, Any]] = []
        self._cards: Dict[str, HistoryCard] = {}
        self._selected_id = ""
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

        # ---- 卡片流 / 空状态 堆叠 ----
        self.card_area = QScrollArea()
        self.card_area.setObjectName("histCardArea")
        self.card_area.setWidgetResizable(True)
        self.card_area.setFrameShape(QFrame.NoFrame)
        self.card_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.card_list = QWidget()
        self.card_list.setObjectName("histCardList")
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
        self.stack.addWidget(self.card_area)
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

        # 空状态：仅在「总记录数」为 0 时显示引导；筛选后无结果显示空卡片区
        self.stack.setCurrentIndex(0 if self._records else 1)

        ids = {r.get("id") for r in rows}
        for rid, card in list(self._cards.items()):
            if rid not in ids:
                self.card_layout.removeWidget(card)
                card.deleteLater()
                self._cards.pop(rid, None)
        if self._selected_id not in ids:
            self._selected_id = ""

        for rec in rows:
            rid = rec.get("id", "")
            card = self._cards.get(rid)
            if card is None:
                card = HistoryCard(self, self.card_list)
                card.clicked.connect(self._on_card_clicked)
                self._cards[rid] = card
                self.card_layout.insertWidget(self.card_layout.count() - 1, card)
            card.update_data(rec, rid == self._selected_id)

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

    def _on_card_clicked(self, record_id: str) -> None:
        self._selected_id = record_id
        for rid, card in self._cards.items():
            card.set_selected(rid == record_id)

    def retry_selected_failed(self) -> None:
        rec = next((r for r in self._records if r.get("id") == self._selected_id), None)
        if not rec:
            QMessageBox.information(self, "提示", "请先选中一条失败记录")
            return
        if rec.get("success"):
            QMessageBox.information(self, "提示", "该记录是成功的下载，无需重试")
            return
        self.request_retry.emit({
            "id": rec.get("book_id", ""),
            "title": rec.get("title", ""),
            "author": rec.get("author", ""),
            "source": rec.get("source", ""),
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
                writer.writerow([tr(k) for k in COLUMNS])
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
        goto_search_page(self)

    def _show_context_menu_for(self, record_id: str, pos) -> None:
        menu = QMenu(self)
        self._selected_id = record_id
        a = menu.addAction(tr("hist.retry_failed"))
        a.triggered.connect(self.retry_selected_failed)
        a = menu.addAction(tr("hist.export_csv"))
        a.triggered.connect(self.export_csv)
        menu.exec(pos)

    def _show_context_menu(self, pos) -> None:
        """兼容旧调用：以当前选中卡片为准。"""
        self._show_context_menu_for(self._selected_id, pos)

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
            # 书源随记录落库：失败重试要靠它选对内核
            "source": book_info.get("source", ""),
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
        # 卡片内的结果 / 耗时文案由 update_data 重新 tr() 生成
        self._apply_filter()
