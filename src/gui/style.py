# -*- coding: utf-8 -*-
"""
界面主题样式：复刻参考项目（番茄小说下载器）的深色视觉风格。

设计语言
========
- 背景：近黑 #181818 / 深灰 #1e1e1e / 卡片 #242424
- 强调色：淡紫 #B39DDB（登录按钮、选中项）
- 导航：顶部一行带图标的标签，选中项淡紫色高亮
- 控件：圆角 8px，边框 #343434，输入框 #2a2a2a
- 字体：系统无衬线，正文 13px，标题 16-20px
- 圆角卡片 + 细边框 + 轻分割线，整体克制、低饱和
"""

# ---- 配色常量 ----
BG = "#181818"           # 主背景（近黑）
BG_TOP = "#1e1e1e"       # 顶栏背景
BG_CARD = "#242424"      # 卡片背景
BG_INPUT = "#2a2a2a"     # 输入框背景
BG_HOVER = "#2f2f2f"     # 悬停背景
BORDER = "#343434"       # 边框
BORDER_LIGHT = "#2c2c2c" # 轻边框
TEXT = "#e6e6e6"         # 主文字
TEXT_DIM = "#9a9a9a"     # 次文字
TEXT_FAINT = "#6e6e6e"   # 弱化文字
ACCENT = "#B39DDB"       # 强调紫
ACCENT_HOVER = "#C6B3E8" # 强调紫悬停
ACCENT_DARK = "#7E6AA6"  # 强调紫深
SUCCESS = "#81c784"      # 成功绿

# 可切换的强调色调色板：(主色, 悬停色, 深色, 同色系渐变)。key 用于持久化。
# 渐变值用 nation_theme 的 ``linear:角度|颜色1,颜色2`` 格式；主色仍是纯色，
# 供图标染色 / 内联色 / 对比计算使用（保持 accent_color() 纯色 API 不变）。
ACCENT_PALETTES = {
    "purple": (ACCENT, ACCENT_HOVER, ACCENT_DARK, "linear:135|#B39DDB,#7E6AA6"),
    "cyan":   ("#26C6DA", "#4DD0E1", "#00838F", "linear:135|#4DD0E1,#00838F"),
    "blue":   ("#64B5F6", "#90CAF9", "#1565C0", "linear:135|#90CAF9,#1565C0"),
    "green":  ("#81C784", "#A5D6A7", "#2E7D32", "linear:135|#A5D6A7,#2E7D32"),
    "orange": ("#FFB74D", "#FFCC80", "#EF6C00", "linear:135|#FFCC80,#EF6C00"),
}
ACCENT_LABELS = {
    "purple": "紫", "cyan": "青", "blue": "蓝", "green": "绿", "orange": "橙",
}


def modern_scrollbar_qss(dark: bool = True) -> str:
    """生成现代风格滚动条 QSS：细轨、圆角滑块、隐藏箭头、悬停主题色。

    统一供深色 / 浅色主题复用，避免两套 QSS 各写一份而样式漂移。
    外部对话框（如音色选择）也可在自己样式里拼接本函数结果。
    """
    if dark:
        handle = "rgba(255,255,255,0.16)"
        handle_hover = "rgba(255,255,255,0.30)"
        handle_press = ACCENT_DARK
        track = "rgba(255,255,255,0.03)"
    else:
        handle = "rgba(0,0,0,0.18)"
        handle_hover = "rgba(0,0,0,0.32)"
        handle_press = "#7E6AA6"
        track = "rgba(0,0,0,0.03)"
    return f"""
/* ============ 滚动条（现代细条） ============ */
QScrollBar:vertical {{
    background: {track};
    width: 9px;
    margin: 0px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical {{
    background: {handle};
    border-radius: 4px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {handle_hover}; }}
QScrollBar::handle:vertical:pressed {{ background: {handle_press}; }}
QScrollBar:horizontal {{
    background: {track};
    height: 9px;
    margin: 0px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal {{
    background: {handle};
    border-radius: 4px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {handle_hover}; }}
QScrollBar::handle:horizontal:pressed {{ background: {handle_press}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px; height: 0px;
    border: none; background: transparent;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}
"""


