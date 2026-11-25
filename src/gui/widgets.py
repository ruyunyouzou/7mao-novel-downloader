# -*- coding: utf-8 -*-
"""自定义界面组件：标题栏、卡片容器、导航按钮等。"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QFrame, QSizePolicy

from .style import BG_TOP, BORDER_LIGHT, TEXT_DIM, TEXT, ACCENT, ACCENT_HOVER
from .version import VERSION_LABEL
from .i18n import tr, manager as i18n_manager


# 分组标题可使用的原神国徽（8 个循环分配给各功能分组）
NATION_ICONS = (
    "mondstadt", "liyue", "inazuma", "sumeru",
    "fontaine", "natlan", "nodkrai", "snezhnaya",
)


def nation_icon(nation: str, color: str, size: int = 18) -> QIcon:
    """把白色国徽 PNG 染成指定颜色，返回 QIcon。

    部件独立配色：若用户给该国徽设置了自定义色，则强制覆盖 ``color``
    （包括原本的固定对比色），实现“改一个国徽，全项目同名图标一起变”。
    """
    from . import nation_theme
    from .resource_path import resource_path
    color = nation_theme.resolved_nation_color(nation, color)
    pm = QPixmap(resource_path("assets", "nation_icons", f"{nation}.png"))
    if pm.isNull():
        return QIcon()
    pm = nation_theme.tint_pixmap(pm, color, size)
    return QIcon(pm)


def apply_nation_icon(widget, nation: str, color: str = ACCENT, size: int = 16) -> None:
    """给按钮/标签设置国徽图标（替代 emoji 文本前缀）。

    - 按钮（QAbstractButton）：setIcon，自动去掉文字开头的 emoji
    - 标签（QLabel）：setPixmap
    记录 ``_nation`` / ``_nation_size`` 供主题色切换时统一刷新。
    """
    from PySide6.QtWidgets import QAbstractButton
    widget._nation = nation
    widget._nation_size = size
    icon = nation_icon(nation, color, size)
    if isinstance(widget, QAbstractButton):
        widget.setIcon(icon)
        widget.setIconSize(QSize(size, size))
        # 去掉文字开头的 emoji（一个 emoji + 可选空格）
        import re
        widget.setText(re.sub(r"^[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]\s*", "",
                              widget.text()))
    else:  # QLabel
        widget.setPixmap(icon.pixmap(size, size))


@lru_cache(maxsize=128)
def nation_pixmap(nation: str, color: str, size: int) -> QPixmap:
    """带缓存的国徽 pixmap，供卡片等高频刷新处使用，避免反复染色。

    与 ``nation_icon`` 同源（会走部件独立配色），仅多了 pixmap 级缓存。
    """
    return nation_icon(nation, color, size).pixmap(size, size)


def goto_search_page(widget) -> None:
    """空状态「去搜索」：把主窗口切到搜索标签页。

    队列页与历史页共用；找不到搜索页时静默不动。
    """
    win = widget.window()
    if not win or not hasattr(win, "tabs"):
        return
    for i in range(win.tabs.count()):
        text = win.tabs.tabText(i)
        if "搜索" in text or "search" in text.lower():
            win.tabs.setCurrentIndex(i)
            return


class GroupTitleLabel(QWidget):
    """带原神国徽的功能分组标题。

    重写 setText 只更新文字、不重建图标，因此语言切换时调用 setText
    不会丢失国徽。图标颜色可通过 set_icon_color 更新（跟随主题色）。
    """

    def __init__(self, text: str, nation: str, color: str = ACCENT,
                 font_size: int = 14, parent=None) -> None:
        super().__init__(parent)
        self._nation = nation
        self._color = color
        self._font_size = font_size
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        self._icon_label = QLabel()
        self._icon_label.setFixedSize(font_size + 4, font_size + 4)
        if not nation:  # 无国徽时不占位
            self._icon_label.hide()
        root.addWidget(self._icon_label)
        self._text_label = QLabel(text)
        self._text_label.setStyleSheet(
            f"color: {color}; font-size: {font_size}px; font-weight: bold;"
            "background:transparent;border:none;padding:0;")
        root.addWidget(self._text_label, 1)
        self.set_icon_color(color)

    def setText(self, text: str) -> None:  # noqa: N802
        self._text_label.setText(text)

    def text(self) -> str:  # noqa: N802
        return self._text_label.text()

    def set_icon_color(self, color: str) -> None:
        """更新图标与文字颜色（主题色切换时调用）。"""
        self._color = color
        if self._nation:
            self._icon_label.setPixmap(
                nation_icon(self._nation, color, self._font_size + 4).pixmap(
                    self._font_size + 4, self._font_size + 4))
        self._text_label.setStyleSheet(
            f"color: {color}; font-size: {self._font_size}px; font-weight: bold;"
            "background:transparent;border:none;padding:0;")

    def setStyleSheet(self, style: str) -> None:  # noqa: N802
        """兼容旧代码对标题 setStyleSheet 的调用，转发给文字标签。"""
        self._text_label.setStyleSheet(style)



class TitleBar(QWidget):
    """自定义标题栏：左侧账号入口，右侧版本 / 主题切换 / 窗口控制。"""

    theme_toggled = Signal(bool)  # True=深色
    account_clicked = Signal()    # 点击「账号」入口

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(52)
        # 自定义 QWidget 子类默认不绘制 QSS 背景；开启后标题栏自身背景真正绘制，
        # 不再透出父容器 #appCentral 的底色（否则自定义界面背景色会在顶部漏出）。
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            QWidget {{ background-color: {BG_TOP}; border-bottom: 1px solid {BORDER_LIGHT}; }}
            QLabel {{ background: transparent; }}
        """)
        self._dark = True
        self._drag_pos = None
        self._build()
        try:
            i18n_manager().changed.connect(self.retranslate)
        except Exception:  # noqa: BLE001
            pass

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 12, 0)
        layout.setSpacing(10)

        # logo 与应用名已移至左侧导航栏；左侧放账号入口
        self.account_btn = QPushButton("登录")
        self.account_btn.setToolTip(tr("titlebar.account_tip"))
        self.account_btn.setStyleSheet(f"""
            QPushButton {{ background-color: {ACCENT}; color: #1a1a1a; border: none;
                           border-radius: 8px; padding: 6px 14px; font-size: 12px; font-weight: bold; }}
            QPushButton:hover {{ background-color: {ACCENT_HOVER}; }}
        """)
        self.account_btn.clicked.connect(self.account_clicked.emit)
        layout.addWidget(self.account_btn)

        layout.addStretch()

        # 版本号
        self.ver_label = QLabel(tr("titlebar.version", v=VERSION_LABEL))
        self.ver_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.ver_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        self.ver_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        layout.addWidget(self.ver_label)

        # 主题切换按钮
        self.theme_btn = QPushButton("☾")
        self.theme_btn.setFixedSize(34, 30)
        self.theme_btn.setToolTip(tr("titlebar.theme_tip"))
        self.theme_btn.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; border: 1px solid {BORDER_LIGHT};
                           border-radius: 8px; color: {TEXT}; font-size: 16px; }}
            QPushButton:hover {{ background-color: #2f2f2f; }}
        """)
        self.theme_btn.clicked.connect(self._toggle_theme)
        layout.addWidget(self.theme_btn)

        # 窗口控制按钮
        for text, slot, tip in [
            ("−", self._minimize, "最小化"),
            ("□", self._maximize, "最大化/还原"),
            ("✕", self._close, "关闭"),
        ]:
            btn = QPushButton(text)
            btn.setFixedSize(34, 30)
            btn.setToolTip(tip)
            btn.setStyleSheet(f"""
                QPushButton {{ background-color: transparent; border: none; color: {TEXT_DIM}; font-size: 15px; }}
                QPushButton:hover {{ background-color: #2f2f2f; color: {TEXT}; }}
            """)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

    def retranslate(self, *args) -> None:
        """界面语言切换后刷新标题栏文案。"""
        self.account_btn.setToolTip(tr("titlebar.account_tip"))
        self.theme_btn.setToolTip(tr("titlebar.theme_tip"))
        self.ver_label.setText(tr("titlebar.version", v=VERSION_LABEL))

    def set_account_label(self, text: str) -> None:
        """根据是否已配置账号更新入口文案。"""
        self.account_btn.setText(text)

    def refresh_theme(self, dark: bool) -> None:
        """标题栏跟随深浅主题（自身内联样式表优先级高于全局 QSS）。"""
        self._dark = dark
        bg = BG_TOP if dark else "#ffffff"
        bd = BORDER_LIGHT if dark else "#e0e0e0"
        fg = TEXT if dark else "#333333"
        hover = "#2f2f2f" if dark else "#eeeeee"
        self.setStyleSheet(f"""
            QWidget {{ background-color: {bg}; border-bottom: 1px solid {bd}; }}
            QLabel {{ background: transparent; }}
            QPushButton {{ background-color: transparent; border: none; color: {TEXT_DIM}; font-size: 15px; }}
            QPushButton:hover {{ background-color: {hover}; color: {fg}; }}
        """)
        self.theme_btn.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; border: 1px solid {bd};
                           border-radius: 8px; color: {fg}; font-size: 16px; }}
            QPushButton:hover {{ background-color: {hover}; }}
        """)
        self.theme_btn.setText("☾" if dark else "☀")

    def _toggle_theme(self) -> None:
        self._dark = not self._dark
        self.theme_btn.setText("☾" if self._dark else "☀")
        self.theme_toggled.emit(self._dark)

    def _minimize(self) -> None:
        w = self.window()
        if w:
            w.showMinimized()

    def _maximize(self) -> None:
        w = self.window()
        if w:
            if w.isMaximized():
                w.showNormal()
            else:
                w.showMaximized()

    def _close(self) -> None:
        w = self.window()
        if w:
            w.close()

    # --- 拖动窗口（frameless 时） ---
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos and event.buttons() & Qt.LeftButton:
            delta = event.globalPosition().toPoint() - self._drag_pos
            w = self.window()
            if w:
                w.move(w.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None


class Card(QFrame):
    """圆角卡片容器（跟随深浅主题）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._dark = True
        self._apply_style()

    def _apply_style(self) -> None:
        bg = "#242424" if self._dark else "#ffffff"
        bd = "#2c2c2c" if self._dark else "#e5e5e5"
        self.setStyleSheet(f"""
            QFrame {{ background-color: {bg}; border: 1px solid {bd}; border-radius: 8px; }}
        """)

    def refresh_theme(self, dark: bool) -> None:
        self._dark = dark
        self._apply_style()


def section_title(text: str, nation: str = "", color: str = ACCENT) -> GroupTitleLabel:
    """生成卡片内的分区标题（nation 非空时带原神国徽）。"""
    label = GroupTitleLabel(text, nation, color, font_size=16)
    label.setStyleSheet("font-size: 16px; font-weight: bold; padding: 4px 0;")
    return label


def hint_label(text: str) -> QLabel:
    """生成弱化提示文字标签。"""
    label = QLabel(text)
    label.setStyleSheet("color: #9a9a9a; font-size: 12px;")
    return label
