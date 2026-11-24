# -*- coding: utf-8 -*-
"""统一渲染质量控制：抗锯齿 / 高 DPI / 平滑变换。

全项目所有自定义 QPainter 绘制都经本模块，保证「界面抗锯齿」开关
（设置项 ui_antialias，默认开）一处生效，让图标、圆角、渐变光效、
文字边缘更精细。绘制模块只读 enabled()，不直接依赖 DataStore。

用法：
    from .render_quality import apply_painter
    painter = QPainter(self)
    apply_painter(painter, text=True, smooth=True)
"""

from __future__ import annotations

from typing import Optional

_getter = None                       # 由主窗口注入 settings_get(key, default)
_fallback_cache: Optional[bool] = None  # 未注入时独立读取的缓存


def bind_settings(getter) -> None:
    """注入设置读取函数，签名 f(key, default)。清缓存以便即时生效。"""
    global _getter, _fallback_cache
    _getter = getter
    _fallback_cache = None


def invalidate() -> None:
    """设置变更后调用，清独立读取缓存。"""
    global _fallback_cache
    _fallback_cache = None


def enabled() -> bool:
    """是否启用抗锯齿。默认开启；未注入 getter 时独立从 datastore 读取。"""
    if _getter is not None:
        try:
            return bool(_getter("ui_antialias", True))
        except Exception:  # noqa: BLE001
            return True
    global _fallback_cache
    if _fallback_cache is None:
        try:
            from .datastore import DataStore
            _fallback_cache = bool(
                DataStore().settings_get("ui_antialias", True))
        except Exception:  # noqa: BLE001
            _fallback_cache = True
    return _fallback_cache


def apply_global(app) -> None:
    """应用全局渲染质量。应在 QApplication 创建后、窗口创建前调用一次。

    - 高 DPI pixmap：让 XML/PNG 图标在高分屏按设备像素绘制，避免发虚；
    - 全局字体抗锯齿：默认 PreferAntialias，让所有控件文字边缘平滑。
    """
    if app is None:
        return
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont

    # Qt6 默认已启用高 DPI 缩放，这里显式打开 pixmap 高 DPI（Qt5 兼容）
    try:
        app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    except Exception:  # noqa: BLE001
        pass

    _apply_font_strategy(app, QFont)


def _apply_font_strategy(app, QFont_cls) -> None:
    """按抗锯齿开关设置全局字体策略。"""
    S = QFont_cls.StyleStrategy
    strategy = S.PreferAntialias if enabled() else S.NoAntialias
    try:
        f = app.font()
        f.setStyleStrategy(strategy)
        app.setFont(f)
    except Exception:  # noqa: BLE001
        pass


def refresh_fonts() -> None:
    """开关切换后调用：更新全局字体策略并触发已有窗口重绘。

    注意：已缓存的 QIcon/QPixmap 是烘焙后的位图，其抗锯齿在生成时
    已固定；切换开关后这类图像需重启程序才会完全重生成（UI 上已提示）。
    """
    invalidate()
    from PySide6.QtGui import QFont, QGuiApplication
    app = QGuiApplication.instance()
    if app is not None:
        _apply_font_strategy(app, QFont)
        for widget in app.topLevelWidgets():
            try:
                widget.update()
            except Exception:  # noqa: BLE001
                pass


def apply_painter(painter, text: bool = False, smooth: bool = False) -> None:
    """按当前抗锯齿开关设置 QPainter 的渲染提示。

    :param painter: QPainter 实例
    :param text: 是否同时开启文字抗锯齿（有文字绘制时传 True）
    :param smooth: 是否同时开启图片平滑变换（有 pixmap 缩放时传 True）
    """
    from PySide6.QtGui import QPainter
    on = enabled()
    painter.setRenderHint(QPainter.Antialiasing, on)
    if text:
        painter.setRenderHint(QPainter.TextAntialiasing, on)
    if smooth:
        painter.setRenderHint(QPainter.SmoothPixmapTransform, on)


def smooth_transform(pm, size: int):
    """平滑缩放 pixmap（跟随抗锯齿开关，关闭时用快速变换）。"""
    from PySide6.QtCore import Qt
    mode = Qt.SmoothTransformation if enabled() else Qt.FastTransformation
    return pm.scaled(size, size, Qt.KeepAspectRatio, mode)
