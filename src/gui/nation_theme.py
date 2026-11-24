# -*- coding: utf-8 -*-
"""部件独立配色中心（国徽 / 文字 / 功能背景）+ 渐变色支持。

设计目标
========
把每一个功能的外观拆成三个**可独立调节**的部件：

1. 国徽图标 —— 8 个国徽各有独立颜色/渐变，改一个只影响同名图标；
2. 文字     —— 左侧导航文字的颜色与字体；
3. 功能背景 —— 导航项背景 + 各功能页卡片背景（不是整个 UI 页面背景）。

所有色值/字体都持久化在 DataStore.settings，键名统一以 ``part_`` 开头。
本模块只负责"解析出当前该用的值"，不负责绘制；绘制层（widgets/side_nav/
各 view）在生成图标、文字、卡片背景前调用这里的解析函数即可。

颜色值格式
==========
* 纯色： ``#rrggbb`` / ``#rgb``
* 渐变： ``linear:{angle}|{c1},{c2},...``
  例： ``linear:135|#359697,#52C4BE``（角度为 CSS 语义：0=向上，90=向右）

默认配色（对应本次需求）
========================
* **8 个国徽**：默认使用各自的官方主题色（原神七元素官方色 + 挪德卡莱月光色），
  来自 ``assets/gradient_presets.json``；用户未设置或点"恢复默认"时回到该官方色。
* **其他部件**（导航文字色/字体、导航背景、卡片背景）：默认空串 = 跟随系统主题。

强制染色语义
============
``resolved_nation_color(nation, fallback)`` 的优先级：
用户自定义（纯色/渐变） > 该国官方默认色 > 调用点 fallback。
即用户给该国徽设了色（含渐变）一律覆盖 fallback，实现"改蒙德，全项目所有
蒙德图标一起变色，其他国徽不变"。

绑定
====
启动时由主窗口调用 :func:`bind_settings` 注入 store 读取器（与 motion /
notify_core 同一模式）。未绑定时全部回退到默认值，保证模块可独立导入。
"""

from __future__ import annotations

import json
import math
import os
from typing import Callable, Dict, List, Optional, Tuple

# ------------------------------------------------------------------ 国徽定义

# 8 个国徽的稳定顺序（与 side_nav.NATION_ICONS / widgets.NATION_ICONS 一致）
NATIONS = (
    "mondstadt",   # 蒙德
    "liyue",       # 璃月
    "inazuma",     # 稻妻
    "sumeru",      # 须弥
    "fontaine",    # 枫丹
    "natlan",      # 纳塔
    "nodkrai",     # 挪德卡莱
    "snezhnaya",   # 至冬
)

# 中文显示名（设置页用；英文键在 i18n 里另有映射）
NATION_LABELS_ZH: Dict[str, str] = {
    "mondstadt": "蒙德", "liyue": "璃月", "inazuma": "稻妻", "sumeru": "须弥",
    "fontaine": "枫丹", "natlan": "纳塔", "nodkrai": "挪德卡莱", "snezhnaya": "至冬",
}

# 兜底官方主题色（资源文件缺失时使用，保证离线可用）
_FALLBACK_NATION_BASE: Dict[str, str] = {
    "mondstadt": "#359697", "liyue": "#DEBD6C", "inazuma": "#945DC4", "sumeru": "#66AD16",
    "fontaine": "#00BFFF", "natlan": "#EC4923", "nodkrai": "#A99BE8", "snezhnaya": "#4682B4",
}

# ------------------------------------------------------------------ 渐变格式

GRAD_PREFIX = "linear:"


def make_gradient(angle: int, colors: List[str]) -> str:
    """把角度+颜色列表编码成持久化字符串。"""
    colors = [c for c in (str(x).strip() for x in colors) if c]
    return f"{GRAD_PREFIX}{int(angle)}|" + ",".join(colors)


def is_gradient(value) -> bool:
    return isinstance(value, str) and value.startswith(GRAD_PREFIX)


def parse_gradient(value) -> Optional[Tuple[int, List[str]]]:
    """解析渐变字符串 -> (angle, [colors])；非法返回 None。"""
    if not is_gradient(value):
        return None
    try:
        body = value[len(GRAD_PREFIX):]
        angle_s, colors_s = body.split("|", 1)
        angle = int(angle_s)
        colors = [c.strip() for c in colors_s.split(",") if c.strip()]
        if len(colors) < 2 or not all(_valid_color(c) for c in colors):
            return None
        return angle % 360, colors
    except Exception:  # noqa: BLE001
        return None


def _gradient_vector(angle: int) -> Tuple[float, float, float, float]:
    """CSS 角度 -> QSS/QPainter 通用的 (x1,y1,x2,y2)，取值 0..1。"""
    rad = math.radians(angle)
    dx, dy = math.sin(rad), -math.cos(rad)  # y 向下为正
    return (0.5 - 0.5 * dx, 0.5 - 0.5 * dy,
            0.5 + 0.5 * dx, 0.5 + 0.5 * dy)


