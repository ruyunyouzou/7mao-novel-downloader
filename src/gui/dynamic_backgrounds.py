# -*- coding: utf-8 -*-
"""纯 Qt 多效果动态背景引擎。

在原有流光渐变（flow_background.FlowGradientWidget）之外，提供多种轻量
动态背景：极光波浪、星河漂流、涟漪扩散、粒子上升。全部满足：

- 只用 QPainter + QTimer，零外部依赖、跨平台；
- 铺满内容区底层、鼠标事件透明，不干扰任何上层控件；
- 统一基类管理定时器 / 暂停 / 深浅主题 / 颜色注入；
- 不可见或暂停时完全停止重绘，CPU 占用低。

颜色由配色中心（ui_background_theme）注入：每个效果有自己的色数（3~5），
深色/浅色各一套；未注入时用内置兜底色。
"""

from __future__ import annotations

import math
import random
from typing import List

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QColor, QBrush, QLinearGradient, QPen,
)
from PySide6.QtWidgets import QWidget

_DARK_BASE = "#17171f"
_LIGHT_BASE = "#f7f7f8"


class AnimatedBackground(QWidget):
    """动态背景基类：定时器、暂停、深浅主题、颜色注入的公共实现。"""

    # 子类覆盖：效果 key、默认深色/浅色（颜色列表）
    effect_key = "base"
    default_dark: List[str] = []
    default_light: List[str] = []

    def __init__(self, parent=None, dark: bool = True, interval: int = 50) -> None:
        super().__init__(parent)
        self._dark = bool(dark)
        self._paused = False
        self._t = 0.0
        self._colors: List[str] = []
        self._timer = QTimer(self)
        self._timer.setInterval(interval)
        self._timer.timeout.connect(self._on_tick)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self._apply_timer()

    # ---- 对外控制 ----
    def set_dark(self, dark: bool) -> None:
        if self._dark != bool(dark):
            self._dark = bool(dark)
            self.update()

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)
        self._apply_timer()

    def is_paused(self) -> bool:
        return self._paused

    def set_colors(self, colors) -> None:
        """注入配色中心的颜色列表（非法/不足时用默认值补齐）。"""
        self._colors = [str(c) for c in (colors or []) if c]
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    # ---- 内部 ----
    @property
    def colors(self) -> List[str]:
        defaults = self.default_dark if self._dark else self.default_light
        result = []
        for i, c in enumerate(defaults):
            raw = self._colors[i] if i < len(self._colors) else c
            col = QColor(raw)
            result.append(col.name() if col.isValid() else c)
        # 若注入色多于默认，额外颜色也保留
        for raw in self._colors[len(defaults):]:
            col = QColor(raw)
            if col.isValid():
                result.append(col.name())
        return result

    @property
    def base_color(self) -> QColor:
        return QColor(_DARK_BASE if self._dark else _LIGHT_BASE)

    def _apply_timer(self) -> None:
        if self._paused or not self.isVisible():
            self._timer.stop()
        else:
            self._timer.start()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_timer()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._timer.stop()

    def _on_tick(self) -> None:
        # 每帧推进幅度：60ms 一帧、+0.022/帧，约 1.5 秒一个完整相位，肉眼可见但柔和
        self._t += 0.022
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.base_color)
        self.draw(painter, w, h)
        painter.end()

    def draw(self, painter: QPainter, w: int, h: int) -> None:
        """子类实现具体绘制。"""
        raise NotImplementedError


class AuroraWaveBackground(AnimatedBackground):
    """极光波浪：数条彩色光带沿正弦曲线缓慢起伏、渐隐。"""

    effect_key = "aurora-wave"
    default_dark = ["#3a2b6b", "#1f5a6e", "#5b2a6e", "#2b6b5a"]
    default_light = ["#d9c9f5", "#c4e8f5", "#efc9f0", "#c9f0e4"]

    def draw(self, painter, w, h):
        cols = self.colors
        n = len(cols) or 1
        alpha = 105 if self._dark else 88
        for band in range(n):
            color = QColor(cols[band])
            phase = self._t * (0.55 + band * 0.12) + band * 1.7
            band_y = h * (0.30 + 0.42 * (band / max(1, n - 1)))
            amp = min(h, w) * (0.12 + 0.04 * (band % 2))
            # 用多边形近似正弦波带
            steps = 40
            top = []
            bottom = []
            thick = min(h, w) * (0.16 + 0.05 * (band % 2))
            for i in range(steps + 1):
                x = w * i / steps
                y = band_y + amp * math.sin(x / max(1, w) * math.pi * 2 + phase)
                top.append(QPointF(x, y - thick / 2))
                bottom.append(QPointF(x, y + thick / 2))
            poly = top + list(reversed(bottom))
            from PySide6.QtGui import QPolygonF
            grad = QLinearGradient(0, band_y - thick, 0, band_y + thick)
            c0 = QColor(color); c0.setAlpha(0)
            c1 = QColor(color); c1.setAlpha(alpha)
            grad.setColorAt(0.0, c0)
            grad.setColorAt(0.5, c1)
            grad.setColorAt(1.0, c0)
            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.NoPen)
            painter.drawPolygon(QPolygonF(poly))