def accent_gradient(key: str) -> str:
    """返回指定调色板的同色系渐变值（linear:...）；无渐变时返回空串。"""
    palette = ACCENT_PALETTES.get(key) or ACCENT_PALETTES["purple"]
    return palette[3] if len(palette) > 3 else ""


def _accent_qss_gradient(key: str) -> str:
    """把主题色渐变转成 Qt QSS 的 qlineargradient(...) 值；失败返回空串。"""
    value = accent_gradient(key)
    if not value:
        return ""
    try:
        from . import nation_theme as nt
        return nt.qss_gradient(value)
    except Exception:  # noqa: BLE001
        return ""


def recolor_accent(qss: str, key: str) -> str:
    """把 QSS 中的默认强调紫替换为指定调色板颜色。

    按钮/选中态/进度条等「背景」区域优先用同色系渐变（qlineargradient），
    文字色/边框色/图标色仍用纯色，保证对比清晰。
    """
    palette = ACCENT_PALETTES.get(key)
    if palette is None:
        return qss
    a, h, d = palette[:3]
    qss = qss.replace(ACCENT_HOVER, h).replace(ACCENT_DARK, d)
    grad = _accent_qss_gradient(key)
    if grad:
        # 主色作为「背景」出现时换成渐变；作为文字/边框出现时保持纯色。
        # QSS 里只有 background-color:{ACCENT} 才可安全换成 background:{grad}。
        qss = qss.replace(f"background-color: {ACCENT}", f"background: {grad}")
    else:
        qss = qss.replace(ACCENT, a)
    # 兜底：把剩余的纯色主色替换掉（文字色、边框色等）
    qss = qss.replace(ACCENT, a)
    return qss


def accent_color(key: str) -> str:
    """返回指定调色板的主色（纯色代表色）。"""
    return ACCENT_PALETTES.get(key, ACCENT_PALETTES["purple"])[0]


# ---- 分部件自定义配色 ----
# 每个值为 "#RRGGBB" 或空串；空串表示跟随当前主题色。
# 导航栏三色独立于全局强调色，可分别设置文字/图标/背景。
COLOR_NAV_TEXT = "nav_text_color"
COLOR_NAV_ICON = "nav_icon_color"
COLOR_NAV_BG = "nav_bg_color"
COLOR_APP_TEXT = "app_text_color"
COLOR_APP_BG = "app_bg_color"
CUSTOM_COLOR_KEYS = (
    COLOR_NAV_TEXT, COLOR_NAV_ICON, COLOR_NAV_BG,
    COLOR_APP_TEXT, COLOR_APP_BG,
)


def is_valid_color(value: str) -> bool:
    """判断字符串是否是 Qt 可识别的颜色（空串/None 视为无效）。"""
    if not value:
        return False
    from PySide6.QtGui import QColor
    return QColor(str(value)).isValid()


def apply_custom_colors(qss: str, dark: bool, app_text: str = "", app_bg: str = "") -> str:
    """把全局主文字色 / 内容区底色替换为用户自定义色。

    「界面背景」自定义色的语义是**只改内容区**，因此这里只精确替换两个
    内容区锚点选择器（QWidget#appCentral / QStackedWidget#appStack）里的底色。

    绝不能再用全局字符串替换 #181818：那会连带把 QMainWindow / QTabWidget::pane
    等基底也染色。标题栏 (TitleBar) 自身透明，透出的正是 QMainWindow 底色，
    一旦被染色就会在顶部「登录」那一条露出意外颜色（历史 bug，已修复）。

    卡片、输入框、边框、导航栏等保持各自层次，不受影响。传入空串表示跟随主题。
    注意：开了背景插件时，插件注入的内容区背景会覆盖这里的自定义底色，属既有层级关系。
    """
    import re
    from PySide6.QtGui import QColor

    def norm(value: str) -> str:
        c = QColor(str(value))
        return c.name() if c.isValid() else ""

    text = norm(app_text)
    bg = norm(app_bg)
    if text:
        # 深色主文字 #e6e6e6 / 浅色主文字 #2a2a2a
        qss = qss.replace("#e6e6e6" if dark else "#2a2a2a", text)
    if bg:
        base_color = re.escape("#181818" if dark else "#f7f7f8")
        # 只命中内容区锚点选择器块，避免误伤 QMainWindow / QTabWidget::pane
        for sel in ("QWidget#appCentral", "QStackedWidget#appStack"):
            pattern = re.compile(
                r"(" + re.escape(sel) + r"\s*\{\s*background-color:\s*)" + base_color
            )
            qss = pattern.sub(lambda m: m.group(1) + bg, qss)
    return qss