def qss_gradient(value: str) -> str:
    """渐变字符串 -> Qt 样式表 qlineargradient(...) 值。"""
    parsed = parse_gradient(value)
    if not parsed:
        return ""
    angle, colors = parsed
    x1, y1, x2, y2 = _gradient_vector(angle)
    n = len(colors)
    stops = " ".join(
        f"stop:{i / (n - 1):.4f} {c}" for i, c in enumerate(colors)
    )
    return (f"qlineargradient(x1:{x1:.3f}, y1:{y1:.3f}, x2:{x2:.3f}, y2:{y2:.3f}, "
            f"{stops})")


def gradient_brush(value: str, rect):
    """渐变字符串 + QRectF -> QBrush（供 QPainter 填充）；非渐变返回 None。"""
    parsed = parse_gradient(value)
    if not parsed:
        return None
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QBrush, QLinearGradient
    angle, colors = parsed
    x1, y1, x2, y2 = _gradient_vector(angle)
    grad = QLinearGradient(
        QPointF(rect.x() + rect.width() * x1, rect.y() + rect.height() * y1),
        QPointF(rect.x() + rect.width() * x2, rect.y() + rect.height() * y2),
    )
    n = len(colors)
    for i, c in enumerate(colors):
        grad.setColorAt(i / (n - 1), _qcolor(c))
    return QBrush(grad)


def _qcolor(value: str):
    from PySide6.QtGui import QColor
    return QColor(value)


def color_swatch_icon(value: str, size: int = 14):
    """生成纯色/渐变的色块 QIcon，供标题颜色等下拉/菜单复用。"""
    from PySide6.QtCore import Qt, QRectF
    from PySide6.QtGui import QPixmap, QPainter, QIcon, QColor
    from . import render_quality
    pm = QPixmap(size + 4, size + 4)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    render_quality.apply_painter(painter)
    rect = QRectF(2, 2, size, size)
    if parse_gradient(value):
        brush = gradient_brush(value, rect)
        if brush is not None:
            painter.setBrush(brush)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 3, 3)
    else:
        c = QColor(value)
        if c.isValid():
            painter.setBrush(c)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 3, 3)
    painter.end()
    return QIcon(pm)


def tint_pixmap(pm, color: str, size: Optional[int] = None):
    """把白色 PNG 用纯色/渐变染色（SourceIn），返回染色后的 QPixmap。

    供 widgets.nation_icon 与 side_nav._tinted_icon 共用，保证渐变国徽
    在侧栏与页面内表现一致。
    """
    from PySide6.QtGui import QPainter
    from PySide6.QtGui import QColor
    from . import render_quality
    if size and size > 0:
        pm = render_quality.smooth_transform(pm, size)
    painter = QPainter(pm)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    brush = gradient_brush(color, pm.rect())
    painter.fillRect(pm.rect(), brush if brush is not None else QColor(color))
    painter.end()
    return pm


# ------------------------------------------------------------------ 设置键

PREFIX = "part_"

def nation_key(nation: str) -> str:
    return f"{PREFIX}nation_{nation}"

KEY_NAV_TEXT = f"{PREFIX}nav_text_color"
KEY_NAV_FONT = f"{PREFIX}nav_font"
KEY_NAV_BG = f"{PREFIX}nav_bg_color"
KEY_CARD_BG = f"{PREFIX}card_bg_color"

# 可选导航字体：(设置值, 字体文件名, 展示名) —— 文件位于 assets/fonts/
NAV_FONTS = (
    ("mashan",   "MaShanZheng-Regular.ttf",      "马善政毛笔楷书"),
    ("zhimang",  "ZhiMangXing-Regular.ttf",      "钟齐志莽行书"),
    ("kuaile",   "free/ZCOOLKuaiLe-Regular.ttf", "站酷快乐体"),
    ("huangyou", "free/ZCOOLQingKeHuangYou-Regular.ttf", "站酷庆科黄油体"),
    ("xiaowei",  "free/ZCOOLXiaoWei-Regular.ttf", "站酷小薇体"),
    ("longcang", "free/LongCang-Regular.ttf",    "龙藏体"),
    ("liujian",  "free/LiuJianMaoCao-Regular.ttf", "刘建毛草"),
)
DEFAULT_NAV_FONT = "mashan"

# ------------------------------------------------------------------ store 绑定

_getter: Optional[Callable[[str, object], object]] = None


def bind_settings(getter: Callable[[str, object], object]) -> None:
    """注入 settings 读取器 ``getter(key, default)``。"""
    global _getter
    _getter = getter


def _setting(key: str, default=None):
    if _getter is None:
        return default
    try:
        return _getter(key, default)
    except Exception:  # noqa: BLE001
        return default


def _valid_color(value) -> bool:
    return isinstance(value, str) and len(value) in (4, 7) and value.startswith("#")


def _valid_paint(value) -> bool:
    """合法的颜色值：纯色或渐变。"""
    return _valid_color(value) or (is_gradient(value) and parse_gradient(value) is not None)


# ------------------------------------------------------------------ 预设资源

_PRESETS_CACHE: Optional[dict] = None


