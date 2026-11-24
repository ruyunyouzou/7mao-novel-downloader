# -*- coding: utf-8 -*-
"""书架标签页：展示本地已下载小说，支持打开、移除、筛选。"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, Dict, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QLineEdit, QPushButton, QComboBox, QAbstractItemView,
    QMenu, QMessageBox, QLabel, QFileDialog, QFrame,
)

from ..datastore import DataStore
from ..i18n import tr
from ..resource_path import resource_path
from ..style import accent_color

# 分组标题国徽（国徽名称见 assets/nation_icons/）
GROUP_ICONS = {
    "filter": "snezhnaya",   # 筛选组 · 至冬
    "read": "mondstadt",     # 阅读管理组 · 蒙德
    "file": "liyue",         # 文件操作组 · 璃月
}
# 组内按钮国徽
NATION_FORMAT = "fontaine"      # 格式筛选
NATION_TAG = "sumeru"           # 标签筛选
NATION_READ = "mondstadt"       # 阅读
NATION_IMPORT = "liyue"         # 导入本地书
NATION_ACTIONS = {
    "tag": "sumeru", "follow": "fontaine", "open": "natlan",
    "folder": "snezhnaya", "remove": "nodkrai",
}


class ShelfView(QWidget):
    """书架视图。"""

    request_download = Signal(dict)  # 请求重新下载某本书
    request_read = Signal(str, str, str, object)  # 请求阅读：path, title, key, chapter_index
    request_weread_import = Signal(list)  # 请求把选中书导入微信读书：entries(list)
    request_calibre_edit = Signal(str)  # 请求用 Calibre 编辑 EPUB：file_path
    request_follow = Signal()  # 请求打开增量追更对话框

    COLUMNS = [
        "shelf.col.title", "shelf.col.author", "shelf.col.format",
        "shelf.col.tags", "shelf.col.progress", "shelf.col.words",
        "shelf.col.added", "shelf.col.path",
    ]

    def __init__(self, store: DataStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self._entries: List[Dict[str, Any]] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ===== 工具栏：三组卡片 =====
        # 第一行：筛选卡片（占满整行，搜索框不拥挤）
        filter_card, filter_body = self._make_group_card("shelf.group_filter", GROUP_ICONS["filter"])
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("shelf.filter"))
        self.search_edit.textChanged.connect(self._apply_filter)
        filter_body.addWidget(self.search_edit, 1)
        self.format_combo = QComboBox()
        self.format_combo.addItems([tr("shelf.all_formats"), "txt", "epub", "chapter"])
        self.format_combo.currentIndexChanged.connect(self._apply_filter)
        filter_body.addWidget(self.format_combo)
        self.tag_combo = QComboBox()
        self.tag_combo.currentIndexChanged.connect(self._apply_filter)
        filter_body.addWidget(self.tag_combo)
        layout.addWidget(filter_card)

        # 第二行：阅读管理 + 文件操作（并排）
        op_row = QHBoxLayout()
        op_row.setSpacing(8)

        read_card, read_body = self._make_group_card("shelf.group_read", GROUP_ICONS["read"])
        self.btn_read = QPushButton(tr("shelf.read"))
        self.btn_read.clicked.connect(self.read_selected)
        read_body.addWidget(self.btn_read)
        self.btn_follow = QPushButton(tr("follow.title"))
        self.btn_follow.clicked.connect(lambda: self.request_follow.emit())
        read_body.addWidget(self.btn_follow)
        self.btn_tag = QPushButton(tr("shelf.set_tags"))
        self.btn_tag.clicked.connect(self.set_tags_selected)
        read_body.addWidget(self.btn_tag)
        read_body.addStretch()
        op_row.addWidget(read_card, 1)

        file_card, file_body = self._make_group_card("shelf.group_file", GROUP_ICONS["file"])
        self.btn_import = QPushButton(tr("shelf.import_local"))
        self.btn_import.clicked.connect(self.import_local)
        file_body.addWidget(self.btn_import)
        self.btn_open = QPushButton(tr("shelf.open_file"))
        self.btn_open.clicked.connect(self.open_selected)
        file_body.addWidget(self.btn_open)
        self.btn_folder = QPushButton(tr("shelf.open_folder"))
        self.btn_folder.clicked.connect(self.open_folder)
        file_body.addWidget(self.btn_folder)
        self.btn_remove = QPushButton(tr("shelf.remove"))
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_remove.setObjectName("shelfDanger")  # 红色样式
        file_body.addWidget(self.btn_remove)
        file_body.addStretch()
        op_row.addWidget(file_card, 1)

        layout.addLayout(op_row)

        # 表格
        self.table = QTableWidget()
        self.table.setColumnCount(len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(k) for k in self.COLUMNS])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.doubleClicked.connect(self.read_selected)
        layout.addWidget(self.table)

        self.count_label = QLabel()
        layout.addWidget(self.count_label)

        # 统一给国徽图标染色（主题色）
        self._apply_nation_icons()

    def _make_group_card(self, title_key: str, icon_name: str):
        """创建一张分组卡片，返回 (card, 用于放按钮的 QHBoxLayout)。"""
        card = QFrame()
        card.setObjectName("shelfGroupCard")
        root = QVBoxLayout(card)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(7)
        # 标题行：国徽 + 标题文字
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        icon_label = QLabel()
        icon_label.setFixedSize(18, 18)
        icon_label.setObjectName("shelfGroupIcon")
        title_row.addWidget(icon_label)
        title = QLabel(tr(title_key))
        title.setObjectName("shelfGroupTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        root.addLayout(title_row)
        # 按钮行
        body = QHBoxLayout()
        body.setSpacing(6)
        root.addLayout(body)
        # 记录国徽标签与文案 key（染色 / 换语言用）
        if not hasattr(self, "_group_icon_labels"):
            self._group_icon_labels = []
            self._group_icon_keys = []
        self._group_icon_labels.append(icon_label)
        self._group_icon_keys.append((title, title_key))
        return card, body

    def _nation_icon(self, name: str, size: int = 18) -> QIcon:
        """把白色国徽 PNG 染成当前强调色，返回 QIcon。"""
        from ..style import ACCENT
        key = str(self.store.settings_get("accent_color", "purple") or "purple")
        color = accent_color(key) or ACCENT
        path = resource_path("assets", "nation_icons", f"{name}.png")
        pm = QPixmap(path)
        if pm.isNull():
            return QIcon()
        pm = pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        painter = QPainter(pm)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(pm.rect(), QColor(color))
        painter.end()
        return QIcon(pm)

    def _apply_combo_icons(self) -> None:
        """给筛选下拉框的每一项都设图标，避免切换选项后图标消失。"""
        fmt_icon = self._nation_icon(NATION_FORMAT)
        tag_icon = self._nation_icon(NATION_TAG)
        for i in range(self.format_combo.count()):
            self.format_combo.setItemIcon(i, fmt_icon)
        for i in range(self.tag_combo.count()):
            self.tag_combo.setItemIcon(i, tag_icon)

    def _apply_nation_icons(self) -> None:
        """给分组标题、卡片按钮、下拉框统一设置国徽图标（跟随主题色）。"""
        # 三张卡片的标题国徽
        for label, nation in zip(self._group_icon_labels,
                                 (GROUP_ICONS["filter"], GROUP_ICONS["read"], GROUP_ICONS["file"])):
            label.setPixmap(self._nation_icon(nation, 18).pixmap(18, 18))
        # 卡片按钮
        self.btn_read.setIcon(self._nation_icon(NATION_READ))
        self.btn_follow.setIcon(self._nation_icon(NATION_ACTIONS["follow"]))
        self.btn_tag.setIcon(self._nation_icon(NATION_ACTIONS["tag"]))
        self.btn_import.setIcon(self._nation_icon(NATION_IMPORT))
        self.btn_open.setIcon(self._nation_icon(NATION_ACTIONS["open"]))
        self.btn_folder.setIcon(self._nation_icon(NATION_ACTIONS["folder"]))
        self.btn_remove.setIcon(self._nation_icon(NATION_ACTIONS["remove"]))
        # 筛选下拉框
        self._apply_combo_icons()

    def refresh(self) -> None:
        """从数据存储重新加载。"""
        self._entries = self.store.shelf_all()
        self._rebuild_tag_combo()
        self._apply_filter()

    def _rebuild_tag_combo(self) -> None:
        """重建标签筛选下拉（保留当前选择）。"""
        current = self.tag_combo.currentText()
        tags = set()
        for e in self._entries:
            for t in (e.get("tags") or []):
                if t:
                    tags.add(str(t))
        self.tag_combo.blockSignals(True)
        self.tag_combo.clear()
        self.tag_combo.addItem(tr("shelf.all_tags"))
        self.tag_combo.addItems(sorted(tags))
        idx = self.tag_combo.findText(current)
        self.tag_combo.setCurrentIndex(max(0, idx))
        self.tag_combo.blockSignals(False)
        # 重建后重新贴图标（QComboBox.clear() 会清掉 itemIcon）
        if hasattr(self, "btn_read"):  # 确保 UI 已构建完成
            self._apply_combo_icons()

    def _apply_filter(self) -> None:
        keyword = self.search_edit.text().strip().lower()
        fmt_idx = self.format_combo.currentIndex()
        # 第 0 项为「全部格式」；格式项文本固定为 txt/epub/chapter
        fmt = self.format_combo.itemText(fmt_idx) if fmt_idx > 0 else ""
        tag = self.tag_combo.currentText() if self.tag_combo.currentIndex() > 0 else ""
        rows = []
        for e in self._entries:
            if fmt and e.get("format") != fmt:
                continue
            if tag and tag not in (e.get("tags") or []):
                continue
            if keyword and keyword not in (
                f"{e.get('title','')} {e.get('author','')} {e.get('book_id','')} "
                f"{' '.join(e.get('tags') or [])}".lower()
            ):
                continue
            rows.append(e)

        self.table.setRowCount(len(rows))
        for r, e in enumerate(rows):
            # 阅读进度：优先用 book_id 关联的进度
            key = e.get("book_id") or e.get("file_path", "")
            prog = self.store.progress_get(key)
            pct = prog.get("percent", 0) if prog else 0
            progress_text = f"{pct:.0f}%" if pct else "—"
            values = [
                e.get("title", ""), e.get("author", ""), e.get("format", ""),
                ", ".join(e.get("tags") or []),
                progress_text, str(e.get("words_num", "")), e.get("added_at", ""),
                e.get("file_path", ""),
            ]
            for c, val in enumerate(values):
                item = QTableWidgetItem(str(val))
                item.setData(Qt.UserRole, e.get("id", ""))
                self.table.setItem(r, c, item)
        count_text = tr("shelf.count", n=len(rows))
        if keyword or fmt or tag:
            count_text += tr("shelf.count_filtered", n=len(self._entries))
        self.count_label.setText(count_text)

    def _current_entry(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= self.table.rowCount():
            return None
        entry_id = self.table.item(row, 0).data(Qt.UserRole)
        for e in self._entries:
            if e.get("id") == entry_id:
                return e
        return None

    def read_selected(self) -> None:
        """在内置阅读器中打开选中书籍。"""
        entry = self._current_entry()
        if not entry:
            QMessageBox.information(self, "提示", "请先选中一本书")
            return
        path = entry.get("file_path", "")
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "无法阅读", f"文件不存在：\n{path}")
            return
        key = entry.get("book_id") or path
        prog = self.store.progress_get(key)
        idx = prog.get("chapter_index") if prog else None
        self.request_read.emit(path, entry.get("title", ""), key, idx)

    def import_local(self) -> None:
        """导入本地 TXT/EPUB 到书架。"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择本地小说", "", "小说文件 (*.txt *.epub);;所有文件 (*.*)"
        )
        if not paths:
            return
        count = 0
        for path in paths:
            title = os.path.splitext(os.path.basename(path))[0]
            fmt = "epub" if path.lower().endswith(".epub") else "txt"
            self.store.shelf_add({
                "book_id": "", "title": title, "author": "本地导入",
                "format": fmt, "words_num": "", "file_path": path,
                "local": True,
            })
            count += 1
        self.refresh()
        QMessageBox.information(self, "导入完成", f"已导入 {count} 本到本地书架")

    def open_selected(self) -> None:
        entry = self._current_entry()
        if not entry:
            QMessageBox.information(self, "提示", "请先选中一本书")
            return
        path = entry.get("file_path", "")
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "无法打开", f"文件不存在：\n{path}")
            return
        self._open_path(path)

    def open_folder(self) -> None:
        entry = self._current_entry()
        if not entry:
            QMessageBox.information(self, "提示", "请先选中一本书")
            return
        path = entry.get("file_path", "")
        folder = os.path.dirname(path) if os.path.isfile(path) else path
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, "无法打开", f"目录不存在：\n{folder}")
            return
        self._open_path(folder)

    @staticmethod
    def _open_path(path: str) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(None, "打开失败", str(exc))

    def set_tags_selected(self) -> None:
        """为选中书设置标签（逗号分隔）。"""
        entry = self._current_entry()
        if not entry:
            QMessageBox.information(self, "提示", "请先选中一本书")
            return
        from PySide6.QtWidgets import QInputDialog
        current = ", ".join(entry.get("tags") or [])
        text, ok = QInputDialog.getText(
            self, tr("shelf.set_tags"), tr("shelf.tags_hint"), text=current)
        if not ok:
            return
        tags = [t.strip() for t in text.replace("，", ",").split(",") if t.strip()]
        self.store.shelf_update(entry["id"], {"tags": tags})
        self.refresh()

    def remove_selected(self) -> None:
        entry = self._current_entry()
        if not entry:
            QMessageBox.information(self, "提示", "请先选中一本书")
            return
        if QMessageBox.question(
            self, "确认", f"确定从书架移除《{entry.get('title')}》？\n（不会删除本地文件）",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            self.store.shelf_remove(entry["id"])
            self.refresh()

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        act_read = menu.addAction(tr("shelf.read"))
        act_read.triggered.connect(self.read_selected)
        act_open = menu.addAction(tr("shelf.open_file"))
        act_open.triggered.connect(self.open_selected)
        act_folder = menu.addAction(tr("shelf.open_folder"))
        act_folder.triggered.connect(self.open_folder)
        act_tags = menu.addAction(tr("shelf.set_tags"))
        act_tags.triggered.connect(self.set_tags_selected)
        entry = self._current_entry()
        if entry:
            act_redownload = menu.addAction(tr("shelf.redownload"))
            act_redownload.triggered.connect(
                lambda: self.request_download.emit({
                    "id": entry.get("book_id", ""),
                    "title": entry.get("title", ""),
                    "author": entry.get("author", ""),
                })
            )
            act_weread = menu.addAction(tr("shelf.weread_import"))
            act_weread.triggered.connect(
                lambda: self.request_weread_import.emit([entry])
            )
            # EPUB 才能用 Calibre Editor 直接编辑
            fp = str(entry.get("file_path") or "")
            if fp.lower().endswith(".epub") and os.path.isfile(fp):
                act_calibre = menu.addAction(tr("shelf.calibre_edit"))
                act_calibre.triggered.connect(
                    lambda: self.request_calibre_edit.emit(fp)
                )
        menu.exec(self.table.mapToGlobal(pos))

    def add_entry(self, book_info: Dict[str, Any], file_path: str, fmt: str, words_num: Any = "") -> None:
        """供主程序在下载完成时调用，加入书架。"""
        self.store.shelf_add({
            "book_id": book_info.get("id", ""),
            "title": book_info.get("title", ""),
            "author": book_info.get("author", ""),
            "format": fmt,
            "words_num": words_num,
            "file_path": file_path,
        })
        self.refresh()

    def retranslate(self) -> None:
        """语言切换后刷新文案。"""
        self.search_edit.setPlaceholderText(tr("shelf.filter"))
        fmt_idx = self.format_combo.currentIndex()
        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        self.format_combo.addItems([tr("shelf.all_formats"), "txt", "epub", "chapter"])
        self.format_combo.setCurrentIndex(max(0, fmt_idx))
        self.format_combo.blockSignals(False)
        # 分组标题
        for label, key in self._group_icon_keys:
            label.setText(tr(key))
        # 卡片按钮
        self.btn_read.setText(tr("shelf.read"))
        self.btn_follow.setText(tr("follow.title"))
        self.btn_tag.setText(tr("shelf.set_tags"))
        self.btn_import.setText(tr("shelf.import_local"))
        self.btn_open.setText(tr("shelf.open_file"))
        self.btn_folder.setText(tr("shelf.open_folder"))
        self.btn_remove.setText(tr("shelf.remove"))
        self._rebuild_tag_combo()
        self.table.setHorizontalHeaderLabels([tr(k) for k in self.COLUMNS])
        self._apply_filter()
