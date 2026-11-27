# -*- coding: utf-8 -*-
"""内置使用说明书页面（方案 A：门户卡片首页）。

首页是 Hero 横幅 + 功能分区卡片网格（国徽图标），点击卡片进入该分区章节
列表，再点章节进入正文（QTextBrowser 渲染 Markdown + 演示图）。
小说 Logo（app_logo.png，七猫猫头）保持不变；功能图标全部使用原神国徽。

说明书正文来自项目根目录的 `使用说明书.md`，由 help_content.py 负责定位与兜底。
"""

from __future__ import annotations

import os
import re
from typing import List, Tuple

from PySide6.QtCore import Qt, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QTextBrowser, QPushButton, QMessageBox,
    QApplication, QToolButton, QDialog, QScrollArea, QFrame, QStackedWidget,
)

from ..datastore import DataStore
from ..help_content import load_manual, manual_path
from ..help_demos import DEMOS, demo_image
from ..i18n import tr
from ..resource_path import resource_path
from ..style import (
    ACCENT_LABELS, ACCENT_PALETTES, accent_color, modern_scrollbar_qss,
)
from ..widgets import nation_icon
from ..version import VERSION_LABEL

# 正文字号范围与默认值
_FONT_MIN, _FONT_MAX, _FONT_DEFAULT = 10, 24, 14
_FONT_SIZE_KEY = "help_font_size"
_DEMO_SCHEME = "demo://"

# 功能分区：(国徽 key, 标题 i18n, 简介 i18n, 演示图, 章节序号前缀)
# 章节序号对应《使用说明书.md》的二级标题，如「1. 快速开始」。
_GROUPS = (
    ("mondstadt", "help.group_start", "help.group_start_desc",
     "preview_help_light.png", ("1.", "2.")),
    ("liyue", "help.group_fetch", "help.group_fetch_desc",
     "preview_search_light.png", ("8.", "13.", "14.")),
    ("inazuma", "help.group_read", "help.group_read_desc",
     "preview_dialogue_light.png", ("9.", "10.")),
    ("sumeru", "help.group_style", "help.group_style_desc",
     "preview_bg_settings_light.png", ("6.", "7.")),
    ("fontaine", "help.group_manage", "help.group_manage_desc",
     "preview_weread_dashboard.png", ("3.", "4.", "5.", "12.")),
    ("natlan", "help.group_advanced", "help.group_advanced_desc",
     "plugins.png", ("11.", "15.", "16.", "17.", "附录")),
)


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


class _AccentDot(QFrame):
    """色彩对话框里的主题色圆点，选中态用白色描边标识。"""

    def __init__(self, key: str, palette: tuple, on_pick, parent=None) -> None:
        super().__init__(parent)
        self.key = key
        self.on_pick = on_pick
        self.setFixedSize(28, 28)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(ACCENT_LABELS.get(key, key))
        main = palette[0] if palette else "#B39DDB"
        gradient = palette[3] if len(palette) > 3 and palette[3] else ""
        bg = ""
        if gradient:
            try:
                from .. import nation_theme as nt
                bg = f"background: {nt.qss_gradient(gradient)};"
            except Exception:  # noqa: BLE001
                bg = ""
        if not bg:
            bg = f"background-color: {main};"
        self._bg = bg
        self._selected = False
        self._paint()

    def _paint(self) -> None:
        border = "#ffffff" if self._selected else "rgba(255,255,255,0.30)"
        self.setStyleSheet(
            f"QFrame {{ {self._bg} border: 2px solid {border}; border-radius: 14px; }}")

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._paint()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.on_pick(self.key)


