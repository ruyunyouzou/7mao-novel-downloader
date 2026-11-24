# -*- coding: utf-8 -*-
"""内置使用说明书页面。

左侧是章节目录与搜索，右侧用 QTextBrowser 渲染 Markdown。说明书正文来自
项目根目录的 `使用说明书.md`，由 help_content.py 负责定位与兜底。
"""

from __future__ import annotations

from typing import List, Tuple

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QTextBrowser, QPushButton, QMessageBox, QApplication,
)

from ..datastore import DataStore
from ..help_content import load_manual, manual_path
from ..i18n import tr
from ..style import accent_color
from ..widgets import nation_icon


def _parse_sections(markdown: str) -> List[Tuple[str, str]]:
    """把说明书按二级标题拆成目录；返回 [(标题, 正文), ...]。"""
    sections: List[Tuple[str, str]] = []
    intro: List[str] = []
    title = ""
    body: List[str] = []

    def flush() -> None:
        nonlocal title, body
        if title and title.strip() != "目录":
            sections.append((title.strip(), "\n".join(body).strip()))
        title = ""
        body = []

    for line in markdown.splitlines():
        if line.startswith("## "):
            flush()
            title = line[3:].strip()
        elif title:
            body.append(line)
        elif line.strip() and not line.startswith("# "):
            intro.append(line)
    flush()

    result: List[Tuple[str, str]] = [("全文", markdown)]
    if intro:
        result.append(("概览", "\n".join(intro).strip()))
    result.extend(sections)
    return result


