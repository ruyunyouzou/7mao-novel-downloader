# -*- coding: utf-8 -*-
"""本地阅读统计页：基于本地数据展示阅读次数/进度分布/热门书籍。

图表：纯 Qt 折线图（近 N 天阅读次数），带渐变填充、数据点、网格线、坐标轴标签。
配色跟随全局主题色与深浅主题。
"""

from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QBrush, QLinearGradient, QPolygonF
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QGridLayout, QPushButton, QSizePolicy,
)

from ..datastore import DataStore
from ..i18n import tr
from .. import render_quality
from ..stats_core import build_stats
from ..style import accent_color, ACCENT
from ..widgets import apply_nation_icon, nation_icon


# 统计卡片：(i18n key, 国徽, 取值函数) —— 在 refresh 中动态构造
_STAT_CARDS = [
    ("shelf.read", "mondstadt"),
    ("extras.stat_days", "snezhnaya"),
    ("extras.stat_shelf", "liyue"),
    ("extras.stat_reading", "inazuma"),
    ("extras.stat_finished", "sumeru"),
    ("extras.stat_words", "fontaine"),
]


def _hex_alpha(color: str, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(alpha)
    return c


class _LineChart(QWidget):
    """纯 Qt 折线图（近 N 天阅读次数）。"""

    def __init__(self, data: List[Dict[str, Any]], color: str = ACCENT,
                 dark: bool = True, parent=None):
        super().__init__(parent)
        self.data = data
        self.color = color
        self.dark = dark
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        render_quality.apply_painter(p, text=True)
        w, h = self.width(), self.height()
        pad_l, pad_r, pad_t, pad_b = 34, 12, 14, 26
        cw, ch = w - pad_l - pad_r, h - pad_t - pad_b
        if cw <= 0 or ch <= 0:
            p.end()
            return

        dim = "#9a9a9a" if self.dark else "#888888"
        grid = "#3a3a3a" if self.dark else "#e6e6e6"
        vals = [int(d.get("count", 0)) for d in self.data]
        max_v = max(vals, default=0) or 1
        n = max(1, len(self.data))

        # ---- Y 轴网格线 + 刻度 ----
        p.setFont(QFont("Microsoft YaHei", 7))
        steps = 4
        for i in range(steps + 1):
            gy = pad_t + ch - int(ch * i / steps)
            p.setPen(QPen(QColor(grid), 1, Qt.DashLine))
            p.drawLine(pad_l, gy, pad_l + cw, gy)
            p.setPen(QColor(dim))
            p.drawText(0, gy - 6, pad_l - 5, 12, Qt.AlignRight,
                       str(int(max_v * i / steps)))

        if not self.data:
            p.setPen(QColor(dim))
            p.drawText(self.rect(), Qt.AlignCenter, tr("extras.stat_empty"))
            p.end()
            return

        # 数据点坐标
        def x_at(i: int) -> float:
            return pad_l + (cw if n == 1 else cw * i / (n - 1))

        def y_at(v: int) -> float:
            return pad_t + ch - ch * v / max_v

        pts = [QPointF(x_at(i), y_at(v)) for i, v in enumerate(vals)]
        poly = QPolygonF(pts)

        # ---- 折线下方的渐变填充 ----
        grad = QLinearGradient(0, pad_t, 0, pad_t + ch)
        grad.setColorAt(0, _hex_alpha(self.color, 110))
        grad.setColorAt(1, _hex_alpha(self.color, 8))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        fill = QPolygonF(pts)
        fill.append(QPointF(x_at(n - 1), pad_t + ch))
        fill.append(QPointF(x_at(0), pad_t + ch))
        p.drawPolygon(fill)

        # ---- 折线 ----
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(self.color), 2.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline(poly)

        # ---- 数据点 + 数值（非零、稀疏显示）----
        for i, (pt, v) in enumerate(zip(pts, vals)):
            if v:
                p.setBrush(QBrush(QColor(self.color)))
                p.setPen(QPen(QColor("#ffffff" if self.dark else "#ffffff"), 1.2))
                p.drawEllipse(pt, 3.5, 3.5)
                if i % 2 == 0:
                    p.setPen(QColor(dim))
                    p.drawText(pt.x() - 12, pt.y() - 10, 24, 12, Qt.AlignCenter, str(v))

        # ---- X 轴日期（隔几个显示，避免拥挤）----
        label_step = max(1, n // 7)
        for i, d in enumerate(self.data):
            if i % label_step and i != n - 1:
                continue
            day = str(d.get("day", ""))[5:]  # MM-DD
            p.setPen(QColor(dim))
            p.drawText(x_at(i) - 20, pad_t + ch + 6, 40, 14, Qt.AlignCenter, day)
        p.end()


def _stat_card(title: str, value: str, nation: str, color: str,
               dark: bool) -> QFrame:
    from .. import nation_theme
    card = QFrame()
    bg = nation_theme.resolved_card_bg("#242424" if dark else "#fafafa")
    bd = "#343434" if dark else "#e0e0e0"
    card.setStyleSheet(
        f"QFrame{{background:{bg};border:1px solid {bd};border-radius:10px;}}")
    l = QVBoxLayout(card)
    l.setContentsMargins(14, 10, 14, 12)
    l.setSpacing(4)

    head = QHBoxLayout()
    ico = QLabel()
    ico.setPixmap(nation_icon(nation, color, 18).pixmap(18, 18))
    ico.setStyleSheet("QLabel{border:none;background:transparent;padding:0;}")
    head.addWidget(ico)
    head.addStretch()
    l.addLayout(head)

    v = QLabel(value)
    v.setStyleSheet(
        f"QLabel{{color:{color};font-size:22px;font-weight:bold;"
        f"border:none;background:transparent;padding:0;}}")
    l.addWidget(v)
    t = QLabel(title)
    t.setStyleSheet(
        f"QLabel{{color:{'#9a9a9a' if dark else '#666'};font-size:12px;"
        f"border:none;background:transparent;padding:0;}}")
    l.addWidget(t)
    return card


class StatsView(QWidget):
    """阅读统计视图。"""

    def __init__(self, store: DataStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.dark = True
        self._accent = ACCENT
        self._load_palette()
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------ 配色
    def _load_palette(self) -> None:
        key = str(self.store.settings_get("accent_color", "purple") or "purple")
        self._accent = accent_color(key)
        mode = str(self.store.settings_get("theme_mode", "dark") or "dark")
        self.dark = (mode != "light")

    def apply_colors(self) -> None:
        """供主窗口在主题色/深浅主题切换后调用，重绘配色。"""
        self._load_palette()
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(16)

        head = QHBoxLayout()
        self.title_label = QLabel(tr("extras.stat_title"))
        head.addWidget(self.title_label)
        head.addStretch()
        self.btn_refresh = QPushButton(tr("search.refresh"))
        apply_nation_icon(self.btn_refresh, "liyue", self._accent, 16)
        self.btn_refresh.clicked.connect(self.refresh)
        head.addWidget(self.btn_refresh)
        lay.addLayout(head)

        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        lay.addLayout(self.grid)

        self.chart_card = QFrame()
        self.chart_card.setObjectName("chartCard")
        cl = QVBoxLayout(self.chart_card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)
        self.chart_label = QLabel(tr("extras.stat_daily"))
        cl.addWidget(self.chart_label)
        self.chart = _LineChart([], self._accent, self.dark)
        cl.addWidget(self.chart)
        lay.addWidget(self.chart_card)

        self.top_card = QFrame()
        self.top_card.setObjectName("topCard")
        tl = QVBoxLayout(self.top_card)
        tl.setContentsMargins(14, 12, 14, 12)
        self.top_label = QLabel(tr("extras.stat_top"))
        tl.addWidget(self.top_label)
        self.top_body = QLabel("")
        self.top_body.setWordWrap(True)
        tl.addWidget(self.top_body)
        lay.addWidget(self.top_card)

        lay.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll)
        self._apply_static_colors()

    def _apply_static_colors(self) -> None:
        """标题/卡片边框等静态配色。"""
        from .. import nation_theme
        txt = "#e6e6e6" if self.dark else "#2a2a2a"
        card_bg = nation_theme.resolved_card_bg("#242424" if self.dark else "#fafafa")
        bd = "#343434" if self.dark else "#e0e0e0"
        dim = "#9a9a9a" if self.dark else "#666"
        self.title_label.setStyleSheet(
            f"QLabel{{color:{self._accent};font-size:18px;font-weight:bold;"
            f"border:none;background:transparent;padding:0;}}")
        self.chart_label.setStyleSheet(
            f"QLabel{{color:{txt};font-weight:bold;border:none;background:transparent;}}")
        self.top_label.setStyleSheet(
            f"QLabel{{color:{txt};font-weight:bold;border:none;background:transparent;}}")
        self.top_body.setStyleSheet(
            f"QLabel{{color:{dim};line-height:160%;border:none;background:transparent;}}")
        for card in (self.chart_card, self.top_card):
            card.setStyleSheet(
                f"QFrame#chartCard, QFrame#topCard {{background:{card_bg};"
                f"border:1px solid {bd};border-radius:10px;}}")
        # 刷新按钮国徽图标跟随主题色
        apply_nation_icon(self.btn_refresh, "liyue", self._accent, 16)

    def retranslate(self) -> None:
        """语言切换时刷新静态标题（卡片区由 refresh 重建）。"""
        self.title_label.setText(tr("extras.stat_title"))
        self.btn_refresh.setText(tr("search.refresh"))
        self.chart_label.setText(tr("extras.stat_daily"))
        self.top_label.setText(tr("extras.stat_top"))
        self.refresh()

    def refresh(self) -> None:
        s = build_stats(self.store)
        self._load_palette()
        # 清空 grid：setParent(None) 同步移出，避免 deleteLater 在刷新时叠加卡片
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        cards = [
            (tr("shelf.read"), str(s["read_events"])),
            (tr("extras.stat_days"), str(s["active_days"])),
            (tr("extras.stat_shelf"), str(s["shelf_books"])),
            (tr("extras.stat_reading"), str(s["in_progress"])),
            (tr("extras.stat_finished"), str(s["finished_books"])),
            (tr("extras.stat_words"), f"{s['total_words']/10000:.0f}万" if s["total_words"] else "0"),
        ]
        for i, ((t, v), (key, nation)) in enumerate(zip(cards, _STAT_CARDS)):
            self.grid.addWidget(_stat_card(t, v, nation, self._accent, self.dark),
                                i // 3, i % 3)

        # 折线图重建数据 + 配色
        self.chart.data = s["daily"]
        self.chart.color = self._accent
        self.chart.dark = self.dark
        self.chart.update()

        if s["top_books"]:
            lines = []
            for i, b in enumerate(s["top_books"], 1):
                lines.append(f"{i}. {b['title']} — {b['count']} 次")
            self.top_body.setText("\n".join(lines))
        else:
            self.top_body.setText(tr("extras.stat_empty"))
        self._apply_static_colors()