class HelpColorDialog(QDialog):
    """使用说明页内的色彩修改入口：主题色 + 深浅模式 + 完整配色中心。"""

    def __init__(self, view: "HelpView", parent=None) -> None:
        super().__init__(parent or view)
        self.view = view
        self.store = view.store
        self.setWindowTitle(tr("help.color_title"))
        self.setModal(True)
        self.setMinimumWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        hint = QLabel(tr("help.color_hint"))
        hint.setWordWrap(True)
        root.addWidget(hint)

        root.addWidget(self._section_label(tr("help.color_accent")))
        dot_row = QHBoxLayout()
        dot_row.setSpacing(12)
        self.dots = {}
        cur = str(self.store.settings_get("accent_color", "purple") or "purple")
        for key, palette in ACCENT_PALETTES.items():
            dot = _AccentDot(key, palette, self._pick_accent, self)
            dot.set_selected(key == cur)
            self.dots[key] = dot
            cell = QVBoxLayout()
            cell.setSpacing(4)
            cell.addWidget(dot, 0, Qt.AlignHCenter)
            cap = QLabel(ACCENT_LABELS.get(key, key))
            cap.setAlignment(Qt.AlignHCenter)
            cell.addWidget(cap)
            dot_row.addLayout(cell)
        dot_row.addStretch(1)
        root.addLayout(dot_row)

        root.addWidget(self._section_label(tr("help.color_mode")))
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        self.dark_btn = QPushButton(tr("help.color_dark"))
        self.light_btn = QPushButton(tr("help.color_light"))
        self.dark_btn.setCheckable(True)
        self.light_btn.setCheckable(True)
        self.dark_btn.clicked.connect(lambda: self._set_theme(True))
        self.light_btn.clicked.connect(lambda: self._set_theme(False))
        mode_row.addWidget(self.dark_btn)
        mode_row.addWidget(self.light_btn)
        mode_row.addStretch(1)
        root.addLayout(mode_row)

        root.addStretch(1)

        actions = QHBoxLayout()
        more_btn = QPushButton(tr("help.color_more"))
        more_btn.clicked.connect(self._open_settings)
        close_btn = QPushButton(tr("help.color_close"))
        close_btn.clicked.connect(self.accept)
        actions.addWidget(more_btn)
        actions.addStretch(1)
        actions.addWidget(close_btn)
        root.addLayout(actions)

        self._refresh()
        self._apply_dialog_style()

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet("font-weight: 600; font-size: 13px;")
        return lab

    def _apply_dialog_style(self) -> None:
        dark = getattr(self.view, "_dark", True)
        if dark:
            bg, text, dim = "#211e19", "#e8dfcf", "#a99c86"
            border, btn_bg = "#463f33", "#2b2721"
        else:
            bg, text, dim = "#f5f1e8", "#4a4136", "#9a9082"
            border, btn_bg = "#d8cfbc", "#ffffff"
        accent = getattr(self.view, "_accent", "#B39DDB")
        self.setStyleSheet(
            f"QDialog {{ background: {bg}; }}"
            f"QLabel {{ color: {text}; background: transparent; }}"
            f"QPushButton {{ background: {btn_bg}; color: {text}; "
            f"border: 1px solid {border}; border-radius: 8px; padding: 7px 14px; }}"
            f"QPushButton:checked {{ border: 2px solid {accent}; color: {accent}; "
            f"font-weight: 600; }}"
            f"QPushButton:hover {{ border-color: {accent}; }}")
        for lab in self.findChildren(QLabel):
            if lab.text() == tr("help.color_hint"):
                lab.setStyleSheet(f"color: {dim}; background: transparent;")

    def _refresh(self) -> None:
        cur = str(self.store.settings_get("accent_color", "purple") or "purple")
        for key, dot in self.dots.items():
            dot.set_selected(key == cur)
        dark = getattr(self.view, "_dark", True)
        self.dark_btn.setChecked(bool(dark))
        self.light_btn.setChecked(not dark)

    def _reapply(self) -> None:
        mw = self.view.main_window
        apply = getattr(mw, "apply_style_animated", None)
        if callable(apply):
            apply()
        else:
            legacy = getattr(mw, "_apply_theme_color", None)
            if callable(legacy):
                legacy()
        self.view.apply_colors()
        self._apply_dialog_style()
        self._refresh()

    def _pick_accent(self, key: str) -> None:
        self.store.settings_set("accent_color", key)
        self._reapply()

    def _set_theme(self, dark: bool) -> None:
        mode = "dark" if dark else "light"
        self.store.settings_set("theme_mode", mode)
        mw = self.view.main_window
        setter = getattr(mw, "set_theme_mode", None)
        if callable(setter):
            setter(mode)
        self._reapply()

    def _open_settings(self) -> None:
        mw = self.view.main_window
        tabs = getattr(mw, "tabs", None)
        settings_view = getattr(mw, "settings_view", None)
        if tabs is not None and settings_view is not None:
            idx = tabs.indexOf(settings_view)
            if idx >= 0:
                tabs.setCurrentIndex(idx)
        self.accept()


