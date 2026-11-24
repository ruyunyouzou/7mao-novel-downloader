# -*- coding: utf-8 -*-
"""全局快速搜索面板（Ctrl+Space）。

一个置顶、无边框的浮动窗口：输入关键词 → 回车 → 调用主窗口搜索并跳转。
参考 olib 的「打开快速搜索」全局快捷键交互。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLineEdit, QLabel, QListWidget,
    QListWidgetItem, QFrame,
)

from .i18n import tr
from .style import QSS, LIGHT_QSS, recolor_accent, ACCENT
from .widgets import nation_icon


class QuickSearchDialog(QDialog):
    """快速搜索浮层：输入即查历史建议，回车触发主窗口搜索。"""

    def __init__(self, main_window, dark: bool = True) -> None:
        super().__init__(main_window)
        self.main_window = main_window
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setModal(False)
        self.setFixedWidth(560)
        self._dark = dark
        self._apply_theme(dark)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        frame = QFrame()
        frame.setObjectName("quickFrame")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(10, 10, 10, 10)
        fl.setSpacing(8)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(tr("quick.placeholder"))
        self.edit.setObjectName("quickEdit")
        fl.addWidget(self.edit)

        self.hint = QLabel(tr("quick.hint"))
        self.hint.setObjectName("quickHint")
        fl.addWidget(self.hint)

        self.list = QListWidget()
        self.list.setObjectName("quickList")
        self.list.setMaximumHeight(220)
        fl.addWidget(self.list)
        root.addWidget(frame)

        self.edit.textChanged.connect(self._on_text)
        self.edit.returnPressed.connect(self._submit)
        self.list.itemActivated.connect(self._on_pick)

        # 载入搜索历史作为建议
        self._all_history = []
        try:
            # search_history_all() 返回的是关键词字符串列表（List[str]），不是字典
            self._all_history = [str(k) for k in main_window.store.search_history_all() if k]
        except Exception:  # noqa: BLE001
            pass
        self._on_text("")

    def _apply_theme(self, dark: bool) -> None:
        base = QSS if dark else LIGHT_QSS
        try:
            key = str(self.main_window.store.settings_get("accent_color", "purple") or "purple")
            base = recolor_accent(base, key)
        except Exception:  # noqa: BLE001
            pass
        self.setStyleSheet(base + """
            QFrame#quickFrame { border: 1px solid #3a3a3a; border-radius: 12px; }
            QLineEdit#quickEdit { font-size: 15px; padding: 10px 12px; }
            QLabel#quickHint { color: #888; font-size: 11px; }
            QListWidget#quickList { border: none; background: transparent; }
            QListWidget#quickList::item { padding: 7px 10px; border-radius: 6px; }
        """)

    def show_at_center(self) -> None:
        """显示在主窗口中央偏上位置并聚焦输入框。"""
        parent = self.main_window
        self._apply_theme(getattr(parent, "_dark_theme", True))
        pw, ph = parent.width(), parent.height()
        self.move(parent.x() + (pw - self.width()) // 2,
                  parent.y() + max(60, ph // 4))
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()
        QTimer.singleShot(0, self.edit.setFocus)

    def _on_text(self, text: str) -> None:
        self.list.clear()
        q = text.strip().lower()
        suggestions = []
        for h in self._all_history:
            if (not q or q in h.lower()) and h not in suggestions:
                suggestions.append(h)
            if len(suggestions) >= 8:
                break
        for s in suggestions:
            self.list.addItem(QListWidgetItem(nation_icon("snezhnaya", ACCENT, 16), s))
        self.hint.setText(tr("quick.hint") if not q else tr("quick.enter_hint"))

    def _on_pick(self, item: QListWidgetItem) -> None:
        text = item.text().strip()
        self.edit.setText(text)
        self._submit()

    def _submit(self) -> None:
        q = self.edit.text().strip()
        if not q:
            return
        try:
            self.main_window.store.search_history_add(q)
        except Exception:  # noqa: BLE001
            pass
        self.accept()
        # 跳到搜索页并填入关键词执行搜索；沿用主窗口当前选中的搜索类型（书名/作者），
        # 不强行重置——原先读取的 search_type 是从未被写入的死键，会误切回"搜书"
        mw = self.main_window
        mw.tabs.setCurrentIndex(0)
        if hasattr(mw, "search_edit"):
            mw.search_edit.setText(q)
        QTimer.singleShot(0, mw.do_search)
