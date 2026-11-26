# -*- coding: utf-8 -*-
"""UI 界面背景配色中心（内容区整体背景，与功能卡片背景无关）。

职责
====
管理"背景美化"插件各套背景的可编辑配色：

* 5 套静态渐变（aurora / ocean / sunset / forest / violet-glow）；
* 5 套动态背景（flow 流光 + aurora-wave / starfield / ripple / particle）；
* 每套背景有深色、浅色两套色（默认来自插件里的写死值，作为出厂默认）；
* 用户可编辑任意一套的配色（纯色或渐变），也可新增一个"自定义背景"；
* 每个动态效果的光团/光带颜色（3~4 个）可单独编辑，深/浅各一套；
* 自动适配深浅：每套都存深/浅两色，切换主题时用对应的一套。

写入 store 的键统一以 ``ui_bg_`` 开头：
* ``ui_bg_{key}_dark`` / ``ui_bg_{key}_light``：该背景的渐变值（纯色/渐变）
* ``ui_bg_{key}_blob0..N``：动态效果的第 N 个颜色（key=flow 时即旧流光光团键）

值格式复用 nation_theme 的渐变编码（linear:角度|色1,色2,...），纯色为 #rrggbb。
空串（未设置）= 使用插件出厂默认色。
"""

from __future__ import annotations

from typing import Dict, List

from . import nation_theme as nt

# ------------------------------------------------------------------ 出厂默认配色

# 每套背景的 (key, 展示名, 深色默认[colors], 浅色默认[colors])
# 默认色取自 background_plugin.py 的写死值，作为"跟随默认"时的回退
PRESET_DEFAULTS: Dict[str, dict] = {
    "aurora": {
        "name": "极光紫", "shape": "linear",
        "dark": ["#332550", "#221c33", "#17171f"],
        "light": ["#e6dcf7", "#f2eefb", "#f7f7f8"],
    },
    "ocean": {
        "name": "深空蓝", "shape": "radial",
        "dark": ["#26405e", "#1a2635", "#161719"],
        "light": ["#e3eefb", "#f1f5fa", "#f7f7f8"],
    },
    "sunset": {
        "name": "暮光橙", "shape": "linear",
        "dark": ["#33241d", "#241c1a", "#171718"],
        "light": ["#fbeede", "#faf5ef", "#f7f7f8"],
    },
    "forest": {
        "name": "墨绿", "shape": "linear",
        "dark": ["#1c2b27", "#1a2220", "#171717"],
        "light": ["#e2f0ea", "#f0f7f4", "#f7f7f8"],
    },
    "violet-glow": {
        "name": "星云辉光", "shape": "radial",
        "dark": ["#2f2542", "#211d2e", "#171718"],
        "light": ["#e9e1f6", "#f3effa", "#f7f7f8"],
    },
    "flow": {
        "name": "流光渐变（动态）", "shape": "flow",
        "dark": ["#3a2b63", "#1f4066", "#5b2a52", "#2b4a55"],
        "light": ["#d9c9f2", "#c4dcf5", "#f0cfe4", "#cfe6e6"],
    },
    # ---- 新增动态效果（shape=dynamic，绘制由 dynamic_backgrounds 引擎负责）----
    "aurora-wave": {
        "name": "极光波浪（动态）", "shape": "dynamic",
        "dark": ["#3a2b6b", "#1f5a6e", "#5b2a6e", "#2b6b5a"],
        "light": ["#d9c9f5", "#c4e8f5", "#efc9f0", "#c9f0e4"],
    },
    "starfield": {
        "name": "星河漂流（动态）", "shape": "dynamic",
        "dark": ["#ffffff", "#b8c6ff", "#ffd6e8"],
        "light": ["#7a6fa8", "#6f8fc8", "#c88fae"],
    },
    "ripple": {
        "name": "涟漪扩散（动态）", "shape": "dynamic",
        "dark": ["#4a6fa5", "#6a4a9e", "#3a8a83"],
        "light": ["#bcd2f0", "#d0bcee", "#bce6de"],
    },
    "particle": {
        "name": "浮光粒子（动态）", "shape": "dynamic",
        "dark": ["#8ec5ff", "#d0b0ff", "#9affe0", "#ffd0b0"],
        "light": ["#7ba6d8", "#a98fd0", "#6fbfae", "#d8a878"],
    },
}

