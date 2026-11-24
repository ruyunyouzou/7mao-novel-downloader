# -*- coding: utf-8 -*-
"""
左侧导航栏组件（复刻番茄小说下载器的侧边栏布局）。

截图风格：
- 顶部：应用 logo + 应用名
- 下方：竖排导航项（图标 + 文字），选中项带左侧淡紫竖条 + 高亮背景
- 右侧：堆叠页面容器

对外暴露与 QTabWidget 兼容的接口：addTab / setCurrentWidget / setCurrentIndex /
count / tabText / currentIndex / currentWidget，因此主窗口逻辑无需改动。
"""

from __future__ import annotations

import os
from typing import List, Optional

from PySide6.QtCore import Qt, Signal, QTimer, QSize, QSettings
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QStackedWidget, QFrame, QStackedLayout, QSizePolicy

from .style import BG_TOP, BORDER, TEXT, ACCENT, nav_font_family
from .version import VERSION_LABEL
from . import motion

# 国徽图标顺序（原神各国），导航项超出数量时循环使用
NATION_ICONS = [
    "mondstadt", "liyue", "inazuma", "sumeru",
    "fontaine", "natlan", "nodkrai", "snezhnaya",
]
# 侧栏宽度可拖拽范围
SIDEBAR_MIN_WIDTH = 200
SIDEBAR_MAX_WIDTH = 250
SIDEBAR_DEFAULT_WIDTH = 208