class StarfieldBackground(AnimatedBackground):
    """星河漂流：随机星点缓慢横向漂移并呼吸闪烁。"""

    effect_key = "starfield"
    default_dark = ["#ffffff", "#b8c6ff", "#ffd6e8"]
    default_light = ["#7a6fa8", "#6f8fc8", "#c88fae"]

    def __init__(self, parent=None, dark=True, interval=50):
        super().__init__(parent, dark, interval)
        rng = random.Random(20261124)
        # 星点更大、速度差更大；另加 3 团缓慢漂移的星云光晕，整体动感明显
        self._stars = [
            (rng.random(), rng.random(), rng.uniform(1.8, 4.0),
             rng.uniform(0, math.tau), rng.uniform(0.8, 2.4), rng.uniform(0.55, 1.0))
            for _ in range(150)
        ]
        self._nebula = [
            (rng.random(), rng.random(), rng.uniform(0.18, 0.34),
             rng.uniform(0, math.tau), rng.uniform(0.3, 0.7))
            for _ in range(3)
        ]
        self._nebula_cache = {}
        self._pix_cache = {}

    def _ensure_pixmaps(self, cols) -> None:
        """预渲染柔光贴图（星云团 + 星点），避免高频循环里构造 QBrush 崩溃。"""
        from PySide6.QtGui import QPixmap, QRadialGradient
        from PySide6.QtCore import QPointF

        def make(color_hex: str, size: int, peak: int) -> QPixmap:
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            grad = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
            inner = QColor(color_hex); inner.setAlpha(peak)
            outer = QColor(color_hex); outer.setAlpha(0)
            grad.setColorAt(0.0, inner)
            grad.setColorAt(1.0, outer)
            p.setBrush(QBrush(grad)); p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(0, 0, size, size))
            p.end()
            return pm

        for c in cols:
            if c not in self._pix_cache:
                self._pix_cache[c] = make(c, 32, 235)       # 星点贴图
            if c not in self._nebula_cache:
                self._nebula_cache[c] = make(c, 256, 95)    # 星云贴图

    def set_colors(self, colors) -> None:
        super().set_colors(colors)
        self._pix_cache.clear()
        self._nebula_cache.clear()

    def draw(self, painter, w, h):
        cols = self.colors
        n = len(cols) or 1
        self._ensure_pixmaps(cols)
        painter.setOpacity(1.0)
        # 1) 漂移星云底色：给纯色底加明显的流动光晕
        for nx, ny, scale, phase, speed in self._nebula:
            cx = ((nx + self._t * 0.012 * speed) % 1.0) * w
            cy = (ny + 0.05 * math.sin(self._t * speed + phase)) * h
            d = int(min(w, h) * scale * 2)
            c = cols[int((nx + ny) * n * 3) % n]
            painter.setOpacity(0.55 if self._dark else 0.4)
            target = QRectF(cx - d / 2, cy - d / 2, d, d)
            src = QRectF(0, 0, self._nebula_cache[c].width(), self._nebula_cache[c].height())
            painter.drawPixmap(target, self._nebula_cache[c], src)
        # 2) 闪烁漂移星点
        for sx, sy, size, phase, speed, base_a in self._stars:
            x = ((sx + self._t * 0.045 * speed) % 1.0) * w
            y = sy * h
            twinkle = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(self._t * speed * 2.2 + phase))
            c = cols[int((sx * 7 + sy * 3) * n) % n]
            d = int(size * 3)
            painter.setOpacity(max(0.05, base_a * twinkle))
            target = QRectF(x - d / 2, y - d / 2, d, d)
            src = QRectF(0, 0, self._pix_cache[c].width(), self._pix_cache[c].height())
            painter.drawPixmap(target, self._pix_cache[c], src)
        painter.setOpacity(1.0)