ERROR = "#ef5350"        # 错误红
WARN = "#ffb74d"         # 警告橙

QSS = f"""
/* ============ 全局 ============ */
QWidget {{
    color: {TEXT};
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "HarmonyOS Sans SC", sans-serif;
    font-size: 13px;
}}
/* 裸容器背景透明，透出主窗口背景；实体控件在下方各自声明背景色，不受影响。
   背景插件可仅替换本条 background-image 即可整体换背景，不动任何控件。 */
QMainWindow {{ background-color: {BG}; }}
QDialog {{ background-color: {BG_CARD}; }}
/* 内容区锚点：默认纯色，背景插件会覆盖这两条注入渐变/纹理 */
QWidget#appCentral {{ background-color: {BG}; }}
QStackedWidget#appStack {{ background-color: {BG}; }}
/* 滚动区视口与其内容容器透明，避免回退到系统调色板的白色，透出内容区背景。
   实体控件（Card/按钮/输入框等）都在更深层子树，不命中这两级选择器。 */
QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{ background-color: transparent; }}
QToolTip {{
    background-color: {BG_CARD};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 8px;
}}

/* ============ 菜单栏（保留兼容） ============ */
QMenuBar {{
    background-color: {BG_TOP};
    color: {TEXT_DIM};
    border-bottom: 1px solid {BORDER_LIGHT};
    padding: 4px 8px;
}}
QMenuBar::item {{ background: transparent; padding: 5px 12px; border-radius: 6px; }}
QMenuBar::item:selected {{ background-color: {BG_HOVER}; color: {TEXT}; }}
QMenu {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{ padding: 7px 24px 7px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background-color: {ACCENT_DARK}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}

/* ============ 工具栏 ============ */
QToolBar {{
    background-color: {BG_TOP};
    border-bottom: 1px solid {BORDER_LIGHT};
    padding: 8px 10px;
    spacing: 8px;
}}
QToolBar QLabel {{ color: {TEXT_DIM}; background: transparent; }}

/* ============ 顶部导航标签 ============ */
QTabWidget::pane {{
    border: none;
    background-color: {BG};
    margin-top: 6px;
}}
QTabWidget::tab-bar {{ alignment: left; left: 12px; }}
QTabBar::tab {{
    background-color: transparent;
    color: {TEXT_DIM};
    padding: 11px 20px;
    margin-right: 4px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 14px;
    min-width: 70px;
}}
QTabBar::tab:hover {{ color: {TEXT}; background-color: {BG_HOVER}; border-radius: 8px; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; font-weight: bold; }}

/* ============ 输入框 ============ */
QLineEdit {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 12px;
    color: {TEXT};
    selection-background-color: {ACCENT_DARK};
}}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit::placeholder {{ color: {TEXT_FAINT}; }}

/* ============ 按钮 ============ */
QPushButton {{
    background-color: {BG_INPUT};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 16px;
}}
QPushButton:hover {{ background-color: {BG_HOVER}; border-color: {ACCENT_DARK}; }}
QPushButton:pressed {{ background-color: {BG_CARD}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER_LIGHT}; }}
QPushButton[accent="true"] {{
    background-color: {ACCENT};
    color: #1a1a1a;
    border: none;
    font-weight: bold;
}}
QPushButton[accent="true"]:hover {{ background-color: {ACCENT_HOVER}; }}
/* 搜索页顶部工具标签 */
QPushButton#searchToolTab {{
    background-color: {BG_INPUT};
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 14px;
    font-weight: bold;
}}
QPushButton#searchToolTab:hover {{ border-color: {ACCENT_DARK}; color: {TEXT}; }}
QPushButton#searchToolTab:checked {{
    background-color: {ACCENT};
    color: #1a1a1a;
    border: none;
}}

/* ============ 下拉框 / 数字框 ============ */
QComboBox {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 12px;
    color: {TEXT};
}}
QComboBox:hover {{ border-color: {ACCENT_DARK}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {TEXT_DIM};
    margin-right: 10px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    selection-background-color: {ACCENT_DARK};
    padding: 4px;
}}
QSpinBox {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    color: {TEXT};
}}

/* ============ 表格 ============ */
QTableWidget {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER_LIGHT};
    border-radius: 8px;
    gridline-color: {BORDER_LIGHT};
    selection-background-color: {ACCENT_DARK};
    selection-color: {TEXT};
    padding: 0px;
}}
QTableWidget::item {{ padding: 8px 6px; border-bottom: 1px solid {BORDER_LIGHT}; }}
QTableWidget::item:selected {{ background-color: {ACCENT_DARK}; }}
QHeaderView {{ background-color: {BG_TOP}; border: none; }}
QHeaderView::section {{
    background-color: {BG_TOP};
    color: {TEXT_DIM};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 9px 8px;
    font-weight: bold;
}}
QTableCornerButton::section {{ background-color: {BG_TOP}; border: none; }}

/* ============ 文本区 ============ */
QTextEdit {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER_LIGHT};
    border-radius: 8px;
    padding: 14px;
    color: {TEXT};
    selection-background-color: {ACCENT_DARK};
}}
QTextEdit:focus {{ border: 1px solid {ACCENT_DARK}; }}

/* ============ 进度条 ============ */
QProgressBar {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    height: 10px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 7px; }}

/* ============ 标签 ============ */
QLabel {{ color: {TEXT}; background: transparent; }}

{modern_scrollbar_qss(True)}

/* ============ 状态栏 ============ */
QStatusBar {{
    background-color: {BG_TOP};
    border-top: 1px solid {BORDER_LIGHT};
    color: {TEXT_DIM};
    padding: 4px 10px;
}}

/* ============ 分组框 ============ */
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 12px;
    padding: 12px;
}}
QGroupBox::title {{ color: {ACCENT}; padding: 0 6px; }}

/* ============ 书架分组卡片 ============ */
QFrame#shelfGroupCard {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QLabel#shelfGroupTitle {{
    color: {ACCENT};
    font-weight: bold;
    font-size: 13px;
}}
QPushButton#shelfDanger {{ color: #e08888; border-color: #6a4040; }}
QPushButton#shelfDanger:hover {{ background-color: #4a3030; border-color: #9a5050; }}
/* 任务队列：危险操作按钮 */
QPushButton#queueDanger {{ color: #e08888; border-color: #7a4242; }}
QPushButton#queueDanger:hover {{ background-color: #4a3030; border-color: #b05a5a; }}
QFrame#queueConcurrentCard {{ background: #2a2733; border: 1px solid #4a4458; border-radius: 8px; }}
/* 历史记录页 */
QPushButton#histDanger {{ color: #e08888; border-color: #7a4242; }}
QPushButton#histDanger:hover {{ background-color: #4a3030; border-color: #b05a5a; }}
QLabel#histStatPill {{ background: #2f2c38; border: 1px solid #4a4458; border-radius: 10px; padding: 2px 9px; color: #bbb; font-size: 11px; }}

/* ============ 任务队列 / 历史：B2 卡片流 ============ */
QScrollArea#queueCardArea, QScrollArea#histCardArea {{ background: transparent; border: none; }}
QWidget#queueCardList, QWidget#histCardList {{ background: transparent; }}
QFrame#queueTaskCard, QFrame#histTaskCard {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#queueTaskCard:hover, QFrame#histTaskCard:hover {{ border-color: {ACCENT_DARK}; }}
QFrame#queueTaskCard[selected="true"], QFrame#histTaskCard[selected="true"] {{
    border-color: {ACCENT};
    background-color: #2b2833;
}}
QLabel#queueCover, QLabel#histCover {{
    background: qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:1,
        stop:0 #5a4f72, stop:1 #37304a);
    color: #e6dcff;
    border-radius: 5px;
    font-size: 17px;
    font-weight: bold;
}}
QLabel#queueCardTitle, QLabel#histCardTitle {{ font-size: 13.5px; font-weight: bold; color: {TEXT}; }}
QLabel#queueMetaText, QLabel#histMetaText {{ color: {TEXT_DIM}; font-size: 11px; }}
QLabel#queuePct {{ color: {TEXT_DIM}; font-size: 11px; }}
QFrame#queueCardLine, QFrame#histCardLine {{ background-color: {BORDER_LIGHT}; }}
QLabel#queueStatusText, QLabel#histStatusText {{ font-size: 11px; }}
QProgressBar {{ min-height: 8px; }}

/* 分卷导出：可收缩逐卷进度区（仅文库8分卷任务显示） */
QPushButton#queueVolToggle {{
    background: transparent; border: none; border-top: 1px dashed #3f3a4a;
    color: {ACCENT}; font-size: 11px; padding: 6px 2px 2px 2px; text-align: center;
}}
QPushButton#queueVolToggle:hover {{ color: {ACCENT_HOVER}; }}
QWidget#queueVolArea {{ background: transparent; }}
QLabel#queueVolSummary {{ color: {TEXT_DIM}; font-size: 11px; padding: 2px 0; }}
QLabel#queueVolIdx {{
    border-radius: 5px; background: rgba(179,157,219,.14); color: {ACCENT};
    font-size: 11px; font-weight: bold;
}}
QLabel#queueVolIdx[volState="done"] {{ background: rgba(129,199,132,.16); color: {SUCCESS}; }}
QLabel#queueVolIdx[volState="failed"] {{ background: rgba(200,90,90,.18); color: #e07a7a; }}
QLabel#queueVolName {{ color: #d5d5d5; font-size: 11px; }}
QLabel#queueVolPct {{ color: {ACCENT}; font-size: 11px; font-weight: bold; }}
QLabel#queueVolState {{ color: {TEXT_DIM}; font-size: 10px; }}
QLabel#queueVolPct[volState="done"], QLabel#queueVolState[volState="done"] {{ color: {SUCCESS}; }}
QLabel#queueVolPct[volState="failed"], QLabel#queueVolState[volState="failed"] {{ color: #e07a7a; }}
QLabel#queueVolIdx[volState="waiting"], QLabel#queueVolPct[volState="waiting"],
QLabel#queueVolState[volState="waiting"] {{ color: #777; }}

/* ============ 消息框 ============ */
QMessageBox {{
    background-color: {BG_CARD};
}}
QMessageBox QLabel {{ color: {TEXT}; }}
QMessageBox QPushButton {{ min-width: 80px; }}
"""