class HelpView(QWidget):
    """软件内置说明书页面（门户首页 -> 章节列表 -> 正文）。"""

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
        self._font_size = self._load_font_size()
        self._group_cards = []  # (frame, 国徽 label, 标题 label, 简介 label, 缩略 label, 组数据)
        self._chapter_rows = []  # (item, section_index)
        self._current_group = 0
        self._current_section = -1
        self._build_ui()
        self._show_home()
        self.apply_colors()
        self.retranslate()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        # ---- 顶部工具条 ----
        head = QHBoxLayout()
        head.setSpacing(8)

        self.back_btn = QToolButton()
        self.back_btn.setObjectName("helpBack")
        self.back_btn.clicked.connect(self._go_back)
        head.addWidget(self.back_btn)

        self.logo_label = QLabel()
        self.logo_label.setObjectName("helpLogo")  # 小说 Logo，保持不变
        self.logo_label.setFixedSize(30, 30)
        logo_path = resource_path("assets", "app_logo.png")
        if logo_path and os.path.isfile(logo_path):
            pm = QPixmap(logo_path)
            if not pm.isNull():
                self.logo_label.setPixmap(
                    pm.scaled(30, 30, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        head.addWidget(self.logo_label)

        self.title_label = QLabel()
        self.title_label.setObjectName("helpTitle")
        head.addWidget(self.title_label)
        head.addStretch(1)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("helpSearch")
        self.search_edit.setFixedWidth(210)
        self.search_edit.textChanged.connect(self._filter_sections)
        head.addWidget(self.search_edit)

        # 字号控件
        self.font_smaller_btn = QToolButton()
        self.font_smaller_btn.setObjectName("helpFontBtn")
        self.font_smaller_btn.setText("A−")
        self.font_smaller_btn.clicked.connect(lambda: self._change_font_size(-1))
        head.addWidget(self.font_smaller_btn)

        self.font_size_btn = QToolButton()
        self.font_size_btn.setObjectName("helpFontBtn")
        self.font_size_btn.clicked.connect(self._edit_font_size)
        head.addWidget(self.font_size_btn)

        self.font_bigger_btn = QToolButton()
        self.font_bigger_btn.setObjectName("helpFontBtn")
        self.font_bigger_btn.setText("A＋")
        self.font_bigger_btn.clicked.connect(lambda: self._change_font_size(1))
        head.addWidget(self.font_bigger_btn)

        self.font_reset_btn = QToolButton()
        self.font_reset_btn.setObjectName("helpFontBtn")
        self.font_reset_btn.setText("↺")
        self.font_reset_btn.clicked.connect(self._reset_font_size)
        head.addWidget(self.font_reset_btn)
        self._update_font_btn()

        # 色彩修改入口：首次引导用过一次后就消失，这里常驻工具栏
        self.color_btn = QPushButton()
        self.color_btn.setObjectName("helpColorBtn")
        self.color_btn.clicked.connect(self._open_color_dialog)
        head.addWidget(self.color_btn)

        self.open_btn = QPushButton()
        self.open_btn.setObjectName("helpOpenFile")
        self.open_btn.clicked.connect(self._open_external)
        head.addWidget(self.open_btn)

        root.addLayout(head)

        # ---- 页面栈 ----
        self.stack = QStackedWidget()
        self.stack.setObjectName("helpStack")

        # 首页（Hero + 卡片网格，放滚动区里）
        self.home_scroll = QScrollArea()
        self.home_scroll.setObjectName("helpHomeScroll")
        self.home_scroll.setWidgetResizable(True)
        self.home_scroll.setFrameShape(QFrame.NoFrame)
        home_widget = QWidget()
        home_widget.setObjectName("helpHome")
        home = QVBoxLayout(home_widget)
        home.setContentsMargins(26, 20, 26, 26)
        home.setSpacing(16)

        self.hero = QFrame()
        self.hero.setObjectName("helpHero")
        hero_l = QVBoxLayout(self.hero)
        hero_l.setContentsMargins(26, 22, 26, 22)
        hero_l.setSpacing(8)
        self.hero_title = QLabel()
        self.hero_title.setObjectName("heroTitle")
        hero_l.addWidget(self.hero_title)
        self.hero_sub = QLabel()
        self.hero_sub.setObjectName("heroSub")
        self.hero_sub.setWordWrap(True)
        hero_l.addWidget(self.hero_sub)
        self.badge_bar = QHBoxLayout()
        self.badge_bar.setSpacing(8)
        self.badge_version = QLabel()
        self.badge_version.setObjectName("heroBadge")
        self.badge_chapters = QLabel()
        self.badge_chapters.setObjectName("heroBadge")
        self.badge_demos = QLabel()
        self.badge_demos.setObjectName("heroBadge")
        for w in (self.badge_version, self.badge_chapters, self.badge_demos):
            self.badge_bar.addWidget(w)
        self.badge_bar.addStretch(1)
        hero_l.addLayout(self.badge_bar)
        self.hero_start = QPushButton()
        self.hero_start.setObjectName("heroStart")
        self.hero_start.clicked.connect(self._quick_start)
        hero_l.addWidget(self.hero_start, 0, Qt.AlignLeft)
        home.addWidget(self.hero)

        self.grid_head = QLabel()
        self.grid_head.setObjectName("gridHead")
        home.addWidget(self.grid_head)

        grid = QGridLayout()
        grid.setSpacing(14)
        for i, (nation, title_key, desc_key, demo, prefixes) in enumerate(_GROUPS):
            row, col = divmod(i, 3)
            card = QFrame()
            card.setObjectName("helpCard")
            card.setCursor(Qt.PointingHandCursor)
            card.setFixedHeight(184)
            card._nation = nation
            card._title_key = title_key
            card._desc_key = desc_key
            card._demo = demo
            card._prefixes = prefixes
            card._card_index = i
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(0, 0, 0, 10)
            card_layout.setSpacing(0)

            thumb = QLabel()
            thumb.setObjectName("cardThumb")
            thumb.setFixedHeight(82)
            thumb.setAlignment(Qt.AlignCenter)
            tp = resource_path("assets", "demo", demo)
            if tp and os.path.isfile(tp):
                pm = QPixmap(tp)
                if not pm.isNull():
                    thumb.setPixmap(pm.scaled(360, 82, Qt.KeepAspectRatioByExpanding,
                                              Qt.SmoothTransformation))
            card_layout.addWidget(thumb)

            info = QHBoxLayout()
            info.setContentsMargins(12, 8, 12, 0)
            info.setSpacing(10)
            icon = QLabel()
            icon.setObjectName("cardIcon")
            icon.setFixedSize(26, 26)
            info.addWidget(icon, 0, Qt.AlignTop)
            texts = QVBoxLayout()
            texts.setSpacing(2)
            ct = QLabel()
            ct.setObjectName("cardTitle")
            cd = QLabel()
            cd.setObjectName("cardDesc")
            cd.setWordWrap(True)
            texts.addWidget(ct)
            texts.addWidget(cd)
            info.addLayout(texts, 1)
            card_layout.addLayout(info)

            # 鼠标点击进入分区
            card.mousePressEvent = self._make_card_click(i)
            self._group_cards.append((card, icon, ct, cd, thumb,
                                      (nation, title_key, desc_key, demo, prefixes)))
            grid.addWidget(card, row, col)
        home.addLayout(grid)
        home.addStretch(1)
        self.home_scroll.setWidget(home_widget)
        self.stack.addWidget(self.home_scroll)

        # 章节列表页
        self.list_widget = QWidget()
        self.list_widget.setObjectName("helpList")
        list_l = QVBoxLayout(self.list_widget)
        list_l.setContentsMargins(26, 20, 26, 20)
        list_l.setSpacing(12)
        self.list_head = QLabel()
        self.list_head.setObjectName("listHead")
        list_l.addWidget(self.list_head)
        self.chapter_list = QListWidget()
        self.chapter_list.setObjectName("chapterList")
        self.chapter_list.itemClicked.connect(self._on_chapter_clicked)
        list_l.addWidget(self.chapter_list, 1)
        self.stack.addWidget(self.list_widget)

        # 正文页
        article_widget = QWidget()
        article_l = QVBoxLayout(article_widget)
        article_l.setContentsMargins(0, 0, 0, 0)
        article_l.setSpacing(0)
        self.text = QTextBrowser()
        self.text.setObjectName("helpText")
        self.text.setOpenExternalLinks(True)
        self.text.setReadOnly(True)
        self.text.anchorClicked.connect(self._on_anchor_clicked)
        article_l.addWidget(self.text, 1)
        self._article_widget = article_widget
        self.stack.addWidget(article_widget)

        root.addWidget(self.stack, 1)

    # -------------------------------------------------------------- 交互
    def _make_card_click(self, index):
        def handler(event):
            self._show_group(index)
        return handler

    def _quick_start(self) -> None:
        """Hero 快速开始：进入「快速上手」分区。"""
        self._show_group(0)

    def _show_home(self) -> None:
        self.stack.setCurrentWidget(self.home_scroll)
        self.back_btn.setVisible(False)
        self._set_font_controls_visible(False)

    def _show_group(self, group_index: int) -> None:
        """显示某分区的章节列表。"""
        if not (0 <= group_index < len(_GROUPS)):
            return
        nation, title_key, _desc_key, _demo, prefixes = _GROUPS[group_index]
        self._current_group = group_index
        self.list_head.setText(tr(title_key))
        self.chapter_list.clear()
        self._chapter_rows = []
        for i, (title, content) in enumerate(self._sections):
            if title in ("全文", "概览"):
                continue
            if any(title.startswith(p) for p in prefixes):
                # 取正文第一句作为副标题，让章节列表更饱满、更像页面卡片
                summary = ""
                for line in content.splitlines():
                    line = line.strip().lstrip("#>-*").strip()
                    if line and not line.startswith("```") and len(line) > 6:
                        summary = line[:48] + ("…" if len(line) > 48 else "")
                        break
                item = QListWidgetItem()
                item.setData(Qt.UserRole, i)
                item.setToolTip(content[:240])
                if summary:
                    item.setText(f"{title}　·　{summary}")
                else:
                    item.setText(title)
                item.setSizeHint(QSize(10, 44))
                icon = nation_icon(nation, self._accent, 20)
                if not icon.isNull():
                    item.setIcon(icon)
                self.chapter_list.addItem(item)
                self._chapter_rows.append((item, i))
        self.stack.setCurrentWidget(self.list_widget)
        self.back_btn.setVisible(True)
        self._set_font_controls_visible(False)

    def _on_chapter_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole)
        if data is None:
            return
        idx = int(data)
        self._current_section = idx
        self._select_index(idx)
        self.stack.setCurrentWidget(self._article_widget)
        self.back_btn.setVisible(True)
        self._set_font_controls_visible(True)

    def _go_back(self) -> None:
        """从正文/列表返回：正文 -> 该分区列表；列表 -> 首页。"""
        if self.stack.currentWidget() is self._article_widget:
            self._show_group(self._current_group)
        else:
            self._show_home()

    def _set_font_controls_visible(self, visible: bool) -> None:
        for btn in (self.font_smaller_btn, self.font_size_btn,
                    self.font_bigger_btn, self.font_reset_btn):
            btn.setVisible(visible)

    def _select_index(self, index: int) -> None:
        if not (0 <= index < len(self._sections)):
            return
        _title, content = self._sections[index]
        # 先用 markdown 渲染纯正文，再用 QTextCursor 精确插入演示图：
        # Qt 的 markdown 渲染器忽略 HTML 的 align/style，且 <img> 与相邻
        # 列表/表格易串位，故改用 Qt 原生富文本插入以保证居中与独立成块。
        self.text.setMarkdown(content)
        self._insert_demo_images(content)
        bar = self.text.verticalScrollBar()
        if bar is not None:
            bar.setValue(0)

    def _insert_demo_images(self, markdown: str) -> None:
        """在渲染后的文档里，按小节标题定位并插入居中的演示图与提示。"""
        from PySide6.QtGui import QTextImageFormat, QTextCharFormat, QTextBlockFormat, QTextCursor
        from PySide6.QtCore import Qt as _Qt

        doc = self.text.document()
        desired: List[Tuple[str, str]] = []
        for line in markdown.splitlines():
            m = re.match(r"^#{3,4}\s+(.+?)\s*$", line)
            if not m:
                continue
            title = m.group(1).strip()
            image = demo_image(title)
            if not image:
                continue
            path = resource_path("assets", "demo", image)
            if path and os.path.isfile(path):
                desired.append((title, path))
        if not desired:
            return

        for title, path in reversed(desired):
            cursor = doc.find(title)
            if cursor.isNull():
                continue
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)

            block_fmt = QTextBlockFormat()
            block_fmt.setAlignment(_Qt.AlignmentFlag.AlignCenter)
            block_fmt.setTopMargin(8)
            block_fmt.setBottomMargin(2)

            img_fmt = QTextImageFormat()
            file_url = "file:///" + path.replace("\\", "/").lstrip("/")
            img_fmt.setName(file_url)
            img_fmt.setWidth(380)

            anchor_fmt = QTextCharFormat()
            anchor_fmt.setAnchor(True)
            anchor_fmt.setAnchorHref(_DEMO_SCHEME + path.replace("\\", "/"))

            cursor.insertBlock(block_fmt)
            cursor.setCharFormat(anchor_fmt)
            cursor.insertImage(img_fmt)

            tip_fmt = QTextBlockFormat()
            tip_fmt.setAlignment(_Qt.AlignmentFlag.AlignCenter)
            tip_fmt.setBottomMargin(8)
            cursor.insertBlock(tip_fmt)
            tip_char = QTextCharFormat(anchor_fmt)
            tip_char.setFontPointSize(10)
            tip_char.setForeground(self.palette().color(self.foregroundRole()))
            cursor.setCharFormat(tip_char)
            cursor.insertText(f'▶ {tr("help.demo_label")} · {tr("help.demo_tip")}')

    def _current_content(self) -> str:
        if 0 <= self._current_section < len(self._sections) and \
                self.stack.currentWidget() is self._article_widget:
            return self._sections[self._current_section][1]
        return self._markdown

    def _open_external(self) -> None:
        path = manual_path()
        if not path:
            QMessageBox.warning(self, tr("common.tip"), tr("help.file_missing"))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_color_dialog(self) -> None:
        """打开色彩修改对话框（主题色 / 深浅模式 / 完整配色中心）。"""
        dlg = HelpColorDialog(self, self)
        dlg.exec()

    # -------------------------------------------------------------- 搜索过滤
    def _filter_sections(self, keyword: str) -> None:
        """搜索：有关键词时在正文页显示匹配的章节列表；清空则回首页。"""
        kw = (keyword or "").strip().lower()
        if not kw:
            self._show_home()
            return
        self.chapter_list.clear()
        self._chapter_rows = []
        for i, (title, content) in enumerate(self._sections):
            if kw in title.lower() or kw in content.lower():
                summary = ""
                for line in content.splitlines():
                    line = line.strip().lstrip("#>-*").strip()
                    if line and not line.startswith("```") and len(line) > 6:
                        summary = line[:48] + ("…" if len(line) > 48 else "")
                        break
                item = QListWidgetItem(f"{title}　·　{summary}" if summary else title)
                item.setData(Qt.UserRole, i)
                item.setToolTip(content[:240])
                item.setSizeHint(QSize(10, 44))
                icon = nation_icon("fontaine", self._accent, 20)
                if not icon.isNull():
                    item.setIcon(icon)
                self.chapter_list.addItem(item)
                self._chapter_rows.append((item, i))
        self.list_head.setText(tr("help.search_results"))
        if not self._chapter_rows:
            item = QListWidgetItem(tr("help.no_result"))
            item.setFlags(Qt.NoItemFlags)
            self.chapter_list.addItem(item)
        self.stack.setCurrentWidget(self.list_widget)
        self.back_btn.setVisible(True)
        self._set_font_controls_visible(False)

    # -------------------------------------------------------------- 字号调节
    def _load_font_size(self) -> int:
        try:
            val = int(self.store.settings_get(_FONT_SIZE_KEY, _FONT_DEFAULT))
        except (TypeError, ValueError):
            return _FONT_DEFAULT
        return max(_FONT_MIN, min(_FONT_MAX, val))

    def _save_font_size(self) -> None:
        try:
            self.store.settings_set(_FONT_SIZE_KEY, self._font_size)
        except Exception:  # noqa: BLE001
            pass

    def _update_font_btn(self) -> None:
        self.font_size_btn.setText(str(self._font_size))
        self.font_smaller_btn.setEnabled(self._font_size > _FONT_MIN)
        self.font_bigger_btn.setEnabled(self._font_size < _FONT_MAX)

    def _change_font_size(self, delta: int) -> None:
        new = max(_FONT_MIN, min(_FONT_MAX, self._font_size + delta))
        if new == self._font_size:
            return
        self._font_size = new
        self._save_font_size()
        self._update_font_btn()
        self._apply_font_size()

    def _reset_font_size(self) -> None:
        self._font_size = _FONT_DEFAULT
        self._save_font_size()
        self._update_font_btn()
        self._apply_font_size()

    def _edit_font_size(self) -> None:
        presets = [12, 14, 16, 18, 20]
        if self._font_size in presets:
            idx = (presets.index(self._font_size) + 1) % len(presets)
        else:
            idx = 0
        self._font_size = presets[idx]
        self._save_font_size()
        self._update_font_btn()
        self._apply_font_size()

    def _apply_font_size(self) -> None:
        self.apply_colors()
        idx = self._current_section
        if 0 <= idx < len(self._sections):
            self._select_index(idx)

    # -------------------------------------------------------------- 演示图放大
    def _on_anchor_clicked(self, url: QUrl) -> None:
        url_str = url.toString()
        if not url_str.startswith(_DEMO_SCHEME):
            return
        path = url_str[len(_DEMO_SCHEME):]
        if not os.path.isfile(path):
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("help.demo_label"))
        dlg.setMinimumSize(640, 480)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            screen = QApplication.primaryScreen()
            max_h = screen.size().height() - 120 if screen else 720
            max_w = screen.size().width() - 120 if screen else 1000
            scaled = pixmap.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            label.setPixmap(scaled)
        scroll.setWidget(label)
        layout.addWidget(scroll)
        dlg.exec()

    # -------------------------------------------------------------- 主题适配
    _SERIF = "'Noto Serif CJK SC', 'Songti SC', 'STSong', serif"

    def apply_colors(self) -> None:
        """跟随全局深浅主题与强调色刷新配色（门户首页 + 正文，深浅双版）。"""
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
            page_bg = "#211e19"
            card_bg, body_bg = "#2b2721", "#302b23"
            border = "#463f33"
            text, dim = "#e8dfcf", "#a99c86"
            hover = "#3a342a"
            link = "#d8b981"
            btn_bg, btn_text = "#2b2721", "#c9bda6"
            hero_a, hero_b = "#3a2a6b", "#7d5cff"
            thumb_bg = "#12101a"
        else:
            page_bg = "#eae5d9"
            card_bg, body_bg = "#f5f1e8", "#fbf8f0"
            border = "#d8cfbc"
            text, dim = "#4a4136", "#9a9082"
            hover = "#ebe3d2"
            link = "#8a6d3b"
            btn_bg, btn_text = "#f5f1e8", "#6b6053"
            hero_a, hero_b = "#5b3fb0", "#9a7cff"
            thumb_bg = "#e2dccb"

        card_bg = nation_theme.resolved_card_bg(card_bg)
        body_bg = nation_theme.resolved_card_bg(body_bg)
        serif = self._SERIF
        scroll_qss = modern_scrollbar_qss(self._dark)

        self.setStyleSheet(
            f"QWidget#helpPage {{ background: {page_bg}; }}"
            f"QWidget#helpHome, QWidget#helpList {{ background: {page_bg}; }}"
            + scroll_qss
        )
        self.title_label.setStyleSheet(
            f"color: {text}; font-size: 18px; font-weight: 600; background: transparent; "
            f"font-family: {serif}; letter-spacing: 1px;")
        self.search_edit.setStyleSheet(
            f"QLineEdit {{ background: {card_bg}; color: {text}; border: 1px solid {border}; "
            f"border-radius: 18px; padding: 7px 14px; font-family: {serif}; font-size: 12px; }}"
            f"QLineEdit:focus {{ border: 1px solid {link}; }}")
        self.text.setStyleSheet(
            f"QTextBrowser {{ background: {body_bg}; color: {text}; border: 1px solid {border}; "
            f"border-radius: 14px; padding: 28px 36px; font-size: {self._font_size}px; "
            f"font-family: {serif}; }}"
            f"QTextBrowser a {{ color: {link}; }}" + scroll_qss)
        self.chapter_list.setStyleSheet(
            f"QListWidget {{ background: {card_bg}; color: {text}; border: 1px solid {border}; "
            f"border-radius: 14px; padding: 8px; font-family: {serif}; font-size: 13px; }}"
            f"QListWidget::item {{ border-radius: 10px; padding: 10px 12px; }}"
            f"QListWidget::item:hover {{ background: {hover}; }}"
            f"QListWidget::item:selected {{ background: {link}; color: {page_bg}; font-weight: bold; }}"
            + scroll_qss)

        # Hero
        self.hero.setStyleSheet(
            f"QFrame#helpHero {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            f"stop:0 {hero_a}, stop:1 {hero_b}); border-radius: 16px; }}")
        self.hero_title.setStyleSheet(
            f"color: #ffffff; font-size: 24px; font-weight: 700; background: transparent; "
            f"font-family: {serif};")
        self.hero_sub.setStyleSheet(
            f"color: rgba(255,255,255,0.86); font-size: 13px; background: transparent; "
            f"font-family: {serif};")
        badge_style = (
            f"QLabel#heroBadge {{ color: #ffffff; background: rgba(255,255,255,0.16); "
            f"border-radius: 12px; padding: 4px 12px; font-size: 11px; font-family: {serif}; }}")
        for w in (self.badge_version, self.badge_chapters, self.badge_demos):
            w.setStyleSheet(badge_style)
        self.hero_start.setStyleSheet(
            f"QPushButton#heroStart {{ background: #ffffff; color: {hero_a}; border: none; "
            f"border-radius: 10px; padding: 9px 20px; font-weight: 700; font-size: 13px; "
            f"font-family: {serif}; }}"
            f"QPushButton#heroStart:hover {{ background: rgba(255,255,255,0.85); }}")
        # 快速开始按钮图标用国徽（小说 Logo 保持不变，其余功能图标均为国徽）
        hero_icon = nation_icon("mondstadt", hero_a, 16)
        if not hero_icon.isNull():
            self.hero_start.setIcon(hero_icon)
            self.hero_start.setIconSize(QSize(16, 16))

        self.grid_head.setStyleSheet(
            f"color: {text}; font-size: 15px; font-weight: 700; background: transparent; "
            f"font-family: {serif}; padding-left: 4px;")
        self.list_head.setStyleSheet(
            f"color: {text}; font-size: 20px; font-weight: 700; background: transparent; "
            f"font-family: {serif};")

        # 卡片
        for card, icon_l, ct, cd, thumb, data in self._group_cards:
            nation, title_key, desc_key, _demo, _prefixes = data
            card.setStyleSheet(
                f"QFrame#helpCard {{ background: {card_bg}; border: 1px solid {border}; "
                f"border-radius: 14px; }}"
                f"QFrame#helpCard:hover {{ border: 1px solid {self._accent}; "
                f"background: {hover}; }}"
                f"QLabel#cardThumb {{ background: {thumb_bg}; border: none; }}"
                f"QLabel#cardTitle {{ color: {text}; font-size: 13px; font-weight: 700; "
                f"font-family: {serif}; background: transparent; }}"
                f"QLabel#cardDesc {{ color: {dim}; font-size: 11px; font-family: {serif}; "
                f"background: transparent; }}")
            icon_l.setPixmap(nation_icon(nation, self._accent, 26).pixmap(26, 26))

        # 工具按钮
        self.open_btn.setStyleSheet(
            f"QPushButton#helpOpenFile {{ background: {btn_bg}; color: {btn_text}; "
            f"border: 1px solid {border}; border-radius: 18px; padding: 7px 16px; "
            f"font-family: {serif}; font-size: 12px; }}"
            f"QPushButton#helpOpenFile:hover {{ background: {hover}; }}")
        # 色彩入口用强调色实心按钮，避免像初版那样选完就找不到了
        self.color_btn.setStyleSheet(
            f"QPushButton#helpColorBtn {{ background: {self._accent}; color: #ffffff; "
            f"border: none; border-radius: 18px; padding: 7px 16px; "
            f"font-family: {serif}; font-size: 12px; font-weight: 600; }}"
            f"QPushButton#helpColorBtn:hover {{ opacity: 0.85; }}")
        for btn in (self.back_btn, self.font_smaller_btn, self.font_size_btn,
                    self.font_bigger_btn, self.font_reset_btn):
            btn.setStyleSheet(
                f"QToolButton {{ background: {btn_bg}; color: {btn_text}; "
                f"border: 1px solid {border}; border-radius: 10px; padding: 4px 9px; "
                f"font-family: {serif}; font-size: 13px; min-width: 24px; }}"
                f"QToolButton:hover {{ background: {hover}; }}"
                f"QToolButton:disabled {{ opacity: 0.4; }}")

        # 章节列表图标随主题色刷新
        for item, _idx in self._chapter_rows:
            if item.icon() and not item.icon().isNull():
                item.setIcon(nation_icon("fontaine", self._accent, 20))

        self.home_scroll.setStyleSheet(scroll_qss)

    def refresh_theme(self, dark: bool) -> None:
        """兼容主窗口主题刷新调用。"""
        self._dark = bool(dark)
        self.apply_colors()

    def retranslate(self) -> None:
        """语言切换后刷新所有文案。"""
        self.window_title = tr("nav.help")
        self.setWindowTitle(self.window_title)
        self.title_label.setText(tr("help.title"))
        self.search_edit.setPlaceholderText(tr("help.search_placeholder"))
        self.color_btn.setText(tr("help.color_btn"))
        self.color_btn.setToolTip(tr("help.color_title"))
        self.open_btn.setText(tr("help.open_file"))
        self.back_btn.setText("‹ " + tr("help.back"))
        self.back_btn.setToolTip(tr("help.back"))
        self.font_smaller_btn.setToolTip(tr("help.font_smaller"))
        self.font_size_btn.setToolTip(tr("help.font_size"))
        self.font_bigger_btn.setToolTip(tr("help.font_bigger"))
        self.font_reset_btn.setToolTip(tr("help.font_reset"))

        self.hero_title.setText(tr("help.title"))
        self.hero_sub.setText(tr("help.hero_sub"))
        self.badge_version.setText(tr("help.badge_version", v=VERSION_LABEL))
        self.badge_chapters.setText(tr("help.badge_chapters", n=len(self._sections)))
        demo_total = len({v for v in DEMOS.values() if v})
        self.badge_demos.setText(tr("help.badge_demos", n=demo_total))
        self.hero_start.setText(tr("help.hero_start"))
        self.grid_head.setText(tr("help.group_manage") + " · " +
                               tr("help.section_count_short", n=len(_GROUPS)))

        for card, _icon, ct, cd, _thumb, data in self._group_cards:
            _nation, title_key, desc_key, _demo, _prefixes = data
            ct.setText(tr(title_key))
            cd.setText(tr(desc_key))