class RippleBackground(AnimatedBackground):
    """涟漪扩散：若干同心圆环从多个中心缓慢扩散、淡出。"""

    effect_key = "ripple"
    default_dark = ["#4a6fa5", "#6a4a9e", "#3a8a83"]
    default_light = ["#bcd2f0", "#d0bcee", "#bce6de"]

    def __init__(self, parent=None, dark=True, interval=50):
        super().__init__(parent, dark, interval)
        self._centers = [(0.30, 0.35), (0.72, 0.62), (0.50, 0.20)]

    def draw(self, painter, w, h):
        cols = self.colors
        n = len(cols) or 1
        unit = min(w, h)
        max_r = unit * 0.85
        rings = 5
        for ci, (cx, cy) in enumerate(self._centers):
            for k in range(rings):
                prog = ((self._t * 0.20 + k / rings + ci * 0.21) % 1.0)
                r = max_r * prog
                color = QColor(cols[ci % n])
                color.setAlpha(int((125 if self._dark else 100) * (1.0 - prog)))
                pen = QPen(color, 2.6, Qt.SolidLine)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                x, y = cx * w, cy * h
                painter.drawEllipse(QRectF(x - r, y - r, r * 2, r * 2))


class ParticleBackground(AnimatedBackground):
    """浮光粒子：光点缓慢上升并轻微横向摆动，循环再生。

    实现说明：本环境的 PySide6 在 paintEvent 高频循环里反复构造
    ``QBrush(QColor)`` / ``QRadialGradient`` 会触发 shiboken 引用计数崩溃
    （``none_dealloc``）。因此改为「预渲染柔光粒子贴图 + drawPixmap」：
    每种颜色只在初始化时生成一次贴图，绘制帧里只做 drawPixmap，既保留
    柔光晕外观，又彻底避开该崩溃，且帧率更稳。
    """

    effect_key = "particle"
    default_dark = ["#8ec5ff", "#d0b0ff", "#9affe0", "#ffd0b0"]
    default_light = ["#7ba6d8", "#a98fd0", "#6fbfae", "#d8a878"]
    _PIX_SIZE = 24  # 贴图尺寸（含柔光晕）

    def __init__(self, parent=None, dark=True, interval=50):
        super().__init__(parent, dark, interval)
        rng = random.Random(861230)
        self._parts = [
            (rng.random(), rng.random(), rng.uniform(2.0, 4.6),
             rng.uniform(0.5, 1.3), rng.uniform(0, math.tau))
            for _ in range(60)
        ]
        self._pix_cache = {}  # color_hex -> QPixmap，按当前 colors 惰性生成

    def _ensure_pixmaps(self, cols) -> None:
        """为每种粒子颜色预渲染一张柔光贴图（只做一次）。"""
        from PySide6.QtGui import QPixmap, QRadialGradient
        from PySide6.QtCore import QPointF
        for c in cols:
            if c in self._pix_cache:
                continue
            s = self._PIX_SIZE
            pm = QPixmap(s, s)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            grad = QRadialGradient(QPointF(s / 2, s / 2), s / 2)
            inner = QColor(c); inner.setAlpha(235)
            outer = QColor(c); outer.setAlpha(0)
            grad.setColorAt(0.0, inner)
            grad.setColorAt(1.0, outer)
            p.setBrush(QBrush(grad))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(0, 0, s, s))
            p.end()
            self._pix_cache[c] = pm

    def set_colors(self, colors) -> None:
        super().set_colors(colors)
        self._pix_cache.clear()  # 配色变了，贴图需重建

    def draw(self, painter, w, h):
        cols = self.colors
        n = len(cols) or 1
        self._ensure_pixmaps(cols)
        painter.setOpacity(1.0)
        for px, py, size, speed, phase in self._parts:
            prog = (py - self._t * 0.05 * speed) % 1.0
            y = prog * h
            x = ((px + 0.025 * math.sin(self._t * speed * 0.9 + phase)) % 1.0) * w
            color_hex = cols[int((px + py) * n) % n]
            d = int(size * 6)  # 光点直径（含柔光晕）
            # 两端淡出：用 painter 透明度，避免新建任何 brush
            painter.setOpacity(max(0.04, math.sin(prog * math.pi)))
            target = QRectF(x - d / 2, y - d / 2, d, d)
            src = QRectF(0, 0, self._pix_cache[color_hex].width(),
                         self._pix_cache[color_hex].height())
            painter.drawPixmap(target, self._pix_cache[color_hex], src)
        painter.setOpacity(1.0)


# 效果注册表：key -> 类（供插件与设置页使用）
EFFECTS = {
    cls.effect_key: cls
    for cls in (AuroraWaveBackground, StarfieldBackground,
                RippleBackground, ParticleBackground)
}
