# -*- coding: utf-8 -*-
"""统一动效模块：给界面加"丝滑"感，不改变任何布局、配色、控件。

参照 Wenku8 Flutter 的动效理念，但只用 Qt 原生能力实现：
- 页面切换：淡入 + 轻微上浮（QPropertyAnimation + OutCubic 缓动）
- 导航指示条：选中竖条在各项之间滑动
- 进度条：数值平滑增长
- 滚动：滚轮平滑滚动（缓动驱动滚动条）
- 动效总开关：可从设置读取 ui_motion / ui_motion_level，默认开启；
  低性能模式（ui_low_power）可一键关闭全部动效。

设计原则：
- 所有动画都可被全局开关``enabled()``关闭；关闭时函数直接同步执行，行为与无动效版一致。
- 尊重系统"减少动画"的无障碍偏好（styleHints().animationEnabled）。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, Qt
from PySide6.QtWidgets import (
    QAbstractScrollArea, QApplication, QComboBox, QGraphicsOpacityEffect, QProgressBar, QWidget,
)

# ---- 时长档位（毫秒）----
_LEVELS = {
    "low": {"page": 110, "nav": 120, "scroll": 130, "progress": 120},
    "normal": {"page": 220, "nav": 180, "scroll": 240, "progress": 220},
    "high": {"page": 340, "nav": 280, "scroll": 380, "progress": 340},
}

_getter = None  # 由主窗口注入 settings_get(key, default)


def bind_settings(getter) -> None:
    """注入设置读取函数，签名 ``f(key, default)``。"""
    global _getter
    _getter = getter


def _setting(key: str, default):
    if _getter is None:
        return default
    try:
        return _getter(key, default)
    except Exception:  # noqa: BLE001
        return default


def enabled() -> bool:
    """动效总开关。默认开启；低性能模式可整体关闭。"""
    if not bool(_setting("ui_motion", True)):
        return False
    if bool(_setting("ui_low_power", False)):
        return False
    try:
        app = QApplication.instance()
        if app is not None:
            # 无头/offscreen 环境不进事件循环，动画会停在半透明首帧；
            # 此时禁用动效，行为与无动效版完全一致（截图/回归不受影响）。
            if str(app.platformName()).lower() in ("offscreen", "minimal"):
                return False
            # 尊重系统"减少动画"偏好（PySide6 用 hasAnimationRelatedSetting）
            hint = app.styleHints()
            if hint is not None:
                getter = getattr(hint, "animationEnabled", None)
                if getter is not None and not getter():
                    return False
    except Exception:  # noqa: BLE001
        pass
    return True


def _duration(kind: str, base_ms: int) -> int:
    level = str(_setting("ui_motion_level", "normal"))
    if level not in _LEVELS:
        level = "normal"
    return int(_LEVELS[level].get(kind, base_ms))


def _curve(shape=QEasingCurve.OutCubic) -> QEasingCurve:
    return QEasingCurve(shape)


# ------------------------------------------------------------------ 页面切换

def fade_in_page(widget: Optional[QWidget], rise: int = 10) -> Optional[QPropertyAnimation]:
    """页面淡入 + 轻微上浮。返回不透明度的动画对象（供复用/测试）。

    动效关闭时返回 None，且不改动 widget 任何状态。
    """
    if widget is None or not enabled():
        return None
    try:
        effect = getattr(widget, "_motion_effect", None)
        if effect is None:
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
            setattr(widget, "_motion_effect", effect)
        effect.setOpacity(0.0)

        base_pos = QPoint(widget.pos())
        anim = QPropertyAnimation(effect, b"opacity")
        anim.setDuration(_duration("page", 220))
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(_curve())
        setattr(widget, "_motion_anim", anim)
        anim.start()

        # pos 动画独立进行，结束后精确归位，避免与布局打架
        if rise:
            pos_anim = QPropertyAnimation(widget, b"pos")
            pos_anim.setDuration(_duration("page", 220))
            pos_anim.setStartValue(base_pos + QPoint(0, rise))
            pos_anim.setEndValue(base_pos)
            pos_anim.setEasingCurve(_curve())
            pos_anim.finished.connect(lambda: widget.move(base_pos))
            setattr(widget, "_motion_pos_anim", pos_anim)
            pos_anim.start()
        return anim
    except Exception:  # noqa: BLE001
        return None


def clear_page_effect(widget: Optional[QWidget]) -> None:
    """切走页面后清理淡入 effect，避免叠加与渲染负担。"""
    if widget is None:
        return
    # 先停动画并重置属性引用，再释放 effect，任何一步失败都不影响后续清理
    for anim_key in ("_motion_anim", "_motion_pos_anim"):
        anim = getattr(widget, anim_key, None)
        if anim is not None:
            try:
                anim.stop()
            except Exception:  # noqa: BLE001
                pass
            setattr(widget, anim_key, None)
    effect = getattr(widget, "_motion_effect", None)
    setattr(widget, "_motion_effect", None)
    if effect is not None:
        try:
            widget.setGraphicsEffect(None)
        except Exception:  # noqa: BLE001
            pass
        try:
            effect.deleteLater()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ 主题/背景过渡

def crossfade_apply(widget: Optional[QWidget], apply_fn, *,
                    fade_out: int = 90, fade_in: int = 150) -> bool:
    """交叉淡入淡出地执行一次性视图变更（主题/背景切换用）。

    流程：把 widget 整体淡出到略透明 → 在最低点执行 ``apply_fn()``（重绘/换色）→
    再淡入回不透明。这样把一次性、生硬的全量重绘藏在渐变里，观感丝滑。

    - ``apply_fn`` 必须是无参可调用，且只做同步视图变更；
    - 全局动效关闭 / offscreen 环境下：直接同步执行 ``apply_fn()``，返回 False；
    - 返回 True 表示启动了异步动画；返回 False 表示已同步执行完毕。
    - 自带防重入：动画进行中再次调用不叠加动画，只记下最新一次 ``apply_fn``，
      待动画结束后补执行一次——保证「连点配色」时每次配置变更最终都反映到界面。
    """
    if widget is None or not callable(apply_fn):
        if callable(apply_fn):
            apply_fn()
        return False
    if not enabled():
        apply_fn()
        return False
    try:
        if getattr(widget, "_crossfade_running", False):
            # 动画中：不重启动画，只把最新视图变更记下来，结束时补执行
            setattr(widget, "_crossfade_pending", apply_fn)
            return True
        effect = getattr(widget, "_crossfade_effect", None)
        if effect is None:
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
            setattr(widget, "_crossfade_effect", effect)

        setattr(widget, "_crossfade_running", True)
        anim_out = QPropertyAnimation(effect, b"opacity")
        anim_out.setDuration(_duration("page", fade_out))
        anim_out.setStartValue(1.0)
        anim_out.setEndValue(0.35)
        anim_out.setEasingCurve(_curve(QEasingCurve.InQuad))

        anim_in = QPropertyAnimation(effect, b"opacity")
        anim_in.setDuration(_duration("page", fade_in))
        anim_in.setStartValue(0.35)
        anim_in.setEndValue(1.0)
        anim_in.setEasingCurve(_curve(QEasingCurve.OutCubic))

        def _on_out_finished():
            try:
                apply_fn()  # 在透明度最低点做重绘，藏住生硬切换
            finally:
                anim_in.start()

        def _on_in_finished():
            try:
                widget.setGraphicsEffect(None)
                effect.deleteLater()
            except Exception:  # noqa: BLE001
                pass
            setattr(widget, "_crossfade_effect", None)
            setattr(widget, "_crossfade_running", False)
            setattr(widget, "_crossfade_anim_out", None)
            setattr(widget, "_crossfade_anim_in", None)
            # 补执行动画期间累积的最后一次视图变更（连点配色不丢）
            pending = getattr(widget, "_crossfade_pending", None)
            setattr(widget, "_crossfade_pending", None)
            if callable(pending):
                try:
                    pending()
                except Exception:  # noqa: BLE001
                    pass

        anim_out.finished.connect(_on_out_finished)
        anim_in.finished.connect(_on_in_finished)
        setattr(widget, "_crossfade_anim_out", anim_out)
        setattr(widget, "_crossfade_anim_in", anim_in)
        anim_out.start()
        return True
    except Exception:  # noqa: BLE001
        # 任何异常都退回同步执行，保证视图变更一定发生
        setattr(widget, "_crossfade_running", False)
        apply_fn()
        return False


def cancel_crossfade(widget: Optional[QWidget]) -> None:
    """取消进行中的过渡（窗口关闭等场景），恢复控件原状。"""
    if widget is None:
        return
    for key in ("_crossfade_anim_out", "_crossfade_anim_in"):
        anim = getattr(widget, key, None)
        if anim is not None:
            try:
                anim.stop()
            except Exception:  # noqa: BLE001
                pass
            setattr(widget, key, None)
    effect = getattr(widget, "_crossfade_effect", None)
    if effect is not None:
        try:
            widget.setGraphicsEffect(None)
            effect.deleteLater()
        except Exception:  # noqa: BLE001
            pass
    setattr(widget, "_crossfade_effect", None)
    setattr(widget, "_crossfade_running", False)
    setattr(widget, "_crossfade_pending", None)


# ------------------------------------------------------------------ 导航指示条滑动

def slide_indicator(indicator: Optional[QWidget], target_y: int) -> Optional[QPropertyAnimation]:
    """把选中指示条平滑滑动到目标 y（保持 x 不变），返回动画对象供调用方保活/回调。"""
    if indicator is None:
        return None
    if not enabled():
        indicator.move(indicator.x(), target_y)
        return None
    try:
        anim = getattr(indicator, "_motion_anim", None)
        if isinstance(anim, QPropertyAnimation) and anim.state() == QPropertyAnimation.Running:
            anim.stop()
        anim = QPropertyAnimation(indicator, b"pos")
        anim.setDuration(_duration("nav", 180))
        anim.setStartValue(indicator.pos())
        anim.setEndValue(QPoint(indicator.x(), target_y))
        anim.setEasingCurve(_curve(QEasingCurve.OutQuad))
        setattr(indicator, "_motion_anim", anim)
        anim.start()
        return anim
    except Exception:  # noqa: BLE001
        indicator.move(indicator.x(), target_y)
        return None


# ------------------------------------------------------------------ 进度条

def animate_progress(bar: Optional[QProgressBar], value: int) -> None:
    """进度条数值平滑增长。动效关闭/进度回退时直接同步设置。"""
    if bar is None:
        return
    current = bar.value()
    if not enabled() or value <= current or value >= 100:
        bar.setValue(value)
        return
    try:
        anim = getattr(bar, "_motion_anim", None)
        if isinstance(anim, QPropertyAnimation) and anim.state() == QPropertyAnimation.Running:
            anim.stop()
        anim = QPropertyAnimation(bar, b"value")
        anim.setDuration(_duration("progress", 220))
        anim.setStartValue(current)
        anim.setEndValue(value)
        anim.setEasingCurve(_curve())
        setattr(bar, "_motion_anim", anim)
        anim.start()
    except Exception:  # noqa: BLE001
        bar.setValue(value)


# ------------------------------------------------------------------ 平滑滚动

class SmoothWheelFilter(QObject):
    """拦截滚轮，用缓动动画驱动滚动条，得到接近 Flutter 的滚动手感。

    只作用于 QAbstractScrollArea（QTableWidget / QTextEdit / QScrollArea 等）；
    不改变滚动步长语义，只是把"瞬跳"变成"缓动到位"。
    """

    def __init__(self, area: QAbstractScrollArea, parent=None) -> None:
        # 用 area 作为 QObject 父：过滤器生命周期由 Qt 管理，随控件一同销毁，
        # 避免解释器退出时 Python/C++ 销毁顺序不当导致的段错误。
        super().__init__(area if parent is None else parent)
        self._area = area
        self._anim: Optional[QPropertyAnimation] = None
        # 连续滚轮的累积目标：动画未走完时在新目标上叠加，而不是从当前值重算，
        # 否则快速连滚的后续增量会被“吃掉”，手感发黏。
        self._target: int = 0

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() != QEvent.Wheel or not enabled():
            return False
        area = self._area
        try:
            delta = event.angleDelta()
            if delta.isNull():
                return False
            if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
                return False  # 组合键交给原生（缩放等）
            vbar = area.verticalScrollBar()
            hbar = area.horizontalScrollBar()
            bar = vbar if abs(delta.y()) >= abs(delta.x()) and vbar is not None else hbar
            if bar is None or bar.maximum() <= bar.minimum():
                return False

            step = -(delta.y() if bar is vbar else delta.x())
            running = (self._anim is not None
                       and self._anim.state() == QPropertyAnimation.Running)
            base = self._target if running else bar.value()
            target = max(bar.minimum(), min(bar.maximum(), base + int(step)))
            self._target = target
            if running:
                self._anim.stop()
            self._anim = QPropertyAnimation(bar, b"value")
            self._anim.setDuration(_duration("scroll", 240))
            self._anim.setStartValue(bar.value())
            self._anim.setEndValue(target)
            self._anim.setEasingCurve(_curve())
            self._anim.start()
            return True  # 吞掉原生滚轮，避免"瞬跳 + 动画"双重滚动
        except Exception:  # noqa: BLE001
            return False


def install_smooth_scrolling(root: Optional[QWidget]) -> None:
    """为 root 及其所有子滚动区域安装平滑滚动（幂等）。

    在窗口构建完成后调用一次即可；新增页面若含滚动区，可再次调用。
    """
    if root is None or not enabled():
        return
    try:
        areas = []
        if isinstance(root, QAbstractScrollArea):
            areas.append(root)
        areas.extend(root.findChildren(QAbstractScrollArea))
        for area in areas:
            # 用 C++ 侧 property 做幂等标记，不在 Python 对象间建立相互引用
            if area.property("_smooth_wheel_installed"):
                continue
            filt = SmoothWheelFilter(area)  # parent=area，生命周期交给 Qt
            area.installEventFilter(filt)
            area.setProperty("_smooth_wheel_installed", True)
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ 下拉框滚轮守卫

class ComboWheelGuard(QObject):
    """全局守卫：滚轮悬停在 QComboBox 上时不再切换选项，改为滚动所在页面。

    QComboBox 默认会消费滚轮事件来切换当前项，导致：滚轮划到下拉框上时
    选项被无意改动，同时页面又不滚动。这里在 QApplication 层拦截 combo 自身
    的滚轮，吞掉切换，并把滚轮转发给它祖先里最近的滚动区，从而既能正常滚
    页面、又不误改选择；点击右侧箭头弹窗、在弹窗列表里滚轮选项均不受影响。

    守卫装在 QApplication 上（以 app 为 QObject 父），全局覆盖所有页面与弹窗，
    生命周期随应用，避免解释器退出时的销毁顺序问题。
    """

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() != QEvent.Wheel or not isinstance(obj, QComboBox):
            return False
        try:
            # 组合键（Ctrl 缩放等）交给原生处理，不改变既有语义
            if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
                return False
            area = self._nearest_scroll_area(obj)
            if area is None:
                # 不在滚动区里：仍吞掉滚轮以避免误切换选项
                return True
            # 转发给滚动区 viewport，让页面照常滚动（含已安装的平滑滚动）
            target = area.viewport() if hasattr(area, "viewport") else area
            try:
                QApplication.sendEvent(target, event)
            except Exception:  # noqa: BLE001
                # 某些 Qt 版本事件不可重发：退化为直接驱动滚动条
                self._fallback_scroll(area, event)
            return True
        except Exception:  # noqa: BLE001
            return True

    @staticmethod
    def _nearest_scroll_area(widget) -> Optional[QAbstractScrollArea]:
        parent = widget.parentWidget() if widget is not None else None
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    @staticmethod
    def _fallback_scroll(area: QAbstractScrollArea, event) -> None:
        delta = event.angleDelta()
        if delta.isNull():
            return
        vbar = area.verticalScrollBar()
        hbar = area.horizontalScrollBar()
        bar = vbar if abs(delta.y()) >= abs(delta.x()) and vbar is not None else hbar
        if bar is None or bar.maximum() <= bar.minimum():
            return
        step = -(delta.y() if bar is vbar else delta.x())
        bar.setValue(max(bar.minimum(), min(bar.maximum(), bar.value() + int(step))))


_combo_guard: Optional[ComboWheelGuard] = None


def install_combo_wheel_guard() -> None:
    """在应用级安装下拉框滚轮守卫（幂等，不依赖动效开关）。"""
    global _combo_guard
    app = QApplication.instance()
    if app is None:
        return
    if _combo_guard is not None:
        try:
            if _combo_guard.parent() is app:
                return
        except Exception:  # noqa: BLE001
            pass
    _combo_guard = ComboWheelGuard(app)
    app.installEventFilter(_combo_guard)


def fade_in_window(window: Optional[QWidget]) -> None:
    """窗口整体淡入（首次显示时用）。"""
    if window is None or not enabled():
        return
    try:
        if getattr(window, "_motion_window_shown", False):
            return
        setattr(window, "_motion_window_shown", True)
        window.setWindowOpacity(0.0)
        anim = QPropertyAnimation(window, b"windowOpacity")
        anim.setDuration(_duration("page", 220))
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(_curve())
        setattr(window, "_motion_window_anim", anim)
        anim.start()
    except Exception:  # noqa: BLE001
        pass
