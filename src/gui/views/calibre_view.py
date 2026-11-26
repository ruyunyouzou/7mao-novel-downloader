# -*- coding: utf-8 -*-
"""
Calibre 接入页：管理 Calibre 书库、用 ebook-edit 随时编辑 EPUB、格式转换。

- 自动探测本机 Calibre 安装与书库，也允许手动指定路径
- 书库列表只读解析 metadata.db（不破坏 Calibre 数据结构）
- 双击书籍或点「编辑 EPUB」调用 ebook-edit 可视化修改并原位保存

视觉：BC 混合方案
- 方案 B：连接状态大卡片 + 高级设置抽屉 + 分组工具栏 + 统计胶囊 + 空状态引导
- 方案 C：三张统计卡 + 左侧书籍列表 + 右侧书籍详情预览
- 配色跟随全局深浅主题与主题色（不再固定深紫橙）
"""

from __future__ import annotations

import os
from typing import List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap, QPainter, QColor, QLinearGradient, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMessageBox, QProgressBar, QFrame, QStackedWidget,
)

from ..datastore import DataStore
from .. import filedialogs as _fd
from ..i18n import tr
from ..style import accent_color, BG as _BG_DARK, BG_CARD as _CARD_DARK, BORDER as _LINE_DARK, TEXT as _TEXT_DARK, TEXT_DIM as _DIM_DARK, BG_INPUT as _INPUT_DARK
from ..widgets import apply_nation_icon, nation_icon
from ..calibre_core import (
    CalibreError, detect_calibre, detect_library, list_books, book_file,
    open_editor, convert_format, add_to_library, export_from_library,
    CalibreInstall, BookEntry,
)


# ---- 明暗两套配色（跟随全局主题） ----
def _palette(dark: bool, accent: str) -> dict:
    from .. import nation_theme
    p = _palette_base(dark, accent)
    # 功能背景：用户自定义卡片色覆盖（收集完再覆盖一次，避免多处遗漏）
    p["card"] = nation_theme.resolved_card_bg(p["card"])
    return p


def _palette_base(dark: bool, accent: str) -> dict:
    if dark:
        return dict(
            page=_BG_DARK, card=_CARD_DARK, input=_INPUT_DARK,
            line=_LINE_DARK, text=_TEXT_DARK, dim=_DIM_DARK,
            hover="#2f2f2f", accent=accent,
            ok="#81c784", warn="#ffb74d", err="#ef5350",
            conn="rgba(179,157,219,0.10)" if accent.lower() == "#b39ddb"
                 else _with_alpha(accent, 26),
            chip=_with_alpha(accent, 30),
            cover1=accent,
        )
    return dict(
        page="#f4f2f7", card="#ffffff", input="#ffffff",
        line="#ddd8e3", text="#241f2c", dim="#746c80",
        hover="#eeebf3", accent=accent,
        ok="#2e7d32", warn="#e07b00", err="#c62828",
        conn=_with_alpha(accent, 20),
        chip=_with_alpha(accent, 22),
        cover1=accent,
    )


def _with_alpha(color: str, alpha: int) -> str:
    c = QColor(color)
    c.setAlpha(alpha)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha / 255:.2f})"


def _lighten(color: str, factor: int = 115) -> str:
    return QColor(color).lighter(factor).name()


def _lbl(text: str = "", color: str = "#e6e6e6", size: int = 13,
         bold: bool = False, wrap: bool = False) -> QLabel:
    q = QLabel(text)
    css = f"color:{color};font-size:{size}px;background:transparent;border:none;"
    if bold:
        css += "font-weight:bold;"
    q.setStyleSheet(css)
    q.setWordWrap(wrap)
    return q


def _btn(text: str, primary: bool = False, color: str = "#b39ddb") -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    if primary:
        b.setStyleSheet(
            f"QPushButton{{background-color:{color};color:#1a1a1a;border:none;"
            "border-radius:8px;padding:8px 16px;font-weight:bold;}"
            f"QPushButton:hover{{background-color:{_lighten(color)};}}"
            "QPushButton:disabled{background-color:#5a5560;color:#ddd;}"
        )
        b.setProperty("accent", "true")
    else:
        b.setStyleSheet(
            f"QPushButton{{background-color:transparent;color:{color};"
            f"border:1px solid {color};border-radius:8px;padding:7px 14px;}}"
            f"QPushButton:hover{{background-color:{_with_alpha(color, 40)};}}"
            "QPushButton:disabled{color:#7a7480;border-color:#555;}"
        )
    return b