def load_presets() -> dict:
    """读取 assets/gradient_presets.json（含八国官方色与通用渐变预设）。"""
    global _PRESETS_CACHE
    if _PRESETS_CACHE is not None:
        return _PRESETS_CACHE
    data: dict = {"nation": {}, "presets": []}
    try:
        from .resource_path import resource_path
        path = resource_path("assets", "gradient_presets.json")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = {
                    "nation": loaded.get("nation") or {},
                    "presets": loaded.get("presets") or [],
                }
    except Exception:  # noqa: BLE001
        pass
    _PRESETS_CACHE = data
    return data


def nation_default(nation: str) -> str:
    """该国徽的官方主题色（纯色，默认色）。"""
    info = load_presets().get("nation", {}).get(nation) or {}
    base = info.get("base", "")
    return base if _valid_color(base) else _FALLBACK_NATION_BASE.get(nation, "")


def nation_official_gradient(nation: str) -> str:
    """该国徽的官方同色系渐变值（供预设快捷项使用）。"""
    info = load_presets().get("nation", {}).get(nation) or {}
    colors = info.get("gradient") or []
    if len(colors) >= 2 and all(_valid_color(c) for c in colors):
        return make_gradient(135, colors[:3])
    base = nation_default(nation)
    return make_gradient(135, [base, base]) if base else ""


def general_presets() -> List[dict]:
    """通用渐变预设列表：[{name, colors}, ...]。"""
    return list(load_presets().get("presets") or [])


# ------------------------------------------------------------------ 解析接口

def nation_color(nation: str) -> str:
    """返回该国徽的用户自定义色/渐变；未设置或非法时返回空串。"""
    val = _setting(nation_key(nation), "")
    return val if _valid_paint(val) else ""


def resolved_nation_color(nation: str, fallback: str) -> str:
    """强制染色解析：用户自定义 > 官方默认色 > 调用点 fallback。"""
    return nation_color(nation) or nation_default(nation) or fallback


def nav_text_color() -> str:
    """导航文字色（仅纯色，文字不支持渐变）。"""
    val = _setting(KEY_NAV_TEXT, "")
    return val if _valid_color(val) else ""


def nav_font_key() -> str:
    val = str(_setting(KEY_NAV_FONT, DEFAULT_NAV_FONT) or DEFAULT_NAV_FONT)
    return val if any(k == val for k, _, _ in NAV_FONTS) else DEFAULT_NAV_FONT


def nav_bg_color() -> str:
    """导航项背景色（支持纯色/渐变），默认空=跟随主题。"""
    val = _setting(KEY_NAV_BG, "")
    return val if _valid_paint(val) else ""


def card_bg_color() -> str:
    """功能卡片背景色（支持纯色/渐变），默认空=跟随主题。"""
    val = _setting(KEY_CARD_BG, "")
    return val if _valid_paint(val) else ""


def bg_qss_decl(value: str, fallback: str = "transparent") -> str:
    """把背景色值转成 QSS 声明（渐变用 background:，纯色用 background-color:）。"""
    g = qss_gradient(value)
    if g:
        return f"background: {g};"
    return f"background-color: {value or fallback};"


def paint_representative(value: str) -> str:
    """渐变 -> 首色（代表色），纯色 -> 自身。供需要纯色做对比计算的场景使用。"""
    parsed = parse_gradient(value)
    if parsed:
        return parsed[1][0]
    return value


def resolved_card_bg(fallback: str) -> str:
    """功能页卡片背景：用户设了就用它（渐变取代表色），否则用视图默认。

    返回纯色以保证视图内部基于 QColor 的对比/派生计算正常；
    渐变背景的视觉效果由主窗口 QSS 全局覆盖（bg_qss_decl）实现。
    """
    override = card_bg_color()
    return paint_representative(override) if override else fallback


def shift_color(color: str, amount: int) -> str:
    """把 #rrggbb 向白(amount>0)/黑(amount<0) 偏移，保持卡片层次。"""
    if not _valid_color(color):
        return color
    try:
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        if len(color) == 4:
            r, g, b = int(color[1] * 2, 16), int(color[2] * 2, 16), int(color[3] * 2, 16)
        if amount >= 0:
            r, g, b = (r + (255 - r) * amount // 100,
                       g + (255 - g) * amount // 100,
                       b + (255 - b) * amount // 100)
        else:
            k = -amount
            r, g, b = (r * (100 - k) // 100, g * (100 - k) // 100, b * (100 - k) // 100)
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:  # noqa: BLE001
        return color


def resolved_card_pair(dark: bool, card: str, card2: str) -> tuple:
    """返回覆盖后的 (card, card2) 纯色对（渐变取代表色），保证对比计算正常。

    渐变背景的视觉效果由主窗口 QSS 全局覆盖（bg_qss_decl）实现。
    """
    override = card_bg_color()
    if not override:
        return card, card2
    base = paint_representative(override)
    derived = shift_color(base, 9 if dark else -6)
    return base, derived


def any_custom() -> bool:
    """是否存在任何自定义部件配置（供 UI 提示用）。"""
    if nav_text_color() or nav_bg_color() or card_bg_color():
        return True
    if str(_setting(KEY_NAV_FONT, "")):
        return True
    return any(nation_color(n) for n in NATIONS)
