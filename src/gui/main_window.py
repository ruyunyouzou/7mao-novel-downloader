# -*- coding: utf-8 -*-
"""
桌面 GUI 主窗口（深色主题，复刻番茄小说下载器视觉风格）。

布局：
- 自定义标题栏：logo / 应用名 / 版本 / 主题切换 / 窗口控制
- 左侧导航栏：搜索 / 书架 / 阅读 / 任务队列 / 历史 / 统计 / 插件 / 设置 / 插件注入页
- 内容区：搜索页（左结果 + 右详情）与各功能视图
- 状态栏：日志输出

功能逻辑与原版完全一致，仅 UI 骨架与视觉焕新。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QTextCursor, QIcon, QKeySequence, QShortcut, QColor
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QLabel, QTextEdit, QProgressBar, QFileDialog, QMessageBox, QComboBox, QScrollArea, QCheckBox, QSystemTrayIcon, QMenu, QStackedWidget, QColorDialog

from .plugin import PluginManager, Hook, DownloadResult
from .workers import SearchWorker, DownloadWorker, DetailWorker
from .datastore import DataStore
from .i18n import tr, manager as i18n_manager, LANGUAGES
from .views import ShelfView, QueueView, HistoryView, ReaderView, StatsView
from .widgets import TitleBar, Card, section_title, hint_label
from .side_nav import SideNavWidget
from . import motion, notify_core
from .style import (QSS, LIGHT_QSS, BORDER, TEXT, TEXT_DIM, ACCENT, WARN,
                    SUCCESS, accent_button, recolor_accent, accent_color)


# 导航标签定义（图标 + i18n key）
NAV_ITEMS = [
    ("🔍", "nav.search"),
    ("📚", "nav.shelf"),
    ("📖", "nav.read"),
    ("⬇", "nav.queue"),
    ("🕘", "nav.history"),
    ("🔌", "nav.plugins"),
    ("⚙", "nav.settings"),
]
NAV_ICONS = {key: icon for icon, key in NAV_ITEMS}
NAV_ICONS["nav.statistics"] = "📊"

# 首屏"小说推荐"轮询使用的热门关键词（无官方热搜接口时的本地兜底）
RECOMMEND_KEYWORDS = [
    "斗破苍穹", "诡秘之主", "遮天", "深海余烬", "大奉打更人",
    "吞噬星空", "武动乾坤", "完美世界", "凡人修仙传", "雪中悍刀行",
    "剑来", "灵境行者", "夜的命名术", "大乘期才有逆袭系统", "光阴之外",
]


def _title_color_icon(value: str, size: int = 14) -> QIcon:
    """为标题颜色下拉生成色块图标：纯色填充或渐变条。"""
    from .nation_theme import color_swatch_icon
    return color_swatch_icon(value, size)


class MainWindow(QMainWindow):
    """主窗口。"""

    def __init__(self, plugin_manager: PluginManager) -> None:
        super().__init__()
        self.pm = plugin_manager
        self.pm.app = self
        for p in self.pm.plugins:
            p.app = self

        self.current_book: Optional[Dict[str, Any]] = None
        self.search_results: List[Dict[str, Any]] = []
        self.search_worker: Optional[SearchWorker] = None
        self.download_worker: Optional[DownloadWorker] = None
        self.detail_worker: Optional[DetailWorker] = None
        self.default_save_dir = os.path.join(os.path.expanduser("~"), "七猫小说")
        self._dark_theme = True
        self._dynamic_bg = None  # 当前动态背景 widget（若背景为动态）
        self._system_scheme_connected = False  # 是否已监听系统深浅色变化
        # 搜索页运行态
        self._recommend_index = -1      # 推荐关键词轮询游标
        self._current_recommend_kw = ""  # 当前推荐关键词
        self._last_search_tag = ""       # 最近一次搜索来源：search / recommend

        self.store = DataStore()
        # 动效模块读取设置：ui_motion / ui_motion_level / ui_low_power
        motion.bind_settings(lambda k, d: self.store.settings_get(k, d))
        # 通知/提示音模块读取设置
        notify_core.bind_settings(lambda k, d: self.store.settings_get(k, d))
        # 渲染质量模块读取设置（ui_antialias）
        from . import render_quality
        render_quality.bind_settings(lambda k, d: self.store.settings_get(k, d))
        self.shelf_view: Optional[ShelfView] = None
        self.reader_view: Optional[ReaderView] = None
        self.queue_view: Optional[QueueView] = None
        self.history_view: Optional[HistoryView] = None
        self.stats_view: Optional[StatsView] = None
        self.settings_view: Optional[QWidget] = None
        # 每个导航项 (i18n key, 图标)，用于语言切换后重建标签文字
        self._nav_tab_specs: List[Tuple[str, str]] = []

        # 绑定部件配色中心（国徽/文字/功能背景）；必须在构建 UI 前绑定
        from . import nation_theme
        nation_theme.bind_settings(lambda k, d: self.store.settings_get(k, d))

        # 绑定 UI 背景配色中心（内容区整体背景）；与功能卡片背景无关
        from . import ui_background_theme
        ui_background_theme.bind_settings(lambda k, d: self.store.settings_get(k, d))

        # 绑定语言持久化；必须在构建 UI 前载入已保存语言
        i18n_manager().bind_store(
            lambda k, d: self.store.settings_get(k, d),
            lambda k, v: self.store.settings_set(k, v),
        )
        i18n_manager().changed.connect(self._retranslate)

        self.setWindowTitle(f"{tr('app.name')} · GUI")
        self.resize(1180, 760)
        # 窗口图标（与托盘、任务栏一致）
        from .resource_path import resource_path
        _icon_path = resource_path("main.ico")
        if os.path.isfile(_icon_path):
            self.setWindowIcon(QIcon(_icon_path))
        self._apply_stylesheet()

        self._build_ui()
        self._apply_plugin_extensions()
        self._arrange_nav()
        self._apply_startup_page()
        self._apply_theme_mode_on_startup()

        self.log("程序启动完成。插件已加载，可在「插件」标签页查看。")
        downloader = self.pm.get_downloader()
        if downloader is None:
            self.log("⚠ 未检测到下载内核插件：开源版不含章节下载逻辑，需安装提供内核的插件。")
        else:
            self.log(f"✓ 已加载下载内核：{downloader.name}")

        self._force_quit = False
        self._shortcuts: list = []
        self._setup_tray()
        self._setup_shortcuts()
        # 启动时自动检查更新（延迟 3 秒，不阻塞界面）
        if self.store.settings_get("auto_update_check", False):
            QTimer.singleShot(3000, self._silent_check_update)

    def _system_theme_is_dark(self) -> bool:
        """读取系统当前明暗色；读取失败时沿用当前主题。"""
        from PySide6.QtWidgets import QApplication
        try:
            app = QApplication.instance()
            hints = app.styleHints() if app else None
            scheme = hints.colorScheme() if hints is not None else None
            return "dark" in str(scheme).lower()
        except Exception:  # noqa: BLE001
            return self._dark_theme

    def _connect_system_scheme_listener(self) -> None:
        """连接系统明暗色变化信号；重复调用不会重复连接。"""
        if self._system_scheme_connected:
            return
        from PySide6.QtWidgets import QApplication
        try:
            app = QApplication.instance()
            hints = app.styleHints() if app else None
            if hints is not None and hasattr(hints, "colorSchemeChanged"):
                hints.colorSchemeChanged.connect(self._on_system_scheme_changed)
                self._system_scheme_connected = True
        except Exception:  # noqa: BLE001
            pass

    def _sync_theme_mode_combo(self, mode: str) -> None:
        """把当前主题模式同步到「主题与外观」下拉框，避免两个入口显示不一致。"""
        combo = getattr(self, "theme_mode_combo", None)
        if combo is None:
            return
        idx = combo.findData(mode)
        if idx < 0:
            return
        combo.blockSignals(True)
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def set_theme_mode(self, mode: str, *, persist: bool = True) -> None:
        """统一主题入口：持久化 theme_mode，并同步下拉框、按钮、背景与实际主题。"""
        mode = str(mode or "dark")
        if mode not in ("dark", "light", "system", "dynamic"):
            mode = "dark"
        if persist:
            self.store.settings_set("theme_mode", mode)
        self._sync_theme_mode_combo(mode)

        if mode == "dynamic":
            key = ""
            for provider in self.pm.get_backgrounds():
                p_key = str(getattr(provider, "key", "") or "")
                p_name = str(getattr(provider, "name", "") or "")
                if "flow" in p_key.lower() or "gradient" in p_key.lower() or "流光" in p_name:
                    key = p_key
                    break
            current_bg = str(self.store.settings_get("background_key", "") or "")
            if current_bg != key:
                self.set_background(key)
            # 动态光效沿用深色主题，保证流光背景与控件可读性一致
            if not self._dark_theme:
                self._on_theme_toggled(True)
            return

        if mode in ("dark", "light"):
            # 普通深浅主题恢复默认纯色背景；已有背景时才重建样式，避免多余日志
            if str(self.store.settings_get("background_key", "") or ""):
                self.set_background("")
            dark = (mode == "dark")
        else:
            self._connect_system_scheme_listener()
            dark = self._system_theme_is_dark()

        if dark != self._dark_theme:
            self._on_theme_toggled(dark)

    def _apply_theme_mode_on_startup(self) -> None:
        """按持久化的主题模式恢复启动主题。"""
        mode = str(self.store.settings_get("theme_mode", "dark") or "dark")
        self.set_theme_mode(mode, persist=False)

    def _on_system_scheme_changed(self, scheme) -> None:
        """系统明暗色变化时，若当前为跟随系统模式则同步主题。"""
        mode = str(self.store.settings_get("theme_mode", "dark") or "dark")
        if mode != "system":
            return
        try:
            dark = "dark" in str(scheme).lower()
        except Exception:  # noqa: BLE001
            return
        if dark != self._dark_theme:
            self._on_theme_toggled(dark)

    def _apply_startup_page(self) -> None:
        """按设置跳到启动页面。"""
        page = str(self.store.settings_get("startup_page", "search") or "search")
        key_map = {"search": "nav.search", "shelf": "nav.shelf", "read": "nav.read",
                   "queue": "nav.queue", "history": "nav.history", "stats": "nav.statistics"}
        target = key_map.get(page, "nav.search")
        for i, (key, _icon) in enumerate(self._nav_tab_specs):
            if key == target and 0 <= i < self.tabs.count():
                self.tabs.setCurrentIndex(i)
                break

    def _silent_check_update(self) -> None:
        """后台静默检查更新：普通更新只记日志；强制更新弹不可跳过对话框。"""
        from PySide6.QtCore import QThread, Signal

        class _SilentUpdateWorker(QThread):
            done = Signal(dict)

            def run(self):
                from .update_core import check_update
                self.done.emit(check_update())

        def on_done(r):
            if r.get("force"):
                from .update_dialog import show_update_dialog
                show_update_dialog(r, self)
            elif r["has_update"]:
                self.log(f"🆕 {r['message']}：{r.get('url', '')}")
            elif r["ok"]:
                self.log(f"更新检查：{r['message']}")

        worker = _SilentUpdateWorker(self)
        worker.done.connect(on_done)
        worker.start()
        # 保活引用直到线程结束，避免被 GC 导致 "QThread destroyed while running"
        self._silent_update_worker = worker

    def _setup_tray(self) -> None:
        """创建系统托盘（若平台支持且设置开启）。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        self.tray = QSystemTrayIcon(self)
        from .resource_path import resource_path
        icon_path = resource_path("main.ico")
        if os.path.isfile(icon_path):
            self.tray.setIcon(QIcon(icon_path))
        else:
            from PySide6.QtWidgets import QStyle
            self.tray.setIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        self.tray.setToolTip(tr("app.name"))
        menu = QMenu()
        act_show = menu.addAction(tr("tray.show"))
        act_show.triggered.connect(self._restore_from_tray)
        act_quit = menu.addAction(tr("tray.quit"))
        act_quit.triggered.connect(self._quit_from_tray)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self._restore_from_tray()

    def _restore_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self) -> None:
        self._force_quit = True
        self.close()

    def _setup_shortcuts(self) -> None:
        """注册应用内全局快捷键（窗口激活时生效，覆盖所有页面）。"""
        def add(seq: str, slot) -> None:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(slot)
            self._shortcuts.append(sc)

        # Ctrl+1..9：快速切换导航页（按当前实际页数量绑定）
        for i in range(min(9, self.tabs.count())):
            add(f"Ctrl+{i + 1}", lambda idx=i: self.tabs.setCurrentIndex(idx))
        # Ctrl+F：跳到搜索页并聚焦搜索框
        add("Ctrl+F", self._focus_search)
        # Ctrl+Space：全局快速搜索面板
        add("Ctrl+Space", self._open_quick_search)
        # Esc：最小化到托盘（若托盘关闭则最小化窗口）
        add("Esc", self._minimize_to_tray)

    def _open_quick_search(self) -> None:
        """唤起全局快速搜索面板。"""
        from .quick_search import QuickSearchDialog
        dlg = QuickSearchDialog(self, dark=getattr(self, "_dark_theme", True))
        dlg.show_at_center()

    def _focus_search(self) -> None:
        self.tabs.setCurrentIndex(0)
        if hasattr(self, "search_edit"):
            self.search_edit.setFocus()
            self.search_edit.selectAll()

    def _minimize_to_tray(self) -> None:
        if self.tray is not None and self.store.settings_get("tray_enabled", True):
            self.hide()
            self.log("已最小化到系统托盘（双击托盘图标或 Ctrl+1 返回）。")
        else:
            self.showMinimized()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        """构建界面骨架：标题栏 + 导航栏 + 内容区 + 状态栏。"""
        # 保留隐藏菜单栏以兼容插件 register_menus（示例插件会调用 menuBar()）
        self.menuBar().hide()

        central = QWidget()
        central.setObjectName("appCentral")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题栏
        self.title_bar = TitleBar(self)
        self.title_bar.theme_toggled.connect(
            lambda dark: self.set_theme_mode("dark" if dark else "light"))
        self.title_bar.account_clicked.connect(self._open_account_dialog)
        self._refresh_account_button()
        root.addWidget(self.title_bar)

        # 左侧导航栏 + 右侧页面
        self.tabs = SideNavWidget(tr("app.name"), self)
        root.addWidget(self.tabs, 1)

        self.setCentralWidget(central)
        self._build_statusbar()

        # 各页面
        self._build_search_page()
        self._build_shelf_page()
        self._build_reader_page()
        self._build_queue_page()
        self._build_history_page()
        self._build_stats_page()
        self._build_plugin_page()
        self._build_settings_page()

        # tabs 与各页面已就绪，补挂一次动态背景（__init__ 早期调用时尚无 tabs）
        self._sync_dynamic_background(self._active_background())
        self._update_bg_pause_visibility()
        # 给所有滚动区域装平滑滚动（表格/文本域/滚动页）
        motion.install_smooth_scrolling(self)

    def _build_search_page(self) -> None:
        """搜索页：搜索栏（含搜书/搜作者、热搜词）+ 卡片结果列表 + 右侧详情。"""
        from .book_card import BookCardList
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        # ---- 顶部工具标签栏：搜索 / 直达 / 历史书签（方案 C）----
        from .widgets import apply_nation_icon
        self._search_tool_tabs: list = []
        self._search_tool_tab_specs = [
            ("search.tab_search", "mondstadt"),
            ("search.tab_direct", "snezhnaya"),
            ("search.tab_history", "liyue"),
        ]
        tab_bar = QHBoxLayout()
        tab_bar.setContentsMargins(0, 0, 0, 0)
        tab_bar.setSpacing(8)
        for i, (ikey, nation) in enumerate(self._search_tool_tab_specs):
            tb = QPushButton(tr(ikey))
            tb.setObjectName("searchToolTab")
            tb.setCheckable(True)
            tb.setChecked(i == 0)
            tb._nation = nation
            tb._nation_size = 16
            tb._nation_fixed_color = True  # 选中/未选中两色，自己管理
            tb.clicked.connect(lambda _c=False, idx=i: self._on_search_tool_tab(idx))
            tab_bar.addWidget(tb)
            self._search_tool_tabs.append(tb)
        tab_bar.addStretch()
        layout.addLayout(tab_bar)
        self._refresh_search_tool_tab_icons()

        # ---- 堆叠面板：三个标签在同一区域复用 ----
        self.search_tool_stack = QStackedWidget()
        # 统一高度策略：始终取最高面板的尺寸，避免切换时内容区跳动
        from PySide6.QtWidgets import QSizePolicy
        self.search_tool_stack.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.MinimumExpanding)

        # 面板 0：搜索（搜索栏卡片 + 热搜词行）
        search_panel = QWidget()
        sp = QVBoxLayout(search_panel)
        sp.setContentsMargins(0, 0, 0, 0)
        sp.setSpacing(8)

        search_card = Card()
        bar = QHBoxLayout(search_card)
        bar.setContentsMargins(14, 10, 14, 10)
        bar.setSpacing(8)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("search.placeholder"))
        self.search_edit.setMinimumWidth(220)
        self.search_edit.returnPressed.connect(self.do_search)
        bar.addWidget(self.search_edit, 1)

        # 搜书 / 搜作者 切换
        self.tab_combo = QComboBox()
        self.tab_combo.addItems([tr("search.by_title"), tr("search.by_author")])
        self.tab_combo.setToolTip(tr("search.switch_type"))
        self.tab_combo.currentIndexChanged.connect(self._on_search_tab_changed)
        bar.addWidget(self.tab_combo)

        self.search_btn = QPushButton(tr("search.button"))
        accent_button(self.search_btn)
        apply_nation_icon(self.search_btn, "mondstadt", "#1a1a1a", 16)
        self.search_btn.clicked.connect(self.do_search)
        bar.addWidget(self.search_btn)

        self.format_label = QLabel(tr("search.format"))
        bar.addWidget(self.format_label)
        self.format_combo = QComboBox()
        self.format_combo.addItems(["txt", "epub", "chapter"])
        bar.addWidget(self.format_combo)

        self.download_btn = QPushButton(tr("search.add_queue"))
        accent_button(self.download_btn)
        apply_nation_icon(self.download_btn, "inazuma", "#1a1a1a", 16)
        self.download_btn.clicked.connect(self.do_download)
        bar.addWidget(self.download_btn)
        sp.addWidget(search_card)

        # 热搜词行
        hot_row = QHBoxLayout()
        hot_row.setSpacing(6)
        self.hot_prefix_label = hint_label(tr("search.hot_prefix"))
        hot_row.addWidget(self.hot_prefix_label)
        self.hot_words = ["斗破苍穹", "诡秘之主", "遮天", "深海余烬", "大奉打更人"]
        self._hot_word_buttons = []
        for w in self.hot_words:
            btn = QPushButton(w)
            btn.setFlat(True)
            btn.setStyleSheet(f"color: {ACCENT}; font-size: 12px; border: none; padding: 2px 6px;")
            btn.clicked.connect(lambda _, k=w: (self.search_edit.setText(k), self.do_search()))
            hot_row.addWidget(btn)
            self._hot_word_buttons.append(btn)
        hot_row.addStretch()
        sp.addLayout(hot_row)
        self.search_tool_stack.addWidget(search_panel)

        # 面板 1 / 2：直达 / 历史书签（build_search_tools 返回两个面板）
        from .search_tools import build_search_tools
        direct_panel, history_panel = build_search_tools(self)
        self.search_tools_direct = direct_panel
        self.search_tools_history = history_panel
        self.search_tools = direct_panel  # 兼容旧引用（原为完整工具条 widget）
        self.search_tool_stack.addWidget(direct_panel)
        self.search_tool_stack.addWidget(history_panel)
        # 搜索页与链接直达页的格式下拉双向同步，始终保持一致
        self.format_combo.currentTextChanged.connect(self._on_main_format_changed)
        self.direct_format_combo.currentTextChanged.connect(self._on_direct_format_changed)

        layout.addWidget(self.search_tool_stack)

        # ---- 内容区：卡片列表 + 详情 ----
        splitter = QSplitter(Qt.Horizontal)
        splitter.setStyleSheet("QSplitter::handle { background: transparent; width: 6px; }")

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        # 带"换一批"的标题行
        title_row = QHBoxLayout()
        self.result_title = section_title(tr("search.recommend_title"))
        title_row.addWidget(self.result_title)
        title_row.addStretch()
        self.refresh_btn = QPushButton(tr("search.refresh"))
        self.refresh_btn.setMaximumWidth(110)
        from .widgets import apply_nation_icon
        apply_nation_icon(self.refresh_btn, "liyue", ACCENT, 16)
        self.refresh_btn.clicked.connect(self._refresh_recommend)
        title_row.addWidget(self.refresh_btn)
        left_layout.addLayout(title_row)
        self.result_hint = hint_label(tr("search.recommend_hint"))
        left_layout.addWidget(self.result_hint)

        self.card_list = BookCardList()
        self.card_list.book_selected.connect(self._on_card_selected)
        left_layout.addWidget(self.card_list)

        # 隐藏的兼容表格（保留数据供详情/选择逻辑使用，不显示）
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(4)
        self.result_table.setHorizontalHeaderLabels([
            tr("search.col_title"), tr("search.col_author"),
            tr("search.col_words"), tr("search.col_id"),
        ])
        self.result_table.hide()
        left_layout.addWidget(self.result_table)
        splitter.addWidget(left)

        right = QWidget()
        self.detailPane = right
        right.setObjectName("detailPane")
        right.setStyleSheet("QWidget#detailPane { border-left: 1px solid #2c2c2c; }")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 0, 0, 0)
        right_layout.setSpacing(6)
        self.detail_section_title = section_title(tr("search.detail_title"))
        right_layout.addWidget(self.detail_section_title)
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setPlaceholderText(tr("search.detail_placeholder"))
        right_layout.addWidget(self.detail_text)
        # 选中书后可直接从这里加入下载队列
        self.detail_download_btn = QPushButton(tr("search.add_queue"))
        self.detail_download_btn.setEnabled(False)
        accent_button(self.detail_download_btn)
        apply_nation_icon(self.detail_download_btn, "inazuma", "#1a1a1a", 16)
        self.detail_download_btn.clicked.connect(self.do_download)
        right_layout.addWidget(self.detail_download_btn)
        # 收藏书签
        self.btn_bookmark = QPushButton("☆ " + tr("common.bookmark_add"))
        self.btn_bookmark.clicked.connect(self._toggle_bookmark)
        right_layout.addWidget(self.btn_bookmark)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self._add_nav_tab(page, "nav.search")
        # 首屏加载推荐
        QTimer.singleShot(200, self._load_recommend)

    def _refresh_search_tool_tab_icons(self) -> None:
        """刷新搜索页顶部标签的国徽图标：选中态深色，未选中态主题色。"""
        if not hasattr(self, "_search_tool_tabs"):
            return
        from .widgets import nation_icon
        key = str(self.store.settings_get("accent_color", "purple") or "purple")
        color = accent_color(key)
        for i, tb in enumerate(self._search_tool_tabs):
            nation = getattr(tb, "_nation", None)
            if not nation:
                continue
            icon_color = "#1a1a1a" if tb.isChecked() else color
            tb.setIcon(nation_icon(nation, icon_color, 16))
            tb.setIconSize(QSize(16, 16))

    def _on_search_tool_tab(self, index: int) -> None:
        """切换搜索页顶部工具面板，并刷新标签选中态与图标。"""
        if not hasattr(self, "search_tool_stack"):
            return
        self.search_tool_stack.setCurrentIndex(index)
        for i, tb in enumerate(getattr(self, "_search_tool_tabs", [])):
            tb.setChecked(i == index)
        self._refresh_search_tool_tab_icons()

    def _on_main_format_changed(self, fmt: str) -> None:
        """搜索页格式变化：同步到直达页下拉（不重复写设置）。"""
        dc = getattr(self, "direct_format_combo", None)
        if dc is not None and dc.currentText() != fmt:
            dc.blockSignals(True)
            dc.setCurrentText(fmt)
            dc.blockSignals(False)

    def _on_direct_format_changed(self, fmt: str) -> None:
        """直达页格式变化：同步到搜索页下拉并持久化。"""
        fc = getattr(self, "format_combo", None)
        if fc is not None and fc.currentText() != fmt:
            fc.blockSignals(True)
            fc.setCurrentText(fmt)
            fc.blockSignals(False)
        self.store.settings_set("default_format", fmt)

    def _build_shelf_page(self) -> None:
        self.shelf_view = ShelfView(self.store, self)
        self.shelf_view.request_download.connect(self._on_request_redownload)
        self.shelf_view.request_read.connect(self._on_request_read)
        self.shelf_view.request_weread_import.connect(self._on_weread_import)
        self.shelf_view.request_calibre_edit.connect(self._on_calibre_edit)
        self.shelf_view.request_follow.connect(self._open_follow_dialog)
        self._add_nav_tab(self.shelf_view, "nav.shelf")

    def _build_reader_page(self) -> None:
        self.reader_view = ReaderView(self.store, self)
        self.reader_view.progress_changed.connect(self._on_read_progress)
        self._add_nav_tab(self.reader_view, "nav.read")

    def _build_queue_page(self) -> None:
        self.queue_view = QueueView(self.store, lambda: self.pm.get_downloader(), self)
        self.queue_view.task_finished.connect(self._on_queue_task_finished)
        self._add_nav_tab(self.queue_view, "nav.queue")

    def _build_history_page(self) -> None:
        self.history_view = HistoryView(self.store, self)
        self.history_view.request_retry.connect(self._on_request_redownload)
        self._add_nav_tab(self.history_view, "nav.history")

    def _build_stats_page(self) -> None:
        self.stats_view = StatsView(self.store, self)
        self._add_nav_tab(self.stats_view, "nav.statistics")

    def _build_plugin_page(self) -> None:
        """插件管理页：分组卡片墙（方案一 · 单列）+ 添加插件功能。"""
        from .views.plugins_view import PluginsView
        self.plugins_view = PluginsView(self.pm, self.store, self, self)
        self._add_nav_tab(self.plugins_view, "nav.plugins")

    def _build_settings_page(self) -> None:
        """设置页：下载、外观、语言、数据管理、关于。"""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(16, 12, 16, 12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(16)

        # 下载设置卡片
        card1 = Card()
        l1 = QVBoxLayout(card1)
        l1.setContentsMargins(16, 14, 16, 14)
        l1.setSpacing(10)
        self.set_download_title = section_title(tr("set.download"), "mondstadt")
        l1.addWidget(self.set_download_title)
        self.set_default_dir_label = hint_label(tr("set.default_dir"))
        l1.addWidget(self.set_default_dir_label)
        row = QHBoxLayout()
        self.settings_path = QLabel(self.default_save_dir)
        self.settings_path.setStyleSheet(f"color: {TEXT_DIM};")
        row.addWidget(self.settings_path, 1)
        self.settings_choose_btn = QPushButton(tr("set.choose_dir"))
        self.settings_choose_btn.clicked.connect(self._choose_save_dir)
        row.addWidget(self.settings_choose_btn)
        l1.addLayout(row)
        self.set_default_format_label = hint_label(tr("set.default_format"))
        l1.addWidget(self.set_default_format_label)
        self.settings_format = QComboBox()
        self.settings_format.addItems(["txt", "epub", "chapter"])
        # 载入持久化默认值，并同步到搜索页下拉
        default_fmt = str(self.store.settings_get("default_format", "txt") or "txt")
        if self.settings_format.findText(default_fmt) < 0:
            default_fmt = "txt"
        self.settings_format.setCurrentText(default_fmt)
        self.format_combo.setCurrentText(default_fmt)
        self.settings_format.currentTextChanged.connect(self._on_default_format_changed)
        l1.addWidget(self.settings_format)
        # 文件名模板
        self.set_tpl_label = hint_label(tr("set.filename_tpl"))
        l1.addWidget(self.set_tpl_label)
        row_tpl = QHBoxLayout()
        self.settings_template = QLineEdit(
            str(self.store.settings_get("filename_template", "{title}") or "{title}")
        )
        self.settings_template.setPlaceholderText("{title}")
        row_tpl.addWidget(self.settings_template, 1)
        self.settings_tpl_save_btn = QPushButton(tr("common.save"))
        self.settings_tpl_save_btn.setMaximumWidth(70)
        self.settings_tpl_save_btn.clicked.connect(self._save_filename_template)
        row_tpl.addWidget(self.settings_tpl_save_btn)
        l1.addLayout(row_tpl)
        self.set_tpl_vars_label = hint_label(tr("set.tpl_vars"))
        l1.addWidget(self.set_tpl_vars_label)
        layout.addWidget(card1)

        # 外观设置卡片
        card2 = Card()
        l2 = QVBoxLayout(card2)
        l2.setContentsMargins(16, 14, 16, 14)
        l2.setSpacing(10)
        self.set_appearance_title = section_title(tr("set.appearance"), "liyue")
        l2.addWidget(self.set_appearance_title)
        self.theme_status = QLabel(
            tr("set.current_theme",
               theme=tr("set.theme_dark" if self._dark_theme else "set.theme_light"))
        )
        self.theme_status.setStyleSheet(f"color: {ACCENT};")
        l2.addWidget(self.theme_status)
        self.settings_theme_btn = QPushButton(
            tr("set.switch_light" if self._dark_theme else "set.switch_dark")
        )
        self.settings_theme_btn.setMaximumWidth(180)
        self.settings_theme_btn.clicked.connect(
            lambda: self.set_theme_mode("light" if self._dark_theme else "dark")
        )
        l2.addWidget(self.settings_theme_btn)
        layout.addWidget(card2)

        # 语言设置卡片
        card_lang = Card()
        l_lang = QVBoxLayout(card_lang)
        l_lang.setContentsMargins(16, 14, 16, 14)
        l_lang.setSpacing(10)
        self.set_language_title = section_title(tr("set.language"), "inazuma")
        l_lang.addWidget(self.set_language_title)
        self.settings_language_combo = QComboBox()
        self.settings_language_combo.addItems([name for _, name in LANGUAGES])
        codes = [code for code, _ in LANGUAGES]
        idx = codes.index(i18n_manager().current) if i18n_manager().current in codes else 0
        self.settings_language_combo.setCurrentIndex(idx)
        self.settings_language_combo.currentIndexChanged.connect(self._on_language_changed)
        self.settings_language_combo.setMaximumWidth(200)
        l_lang.addWidget(self.settings_language_combo)
        layout.addWidget(card_lang)

        # 背景设置卡片
        card_bg = Card()
        l_bg = QVBoxLayout(card_bg)
        l_bg.setContentsMargins(16, 14, 16, 14)
        l_bg.setSpacing(10)
        self.set_background_title = section_title(tr("set.background"), "sumeru")
        l_bg.addWidget(self.set_background_title)
        # 色块式背景选择器（可视化切换 + 编辑每套配色）；在 combo 构建后插入
        self._bg_swatch_anchor = QWidget()  # 占位，稍后替换为色块网格
        l_bg.addWidget(self._bg_swatch_anchor)
        self.settings_bg_combo = QComboBox()
        self.settings_bg_combo.addItem(tr("set.bg_default"), "")
        for provider in self.pm.get_backgrounds():
            self.settings_bg_combo.addItem(provider.name, provider.key)
        current_bg = str(self.store.settings_get("background_key", "") or "")
        bg_idx = self.settings_bg_combo.findData(current_bg)
        self.settings_bg_combo.setCurrentIndex(max(0, bg_idx))
        self.settings_bg_combo.currentIndexChanged.connect(self._on_background_changed)
        self.settings_bg_combo.setMaximumWidth(220)
        self.settings_bg_combo.hide()  # 色块网格替代可视选择，combo 仅保留兼容
        # 用色块网格替换占位：可视化切换 + 每套配色可编辑
        from .ui_background_widgets import build_background_swatches
        swatch_host = build_background_swatches(self, self.settings_bg_combo)
        l_bg.replaceWidget(self._bg_swatch_anchor, swatch_host)
        self._bg_swatch_anchor.deleteLater()
        # 暂停动画开关：仅在动态背景下显示，省资源
        self.bg_pause_check = QCheckBox(tr("set.bg_pause"))
        self.bg_pause_check.setChecked(bool(self.store.settings_get("bg_anim_paused", False)))
        self.bg_pause_check.setToolTip(tr("set.bg_pause_tip"))
        self.bg_pause_check.stateChanged.connect(self._on_bg_pause_changed)
        l_bg.addWidget(self.bg_pause_check)
        self._update_bg_pause_visibility()
        layout.addWidget(card_bg)

        # EPUB 排版卡片（下载时固化：章节字体 / 分卷字体 + 对话彩色高亮）
        card_epub = Card()
        l_epub = QVBoxLayout(card_epub)
        l_epub.setContentsMargins(16, 14, 16, 14)
        l_epub.setSpacing(10)
        self.set_epub_title = section_title(tr("set.epub_title"), "fontaine")
        l_epub.addWidget(self.set_epub_title)
        from .qimao_core import font_combo_items
        self.set_chapter_font_label = QLabel(tr("set.chapter_font"))
        self.settings_font_combo = QComboBox()  # 章节字体（保留原名作兼容）
        for value, label in font_combo_items():
            self.settings_font_combo.addItem(label, value)
        self.settings_font_combo.currentIndexChanged.connect(self._on_font_scheme_changed)
        self.btn_chapter_font_file = QPushButton(tr("set.font_file_btn"))
        self.btn_chapter_font_file.clicked.connect(lambda: self._pick_font_file("chapter"))
        row_chapter = QHBoxLayout()
        row_chapter.addWidget(self.set_chapter_font_label)
        row_chapter.addWidget(self.settings_font_combo, 1)
        row_chapter.addWidget(self.btn_chapter_font_file)
        l_epub.addLayout(row_chapter)

        self.set_volume_font_label = QLabel(tr("set.volume_font"))
        self.settings_volume_combo = QComboBox()
        self.settings_volume_combo.addItem(tr("set.volume_auto"), "__auto__")
        for value, label in font_combo_items():
            self.settings_volume_combo.addItem(label, value)
        self.settings_volume_combo.currentIndexChanged.connect(self._on_volume_font_changed)
        self.btn_volume_font_file = QPushButton(tr("set.font_file_btn"))
        self.btn_volume_font_file.clicked.connect(lambda: self._pick_font_file("volume"))
        row_volume = QHBoxLayout()
        row_volume.addWidget(self.set_volume_font_label)
        row_volume.addWidget(self.settings_volume_combo, 1)
        row_volume.addWidget(self.btn_volume_font_file)
        l_epub.addLayout(row_volume)

        # 当前字体文件路径显示（选择字体文件后更新）
        self.chapter_font_path_label = QLabel("")
        self.chapter_font_path_label.setObjectName("fontPathLabel")
        self.volume_font_path_label = QLabel("")
        self.volume_font_path_label.setObjectName("fontPathLabel")
        l_epub.addWidget(self.chapter_font_path_label)
        l_epub.addWidget(self.volume_font_path_label)

        # 恢复持久化选择：章节字体（默认马善政毛笔楷书）
        chapter_cfg = str(self.store.settings_get(
            "epub_chapter_font",
            self.store.settings_get("epub_font_scheme", "builtin:mashan")) or "builtin:mashan")
        self._set_font_combo(self.settings_font_combo, chapter_cfg)
        # 分卷字体（默认钟齐志莽行书）
        volume_cfg = str(self.store.settings_get("epub_volume_font", "builtin:zhimang") or "builtin:zhimang")
        self._set_font_combo(self.settings_volume_combo, volume_cfg)
        self._refresh_font_path_labels()

        # ---- 章名 / 卷名颜色（纯色 + 渐变预设，下载时固化） ----
        from .qimao_core import (TITLE_COLOR_PRESETS, CHAPTER_TITLE_COLOR,
                                 VOLUME_TITLE_COLOR)
        self._color_presets = TITLE_COLOR_PRESETS
        self.set_chapter_color_label = QLabel(tr("set.chapter_color"))
        self.chapter_color_combo = QComboBox()
        self.set_volume_color_label = QLabel(tr("set.volume_color"))
        self.volume_color_combo = QComboBox()
        for combo in (self.chapter_color_combo, self.volume_color_combo):
            for value, i18n_key in TITLE_COLOR_PRESETS:
                combo.addItem(_title_color_icon(value), tr(i18n_key), value)
            combo.addItem(tr("set.color_custom"), "__custom__")
        self._set_title_color_combo(
            self.chapter_color_combo,
            str(self.store.settings_get("epub_chapter_color", CHAPTER_TITLE_COLOR) or CHAPTER_TITLE_COLOR))
        self._set_title_color_combo(
            self.volume_color_combo,
            str(self.store.settings_get("epub_volume_color", VOLUME_TITLE_COLOR) or VOLUME_TITLE_COLOR))
        self.chapter_color_combo.currentIndexChanged.connect(
            lambda _: self._on_title_color_changed("chapter"))
        self.volume_color_combo.currentIndexChanged.connect(
            lambda _: self._on_title_color_changed("volume"))
        row_color = QHBoxLayout()
        row_color.addWidget(self.set_chapter_color_label)
        row_color.addWidget(self.chapter_color_combo, 1)
        l_epub.addLayout(row_color)
        row_vcolor = QHBoxLayout()
        row_vcolor.addWidget(self.set_volume_color_label)
        row_vcolor.addWidget(self.volume_color_combo, 1)
        l_epub.addLayout(row_vcolor)

        self.epub_colorize_check = QCheckBox(tr("set.epub_colorize"))
        self.epub_colorize_check.setChecked(
            bool(self.store.settings_get("epub_colorize_dialogue", True)))
        self.epub_colorize_check.setToolTip(tr("set.epub_colorize_tip"))
        self.epub_colorize_check.stateChanged.connect(self._on_epub_colorize_changed)
        l_epub.addWidget(self.epub_colorize_check)

        # ---- 智能选题预设（题图30含7神明 / 尾图2020+随机10张） ----
        self.epub_smart_art_check = QCheckBox(tr("set.epub_smart_art"))
        self.epub_smart_art_check.setChecked(bool(self.store.settings_get("epub_smart_art", True)))
        self.epub_smart_art_check.setToolTip(tr("set.epub_smart_art_tip"))
        self.epub_smart_art_check.stateChanged.connect(self._on_epub_smart_art_changed)
        l_epub.addWidget(self.epub_smart_art_check)
        self.epub_smart_art_hint = hint_label(tr("set.epub_smart_art_hint"))
        l_epub.addWidget(self.epub_smart_art_hint)

        # ---- 章首题图（原神祈愿立绘，标题上方，底部渐隐） ----
        self.epub_hero_check = QCheckBox(tr("set.epub_hero"))
        self.epub_hero_check.setChecked(bool(self.store.settings_get("epub_chapter_hero", False)))
        self.epub_hero_check.setToolTip(tr("set.epub_hero_tip"))
        self.epub_hero_check.stateChanged.connect(self._on_epub_hero_changed)
        l_epub.addWidget(self.epub_hero_check)

        row_hero = QHBoxLayout()
        self.set_hero_mode_label = QLabel(tr("set.epub_hero_mode"))
        self.epub_hero_mode_combo = QComboBox()
        self.epub_hero_mode_combo.addItem(tr("set.epub_hero_rotate"), "rotate")
        self.epub_hero_mode_combo.addItem(tr("set.epub_hero_fixed"), "fixed")
        hero_mode_cfg = str(self.store.settings_get("epub_hero_mode", "rotate") or "rotate")
        mi = self.epub_hero_mode_combo.findData(hero_mode_cfg)
        self.epub_hero_mode_combo.setCurrentIndex(max(0, mi))
        self.epub_hero_mode_combo.currentIndexChanged.connect(self._on_epub_hero_mode_changed)
        self.epub_hero_fixed_combo = QComboBox()
        self._refresh_hero_fixed_combo("genshin")
        self.epub_hero_fixed_combo.currentIndexChanged.connect(self._on_epub_hero_fixed_changed)
        row_hero.addWidget(self.set_hero_mode_label)
        row_hero.addWidget(self.epub_hero_mode_combo)
        row_hero.addWidget(self.epub_hero_fixed_combo, 1)
        l_epub.addLayout(row_hero)
        self._sync_hero_mode_ui()

        # ---- 章末尾图（火影高招 S/A 横屏无字图，正文之后，底部渐隐） ----
        self.epub_tail_check = QCheckBox(tr("set.epub_tail"))
        self.epub_tail_check.setChecked(bool(self.store.settings_get("epub_chapter_tail", False)))
        self.epub_tail_check.setToolTip(tr("set.epub_tail_tip"))
        self.epub_tail_check.stateChanged.connect(self._on_epub_tail_changed)
        l_epub.addWidget(self.epub_tail_check)

        row_tail = QHBoxLayout()
        self.set_tail_mode_label = QLabel(tr("set.epub_tail_mode"))
        self.epub_tail_mode_combo = QComboBox()
        self.epub_tail_mode_combo.addItem(tr("set.epub_tail_rotate"), "rotate")
        self.epub_tail_mode_combo.addItem(tr("set.epub_tail_fixed"), "fixed")
        tail_mode_cfg = str(self.store.settings_get("epub_tail_mode", "rotate") or "rotate")
        ti = self.epub_tail_mode_combo.findData(tail_mode_cfg)
        self.epub_tail_mode_combo.setCurrentIndex(max(0, ti))
        self.epub_tail_mode_combo.currentIndexChanged.connect(self._on_epub_tail_mode_changed)
        self.epub_tail_fixed_combo = QComboBox()
        self._refresh_hero_fixed_combo("naruto")
        self.epub_tail_fixed_combo.currentIndexChanged.connect(self._on_epub_tail_fixed_changed)
        row_tail.addWidget(self.set_tail_mode_label)
        row_tail.addWidget(self.epub_tail_mode_combo)
        row_tail.addWidget(self.epub_tail_fixed_combo, 1)
        l_epub.addLayout(row_tail)
        self._sync_tail_mode_ui()
        layout.addWidget(card_epub)

        # 扩展功能卡片（预设/自动动作/阅读/WebDAV/维护）
        from .settings_extras import build_extras
        self.extras_host = build_extras(self)
        layout.addWidget(self.extras_host)

        # olib 风格补充设置（主题模式/主题色/下载行为/通用/快捷键）
        from .settings_more import build_settings_more
        self.more_host = build_settings_more(self)
        layout.addWidget(self.more_host)

        # 数据管理卡片
        card3 = Card()
        l3 = QVBoxLayout(card3)
        l3.setContentsMargins(16, 14, 16, 14)
        l3.setSpacing(10)
        self.set_data_title = section_title(tr("set.data"), "natlan")
        l3.addWidget(self.set_data_title)
        self.stats_label = QLabel(self._stats_text())
        self.stats_label.setStyleSheet(f"color: {TEXT_DIM};")
        l3.addWidget(self.stats_label)
        self.set_data_location_label = hint_label(tr("set.data_location"))
        l3.addWidget(self.set_data_location_label)
        row_backup = QHBoxLayout()
        self.settings_backup_btn = QPushButton(tr("set.backup"))
        self.settings_backup_btn.clicked.connect(self._backup_data)
        row_backup.addWidget(self.settings_backup_btn)
        self.settings_restore_btn = QPushButton(tr("set.restore"))
        self.settings_restore_btn.clicked.connect(self._restore_data)
        row_backup.addWidget(self.settings_restore_btn)
        l3.addLayout(row_backup)
        layout.addWidget(card3)

        # 关于卡片
        card4 = Card()
        l4 = QVBoxLayout(card4)
        l4.setContentsMargins(16, 14, 16, 14)
        l4.setSpacing(10)
        self.set_about_title = section_title(tr("set.about"), "nodkrai")
        l4.addWidget(self.set_about_title)
        # 下载内核自检
        self.set_protocol_hint = QLabel(tr("set.protocol_hint"))
        self.set_protocol_hint.setStyleSheet(f"color: {TEXT_DIM};")
        self.set_protocol_hint.setWordWrap(True)
        l4.addWidget(self.set_protocol_hint)
        row_check = QHBoxLayout()
        self.settings_protocol_btn = QPushButton(tr("set.protocol_check"))
        self.settings_protocol_btn.clicked.connect(self._check_protocol)
        row_check.addWidget(self.settings_protocol_btn)
        self.set_protocol_status = QLabel("")
        self.set_protocol_status.setStyleSheet(f"color: {TEXT_DIM};")
        self.set_protocol_status.setWordWrap(True)
        row_check.addWidget(self.set_protocol_status, 1)
        l4.addLayout(row_check)
        self.about_label = QLabel(tr("set.about_text"))
        self.about_label.setStyleSheet(f"color: {TEXT_DIM}; line-height: 150%;")
        l4.addWidget(self.about_label)
        layout.addWidget(card4)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self.settings_view = page
        self._add_nav_tab(page, "nav.settings")

    def _arrange_nav(self) -> None:
        """把扁平导航重组为三大分组（stack 页面索引不变）。

        三组风格统一，均为「分组标题（国徽图标 + 名称 + 折叠箭头）+ 缩进子项」：
        - 书库管理：搜索 / 书架 / 阅读 / 任务队列 / 历史记录 / 统计 / 设置 / 功能说明 / 使用说明
        - 导入导出：Calibre / 微信读书 / 笔记
        - 插件扩展：插件 / 示例插件
        用 key 查索引而非硬编码，避免插件加载顺序变化导致错位。
        """
        from .resource_path import resource_path

        key_to_idx = {}
        for i, (k, _icon) in enumerate(self._nav_tab_specs):
            key_to_idx.setdefault(k, i)

        def idx(key):
            return key_to_idx.get(key)

        def icon_path(nation):
            p = resource_path("assets", "nation_icons", f"{nation}.png")
            return p if os.path.isfile(p) else ""

        # 分组 1：书库管理（蒙德国徽）
        main_kids = []
        for k in ("nav.search", "nav.shelf", "nav.read", "nav.queue",
                  "nav.history", "nav.statistics", "nav.settings"):
            i = idx(k)
            if i is not None:
                main_kids.append(i)
        # 功能说明与使用说明也归入书库管理
        for k in ("功能说明", "nav.help"):
            i = idx(k)
            if i is not None:
                main_kids.append(i)

        # 分组 2：导入导出（璃月国徽）
        io_kids = []
        for k in ("Calibre", "nav.weread", "nav.notebook"):
            i = idx(k)
            if i is not None:
                io_kids.append(i)

        # 分组 3：插件扩展（稻妻国徽）
        plugin_kids = []
        for k in ("nav.plugins", "示例插件"):
            i = idx(k)
            if i is not None:
                plugin_kids.append(i)

        spec = []
        self._section_keys = []
        if main_kids:
            spec.append(("section", tr("nav.group_main"), main_kids, icon_path("mondstadt")))
            self._section_keys.append("nav.group_main")
        if io_kids:
            spec.append(("section", tr("nav.io_group"), io_kids, icon_path("liyue")))
            self._section_keys.append("nav.io_group")
        if plugin_kids:
            spec.append(("section", tr("nav.group_plugins"), plugin_kids, icon_path("inazuma")))
            self._section_keys.append("nav.group_plugins")

        # 兜底：任何未归入三组的项作为普通顶层项补上，防止遗漏
        grouped = set(main_kids) | set(io_kids) | set(plugin_kids)
        for i in range(len(self._nav_tab_specs)):
            if i not in grouped:
                spec.append(("item", i))

        self.tabs.arrange(spec)

    def _sync_nav_section_texts(self) -> None:
        """语言切换后更新分层导航的纯标题分组文案。"""
        if not hasattr(self.tabs, "section_headers"):
            return
        headers = self.tabs.section_headers()
        for i, (header, _old) in enumerate(headers):
            keys = getattr(self, "_section_keys", [])
            if i < len(keys):
                header.set_text(tr(keys[i]))

    def _add_nav_tab(self, widget: QWidget, key: str) -> None:
        """添加带图标的导航标签（key 为 i18n 键）。"""
        icon = NAV_ICONS.get(key, "•")
        self._nav_tab_specs.append((key, icon))
        self.tabs.addTab(widget, f"{icon}  {tr(key)}")

    def _build_statusbar(self) -> None:
        self.status = self.statusBar()
        self.status_label = QLabel(tr("status.ready"))
        self.status.addWidget(self.status_label, 1)

    def _apply_plugin_extensions(self) -> None:
        """插件的菜单与标签页挂载点。"""
        self.pm.emit(Hook.REGISTER_MENUS, self)
        for plugin in self.pm.plugins:
            func = getattr(plugin, Hook.REGISTER_TABS.value, None)
            if func is None or not callable(func):
                continue
            try:
                item = func(self)
            except Exception as exc:  # noqa: BLE001
                self.pm.errors.append(f"插件 {plugin.meta.name} register_tabs 出错: {exc}")
                continue
            widgets = item if isinstance(item, list) else [item]
            for widget in widgets:
                if isinstance(widget, QWidget):
                    # 通用挂载：插件注入页若提供 apply_colors，则自动接入主题刷新链
                    if hasattr(widget, "apply_colors"):
                        if not hasattr(self, "_plugin_theme_widgets"):
                            self._plugin_theme_widgets = []
                        self._plugin_theme_widgets.append(widget)
                    label = widget.windowTitle() or plugin.meta.name
                    # 给已知插件更贴切的图标
                    if "笔记" in label:
                        icon_text = "📝 笔记"
                    elif "微信读书" in label:
                        icon_text = "📚 微信读书"
                    elif "Calibre" in label:
                        icon_text = "📖 Calibre"
                    elif "功能说明" in label:
                        icon_text = "ℹ 功能说明"
                    elif "使用说明" in label:
                        icon_text = "📖 使用说明"
                    elif "微信读书" in plugin.meta.name:
                        icon_text = "📚 微信读书"
                    else:
                        icon_text = f"🔌  {label}"
                    # 记录 spec，便于语言切换后重建标签文字
                    parts = icon_text.split()
                    icon = parts[0] if parts else "🔌"
                    raw_text = " ".join(parts[1:]) if len(parts) > 1 else label
                    key_map = {
                        "笔记": "nav.notebook",
                        "微信读书": "nav.weread",
                        "使用说明": "nav.help",
                    }
                    key = key_map.get(raw_text, raw_text)
                    self._nav_tab_specs.append((key, icon))
                    self.tabs.addTab(widget, f"{icon}  {tr(key)}")

    # ------------------------------------------------------------------ 账号

    def _refresh_account_button(self) -> None:
        """根据本地配置更新标题栏账号入口文案。"""
        from .account_dialog import get_account
        account = get_account(self.store)
        if account and account.get("nickname"):
            label = str(account["nickname"])
        elif account:
            label = tr("account.configured")
        else:
            label = tr("account.login")
        self.title_bar.set_account_label(label)

    def _open_account_dialog(self) -> None:
        """打开账号本地配置对话框（非真实登录）。"""
        from .account_dialog import AccountDialog
        from PySide6.QtWidgets import QDialog
        dlg = AccountDialog(self.store, self)
        if dlg.exec() == QDialog.Accepted:
            self._refresh_account_button()
            self.log("账号信息已更新（仅保存在本机）")

    # ------------------------------------------------------------------ 主题 / 背景

    def _active_background(self):
        """返回当前选中的背景提供者（由插件提供），未选中返回 None。

        内置 ``custom`` 背景：用配色中心里用户自定义的色值生成 QSS，
        深色/浅色自动使用各自存储的一套。
        """
        key = str(self.store.settings_get("background_key", "") or "")
        if not key:
            return None
        if key == "custom":
            from .ui_background_theme import _StaticProvider
            return _StaticProvider("custom")
        for provider in self.pm.get_backgrounds():
            if provider.key == key:
                return provider
        return None

    def _apply_stylesheet(self) -> None:
        """合成基础 QSS + 当前背景样式，同时应用到窗口与应用级。

        背景样式由插件通过 ``provide_background`` 提供，只覆盖锚点容器
        （#appCentral / #appStack 的背景），不改动任何控件自身样式。
        """
        from PySide6.QtWidgets import QApplication
        base = QSS if self._dark_theme else LIGHT_QSS
        provider = self._active_background()
        extra = ""
        if provider is not None:
            try:
                extra = provider.stylesheet(self._dark_theme) or ""
            except Exception:  # noqa: BLE001
                extra = ""
        combined = base + ("\n" + extra if extra else "")
        # 功能背景色（用户自定义）：只覆盖卡片类容器，不动整页背景/按钮/输入框
        from . import nation_theme as _nt
        custom_card = _nt.card_bg_color()
        if custom_card:
            if _nt.is_gradient(custom_card):
                # 渐变背景：用 qlineargradient 声明，hover 复用同渐变
                bg_decl = _nt.bg_qss_decl(custom_card)
                hover_decl = bg_decl
            else:
                card2 = _nt.shift_color(custom_card, 9 if self._dark_theme else -6)
                bg_decl = f"background-color: {custom_card};"
                hover_decl = f"background-color: {card2};"
            combined += (
                "\n/* 部件配色：功能卡片背景覆盖（只动卡片容器，不碰整页/按钮/输入框） */\n"
                f"QFrame#bookCard, QFrame#shelfGroupCard, QFrame#queueConcurrentCard {{ "
                f"{bg_decl} }}\n"
                f"QFrame#bookCard:hover {{ {hover_decl} }}\n"
            )
        # 主题色圆点：重染强调色（覆盖按钮/进度条/选中态等全局控件）
        accent_key = str(self.store.settings_get("accent_color", "purple") or "purple")
        combined = recolor_accent(combined, accent_key)
        # 分部件配色：全局主文字色 / 主背景色（空串=跟随主题）
        from .style import apply_custom_colors, COLOR_APP_TEXT, COLOR_APP_BG
        app_text = str(self.store.settings_get(COLOR_APP_TEXT, "") or "")
        app_bg = str(self.store.settings_get(COLOR_APP_BG, "") or "")
        combined = apply_custom_colors(combined, self._dark_theme, app_text, app_bg)
        self.setStyleSheet(combined)
        app = QApplication.instance()
        if app:
            app.setStyleSheet(combined)
        # 内联强调色控件同步（导航指示条、状态文字等）
        self._sync_inline_accent()
        # 动态背景 widget 与静态 QSS 同步：只影响内容区底层，不碰任何控件
        self._sync_dynamic_background(provider)

    def _sync_inline_accent(self) -> None:
        """同步少数内联样式里的强调色（QSS 全局重染覆盖不到的控件）。"""
        key = str(self.store.settings_get("accent_color", "purple") or "purple")
        color = accent_color(key)
        if hasattr(self, "tabs") and hasattr(self.tabs, "set_accent"):
            self.tabs.set_accent(color)
        # 书架工具栏国徽图标跟随主题色
        if getattr(self, "shelf_view", None) and hasattr(self.shelf_view, "_apply_nation_icons"):
            try:
                self.shelf_view._apply_nation_icons()
            except Exception:  # noqa: BLE001
                pass
        # 所有分组标题的国徽 + 文字跟随主题色
        try:
            from .widgets import GroupTitleLabel
            for title in self.findChildren(GroupTitleLabel):
                try:
                    if getattr(title, "_nation_fixed_color", False):
                        continue
                    title.set_icon_color(color)
                except Exception:  # noqa: BLE001
                    pass
            # 所有功能按钮/标签的国徽图标跟随主题色
            from .widgets import nation_icon
            from PySide6.QtWidgets import QLabel
            for w in self.findChildren(QWidget):
                nation = getattr(w, "_nation", None)
                if not nation or getattr(w, "_nation_fixed_color", False):
                    continue
                try:
                    # accent 按钮是主题色底，图标用深色才看得清
                    from PySide6.QtWidgets import QAbstractButton
                    icon_color = ("#1a1a1a" if isinstance(w, QAbstractButton)
                                  and w.property("accent") == "true" else color)
                    sz = getattr(w, "_nation_size", 16)
                    if isinstance(w, QLabel):
                        w.setPixmap(nation_icon(nation, icon_color, sz).pixmap(sz, sz))
                    else:
                        w.setIcon(nation_icon(nation, icon_color, sz))
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        for attr in ("theme_status",):
            if hasattr(self, attr):
                try:
                    w = getattr(self, attr)
                    w.setStyleSheet(f"color: {color};")
                except Exception:  # noqa: BLE001
                    pass
        # 热搜词按钮也跟随主题色
        for btn in getattr(self, "_hot_word_buttons", []):
            try:
                btn.setStyleSheet(
                    f"color: {color}; font-size: 12px; border: none; padding: 2px 6px;")
            except Exception:  # noqa: BLE001
                pass
        # 搜索页工具标签的国徽图标跟随主题色
        try:
            self._refresh_search_tool_tab_icons()
        except Exception:  # noqa: BLE001
            pass
        # 统计页折线图 / 卡片配色跟随主题色与深浅主题
        if getattr(self, "stats_view", None) and hasattr(self.stats_view, "apply_colors"):
            try:
                self.stats_view.apply_colors()
            except Exception:  # noqa: BLE001
                pass
        # 使用说明书页跟随主题色与深浅主题
        if getattr(self, "help_view", None) and hasattr(self.help_view, "apply_colors"):
            try:
                self.help_view.apply_colors()
            except Exception:  # noqa: BLE001
                pass
        # Calibre 页跟随主题色与深浅主题（BC 混合美化版）
        if getattr(self, "calibre_view", None) and hasattr(self.calibre_view, "apply_colors"):
            try:
                self.calibre_view.apply_colors()
            except Exception:  # noqa: BLE001
                pass
        # 微信读书仪表盘跟随主题色与深浅主题
        if getattr(self, "weread_view", None) and hasattr(self.weread_view, "apply_colors"):
            try:
                self.weread_view.apply_colors()
            except Exception:  # noqa: BLE001
                pass
        # 微信读书笔记页跟随主题色与深浅主题
        if getattr(self, "notebook_view", None) and hasattr(self.notebook_view, "apply_colors"):
            try:
                self.notebook_view.apply_colors()
            except Exception:  # noqa: BLE001
                pass
        # 插件管理页跟随主题色与深浅主题（分组卡片墙）
        if getattr(self, "plugins_view", None) and hasattr(self.plugins_view, "apply_colors"):
            try:
                self.plugins_view.apply_colors()
            except Exception:  # noqa: BLE001
                pass
        # 插件注入的自定义页（如示例插件）跟随主题色与深浅主题
        for plugin_page in getattr(self, "_plugin_theme_widgets", []):
            try:
                plugin_page.apply_colors()
            except Exception:  # noqa: BLE001
                pass
        # 分部件配色：导航栏文字/图标/背景三色独立覆盖
        from .style import COLOR_NAV_TEXT, COLOR_NAV_ICON, COLOR_NAV_BG
        nv_text = str(self.store.settings_get(COLOR_NAV_TEXT, "") or "")
        nv_icon = str(self.store.settings_get(COLOR_NAV_ICON, "") or "")
        nv_bg = str(self.store.settings_get(COLOR_NAV_BG, "") or "")
        if hasattr(self, "tabs") and hasattr(self.tabs, "set_nav_colors"):
            self.tabs.set_nav_colors(nv_text, nv_icon, nv_bg)

    def _apply_theme_color(self) -> None:
        """主题色圆点切换后的入口：重绘全局 QSS + 内联强调色。"""
        self._apply_stylesheet()

    def _refresh_part_colors(self) -> None:
        """部件独立配色变更后的即时刷新：侧栏（字体/国徽/文字/背景）+ 各功能页。

        国徽图标通过 nation_icon 在生成时读取配色中心，这里触发各页重建即可；
        导航字体需清缓存后令侧栏 QSS 重新解析（清缓存由设置页负责）。
        """
        # 侧栏：重刷主题色会重建按钮 QSS 与国徽图标
        tabs = getattr(self, "tabs", None)
        accent_key = str(self.store.settings_get("accent_color", "purple") or "purple")
        from .style import accent_color
        c = accent_color(accent_key)
        if tabs is not None and hasattr(tabs, "set_accent"):
            try:
                tabs.set_accent(c)
            except Exception:  # noqa: BLE001
                pass
        if tabs is not None and hasattr(tabs, "refresh_theme"):
            try:
                tabs.refresh_theme(self._dark_theme)
            except Exception:  # noqa: BLE001
                pass
        # 各功能页重绘（国徽图标/卡片背景重新读取配色中心）
        for i in range(tabs.count()) if tabs is not None else []:
            wd = tabs.widget(i)
            for meth in ("apply_colors", "refresh", "reload"):
                fn = getattr(wd, meth, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:  # noqa: BLE001
                        pass

    def _sync_dynamic_background(self, provider) -> None:
        """根据当前背景 provider 挂载/更新/卸载动态背景 widget。

        仅操作 SideNavWidget 的内容区底层（StackAll 的最下层），
        页面与控件都在其上层，因此不会遮挡或改变任何交互。
        """
        if not hasattr(self, "tabs"):
            return
        widget = None
        if provider is not None:
            try:
                widget = provider.create_widget(self.tabs, dark=self._dark_theme)
            except Exception:  # noqa: BLE001
                widget = None
        if widget is None:
            self.tabs.set_backdrop_widget(None)
            self._dynamic_bg = None
            self._update_bg_pause_visibility()
            return

        # 暂停状态来自设置，持久化；默认运行
        paused = bool(self.store.settings_get("bg_anim_paused", False))
        if hasattr(widget, "set_dark"):
            widget.set_dark(self._dark_theme)
        if hasattr(widget, "set_paused"):
            widget.set_paused(paused)
        self.tabs.set_backdrop_widget(widget)
        self._dynamic_bg = widget
        self._update_bg_pause_visibility()

    def set_background(self, key: str) -> None:
        """切换背景并持久化；key 为空表示恢复默认纯色。"""
        self.store.settings_set("background_key", key)
        self._apply_stylesheet()
        provider = self._active_background()
        name = provider.name if provider else "默认纯色"
        self.log(f"背景已切换为：{name}")

    def _refresh_bg_swatches(self) -> None:
        """主题切换后，刷新设置页背景色块的预览色为当前主题对应的一套。"""
        from . import ui_background_theme as ub
        swatches = getattr(self, "_bg_swatches", None)
        if not swatches:
            return
        for key, sw in swatches.items():
            if key == "__default__":
                continue
            if key in ub.PRESET_DEFAULTS or key == ub.CUSTOM_KEY:
                sw.set_colors(ub.resolved_value(key, self._dark_theme))

    def _on_theme_toggled(self, dark: bool) -> None:
        self._dark_theme = dark
        # 用统一合成入口，保证切主题后背景样式仍然保留
        self._apply_stylesheet()
        self._refresh_bg_swatches()
        # 阅读页纸张"跟随界面"时即时重算（需在 theme_mode 已写入后）
        if getattr(self, "reader_view", None) is not None and \
                hasattr(self.reader_view, "on_app_theme_changed"):
            self.reader_view.on_app_theme_changed()
        if hasattr(self, "title_bar") and hasattr(self.title_bar, "refresh_theme"):
            self.title_bar.refresh_theme(dark)
        if hasattr(self, "theme_status"):
            self.theme_status.setText(
                tr("set.current_theme",
                   theme=tr("set.theme_dark" if dark else "set.theme_light"))
            )
        if hasattr(self, "settings_theme_btn"):
            self.settings_theme_btn.setText(
                tr("set.switch_light" if dark else "set.switch_dark")
            )
        if hasattr(self, "tabs") and hasattr(self.tabs, "refresh_theme"):
            self.tabs.refresh_theme(dark)
        if hasattr(self, "card_list") and hasattr(self.card_list, "refresh_theme"):
            self.card_list.refresh_theme(dark)
        for card in self.findChildren(Card):
            card.refresh_theme(dark)
        if hasattr(self, "detailPane"):
            self.detailPane.setStyleSheet(
                f"QWidget#detailPane {{ border-left: 1px solid {BORDER if dark else '#e5e5e5'}; }}"
            )
        # 重新渲染已打开书籍的详情，避免沿用旧主题的 HTML 文字色
        if getattr(self, "current_book", None) is not None:
            self._load_detail(self.current_book)
        # refresh_theme 会重置导航指示条颜色，末尾再同步一次分部件配色
        self._sync_inline_accent()
        self.log(f"已切换到{'深色' if dark else '浅色'}主题")

    # ------------------------------------------------------------------ 多语言

    def _stats_text(self) -> str:
        stats = self.store.stats()
        return tr(
            "set.stats",
            shelf=stats.get("shelf", 0),
            history=stats.get("history", 0),
            success=stats.get("history_success", 0),
            failed=stats.get("history_failed", 0),
        )

    def _on_language_changed(self, index: int) -> None:
        """设置页语言下拉框变更。"""
        if 0 <= index < len(LANGUAGES):
            i18n_manager().set_language(LANGUAGES[index][0])

    def _on_background_changed(self, index: int) -> None:
        """设置页背景下拉框变更。"""
        key = str(self.settings_bg_combo.itemData(index) or "")
        self.set_background(key)
        self._update_bg_pause_visibility()

    def _update_bg_pause_visibility(self) -> None:
        """仅当选中动态背景（覆写了 create_widget）时显示暂停开关。"""
        if not hasattr(self, "bg_pause_check"):
            return
        from .plugin.base import BackgroundProvider
        provider = self._active_background()
        is_dynamic = False
        if provider is not None:
            # 基类默认 create_widget 返回 None；只有覆写的才是动态背景
            override = getattr(type(provider), "create_widget", None)
            base_impl = getattr(BackgroundProvider, "create_widget", None)
            is_dynamic = override is not None and override is not base_impl
        self.bg_pause_check.setVisible(is_dynamic)

    def _on_bg_pause_changed(self, state: int) -> None:
        """暂停动画开关变更，持久化并即时生效。"""
        paused = bool(state)
        self.store.settings_set("bg_anim_paused", paused)
        widget = getattr(self, "_dynamic_bg", None)
        if widget is not None and hasattr(widget, "set_paused"):
            widget.set_paused(paused)
        self.log("动态背景动画已" + ("暂停" if paused else "继续"))

    def _set_font_combo(self, combo: QComboBox, cfg: str) -> None:
        """把字体配置值回填到下拉框；文件路径/自定义名显示为动态项。"""
        idx = combo.findData(cfg)
        if idx < 0:
            # 自定义字体（文件路径或字体名）→ 加一项动态显示
            combo.addItem(os.path.basename(cfg) or cfg, cfg)
            idx = combo.count() - 1
        combo.blockSignals(True)
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _refresh_font_path_labels(self) -> None:
        """显示当前章节/分卷字体来源（文件或内置方案）。"""
        ch = str(self.store.settings_get(
            "epub_chapter_font", self.store.settings_get("epub_font_scheme", "song")) or "song")
        vol = str(self.store.settings_get("epub_volume_font", "") or "")
        for label, cfg, name in (
            (self.chapter_font_path_label, ch, tr("set.chapter_font")),
            (self.volume_font_path_label, vol, tr("set.volume_font")),
        ):
            if cfg and (cfg.endswith((".ttf", ".otf", ".woff", ".woff2"))):
                label.setText(f"{name}：{os.path.basename(cfg)}")
                label.setToolTip(cfg)
                label.setStyleSheet(f"color: {'#b0b0b0' if self._dark_theme else '#777'}; font-size: 11px;")
            else:
                label.setText("")

    def _pick_font_file(self, which: str) -> None:
        """选择字体文件（ttf/otf/woff/woff2），持久化到章节或分卷字体。"""
        path, _ = QFileDialog.getOpenFileName(
            self, tr("set.font_file_title"), "",
            "字体文件 (*.ttf *.otf *.woff *.woff2);;所有文件 (*)")
        if not path:
            return
        key = "epub_chapter_font" if which == "chapter" else "epub_volume_font"
        self.store.settings_set(key, path)
        combo = self.settings_font_combo if which == "chapter" else self.settings_volume_combo
        self._set_font_combo(combo, path)
        self._refresh_font_path_labels()
        who = tr("set.chapter_font") if which == "chapter" else tr("set.volume_font")
        self.log(f"{who}已设为字体文件：{os.path.basename(path)}（将嵌入 EPUB，下次下载生效）")

    def _on_font_scheme_changed(self, index: int) -> None:
        """章节字体下拉变更，持久化（下载时生效）。"""
        key = str(self.settings_font_combo.itemData(index) or "song")
        self.store.settings_set("epub_chapter_font", key)
        from .qimao_core import font_label
        self._refresh_font_path_labels()
        self.log(f"章节字体已设为：{font_label(key)}（下次下载生效）")

    def _on_volume_font_changed(self, index: int) -> None:
        """分卷字体下拉变更，持久化（下载时生效）。"""
        key = str(self.settings_volume_combo.itemData(index) or "")
        self.store.settings_set("epub_volume_font", "" if key == "__auto__" else key)
        from .qimao_core import font_label
        label = tr("set.volume_auto") if key == "__auto__" else font_label(key)
        self._refresh_font_path_labels()
        self.log(f"分卷字体已设为：{label}（下次下载生效）")

    def _set_title_color_combo(self, combo: QComboBox, value: str) -> None:
        """回填标题颜色下拉；自定义色（预设外）动态加一项显示。"""
        idx = combo.findData(value)
        if idx < 0:
            combo.addItem(_title_color_icon(value), value, value)
            idx = combo.count() - 1
        combo.blockSignals(True)
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _on_title_color_changed(self, which: str) -> None:
        """章名/卷名颜色下拉变更：自定义则弹取色器，持久化（下载时生效）。"""
        combo = self.chapter_color_combo if which == "chapter" else self.volume_color_combo
        key = "epub_chapter_color" if which == "chapter" else "epub_volume_color"
        value = str(combo.currentData() or "")
        if value == "__custom__":
            fallback = str(self.store.settings_get(key, "#c62828") or "#c62828")
            c = QColorDialog.getColor(QColor(fallback), self, tr("set.color_pick_title"))
            if not c.isValid():
                self._set_title_color_combo(combo, fallback)
                return
            value = c.name()
            self._set_title_color_combo(combo, value)
        self.store.settings_set(key, value)
        who = tr("set.chapter_color") if which == "chapter" else tr("set.volume_color")
        self.log(f"{who}已设为：{value}（下次下载生效）")

    def _on_epub_colorize_changed(self, state: int) -> None:
        """EPUB 对话着色开关变更，持久化（下载时生效）。"""
        self.store.settings_set("epub_colorize_dialogue", bool(state))
        self.log("EPUB 对话彩色高亮已" + ("开启" if state else "关闭") + "（下次下载生效）")

    def _on_epub_smart_art_changed(self, state: int) -> None:
        """智能选题预设开关变更，持久化。"""
        self.store.settings_set("epub_smart_art", bool(state))
        self.log("EPUB 智能选题预设已" + ("开启" if state else "关闭") + "（下次下载生效）")

    def _on_epub_hero_changed(self, state: int) -> None:
        """章首题图开关变更，持久化。"""
        self.store.settings_set("epub_chapter_hero", bool(state))
        self._sync_hero_mode_ui()
        self.log("EPUB 章首题图已" + ("开启" if state else "关闭") + "（下次下载生效）")

    def _refresh_hero_fixed_combo(self, library: str = "genshin") -> None:
        """按图库重建「固定图」下拉。

        library=genshin 时刷新题图下拉；library=naruto 时刷新尾图下拉。
        """
        is_tail = library == "naruto"
        combo = getattr(self, "epub_tail_fixed_combo", None) if is_tail \
            else getattr(self, "epub_hero_fixed_combo", None)
        if combo is None:
            return
        fixed_key = "epub_tail_fixed" if is_tail else "epub_hero_fixed"
        combo.blockSignals(True)
        combo.clear()
        try:
            from . import chapter_hero as _hero_mod
            for h in _hero_mod.available_heroes(library):
                combo.addItem(h["zh"], h["key"])
        except Exception:  # noqa: BLE001
            pass
        fixed_cfg = str(self.store.settings_get(fixed_key, "") or "")
        idx = combo.findData(fixed_cfg)
        combo.setCurrentIndex(max(0, idx))
        if idx < 0 and combo.count() > 0:
            self.store.settings_set(fixed_key, combo.itemData(0) or "")
        combo.blockSignals(False)

    def _on_epub_hero_mode_changed(self, _idx: int = 0) -> None:
        """题图分配方式变更。"""
        self.store.settings_set("epub_hero_mode", self.epub_hero_mode_combo.currentData() or "rotate")
        if self.epub_hero_mode_combo.currentData() == "fixed" and \
                not self.store.settings_get("epub_hero_fixed", "") and \
                self.epub_hero_fixed_combo.count() > 0:
            self.store.settings_set("epub_hero_fixed", self.epub_hero_fixed_combo.itemData(0) or "")
        self._sync_hero_mode_ui()

    def _on_epub_hero_fixed_changed(self, _idx: int = 0) -> None:
        """固定题图选择变更。"""
        self.store.settings_set("epub_hero_fixed", self.epub_hero_fixed_combo.currentData() or "")

    def _sync_hero_mode_ui(self) -> None:
        """题图开启且方式为固定时，才启用固定图下拉。"""
        enabled = bool(self.store.settings_get("epub_chapter_hero", False))
        is_fixed = (self.epub_hero_mode_combo.currentData() == "fixed") if hasattr(self, "epub_hero_mode_combo") else False
        if hasattr(self, "epub_hero_fixed_combo"):
            self.epub_hero_fixed_combo.setEnabled(enabled and is_fixed)
        if hasattr(self, "epub_hero_mode_combo"):
            self.epub_hero_mode_combo.setEnabled(enabled)

    # ---- 章末尾图（火影横屏）回调 ----
    def _on_epub_tail_changed(self, state: int) -> None:
        """章末尾图开关变更，持久化。"""
        self.store.settings_set("epub_chapter_tail", bool(state))
        self._sync_tail_mode_ui()
        self.log("EPUB 章末尾图（火影横屏）已" + ("开启" if state else "关闭") + "（下次下载生效）")

    def _on_epub_tail_mode_changed(self, _idx: int = 0) -> None:
        """尾图分配方式变更。"""
        self.store.settings_set("epub_tail_mode", self.epub_tail_mode_combo.currentData() or "rotate")
        if self.epub_tail_mode_combo.currentData() == "fixed" and \
                not self.store.settings_get("epub_tail_fixed", "") and \
                self.epub_tail_fixed_combo.count() > 0:
            self.store.settings_set("epub_tail_fixed", self.epub_tail_fixed_combo.itemData(0) or "")
        self._sync_tail_mode_ui()

    def _on_epub_tail_fixed_changed(self, _idx: int = 0) -> None:
        """固定尾图选择变更。"""
        self.store.settings_set("epub_tail_fixed", self.epub_tail_fixed_combo.currentData() or "")

    def _sync_tail_mode_ui(self) -> None:
        """尾图开启且方式为固定时，才启用固定图下拉。"""
        enabled = bool(self.store.settings_get("epub_chapter_tail", False))
        is_fixed = (self.epub_tail_mode_combo.currentData() == "fixed") if hasattr(self, "epub_tail_mode_combo") else False
        if hasattr(self, "epub_tail_fixed_combo"):
            self.epub_tail_fixed_combo.setEnabled(enabled and is_fixed)
        if hasattr(self, "epub_tail_mode_combo"):
            self.epub_tail_mode_combo.setEnabled(enabled)

    def _retranslate(self, code: str = "") -> None:
        """语言切换后刷新主窗口可翻译文案（由 i18n 信号触发）。"""
        # 窗口与应用名
        self.setWindowTitle(f"{tr('app.name')} · GUI")
        self.tabs.set_app_name(tr("app.name"))
        for i, (key, icon) in enumerate(self._nav_tab_specs):
            self.tabs.setTabText(i, f"{icon}  {tr(key)}")
        # 分层导航的纯标题分组文案随语言更新
        self._sync_nav_section_texts()

        # 搜索页
        for tb, (ikey, _nation) in zip(
                getattr(self, "_search_tool_tabs", []),
                [("search.tab_search", "mondstadt"),
                 ("search.tab_direct", "snezhnaya"),
                 ("search.tab_history", "liyue")]):
            tb.setText(tr(ikey))
        self.search_edit.setPlaceholderText(tr("search.placeholder"))
        tab_idx = self.tab_combo.currentIndex()
        self.tab_combo.blockSignals(True)
        self.tab_combo.clear()
        self.tab_combo.addItems([tr("search.by_title"), tr("search.by_author")])
        self.tab_combo.setCurrentIndex(max(0, tab_idx))
        self.tab_combo.setToolTip(tr("search.switch_type"))
        self.tab_combo.blockSignals(False)
        self.search_btn.setText(tr("search.button"))
        self.format_label.setText(tr("search.format"))
        if hasattr(self, "direct_format_label"):
            self.direct_format_label.setText(tr("search.format"))
        self.download_btn.setText(tr("search.add_queue"))
        self.hot_prefix_label.setText(tr("search.hot_prefix"))
        self.result_title.setText(tr("search.recommend_title"))
        self.refresh_btn.setText(tr("search.refresh"))
        self.result_hint.setText(tr("search.recommend_hint"))
        self.result_table.setHorizontalHeaderLabels([
            tr("search.col_title"), tr("search.col_author"),
            tr("search.col_words"), tr("search.col_id"),
        ])
        self.detail_section_title.setText(tr("search.detail_title"))
        self.detail_text.setPlaceholderText(tr("search.detail_placeholder"))
        self.detail_download_btn.setText(tr("search.add_queue"))

        # 设置页
        self.set_download_title.setText(tr("set.download"))
        self.set_default_dir_label.setText(tr("set.default_dir"))
        self.settings_choose_btn.setText(tr("set.choose_dir"))
        self.set_default_format_label.setText(tr("set.default_format"))
        self.set_tpl_label.setText(tr("set.filename_tpl"))
        self.settings_tpl_save_btn.setText(tr("common.save"))
        self.set_tpl_vars_label.setText(tr("set.tpl_vars"))
        self.set_appearance_title.setText(tr("set.appearance"))
        self.set_language_title.setText(tr("set.language"))
        self.set_background_title.setText(tr("set.background"))
        self.settings_bg_combo.blockSignals(True)
        self.settings_bg_combo.setItemText(0, tr("set.bg_default"))
        self.settings_bg_combo.blockSignals(False)
        if hasattr(self, "bg_pause_check"):
            self.bg_pause_check.setText(tr("set.bg_pause"))
            self.bg_pause_check.setToolTip(tr("set.bg_pause_tip"))
        if hasattr(self, "set_epub_title"):
            self.set_epub_title.setText(tr("set.epub_title"))
            self.set_chapter_font_label.setText(tr("set.chapter_font"))
            self.set_volume_font_label.setText(tr("set.volume_font"))
            self.btn_chapter_font_file.setText(tr("set.font_file_btn"))
            self.btn_volume_font_file.setText(tr("set.font_file_btn"))
            if hasattr(self, "chapter_color_combo"):
                self.set_chapter_color_label.setText(tr("set.chapter_color"))
                self.set_volume_color_label.setText(tr("set.volume_color"))
                for combo in (self.chapter_color_combo, self.volume_color_combo):
                    cur_val = combo.currentData()
                    combo.blockSignals(True)
                    combo.clear()
                    for value, i18n_key in self._color_presets:
                        combo.addItem(_title_color_icon(value), tr(i18n_key), value)
                    combo.addItem(tr("set.color_custom"), "__custom__")
                    self._set_title_color_combo(combo, str(cur_val or "#c62828"))
                    combo.blockSignals(False)
            self.epub_colorize_check.setText(tr("set.epub_colorize"))
            self.epub_colorize_check.setToolTip(tr("set.epub_colorize_tip"))
            if hasattr(self, "epub_smart_art_check"):
                self.epub_smart_art_check.setText(tr("set.epub_smart_art"))
                self.epub_smart_art_check.setToolTip(tr("set.epub_smart_art_tip"))
                self.epub_smart_art_hint.setText(tr("set.epub_smart_art_hint"))
            if hasattr(self, "epub_hero_check"):
                self.epub_hero_check.setText(tr("set.epub_hero"))
                self.epub_hero_check.setToolTip(tr("set.epub_hero_tip"))
                self.set_hero_mode_label.setText(tr("set.epub_hero_mode"))
                cur_mode = self.epub_hero_mode_combo.currentData()
                self.epub_hero_mode_combo.blockSignals(True)
                self.epub_hero_mode_combo.clear()
                self.epub_hero_mode_combo.addItem(tr("set.epub_hero_rotate"), "rotate")
                self.epub_hero_mode_combo.addItem(tr("set.epub_hero_fixed"), "fixed")
                self.epub_hero_mode_combo.setCurrentIndex(max(0, self.epub_hero_mode_combo.findData(cur_mode)))
                self.epub_hero_mode_combo.blockSignals(False)
                self._refresh_hero_fixed_combo("genshin")
            if hasattr(self, "epub_tail_check"):
                self.epub_tail_check.setText(tr("set.epub_tail"))
                self.epub_tail_check.setToolTip(tr("set.epub_tail_tip"))
                self.set_tail_mode_label.setText(tr("set.epub_tail_mode"))
                cur_tmode = self.epub_tail_mode_combo.currentData()
                self.epub_tail_mode_combo.blockSignals(True)
                self.epub_tail_mode_combo.clear()
                self.epub_tail_mode_combo.addItem(tr("set.epub_tail_rotate"), "rotate")
                self.epub_tail_mode_combo.addItem(tr("set.epub_tail_fixed"), "fixed")
                self.epub_tail_mode_combo.setCurrentIndex(max(0, self.epub_tail_mode_combo.findData(cur_tmode)))
                self.epub_tail_mode_combo.blockSignals(False)
                self._refresh_hero_fixed_combo("naruto")
            # 两个字体下拉框：保留当前配置值，重建为当前语言的标签
            from .qimao_core import font_combo_items
            cur_chapter = str(self.settings_font_combo.currentData() or "song")
            cur_volume = str(self.settings_volume_combo.currentData() or "__auto__")
            combo_items = font_combo_items()
            for combo, cur, with_auto in (
                (self.settings_font_combo, cur_chapter, False),
                (self.settings_volume_combo, cur_volume, True),
            ):
                combo.blockSignals(True)
                combo.clear()
                if with_auto:
                    combo.addItem(tr("set.volume_auto"), "__auto__")
                for value, label in combo_items:
                    combo.addItem(label, value)
                self._set_font_combo(combo, cur)  # 内部已 blockSignals
                combo.blockSignals(False)
            self._refresh_font_path_labels()
        # 同步语言下拉框（blockSignals 避免再次触发切换）
        codes = [code for code, _ in LANGUAGES]
        if i18n_manager().current in codes:
            combo_idx = codes.index(i18n_manager().current)
            self.settings_language_combo.blockSignals(True)
            self.settings_language_combo.setCurrentIndex(combo_idx)
            self.settings_language_combo.blockSignals(False)
        self.set_data_title.setText(tr("set.data"))
        self.stats_label.setText(self._stats_text())
        self.set_data_location_label.setText(tr("set.data_location"))
        self.settings_backup_btn.setText(tr("set.backup"))
        self.settings_restore_btn.setText(tr("set.restore"))
        self.set_about_title.setText(tr("set.about"))
        self.set_protocol_hint.setText(tr("set.protocol_hint"))
        self.settings_protocol_btn.setText(tr("set.protocol_check"))
        self.about_label.setText(tr("set.about_text"))
        self.theme_status.setText(
            tr("set.current_theme",
               theme=tr("set.theme_dark" if self._dark_theme else "set.theme_light"))
        )
        self.settings_theme_btn.setText(
            tr("set.switch_light" if self._dark_theme else "set.switch_dark")
        )

        # 状态栏：仅当显示为"就绪"时更新，避免覆盖正在输出的日志
        if self.status_label.text() in {"就绪", "Ready", "Готово"}:
            self.status_label.setText(tr("status.ready"))
        self._refresh_account_button()

        # 子视图各自刷新（如有实现）
        for view in (self.shelf_view, self.queue_view, self.history_view,
                     self.reader_view, getattr(self, "stats_view", None),
                     getattr(self, "weread_view", None),
                     getattr(self, "notebook_view", None),
                     getattr(self, "calibre_view", None),
                     getattr(self, "help_view", None),
                     getattr(self, "limitations_view", None)):
            retranslate = getattr(view, "retranslate", None)
            if callable(retranslate):
                try:
                    retranslate()
                except Exception:  # noqa: BLE001
                    pass

        # 详情区若已有书，按当前语言重绘
        if getattr(self, "current_book", None) is not None:
            self._load_detail(self.current_book)
        self._retranslate_new_features()

    def _retranslate_new_features(self) -> None:
        """刷新本轮新增功能控件的文案（不重建，保留用户输入）。"""
        # 扩展功能卡片（含界面抗锯齿开关）：整体重建以同步文案
        if hasattr(self, "extras_host") and self.extras_host is not None:
            try:
                from .settings_extras import build_extras
                parent_layout = self.extras_host.parent().layout()
                idx = parent_layout.indexOf(self.extras_host)
                old = self.extras_host
                new_host = build_extras(self)
                parent_layout.insertWidget(idx, new_host)
                old.setParent(None)
                old.deleteLater()
                self.extras_host = new_host
            except Exception:  # noqa: BLE001
                pass
        # olib 风格补充设置卡片：整体重建（取值来自 store，不会丢用户输入）
        if hasattr(self, "more_host") and self.more_host is not None:
            try:
                from .settings_more import build_settings_more
                parent_layout = self.more_host.parent().layout()
                idx = parent_layout.indexOf(self.more_host)
                old = self.more_host
                new_host = build_settings_more(self)
                parent_layout.insertWidget(idx, new_host)
                old.setParent(None)
                old.deleteLater()
                self.more_host = new_host
            except Exception:  # noqa: BLE001
                pass
        # 书架工具栏文案由 ShelfView.retranslate() 统一处理（含更多菜单 action）
        # 详情收藏按钮
        if hasattr(self, "btn_bookmark") and self.current_book:
            self.btn_bookmark.setText(
                self._bookmark_label(self.current_book.get("id", "")))
        # 阅读器自动阅读
        if getattr(self, "reader_view", None) is not None \
                and hasattr(self.reader_view, "btn_auto"):
            self.reader_view.btn_auto.setText(
                "⏸ " + tr("follow.update_selected") if self._auto_read_label()
                else "▶️ " + tr("extras.auto_interval").replace("：", ""))
        # 搜索工具条
        if hasattr(self, "link_edit"):
            self.link_edit.setPlaceholderText(tr("searchtools.link_placeholder"))
        for attr, key in (("btn_link_goto", "searchtools.goto"),
                          ("btn_bulk", "searchtools.bulk")):
            if hasattr(self, attr):
                getattr(self, attr).setText(tr(key))
        if hasattr(self, "reload_search_tools"):
            self.reload_search_tools()
        # extras 卡片按钮/复选框
        mappings = {
            "preset_save": ("btn_save_preset", "extras.preset_save"),
            "preset_delete": ("btn_del_preset", "extras.preset_delete"),
            "preset_apply": ("btn_apply_preset", "extras.preset_apply"),
            "autorun_calibre": ("auto_calibre_check", "extras.autorun_calibre"),
            "autorun_weread": ("auto_weread_check", "extras.autorun_weread"),
            "autorun_verify": ("auto_verify_check", "extras.autorun_verify"),
            "tray": ("tray_check", "extras.tray"),
            "auto_update": ("auto_update_check", "extras.auto_update"),
            "check_update": ("btn_check_update", "extras.check_update"),
            "open_log": ("btn_open_log", "extras.open_log"),
            "scrape": ("btn_scrape", "extras.scrape"),
            "webdav_test": ("btn_dav_test", "extras.webdav_test"),
            "webdav_backup": ("btn_dav_backup", "extras.webdav_backup"),
            "webdav_restore": ("btn_dav_restore", "extras.webdav_restore"),
            "motion_enable": ("motion_check", "extras.motion_enable"),
            "motion_low_power": ("low_power_check", "extras.motion_low_power"),
        }
        for _, (attr, key) in mappings.items():
            if hasattr(self, attr):
                getattr(self, attr).setText(tr(key))
        # 动效强度下拉框：保留当前值，仅刷新文案
        combo = getattr(self, "motion_level_combo", None)
        if combo is not None:
            current = str(combo.currentData() or "normal")
            combo.blockSignals(True)
            combo.clear()
            for key, label in (("low", "extras.motion_low"),
                               ("normal", "extras.motion_normal"),
                               ("high", "extras.motion_high")):
                combo.addItem(tr(label), key)
            idx = combo.findData(current)
            combo.setCurrentIndex(max(0, idx))
            combo.blockSignals(False)

    def _bookmark_label(self, bid: str) -> str:
        """收藏按钮文案（跟随当前语言）。"""
        if self.store.bookmark_has(str(bid)):
            return "⭐ " + tr("common.bookmarked")
        return "☆ " + tr("common.bookmark_add")

    def _auto_read_label(self) -> bool:
        return bool(getattr(getattr(self, "reader_view", None), "_auto_read", False))

    # ------------------------------------------------------------------ 行为

    def log(self, message: str) -> None:
        self.status_label.setText(message)

    # ---------------- 搜索页：搜索 / 推荐 / 卡片选择 ----------------

    def _next_recommend_keyword(self) -> str:
        """轮询推荐关键词，返回本次要加载的热门词。"""
        self._recommend_index = (self._recommend_index + 1) % len(RECOMMEND_KEYWORDS)
        self._current_recommend_kw = RECOMMEND_KEYWORDS[self._recommend_index]
        return self._current_recommend_kw

    def _start_search(self, keyword: str, tab: str = "0", page: int = 1,
                      tag: str = "search") -> None:
        """统一发起搜索请求：推荐与手动搜索共用同一条异步链路。"""
        if self.search_worker is not None and self.search_worker.isRunning():
            self.search_worker.terminate()
            self.search_worker.wait(500)
        self._last_search_tag = tag
        self.search_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.log(f"正在搜索：{keyword} …")
        self.pm.emit(Hook.ON_SEARCH, keyword, [])
        self.search_worker = SearchWorker(keyword, self, tab=tab, page=page, tag=tag)
        self.search_worker.finished.connect(self._on_search_done)
        self.search_worker.failed.connect(self._on_search_failed)
        self.search_worker.start()

    def do_search(self) -> None:
        keyword = self.search_edit.text().strip()
        if not keyword:
            QMessageBox.warning(self, "提示", "请输入搜索关键词")
            return
        # 搜书 tab=0，搜作者 tab=1
        tab = "1" if self.tab_combo.currentIndex() == 1 else "0"
        # 记录搜索历史（仅搜书模式）
        if tab == "0":
            self.store.search_history_add(keyword)
            if hasattr(self, "reload_search_tools"):
                self.reload_search_tools()
        self._start_search(keyword, tab=tab, page=1, tag="search")

    def _on_search_tab_changed(self, index: int) -> None:
        """切换「搜书 / 搜作者」：已有关键词时立即按新模式重新搜索。"""
        mode = "搜书" if index == 0 else "搜作者"
        self.log(f"已切换为{mode}模式")
        keyword = self.search_edit.text().strip()
        if keyword:
            self.do_search()
        else:
            self.result_hint.setText(f"{mode}模式：输入关键词后开始搜索")

    def _load_recommend(self) -> None:
        """首屏推荐：取一个热门关键词作为推荐内容。"""
        if not hasattr(self, "card_list"):
            return
        keyword = self._next_recommend_keyword()
        self.result_hint.setText(f"根据「{keyword}」的热门结果推荐 · 点击卡片查看详情")
        self._start_search(keyword, tab="0", page=1, tag="recommend")

    def _refresh_recommend(self) -> None:
        """换一批：换一个热门关键词重新加载推荐卡片。"""
        keyword = self._next_recommend_keyword()
        self.result_hint.setText(f"根据「{keyword}」的热门结果推荐 · 点击卡片查看详情")
        self._start_search(keyword, tab="0", page=1, tag="recommend")

    def _on_card_selected(self, book: Dict[str, Any]) -> None:
        """点击卡片：同步选中态、触发插件钩子并加载详情。"""
        self.current_book = book
        self.pm.emit(Hook.ON_BOOK_SELECTED, book)
        self._load_detail(book)
        if hasattr(self, "btn_bookmark"):
            self.btn_bookmark.setText(self._bookmark_label(book.get("id", "")))
        self.log(f"已选择：《{book.get('title', '')}》")

    def _on_search_done(self, results: List[Dict[str, Any]]) -> None:
        self.search_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        keyword = self.search_edit.text().strip()
        self.pm.emit(Hook.ON_SEARCH, keyword, results)
        self.search_results = results

        # 隐藏表格保持数据同步（供插件与兼容逻辑读取）
        self.result_table.setRowCount(len(results))
        for row, item in enumerate(results):
            self.result_table.setItem(row, 0, QTableWidgetItem(item["title"]))
            self.result_table.setItem(row, 1, QTableWidgetItem(item["author"]))
            self.result_table.setItem(row, 2, QTableWidgetItem(str(item["words_num"])))
            self.result_table.setItem(row, 3, QTableWidgetItem(item["id"]))

        # 卡片列表（用户实际看到的区域）
        self.card_list.set_books(results)

        if self._last_search_tag == "recommend":
            self.result_title.setText("小说推荐")
            self.current_book = None
            self.detail_download_btn.setEnabled(False)
            self.detail_text.setPlainText("请选择一本书，查看详情并加入下载队列")
        else:
            self.result_title.setText(f"搜索结果（{len(results)}）")
            self.result_hint.setText(f"关键词「{keyword}」· 点击卡片查看详情")
            if results:
                self.card_list.setCurrentRow(0)
                self._load_detail(results[0])

        self.log(f"搜索完成，共 {len(results)} 条结果")

    def _on_search_failed(self, message: str) -> None:
        self.search_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.log(f"搜索失败：{message}")
        QMessageBox.critical(self, "搜索失败", message)

    def _on_row_selected(self) -> None:
        row = self.result_table.currentRow()
        if 0 <= row < len(self.search_results):
            book = self.search_results[row]
            self.current_book = book
            self.pm.emit(Hook.ON_BOOK_SELECTED, book)
            self._load_detail(book)

    def _load_book_by_id(self, book_id: str) -> None:
        """根据书 ID 直达：拉取详情并展示，无需搜索。"""
        if not book_id:
            return
        try:
            from .api import get_book_info
            info = get_book_info(str(book_id), fallback=None)
            book = {
                "id": str(book_id),
                "title": info.get("title", f"书 {book_id}"),
                "author": info.get("author", ""),
                "words_num": info.get("words_num", ""),
                "cover": info.get("img_url", ""),
                "intro": info.get("intro", ""),
                "tags": info.get("tags", ""),
            }
            self.current_book = book
            self._load_detail(book)
            self.log(f"已直达：《{book['title']}》(ID {book_id})")
        except Exception as exc:  # noqa: BLE001
            from .logutil import log_error
            log_error(f"书 ID 直达失败 {book_id}", exc, tag="link")
            QMessageBox.warning(self, "直达失败", f"无法获取该书信息：{exc}")

    def _bulk_download(self, book_ids: List[str]) -> None:
        """批量下载：把多个书 ID 依次解析后加入队列（用当前格式与默认选项）。"""
        if self.pm.get_downloader() is None:
            QMessageBox.information(self, "无下载内核", "当前没有可用的下载内核插件。")
            return
        from .api import get_book_info
        dc = getattr(self, "direct_format_combo", None)
        fmt = dc.currentText() if dc is not None else self.format_combo.currentText()
        added, failed = 0, 0
        for bid in book_ids:
            try:
                info = get_book_info(str(bid), fallback=None)
                book = {
                    "id": str(bid),
                    "title": info.get("title", f"书 {bid}"),
                    "author": info.get("author", ""),
                    "words_num": info.get("words_num", ""),
                }
                self.queue_view.add_task(book, self.default_save_dir, fmt)
                added += 1
            except Exception as exc:  # noqa: BLE001
                from .logutil import log_error
                log_error(f"批量下载解析失败 {bid}", exc, tag="bulk")
                failed += 1
        self.log(f"批量下载：已加入 {added} 本，失败 {failed} 本")
        QMessageBox.information(self, "批量下载", f"已加入队列 {added} 本，失败 {failed} 本")
        if added:
            self.tabs.setCurrentWidget(self.queue_view)

    def _toggle_bookmark(self) -> None:
        """收藏/取消收藏当前书籍。"""
        if not self.current_book:
            return
        bid = str(self.current_book.get("id", ""))
        if not bid:
            return
        if self.store.bookmark_has(bid):
            self.store.bookmark_remove(bid)
            self.log(f"已取消书签：《{self.current_book.get('title')}》")
        else:
            self.store.bookmark_add(self.current_book)
            self.log(f"已加书签：《{self.current_book.get('title')}》")
        if hasattr(self, "reload_search_tools"):
            self.reload_search_tools()
        # 更新按钮文字
        if hasattr(self, "btn_bookmark"):
            self.btn_bookmark.setText(self._bookmark_label(bid))

    def _detail_colors(self):
        """详情区文字配色随主题切换。"""
        if self._dark_theme:
            return TEXT, TEXT_DIM, WARN, BORDER
        return "#2a2a2a", "#666666", "#e65100", "#e0e0e0"

    def _load_detail(self, book: Dict[str, Any]) -> None:
        self.current_book = book
        self.detail_download_btn.setEnabled(True)
        main_c, dim_c, warn_c, border_c = self._detail_colors()
        self.detail_text.setHtml(
            f"<h2 style='color:{main_c}'>{book['title']}</h2>"
            f"<p style='color:{dim_c}'><b>作者：</b>{book['author']}<br>"
            f"<b>字数：</b>{book['words_num']}<br>"
            f"<b>ID：</b>{book['id']}</p>"
            f"<p style='color:{dim_c}'>正在加载详情…</p>"
        )
        # 重建前先停掉上一次的详情请求：断开信号，避免旧请求回调串台；
        # 运行中则等待其结束，并延迟删除，防止反复切书/切主题累积线程对象
        old = getattr(self, "detail_worker", None)
        if old is not None:
            try:
                old.finished.disconnect()
                old.failed.disconnect()
            except (RuntimeError, TypeError):
                pass
            if old.isRunning():
                old.terminate()
                old.wait(1000)
            old.deleteLater()
        self.detail_worker = DetailWorker(book["id"], book, self)
        self.detail_worker.finished.connect(lambda info: self._on_detail_done(book, info))
        self.detail_worker.failed.connect(
            lambda msg, b=book: self._on_detail_failed(b, msg)
        )
        self.detail_worker.start()

    def _on_detail_failed(self, book: Dict[str, Any], msg: str) -> None:
        """详情加载失败回调：校验书籍 ID，避免旧请求失败覆盖当前书籍的面板。"""
        if self.current_book is None or self.current_book.get("id") != book.get("id"):
            return
        self.detail_text.setPlainText(f"详情加载失败：{msg}")

    def _on_detail_done(self, book: Dict[str, Any], info: Dict[str, Any]) -> None:
        if self.current_book is None or self.current_book.get("id") != book.get("id"):
            return
        main_c, dim_c, warn_c, border_c = self._detail_colors()
        html = (
            f"<h2 style='color:{main_c}'>{info['title']}</h2>"
            f"<p style='color:{dim_c}'><b>作者：</b>{info['author']}<br>"
            f"<b>字数：</b>{info['words_num']}<br>"
            f"<b>标签：</b>{info.get('tags') or '—'}<br>"
            f"<b>ID：</b>{info['id']}</p>"
            f"<p style='color:{main_c}'><b>简介：</b><br>{info['intro']}</p>"
        )
        if info.get("degraded"):
            html += f"<p style='color:{warn_c}'>⚠ 详情接口不可用，仅显示搜索结果中的基础信息。</p><hr style='border-color:{border_c}'>"
        self.detail_text.setHtml(html)
        try:
            from .api import get_catalog
            catalog = get_catalog(book["id"])
            if catalog:
                cat_html = f"<p style='color:{dim_c}'><b>目录：</b>共 {len(catalog)} 章<br>"
                cat_html += "、".join(c["title"] for c in catalog[:5])
                if len(catalog) > 5:
                    cat_html += " …"
                cat_html += "</p>"
                cursor = self.detail_text.textCursor()
                cursor.movePosition(QTextCursor.End)
                self.detail_text.setTextCursor(cursor)
                self.detail_text.insertHtml(cat_html)
        except Exception:  # noqa: BLE001
            pass

    def do_download(self) -> None:
        if self.current_book is None:
            QMessageBox.warning(self, "提示", "请先搜索并选中一本书")
            return
        if self.pm.get_downloader() is None:
            QMessageBox.information(
                self, "无下载内核",
                "当前没有插件提供下载内核。\n\n"
                "开源版源码中的章节下载逻辑被作者移除（私有内核），"
                "请安装提供下载内核的插件后再试。\n"
                "示例插件提供的是一个模拟内核，可用来体验流程。",
            )
            return
        from PySide6.QtWidgets import QDialog
        from .download_dialog import DownloadDialog
        book = self.current_book
        # 跳过重复文件：书架上已有同书且文件仍存在则提示并跳过
        if self.store.settings_get("skip_duplicate", True):
            bid = str(book.get("id", ""))
            fmt_now = self.format_combo.currentText()
            for entry in self.store.shelf_all():
                if str(entry.get("book_id", "")) != bid:
                    continue
                if str(entry.get("format", "")).lower() != fmt_now.lower():
                    continue
                fp = str(entry.get("file_path", "") or "")
                if fp and os.path.isfile(fp):
                    self.log(f"跳过重复下载：《{book.get('title')}》已存在文件 {fp}")
                    QMessageBox.information(
                        self, tr("common.tip"),
                        tr("more.duplicate_skip", title=book.get("title", "")))
                    return
        from .qimao_core import CHAPTER_TITLE_COLOR, VOLUME_TITLE_COLOR
        dlg = DownloadDialog(
            book,
            fmt=self.format_combo.currentText(),
            template=str(self.store.settings_get("filename_template", "{title}") or "{title}"),
            parent=self,
            chapter_font=str(self.store.settings_get(
                "epub_chapter_font", "builtin:mashan") or "builtin:mashan"),
            volume_font=str(self.store.settings_get(
                "epub_volume_font", "builtin:zhimang") or "builtin:zhimang"),
            chapter_color=str(self.store.settings_get(
                "epub_chapter_color", CHAPTER_TITLE_COLOR) or CHAPTER_TITLE_COLOR),
            volume_color=str(self.store.settings_get(
                "epub_volume_color", VOLUME_TITLE_COLOR) or VOLUME_TITLE_COLOR),
        )
        if dlg.exec() != QDialog.Accepted:
            return
        fmt = dlg.format()
        options = dlg.options()
        # 下载方式：失败自动重试
        if str(self.store.settings_get("download_method", "builtin") or "builtin") == "builtin_retry":
            options["_retry"] = 2
        self.pm.emit(Hook.ON_DOWNLOAD_START, book)
        self.queue_view.add_task(book, self.default_save_dir, fmt, options=options)
        scope = ""
        if options.get("start_chapter"):
            scope = f"（第{options['start_chapter']}-{options['end_chapter']}章）"
        self.log(f"已加入下载队列：《{book['title']}》（{fmt}）{scope}")
        self.tabs.setCurrentWidget(self.queue_view)

    def _on_queue_task_finished(self, task: Dict[str, Any]) -> None:
        book = task.get("book", {})
        success = task.get("status") == "done"
        result = DownloadResult(
            book_id=book.get("id", ""), title=book.get("title", ""),
            success=success, file_path=task.get("file_path", ""),
            message=task.get("message", ""),
        )
        self.history_view.add_record(
            book, task.get("format", "txt"), success,
            task.get("message", ""), task.get("elapsed", 0.0),
            task.get("file_path", ""),
        )
        if success:
            self.shelf_view.add_entry(
                book, task.get("file_path", ""), task.get("format", "txt"),
                book.get("words_num", ""),
            )
            self.log(f"下载完成：《{book.get('title')}》已加入书架与历史")
            self._run_post_download_actions(book, task.get("file_path", ""),
                                            task.get("format", "txt"))
        else:
            self.log(f"下载失败：《{book.get('title')}》— {task.get('message', '')}")
        # 下载完成系统通知 + 提示音（按设置）
        self._notify_download_result(book, success, task.get("message", ""))
        self.pm.emit(Hook.ON_DOWNLOAD_COMPLETE, result)

    def _notify_download_result(self, book: Dict[str, Any], success: bool,
                                message: str) -> None:
        """按设置显示系统通知并播放提示音。"""
        title = tr("app.name")
        if success:
            body = tr("notify.success", title=book.get("title", ""))
        else:
            body = tr("notify.fail", title=book.get("title", ""),
                      message=(message or "")[:60])
        notify_core.notify_download(title, body, self)
        notify_core.play_download_sound(success)

    def _run_post_download_actions(self, book: Dict[str, Any], file_path: str,
                                   fmt: str) -> None:
        """下载成功后的可配置自动动作（失败只记日志，不影响下载结果）。"""
        from .logutil import log_error, logger
        # 1) EPUB 结构校验
        if fmt == "epub" and self.store.settings_get("auto_verify_epub", True) \
                and file_path and os.path.isfile(file_path):
            try:
                from .epub_check import validate_epub
                r = validate_epub(file_path)
                if r["ok"]:
                    self.log(f"✓ EPUB 校验通过：{os.path.basename(file_path)}（{r['engine']}）")
                else:
                    self.log(f"⚠ EPUB 校验发现问题：{r['errors'][:2]}")
                    logger.warning(f"EPUB 校验失败 {file_path}: {r['errors']}", tag="epubcheck")
            except Exception as exc:  # noqa: BLE001
                log_error("EPUB 校验异常", exc, tag="epubcheck")
        # 2) 自动加入 Calibre 书库
        if self.store.settings_get("auto_calibre", False) and file_path \
                and os.path.isfile(file_path):
            try:
                from .calibre_core import detect_calibre, detect_library, add_to_library
                inst = detect_calibre()
                if inst.available:
                    lib = detect_library()
                    if lib:
                        add_to_library(inst, lib, file_path)
                        self.log(f"✓ 已自动加入 Calibre 书库：{book.get('title')}")
                    else:
                        self.log("⚠ 自动加入 Calibre 失败：未找到书库")
                else:
                    self.log("⚠ 自动加入 Calibre 失败：未检测到 Calibre")
            except Exception as exc:  # noqa: BLE001
                log_error("自动加入 Calibre 失败", exc, tag="autorun")
        # 3) 自动导入微信读书
        if self.store.settings_get("auto_weread", False) and file_path \
                and os.path.isfile(file_path):
            cookie = str(self.store.settings_get("weread_cookie", "") or "")
            try:
                from .weread_core import WeReadUploader
                from .weread_login import load_session
                mobile_session = load_session(self.store)
                if not cookie and mobile_session is None:
                    self.log("⚠ 自动导入微信读书失败：未扫码登录，也未配置 Cookie")
                else:
                    up = WeReadUploader(cookie, mobile_session=mobile_session)
                    r = up.upload(file_path)
                    self.log(("✓ 已自动导入微信读书：" if r["success"] else "⚠ 自动导入微信读书失败：")
                             + book.get("title", "") + " — " + r["message"])
            except Exception as exc:  # noqa: BLE001
                log_error("自动导入微信读书失败", exc, tag="autorun")

    def _on_request_redownload(self, book: Dict[str, Any]) -> None:
        if self.pm.get_downloader() is None:
            QMessageBox.information(self, "无下载内核", "当前没有可用的下载内核插件。")
            return
        fmt = self.format_combo.currentText()
        self.queue_view.add_task(book, self.default_save_dir, fmt)
        self.tabs.setCurrentWidget(self.queue_view)
        self.log(f"已重新加入队列：《{book.get('title')}》")

    def _on_request_read(self, path: str, title: str, key: str,
                         chapter_index: object) -> None:
        """书架请求阅读：切到阅读页并打开书籍。"""
        self.tabs.setCurrentWidget(self.reader_view)
        idx = chapter_index if isinstance(chapter_index, int) else None
        self.reader_view.open_book(path, title, key, idx)
        self.log(f"正在阅读《{title}》")

    def _on_weread_import(self, entries: List[Dict[str, Any]]) -> None:
        """书架右键「导入到微信读书」：切到微信读书页并执行导入。"""
        view = getattr(self, "weread_view", None)
        if view is None:
            QMessageBox.information(self, "微信读书未接入", "未检测到微信读书插件。")
            return
        self.tabs.setCurrentWidget(view)
        view.import_from_shelf(entries)

    def _on_calibre_edit(self, file_path: str) -> None:
        """书架右键「用 Calibre 编辑 EPUB」。"""
        view = getattr(self, "calibre_view", None)
        if view is None:
            QMessageBox.information(self, "Calibre 未接入", "未检测到 Calibre 插件。")
            return
        view.open_external_file(file_path)

    def _open_follow_dialog(self) -> None:
        """打开增量追更对话框。"""
        from .follow_dialog import FollowDialog
        from PySide6.QtCore import Qt
        dlg = FollowDialog(self.store, self)
        # 关闭即销毁，避免反复打开累积对话框及其后台 worker
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        dlg.exec()

    def _on_read_progress(self, key: str, chapter_index: int, percent: float) -> None:
        """阅读进度变化时刷新书架进度列（轻量刷新）。"""
        if self.shelf_view is not None:
            self.shelf_view.refresh()

    def _on_download_progress(self, value: int) -> None:
        motion.animate_progress(self.progress, value)
        self.pm.emit(Hook.ON_DOWNLOAD_PROGRESS, self.current_book, value)
        self.log(f"下载进度：{value}%")

    def _on_download_done(self, result: DownloadResult) -> None:
        self.download_btn.setEnabled(True)
        self.progress.setValue(100 if result.success else 0)
        self.pm.emit(Hook.ON_DOWNLOAD_COMPLETE, result)
        if result.success:
            self.log(f"下载完成：{result.title}")
        else:
            self.log(f"下载失败：{result.message}")

    def _on_download_failed(self, message: str) -> None:
        self.download_btn.setEnabled(True)
        self.log(f"下载失败：{message}")

    # ------------------------------------------------------------------ 工具

    def _check_protocol(self) -> None:
        """后台检测下载内核依赖的搜索/目录接口是否仍可用。"""
        from .workers import ProtocolCheckWorker

        # 防重复点击
        btn = self.settings_protocol_btn
        if getattr(self, "_protocol_worker", None) is not None and \
                self._protocol_worker.isRunning():
            return
        btn.setEnabled(False)
        self.set_protocol_status.setText(tr("set.protocol_checking"))
        self.log("开始检测下载内核协议状态…")

        worker = ProtocolCheckWorker(self)
        self._protocol_worker = worker
        worker.progress.connect(lambda text: self.set_protocol_status.setText(text))
        worker.finished.connect(self._on_protocol_checked)

        def _cleanup():
            btn.setEnabled(True)
            # 线程结束后断开引用，允许被回收
            if getattr(self, "_protocol_worker", None) is worker:
                self._protocol_worker = None

        worker.finished.connect(_cleanup)
        worker.start()

    def _on_protocol_checked(self, result: dict) -> None:
        """显示自检结果；异常时给出上游协议变更提示。"""
        msg = result.get("message", "")
        detail = result.get("detail", "")
        self.set_protocol_status.setText(msg)
        self.set_protocol_status.setToolTip(detail)
        color = SUCCESS if result.get("ok") else "#e08888"
        self.set_protocol_status.setStyleSheet(f"color: {color};")
        self.log(f"内核自检：{msg}" + (f"（{detail}）" if detail else ""))

    def _backup_data(self) -> None:
        """把 ~/.qimao_gui/ 的数据打包为 zip 备份。"""
        import zipfile
        from datetime import datetime
        default = os.path.join(
            os.path.expanduser("~"),
            f"qimao_backup_{datetime.now():%Y%m%d_%H%M%S}.zip",
        )
        path, _ = QFileDialog.getSaveFileName(self, "备份数据", default, "ZIP 文件 (*.zip)")
        if not path:
            return
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                for name in self.store.files():
                    fp = os.path.join(self.store.base_dir, name)
                    if os.path.exists(fp):
                        zf.write(fp, name)
            QMessageBox.information(self, "备份完成", f"数据已备份到：\n{path}")
            self.log(f"数据已备份：{path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "备份失败", str(exc))

    def _restore_data(self) -> None:
        """从 zip 备份恢复数据（覆盖前需确认）。"""
        import zipfile
        path, _ = QFileDialog.getOpenFileName(self, "选择备份文件", "", "ZIP 文件 (*.zip)")
        if not path:
            return
        if QMessageBox.question(
            self, "确认恢复",
            "恢复将覆盖当前的书架/历史/进度等数据，确定继续？\n（建议先备份当前数据）",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = set(zf.namelist())
                for name in self.store.files():
                    if name in names:
                        zf.extract(name, self.store.base_dir)
            # 重新加载内存数据
            self.store.__init__(self.store.base_dir)
            self.shelf_view.refresh()
            self.history_view.refresh()
            if hasattr(self.history_view, "refresh"):
                pass
            QMessageBox.information(self, "恢复完成", "数据已从备份恢复，请重启程序以确保全部生效。")
            self.log("数据已从备份恢复")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "恢复失败", str(exc))

    def _choose_save_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择保存目录", self.default_save_dir)
        if directory:
            self.default_save_dir = directory
            if hasattr(self, "settings_path"):
                self.settings_path.setText(directory)
            self.log(f"保存目录已设为：{directory}")

    def _on_default_format_changed(self, fmt: str) -> None:
        """设置页切换默认下载格式：持久化并同步到搜索页、直达页下拉。"""
        self.store.settings_set("default_format", fmt)
        for combo in (getattr(self, "format_combo", None),
                      getattr(self, "direct_format_combo", None)):
            if combo is not None:
                combo.blockSignals(True)
                combo.setCurrentText(fmt)
                combo.blockSignals(False)
        self.log(f"默认下载格式已设为：{fmt}")

    def _save_filename_template(self) -> None:
        """保存设置页中的导出文件名模板。"""
        tpl = self.settings_template.text().strip() or "{title}"
        self.store.settings_set("filename_template", tpl)
        self.settings_template.setText(tpl)
        self.log(f"导出文件名模板已保存：{tpl}")

    def _show_about(self) -> None:
        QMessageBox.about(
            self, "关于",
            "七猫小说下载器 · GUI 版\n\n"
            "基于 shing-yu/7mao-novel-downloader（AGPL-3.0）\n\n"
            "新增：深色界面、插件扩展机制、书架、任务队列、历史记录。\n"
            "开源版本身不含章节下载内核，可通过插件提供。",
        )

    def closeEvent(self, event) -> None:
        # 最小化到托盘：下载可在后台继续
        tray = getattr(self, "tray", None)
        if (not self._force_quit and tray is not None
                and self.store.settings_get("tray_enabled", True)):
            self.hide()
            self.log("已最小化到系统托盘，下载仍在后台进行。")
            event.ignore()
            return
        # 真正退出：清理资源
        if tray is not None:
            tray.hide()
        # 停止动态背景动画定时器
        dyn = getattr(self, "_dynamic_bg", None)
        if dyn is not None and hasattr(dyn, "stop"):
            dyn.stop()
        if self.queue_view:
            self.queue_view._timer.stop()
            for worker in list(self.queue_view._runners.values()):
                worker.cancel()
                if worker.isRunning():
                    worker.wait(2000)
        for worker in (self.search_worker, self.download_worker, getattr(self, "detail_worker", None)):
            if worker and worker.isRunning():
                # terminate() 是异步请求，必须 wait() 确认线程真正退出，
                # 否则 Qt 销毁仍运行的 QThread 会触发 "Destroyed while thread is still running" 崩溃
                worker.terminate()
                worker.wait(2000)
        # 兜底：停止所有挂在窗口对象树上的后台线程（微信读书/Calibre/笔记/WebDAV/刮削等）。
        # 这些 worker 以窗口或各 view 为 parent，退出时若仍在跑会随父对象销毁而崩溃。
        from PySide6.QtCore import QThread
        seen = {id(w) for w in (self.search_worker, self.download_worker,
                                getattr(self, "detail_worker", None)) if w is not None}
        for worker in self.findChildren(QThread):
            if id(worker) in seen:
                continue
            seen.add(id(worker))
            try:
                if worker.isRunning():
                    worker.terminate()
                    worker.wait(1000)
            except RuntimeError:
                pass
        # 封面加载线程刻意不设 parent（book_card._orphan_loaders 持有引用），
        # 不在上面的对象树里。退出时统一停掉仍在运行的，避免
        # "QThread: Destroyed while thread is still running" 警告。
        try:
            from .book_card import _orphan_loaders
            for loader in list(_orphan_loaders):
                try:
                    if loader.isRunning():
                        loader.terminate()
                        loader.wait(1000)
                except RuntimeError:
                    pass
        except Exception:  # noqa: BLE001
            pass
        self.pm.unload_all()
        event.accept()