class LoadBooksWorker(QThread):
    finished = Signal(object)   # list[BookEntry]
    failed = Signal(str)

    def __init__(self, library: str, keyword: str, parent=None) -> None:
        super().__init__(parent)
        self.library = library
        self.keyword = keyword

    def run(self) -> None:
        try:
            self.finished.emit(list_books(self.library, keyword=self.keyword))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class CalibreView(QWidget):
    """Calibre 书库管理页（BC 混合美化版）。"""

    def __init__(self, store: DataStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.setObjectName("calibrePage")
        self.install: CalibreInstall = CalibreInstall()
        self.books: List[BookEntry] = []
        self._worker: Optional[LoadBooksWorker] = None
        self._dark = str(store.settings_get("theme_mode", "dark") or "dark") != "light"
        self._accent = accent_color(str(store.settings_get("accent_color", "purple") or "purple"))
        self._palette = _palette(self._dark, self._accent)
        self._build_ui()
        self._load_settings()
        self.apply_colors()
        self._detect()

    # ------------------------------------------------------------------ 主题

    def apply_colors(self) -> None:
        """跟随全局深浅主题与主题色重绘本页（主窗口切换主题时调用）。"""
        self._dark = str(self.store.settings_get("theme_mode", "dark") or "dark") != "light"
        self._accent = accent_color(str(self.store.settings_get("accent_color", "purple") or "purple"))
        self._palette = _palette(self._dark, self._accent)
        p = self._palette
        self.setStyleSheet(f"QWidget#calibrePage{{background-color:{p['page']};}}")

        # 顶部标题
        self.title_lbl.setStyleSheet(
            f"color:{p['text']};font-size:22px;font-weight:bold;background:transparent;border:none;")
        self.subtitle_lbl.setStyleSheet(
            f"color:{p['dim']};font-size:12px;background:transparent;border:none;")
        self.badge_lbl.setStyleSheet(
            f"color:{p['warn']};font-size:11px;border:1px solid {_with_alpha(p['warn'], 120)};"
            f"border-radius:12px;padding:3px 10px;background:{_with_alpha(p['warn'], 24)};")

        # 连接状态卡
        self.conn_card.setStyleSheet(
            f"QFrame#connCard{{background-color:{p['conn']};border:1px solid {_with_alpha(p['accent'], 130)};"
            "border-radius:12px;}")
        self.conn_title.setStyleSheet(
            f"color:{p['text']};font-size:14px;font-weight:bold;background:transparent;border:none;")
        self.conn_path.setStyleSheet(
            f"color:{p['dim']};font-size:11px;background:transparent;border:none;")
        self.conn_state.setStyleSheet(
            f"color:{p['ok'] if self.install.available else p['warn']};font-size:11px;font-weight:bold;"
            "background:transparent;border:none;")
        self.conn_icon_lbl.setPixmap(nation_icon("fontaine", p["accent"], 30).pixmap(30, 30))
        self.btn_redetect.setStyleSheet(
            f"QPushButton{{background-color:transparent;color:{p['accent']};"
            f"border:1px solid {p['accent']};border-radius:8px;padding:7px 13px;}}"
            f"QPushButton:hover{{background-color:{_with_alpha(p['accent'], 40)};}}")

        # 高级设置抽屉
        self.path_box.setStyleSheet(
            f"QGroupBox{{color:{p['dim']};font-size:12px;border:1px solid {p['line']};"
            "border-radius:8px;margin-top:6px;}"
            f"QGroupBox::title{{subcontrol-origin:margin;left:10px;padding:0 4px;color:{p['accent']};}}"
            f"QLabel{{color:{p['dim']};font-size:11px;background:transparent;border:none;}}"
            f"QLineEdit{{background-color:{p['input']};color:{p['text']};"
            f"border:1px solid {p['line']};border-radius:7px;padding:6px 10px;}}")

        # 输入框与按钮
        self.search_edit.setStyleSheet(
            f"QLineEdit{{background-color:{p['input']};color:{p['text']};"
            f"border:1px solid {p['line']};border-radius:7px;padding:6px 10px;}}")
        for b in (self.btn_bin, self.btn_lib, self.btn_save, self.btn_search,
                  self.btn_convert, self.btn_add, self.btn_export,
                  self.btn_detail_edit, self.btn_detail_convert, self.btn_detail_export):
            self._style_secondary(b, p)
        self._style_primary(self.btn_edit, p)
        self._style_primary(self.btn_detail_edit, p)
        # 详情面板的三个操作按钮单独放大，填充面板空间
        self.btn_detail_edit.setStyleSheet(
            f"QPushButton{{background-color:{p['accent']};color:#1a1a1a;border:none;"
            "border-radius:9px;padding:11px 10px;font-size:13px;font-weight:bold;}"
            f"QPushButton:hover{{background-color:{_lighten(p['accent'])};}}"
            "QPushButton:disabled{background-color:#5a5560;color:#ddd;}")
        self.btn_detail_edit.setProperty("accent", "true")
        for b in (self.btn_detail_convert, self.btn_detail_export):
            b.setStyleSheet(
                f"QPushButton{{background-color:transparent;color:{p['accent']};"
                f"border:1px solid {p['accent']};border-radius:9px;padding:10px 8px;font-size:13px;}}"
                f"QPushButton:hover{{background-color:{_with_alpha(p['accent'], 40)};}}"
                "QPushButton:disabled{color:#7a7480;border-color:#555;}")
            b.setProperty("accent", "false")
        for sep in self._seps:
            sep.setStyleSheet(f"background-color:{p['line']};")

        # 统计卡
        for card in (self.stat_total, self.stat_epub, self.stat_format):
            card.setStyleSheet(
                f"QFrame#statCard{{background-color:{p['card']};border:1px solid {p['line']};"
                "border-radius:10px;}")
            card.findChild(QLabel, "num").setStyleSheet(
                f"color:{p['accent']};font-size:19px;font-weight:bold;background:transparent;border:none;")
            card.findChild(QLabel, "cap").setStyleSheet(
                f"color:{p['dim']};font-size:10px;background:transparent;border:none;")

        # 书籍列表 + 详情
        self.table.setStyleSheet(
            f"QTableWidget{{background-color:{p['card']};color:{p['text']};"
            f"border:1px solid {p['line']};border-radius:10px;gridline-color:{p['line']};}}"
            f"QHeaderView::section{{background-color:{p['input']};color:{p['dim']};"
            "border:none;padding:6px;font-size:11px;}"
            f"QTableWidget::item{{padding:6px;border-bottom:1px solid {p['line']};}}"
            f"QTableWidget::item:selected{{background-color:{_with_alpha(p['accent'], 70)};color:{p['text']};}}")
        self.detail_panel.setStyleSheet(
            f"QFrame#detailPanel{{background-color:{p['card']};border:1px solid {p['line']};"
            "border-radius:10px;}")
        self.detail_title.setStyleSheet(
            f"color:{p['text']};font-size:22px;font-weight:bold;background:transparent;border:none;")
        for lbl in self._detail_meta_labels:
            lbl.setStyleSheet(
                f"color:{p['dim']};font-size:14px;background:transparent;border:none;")
        self.detail_empty_title.setStyleSheet(
            f"color:{p['dim']};font-size:16px;font-weight:bold;background:transparent;border:none;")
        self.detail_empty_hint.setStyleSheet(
            f"color:{p['dim']};font-size:13px;background:transparent;border:none;")
        self.empty_title.setStyleSheet(
            f"color:{p['text']};font-size:16px;font-weight:bold;background:transparent;border:none;")
        self.empty_hint.setStyleSheet(
            f"color:{p['dim']};font-size:12px;background:transparent;border:none;")
        self.empty_icon.setPixmap(nation_icon("liyue", p["accent"], 42).pixmap(42, 42))
        self.btn_empty_add.setStyleSheet(
            f"QPushButton{{background-color:{p['accent']};color:#1a1a1a;border:none;"
            "border-radius:8px;padding:8px 16px;font-weight:bold;}"
            f"QPushButton:hover{{background-color:{_lighten(p['accent'])};}}")

        self.progress.setStyleSheet(
            f"QProgressBar{{background-color:{p['input']};border:none;border-radius:3px;}}"
            f"QProgressBar::chunk{{background-color:{p['accent']};border-radius:3px;}}")
        self.log_lbl.setStyleSheet(
            f"color:{p['dim']};font-size:11px;background:transparent;border:none;")

        self._refresh_cover()

    @staticmethod
    def _style_primary(b: QPushButton, p: dict) -> None:
        b.setStyleSheet(
            f"QPushButton{{background-color:{p['accent']};color:#1a1a1a;border:none;"
            "border-radius:8px;padding:8px 15px;font-weight:bold;}"
            f"QPushButton:hover{{background-color:{_lighten(p['accent'])};}}"
            "QPushButton:disabled{background-color:#5a5560;color:#ddd;}")
        b.setProperty("accent", "true")

    @staticmethod
    def _style_secondary(b: QPushButton, p: dict) -> None:
        b.setStyleSheet(
            f"QPushButton{{background-color:transparent;color:{p['accent']};"
            f"border:1px solid {p['accent']};border-radius:8px;padding:7px 13px;}}"
            f"QPushButton:hover{{background-color:{_with_alpha(p['accent'], 40)};}}"
            "QPushButton:disabled{color:#7a7480;border-color:#555;}")
        b.setProperty("accent", "false")

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(9)

        # ---- 标题 ----
        head = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(2)
        self.title_lbl = _lbl(tr("cb.title"), "#e6e6e6", 22, bold=True)
        self.subtitle_lbl = _lbl(tr("cb.subtitle"), "#9a9a9a", 12)
        left.addWidget(self.title_lbl)
        left.addWidget(self.subtitle_lbl)
        head.addLayout(left)
        head.addStretch()
        self.badge_lbl = _lbl(tr("cb.badge"), "#ffb74d", 11)
        head.addWidget(self.badge_lbl)
        root.addLayout(head)

        # ---- 连接状态大卡片 ----
        self.conn_card = QFrame()
        self.conn_card.setObjectName("connCard")
        conn = QHBoxLayout(self.conn_card)
        conn.setContentsMargins(14, 11, 14, 11)
        conn.setSpacing(12)
        self.conn_icon_lbl = QLabel()
        self.conn_icon_lbl.setFixedSize(30, 30)
        conn.addWidget(self.conn_icon_lbl)
        cmain = QVBoxLayout()
        cmain.setSpacing(3)
        c1 = QHBoxLayout()
        self.conn_title = _lbl(tr("cb.detecting"), "#e6e6e6", 14, bold=True)
        self.conn_state = _lbl("● " + tr("cb.detecting"), "#ffb74d", 11, bold=True)
        c1.addWidget(self.conn_title)
        c1.addWidget(self.conn_state)
        c1.addStretch()
        cmain.addLayout(c1)
        self.conn_path = _lbl("", "#9a9a9a", 11)
        cmain.addWidget(self.conn_path)
        conn.addLayout(cmain, 1)
        self.btn_redetect = QPushButton(tr("cb.redetect"))
        self.btn_redetect.setCursor(Qt.PointingHandCursor)
        self.btn_redetect.clicked.connect(self._detect)
        conn.addWidget(self.btn_redetect)
        root.addWidget(self.conn_card)

        # ---- 高级设置抽屉 ----
        from PySide6.QtWidgets import QGroupBox
        self.path_box = QGroupBox(tr("cb.advanced"))
        self.path_box.setCheckable(True)
        # 默认勾选：未勾选时 Qt 会禁用 GroupBox 内全部子控件，导致
        # Bin/书库输入框与「浏览…/保存并重新检测」按钮全部点不动。
        # 自动探测失败时用户必须能手动指定路径，故默认展开且可用。
        self.path_box.setChecked(True)
        form = QVBoxLayout(self.path_box)
        form.setContentsMargins(12, 10, 12, 10)
        r1 = QHBoxLayout()
        self.bin_lbl = _lbl(tr("cb.bin_dir"), "#9a9a9a", 11)
        r1.addWidget(self.bin_lbl, 0)
        self.bin_edit = QLineEdit()
        self.bin_edit.setPlaceholderText(tr("cb.bin_ph"))
        r1.addWidget(self.bin_edit, 1)
        self.btn_bin = QPushButton(tr("cb.browse"))
        self.btn_bin.setCursor(Qt.PointingHandCursor)
        self.btn_bin.clicked.connect(self._choose_bin)
        r1.addWidget(self.btn_bin)
        form.addLayout(r1)
        r2 = QHBoxLayout()
        self.lib_lbl = _lbl(tr("cb.lib_dir"), "#9a9a9a", 11)
        r2.addWidget(self.lib_lbl, 0)
        self.lib_edit = QLineEdit()
        self.lib_edit.setPlaceholderText(tr("cb.lib_ph"))
        r2.addWidget(self.lib_edit, 1)
        self.btn_lib = QPushButton(tr("cb.browse"))
        self.btn_lib.setCursor(Qt.PointingHandCursor)
        self.btn_lib.clicked.connect(self._choose_lib)
        r2.addWidget(self.btn_lib)
        form.addLayout(r2)
        r3 = QHBoxLayout()
        self.btn_save = QPushButton(tr("cb.save_detect"))
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.clicked.connect(self._save_and_detect)
        r3.addWidget(self.btn_save)
        r3.addStretch()
        form.addLayout(r3)
        root.addWidget(self.path_box)

        # ---- 分组工具栏 ----
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("cb.search_ph"))
        self.search_edit.returnPressed.connect(self.reload_books)
        bar.addWidget(self.search_edit, 1)
        self.btn_search = QPushButton(tr("cb.search"))
        self.btn_search.setCursor(Qt.PointingHandCursor)
        self.btn_search.clicked.connect(self.reload_books)
        bar.addWidget(self.btn_search)
        self._seps = []
        for _ in range(2):
            sep = QFrame()
            sep.setFixedSize(1, 22)
            self._seps.append(sep)
            bar.addWidget(sep)
        self.btn_edit = QPushButton(tr("cb.edit_epub"))
        self.btn_edit.setCursor(Qt.PointingHandCursor)
        apply_nation_icon(self.btn_edit, "fontaine", "#1a1a1a", 16)
        self.btn_edit._nation = "fontaine"
        self.btn_edit._nation_fixed_color = True
        self.btn_edit._nation_size = 16
        self.btn_edit.clicked.connect(self.edit_selected)
        bar.addWidget(self.btn_edit)
        self.btn_convert = QPushButton(tr("cb.convert"))
        self.btn_convert.setCursor(Qt.PointingHandCursor)
        self.btn_convert.clicked.connect(self.convert_selected)
        bar.addWidget(self.btn_convert)
        self.btn_add = QPushButton(tr("cb.add"))
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.clicked.connect(self.add_to_lib)
        bar.addWidget(self.btn_add)
        self.btn_export = QPushButton(tr("cb.export"))
        self.btn_export.setCursor(Qt.PointingHandCursor)
        self.btn_export.clicked.connect(self.export_selected)
        bar.addWidget(self.btn_export)
        root.addLayout(bar)

        # ---- 三张统计卡 ----
        stats = QHBoxLayout()
        stats.setSpacing(9)
        self.stat_total = self._stat_card("cb.stat_total")
        self.stat_epub = self._stat_card("cb.stat_epub")
        self.stat_format = self._stat_card("cb.stat_formats")
        for c in (self.stat_total, self.stat_epub, self.stat_format):
            stats.addWidget(c, 1)
        root.addLayout(stats)

        # ---- 列表 + 详情 ----
        content = QHBoxLayout()
        content.setSpacing(9)
        self.list_stack = QStackedWidget()

        self.table = QTableWidget()
        self.COLUMNS = ("cb.col.title", "cb.col.author", "cb.col.format")
        self.table.setColumnCount(len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(k) for k in self.COLUMNS])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self.edit_selected)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_stack.addWidget(self.table)

        self.empty_widget = QWidget()
        ev = QVBoxLayout(self.empty_widget)
        ev.setAlignment(Qt.AlignCenter)
        ev.setSpacing(8)
        self.empty_icon = QLabel()
        self.empty_icon.setAlignment(Qt.AlignCenter)
        self.empty_title = _lbl(tr("cb.empty_title"), "#e6e6e6", 16, bold=True)
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_hint = _lbl(tr("cb.empty_hint"), "#9a9a9a", 12)
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.btn_empty_add = QPushButton(tr("cb.add"))
        self.btn_empty_add.setCursor(Qt.PointingHandCursor)
        self.btn_empty_add.clicked.connect(self.add_to_lib)
        ev.addWidget(self.empty_icon)
        ev.addWidget(self.empty_title)
        ev.addWidget(self.empty_hint)
        ev.addWidget(self.btn_empty_add, 0, Qt.AlignCenter)
        self.list_stack.addWidget(self.empty_widget)
        self.list_stack.setCurrentWidget(self.empty_widget)
        content.addWidget(self.list_stack, 3)

        self.detail_panel = QFrame()
        self.detail_panel.setObjectName("detailPanel")
        dv = QVBoxLayout(self.detail_panel)
        dv.setContentsMargins(18, 18, 18, 18)
        dv.setSpacing(14)
        self.detail_stack = QStackedWidget()
        self.detail_info = QWidget()
        di = QVBoxLayout(self.detail_info)
        di.setContentsMargins(0, 0, 0, 0)
        di.setSpacing(20)
        di.addStretch(1)
        dtop = QHBoxLayout()
        dtop.setSpacing(16)
        self.cover_lbl = QLabel()
        self.cover_lbl.setFixedSize(96, 132)
        self.cover_lbl.setScaledContents(True)
        dtop.addWidget(self.cover_lbl)
        dmeta = QVBoxLayout()
        dmeta.setSpacing(9)
        self.detail_title = _lbl("", "#e6e6e6", 22, bold=True)
        self.detail_title.setWordWrap(True)
        dmeta.addWidget(self.detail_title)
        self._detail_meta_labels = []
        for key in ("cb.detail_author", "cb.detail_format", "cb.detail_added", "cb.detail_path"):
            lbl = _lbl(tr(key), "#9a9a9a", 14, wrap=True)
            lbl._key = key
            self._detail_meta_labels.append(lbl)
            dmeta.addWidget(lbl)
        dtop.addLayout(dmeta, 1)
        di.addLayout(dtop)
        dact = QHBoxLayout()
        self.btn_detail_edit = QPushButton(tr("cb.edit_epub"))
        self.btn_detail_edit.setCursor(Qt.PointingHandCursor)
        self.btn_detail_edit.clicked.connect(self.edit_selected)
        self.btn_detail_convert = QPushButton(tr("cb.convert"))
        self.btn_detail_convert.setCursor(Qt.PointingHandCursor)
        self.btn_detail_convert.clicked.connect(self.convert_selected)
        self.btn_detail_export = QPushButton(tr("cb.export"))
        self.btn_detail_export.setCursor(Qt.PointingHandCursor)
        self.btn_detail_export.clicked.connect(self.export_selected)
        for b in (self.btn_detail_edit, self.btn_detail_convert, self.btn_detail_export):
            dact.addWidget(b)
        di.addLayout(dact)
        di.addStretch(1)
        self.detail_stack.addWidget(self.detail_info)

        self.detail_empty = QWidget()
        de = QVBoxLayout(self.detail_empty)
        de.setAlignment(Qt.AlignCenter)
        self.detail_empty_title = _lbl(tr("cb.detail_empty_title"), "#9a9a9a", 13, bold=True)
        self.detail_empty_title.setAlignment(Qt.AlignCenter)
        self.detail_empty_hint = _lbl(tr("cb.detail_empty_hint"), "#9a9a9a", 11)
        self.detail_empty_hint.setAlignment(Qt.AlignCenter)
        de.addWidget(self.detail_empty_title)
        de.addWidget(self.detail_empty_hint)
        self.detail_stack.addWidget(self.detail_empty)
        self.detail_stack.setCurrentWidget(self.detail_empty)
        dv.addWidget(self.detail_stack)
        content.addWidget(self.detail_panel, 2)
        root.addLayout(content, 1)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setValue(0)
        root.addWidget(self.progress)
        self.log_lbl = _lbl("", "#9a9a9a", 11)
        root.addWidget(self.log_lbl)

    def _stat_card(self, caption_key: str) -> QFrame:
        card = QFrame()
        card.setObjectName("statCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(11, 8, 11, 8)
        v.setSpacing(1)
        num = QLabel("0")
        num.setObjectName("num")
        cap = QLabel(tr(caption_key))
        cap.setObjectName("cap")
        v.addWidget(num)
        v.addWidget(cap)
        return card

    def retranslate(self) -> None:
        """语言切换后刷新静态文案与表头；不触发检测或书库重载。"""
        self.title_lbl.setText(tr("cb.title"))
        self.subtitle_lbl.setText(tr("cb.subtitle"))
        self.badge_lbl.setText(tr("cb.badge"))
        self.path_box.setTitle(tr("cb.advanced"))
        self.bin_lbl.setText(tr("cb.bin_dir"))
        self.bin_edit.setPlaceholderText(tr("cb.bin_ph"))
        self.lib_lbl.setText(tr("cb.lib_dir"))
        self.lib_edit.setPlaceholderText(tr("cb.lib_ph"))
        self.btn_bin.setText(tr("cb.browse"))
        self.btn_lib.setText(tr("cb.browse"))
        self.btn_save.setText(tr("cb.save_detect"))
        self.btn_redetect.setText(tr("cb.redetect"))
        self.search_edit.setPlaceholderText(tr("cb.search_ph"))
        self.btn_search.setText(tr("cb.search"))
        self.btn_edit.setText(tr("cb.edit_epub"))
        self.btn_convert.setText(tr("cb.convert"))
        self.btn_add.setText(tr("cb.add"))
        self.btn_export.setText(tr("cb.export"))
        self.btn_empty_add.setText(tr("cb.add"))
        self.btn_detail_edit.setText(tr("cb.edit_epub"))
        self.btn_detail_convert.setText(tr("cb.convert"))
        self.btn_detail_export.setText(tr("cb.export"))
        self.empty_title.setText(tr("cb.empty_title"))
        self.empty_hint.setText(tr("cb.empty_hint"))
        self.detail_empty_title.setText(tr("cb.detail_empty_title"))
        self.detail_empty_hint.setText(tr("cb.detail_empty_hint"))
        self.stat_total.findChild(QLabel, "cap").setText(tr("cb.stat_total"))
        self.stat_epub.findChild(QLabel, "cap").setText(tr("cb.stat_epub"))
        self.stat_format.findChild(QLabel, "cap").setText(tr("cb.stat_formats"))
        for lbl in self._detail_meta_labels:
            lbl.setText(tr(lbl._key))
        self.table.setHorizontalHeaderLabels([tr(k) for k in self.COLUMNS])
        self._detect()
        self._on_selection_changed()

    # ------------------------------------------------------------------ 配置

    def _load_settings(self) -> None:
        self.bin_edit.setText(str(self.store.settings_get("calibre_bin_dir", "")))
        self.lib_edit.setText(str(self.store.settings_get("calibre_library", "")))

    def _choose_bin(self) -> None:
        d = _fd.select_directory(self, "选择 Calibre 安装目录（含 ebook-edit）")
        if d:
            self.bin_edit.setText(d)

    def _choose_lib(self) -> None:
        d = _fd.select_directory(self, "选择 Calibre 书库目录（含 metadata.db）")
        if d:
            self.lib_edit.setText(d)

    def _save_and_detect(self) -> None:
        self.store.settings_set("calibre_bin_dir", self.bin_edit.text().strip())
        self.store.settings_set("calibre_library", self.lib_edit.text().strip())
        self._detect()
        QMessageBox.information(self, tr("common.saved_title"), tr("calibre.path_saved"))

    def _detect(self) -> None:
        # 每次检测都写日志，让用户明确知道按钮生效了（否则界面看似“没反应”）
        self.log(tr("cb.detecting"))
        self.install = detect_calibre(self.bin_edit.text().strip())
        lib = self.lib_edit.text().strip() or detect_library()
        if lib:
            self.lib_edit.setText(lib)
        p = self._palette
        if not self.install.available:
            self.conn_title.setText(tr("cb.conn_notfound"))
            self.conn_state.setText("● " + tr("cb.conn_notfound"))
            self.conn_state.setStyleSheet(
                f"color:{p['warn']};font-size:11px;font-weight:bold;background:transparent;border:none;")
            self.conn_path.setText(tr("cb.conn_hint"))
            self._set_buttons(False)
            self.log(tr("cb.detect_fail_bin"))
        elif not lib or not os.path.isfile(os.path.join(lib, "metadata.db")):
            self.conn_title.setText(self.install.summary())
            self.conn_state.setText("● " + tr("cb.conn_lib_missing"))
            self.conn_state.setStyleSheet(
                f"color:{p['warn']};font-size:11px;font-weight:bold;background:transparent;border:none;")
            self.conn_path.setText(tr("cb.lib_ph"))
            self._set_buttons(False)
            self.log(tr("cb.detect_fail_lib"))
        else:
            self.conn_title.setText(self.install.summary())
            self.conn_state.setText("● " + tr("cb.connected"))
            self.conn_state.setStyleSheet(
                f"color:{p['ok']};font-size:11px;font-weight:bold;background:transparent;border:none;")
            self.conn_path.setText(tr("cb.conn_path", path=lib))
            self._set_buttons(True)
            self.reload_books()

    def _set_buttons(self, enabled: bool) -> None:
        """未检测到 Calibre / 无书库时禁用全部依赖操作，避免点了才报错。"""
        for b in (self.btn_edit, self.btn_convert, self.btn_add,
                  self.btn_export, self.btn_detail_edit,
                  self.btn_detail_convert, self.btn_detail_export,
                  self.btn_empty_add):
            b.setEnabled(enabled)
        self.table.setEnabled(enabled)
        self.search_edit.setEnabled(enabled)
        self.btn_search.setEnabled(enabled)

    # ------------------------------------------------------------------ 书库

    def reload_books(self) -> None:
        lib = self.lib_edit.text().strip()
        if not lib or not os.path.isfile(os.path.join(lib, "metadata.db")):
            self.log(tr("cb.lib_invalid"))
            return
        self.progress.setValue(10)
        self._worker = LoadBooksWorker(lib, self.search_edit.text().strip(), self)
        self._worker.finished.connect(self._on_books)
        self._worker.failed.connect(self._on_books_failed)
        self._worker.start()

    def _on_books(self, books: List[BookEntry]) -> None:
        self.progress.setValue(100)
        self.books = books
        self.table.setRowCount(len(books))
        for r, b in enumerate(books):
            vals = [b.title, b.authors, b.format_text]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                item.setData(Qt.UserRole, r)
                self.table.setItem(r, c, item)
        # 统计卡
        self.stat_total.findChild(QLabel, "num").setText(str(len(books)))
        n_epub = sum(1 for b in books if any(f.lower() == ".epub" for f in b.formats))
        self.stat_epub.findChild(QLabel, "num").setText(str(n_epub))
        n_fmt = len({f.lower() for b in books for f in b.formats})
        self.stat_format.findChild(QLabel, "num").setText(str(n_fmt))
        # 空状态
        self.list_stack.setCurrentWidget(self.table if books else self.empty_widget)
        self.detail_stack.setCurrentWidget(self.detail_empty)
        if books:
            self.table.selectRow(0)
        self._refresh_cover()
        self.log(tr("cb.books_count", n=len(books)) +
                 (f"（{tr('cb.search')}: {self.search_edit.text()}）" if self.search_edit.text() else ""))

    def _on_books_failed(self, message: str) -> None:
        self.progress.setValue(0)
        self.log(f"{tr('cb.load_failed')}：{message}")
        QMessageBox.critical(self, tr("cb.load_failed"), message)

    def _selected_book(self) -> Optional[BookEntry]:
        row = self.table.currentRow()
        if 0 <= row < len(self.books):
            return self.books[row]
        return None

    def _on_selection_changed(self) -> None:
        book = self._selected_book()
        if book is None:
            self.detail_stack.setCurrentWidget(self.detail_empty)
            return
        self.detail_stack.setCurrentWidget(self.detail_info)
        self.detail_title.setText(book.title)
        meta = self._detail_meta_labels
        meta[0].setText(f"{tr('cb.detail_author')}：{book.authors or '—'}")
        meta[1].setText(f"{tr('cb.detail_format')}：{book.format_text or '—'}")
        meta[2].setText(f"{tr('cb.detail_added')}：{book.timestamp or '—'}")
        meta[3].setText(f"{tr('cb.detail_path')}：{book_file(book, tuple(book.formats)) or '—'}")
        self._refresh_cover(book)

    def _refresh_cover(self, book: Optional[BookEntry] = None) -> None:
        """用书名首字生成渐变封面占位图。"""
        text = (book.title if book else tr("cb.title"))[:1] or "书"
        w, h = 96, 132
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        grad = QLinearGradient(0, 0, w, h)
        c = QColor(self._accent)
        grad.setColorAt(0, c)
        dark = QColor(c).darker(180)
        grad.setColorAt(1, dark)
        p.fillRect(pm.rect(), grad)
        p.setPen(QColor("#ffffff"))
        f = QFont()
        f.setPointSize(40)
        f.setBold(True)
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignCenter, text)
        p.end()
        self.cover_lbl.setPixmap(pm)

    # ------------------------------------------------------------------ 操作

    def edit_selected(self) -> None:
        book = self._selected_book()
        if not book:
            QMessageBox.information(self, tr("common.tip"), tr("calibre.select_book"))
            return
        path = book_file(book, (".epub",))
        if not path:
            QMessageBox.warning(self, tr("calibre.cant_edit_title"), tr("calibre.cant_edit_body"))
            return
        try:
            open_editor(self.install, path)
            self.log(f"已用 Calibre Editor 打开：{book.title}")
        except CalibreError as exc:
            QMessageBox.critical(self, "打开失败", str(exc))

    def convert_selected(self) -> None:
        book = self._selected_book()
        if not book:
            QMessageBox.information(self, tr("common.tip"), tr("calibre.select_book"))
            return
        src = book_file(book, (".epub", ".mobi", ".azw3", ".pdf"))
        if not src:
            QMessageBox.warning(self, tr("calibre.cant_convert_title"), tr("calibre.cant_convert_body"))
            return
        cur_ext = os.path.splitext(src)[1].lower()
        targets = [e for e in (".epub", ".mobi", ".azw3", ".pdf", ".txt") if e != cur_ext]
        from PySide6.QtWidgets import QInputDialog
        target, ok = QInputDialog.getItem(
            self, tr("calibre.convert_title"),
            tr("calibre.convert_label", title=book.title),
            targets, 0, False,
        )
        if not ok:
            return
        out_dir = os.path.dirname(os.path.abspath(src))
        base = os.path.splitext(os.path.basename(src))[0]
        dst = os.path.join(out_dir, f"{base}_converted{target}")
        self.log(f"正在转换：{os.path.basename(src)} → {target} …")
        self.progress.setValue(30)
        try:
            convert_format(self.install, src, dst)
            self.progress.setValue(100)
            self.log(f"✓ 转换完成：{dst}")
            QMessageBox.information(self, tr("common.convert_done"), tr("common.generated", path=dst))
        except CalibreError as exc:
            self.progress.setValue(0)
            QMessageBox.critical(self, "转换失败", str(exc))

    def add_to_lib(self) -> None:
        lib = self.lib_edit.text().strip()
        if not lib or not os.path.isfile(os.path.join(lib, "metadata.db")):
            QMessageBox.warning(self, tr("calibre.invalid_lib_title"), tr("calibre.invalid_lib_body"))
            return
        paths = _fd.open_files(
            self, "选择要加入 Calibre 书库的电子书",
            os.path.expanduser("~/七猫小说"),
            "电子书 (*.epub *.mobi *.azw3 *.pdf *.txt *.fb2);;所有文件 (*.*)",
        )
        if not paths:
            return
        ok = 0
        for p in paths:
            try:
                add_to_library(self.install, lib, p)
                ok += 1
                self.log(f"✓ 已加入书库：{os.path.basename(p)}")
            except CalibreError as exc:
                self.log(f"✗ 加入失败：{os.path.basename(p)} - {exc}")
        QMessageBox.information(self, tr("common.done"), tr("calibre.added_n", ok=ok, total=len(paths)))
        self.reload_books()

    def export_selected(self) -> None:
        book = self._selected_book()
        if not book:
            QMessageBox.information(self, tr("common.tip"), tr("calibre.select_book"))
            return
        lib = self.lib_edit.text().strip()
        out_dir = _fd.select_directory(self, "选择导出目录", lib)
        if not out_dir:
            return
        try:
            export_from_library(self.install, lib, book.book_id, out_dir)
            self.log(f"✓ 已导出《{book.title}》到 {out_dir}")
            QMessageBox.information(self, tr("common.export_done"), tr("common.export_to", path=out_dir))
        except CalibreError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    def open_external_file(self, path: str) -> None:
        """供书架右键调用：直接用 Calibre 编辑指定 EPUB。"""
        if not self.install.available:
            self._detect()
        if not self.install.available:
            QMessageBox.warning(self, tr("calibre.not_found_title"), tr("calibre.not_found_body"))
            return
        try:
            open_editor(self.install, path)
            self.log(f"已用 Calibre Editor 打开：{os.path.basename(path)}")
        except CalibreError as exc:
            QMessageBox.critical(self, "打开失败", str(exc))

    def log(self, message: str) -> None:
        self.log_lbl.setText(message)