CUSTOM_KEY = "custom"
DEFAULT_ANGLE = 135

# ------------------------------------------------------------------ store 绑定

_getter = None


def bind_settings(getter) -> None:
    global _getter
    _getter = getter


def _setting(key: str, default=None):
    if _getter is None:
        return default
    try:
        return _getter(key, default)
    except Exception:  # noqa: BLE001
        return default


def bg_value_key(key: str, dark: bool) -> str:
    return f"ui_bg_{key}_{'dark' if dark else 'light'}"


def effect_color_key(effect_key: str, index: int) -> str:
    """动态效果第 index 个颜色的 store 键。

    对 flow 而言等价于旧的 ``ui_bg_flow_blob{index}``，因此旧数据完全兼容。
    """
    return f"ui_bg_{effect_key}_blob{index}"


def flow_blob_key(index: int) -> str:
    """旧流光光团键，保留为兼容别名。"""
    return effect_color_key("flow", index)


# ------------------------------------------------------------------ 解析

def preset_keys() -> List[str]:
    return list(PRESET_DEFAULTS.keys())


def dynamic_keys() -> List[str]:
    """所有动态背景 key（flow + 新增的动态效果）。"""
    return [k for k, v in PRESET_DEFAULTS.items()
            if v.get("shape") in ("flow", "dynamic")]


def effect_color_count(effect_key: str) -> int:
    """某动态效果的颜色个数（深色默认色的长度）。"""
    preset = PRESET_DEFAULTS.get(effect_key) or {}
    return len(preset.get("dark", []) or [])


def effect_colors(effect_key: str, dark: bool) -> List[str]:
    """返回某动态效果在当前主题下的实际颜色列表。

    优先级：用户逐色自定义值 > 出厂默认。每个颜色单独判断，
    因此只改其中一个也不会影响其他颜色。
    """
    preset = PRESET_DEFAULTS.get(effect_key) or {}
    defaults = list(preset.get("dark" if dark else "light", []) or [])
    result: List[str] = []
    for i in range(len(defaults)):
        stored = _normalize(_setting(effect_color_key(effect_key, i), ""))
        if stored:
            parsed = nt.parse_gradient(stored)
            result.append(parsed[1][0] if parsed else stored)
        else:
            result.append(defaults[i])
    return result


def is_custom_enabled() -> bool:
    """是否设置了自定义背景（深或浅任一套有值）。"""
    return bool(_setting(bg_value_key(CUSTOM_KEY, True), "") or
                _setting(bg_value_key(CUSTOM_KEY, False), ""))


def _normalize(value) -> str:
    """规范化存储的色值：合法纯色/渐变返回，否则空串。"""
    if not value:
        return ""
    return value if nt._valid_paint(value) else ""


def custom_value(dark: bool) -> str:
    return _normalize(_setting(bg_value_key(CUSTOM_KEY, dark), ""))


def resolved_value(key: str, dark: bool) -> List[str]:
    """返回该背景在该主题下实际使用的颜色列表。

    优先级：用户自定义值（纯色/渐变） > 出厂默认。
    """
    stored = _normalize(_setting(bg_value_key(key, dark), ""))
    if stored:
        parsed = nt.parse_gradient(stored)
        if parsed:
            return parsed[1]
        return [stored]
    preset = PRESET_DEFAULTS.get(key)
    if preset:
        return list(preset["dark" if dark else "light"])
    # 自定义但没有单独存值时：从另一套主题派生
    other = _normalize(_setting(bg_value_key(key, not dark), ""))
    if other:
        parsed = nt.parse_gradient(other)
        colors = parsed[1] if parsed else [other]
        return [auto_contrast(c, dark) for c in colors]
    return ["#17171f"] if dark else ["#f7f7f8"]


def shape_of(key: str) -> str:
    """背景绘制形状：linear / radial / flow / dynamic。"""
    preset = PRESET_DEFAULTS.get(key)
    if preset:
        return preset["shape"]
    return "linear"