class HelpView(QWidget):
    """软件内置说明书页面。"""

    def __init__(self, main_window=None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("helpPage")
        self.window_title = tr("nav.help")
        self.setWindowTitle(self.window_title)
        self.main_window = main_window
        self.store = getattr(main_window, "store", None) or DataStore()
        self._markdown = load_manual()
        self._sections = _parse_sections(self._markdown)
        self._accent = accent_color("purple")
        self._dark = True
        self._build_ui()
        self._select_index(0)
        self.apply_colors()
        self.retranslate()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)

        head = QHBoxLayout()
        self.icon_label = QLabel()
        self.icon_label.setObjectName("helpTitleIcon")
        self.icon_label.setFixedSize(26, 26)
        self.icon_label._nation = "fontaine"  # type: ignore[attr-defined]
        head.addWidget(self.icon_label)

        self.title_label = QLabel()
        self.title_label.setObjectName("helpTitle")
        head.addWidget(self.title_label)
        head.addStretch(1)

        self.open_btn = QPushButton()
        self.open_btn.setObjectName("helpOpenFile")
        self.open_btn.clicked.connect(self._open_external)
        head.addWidget(self.open_btn)

        self.copy_btn = QPushButton()
        self.copy_btn.setObjectName("helpCopy")
        self.copy_btn.clicked.connect(self._copy_current)
        head.addWidget(self.copy_btn)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("helpSearch")
        self.search_edit.textChanged.connect(self._filter_sections)
        left.addWidget(self.search_edit)

        self.toc = QListWidget()
        self.toc.setObjectName("helpToc")
        self.toc.currentRowChanged.connect(self._on_toc_changed)
        left.addWidget(self.toc, 1)

        self.count_label = QLabel()
        self.count_label.setObjectName("helpCount")
        left.addWidget(self.count_label)
        body.addLayout(left, 1)

        self.text = QTextBrowser()
        self.text.setObjectName("helpText")
        self.text.setOpenExternalLinks(True)
        self.text.setReadOnly(True)
        body.addWidget(self.text, 4)

        root.addLayout(body, 1)

        for i, (title, content) in enumerate(self._sections):
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, i)
            item.setToolTip(content[:240])
            self.toc.addItem(item)

    # -------------------------------------------------------------- 目录交互
    def _filter_sections(self, keyword: str) -> None:
        kw = (keyword or "").strip().lower()
        visible = 0
        for i in range(self.toc.count()):
            item = self.toc.item(i)
            idx = int(item.data(Qt.UserRole))
            title, content = self._sections[idx]
            show = (not kw or kw in title.lower() or kw in content.lower())
            item.setHidden(not show)
            if show:
                visible += 1
        self.count_label.setText(tr("help.section_count", n=visible, total=len(self._sections)))

    def _on_toc_changed(self, row: int) -> None:
        if row < 0:
            return
        item = self.toc.item(row)
        if item is None:
            return
        idx = int(item.data(Qt.UserRole))
        self._select_index(idx)

    def _select_index(self, index: int) -> None:
        if not (0 <= index < len(self._sections)):
            return
        _title, content = self._sections[index]
        self.text.setMarkdown(content)
        bar = self.text.verticalScrollBar()
        if bar is not None:
            bar.setValue(0)

    def _current_content(self) -> str:
        row = self.toc.currentRow()
        if row < 0:
            return self._markdown
        item = self.toc.item(row)
        if item is None:
            return self._markdown
        idx = int(item.data(Qt.UserRole))
        if not (0 <= idx < len(self._sections)):
            return self._markdown
        return self._sections[idx][1]

    def _open_external(self) -> None:
        path = manual_path()
        if not path:
            QMessageBox.warning(self, tr("common.tip"), tr("help.file_missing"))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _copy_current(self) -> None:
        QApplication.clipboard().setText(self._current_content())

    # -------------------------------------------------------------- 主题适配
    # 沉浸式阅读风（方案 C）：羊皮纸米黄 + 衬线字体 + 大圆角柔和卡片。
    # 字号/圆角/留统一收敛到一处，深浅两套配色都遵循同一套版式语言，
    # 只换颜色不换结构，避免深浅主题下版式割裂。
    _SERIF = "'Noto Serif CJK SC', 'Songti SC', 'STSong', serif"

    def apply_colors(self) -> None:
        """跟随全局深浅主题与强调色刷新配色（沉浸式阅读风，深浅双版）。"""
        key = str(self.store.settings_get("accent_color", "purple") or "purple")
        self._accent = accent_color(key)
        dark_theme = getattr(self.main_window, "_dark_theme", None)
        if dark_theme is None:
            mode = str(self.store.settings_get("theme_mode", "dark") or "dark")
            self._dark = mode not in ("light",)
        else:
            self._dark = bool(dark_theme)

        from .. import nation_theme
        if self._dark:
            # 深色版：夜读羊皮纸 —— 深褐底 + 暖白字，仍是衬线 + 圆角的沉浸观感
            page_bg = "#211e19"
            toc_bg, body_bg = "#2b2721", "#302b23"
            border = "#463f33"
            text, dim = "#e8dfcf", "#a99c86"
            hover, selected = "#3a342a", "#8a7250"
            link, sel_text = "#d8b981", "#fff6e2"
            btn_bg, btn_text = "#2b2721", "#c9bda6"
        else:
            # 浅色版：经典羊皮纸米黄（方案 C 原色）
            page_bg = "#eae5d9"
            toc_bg, body_bg = "#f5f1e8", "#fbf8f0"
            border = "#d8cfbc"
            text, dim = "#4a4136", "#9a9082"
            hover, selected = "#ebe3d2", "#c9b896"
            link, sel_text = "#8a6d3b", "#3a3228"
            btn_bg, btn_text = "#f5f1e8", "#6b6053"

        # 卡片色跟随用户自定义功能背景（深浅兜底各自独立）
        toc_bg = nation_theme.resolved_card_bg(toc_bg)
        body_bg = nation_theme.resolved_card_bg(body_bg)
        serif = self._SERIF

        self.setStyleSheet(f"QWidget#helpPage {{ background: {page_bg}; }}")
        self.title_label.setStyleSheet(
            f"color: {text}; font-size: 22px; font-weight: 600; background: transparent; "
            f"font-family: {serif}; letter-spacing: 1px;")
        self.count_label.setStyleSheet(
            f"color: {dim}; font-size: 11px; background: transparent; font-family: {serif};")
        self.search_edit.setStyleSheet(
            f"QLineEdit {{ background: {toc_bg}; color: {text}; border: 1px solid {border}; "
            f"border-radius: 20px; padding: 9px 18px; font-family: {serif}; }}"
            f"QLineEdit:focus {{ border: 1px solid {link}; }}")
        self.toc.setStyleSheet(
            f"QListWidget {{ background: {toc_bg}; color: {text}; border: 1px solid {border}; "
            f"border-radius: 16px; padding: 10px; font-family: {serif}; }}"
            f"QListWidget::item {{ border-radius: 10px; padding: 8px 10px; }}"
            f"QListWidget::item:hover {{ background: {hover}; }}"
            f"QListWidget::item:selected {{ background: {selected}; color: {sel_text}; "
            f"font-weight: bold; }}")
        self.text.setStyleSheet(
            f"QTextBrowser {{ background: {body_bg}; color: {text}; border: 1px solid {border}; "
            f"border-radius: 16px; padding: 30px 40px; font-size: 14px; "
            f"font-family: {serif}; }}"
            f"QTextBrowser a {{ color: {link}; }}")
        for btn in (self.open_btn, self.copy_btn):
            btn.setStyleSheet(
                f"QPushButton {{ background: {btn_bg}; color: {btn_text}; "
                f"border: 1px solid {border}; border-radius: 20px; padding: 7px 18px; "
                f"font-family: {serif}; }}"
                f"QPushButton:hover {{ background: {selected}; color: {sel_text}; }}")

        self.icon_label.setPixmap(nation_icon("fontaine", self._accent, 24).pixmap(24, 24))

    def refresh_theme(self, dark: bool) -> None:
        """兼容主窗口主题刷新调用。"""
        self._dark = bool(dark)
        self.apply_colors()

    def retranslate(self) -> None:
        """语言切换后刷新标题与按钮文案。"""
        self.window_title = tr("nav.help")
        self.setWindowTitle(self.window_title)
        self.title_label.setText(tr("help.title"))
        self.search_edit.setPlaceholderText(tr("help.search_placeholder"))
        self.open_btn.setText(tr("help.open_file"))
        self.copy_btn.setText(tr("help.copy_section"))
        self.count_label.setText(
            tr("help.section_count", n=self.toc.count(), total=len(self._sections)))
