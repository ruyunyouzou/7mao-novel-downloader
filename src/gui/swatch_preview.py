# -*- coding: utf-8 -*-
"""色块悬停放大预览。

鼠标移到设置页的色块（主题色圆点 / 分部件配色 / 国徽染色 / 取色器预设块）
上时，弹出一个放大的色卡 + 色值文字，方便精确查看颜色。用 QToolTip 的
富文本实现，支持纯色与渐变；带短暂延迟防抖，避免快速划过时闪烁。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QTimer, QObject
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QToolTip, QWidget

_CARD_W = 128
_CARD_H = 56


def _esc(text: str) -> str:
    """HTML 转义，避免色值里的 & < > 破坏富文本。"""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _gradient_css(value: str) -> str:
    """渐变持久化串 -> CSS linear-gradient；失败返回空串。"""
    from . import nation_theme as nt
    parsed = nt.parse_gradient(value)
    if not parsed:
        return ""
    angle, colors = parsed
    return "linear-gradient(%ddeg, %s)" % (angle, ", ".join(colors))


def _color_text(value: str, fallback: str = "") -> str:
    """供色值行显示的文本：渐变转可读格式，跟随主题时给出兜底提示。"""
    from . import nation_theme as nt
    value = (value or "").strip()
    if not value:
        return "跟随主题" + (f"（{fallback}）" if fallback else "")
    parsed = nt.parse_gradient(value)
    if parsed:
        angle, colors = parsed
        return "渐变 %d° · %s" % (angle, " → ".join(colors))
    return value


def _build_html(value: str, name: str, fallback: str = "") -> str:
    """生成放大色卡的富文本 HTML。"""
    from . import nation_theme as nt
    raw = (value or "").strip()
    bg_css = ""
    if raw and nt.is_gradient(raw):
        bg_css = _gradient_css(raw)
    if not bg_css:
        color = raw or fallback or "#888888"
        # 兜底：非法颜色值交给 CSS 时可能无效，用 QColor 校验
        if not QColor(color).isValid():
            color = "#888888"
        bg_css = color

    name_html = _esc(name) if name else "颜色预览"
    val_html = _esc(_color_text(raw, fallback))
    return (
        '<div style="font-family:\'Noto Sans CJK SC\',\'Microsoft YaHei\',sans-serif;">'
        '<div style="width:%dpx;height:%dpx;border-radius:8px;background:%s;'
        'border:1px solid rgba(255,255,255,0.35);"></div>'
        '<div style="margin-top:7px;font-size:12px;font-weight:600;color:#f0f0f0;">%s</div>'
        '<div style="margin-top:2px;font-size:11px;color:#bdbdbd;word-wrap:break-word;">%s</div>'
        '</div>'
    ) % (_CARD_W, _CARD_H, bg_css, name_html, val_html)


class SwatchHoverFilter(QObject):
    """安装到色块控件上的悬停过滤器，延迟弹出放大预览。"""

    def __init__(self, target: QWidget, value_getter, name: str = "",
                 fallback: str = "") -> None:
        super().__init__(target)
        self._target = target
        self._value_getter = value_getter  # callable() -> 色值字符串
        self._name = name
        self._fallback = fallback
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self._show)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is not self._target:
            return False
        et = event.type()
        if et in (QEvent.Enter, QEvent.HoverMove):
            # 鼠标在色块内移动时不断续期，真正停留才弹出
            self._timer.start()
        elif et in (QEvent.Leave, QEvent.MouseButtonPress):
            self._timer.stop()
            QToolTip.hideText()
        return False

    def _show(self) -> None:
        try:
            value = str(self._value_getter() or "")
        except Exception:  # noqa: BLE001
            value = ""
        html = _build_html(value, self._name, self._fallback)
        # 锚点取色块底部中央，预览浮在其下方
        anchor = self._target.mapToGlobal(
            QPoint(self._target.width() // 2, self._target.height()))
        QToolTip.showText(anchor, html, self._target)


def attach_preview(target: QWidget, value_getter, name: str = "",
                   fallback: str = "") -> SwatchHoverFilter:
    """给色块安装悬停放大预览。

    :param target: 色块控件（QFrame / QPushButton）
    :param value_getter: 无参 callable，返回当前色值（纯色/渐变/空=跟随主题）
    :param name: 预览标题（如「导航文字」「蒙德」）
    :param fallback: 跟随主题时的兜底色值，用于展示
    """
    target.setMouseTracking(True)
    filt = SwatchHoverFilter(target, value_getter, name, fallback)
    target.installEventFilter(filt)
    return filt
