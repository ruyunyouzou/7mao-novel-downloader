# -*- coding: utf-8 -*-
"""纯 Qt 自绘的动态背景：缓慢流动的柔光渐变。

设计要点
========
- 不依赖任何外部引擎/资源，只用 QPainter + QTimer，跨平台、零额外依赖。
- 只绘制在自身（铺满内容区底层的独立 QWidget），不触碰任何控件样式。
- 事件透明（WA_TransparentForMouseEvents），不会拦截上层页面的鼠标操作。
- 支持暂停（省资源）与深色/浅色两套配色。

性能
====
- 定时器默认 60ms（约 16fps），对 CPU 占用很低。
- 不可见或暂停时完全停止重绘。
"""

from __future__ import annotations

import math
from typing import List, Tuple

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QPainter, QColor, QRadialGradient, QBrush
from PySide6.QtWidgets import QWidget

from . import render_quality


# (基准x, 基准y, 半径比例, 颜色, 相位, 速度)
_Blob = Tuple[float, float, float, str, float, float]

_DARK_BLOBS: List[_Blob] = [
    (0.18, 0.22, 0.85, "#3a2b63", 0.0, 1.0),   # 紫
    (0.82, 0.18, 0.80, "#1f4066", 1.7, 0.8),   # 蓝
    (0.70, 0.82, 0.90, "#5b2a52", 3.1, 0.6),   # 品红
    (0.25, 0.78, 0.75, "#2b4a55", 4.4, 1.2),   # 青蓝
]
_LIGHT_BLOBS: List[_Blob] = [
    (0.18, 0.22, 0.85, "#d9c9f2", 0.0, 1.0),
    (0.82, 0.18, 0.80, "#c4dcf5", 1.7, 0.8),
    (0.70, 0.82, 0.90, "#f0cfe4", 3.1, 0.6),
    (0.25, 0.78, 0.75, "#cfe6e6", 4.4, 1.2),
]

_DARK_BASE = "#17171f"
_LIGHT_BASE = "#f7f7f8"


class FlowGradientWidget(QWidget):
    """铺满内容区底层的流光渐变背景。"""

    def __init__(self, parent=None, dark: bool = True) -> None:
        super().__init__(parent)
        self._dark = bool(dark)
        self._paused = False
        self._t = 0.0
        self._blob_override = None  # 自定义光团色（由配色中心注入）
        self._blob_pix_cache = {}   # (color, dark) -> 预渲染柔光贴图
        self._timer = QTimer(self)
        self._timer.setInterval(80)  # ~12.5fps，低占用（渐变流动缓慢，肉眼无差）
        self._timer.timeout.connect(self._on_tick)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self._apply_timer()

    # ---------------- 对外控制 ----------------
    def set_dark(self, dark: bool) -> None:
        if self._dark != bool(dark):
            self._dark = bool(dark)
            # 主题变了，从配色中心重新取对应主题的光团色
            try:
                from gui import ui_background_theme as ub
                self.set_blob_colors(ub.flow_blob_colors(dark))
            except Exception:  # noqa: BLE001
                self._blob_override = None
            self._blob_pix_cache.clear()  # 主题变了，柔光贴图 peak alpha 不同
            self.update()

    def set_paused(self, paused: bool) -> None:
        """暂停动画（省资源）；暂停后保留当前画面。"""
        self._paused = bool(paused)
        self._apply_timer()

    def set_blob_colors(self, colors) -> None:
        """注入自定义光团颜色（4 色）；不足或非法时用默认值补齐。"""
        from PySide6.QtGui import QColor
        defaults = _DARK_BLOBS if self._dark else _LIGHT_BLOBS
        self._blob_override = []
        for i, default in enumerate(defaults):
            raw = colors[i] if colors and i < len(colors) else default[3]
            c = QColor(raw)
            self._blob_override.append(c.name() if c.isValid() else default[3])
        self._blob_pix_cache.clear()  # 颜色变了，贴图需重建
        self.update()

    def is_paused(self) -> bool:
        return self._paused

    def stop(self) -> None:
        """彻底停止定时器（程序退出时调用）。"""
        self._timer.stop()

    # ---------------- 内部 ----------------
    def _apply_timer(self) -> None:
        # 最小化 / 无可见窗口时停绘，避免后台持续占用主线程
        if self._paused or not self._window_active() or not self.isVisible():
            self._timer.stop()
        else:
            self._timer.start()

    @staticmethod
    def _window_active() -> bool:
        try:
            from PySide6.QtWidgets import QApplication
            wins = [w for w in QApplication.topLevelWidgets() if w.isVisible()]
            if not wins:
                return True
            return any(not w.isMinimized() for w in wins)
        except Exception:  # noqa: BLE001
            return True

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_timer()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._timer.stop()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        from PySide6.QtCore import QEvent
        if event.type() in (QEvent.WindowStateChange, QEvent.ActivationChange):
            self._apply_timer()

    def _on_tick(self) -> None:
        # 60ms 一帧、+0.022/帧，约 1.5 秒一个完整漂移相位，肉眼可见
        self._t += 0.022
        self.update()

    # 预渲染柔光贴图缓存：color -> QPixmap。渐变只生成一次，绘制帧只做 drawPixmap，
    # 避免每帧构造 4 个大半径 QRadialGradient（实测 4.7ms/帧的主线程热点）。
    _PIX_SIZE = 256

    def _ensure_blob_pixmaps(self, colors) -> None:
        from PySide6.QtGui import QPixmap
        cache = self._blob_pix_cache
        peak = 175 if self._dark else 150
        mid_alpha = 40
        for c in colors:
            key = (c, self._dark)
            if key in cache:
                continue
            s = self._PIX_SIZE
            pm = QPixmap(s, s)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            color = QColor(c)
            gradient = QRadialGradient(
                QRectF(0, 0, s, s).center(), s / 2)
            inner = QColor(color); inner.setAlpha(peak)
            mid = QColor(color.red(), color.green(), color.blue(), mid_alpha)
            outer = QColor(color); outer.setAlpha(0)
            gradient.setColorAt(0.0, inner)
            gradient.setColorAt(0.55, mid)
            gradient.setColorAt(1.0, outer)
            p.setBrush(QBrush(gradient))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(0, 0, s, s))
            p.end()
            cache[key] = pm

    def paintEvent(self, event) -> None:  # noqa: N802
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        painter = QPainter(self)
        render_quality.apply_painter(painter)

        # 底色
        base = QColor(_DARK_BASE if self._dark else _LIGHT_BASE)
        painter.fillRect(self.rect(), base)

        blobs = _DARK_BLOBS if self._dark else _LIGHT_BLOBS
        colors = [
            self._blob_override[i] if (self._blob_override and i < len(self._blob_override))
            else b[3]
            for i, b in enumerate(blobs)
        ]
        self._ensure_blob_pixmaps(colors)
        unit = float(min(w, h))
        for i, (bx, by, br, color, phase, speed) in enumerate(blobs):
            # 光团沿李萨如曲线缓慢漂移，幅度控制在内容区内
            cx = w * (0.5 + 0.32 * math.sin(self._t * speed + phase))
            cy = h * (0.5 + 0.30 * math.cos(self._t * speed * 0.83 + phase * 1.3))
            radius = unit * br
            pm = self._blob_pix_cache[(colors[i], self._dark)]
            # 柔光渐变本身无锐利边缘，放大时不需平滑插值（该项开销大）
            painter.drawPixmap(
                QRectF(cx - radius, cy - radius, radius * 2, radius * 2),
                pm, QRectF(0, 0, pm.width(), pm.height()))

        painter.end()