# ---- 浅色主题（由主题切换按钮触发）----
LIGHT_QSS = f"""
QWidget {{
    color: #2a2a2a;
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    font-size: 13px;
}}
QMainWindow {{ background-color: #f7f7f8; }}
QDialog {{ background-color: #ffffff; }}
QWidget#appCentral {{ background-color: #f7f7f8; }}
QStackedWidget#appStack {{ background-color: #f7f7f8; }}
/* 滚动区视口与其内容容器透明，避免回退到系统调色板的白色，透出内容区背景。
   实体控件（Card/按钮/输入框等）都在更深层子树，不命中这两级选择器。 */
QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{ background-color: transparent; }}
QMenuBar {{ background-color: #ffffff; color: #555; border-bottom: 1px solid #e0e0e0; padding: 4px 8px; }}
QMenuBar::item {{ padding: 5px 12px; border-radius: 6px; }}
QMenuBar::item:selected {{ background: #eee; }}
QMenu {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 6px; }}
QMenu::item:selected {{ background: {ACCENT}; }}
QToolBar {{ background: #fff; border-bottom: 1px solid #e0e0e0; padding: 8px 10px; spacing: 8px; }}
QTabWidget::pane {{ border: none; background: #f7f7f8; margin-top: 6px; }}
QTabWidget::tab-bar {{ alignment: left; left: 12px; }}
QTabBar::tab {{ background: transparent; color: #666; padding: 11px 20px; margin-right: 4px;
                 border: none; border-bottom: 2px solid transparent; font-size: 14px; min-width: 70px; }}
QTabBar::tab:selected {{ color: #7E6AA6; border-bottom: 2px solid #7E6AA6; font-weight: bold; }}
QTabBar::tab:hover {{ background: #eee; border-radius: 8px; }}
QLineEdit {{ background: #fff; border: 1px solid #d5d5d5; border-radius: 8px; padding: 7px 12px; }}
QLineEdit:focus {{ border: 1px solid #7E6AA6; }}
QPushButton {{ background: #fff; color: #333; border: 1px solid #d5d5d5; border-radius: 8px; padding: 7px 16px; }}
QPushButton:hover {{ background: #f0f0f0; }}
QPushButton[accent="true"] {{ background: #7E6AA6; color: #fff; border: none; font-weight: bold; }}
QPushButton[accent="true"]:hover {{ background: #6d5a94; }}
QPushButton#searchToolTab {{ background: #fff; color: #888; border: 1px solid #d5d5d5; border-radius: 8px; padding: 6px 14px; font-weight: bold; }}
QPushButton#searchToolTab:hover {{ border-color: #7E6AA6; color: #555; }}
QPushButton#searchToolTab:checked {{ background: #7E6AA6; color: #fff; border: none; }}
QComboBox, QSpinBox {{ background: #fff; border: 1px solid #d5d5d5; border-radius: 8px; padding: 6px 12px; }}
QTableWidget {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 8px;
                gridline-color: #eee; selection-background-color: #d9c9f0; }}
QHeaderView {{ background: #fafafa; border: none; }}
QHeaderView::section {{ background: #fafafa; color: #555; border: none;
                       border-bottom: 1px solid #ddd; padding: 9px 8px; }}
QTextEdit {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 14px; }}
QProgressBar {{ background: #e8e8e8; border: 1px solid #ddd; border-radius: 8px; height: 10px; }}
QProgressBar::chunk {{ background: #7E6AA6; border-radius: 7px; }}
QLabel {{ color: #2a2a2a; background: transparent; }}
QStatusBar {{ background: #fff; border-top: 1px solid #e0e0e0; color: #666; padding: 4px 10px; }}
QFrame {{ background: #fff; border: 1px solid #e5e5e5; border-radius: 8px; }}
QFrame#shelfGroupCard {{ background: #fafafa; border: 1px solid #e0e0e0; border-radius: 10px; }}
QLabel#shelfGroupTitle {{ color: #7E6AA6; font-weight: bold; font-size: 13px; }}
QPushButton#shelfDanger {{ color: #c0392b; border-color: #e0b0b0; }}
QPushButton#queueDanger {{ color: #c0392b; border-color: #e0b0b0; }}
QPushButton#queueDanger:hover {{ background: #fdecea; border-color: #c0392b; }}
QFrame#queueConcurrentCard {{ background: #fafafa; border: 1px solid #e0e0e0; }}
QPushButton#histDanger {{ color: #c0392b; border-color: #e0b0b0; }}
QPushButton#histDanger:hover {{ background: #fdecea; border-color: #c0392b; }}
QLabel#histStatPill {{ background: #fafafa; border: 1px solid #e0e0e0; border-radius: 10px; padding: 2px 9px; color: #555; font-size: 11px; }}
/* ============ 任务队列 / 历史：B2 卡片流（浅色） ============ */
QScrollArea#queueCardArea, QScrollArea#histCardArea {{ background: transparent; border: none; }}
QWidget#queueCardList, QWidget#histCardList {{ background: transparent; }}
QFrame#queueTaskCard, QFrame#histTaskCard {{
    background-color: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 10px;
}}
QFrame#queueTaskCard:hover, QFrame#histTaskCard:hover {{ border-color: #b9a3d8; }}
QFrame#queueTaskCard[selected="true"], QFrame#histTaskCard[selected="true"] {{
    border-color: #7E6AA6;
    background-color: #f3eefb;
}}
QLabel#queueCover, QLabel#histCover {{
    background: qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:1,
        stop:0 #b9a6d8, stop:1 #8f7cb0);
    color: #ffffff;
    border-radius: 5px;
    font-size: 17px;
    font-weight: bold;
}}
QLabel#queueCardTitle, QLabel#histCardTitle {{ font-size: 13.5px; font-weight: bold; color: #2a2a2a; }}
QLabel#queueMetaText, QLabel#histMetaText {{ color: #666; font-size: 11px; }}
QLabel#queuePct {{ color: #666; font-size: 11px; }}
QFrame#queueCardLine, QFrame#histCardLine {{ background-color: #ececec; }}
/* 分卷导出：可收缩逐卷进度区（浅色） */
QPushButton#queueVolToggle {{
    background: transparent; border: none; border-top: 1px dashed #e0e0e0;
    color: #7E6AA6; font-size: 11px; padding: 6px 2px 2px 2px; text-align: center;
}}
QPushButton#queueVolToggle:hover {{ color: #5f4d82; }}
QWidget#queueVolArea {{ background: transparent; }}
QLabel#queueVolSummary {{ color: #666; font-size: 11px; padding: 2px 0; }}
QLabel#queueVolIdx {{
    border-radius: 5px; background: #f0ebf8; color: #7E6AA6;
    font-size: 11px; font-weight: bold;
}}
QLabel#queueVolIdx[volState="done"] {{ background: #e8f5e9; color: #4e9c52; }}
QLabel#queueVolIdx[volState="failed"] {{ background: #fdecea; color: #c0392b; }}
QLabel#queueVolName {{ color: #444; font-size: 11px; }}
QLabel#queueVolPct {{ color: #7E6AA6; font-size: 11px; font-weight: bold; }}
QLabel#queueVolState {{ color: #888; font-size: 10px; }}
QLabel#queueVolPct[volState="done"], QLabel#queueVolState[volState="done"] {{ color: #4e9c52; }}
QLabel#queueVolPct[volState="failed"], QLabel#queueVolState[volState="failed"] {{ color: #c0392b; }}
QLabel#queueVolIdx[volState="waiting"], QLabel#queueVolPct[volState="waiting"],
QLabel#queueVolState[volState="waiting"] {{ color: #aaa; }}
{modern_scrollbar_qss(False)}
QMessageBox {{ background: #fff; }}
"""


