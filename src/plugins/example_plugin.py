# -*- coding: utf-8 -*-
"""
示例插件：演示插件机制的三种典型用法。

1. register_menus  —— 往主窗口菜单栏加一个菜单
2. register_tabs   —— 往主窗口加一个自定义标签页（方案 A：插件能力展示）
3. provide_downloader —— 提供"下载内核"实现（本项目开源版内核被移除，插件可接管）

这是一个可运行的示例：把它复制到本目录并改成 .py 文件即可被自动加载。
页面跟随主程序深浅主题与主题色，图标全部使用国徽（无 emoji）。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMessageBox, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
    QWidget, QPushButton, QFrame,
)

import sys
import os

# 保证能 import 到 gui 包（程序以 src 为工作目录时）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.plugin.base import (
    Plugin, PluginMeta, DownloaderBase, DownloadResult, declare_nav,
)
from gui.widgets import nation_icon
from gui.style import accent_color


class DemoDownloader(DownloaderBase):
    """演示用下载内核：实际并不下载，只返回模拟结果。

    真实插件可在此调用七猫 API 或你自己的内核代码来完成下载。
    """

    name = "演示内核（不实际下载）"

    def download(self, book_id, save_path, fmt="txt", progress_cb=None, **kwargs):
        if progress_cb:
            for p in range(0, 101, 20):
                progress_cb(p)
        return DownloadResult(
            book_id=book_id,
            title=kwargs.get("title", "未知书名"),
            success=True,
            message="这是示例插件返回的模拟结果（未真正下载文件）",
            extra={"demo": True},
        )


class ExampleDemoView(QWidget):
    """插件注入的展示页（方案 A）：Hero + 2×2 能力卡片 + 操作按钮。

    全部使用国徽图标；深浅主题与主题色跟随主程序。
    """

    # 能力卡：(标题, 描述, 国徽)
    ABILITIES = [
        ("菜单注入", "register_menus 挂载点，往菜单栏添加「示例插件」菜单", "mondstadt"),
        ("标签页注入", "register_tabs 挂载点，把任意 QWidget 加入主界面（即本页）", "liyue"),
        ("下载内核接管", "provide_downloader 挂载点，返回下载器实例接管下载行为", "natlan"),
        ("事件监听", "on_search / on_download_complete 等钩子，观察主程序事件", "sumeru"),
    ]

    def __init__(self, main_window, parent=None) -> None:
        super().__init__(parent)
        self._mw = main_window
        self.setObjectName("exampleDemoPage")
        self._cards = []
        self._build_ui()
        self.apply_colors()

    # ---------------------------------------------------------------- 配色
    def _palette(self) -> dict:
        store = getattr(self._mw, "store", None)
        try:
            mode = str(store.settings_get("theme_mode", "dark") or "dark") if store else "dark"
            key = str(store.settings_get("accent_color", "purple") or "purple") if store else "purple"
        except Exception:  # noqa: BLE001
            mode, key = "dark", "purple"
        dark = mode not in ("light",)
        accent = accent_color(key)
        if dark:
            return dict(bg="#17131d", card="#241e2e", card2="#2b2438",
                        text="#f5f0fa", dim="#9a92a5", faint="#6e6679",
                        line="#332a42", accent=accent, ok="#57d9a3")
        return dict(bg="#f4f2f7", card="#ffffff", card2="#f3eff6",
                    text="#241f2c", dim="#6b6478", faint="#9a92a5",
                    line="#ddd8e3", accent=accent, ok="#1f9d6b")

    def apply_colors(self) -> None:
        p = self._palette()
        self.setStyleSheet(f"QWidget#exampleDemoPage{{background-color:{p['bg']};}}")

        # Hero
        self.hero.setStyleSheet(
            f"QFrame#exHero{{background-color:{p['card2']};"
            f"border:1px solid {p['accent']};border-radius:12px;}}")
        self.hero_title.setStyleSheet(
            f"color:{p['text']};font-size:17px;font-weight:bold;"
            "background:transparent;border:none;padding:0;")
        self.ver_lbl.setStyleSheet(
            f"color:{p['accent']};font-size:10px;font-weight:bold;background-color:{p['card']};"
            "border-radius:8px;padding:2px 8px;")
        self.hero_sub.setStyleSheet(
            f"color:{p['dim']};font-size:11px;background:transparent;border:none;padding:0;")
        self.hero_icon.setPixmap(nation_icon("nodkrai", p["accent"], 44).pixmap(44, 44))

        # 能力卡
        for card in self._cards:
            card.setStyleSheet(
                f"QFrame#exCard{{background-color:{p['card']};"
                f"border:1px solid {p['line']};border-radius:10px;}}"
                f"QFrame#exCard:hover{{border:1px solid {p['accent']};}}")
            card._icon_lbl.setPixmap(
                nation_icon(card._nation_key, p["accent"], 30).pixmap(30, 30))
            card._title_lbl.setStyleSheet(
                f"color:{p['text']};font-size:12px;font-weight:bold;"
                "background:transparent;border:none;padding:0;")
            card._desc_lbl.setStyleSheet(
                f"color:{p['dim']};font-size:10px;background:transparent;border:none;padding:0;")
            card._badge.setStyleSheet(
                f"color:{p['ok']};font-size:9px;font-weight:bold;"
                "background:transparent;border:none;padding:0;")

        # 按钮
        self.btn_try.setStyleSheet(
            f"QPushButton{{background-color:{p['accent']};color:{p['bg']};border:none;"
            "border-radius:8px;padding:7px 16px;font-size:11px;font-weight:bold;}")
        self.btn_summary.setStyleSheet(
            f"QPushButton{{background-color:{p['card2']};color:{p['text']};"
            f"border:1px solid {p['line']};border-radius:8px;padding:7px 16px;font-size:11px;}}"
            f"QPushButton:hover{{background-color:{p['card']};border:1px solid {p['accent']};}}")

    # ---------------------------------------------------------------- 构建
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 14)
        root.setSpacing(14)

        # Hero
        self.hero = QFrame()
        self.hero.setObjectName("exHero")
        h = QHBoxLayout(self.hero)
        h.setContentsMargins(18, 14, 18, 14)
        h.setSpacing(14)
        self.hero_icon = QLabel()
        self.hero_icon.setFixedSize(44, 44)
        self.hero_icon.setAlignment(Qt.AlignCenter)
        h.addWidget(self.hero_icon)
        info = QVBoxLayout()
        info.setSpacing(5)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self.hero_title = QLabel("示例插件")
        self.ver_lbl = QLabel("v1.0.0")
        title_row.addWidget(self.hero_title)
        title_row.addWidget(self.ver_lbl)
        title_row.addStretch()
        info.addLayout(title_row)
        self.hero_sub = QLabel("演示插件机制的四种典型用法 · 由 register_tabs 注入")
        info.addWidget(self.hero_sub)
        h.addLayout(info, 1)
        root.addWidget(self.hero)

        # 2×2 能力卡
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, (title, desc, nation) in enumerate(self.ABILITIES):
            card = QFrame()
            card.setObjectName("exCard")
            card._nation_key = nation
            card.setFixedHeight(116)
            cl = QHBoxLayout(card)
            cl.setContentsMargins(14, 12, 14, 12)
            cl.setSpacing(12)
            cl.setAlignment(Qt.AlignTop)
            card._icon_lbl = QLabel()
            card._icon_lbl.setFixedSize(30, 30)
            card._icon_lbl.setAlignment(Qt.AlignCenter)
            cl.addWidget(card._icon_lbl, 0, Qt.AlignTop)
            tx = QVBoxLayout()
            tx.setSpacing(6)
            title_bar = QHBoxLayout()
            card._title_lbl = QLabel(title)
            card._badge = QLabel("● 可用")
            title_bar.addWidget(card._title_lbl)
            title_bar.addStretch()
            title_bar.addWidget(card._badge)
            tx.addLayout(title_bar)
            card._desc_lbl = QLabel(desc)
            card._desc_lbl.setWordWrap(True)
            tx.addWidget(card._desc_lbl, 0, Qt.AlignTop)
            tx.addStretch()
            cl.addLayout(tx, 1)
            self._cards.append(card)
            grid.addWidget(card, i // 2, i % 2)
        root.addLayout(grid, 0)
        root.addStretch(1)

        # 操作按钮
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.btn_try = QPushButton("点我试试")
        self.btn_try.setCursor(Qt.PointingHandCursor)
        self.btn_try.clicked.connect(self._on_try)
        self.btn_summary = QPushButton("查看插件摘要")
        self.btn_summary.setCursor(Qt.PointingHandCursor)
        self.btn_summary.clicked.connect(self._on_summary)
        bar.addWidget(self.btn_try)
        bar.addWidget(self.btn_summary)
        bar.addStretch()
        root.addLayout(bar)

    # ---------------------------------------------------------------- 行为
    def _on_try(self) -> None:
        QMessageBox.information(
            self, "示例插件",
            "示例插件的标签页按钮被点击了！\n\n这一页由 register_tabs 挂载点注入。")

    def _on_summary(self) -> None:
        pm = getattr(self._mw, "pm", None)
        QMessageBox.information(self, "插件摘要", pm.summary() if pm else "无插件管理器")


class ExamplePlugin(Plugin):
    meta = PluginMeta(
        name="示例插件",
        version="1.0.0",
        author="WorkBuddy",
        description="演示菜单注入、标签页注入与下载内核接管，可安全删除",
        enabled=True,
    )

    def on_load(self, manager):
        self.log("示例插件已加载")

    def register_menus(self, main_window):
        """在主窗口菜单栏增加一个「示例插件」菜单。"""
        menu = main_window.menuBar().addMenu("示例插件")

        action = menu.addAction("关于示例插件")
        action.triggered.connect(
            lambda: QMessageBox.information(
                main_window,
                "示例插件",
                "这是通过插件的 register_menus 挂载点动态添加的菜单项。\n\n"
                "插件机制支持：菜单、标签页、搜索/下载事件、下载内核接管。",
            )
        )

        action2 = menu.addAction("查看插件摘要")
        action2.triggered.connect(
            lambda: QMessageBox.information(main_window, "插件摘要", self.manager.summary())
        )

    def register_tabs(self, main_window):
        """返回一个自定义 QWidget，主窗口会把它作为标签页加入。"""
        view = ExampleDemoView(main_window)
        declare_nav(view, "nav.example", "🔌")
        return [view]

    def provide_downloader(self):
        """接管下载内核。返回 None 则不接管；这里返回一个演示内核。"""
        return DemoDownloader()

    def on_book_selected(self, book_info):
        self.log(f"示例插件观察到选中书籍：{book_info.get('title', book_info.get('id'))}")

    def on_download_complete(self, result: DownloadResult):
        self.log(f"示例插件观察到下载完成：{result.title} (成功={result.success})")