def auto_contrast(color: str, dark: bool) -> str:
    """把颜色向深色/浅色方向派生，保证另一种主题下文字可读。

    dark=True  -> 显著压暗（浅色文字在上）
    dark=False -> 显著提亮（深色文字在上）
    """
    c = nt._qcolor(color)
    if not c.isValid():
        return "#17171f" if dark else "#f7f7f8"
    if dark:
        # 与主色混合大量深色底
        base = nt._qcolor("#17171f")
        ratio = 0.72
    else:
        base = nt._qcolor("#f7f7f8")
        ratio = 0.70
    from PySide6.QtGui import QColor
    mixed = QColor(
        int(c.red() * (1 - ratio) + base.red() * ratio),
        int(c.green() * (1 - ratio) + base.green() * ratio),
        int(c.blue() * (1 - ratio) + base.blue() * ratio),
    )
    return mixed.name()


def derive_pair_value(colors: List[str], source_dark: bool) -> str:
    """给定一套主题的颜色，派生另一主题应存储的渐变/纯色值。"""
    target_dark = not source_dark
    adapted = [auto_contrast(c, target_dark) for c in colors]
    if len(adapted) == 1:
        return adapted[0]
    return nt.make_gradient(DEFAULT_ANGLE, adapted)


# ------------------------------------------------------------------ QSS 生成

def gradient_qss(colors: List[str], shape: str, dark: bool) -> str:
    """把颜色列表 + 形状生成作用于内容区锚点的 QSS 片段。

    只命中 #appCentral / #appStack，不碰任何控件。
    """
    if not colors:
        return ""
    if len(colors) == 1:
        decl = f"background-color: {colors[0]};"
    elif shape == "radial":
        stops = " ".join(
            f"stop:{i / (len(colors) - 1):.4f} {c}" for i, c in enumerate(colors)
        )
        decl = ("background: qradialgradient(cx:0.5, cy:0.0, radius:1.2, "
                f"fx:0.5, fy:0.0, {stops});")
    else:
        stops = " ".join(
            f"stop:{i / (len(colors) - 1):.4f} {c}" for i, c in enumerate(colors)
        )
        decl = f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, {stops});"
    return (
        "/* UI背景配色中心：只作用于内容区锚点，不碰任何控件 */\n"
        "QWidget#appCentral, QStackedWidget#appStack {\n"
        f"    {decl}\n"
        "}\n"
    )


def background_stylesheet(key: str, dark: bool) -> str:
    """给定背景 key 与主题，返回最终 QSS（含用户覆盖）。

    所有动态背景（flow / dynamic）都返回透明化声明，
    实际绘制由铺在底层的动态 widget 完成。
    """
    if shape_of(key) in ("flow", "dynamic"):
        return (
            "/* 动态背景：内容区各层透明，露出底层动态绘制 widget */\n"
            "QStackedWidget#appStack, QWidget#appBackdropHost { "
            "background-color: transparent; background-image: none; }\n"
        )
    colors = resolved_value(key, dark)
    return gradient_qss(colors, shape_of(key), dark)


# ------------------------------------------------------------------ 动态效果颜色（兼容别名）

def flow_blob_colors(dark: bool) -> List[str]:
    """旧流光光团色接口，保留兼容；内部走通用 effect_colors。"""
    return effect_colors("flow", dark)


def any_custom() -> bool:
    for key in preset_keys() + [CUSTOM_KEY]:
        for dark in (True, False):
            if _normalize(_setting(bg_value_key(key, dark), "")):
                return True
    for key in dynamic_keys():
        for i in range(effect_color_count(key)):
            if _normalize(_setting(effect_color_key(key, i), "")):
                return True
    return False


class _StaticProvider:
    """轻量背景提供者：从配色中心读取自定义背景色生成 QSS。

    供主窗口 ``_active_background`` 在 key=custom 时使用，接口与插件的
    BackgroundProvider 一致（stylesheet / create_widget）。
    """

    def __init__(self, key: str = CUSTOM_KEY) -> None:
        self.key = key
        self.name = "自定义背景"

    def stylesheet(self, dark: bool) -> str:
        colors = resolved_value(self.key, dark)
        return gradient_qss(colors, "linear", dark)

    def create_widget(self, parent=None, dark: bool = True):
        return None