# 玻璃质感的表面参数：全局 QSS 补丁与书卡内联样式共用同一份定义，
# 避免两处硬编码漂移（用户之前遇到过的"选色后错位"正源于样式多头维护）。
GLASS_SURFACE = {
    True: {
        "page": "rgba(36, 36, 36, 0.74)",       # BG_CARD 同色半透
        "accent": "rgba(42, 39, 51, 0.74)",     # queueConcurrentCard 同色半透
        "edge": "rgba(255, 255, 255, 0.10)",
        "selected": "rgba(43, 40, 51, 0.86)",
    },
    False: {
        "page": "rgba(255, 255, 255, 0.82)",
        "accent": "rgba(250, 250, 250, 0.86)",
        "edge": "rgba(60, 50, 40, 0.09)",
        "selected": "rgba(243, 238, 251, 0.90)",
    },
}
GLASS_RADIUS = 14


def glass_effect_enabled() -> bool:
    """玻璃质感开关是否开启（默认开）。

    独立从 datastore 读取，供卡片等无法直接拿到主窗口的组件使用；
    未注入 getter 时回退到默认开，保证开关缺失时行为与发布前一致。
    """
    from .datastore import DataStore
    try:
        return bool(DataStore().settings_get("ui_glass", True))
    except Exception:  # noqa: BLE001
        return True