class SideResizeHandle(QFrame):
    """侧栏右缘的拖拽手柄：左右拖动在 [SIDEBAR_MIN_WIDTH, SIDEBAR_MAX_WIDTH] 间调整侧栏宽度。"""

    def __init__(self, side_nav: "SideNavWidget", parent=None) -> None:
        super().__init__(parent)
        self._nav = side_nav
        self.setFixedWidth(5)
        self.setCursor(Qt.SplitHCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._press_x = 0
        self._press_w = 0
        self._dragging = False

    def _apply_color(self) -> None:
        c = QColor(getattr(self._nav, "_accent_color", ACCENT))
        # 鼠标悬停/拖动时高亮，平时用边框色，视觉上提示可拖拽
        if self._dragging or self.underMouse():
            self.setStyleSheet(f"QFrame {{ background-color: {c.name()}; border: none; }}")
        else:
            self.setStyleSheet(f"QFrame {{ background-color: {BORDER}; border: none; }}")

    def enterEvent(self, event) -> None:  # noqa: N802
        self._apply_color()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._apply_color()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._press_x = event.globalPosition().x()
            self._press_w = self._nav.side.width()
            self._apply_color()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            delta = event.globalPosition().x() - self._press_x
            new_w = max(SIDEBAR_MIN_WIDTH, min(SIDEBAR_MAX_WIDTH, self._press_w + delta))
            self._nav.side.setFixedWidth(int(new_w))
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._dragging and event.button() == Qt.LeftButton:
            self._dragging = False
            self._apply_color()
            # 持久化宽度
            QSettings("Xingxian", "QimaoGUI").setValue("sidebarWidth", self._nav.side.width())
            event.accept()


def _tinted_icon(path: str, color: str, size: int = 32) -> QIcon:
    """把白色国徽 PNG 染成指定颜色，返回 QIcon。

    部件独立配色：从文件名识别国徽名，若用户设过该国徽颜色则强制覆盖
    ``color``，与 widgets.nation_icon 的强制染色语义保持一致。
    """
    from . import nation_theme
    nation = os.path.splitext(os.path.basename(path))[0]
    if nation in nation_theme.NATIONS:
        color = nation_theme.resolved_nation_color(nation, color)
    pm = QPixmap(path)
    if pm.isNull():
        return QIcon()
    pm = nation_theme.tint_pixmap(pm, color, size)
    return QIcon(pm)


def _tint(color: str, alpha: int) -> str:
    """把强调色转成带透明度的 #AARRGGBB，用于导航项高亮底色。"""
    c = QColor(color)
    if not c.isValid():
        c = QColor(ACCENT)
    c.setAlpha(alpha)
    return c.name(QColor.NameFormat.HexArgb)


def _shade(color: str, factor: int = 145) -> str:
    """把强调色压暗成同色系深色档（浅色主题文字用，保证白底对比度）。"""
    c = QColor(color)
    if not c.isValid():
        c = QColor(ACCENT)
    return c.darker(factor).name()


class NavButton(QPushButton):
    """单个导航项：左侧淡紫竖条 + 图标 + 文字。"""

    def __init__(self, icon: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self.icon_text = icon
        self.nav_text = text
        self._active = False
        self._dark = True
        self._accent = ACCENT
        # 分部件配色覆盖色：None 表示跟随主题色；否则为 "#RRGGBB"
        self._text_color: Optional[str] = None
        self._icon_color_override: Optional[str] = None
        self._bg_color: Optional[str] = None
        self._indent = 0  # 分组子项缩进像素（0=顶层）
        self._icon_path: Optional[str] = None  # 国徽图标路径（None 表示用 emoji）
        self.setCheckable(True)
        self.setFixedHeight(52)
        self.setCursor(Qt.PointingHandCursor)
        self.setIconSize(QSize(32, 32))
        self.setText(f"  {icon}   {text}")
        self.setStyleSheet(self._qss(False))

    def _icon_color(self) -> str:
        """图标色：国徽自定义色 > 自定义图标色 > 文字色 > 主题色。

        国徽自身颜色在 _tinted_icon 里已按国徽名强制解析，这里只处理
        “未给该国徽设色时”的兜底色（跟随文字/主题）。
        """
        from . import nation_theme
        if self._icon_color_override:
            return self._icon_color_override
        if self._text_color:
            return self._text_color
        nt_text = nation_theme.nav_text_color()
        if nt_text:
            return nt_text
        return self._accent if self._dark else _shade(self._accent)

    def _refresh_icon(self) -> None:
        """按当前图标路径与主题色重绘图标。"""
        if self._icon_path and os.path.isfile(self._icon_path):
            self.setIcon(_tinted_icon(self._icon_path, self._icon_color()))
        else:
            self.setIcon(QIcon())

    def set_nation_icon(self, path: str) -> None:
        """设置为国徽图片图标（替代 emoji）；图标跟主题色走。"""
        self._icon_path = path
        self._refresh_icon()
        # 有图片图标时，文字不再带 emoji 前缀
        self.setText(f"   {self.nav_text}")

    def _qss(self, active: bool) -> str:
        # 侧边栏文字统一使用马善政毛笔楷书；emoji 图标由 Qt 字体回退渲染，不受影响。
        # 文字/图标/背景三色可独立覆盖；未覆盖时跟随主题色（保持原有观感）。
        from . import nation_theme
        font = nav_font_family()
        c = self._accent
        # 文字色：分部件配色覆盖 > 配色中心导航文字色 > 主题色
        if self._text_color:
            text_c = self._text_color
        else:
            nt_text = nation_theme.nav_text_color()
            text_c = nt_text if nt_text else (c if self._dark else _shade(c))
        # 背景派生基准色：分部件配色覆盖 > 配色中心功能背景色 > 主题色
        if self._bg_color:
            bgc = self._bg_color
        else:
            nt_bg = nation_theme.nav_bg_color()
            bgc = nt_bg if nt_bg else c
        # 分组子项缩进：在基础 13px 上叠加，让层级一目了然
        pad_left = 13 + self._indent

        if self._dark:
            a_active, a_idle, a_hover = 58, 24, 45
        else:
            a_active, a_idle, a_hover = 40, 16, 30
        if active:
            # 左侧竖条由独立的可滑动指示条绘制，这里保持透明，外观不变
            return f"""
                QPushButton {{
                    background-color: {_tint(bgc, a_active)}; color: {text_c}; border: none;
                    border-left: 3px solid transparent; border-radius: 4px;
                    text-align: left; padding: 0 10px 0 {pad_left}px;
                    font-family: {font}; font-size: 18px; font-weight: normal;
                }}
            """
        return f"""
            QPushButton {{
                background-color: {_tint(bgc, a_idle)}; color: {text_c}; border: none;
                border-left: 3px solid transparent; border-radius: 4px;
                text-align: left; padding: 0 10px 0 {pad_left}px;
                font-family: {font}; font-size: 18px; font-weight: normal;
            }}
            QPushButton:hover {{ background-color: {_tint(bgc, a_hover)}; color: {text_c}; }}
        """

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setChecked(active)
        self.setStyleSheet(self._qss(active))

    def set_nav_text(self, text: str) -> None:
        """更新导航文字（国际化切换时使用），保留图标。"""
        self.nav_text = text
        if self._icon_path:
            self.setText(f"   {text}")
        else:
            self.setText(f"  {self.icon_text}   {text}")

    def set_accent(self, color: str) -> None:
        """切换主题色：文字、高亮底色与图标同步刷新。"""
        self._accent = color
        self.setStyleSheet(self._qss(self._active))
        self._refresh_icon()

    def refresh_theme(self, dark: bool) -> None:
        self._dark = dark
        self.setStyleSheet(self._qss(self._active))
        self._refresh_icon()

    def set_nav_colors(self, text: Optional[str] = None,
                       icon: Optional[str] = None,
                       bg: Optional[str] = None) -> None:
        """设置分部件配色覆盖色；传 None 表示该部件跟随主题色。"""
        self._text_color = text or None
        self._icon_color_override = icon or None
        self._bg_color = bg or None
        self.setStyleSheet(self._qss(self._active))
        self._refresh_icon()

    def set_indent(self, px: int) -> None:
        """设置分组层级缩进（0=顶层，>0=子项）。"""
        self._indent = max(0, int(px))
        self.setStyleSheet(self._qss(self._active))

    # ---- 分组（折叠子项）----
    def init_group(self) -> None:
        self._children: list = []
        self._expanded = True

    def add_group_child(self, child: "NavButton") -> None:
        if not hasattr(self, "_children"):
            self.init_group()
        self._children.append(child)

    def set_expanded(self, expanded: bool) -> None:
        if not hasattr(self, "_children"):
            self.init_group()
        self._expanded = bool(expanded)
        for child in self._children:
            child.setVisible(self._expanded)

    def toggle_group(self) -> None:
        self.set_expanded(not getattr(self, "_expanded", True))

    def has_group(self) -> bool:
        return bool(getattr(self, "_children", None))


class NavGroupHeader(QFrame):
    """分组标题行：可点击折叠/展开，右侧带 ▾/▸ 箭头。本身不占页面索引。"""

    def __init__(self, text: str, icon_path: str = "", parent=None) -> None:
        super().__init__(parent)
        self._text = text
        self._expanded = True
        self._children: list = []
        self._dark = True
        self._accent = ACCENT
        self._icon_path = icon_path
        self.setFixedHeight(38)
        self.setCursor(Qt.PointingHandCursor)
        root = QHBoxLayout(self)
        root.setContentsMargins(6, 0, 10, 0)
        root.setSpacing(6)
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(26, 26)
        self.icon_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.icon_label)
        self.label = QLabel(text)
        root.addWidget(self.label, 1)
        self.arrow = QLabel("▾")
        root.addWidget(self.arrow)
        self._apply_style()
        self.mousePressEvent = lambda e: self.toggle()

    def _icon_color(self) -> str:
        from . import nation_theme
        override = getattr(self, "_text_override", None)
        if override:
            return override
        nt_text = nation_theme.nav_text_color()
        if nt_text:
            return nt_text
        return self._accent if self._dark else _shade(self._accent)

    def _apply_style(self) -> None:
        color = self._icon_color()
        self.label.setStyleSheet(
            f"color: {color}; font-size: 14px; font-weight: bold;")
        self.arrow.setStyleSheet(f"color: {color}; font-size: 12px;")
        # 国徽图标跟随分组文字色染色
        if self._icon_path and os.path.isfile(self._icon_path):
            self.icon_label.setPixmap(_tinted_icon(self._icon_path, color, 26).pixmap(26, 26))
        else:
            self.icon_label.clear()

    def set_text_override(self, color) -> None:
        self._text_override = color or None
        self._apply_style()

    def set_text(self, text: str) -> None:
        self._text = text
        self.label.setText(text)

    def add_group_child(self, child) -> None:
        self._children.append(child)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.arrow.setText("▾" if self._expanded else "▸")
        for child in self._children:
            child.setVisible(self._expanded)
        # 折叠/展开后通知导航容器重算选中指示条（选中项可能被隐藏）
        cb = getattr(self, "_after_toggle", None)
        if callable(cb):
            QTimer.singleShot(0, cb)

    def toggle_group(self) -> None:
        self.set_expanded(not self._expanded)

    def has_group(self) -> bool:
        return bool(self._children)

    def toggle(self) -> None:
        self.toggle_group()

    def is_expanded(self) -> bool:
        return self._expanded

    def setExpanded(self, expanded: bool) -> None:  # noqa: N802
        self.set_expanded(expanded)

    def set_accent(self, color: str) -> None:
        self._accent = color
        self._apply_style()

    def refresh_theme(self, dark: bool) -> None:
        self._dark = dark
        self._apply_style()


class SideNavWidget(QWidget):
    """左侧导航 + 右侧内容，接口兼容 QTabWidget。"""

    currentChanged = Signal(int)

    def __init__(self, app_name: str = "七猫小说下载器", parent=None) -> None:
        super().__init__(parent)
        self._buttons: List[NavButton] = []
        self._stack = QStackedWidget()
        self._stack.setObjectName("appStack")
        # 内容区背景层：背景 widget 在下，页面 stack 在上（StackAll 叠放）
        self._backdrop_host = QWidget()
        self._backdrop_host.setObjectName("appBackdropHost")
        self._content_layer = QStackedLayout(self._backdrop_host)
        self._content_layer.setStackingMode(QStackedLayout.StackAll)
        self._content_layer.setContentsMargins(0, 0, 0, 0)
        self._placeholder = QWidget()  # 默认空背景（透明，透出 #appStack 纯色/渐变）
        self._placeholder.setObjectName("appBackdrop")
        self._content_layer.addWidget(self._placeholder)
        self._content_layer.addWidget(self._stack)
        self._content_layer.setCurrentWidget(self._stack)
        self._active_backdrop = self._placeholder
        self._app_name = app_name
        self._build()

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------- 左侧栏 ----------
        # 宽度可拖拽调整，并记住上次的选择（限定在合理范围内）
        saved_w = QSettings("Xingxian", "QimaoGUI").value("sidebarWidth", SIDEBAR_DEFAULT_WIDTH)
        try:
            saved_w = int(saved_w)
        except (TypeError, ValueError):
            saved_w = SIDEBAR_DEFAULT_WIDTH
        saved_w = max(SIDEBAR_MIN_WIDTH, min(SIDEBAR_MAX_WIDTH, saved_w))

        self.side = QFrame()
        self.side.setFixedWidth(saved_w)
        self.side.setStyleSheet(f"""
            QFrame {{ background-color: {BG_TOP}; border-right: 1px solid {BORDER}; }}
        """)
        side_layout = QVBoxLayout(self.side)
        side_layout.setContentsMargins(12, 16, 12, 12)
        side_layout.setSpacing(4)

        # 顶部 logo + 应用名
        brand = QWidget()
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(4, 0, 4, 0)
        brand_layout.setSpacing(10)

        # 应用 logo：圆角方块 + 猫头融合数字 7 + 右下角下载徽章
        from PySide6.QtGui import QPixmap
        from .resource_path import resource_path
        self.logo = QLabel()
        self.logo.setFixedSize(34, 34)
        self.logo.setAlignment(Qt.AlignCenter)
        logo_path = resource_path("assets", "app_logo.png")
        if os.path.isfile(logo_path):
            pixmap = QPixmap(logo_path).scaled(
                34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.logo.setPixmap(pixmap)
        else:
            # 资源缺失时降级为原来的文字方块，不影响使用
            self.logo.setText("猫")
            self.logo.setStyleSheet(f"""
                background-color: {ACCENT}; color: #1a1a1a;
                border-radius: 10px; font-size: 17px; font-weight: bold;
            """)
        brand_layout.addWidget(self.logo)

        self.app_name_label = QLabel(self._app_name)
        # 马善政无原生粗体字重，合成加粗（bold）会把毛笔笔锋糊成一团；
        # 用大一号的 normal 字重替代，字号 18px，笔锋更清楚。
        self.app_name_label.setStyleSheet(
            f"color: {TEXT}; font-family: {nav_font_family()}; font-size: 18px; font-weight: normal;")
        brand_layout.addWidget(self.app_name_label)
        brand_layout.addStretch()
        side_layout.addWidget(brand)

        # 分隔
        sep = QWidget()
        sep.setFixedHeight(14)
        side_layout.addWidget(sep)

        # 导航按钮容器
        self.nav_layout = QVBoxLayout()
        self.nav_layout.setSpacing(4)
        side_layout.addLayout(self.nav_layout)
        side_layout.addStretch()

        # 可滑动的选中指示条：替代按钮上静态的 border-left 竖条。
        # 外观（3px 淡紫竖线）完全一致，仅位置切换时带缓动滑动。
        self._indicator = QFrame(self.side)
        self._indicator.setFixedWidth(3)
        self._indicator.setFixedHeight(52)
        self._indicator.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._indicator.setStyleSheet(
            f"background-color: {ACCENT}; border-radius: 2px; border: none;")
        self._indicator.hide()

        # 底部小字
        self.footer = QLabel(f"{VERSION_LABEL} · GPL")
        self.footer.setStyleSheet("color: #6e6e6e; font-size: 11px; padding-left: 8px;")
        side_layout.addWidget(self.footer)

        root.addWidget(self.side)

        # 侧栏右缘的可拖拽分隔条（调整侧栏宽度）
        self._resize_handle = SideResizeHandle(self, self)
        root.addWidget(self._resize_handle)

        # ---------- 右侧内容 ----------
        # 背景层容器：动态背景 widget 在下，#appStack 页面在上。
        # 背景色交给全局 QSS 的 #appStack/#appCentral 控制。
        root.addWidget(self._backdrop_host, 1)

    # ------------------------------------------------------------------ 背景层

    def set_backdrop_widget(self, widget: Optional[QWidget]) -> None:
        """替换内容区底层背景 widget；None 恢复默认的透明占位。

        动态背景（如流光渐变）由此挂载。无论传入什么 widget，都不会影响
        上层 _stack 中的任何页面或控件。旧 widget 会被安全释放。
        """
        target = widget if widget is not None else self._placeholder
        if target is self._active_backdrop:
            return
        old = self._active_backdrop
        self._content_layer.removeWidget(old)
        old.setParent(None)
        if old is not self._placeholder:
            old.deleteLater()
        self._content_layer.insertWidget(0, target)
        self._content_layer.setCurrentWidget(self._stack)
        self._active_backdrop = target

    def backdrop_widget(self) -> Optional[QWidget]:
        """返回当前动态背景 widget（占位返回 None）。"""
        w = self._active_backdrop
        return None if w is self._placeholder else w

    # ------------------------------------------------------------------ QTabWidget 兼容接口

    def addTab(self, widget: QWidget, label: str) -> int:
        """添加一个页面。label 形如 '🔍  搜索'，需含图标与文字。"""
        parts = label.split()
        icon = parts[0] if parts else "•"
        text = " ".join(parts[1:]) if len(parts) > 1 else label

        btn = NavButton(icon, text)
        # 主题色可能已在初始化时设置过，新加入的导航项要继承当前色
        btn._accent = getattr(self, "_accent_color", ACCENT)
        index = len(self._buttons)
        # 按导航顺序循环分配国徽图标（原神七国 + 挪德卡莱）
        from .resource_path import resource_path
        nation = NATION_ICONS[index % len(NATION_ICONS)]
        icon_file = resource_path("assets", "nation_icons", f"{nation}.png")
        if os.path.isfile(icon_file):
            btn.set_nation_icon(icon_file)
        def _on_click(_checked=False, i=index):
            # clicked 信号会带 checked(bool)，用默认参数占位，避免覆盖 i
            self.setCurrentIndex(i)
            # 点击分组头：切到该页，并切换子项的展开/折叠
            b = self._buttons[i]
            if b.has_group():
                b.toggle_group()
                QTimer.singleShot(0, self._relayout_indicator)
        btn.clicked.connect(_on_click)
        self._buttons.append(btn)
        self.nav_layout.addWidget(btn)
        self._stack.addWidget(widget)
        if index == 0:
            btn.set_active(True)
        return index

    # ------------------------------------------------------------------ 分层导航
    def arrange(self, spec) -> None:
        """按分层规格重组导航布局（不改 stack/按钮索引映射）。

        spec 为有序列表，元素格式：
          ("item", idx)                    顶层普通项
          ("group", idx, [child_idx...])   分组头（复用已有页面按钮，可点击切页）+ 子项
          ("section", text, [child_idx...]) 纯标题分组（不可切页，仅折叠）+ 子项
        """
        self._arrange_spec = list(spec)
        self._section_headers = []

        # 1) 清空现有布局（保留按钮引用）
        while self.nav_layout.count():
            it = self.nav_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)

        # 2) 先重置所有按钮为顶层、展开
        for btn in self._buttons:
            btn.set_indent(0)
            if hasattr(btn, "init_group"):
                btn.init_group()
            btn.show()

        child_indices = set()
        for entry in spec:
            kind = entry[0]
            if kind == "item":
                self.nav_layout.addWidget(self._buttons[entry[1]])
            elif kind == "group":
                head_idx, kids = entry[1], entry[2]
                head = self._buttons[head_idx]
                head.init_group()
                self.nav_layout.addWidget(head)
                for ci in kids:
                    if 0 <= ci < len(self._buttons):
                        child = self._buttons[ci]
                        child.set_indent(18)
                        head.add_group_child(child)
                        self.nav_layout.addWidget(child)
                        child_indices.add(ci)
            elif kind == "section":
                text, kids = entry[1], entry[2]
                icon_path = entry[3] if len(entry) > 3 else ""
                header = NavGroupHeader(text, icon_path, self.side)
                header._after_toggle = self._relayout_indicator
                header.setFixedWidth(self.side.width() - 24)
                header.set_accent(getattr(self, "_accent_color", ACCENT))
                header.refresh_theme(getattr(self, "_dark_theme", True))
                if hasattr(self, "_nav_text_color"):
                    header.set_text_override(self._nav_text_color)
                self._section_headers.append((header, text))
                self.nav_layout.addWidget(header)
                for ci in kids:
                    if 0 <= ci < len(self._buttons):
                        child = self._buttons[ci]
                        child.set_indent(18)
                        header.add_group_child(child)
                        self.nav_layout.addWidget(child)
                        child_indices.add(ci)

        # 3) 布局重算后重新定位指示条；若当前选中项被折叠则隐藏
        QTimer.singleShot(0, self._relayout_indicator)

    def _relayout_indicator(self) -> None:
        idx = self.currentIndex()
        btn = self._buttons[idx] if 0 <= idx < len(self._buttons) else None
        if btn is None:
            return
        # 若按钮处于折叠的分组内（不可见），隐藏指示条
        if not btn.isVisible():
            self._indicator.hide()
            return
        self._place_indicator(idx, animate=False)

    def section_headers(self):
        """返回所有纯标题分组的 (header, text) 列表（供语言切换更新文案）。"""
        return getattr(self, "_section_headers", [])

    def _place_indicator(self, index: int, animate: bool = True) -> None:
        """把选中指示条移动到目标导航项左侧。首次显示时直接就位。"""
        if not (0 <= index < len(self._buttons)):
            return
        btn = self._buttons[index]
        # btn 位于 side 的子布局中；换算到 side 坐标系
        y = btn.geometry().y() + (btn.geometry().height() - self._indicator.height()) // 2
        x = btn.geometry().x()
        if not self._indicator.isVisible():
            self._indicator.move(x, y)
            self._indicator.show()
            self._indicator.raise_()
        elif animate:
            # x 一般固定不变，仅纵向滑动
            self._indicator.move(x, self._indicator.y())
            motion.slide_indicator(self._indicator, y)
        else:
            self._indicator.move(x, y)

    def setCurrentIndex(self, index: int) -> None:
        if 0 <= index < len(self._buttons):
            prev = self._stack.currentIndex()
            old_widget = self._stack.widget(prev) if prev >= 0 else None
            new_widget = self._stack.widget(index)
            self._stack.setCurrentIndex(index)
            for i, btn in enumerate(self._buttons):
                btn.set_active(i == index)
            self._place_indicator(index, animate=(prev != index))
            # 页面切换淡入动效（动效关闭时 fade_in_page 为 no-op）
            if prev != index:
                motion.clear_page_effect(old_widget)
                motion.fade_in_page(new_widget)
            self.currentChanged.emit(index)

    def _place_indicator_idle(self) -> None:
        """首帧空闲时定位指示条（bound method，作为 QTimer context 安全）。"""
        self._place_indicator(self.currentIndex(), animate=False)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 首帧布局完成后再定位指示条（构造期 geometry 尚未计算）
        # 传 self 作 context：窗口若在回调前销毁，定时器自动失效，不会访问已删除对象
        QTimer.singleShot(0, self, self._place_indicator_idle)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._indicator is not None and self._indicator.isVisible():
            self._place_indicator(self.currentIndex(), animate=False)

    def setCurrentWidget(self, widget: QWidget) -> None:
        index = self._stack.indexOf(widget)
        if index >= 0:
            self.setCurrentIndex(index)

    def currentIndex(self) -> int:
        return self._stack.currentIndex()

    def currentWidget(self) -> Optional[QWidget]:
        return self._stack.currentWidget()

    def count(self) -> int:
        return self._stack.count()

    def tabText(self, index: int) -> str:
        if 0 <= index < len(self._buttons):
            b = self._buttons[index]
            return f"{b.icon_text}  {b.nav_text}"
        return ""

    def setTabText(self, index: int, label: str) -> None:
        """更新某个导航项的文字（label 形如 '🔍  Search'，含图标）。"""
        if 0 <= index < len(self._buttons):
            parts = label.split()
            icon = parts[0] if parts else "•"
            text = " ".join(parts[1:]) if len(parts) > 1 else label
            self._buttons[index].icon_text = icon
            self._buttons[index].set_nav_text(text)

    def set_accent(self, color: str) -> None:
        """更新导航指示条与全部导航项文字颜色（主题色切换时调用）。"""
        if hasattr(self, "_indicator"):
            self._indicator.setStyleSheet(
                f"background-color: {color}; border-radius: 2px; border: none;")
        self._accent_color = color
        for btn in self._buttons:
            btn.set_accent(color)
        self._sync_section_headers()
        if hasattr(self, "_resize_handle"):
            self._resize_handle._apply_color()

    def set_nav_colors(self, text: Optional[str] = None,
                       icon: Optional[str] = None,
                       bg: Optional[str] = None) -> None:
        """设置导航栏分部件配色（文字/图标/背景），None 表示跟随主题色。

        左侧选中指示条跟随：图标色 > 文字色 > 当前主题色。
        """
        self._nav_text_color = text or None
        self._nav_icon_color = icon or None
        self._nav_bg_color = bg or None
        for btn in self._buttons:
            btn.set_nav_colors(self._nav_text_color, self._nav_icon_color, self._nav_bg_color)
        if hasattr(self, "_indicator"):
            indicator_c = (self._nav_icon_color or self._nav_text_color
                           or getattr(self, "_accent_color", ACCENT))
            self._indicator.setStyleSheet(
                f"background-color: {indicator_c}; border-radius: 2px; border: none;")
        for header, _ in getattr(self, "_section_headers", []):
            header.set_text_override(self._nav_text_color)

    def set_app_name(self, name: str) -> None:
        if hasattr(self, "app_name_label"):
            self.app_name_label.setText(name)

    def widget(self, index: int) -> Optional[QWidget]:
        return self._stack.widget(index)

    def _sync_section_headers(self) -> None:
        """把当前主题色/深浅主题/导航文字色同步到所有纯标题分组。"""
        for header, _ in getattr(self, "_section_headers", []):
            header.set_accent(getattr(self, "_accent_color", ACCENT))
            header.refresh_theme(getattr(self, "_dark_theme", True))

    def refresh_theme(self, dark: bool) -> None:
        self._dark_theme = dark
        for btn in self._buttons:
            btn.refresh_theme(dark)
        self._sync_section_headers()
        if hasattr(self, "_indicator"):
            accent = getattr(self, "_accent_color", ACCENT if dark else "#7E6AA6")
            self._indicator.setStyleSheet(
                f"background-color: {accent}; border-radius: 2px; border: none;")
        if dark:
            self.side.setStyleSheet(f"QFrame {{ background-color: {BG_TOP}; border-right: 1px solid {BORDER}; }}")
            self.app_name_label.setStyleSheet(
                f"color: {TEXT}; font-family: {nav_font_family()}; font-size: 18px; font-weight: normal;")
            self.footer.setStyleSheet("color: #6e6e6e; font-size: 11px; padding-left: 8px;")
        else:
            self.side.setStyleSheet("QFrame { background-color: #fff; border-right: 1px solid #e0e0e0; }")
            self.app_name_label.setStyleSheet(
                f"color: #2a2a2a; font-family: {nav_font_family()}; font-size: 18px; font-weight: normal;")
            self.footer.setStyleSheet("color: #999; font-size: 11px; padding-left: 8px;")
        # 拖拽手柄颜色随主题刷新
        if hasattr(self, "_resize_handle"):
            self._resize_handle._apply_color()
