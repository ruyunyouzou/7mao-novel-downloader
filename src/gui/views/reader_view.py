# -*- coding: utf-8 -*-
"""
阅读器视图：在线/离线阅读本地 TXT 与 EPUB 小说。

功能：
- 解析 TXT（按"第X章"正则切分）与 EPUB（ebooklib），生成章节列表
- 字号调整、行距、字体
- 滚动 / 翻页两种阅读模式
- 背景主题：浅色 / 深色 / 护眼（羊皮纸绿）
- 简洁模式（隐藏工具栏，专注阅读）
- 系统朗读听书（PySide6 QTextToSpeech）
- 自动保存阅读进度（章节 + 滚动位置），记录阅读历史
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont, QKeySequence, QShortcut, QPixmap, QImage, QPainter, QColor, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextBrowser, QListWidget, QPushButton,
    QComboBox, QSlider, QLabel, QSplitter, QMessageBox, QMenu,
    QFrame, QScrollArea, QWidgetAction,
)

from ..datastore import DataStore
from .. import filedialogs as _fd
from ..dialogue_highlight import highlight_text
from ..i18n import tr
from ..style import ACCENT
from ..widgets import apply_nation_icon
from ..voice_library import VoiceProfile, get_voice, sample_for_language, DEFAULT_VOICE_ID, edge_available
from ..tts_engine import TTSEngine
from ..voice_dialog import VoiceDialog

# 主题内部 key（中文，用于 THEMES 查找与持久化）→ i18n key
_THEME_LABELS = {
    "跟随界面": "reader.theme.follow",
    "浅色": "reader.theme.light",
    "深色": "reader.theme.dark",
    "护眼": "reader.theme.eye",
    "羊皮纸": "reader.theme.parchment",
}

# 纸张"跟随界面"：根据主界面深浅动态选用深色/浅色纸张
PAPER_FOLLOW = "跟随界面"
# 正文书页舒适宽度（居中，两侧留白）
PAGE_WIDTH = 820

# 封面墙兜底配色（无真实封面时按书名首字生成渐变色封面）
_COVER_GRADIENTS = [
    ("#7e6aa6", "#3f3560"), ("#5a86ad", "#304a63"), ("#b87a58", "#6e4634"),
    ("#6a9e7a", "#365442"), ("#a06a8f", "#5a3550"), ("#b0935a", "#6b552f"),
    ("#5a8f9e", "#33545c"), ("#9e6a6a", "#5c3a3a"),
]

# 背景主题配色（背景色, 文字色）
THEMES = {
    "浅色": ("#ffffff", "#222222"),
    "深色": ("#1e1e1e", "#d8d8d8"),
    "护眼": ("#e8f0e0", "#2f3b2a"),
    "羊皮纸": ("#f5ecd9", "#4a3b28"),
}


class _CenteredPageHost(QWidget):
    """正文书页容器：把内部文本控件限宽并水平居中，两侧留白。"""

    def __init__(self, text_widget, parent=None) -> None:
        super().__init__(parent)
        self._text = text_widget
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(text_widget)
        lay.setAlignment(text_widget, Qt.AlignHCenter)

    def resizeEvent(self, event) -> None:  # noqa: N802
        # 可用宽度内取舒适宽度，窄窗口时自动收缩，避免横向滚动
        width = min(PAGE_WIDTH, max(320, self.width() - 36))
        self._text.setFixedWidth(width)
        super().resizeEvent(event)


def _is_app_dark(widget) -> bool:
    """读取主界面当前深浅，供"纸张跟随界面"判断。

    优先取主窗口实时状态 ``_dark_theme``（切换即时生效），无主窗口时
    再退回 theme_mode 持久化值。
    """
    try:
        win = widget.window() if widget else None
        if win is not None and hasattr(win, "_dark_theme"):
            return bool(win._dark_theme)
        store = getattr(widget, "store", None)
        mode = str(store.settings_get("theme_mode", "dark") or "dark") if store else "dark"
        return mode != "light"
    except Exception:  # noqa: BLE001
        return True

# 章节标题正则（覆盖"第一章 陨落的天才"、"第1章"、"卷一 xxx"、"序章"等）
CHAPTER_RE = re.compile(
    r"^\s*("
    r"第[零一二三四五六七八九十百千万两〇0-9]+\s*[章节回部篇卷]"  # 第X章（允许"第两百七十四 章"中间空格）
    r"|第[零一二三四五六七八九十百千万两〇0-9]{2,}(?=\s|$|[^\d零一二三四五六七八九十百千万两〇])"  # 第一千三百四十五离开天墓（章字省略）
    r"|[零一二三四五六七八九十百千万两〇]+章(?=\s|$)"          # 四百七十章 这类省略"第"的
    r"|[Cc]hapter\s*\d+"                                     # Chapter N
    r"|序章|楔子|番外|尾声|后记|终章|番外篇"
    r")"
)


class ReaderView(QWidget):
    """阅读器页面。"""

    progress_changed = Signal(str, int, float)  # book_key, chapter_index, percent
    open_book_request = Signal(dict)  # 请求从书架打开某本书

    def __init__(self, store: DataStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.current_path: str = ""
        self.current_key: str = ""  # 书籍标识（book_id 或文件路径）
        self.current_title: str = ""
        self.chapters: List[Dict[str, Any]] = []  # {title, content}
        self.current_index: int = 0
        self._suppress_scroll = False
        # 连续自动阅读
        self._auto_read = False
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self._auto_next)

        # 听书：统一引擎（edge-tts 网络音色优先，系统 TTS 兜底）
        self.tts_engine = TTSEngine(self)
        self.tts_engine.playingChanged.connect(self._on_playing_changed)
        self.tts_engine.errorOccurred.connect(
            lambda msg: self.status_label.setText(msg) if hasattr(self, "status_label") else None)
        # 兼容旧属性：tts_available 表示"任一引擎可用"
        self.tts_available = True
        self.tts = None  # 旧代码若直接引用 tts 不再有系统引擎，统一走 tts_engine
        self._voice_id = str(self.store.settings_get("read_voice", DEFAULT_VOICE_ID) or DEFAULT_VOICE_ID)
        # 听书倍速：0.5 ~ 5.0，持久化并即时应用到引擎
        self._speed = TTSEngine.clamp_speed(self.store.settings_get("read_speed", 1.0))
        self.tts_engine.set_speed(self._speed)
        self._speaking = False

        self._build_ui()
        self._load_settings()
        self._apply_theme()
        self._apply_font()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------- 工具栏（收纳：高频常驻 + 两个下拉菜单）----------
        self.toolbar = QHBoxLayout()
        self.toolbar.setContentsMargins(10, 8, 10, 8)
        self.toolbar.setSpacing(8)

        self.btn_open = QPushButton(tr("reader.open_local"))
        apply_nation_icon(self.btn_open, "liyue", ACCENT, 16)
        self.btn_open.clicked.connect(self.open_local)
        self.toolbar.addWidget(self.btn_open)

        self.btn_prev = QPushButton(tr("reader.prev"))
        self.btn_prev.clicked.connect(self.prev_chapter)
        self.toolbar.addWidget(self.btn_prev)

        self.btn_next = QPushButton(tr("reader.next"))
        self.btn_next.clicked.connect(self.next_chapter)
        self.toolbar.addWidget(self.btn_next)

        # 目录折叠按钮：一键收起/展开左侧章节目录，给正文更多空间
        self.btn_catalog = QPushButton(tr("reader.catalog"))
        self.btn_catalog.setCheckable(True)
        apply_nation_icon(self.btn_catalog, "sumeru", ACCENT, 16)
        self.btn_catalog.setToolTip(tr("reader.catalog_tip"))
        self.btn_catalog.clicked.connect(self._toggle_catalog)
        self.toolbar.addWidget(self.btn_catalog)

        self.toolbar.addStretch()

        # 「阅读设置」菜单：字号 / 主题 / 模式（控件实际驻留在菜单 widget 中）
        self.btn_settings_menu = QPushButton(tr("reader.settings") + "  ▾")
        apply_nation_icon(self.btn_settings_menu, "sumeru", ACCENT, 16)
        self.settings_menu = QMenu(self)
        self._build_settings_menu()
        self.btn_settings_menu.setMenu(self.settings_menu)
        self.toolbar.addWidget(self.btn_settings_menu)

        # 「更多」菜单：听书 / 对话着色 / 自动阅读
        self.btn_more_menu = QPushButton(tr("reader.more") + "  ▾")
        apply_nation_icon(self.btn_more_menu, "mondstadt", ACCENT, 16)
        self.more_menu = QMenu(self)
        self._build_more_menu()
        self.btn_more_menu.setMenu(self.more_menu)
        self.toolbar.addWidget(self.btn_more_menu)

        self.btn_focus = QPushButton(tr("reader.focus"))
        self.btn_focus.setCheckable(True)
        self.btn_focus.clicked.connect(self._toggle_focus)
        self.toolbar.addWidget(self.btn_focus)

        root.addLayout(self.toolbar)

        # 分隔线
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background:#3a3a3a;")
        root.addWidget(line)

        # 主体：左侧目录 + 右侧正文（正文居中成"书页"，两侧留白）
        splitter = QSplitter(Qt.Horizontal)
        self.catalog_list = QListWidget()
        self.catalog_list.setMaximumWidth(240)
        self.catalog_list.currentRowChanged.connect(self._on_catalog_row)
        splitter.addWidget(self.catalog_list)

        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(False)
        self.text.verticalScrollBar().valueChanged.connect(self._on_scroll)
        # 书页容器：正文限宽居中，两侧弹性留白形成阅读房间感
        page_host = _CenteredPageHost(self.text)
        self.page_host = page_host
        splitter.addWidget(page_host)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.reader_splitter = splitter
        root.addWidget(splitter, 1)

        # ---------- 封面墙首屏（覆盖在正文之上）----------
        self.welcome_widget = self._build_welcome()
        root.addWidget(self.welcome_widget, 1)

        self.status_label = QLabel(tr("reader.status_empty"))
        root.addWidget(self.status_label)

        # 快捷键：左右方向键翻章
        QShortcut(QKeySequence("Ctrl+Left"), self, self.prev_chapter)
        QShortcut(QKeySequence("Ctrl+Right"), self, self.next_chapter)

        # 拖放文件支持
        self.setAcceptDrops(True)
        self.welcome_widget.setAcceptDrops(True)

        self._show_welcome()

    def _menu_widget(self, inner: QWidget) -> None:
        """把一个 QWidget 作为动作塞进菜单（保持控件可交互）。"""
        from PySide6.QtWidgets import QWidgetAction
        act = QWidgetAction(self.settings_menu)
        act.setDefaultWidget(inner)
        self.settings_menu.addAction(act)

    def _build_settings_menu(self) -> None:
        """阅读设置菜单：字号滑块 + 主题 + 模式。"""
        holder = QWidget()
        v = QVBoxLayout(holder)
        v.setContentsMargins(12, 8, 12, 10)
        v.setSpacing(8)

        row_font = QHBoxLayout()
        row_font.addWidget(QLabel(tr("reader.font_size")))
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(12, 32)
        self.size_slider.setValue(18)
        self.size_slider.setFixedWidth(160)
        self.size_slider.valueChanged.connect(self._apply_font)
        row_font.addWidget(self.size_slider)
        self.size_value_label = QLabel("18")
        self.size_slider.valueChanged.connect(lambda v: self.size_value_label.setText(str(v)))
        row_font.addWidget(self.size_value_label)
        v.addLayout(row_font)

        row_theme = QHBoxLayout()
        row_theme.addWidget(QLabel(tr("reader.theme")))
        self.theme_combo = QComboBox()
        # 内部值保持中文（THEMES 的 key 与持久化值），显示文案用 tr
        for key in THEMES.keys():
            self.theme_combo.addItem(tr(_THEME_LABELS.get(key, "reader.theme.light")), key)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_index_changed)
        row_theme.addWidget(self.theme_combo)
        v.addLayout(row_theme)

        row_mode = QHBoxLayout()
        row_mode.addWidget(QLabel(tr("reader.mode")))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(tr("reader.mode.scroll"), "滚动")
        self.mode_combo.addItem(tr("reader.mode.page"), "翻页")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_index_changed)
        row_mode.addWidget(self.mode_combo)
        v.addLayout(row_mode)

        from PySide6.QtWidgets import QWidgetAction
        act = QWidgetAction(self.settings_menu)
        act.setDefaultWidget(holder)
        self.settings_menu.addAction(act)

    def _build_more_menu(self) -> None:
        """更多菜单：听书 / 听书音色 / 倍速 / 对话着色 / 自动阅读（checkable）。"""
        self.act_speak = self.more_menu.addAction(tr("reader.speak"))
        self.act_speak.setCheckable(True)
        self.act_speak.setEnabled(self.tts_available)
        self.act_speak.triggered.connect(self._toggle_speech)
        if not self.tts_available:
            self.act_speak.setToolTip("当前系统无可用语音引擎")

        self.act_voice = self.more_menu.addAction(tr("voice.menu"))
        self.act_voice.triggered.connect(self._choose_voice)

        self.act_import_audio = self.more_menu.addAction(tr("voice.import_audio"))
        self.act_import_audio.triggered.connect(self._import_audio)

        self._build_speed_menu()

        self.act_dialogue = self.more_menu.addAction(tr("reader.dialogue"))
        self.act_dialogue.setCheckable(True)
        self.act_dialogue.setToolTip("不同人物的对话显示为不同颜色（仅显示效果，不影响下载/EPUB）")
        self.act_dialogue.triggered.connect(self._toggle_dialogue)

        self.act_auto = self.more_menu.addAction(tr("reader.auto"))
        self.act_auto.setCheckable(True)
        self.act_auto.setToolTip("读到章末后自动翻到下一章，可再次点击关闭")
        self.act_auto.triggered.connect(self._toggle_auto_read)

    # ------------------------------------------------------------------ 听书倍速
    @staticmethod
    def _speed_label(speed: float | None = None) -> str:
        """倍速显示文案，如 1× / 0.5× / 1.25× / 5×。"""
        s = TTSEngine.clamp_speed(speed if speed is not None else 1.0)
        return f"{s:.2f}".rstrip("0").rstrip(".") + "×"

    def _build_speed_menu(self) -> None:
        """更多 → 倍速：滑块精细调节 + 常用档位一键切换（0.5~5 倍）。"""
        self.speed_menu = QMenu(tr("voice.speed"), self.more_menu)
        self.more_menu.addMenu(self.speed_menu)

        # 滑块 + 数值（QWidgetAction，与阅读设置菜单风格一致）
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(12, 8, 12, 4)
        row.setSpacing(8)
        row.addWidget(QLabel(tr("voice.speed")))
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(50, 500)  # 实际倍速 = 滑块值 / 100
        self.speed_slider.setFixedWidth(170)
        self.speed_slider.valueChanged.connect(
            lambda value: self._set_speed(value / 100.0))
        row.addWidget(self.speed_slider)
        self.speed_value_label = QLabel(self._speed_label(self._speed))
        self.speed_value_label.setFixedWidth(44)
        row.addWidget(self.speed_value_label)
        act_slider = QWidgetAction(self.speed_menu)
        act_slider.setDefaultWidget(holder)
        self.speed_menu.addAction(act_slider)

        self.speed_menu.addSeparator()

        # 常用档位
        self.speed_actions: dict[float, object] = {}
        for value in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 5.0):
            act = self.speed_menu.addAction(self._speed_label(value))
            act.setCheckable(True)
            act.triggered.connect(
                lambda checked=False, v=value: self._set_speed(v))
            self.speed_actions[value] = act

        self.speed_slider.blockSignals(True)
        self.speed_slider.setValue(int(round(self._speed * 100)))
        self.speed_slider.blockSignals(False)
        self._sync_speed_actions()

    def _sync_speed_actions(self) -> None:
        """同步倍速档位的勾选状态。"""
        for value, act in getattr(self, "speed_actions", {}).items():
            act.blockSignals(True)
            act.setChecked(abs(value - self._speed) < 0.005)
            act.blockSignals(False)

    def _set_speed(self, value: float, persist: bool = True) -> None:
        """倍速统一入口：引擎 / 滑块 / 档位 / 状态栏同步。"""
        self._speed = TTSEngine.clamp_speed(value)
        self.tts_engine.set_speed(self._speed)
        if persist:
            self.store.settings_set("read_speed", self._speed)
        if hasattr(self, "speed_slider"):
            self.speed_slider.blockSignals(True)
            self.speed_slider.setValue(int(round(self._speed * 100)))
            self.speed_slider.blockSignals(False)
        if hasattr(self, "speed_value_label"):
            self.speed_value_label.setText(self._speed_label(self._speed))
        self._sync_speed_actions()
        if self._speaking:
            self._on_playing_changed(True)

    # ------------------------------------------------------------------ 旧属性兼容别名
    # 工具栏收纳后，听书/着色/自动阅读改为「更多」菜单里的 QAction；
    # 保留 btn_* 别名指向这些 action，兼容旧测试与外部引用。
    @property
    def btn_speak(self):
        return getattr(self, "act_speak", None)

    @property
    def btn_dialogue(self):
        return getattr(self, "act_dialogue", None)

    @property
    def btn_auto(self):
        return getattr(self, "act_auto", None)

    # ------------------------------------------------------------------ 封面墙首屏
    def _build_welcome(self) -> QWidget:
        w = QWidget()
        w.setObjectName("readerWelcome")
        w.setStyleSheet("QWidget#readerWelcome { background: transparent; }")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)
        # 上下各一个弹性空间，让「引导区 + 封面墙」作为整体垂直居中
        outer.addStretch(1)

        center = QVBoxLayout()
        center.setAlignment(Qt.AlignCenter)
        center.setSpacing(8)

        # 国徽主图标
        from ..widgets import nation_icon
        emblem = QLabel()
        pm = nation_icon("sumeru", ACCENT, 46).pixmap(46, 46)
        emblem.setPixmap(pm)
        emblem.setAlignment(Qt.AlignCenter)
        center.addWidget(emblem, 0, Qt.AlignCenter)

        self.welcome_title = QLabel(tr("reader.welcome_title"))
        self.welcome_title.setAlignment(Qt.AlignCenter)
        self.welcome_title.setStyleSheet("font-size:22px; font-weight:bold;")
        center.addWidget(self.welcome_title)

        self.welcome_sub = QLabel(tr("reader.welcome_sub"))
        self.welcome_sub.setAlignment(Qt.AlignCenter)
        self.welcome_sub.setStyleSheet("font-size:12px; opacity:0.7;")
        center.addWidget(self.welcome_sub)

        # 三个入口
        entries = QHBoxLayout()
        entries.setAlignment(Qt.AlignCenter)
        entries.setSpacing(10)
        self.btn_primary_open = QPushButton("＋ " + tr("reader.open_local"))
        self.btn_primary_open.setObjectName("readerPrimary")
        self.btn_primary_open.setStyleSheet(
            f"QPushButton#readerPrimary {{ background: {ACCENT}; color: #1a1a1a; "
            f"border: none; border-radius: 8px; padding: 8px 18px; font-weight: bold; }}"
            f"QPushButton#readerPrimary:hover {{ opacity: 0.9; }}")
        self.btn_primary_open.clicked.connect(self.open_local)
        entries.addWidget(self.btn_primary_open)
        self.btn_from_shelf = QPushButton(tr("reader.from_shelf"))
        self.btn_from_shelf.clicked.connect(self._open_from_shelf)
        entries.addWidget(self.btn_from_shelf)
        self.btn_drop_hint = QLabel(tr("reader.drop_file"))
        self.btn_drop_hint.setStyleSheet("opacity:0.5; font-size:11px; padding:0 8px;")
        entries.addWidget(self.btn_drop_hint)
        center.addLayout(entries)

        outer.addLayout(center, 0)

        # 最近阅读封面墙
        self.recent_hint = QLabel(tr("reader.recent_hint"))
        self.recent_hint.setAlignment(Qt.AlignCenter)
        self.recent_hint.setStyleSheet("font-size:12px; opacity:0.6;")
        outer.addWidget(self.recent_hint)

        self.cover_scroll = QScrollArea()
        self.cover_scroll.setWidgetResizable(True)
        self.cover_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.cover_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cover_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.cover_host = QWidget()
        self.cover_host.setStyleSheet("background: transparent;")
        self.cover_grid = QHBoxLayout(self.cover_host)
        self.cover_grid.setContentsMargins(4, 4, 4, 4)
        self.cover_grid.setSpacing(14)
        # 不设 AlignLeft：由前后 stretch 控制水平居中
        self.cover_scroll.setWidget(self.cover_host)
        self.cover_scroll.setFixedHeight(160)
        outer.addWidget(self.cover_scroll, 0)
        outer.addStretch(1)

        return w

    def _make_cover_card(self, title: str, cover_url: str, path: str,
                         key: str, chapter_index: int, percent: float) -> QWidget:
        """生成一张封面卡片（真实封面或渐变色兜底），点击续读。"""
        card = QFrame()
        card.setObjectName("readerCoverCard")
        card.setCursor(Qt.PointingHandCursor)
        card.setStyleSheet(
            "QFrame#readerCoverCard { background: transparent; border: none; }"
            "QFrame#readerCoverCard:hover { background: rgba(255,255,255,0.06); border-radius:8px; }")
        v = QVBoxLayout(card)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(5)
        v.setAlignment(Qt.AlignCenter)

        cover = QLabel()
        cover.setFixedSize(72, 100)
        cover.setAlignment(Qt.AlignCenter)
        cover.setStyleSheet("border-radius:5px;")
        loaded = False
        if cover_url:
            try:
                import requests
                resp = requests.get(cover_url, timeout=6)
                img = QImage.fromData(resp.content)
                if not img.isNull():
                    cover.setPixmap(QPixmap.fromImage(img).scaled(
                        72, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    loaded = True
            except Exception:  # noqa: BLE001
                pass
        if not loaded:
            cover.setPixmap(self._fallback_cover(title))
        v.addWidget(cover, 0, Qt.AlignCenter)

        name = QLabel(title or "—")
        name.setFixedWidth(76)
        name.setAlignment(Qt.AlignCenter)
        name.setStyleSheet("font-size:11px;")
        name.setWordWrap(True)
        v.addWidget(name, 0, Qt.AlignCenter)

        if percent and percent > 0:
            prog = QLabel(f"{int(percent)}%")
            prog.setAlignment(Qt.AlignCenter)
            prog.setStyleSheet(f"font-size:10px; color: {ACCENT};")
            v.addWidget(prog, 0, Qt.AlignCenter)

        card.mousePressEvent = lambda _e: self.open_book(path, title, key, chapter_index)
        return card

    def _fallback_cover(self, title: str) -> QPixmap:
        """按书名生成渐变色封面 + 首字。"""
        idx = sum(ord(c) for c in (title or "书")) % len(_COVER_GRADIENTS)
        c1, c2 = _COVER_GRADIENTS[idx]
        img = QImage(72, 100, QImage.Format_RGB32)
        img.fill(QColor(c2))
        p = QPainter(img)
        # 垂直渐变
        from PySide6.QtGui import QLinearGradient
        grad = QLinearGradient(0, 0, 0, 100)
        grad.setColorAt(0, QColor(c1))
        grad.setColorAt(1, QColor(c2))
        p.fillRect(img.rect(), grad)
        # 首字
        p.setPen(QColor("#ffffff"))
        f = QFont("Microsoft YaHei", 26)
        p.setFont(f)
        ch = (title or "书").strip()[:1] or "书"
        p.drawText(img.rect(), Qt.AlignCenter, ch)
        p.end()
        return QPixmap.fromImage(img)

    def _recent_books(self) -> List[Dict[str, Any]]:
        """组装最近阅读列表：优先阅读历史，书架补齐，去重，最多 8 本。"""
        out: List[Dict[str, Any]] = []
        seen = set()

        # 进度记录带 chapter_index / percent，优先
        for p in self.store.progress_all():
            key = str(p.get("book_id", "") or "")
            if not key or key in seen:
                continue
            title = str(p.get("title", "") or "")
            path = str(p.get("path", "") or "")
            if not title or not path:
                continue
            seen.add(key)
            out.append({
                "title": title, "path": path, "key": key,
                "chapter_index": int(p.get("chapter_index", 0) or 0),
                "percent": float(p.get("percent", 0) or 0),
                "cover": str(p.get("cover", "") or ""),
            })
            if len(out) >= 8:
                break

        if len(out) < 8:
            for s in self.store.shelf_all():
                if len(out) >= 8:
                    break
                key = str(s.get("book_id", "") or s.get("file_path", "") or "")
                path = str(s.get("file_path", "") or "")
                title = str(s.get("title", "") or "")
                if not path or not title or not os.path.isfile(path):
                    continue
                dedup = key or path
                if dedup in seen:
                    continue
                seen.add(dedup)
                prog = self.store.progress_get(key) if key else None
                out.append({
                    "title": title, "path": path, "key": key or path,
                    "chapter_index": int(prog.get("chapter_index", 0)) if prog else 0,
                    "percent": float(prog.get("percent", 0) or 0) if prog else 0,
                    "cover": str(s.get("cover", "") or ""),
                })
        return out[:8]

    def _refresh_cover_wall(self) -> None:
        # 清空旧卡片：setParent(None) 同步移出父控件，避免 deleteLater
        # 在无事件循环场景下未真正删除、导致新旧封面叠加
        while self.cover_grid.count():
            item = self.cover_grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        books = self._recent_books()
        if not books:
            self.recent_hint.setText(tr("reader.no_recent"))
            self.cover_scroll.hide()
            return
        self.recent_hint.setText(tr("reader.recent_hint"))
        self.cover_scroll.show()
        # 前后各一个弹性空间：书少时整排居中，书多时仍可横向滚动
        self.cover_grid.addStretch(1)
        for b in books:
            self.cover_grid.addWidget(self._make_cover_card(
                b["title"], b["cover"], b["path"], b["key"],
                b["chapter_index"], b["percent"]))
        self.cover_grid.addStretch(1)

    def _show_welcome(self) -> None:
        """显示封面墙首屏，隐藏正文与目录。"""
        self._refresh_cover_wall()
        self.welcome_widget.show()
        self.reader_splitter.hide()
        self.status_label.setText(tr("reader.status_empty"))

    def _hide_welcome(self) -> None:
        self.welcome_widget.hide()
        self.reader_splitter.show()

    def _open_from_shelf(self) -> None:
        """从书架选择：切到书架页。"""
        parent = self.window()
        if parent and hasattr(parent, "tabs"):
            for i in range(parent.tabs.count()):
                if "书架" in parent.tabs.tabText(i) or "shelf" in parent.tabs.tabText(i).lower():
                    parent.tabs.setCurrentIndex(i)
                    break

    # ------------------------------------------------------------------ 拖放
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if path and os.path.isfile(path) and path.lower().endswith((".txt", ".epub")):
            self.open_book(path)
            event.acceptProposedAction()

    # ------------------------------------------------------------------ 打开与解析

    def open_local(self) -> None:
        path = _fd.open_file(
            self, "选择本地小说", "", "小说文件 (*.txt *.epub);;所有文件 (*.*)"
        )
        if path:
            self.open_book(path)

    def open_book(self, path: str, title: str = "", key: str = "",
                  chapter_index: Optional[int] = None) -> None:
        """打开一本书。key 为书架 book_id，用于进度关联。"""
        if not os.path.exists(path):
            QMessageBox.warning(self, "无法打开", f"文件不存在：\n{path}")
            return
        try:
            if path.lower().endswith(".epub"):
                chapters = self._parse_epub(path)
            else:
                chapters = self._parse_txt(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "解析失败", f"无法解析该书籍：\n{exc}")
            return
        if not chapters:
            QMessageBox.warning(self, "无内容", "未能从文件中解析出章节内容")
            return

        self.current_path = path
        self.current_key = key or path
        self.current_title = title or os.path.splitext(os.path.basename(path))[0]
        self.chapters = chapters
        self._hide_welcome()

        self.catalog_list.blockSignals(True)
        self.catalog_list.clear()
        for ch in chapters:
            self.catalog_list.addItem(ch["title"])
        self.catalog_list.blockSignals(False)

        # 恢复进度
        idx = chapter_index
        scroll = 0
        if idx is None:
            prog = self.store.progress_get(self.current_key)
            if prog:
                idx = int(prog.get("chapter_index", 0))
                scroll = int(prog.get("scroll", 0))
        if idx is None or idx < 0 or idx >= len(chapters):
            idx = 0
        self._suppress_scroll = True
        self.catalog_list.setCurrentRow(idx)
        self._render_chapter(idx)
        if scroll:
            QTimer.singleShot(80, lambda s=scroll: self.text.verticalScrollBar().setValue(s))
        self._suppress_scroll = False
        self.status_label.setText(f"正在阅读《{self.current_title}》 · 共 {len(chapters)} 章")
        self._log_read()

    def _parse_txt(self, path: str) -> List[Dict[str, Any]]:
        """按章节标题正则切分 TXT。"""
        for enc in ("utf-8", "gbk", "gb18030"):
            try:
                with open(path, "r", encoding=enc) as f:
                    text = f.read()
                break
            except UnicodeDecodeError:
                continue
        else:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()

        lines = text.splitlines()
        chapters: List[Dict[str, Any]] = []
        cur_title = ""
        cur_lines: List[str] = []
        for line in lines:
            if CHAPTER_RE.match(line) and line.strip():
                if cur_title:  # 已有章节才落盘，忽略文件头部的信息文字
                    chapters.append({"title": cur_title, "content": "\n".join(cur_lines).strip()})
                cur_lines = []
                cur_title = line.strip()
            elif cur_title:  # 章节开始后的内容才收集
                cur_lines.append(line)
        if cur_title and cur_lines:
            chapters.append({"title": cur_title, "content": "\n".join(cur_lines).strip()})

        # 若没识别出章节（整本一段），按长度切分成若干"节"
        if len(chapters) <= 1:
            text = "\n".join(lines)
            size = 4000
            chapters = [
                {"title": f"第 {i // size + 1} 节", "content": text[i:i + size]}
                for i in range(0, len(text), size)
            ]
        return [c for c in chapters if c["content"]]

    def _parse_epub(self, path: str) -> List[Dict[str, Any]]:
        """用 ebooklib 解析 EPUB 章节。"""
        from ebooklib import epub
        from bs4 import BeautifulSoup

        book = epub.read_epub(path)
        chapters = []
        for item in book.get_items():
            if item.get_type() == 9:  # ITEM_DOCUMENT
                soup = BeautifulSoup(item.get_content(), "html.parser")
                for tag in soup(["script", "style", "nav"]):
                    tag.decompose()
                title = soup.title.string.strip() if soup.title and soup.title.string else "章节"
                content = soup.get_text("\n", strip=True)
                if content:
                    chapters.append({"title": title, "content": content})
        return chapters

    # ------------------------------------------------------------------ 渲染与导航

    def _render_chapter(self, index: int) -> None:
        if index < 0 or index >= len(self.chapters):
            return
        self.current_index = index
        ch = self.chapters[index]
        from html import escape
        # 对话着色只在渲染层生效：原始 ch["content"] 与导出/下载文件都不变
        if getattr(self, "act_dialogue", None) is not None and self.act_dialogue.isChecked():
            body = highlight_text(ch["content"], dark=self._paper_colors()[1])
        else:
            body = escape(ch["content"]).replace("\n", "<br/>")
        self.text.setHtml(
            f"<div style='line-height:1.8;'><h2>{escape(ch['title'])}</h2><p>{body}</p></div>"
        )
        self.text.verticalScrollBar().setValue(0)
        self.btn_prev.setEnabled(index > 0)
        self.btn_next.setEnabled(index < len(self.chapters) - 1)

    def _on_catalog_row(self, row: int) -> None:
        if row < 0 or row != self.current_index:
            if row >= 0:
                if self._speaking:
                    self._stop_speech()
                self._render_chapter(row)
                self._save_progress()

    def prev_chapter(self) -> None:
        if self.current_index > 0:
            self.catalog_list.setCurrentRow(self.current_index - 1)

    def next_chapter(self) -> None:
        if self.current_index < len(self.chapters) - 1:
            self.catalog_list.setCurrentRow(self.current_index + 1)

    def _on_scroll(self, value: int) -> None:
        if self._suppress_scroll or not self.chapters or not self.current_key:
            return
        maximum = self.text.verticalScrollBar().maximum()
        percent = value / maximum if maximum > 0 else 0.0
        self.progress_changed.emit(self.current_key, self.current_index, percent)
        # 连续自动阅读：滚动到接近底部时安排在延迟后翻章（翻页模式由 _maybe_next_on_scroll 立即翻）
        if self._auto_read and not self._auto_timer.isActive() \
                and maximum > 0 and value >= maximum - 2:
            self._schedule_auto_next()

    def _on_mode_changed(self, mode: str) -> None:
        """翻页模式下，滚动到底自动跳下一章。"""
        if mode == "翻页":
            self.text.verticalScrollBar().valueChanged.connect(self._maybe_next_on_scroll)
        else:
            try:
                self.text.verticalScrollBar().valueChanged.disconnect(self._maybe_next_on_scroll)
            except Exception:  # noqa: BLE001
                pass

    def _maybe_next_on_scroll(self, value: int) -> None:
        bar = self.text.verticalScrollBar()
        if bar.maximum() > 0 and value >= bar.maximum() - 2:
            if self.current_index < len(self.chapters) - 1:
                self.next_chapter()

    # ------------------------------------------------------------ 连续自动阅读

    def _toggle_auto_read(self, checked: bool = None) -> None:
        """开启/关闭连续自动阅读。"""
        if checked is None:
            checked = self.act_auto.isChecked()
        self._auto_read = bool(checked)
        self.act_auto.setText("⏸ 停止自动" if self._auto_read else "▶️ 自动阅读")
        if not self._auto_read and self._auto_timer.isActive():
            self._auto_timer.stop()
        self._save_setting("auto_read", self._auto_read)
        if self._auto_read:
            # 开启时若已在底部，稍后触发一次
            self._schedule_auto_next()

    def _schedule_auto_next(self) -> None:
        """安排在延迟后翻到下一章（间隔可在设置里配，默认 1.2 秒）。"""
        if not self._auto_read:
            return
        delay = int(self.store.settings_get("auto_read_interval", 1200) or 1200)
        self._auto_timer.start(max(300, delay))

    def _auto_next(self) -> None:
        if not self._auto_read or not self.chapters:
            return
        if self.current_index < len(self.chapters) - 1:
            self.next_chapter()
            # 翻章后重置滚动到顶部，下一章读完再触发
            self.text.verticalScrollBar().setValue(0)
        else:
            # 已是最后一章，自动停止
            self.act_auto.setChecked(False)
            self._toggle_auto_read(False)

    # ------------------------------------------------------------------ 外观

    def _apply_font(self) -> None:
        size = self.size_slider.value()
        self.text.setFont(QFont("Microsoft YaHei", size))
        self._save_setting("font_size", size)

    def _on_theme_index_changed(self, _idx: int = 0) -> None:
        """主题下拉变更（用 userData 存内部 key）。"""
        self._apply_theme()

    def _on_mode_index_changed(self, _idx: int = 0) -> None:
        """模式下拉变更（用 userData 存内部 key）。"""
        self._on_mode_changed(self.mode_combo.currentData() or "滚动")

    def _paper_colors(self):
        """返回 (背景, 文字) 与是否在深色纸张；跟随界面时取主界面深浅。"""
        name = self.theme_combo.currentData() or PAPER_FOLLOW
        if name == PAPER_FOLLOW:
            dark = _is_app_dark(self)
            return THEMES["深色"] if dark else THEMES["浅色"], dark
        bg, fg = THEMES.get(name, THEMES["浅色"])
        return (bg, fg), (name == "深色")

    def _apply_theme(self) -> None:
        name = self.theme_combo.currentData() or PAPER_FOLLOW
        (bg, fg), dark = self._paper_colors()
        # 正文书页：圆角阴影感边框，限宽居中
        self.text.setStyleSheet(
            f"QTextBrowser {{ background: {bg}; color: {fg}; "
            f"border: 1px solid {'#3a3a3a' if dark else '#d8d0c0'}; "
            f"border-radius: 8px; padding: 28px 34px; }}"
        )
        self.catalog_list.setStyleSheet(
            f"QListWidget {{ background: {bg}; color: {fg}; "
            f"border: 1px solid {'#3a3a3a' if dark else '#d8d0c0'}; border-radius: 8px; }}"
            "QListWidget::item:selected { background: #7b6bb0; color: white; }"
        )
        # 书页两侧的留白随纸张深浅走，形成统一阅读房间
        host_bg = '#181818' if dark else '#f3f1ec'
        self.page_host.setStyleSheet(f"QWidget {{ background: {host_bg}; }}")
        self._save_setting("reader_theme", name)
        # 对话着色颜色随深/浅主题切换，重新渲染当前章
        if self.chapters and getattr(self, "act_dialogue", None) is not None \
                and self.act_dialogue.isChecked():
            self._render_chapter(self.current_index)

    def _toggle_dialogue(self, checked: bool = None) -> None:
        """对话着色开关：只重渲染当前章，不动原始数据。"""
        if checked is None:
            checked = self.act_dialogue.isChecked()
        self._save_setting("dialogue_color", bool(checked))
        if self.chapters:
            self._render_chapter(self.current_index)
        self.status_label.setText(
            ("已开启对话着色（不同人物不同颜色，仅显示效果）" if checked else "已关闭对话着色")
            + (f" · 《{self.current_title}》" if self.current_title else "")
        )

    def _toggle_catalog(self, checked: bool = None) -> None:
        """折叠/展开章节目录（专注模式下不强制展开）。"""
        if checked is None:
            checked = self.btn_catalog.isChecked()
        self.catalog_list.setVisible(not checked)
        self.btn_catalog.setText(
            tr("reader.catalog_show") if checked else tr("reader.catalog"))
        self.btn_catalog.setToolTip(
            tr("reader.catalog_show_tip") if checked else tr("reader.catalog_tip"))

    def _toggle_focus(self, checked: bool) -> None:
        """简洁模式：隐藏工具栏与目录。"""
        for i in range(self.toolbar.count()):
            item = self.toolbar.itemAt(i)
            w = item.widget()
            if w:
                w.setVisible(not checked)
        # 专注模式下隐藏目录；退出后按目录按钮的折叠状态恢复
        self.catalog_list.setVisible(
            (not checked) and not self.btn_catalog.isChecked())
        self.btn_focus.setText(tr("reader.exit_focus") if checked else tr("reader.focus"))
        # 专注模式下仍保留一个退出按钮（当前按钮本身在工具栏里会被隐藏，用快捷键 Esc 恢复）
        if checked:
            QShortcut(QKeySequence("Escape"), self, lambda: (self.btn_focus.setChecked(False), self._toggle_focus(False)))

    # ------------------------------------------------------------------ 听书

    def _on_playing_changed(self, playing: bool) -> None:
        """引擎播放状态变化时同步按钮与状态栏。"""
        self._speaking = playing
        if hasattr(self, "act_speak"):
            self.act_speak.setChecked(playing)
            self.act_speak.setText(tr("reader.stop") if playing else tr("reader.speak"))
        if hasattr(self, "status_label"):
            if playing:
                profile = get_voice(self._voice_id)
                engine = tr("voice.engine_edge") if edge_available() else tr("voice.engine_system")
                self.status_label.setText(tr(
                    "voice.now_playing_speed",
                    name=tr(profile.i18n_name), engine=engine,
                    speed=self._speed_label(self._speed)))
            else:
                self.status_label.setText(f"正在阅读《{self.current_title}》")

    def _toggle_speech(self, checked: bool) -> None:
        if not self.tts_available:
            return
        if checked:
            text = self.chapters[self.current_index]["content"][:3000] if self.chapters else ""
            if not text:
                self.act_speak.setChecked(False)
                return
            profile = get_voice(self._voice_id)
            ok = self.tts_engine.speak(text, profile)
            if not ok:
                self.act_speak.setChecked(False)
                self.status_label.setText(tr("voice.play_failed"))
        else:
            self.tts_engine.stop()
            self._on_playing_changed(False)

    def _choose_voice(self) -> None:
        """打开音色选择对话框；选择后保存并提示，试听走同一引擎。"""
        def _preview(profile: VoiceProfile) -> None:
            sample = sample_for_language(
                str(self.store.settings_get("language", "zh_CN") or "zh_CN"))
            self.tts_engine.speak(sample, profile)

        picked = VoiceDialog.pick(
            self._voice_id, self, preview_cb=_preview,
            current_speed=self._speed,
            speed_cb=lambda speed: self._set_speed(speed))
        if picked:
            self._voice_id = picked
            self.store.settings_set("read_voice", picked)
            # 若正在朗读，用新音色重读当前内容
            if self._speaking and self.chapters:
                profile = get_voice(picked)
                self.tts_engine.speak(
                    self.chapters[self.current_index]["content"][:3000], profile)
            else:
                profile = get_voice(picked)
                self.status_label.setText(
                    tr("voice.selected", name=tr(profile.i18n_name)))

    def _import_audio(self) -> None:
        """通过音频导入接口播放本地音频文件（复用倍速/停止/音量控制）。"""
        path = _fd.open_file(
            self, tr("voice.import_audio"), "", TTSEngine.audio_filter())
        if not path:
            return
        ok = self.tts_engine.import_audio(path)
        if ok:
            self.act_speak.setChecked(True)
            self.status_label.setText(tr(
                "voice.import_playing",
                name=os.path.basename(path), speed=self._speed_label(self._speed)))
        else:
            self.status_label.setText(tr("voice.import_failed"))

    def _stop_speech(self) -> None:
        """供切章/关书时调用。"""
        try:
            self.tts_engine.stop()
        except Exception:  # noqa: BLE001
            pass
        self._on_playing_changed(False)

    # ------------------------------------------------------------------ 进度与设置

    def _save_progress(self) -> None:
        if not self.current_key or not self.chapters:
            return
        scroll = self.text.verticalScrollBar().value()
        maximum = self.text.verticalScrollBar().maximum()
        percent = scroll / maximum if maximum > 0 else 0.0
        overall = (self.current_index + percent) / len(self.chapters) * 100
        self.store.progress_save(
            self.current_key, self.current_index, scroll, round(overall, 2),
            extra={"title": self.current_title, "path": self.current_path},
        )
        self.progress_changed.emit(self.current_key, self.current_index, overall)

    def _log_read(self) -> None:
        if not self.current_title:
            return
        self.store.read_history_add({
            "title": self.current_title,
            "path": self.current_path,
            "key": self.current_key,
            "action": "open",
        })

    def _save_setting(self, key: str, value: Any) -> None:
        self.store.settings_set(key, value)

    def _load_settings(self) -> None:
        size = self.store.settings_get("font_size", 18)
        if isinstance(size, int):
            self.size_slider.setValue(size)
        theme = self.store.settings_get("reader_theme", PAPER_FOLLOW)
        idx = self.theme_combo.findData(str(theme))
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        # 恢复模式下拉（默认滚动）
        mode = self.store.settings_get("reader_mode", "滚动")
        midx = self.mode_combo.findData(str(mode))
        if midx >= 0:
            self.mode_combo.setCurrentIndex(midx)
        # 恢复对话着色开关（blockSignals 避免在 UI 就绪前触发重渲染）
        dialogue = bool(self.store.settings_get("dialogue_color", False))
        self.act_dialogue.blockSignals(True)
        self.act_dialogue.setChecked(dialogue)
        self.act_dialogue.blockSignals(False)
        # 恢复连续自动阅读开关（默认关闭，避免 unexpectedly 自动翻页）
        auto = bool(self.store.settings_get("auto_read", False))
        self.act_auto.blockSignals(True)
        self.act_auto.setChecked(auto)
        self.act_auto.blockSignals(False)
        self._auto_read = auto
        if auto:
            self.act_auto.setText("⏸ 停止自动")
        # 恢复听书音色
        self._voice_id = str(self.store.settings_get("read_voice", DEFAULT_VOICE_ID) or DEFAULT_VOICE_ID)
        if not get_voice(self._voice_id).id == self._voice_id:
            self._voice_id = DEFAULT_VOICE_ID
        # 恢复听书倍速
        self._set_speed(self.store.settings_get("read_speed", 1.0), persist=False)

    def on_app_theme_changed(self) -> None:
        """主界面深浅切换时调用：纸张为"跟随界面"则即时重算。"""
        try:
            if (self.theme_combo.currentData() or PAPER_FOLLOW) == PAPER_FOLLOW:
                self._apply_theme()
        except Exception:  # noqa: BLE001
            pass

    def retranslate(self) -> None:
        """语言切换：刷新阅读页所有文案。"""
        self.btn_open.setText(tr("reader.open_local"))
        self.btn_prev.setText(tr("reader.prev"))
        self.btn_next.setText(tr("reader.next"))
        if hasattr(self, "btn_catalog"):
            collapsed = self.btn_catalog.isChecked()
            self.btn_catalog.setText(
                tr("reader.catalog_show") if collapsed else tr("reader.catalog"))
        self.btn_focus.setText(tr("reader.exit_focus") if self.btn_focus.isChecked()
                               else tr("reader.focus"))
        self.btn_settings_menu.setText(tr("reader.settings") + "  ▾")
        self.btn_more_menu.setText(tr("reader.more") + "  ▾")
        self.act_speak.setText(tr("reader.stop") if self._speaking else tr("reader.speak"))
        if hasattr(self, "act_voice"):
            self.act_voice.setText(tr("voice.menu"))
        if hasattr(self, "act_import_audio"):
            self.act_import_audio.setText(tr("voice.import_audio"))
        if hasattr(self, "speed_menu"):
            self.speed_menu.setTitle(tr("voice.speed"))
            for value, act in self.speed_actions.items():
                act.setText(self._speed_label(value))
        self.act_dialogue.setText(tr("reader.dialogue"))
        self.act_auto.setText(tr("reader.auto_stop") if self._auto_read else tr("reader.auto"))
        # 菜单里的标签与下拉项
        if hasattr(self, "size_value_label"):
            pass
        # 主题/模式下拉显示文案（保留 userData）
        for i, key in enumerate(THEMES.keys()):
            self.theme_combo.setItemText(i, tr(_THEME_LABELS.get(key, "reader.theme.light")))
        self.mode_combo.setItemText(0, tr("reader.mode.scroll"))
        self.mode_combo.setItemText(1, tr("reader.mode.page"))
        # 欢迎页
        if hasattr(self, "welcome_title"):
            self.welcome_title.setText(tr("reader.welcome_title"))
            self.welcome_sub.setText(tr("reader.welcome_sub"))
            self.btn_primary_open.setText("＋ " + tr("reader.open_local"))
            self.btn_from_shelf.setText(tr("reader.from_shelf"))
            self.btn_drop_hint.setText(tr("reader.drop_file"))
            self._refresh_cover_wall()
        if not self.chapters:
            self.status_label.setText(tr("reader.status_empty"))

    def closeEvent(self, event) -> None:
        try:
            self.tts_engine.cleanup()
        except Exception:  # noqa: BLE001
            pass
        if self.current_key:
            self._save_progress()
        super().closeEvent(event)