def glass_effect_qss(dark: bool, enabled: bool = True) -> str:
    """玻璃质感补丁：只调整卡片容器的圆角、半透明背景与柔光边框。

    严格不改任何主题色相 / 强调色锚点 / 用户配色方案：
      - 仅把默认卡片底色换成「同色相的半透明版本」；
      - 边框换成低透明柔光边；圆角从 8-10px 统一放大到 14px。
    本函数作为补丁追加在基础 QSS 之后、用户自定义卡片色覆盖之前，
    因此用户若在配色中心设过卡片色，仍以用户色为准（配色方案优先）。
    QSS 不支持 backdrop-filter，半透明透出背景渐变即为可实现的最佳玻璃感。
    enabled=False 时返回空串（关闭开关，恢复基础 QSS 的实心卡片）。
    """
    if not enabled:
        return ""
    g = GLASS_SURFACE[bool(dark)]
    page, accent_card = g["page"], g["accent"]
    edge, sel = g["edge"], g["selected"]
    return f"""
/* ===== 玻璃质感层（仅质感，不改配色方案）===== */
QFrame#shelfGroupCard, QFrame#queueTaskCard, QFrame#histTaskCard {{
    background-color: {page};
    border: 1px solid {edge};
    border-radius: 14px;
}}
QFrame#queueConcurrentCard {{
    background-color: {accent_card};
    border: 1px solid {edge};
    border-radius: 14px;
}}
QFrame#queueTaskCard[selected="true"], QFrame#histTaskCard[selected="true"] {{
    background-color: {sel};
    border-radius: 14px;
}}
QGroupBox {{ border-radius: 12px; }}
"""


def accent_button(button) -> None:
    """把按钮标记为强调色（淡紫主按钮）。"""
    button.setProperty("accent", "true")
    button.style().unpolish(button)
    button.style().polish(button)


# ---- 左侧导航栏字体（可在设置页切换）----
# 仅用于侧边栏文字，不影响正文/标题/按钮等其他区域。
# 按字体 key 分别缓存族名，切换字体后无需重启。
_NAV_FONT_CACHE: dict[str, str] = {}


def clear_nav_font_cache() -> None:
    """切换导航字体后调用，令下次 nav_font_family() 重新解析。"""
    _NAV_FONT_CACHE.clear()


def nav_font_family() -> str:
    """返回侧边栏文字使用的 CSS font-family 值（跟随部件配色的字体选择）。

    字体文件随软件分发（OFL 授权），源码运行与 PyInstaller 打包均可用。
    用 addApplicationFont 返回的 id 精确取族名，避免不同字体族名猜错；
    字体缺失时回退到楷体。
    """
    from . import nation_theme
    key = nation_theme.nav_font_key()
    if key in _NAV_FONT_CACHE:
        return _NAV_FONT_CACHE[key]

    import os
    from PySide6.QtGui import QFontDatabase
    from .resource_path import resource_path

    # 找到该 key 对应的字体文件名
    rel_name = "MaShanZheng-Regular.ttf"
    for k, fname, _label in nation_theme.NAV_FONTS:
        if k == key:
            rel_name = fname
            break
    font_file = resource_path("assets", "fonts", rel_name)
    family = ""
    if os.path.isfile(font_file):
        font_id = QFontDatabase.addApplicationFont(font_file)
        if font_id >= 0:
            fams = QFontDatabase.applicationFontFamilies(font_id)
            if fams:
                family = fams[0]
    if family:
        result = f'"{family}"'
    else:
        # 字体缺失时的体面降级，仍保持毛笔楷书风格
        result = '"KaiTi", "STKaiti", "楷体", serif'
    _NAV_FONT_CACHE[key] = result
    return result
