# -*- coding: utf-8 -*-
"""
微信读书标签页：阅读数据仪表盘（复刻微信读书「我的阅读」界面）。

视觉：深紫底 + 橙色主色，卡片式布局。
数据：全部来自官方 Agent Gateway（wrk- API Key），只读。
  - /readdata/detail  统计 / 偏好分类 / 24 小时时段
  - /shelf/sync + /book/getprogress  继续阅读
  - /user/notebooks + /book/bookmarklist  划线
导入：网页 Cookie 传书（COS 直传 + notify），把七猫已下载的书推到微信读书。
凭据保存在 DataStore.settings（~/.qimao_gui/settings.json），仅存本机。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal, QRectF, QTimer, QUrl
from PySide6.QtGui import QPainter, QColor, QFont, QPixmap, QLinearGradient
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton, QLineEdit, QTextEdit, QMessageBox, QFileDialog, QScrollArea, QGroupBox, QProgressBar

from ..datastore import DataStore
from ..i18n import tr
from .. import render_quality
from ..style import accent_color
from ..weread_core import WeReadClient, WeReadUploader, build_dashboard, greeting_by_time, SUPPORTED_FORMATS, MAX_SIZE_MB
from ..weread_login import WeReadMobileSession, clear_session, load_session, save_session
from ..weread_login_dialog import WereadLoginDialog
from ..widgets import GroupTitleLabel, apply_nation_icon

# ---- 微信读书仪表盘配色：跟随主程序深浅主题 + 主题色 ----
# 下列常量是"默认深色"值；主题切换时由 _apply_palette() 通过 global 重新赋值，
# 所有以 WR_* 拼接的样式串与自绘控件会随之更新。
WR_BG = "#17131d"
WR_CARD = "#241e2e"
WR_CARD2 = "#2b2438"
WR_ORANGE = "#ff6b00"
WR_ORANGE_L = "#ff9d4d"
WR_HERO_A = "#ff6b00"   # 继续阅读卡渐变起点（浅色主题下自动加深）
WR_HERO_B = "#ff9d4d"   # 继续阅读卡渐变终点
WR_TEXT = "#f5f0fa"
WR_DIM = "#a89fb5"
WR_FAINT = "#7a7288"
WR_LINE = "#3a3048"
WR_BAR_IDLE = "#4a3d5e"
WR_HOVER = "#3a3048"
WR_TAG_BG = "#3a2a1c"
WR_TAG_LINE = "#5a3d1c"


def _palette(dark: bool, accent: str) -> Dict[str, str]:
    """按主程序深浅主题 + 主题色生成一整套仪表盘配色。"""
    from .. import nation_theme
    a = QColor(accent)
    a_l = a.lighter(135).name() if dark else a.darker(118).name()
    a_d = a.darker(115).name()
    if dark:
        p = dict(
            bg="#17131d", card="#241e2e", card2="#2b2438",
            accent=accent, accent_l=a_l, accent_d=a_d,
            text="#f5f0fa", dim="#a89fb5", faint="#7a7288", line="#3a3048",
            bar_idle="#4a3d5e", hover="#3a3048",
            tag_bg=_mix("#241e2e", accent, 0.20), tag_line=_mix("#241e2e", accent, 0.38),
            hero_a=accent, hero_b=a_l,
        )
    else:
        p = dict(
            bg="#f4f2f7", card="#ffffff", card2="#efeaf3",
            accent=accent, accent_l=a_l, accent_d=a_d,
            text="#241f2c", dim="#6e6679", faint="#9a92a5", line="#ddd8e3",
            bar_idle="#d8d2e0", hover="#eeebf3",
            tag_bg=_mix("#ffffff", accent, 0.12), tag_line=_mix("#ffffff", accent, 0.30),
            # 浅色主题下 hero 卡仍需深底，否则白字不可读；色相跟随主题色，仅加深
            hero_a=a.darker(150).name(), hero_b=a.darker(120).name(),
        )
    # 功能背景：自定义卡片色 + 派生层次色，且 tag 派生色随新 card 重算
    new_card, new_card2 = nation_theme.resolved_card_pair(dark, p["card"], p["card2"])
    if new_card != p["card"]:
        p["tag_bg"] = _mix(new_card, accent, 0.20 if dark else 0.12)
        p["tag_line"] = _mix(new_card, accent, 0.38 if dark else 0.30)
    p["card"], p["card2"] = new_card, new_card2
    return p


def _mix(base: str, other: str, ratio: float) -> str:
    """按 ratio 把 other 混入 base，返回 #rrggbb。"""
    b = QColor(base)
    o = QColor(other)
    return QColor(
        int(b.red() * (1 - ratio) + o.red() * ratio),
        int(b.green() * (1 - ratio) + o.green() * ratio),
        int(b.blue() * (1 - ratio) + o.blue() * ratio),
    ).name()


def _apply_palette(dark: bool, accent: str) -> Dict[str, str]:
    """重新赋值模块级 WR_* 配色，供自绘控件与后续新建控件读取。"""
    global WR_BG, WR_CARD, WR_CARD2, WR_ORANGE, WR_ORANGE_L
    global WR_TEXT, WR_DIM, WR_FAINT, WR_LINE, WR_BAR_IDLE, WR_HOVER
    global WR_TAG_BG, WR_TAG_LINE, WR_HERO_A, WR_HERO_B
    p = _palette(dark, accent)
    WR_BG = p["bg"]
    WR_CARD = p["card"]
    WR_CARD2 = p["card2"]
    WR_ORANGE = p["accent"]
    WR_ORANGE_L = p["accent_l"]
    WR_TEXT = p["text"]
    WR_DIM = p["dim"]
    WR_FAINT = p["faint"]
    WR_LINE = p["line"]
    WR_BAR_IDLE = p["bar_idle"]
    WR_HOVER = p["hover"]
    WR_TAG_BG = p["tag_bg"]
    WR_TAG_LINE = p["tag_line"]
    WR_HERO_A = p["hero_a"]
    WR_HERO_B = p["hero_b"]
    return p


class CoverLabel(QLabel):
    """书籍封面：优先加载网络封面，失败时用「书名首字」渐变占位图。

    纯 Qt 实现，无外部图片依赖；主题色变化时可重建占位图。
    """

    def __init__(self, title: str, url: str = "", size: tuple = (44, 60),
                 radius: int = 6, parent=None) -> None:
        super().__init__(parent)
        self._title = title or "书"
        self._url = url or ""
        self._w, self._h = size
        self._radius = radius
        self.setFixedSize(self._w, self._h)
        self.setScaledContents(True)
        self.setAlignment(Qt.AlignCenter)
        self._mgr: Optional[QNetworkAccessManager] = None
        self._set_placeholder()
        if self._url.startswith("http"):
            self._load()

    def _set_placeholder(self) -> None:
        pm = QPixmap(self._w * 2, self._h * 2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        render_quality.apply_painter(p, text=True, smooth=True)
        grad = QLinearGradient(0, 0, pm.width(), pm.height())
        a = QColor(WR_ORANGE)
        grad.setColorAt(0.0, QColor(
            int(a.red() * 0.45 + 30), int(a.green() * 0.40 + 26), int(a.blue() * 0.5 + 40)))
        grad.setColorAt(1.0, QColor(
            int(a.red() * 0.28 + 20), int(a.green() * 0.25 + 18), int(a.blue() * 0.35 + 32)))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(pm.rect(), self._radius * 2, self._radius * 2)
        f = QFont()
        f.setPointSize(max(11, self._w // 3))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(pm.rect(), Qt.AlignCenter, self._title[:1])
        p.end()
        self.setPixmap(pm)
        self.setToolTip(self._title)

    def _load(self) -> None:
        try:
            self._mgr = QNetworkAccessManager(self)
            self._mgr.finished.connect(self._on_loaded)
            self._mgr.get(QNetworkRequest(QUrl(self._url)))
        except Exception:  # noqa: BLE001
            pass

    def _on_loaded(self, reply) -> None:
        try:
            data = bytes(reply.readAll())
            pm = QPixmap()
            if pm.loadFromData(data) and not pm.isNull():
                self.setPixmap(pm)
        except Exception:  # noqa: BLE001
            pass
        finally:
            reply.deleteLater()

    def refresh_placeholder(self) -> None:
        """主题色变化后重绘占位图（已成功加载真实封面的不覆盖）。"""
        if not self._url:
            self._set_placeholder()

    def set_book(self, title: str, url: str = "") -> None:
        """复用本控件更新书籍封面，避免反复创建/销毁导致布局错乱。

        相比在父布局里 takeAt + deleteLater 再插入新控件，这种方式
        不会触发「setParent(None) 后 widget() 变 None」的问题，也不会
        在无事件循环时残留旧控件。
        """
        self._title = title or "书"
        self._url = url or ""
        if self._mgr is not None:
            try:
                self._mgr.deleteLater()
            except RuntimeError:  # C++ 对象已被销毁
                pass
            self._mgr = None
        self._set_placeholder()
        if self._url.startswith("http"):
            self._load()


def _fmt_count(value: Any) -> str:
    """大数字缩写：≥1 亿用「亿」，≥1 万用「万」，最多保留 1 位小数。

    非纯数字（接口已返回 "1.2万" 之类）原样返回，避免二次缩写。
    """
    s = str(value if value is not None else "").strip()
    cleaned = s.replace(",", "").replace("，", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        return s
    try:
        num = float(cleaned)
    except ValueError:
        return s
    if num >= 1e8:
        text = f"{num / 1e8:.1f}".rstrip("0").rstrip(".")
        return text + tr("weread.unit_yi")
    if num >= 1e4:
        text = f"{num / 1e4:.1f}".rstrip("0").rstrip(".")
        return text + tr("weread.unit_wan")
    return s


def _lbl(text: str = "", color: str = WR_TEXT, size: int = 13,
         bold: bool = False, wrap: bool = False) -> QLabel:
    q = QLabel(text)
    css = f"color:{color};font-size:{size}px;background:transparent;border:none;padding:0;"
    if bold:
        css += "font-weight:bold;"
    q.setStyleSheet(css)
    q.setWordWrap(wrap)
    return q


def _ghost_btn(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setStyleSheet(
        f"QPushButton{{background-color:{WR_CARD2};color:{WR_TEXT};"
        f"border:1px solid {WR_LINE};border-radius:8px;padding:6px 14px;}}"
        f"QPushButton:hover{{background-color:#3a3048;}}"
        f"QPushButton:disabled{{color:{WR_FAINT};}}"
    )
    return b


# ------------------------------------------------------------------ 自绘柱状图

class BarChart(QWidget):
    """轻量柱状图：data = [(label, value), ...]。"""

    def __init__(self, color: str = WR_ORANGE, highlight: int = -1,
                 show_value: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.data: List[tuple] = []
        self.color = color
        self.highlight = highlight
        self.show_value = show_value
        self.setMinimumHeight(96)

    def set_data(self, data: List[tuple]) -> None:
        self.data = data or []
        self.update()

    def apply_colors(self) -> None:
        """主题切换后同步柱色并重绘。"""
        self.color = WR_ORANGE
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        render_quality.apply_painter(p, text=True)
        w, h = self.width(), self.height()
        n = len(self.data)
        if not n or w < 10:
            return
        label_h, value_h = 18, 15
        chart_top = value_h + 2
        chart_h = max(8, h - label_h - chart_top - 2)
        values = [v for _, v in self.data]
        maxv = max(values) if max(values) > 0 else 1
        slot = w / n
        bar_w = min(slot * 0.62, 22)
        for i, (label, val) in enumerate(self.data):
            cx = slot * (i + 0.5)
            bh = max(3, chart_h * (val / maxv)) if val > 0 else 3
            y = chart_top + chart_h - bh
            x = cx - bar_w / 2
            hot = (i == self.highlight)
            col = QColor(WR_ORANGE_L if hot else self.color) if self.show_value else QColor(self.color)
            if not hot and self.show_value:
                col = QColor(WR_BAR_IDLE)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(x, y, bar_w, bh), 3, 3)
            # 数值
            if self.show_value and val > 0:
                p.setPen(QColor(WR_ORANGE_L if hot else WR_DIM))
                f = QFont(); f.setPointSize(7)
                p.setFont(f)
                p.drawText(QRectF(x - 6, y - value_h, bar_w + 12, value_h),
                           Qt.AlignCenter, str(int(val)))
            # 标签
            if label:
                p.setPen(QColor(WR_DIM if hot else WR_FAINT))
                f = QFont(); f.setPointSize(7)
                p.setFont(f)
                p.drawText(QRectF(cx - slot / 2, h - label_h, slot, label_h),
                           Qt.AlignCenter, label)


class Sparkline(QWidget):
    """迷你折线图：data = [(label, value), ...]，纯 Qt 绘制，带渐变填充。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.data: List[tuple] = []
        self.setMinimumHeight(62)
        self.setMaximumHeight(84)

    def set_data(self, data: List[tuple]) -> None:
        self.data = data or []
        self.update()

    def apply_colors(self) -> None:
        self.update()

    def paintEvent(self, event) -> None:
        if len(self.data) < 2:
            return
        p = QPainter(self)
        render_quality.apply_painter(p, text=True)
        w, h = self.width(), self.height()
        pad = 6
        vals = [float(v) for _, v in self.data]
        maxv = max(vals) or 1
        n = len(vals)
        xs = [pad + (w - 2 * pad) * i / (n - 1) for i in range(n)]
        ys = [h - pad - (h - 2 * pad) * (v / maxv) for v in vals]
        # 渐变填充
        from PySide6.QtGui import QPolygonF, QBrush
        from PySide6.QtCore import QPointF
        poly = QPolygonF([QPointF(xs[0], h - pad)]
                         + [QPointF(x, y) for x, y in zip(xs, ys)]
                         + [QPointF(xs[-1], h - pad)])
        grad = QLinearGradient(0, 0, 0, h)
        ga = QColor(WR_ORANGE)
        ga.setAlpha(70)
        gb = QColor(WR_ORANGE)
        gb.setAlpha(0)
        grad.setColorAt(0.0, ga)
        grad.setColorAt(1.0, gb)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawPolygon(poly)
        # 折线
        line = QPolygonF([QPointF(x, y) for x, y in zip(xs, ys)])
        pen = QColor(WR_ORANGE_L)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(line)
        # 数据点 + 数值
        p.setBrush(QColor(WR_ORANGE_L))
        p.setPen(Qt.NoPen)
        for x, y, v in zip(xs, ys, vals):
            p.drawEllipse(QRectF(x - 2.5, y - 2.5, 5, 5))
        # x 轴标签（只显示首/尾/中间）
        p.setPen(QColor(WR_FAINT))
        f = QFont(); f.setPointSize(7)
        p.setFont(f)
        for i in (0, n // 2, n - 1):
            if i < len(self.data):
                label = str(self.data[i][0])
                tw = p.fontMetrics().horizontalAdvance(label)
                x = max(0, min(w - tw, xs[i] - tw / 2))
                p.drawText(QRectF(x, h - 12, tw, 12), Qt.AlignCenter, label)


# ------------------------------------------------------------------ 卡片组件

class StatCard(QFrame):
    """统计小卡：图标 + 数值 + 单位 + 标签。"""

    def __init__(self, icon: str, label: str, parent=None) -> None:
        super().__init__(parent)
        self._icon = icon
        self._raw_value = "—"
        self._unit = ""
        self.setObjectName("wrStatCard")
        self.setStyleSheet(
            f"QFrame#wrStatCard{{background-color:{WR_CARD};border:none;border-radius:12px;}}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(2)
        top = QHBoxLayout()
        from ..widgets import nation_icon
        self.ico_lbl = QLabel()
        self.ico_lbl.setPixmap(nation_icon(icon, WR_DIM, 18).pixmap(18, 18))
        top.addWidget(self.ico_lbl)
        top.addStretch()
        lay.addLayout(top)
        mid = QHBoxLayout()
        mid.setSpacing(2)
        self.value_lbl = _lbl("—", WR_TEXT, 20, bold=True)
        self.unit_lbl = _lbl("", WR_DIM, 11)
        mid.addWidget(self.value_lbl)
        mid.addWidget(self.unit_lbl)
        mid.addStretch()
        lay.addLayout(mid)
        self.label_lbl = _lbl(label, WR_DIM, 11)
        lay.addWidget(self.label_lbl)

    def set_value(self, value: str, unit: str) -> None:
        self._raw_value = str(value if value is not None else "")
        self._unit = str(unit or "")
        text = _fmt_count(self._raw_value)
        # 字号自适应：内容越长字号越小，避免撑破卡片
        n = len(text)
        size = 20 if n <= 5 else (17 if n <= 7 else (14 if n <= 9 else 12))
        self.value_lbl.setStyleSheet(
            f"color:{WR_TEXT};font-size:{size}px;font-weight:bold;background:transparent;border:none;padding:0;"
        )
        self.value_lbl.setText(text)
        self.unit_lbl.setText(self._unit)
        # 完整值悬停可见
        full = self._raw_value + self._unit
        self.value_lbl.setToolTip(full)
        self.unit_lbl.setToolTip(full)

    def apply_colors(self) -> None:
        """主题切换后刷新卡片背景、图标、文字色并重新计算数值显示。"""
        from ..widgets import nation_icon
        self.setStyleSheet(
            f"QFrame#wrStatCard{{background-color:{WR_CARD};border:none;border-radius:12px;}}"
        )
        self.ico_lbl.setPixmap(nation_icon(self._icon, WR_DIM, 18).pixmap(18, 18))
        self.unit_lbl.setStyleSheet(f"color:{WR_DIM};font-size:11px;background:transparent;border:none;padding:0;")
        self.label_lbl.setStyleSheet(f"color:{WR_DIM};font-size:11px;background:transparent;border:none;padding:0;")
        self.set_value(self._raw_value, self._unit)


class OrangeHero(QFrame):
    """继续阅读大橙卡，带半透明装饰圆。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(190)
        self._book_data: Optional[Dict[str, Any]] = None
        self.title_lbl = _lbl(tr("weread.hero_title"), "#ffe4cc", 12, bold=True)
        self.book_lbl = _lbl(tr("weread.hero_empty"), "#ffffff", 22, bold=True, wrap=True)
        self.desc_lbl = _lbl(tr("weread.hero_empty_desc"),
                             "#ffe0c2", 12, wrap=True)
        # 左侧封面缩略图（方案 B）
        self.cover_lbl = CoverLabel("书", "", size=(56, 78), radius=7)
        self.cover_lbl.hide()

        txt = QVBoxLayout()
        txt.setSpacing(6)
        txt.addWidget(self.title_lbl)
        txt.addStretch()
        txt.addWidget(self.book_lbl)
        txt.addWidget(self.desc_lbl)
        txt.addStretch()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(18)
        lay.addWidget(self.cover_lbl, 0, Qt.AlignVCenter)
        lay.addLayout(txt, 1)

    def set_book(self, data: Optional[Dict[str, Any]]) -> None:
        self._book_data = data
        if not data:
            self.cover_lbl.hide()
            self.book_lbl.setText(tr("weread.hero_empty"))
            self.desc_lbl.setText(tr("weread.hero_empty_desc"))
        else:
            title = str(data.get("title") or tr("weread.continue_reading"))
            # 复用 __init__ 里已加入布局的封面控件，仅更新内容
            self.cover_lbl.set_book(title, str(data.get("cover") or ""))
            self.cover_lbl.show()
            self.book_lbl.setText(title)
            prog = int(data.get("progress") or 0)
            self.desc_lbl.setText(tr("weread.hero_desc",
                                     author=str(data.get("author") or ""), progress=prog))

    def retranslate(self) -> None:
        """用已缓存的数据重绘静态文案（不触发网络）。"""
        self.title_lbl.setText(tr("weread.hero_title"))
        self.set_book(self._book_data)

    def apply_colors(self) -> None:
        """主题切换后刷新文字色并重绘渐变。"""
        self.title_lbl.setStyleSheet(
            "color:#ffe4cc;font-size:12px;font-weight:bold;background:transparent;border:none;padding:0;")
        self.book_lbl.setStyleSheet(
            "color:#ffffff;font-size:22px;font-weight:bold;background:transparent;border:none;padding:0;")
        self.desc_lbl.setStyleSheet(
            "color:#ffe0c2;font-size:12px;background:transparent;border:none;padding:0;")
        self.cover_lbl.refresh_placeholder()
        self.update()

    def paintEvent(self, event) -> None:
        # QFrame 自身不画背景，由这里自绘渐变 + 装饰圆
        p = QPainter(self)
        render_quality.apply_painter(p)
        w, h = self.width(), self.height()
        # 渐变底：跟随主题色（浅色主题下已自动加深，保证白字可读）
        a = QColor(WR_HERO_A)
        a2 = QColor(WR_HERO_B)
        grad = QLinearGradient(0, 0, w, h)
        grad.setColorAt(0.0, a)
        grad.setColorAt(1.0, a2)
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(0, 0, w, h), 14, 14)
        # 半透明装饰圆（参考图右上、底部各一个）
        p.setBrush(QColor(255, 255, 255, 22))
        p.drawEllipse(QRectF(w * 0.60, -h * 0.5, h * 1.3, h * 1.3))
        p.setBrush(QColor(255, 255, 255, 15))
        p.drawEllipse(QRectF(w * 0.80, h * 0.5, h * 0.9, h * 0.9))


class RhythmCard(QFrame):
    """阅读节奏卡：今日分钟 / 目标档位 / 进度条 / 7 日柱状图。"""

    goal_changed = Signal(int)

    def __init__(self, goal: int = 20, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("wrPanel")
        self.setStyleSheet(
            f"QFrame#wrPanel{{background-color:{WR_CARD};border:1px solid {WR_LINE};border-radius:14px;}}"
        )
        self.goal = goal
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(8)

        self._data: Optional[tuple] = None
        head = QHBoxLayout()
        self.title_lbl = GroupTitleLabel(tr("weread.rhythm_title"), "natlan", WR_ORANGE_L, font_size=14)
        self.title_lbl._nation_fixed_color = True
        head.addWidget(self.title_lbl)
        head.addStretch()
        self.hint_lbl = _lbl(tr("weread.rhythm_hint"), WR_FAINT, 10)
        head.addWidget(self.hint_lbl)
        lay.addLayout(head)

        self.today_lbl = _lbl("0", WR_ORANGE, 30, bold=True)
        mid = QHBoxLayout()
        self.today_caption_lbl = _lbl(tr("weread.today_read"), WR_DIM, 12)
        mid.addWidget(self.today_caption_lbl)
        mid.addSpacing(8)
        mid.addWidget(self.today_lbl)
        self.goal_lbl = _lbl(tr("weread.goal", goal=self.goal), WR_DIM, 12)
        mid.addWidget(self.goal_lbl)
        mid.addStretch()
        lay.addLayout(mid)

        gears = QHBoxLayout()
        gears.setSpacing(6)
        self.gear_btns: List[QPushButton] = []
        for g in (10, 20, 30, 60):
            b = QPushButton(tr("weread.gear", n=g))
            b.setProperty("goal", g)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedWidth(46)
            b.clicked.connect(lambda _, v=g: self._set_goal(v))
            self.gear_btns.append(b)
            gears.addWidget(b)
        gears.addStretch()
        lay.addLayout(gears)

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setValue(0)
        self.bar.setStyleSheet(
            f"QProgressBar{{background-color:{WR_CARD2};border:none;border-radius:4px;}}"
            f"QProgressBar::chunk{{background-color:{WR_ORANGE};border-radius:4px;}}"
        )
        lay.addWidget(self.bar)

        self.chart = BarChart(highlight=6, show_value=True)
        lay.addWidget(self.chart)

        self.week_lbl = _lbl(tr("weread.week", total=0, days=0), WR_DIM, 11)
        lay.addWidget(self.week_lbl)
        self._refresh_gears()

    def _refresh_gears(self) -> None:
        for b in self.gear_btns:
            val = int(b.property("goal"))
            if val == self.goal:
                b.setStyleSheet(
                    f"QPushButton{{background-color:{WR_ORANGE};color:#fff;"
                    "border:none;border-radius:12px;padding:3px 0;font-weight:bold;}"
                )
            else:
                b.setStyleSheet(
                    f"QPushButton{{background-color:{WR_CARD2};color:{WR_DIM};"
                    "border:1px solid " + WR_LINE + ";border-radius:12px;padding:3px 0;}"
                )

    def _set_goal(self, value: int) -> None:
        self.goal = value
        self.goal_lbl.setText(tr("weread.goal", goal=value))
        self._refresh_gears()
        self.goal_changed.emit(value)

    def set_data(self, today: int, week_total: int, week_days: int,
                 bars: List[tuple]) -> None:
        self._data = (today, week_total, week_days, bars)
        self.today_lbl.setText(str(today))
        self.bar.setValue(min(100, int(today * 100 / max(self.goal, 1))))
        self.chart.set_data(bars)
        self.week_lbl.setText(tr("weread.week", total=week_total, days=week_days))

    def retranslate(self) -> None:
        """用已缓存的数据重绘静态文案（不触发网络）。"""
        self.title_lbl.setText(tr("weread.rhythm_title"))
        self.hint_lbl.setText(tr("weread.rhythm_hint"))
        self.today_caption_lbl.setText(tr("weread.today_read"))
        self.goal_lbl.setText(tr("weread.goal", goal=self.goal))
        for b in self.gear_btns:
            b.setText(tr("weread.gear", n=b.property("goal")))
        if self._data is not None:
            self.set_data(*self._data)
        else:
            self.week_lbl.setText(tr("weread.week", total=0, days=0))

    def apply_colors(self) -> None:
        """主题切换后刷新整卡配色。"""
        self.setStyleSheet(
            f"QFrame#wrPanel{{background-color:{WR_CARD};"
            f"border:1px solid {WR_LINE};border-radius:14px;}}")
        self.title_lbl.set_icon_color(WR_ORANGE_L)
        self.hint_lbl.setStyleSheet(f"color:{WR_FAINT};font-size:10px;background:transparent;border:none;padding:0;")
        self.today_lbl.setStyleSheet(
            f"color:{WR_ORANGE};font-size:30px;font-weight:bold;background:transparent;border:none;padding:0;")
        self.today_caption_lbl.setStyleSheet(f"color:{WR_DIM};font-size:12px;background:transparent;border:none;padding:0;")
        self.goal_lbl.setStyleSheet(f"color:{WR_DIM};font-size:12px;background:transparent;border:none;padding:0;")
        self.week_lbl.setStyleSheet(f"color:{WR_DIM};font-size:11px;background:transparent;border:none;padding:0;")
        self.bar.setStyleSheet(
            f"QProgressBar{{background-color:{WR_CARD2};border:none;border-radius:4px;}}"
            f"QProgressBar::chunk{{background-color:{WR_ORANGE};border-radius:4px;}}")
        self._refresh_gears()
        self.chart.apply_colors()


class HighlightCard(QFrame):
    """重温划线卡：换一条 / 看笔记。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("wrPanel")
        self.setStyleSheet(
            f"QFrame#wrPanel{{background-color:{WR_CARD};border:1px solid {WR_LINE};border-radius:14px;}}"
        )
        self.highlights: List[Dict[str, Any]] = []
        self.idx = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 12)
        lay.setSpacing(6)
        head = QHBoxLayout()
        self.title_lbl = _lbl(tr("weread.highlight_title"), WR_TEXT, 14, bold=True)
        head.addWidget(self.title_lbl)
        head.addStretch()
        lay.addLayout(head)
        lay.addWidget(_lbl("“", WR_ORANGE, 34, bold=True))
        self.text_lbl = _lbl(tr("weread.highlight_empty"), WR_TEXT, 15, wrap=True)
        self.text_lbl.setMinimumHeight(46)
        lay.addWidget(self.text_lbl)
        lay.addStretch()
        self.source_lbl = _lbl("——", WR_DIM, 11)
        lay.addWidget(self.source_lbl)
        actions = QHBoxLayout()
        self.btn_note = _ghost_btn(tr("weread.btn_note"))
        self.btn_next = _ghost_btn(tr("weread.btn_next"))
        self.btn_note.clicked.connect(self._open_note)
        self.btn_next.clicked.connect(self._next)
        actions.addWidget(self.btn_note)
        actions.addWidget(self.btn_next)
        actions.addStretch()
        lay.addLayout(actions)
        self.reader_link = ""

    def retranslate(self) -> None:
        """用已缓存的划线重绘静态文案（不触发网络）。"""
        self.title_lbl.setText(tr("weread.highlight_title"))
        self.btn_note.setText(tr("weread.btn_note"))
        self.btn_next.setText(tr("weread.btn_next"))
        self._render()

    def apply_colors(self) -> None:
        """主题切换后刷新整卡配色。"""
        self.setStyleSheet(
            f"QFrame#wrPanel{{background-color:{WR_CARD};"
            f"border:1px solid {WR_LINE};border-radius:14px;}}")
        self.title_lbl.setStyleSheet(
            f"color:{WR_TEXT};font-size:14px;font-weight:bold;background:transparent;border:none;padding:0;")
        for w in self.findChildren(QLabel):
            if w is self.title_lbl:
                continue
            if w.text() == "“":
                w.setStyleSheet(f"color:{WR_ORANGE};font-size:34px;font-weight:bold;background:transparent;border:none;padding:0;")
        self.text_lbl.setStyleSheet(f"color:{WR_TEXT};font-size:15px;background:transparent;border:none;padding:0;")
        self.source_lbl.setStyleSheet(f"color:{WR_DIM};font-size:11px;background:transparent;border:none;padding:0;")
        for b in (self.btn_note, self.btn_next):
            b.setStyleSheet(
                f"QPushButton{{background-color:{WR_CARD2};color:{WR_TEXT};"
                f"border:1px solid {WR_LINE};border-radius:8px;padding:6px 14px;}}"
                f"QPushButton:hover{{background-color:{WR_HOVER};}}"
                f"QPushButton:disabled{{color:{WR_FAINT};}}")

    def set_highlights(self, items: List[Dict[str, Any]], reader_link: str = "") -> None:
        self.highlights = items or []
        self.reader_link = reader_link
        self.idx = 0
        self._render()

    def _render(self) -> None:
        if not self.highlights:
            self.text_lbl.setText(tr("weread.highlight_empty"))
            self.source_lbl.setText("——")
            self.btn_note.setEnabled(False)
            self.btn_next.setEnabled(False)
            return
        self.btn_note.setEnabled(True)
        self.btn_next.setEnabled(len(self.highlights) > 1)
        h = self.highlights[self.idx % len(self.highlights)]
        text = str(h.get("text") or "")
        self.text_lbl.setText(text[:120] + ("…" if len(text) > 120 else ""))
        title = str(h.get("title") or "")
        author = str(h.get("author") or "")
        self.source_lbl.setText("——《" + title + "》" + (f"　{author}" if author else ""))

    def _next(self) -> None:
        if self.highlights:
            self.idx = (self.idx + 1) % len(self.highlights)
            self._render()

    def _open_note(self) -> None:
        if self.reader_link:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(self.reader_link))


class PreferenceCard(QFrame):
    """阅读偏好卡：偏好标签 + 分类横条 + 24 小时柱状图。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("wrPanel")
        self.setMinimumHeight(340)
        self.setStyleSheet(
            f"QFrame#wrPanel{{background-color:{WR_CARD};border:1px solid {WR_LINE};border-radius:14px;}}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 12)
        lay.setSpacing(8)
        self._data: Optional[tuple] = None
        head = QHBoxLayout()
        self.title_lbl = GroupTitleLabel(tr("weread.pref_title"), "snezhnaya", WR_ORANGE_L, font_size=14)
        self.title_lbl._nation_fixed_color = True
        head.addWidget(self.title_lbl)
        head.addStretch()
        lay.addLayout(head)

        self.tags_row = QHBoxLayout()
        self.tags_row.setSpacing(6)
        lay.addLayout(self.tags_row)

        self.cats_box = QVBoxLayout()
        self.cats_box.setSpacing(6)
        lay.addLayout(self.cats_box)

        self.time_lbl = _lbl(tr("weread.pref_time"), WR_DIM, 11)
        lay.addWidget(self.time_lbl)
        self.chart = BarChart(color=WR_ORANGE, show_value=False)
        self.chart.setMinimumHeight(76)
        lay.addWidget(self.chart)

    def retranslate(self) -> None:
        """用已缓存的偏好数据重绘静态文案（不触发网络）。"""
        self.title_lbl.setText(tr("weread.pref_title"))
        self.time_lbl.setText(tr("weread.pref_time"))
        if self._data is not None:
            self.set_data(*self._data)

    def apply_colors(self) -> None:
        """主题切换后刷新整卡配色（动态内容用缓存数据重建）。"""
        self.setStyleSheet(
            f"QFrame#wrPanel{{background-color:{WR_CARD};"
            f"border:1px solid {WR_LINE};border-radius:14px;}}")
        self.title_lbl.set_icon_color(WR_ORANGE_L)
        self.time_lbl.setStyleSheet(f"color:{WR_DIM};font-size:11px;background:transparent;border:none;padding:0;")
        self.chart.apply_colors()
        if self._data is not None:
            self.set_data(*self._data)

    def set_data(self, titles: List[str], categories: List[tuple], time_bars: List[tuple]) -> None:
        self._data = (titles, categories, time_bars)
        # 偏好标签
        while self.tags_row.count():
            item = self.tags_row.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        for t in titles[:2]:
            tag = QLabel(f"◈ {t}")
            tag.setStyleSheet(
                f"color:{WR_ORANGE_L};background-color:{WR_TAG_BG};"
                f"border:1px solid {WR_TAG_LINE};border-radius:10px;padding:3px 9px;font-size:11px;"
            )
            self.tags_row.addWidget(tag)
        self.tags_row.addStretch()

        # 分类横条
        while self.cats_box.count():
            item = self.cats_box.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        for name, hours, weight in categories[:5]:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(_lbl(name, WR_DIM, 11), 0)
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setFixedHeight(7)
            bar.setMaximum(100)
            bar.setValue(int(max(0, min(100, weight))))
            bar.setStyleSheet(
                f"QProgressBar{{background-color:{WR_CARD2};border:none;border-radius:4px;}}"
                f"QProgressBar::chunk{{background-color:{WR_ORANGE};border-radius:4px;}}"
            )
            row.addWidget(bar, 1)
            row.addWidget(_lbl(tr("weread.hours", h=hours), WR_TEXT, 11), 0)
            wrap = QWidget()
            wrap.setLayout(row)
            self.cats_box.addWidget(wrap)
        if not categories:
            self.cats_box.addWidget(_lbl(tr("weread.pref_empty"), WR_FAINT, 11))

        # 24 小时柱状图：只在 0/6/12/18/23 显示刻度
        bars = []
        for hour, minutes in time_bars[:24]:
            label = str(hour) if int(hour) % 6 == 0 else ""
            bars.append((label, minutes))
        self.chart.set_data(bars)


class NoteStatsCard(QFrame):
    """书籍划线统计卡：总划线 / 涉及书籍 / 划线最多的书（横条排行）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("wrPanel")
        self.setMinimumHeight(300)
        self.setStyleSheet(
            f"QFrame#wrPanel{{background-color:{WR_CARD};"
            f"border:1px solid {WR_LINE};border-radius:14px;}}")
        self._data: Optional[tuple] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)

        head = QHBoxLayout()
        self.title_lbl = GroupTitleLabel(tr("weread.note_title"), "fontaine", WR_ORANGE_L, font_size=14)
        self.title_lbl._nation_fixed_color = True
        head.addWidget(self.title_lbl)
        head.addStretch()
        lay.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(16)
        # 左侧：概览数字 + 排行
        left = QVBoxLayout()
        left.setSpacing(8)
        nums = QHBoxLayout()
        nums.setSpacing(16)
        self.total_num = _lbl("—", WR_ORANGE, 26, bold=True)
        self.total_cap = _lbl(tr("weread.note_total"), WR_DIM, 11)
        self.books_num = _lbl("—", WR_TEXT, 26, bold=True)
        self.books_cap = _lbl(tr("weread.note_books"), WR_DIM, 11)
        box1 = QVBoxLayout()
        box1.setSpacing(0)
        box1.addWidget(self.total_num)
        box1.addWidget(self.total_cap)
        box2 = QVBoxLayout()
        box2.setSpacing(0)
        box2.addWidget(self.books_num)
        box2.addWidget(self.books_cap)
        nums.addLayout(box1)
        nums.addLayout(box2)
        nums.addStretch()
        left.addLayout(nums)
        self.rank_lbl = _lbl(tr("weread.note_rank"), WR_DIM, 11)
        left.addWidget(self.rank_lbl)
        self.rank_box = QVBoxLayout()
        self.rank_box.setSpacing(7)
        left.addLayout(self.rank_box)
        body.addLayout(left, 2)

        # 右侧：本月划线趋势迷你折线
        right = QVBoxLayout()
        right.setSpacing(4)
        self.trend_title = _lbl(tr("weread.note_month"), WR_DIM, 11)
        right.addWidget(self.trend_title)
        self.spark = Sparkline()
        right.addWidget(self.spark, 0, Qt.AlignTop)
        right.addStretch()
        body.addLayout(right, 1)
        lay.addLayout(body)

    def set_data(self, total: int, book_count: int, top: List[tuple],
                 month: Optional[List[tuple]] = None) -> None:
        self._data = (total, book_count, top, month or [])
        self.total_num.setText(_fmt_count(total))
        self.books_num.setText(_fmt_count(book_count))
        self.total_num.setToolTip(str(total))
        self.books_num.setToolTip(str(book_count))
        self.spark.set_data(month or [])

        while self.rank_box.count():
            item = self.rank_box.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        maxv = max((int(n) for _, n in top), default=0) or 1
        for title, count in (top or [])[:6]:
            row = QHBoxLayout()
            row.setSpacing(8)
            name = _lbl(str(title), WR_DIM, 11)
            name.setMinimumWidth(70)
            row.addWidget(name, 0)
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setFixedHeight(7)
            bar.setMaximum(100)
            bar.setValue(int(max(0, min(100, int(count) * 100 / maxv))))
            bar.setStyleSheet(
                f"QProgressBar{{background-color:{WR_CARD2};border:none;border-radius:4px;}}"
                f"QProgressBar::chunk{{background-color:{WR_ORANGE};border-radius:4px;}}")
            row.addWidget(bar, 1)
            num = _lbl(_fmt_count(count), WR_TEXT, 11)
            num.setMinimumWidth(38)
            num.setAlignment(Qt.AlignRight)
            num.setToolTip(str(count))
            row.addWidget(num, 0)
            wrap = QWidget()
            wrap.setLayout(row)
            self.rank_box.addWidget(wrap)
        if not top:
            self.rank_box.addWidget(_lbl(tr("weread.note_empty"), WR_FAINT, 11))

    def retranslate(self) -> None:
        self.title_lbl.setText(tr("weread.note_title"))
        self.total_cap.setText(tr("weread.note_total"))
        self.books_cap.setText(tr("weread.note_books"))
        self.rank_lbl.setText(tr("weread.note_rank"))
        self.trend_title.setText(tr("weread.note_month"))
        if self._data is not None:
            self.set_data(*self._data)

    def apply_colors(self) -> None:
        self.setStyleSheet(
            f"QFrame#wrPanel{{background-color:{WR_CARD};"
            f"border:1px solid {WR_LINE};border-radius:14px;}}")
        self.title_lbl.set_icon_color(WR_ORANGE_L)
        self.total_num.setStyleSheet(
            f"color:{WR_ORANGE};font-size:26px;font-weight:bold;background:transparent;border:none;padding:0;")
        self.books_num.setStyleSheet(
            f"color:{WR_TEXT};font-size:26px;font-weight:bold;background:transparent;border:none;padding:0;")
        for cap in (self.total_cap, self.books_cap, self.rank_lbl, self.trend_title):
            cap.setStyleSheet(f"color:{WR_DIM};font-size:11px;background:transparent;border:none;padding:0;")
        self.spark.apply_colors()
        if self._data is not None:
            self.set_data(*self._data)


class CoverWallCard(QFrame):
    """书籍封面墙：最近读过的书，横向铺满整行，书名首字占位图兜底。"""

    COVER_W, COVER_H = 60, 84

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("wrPanel")
        self.setStyleSheet(
            f"QFrame#wrPanel{{background-color:{WR_CARD};"
            f"border:1px solid {WR_LINE};border-radius:14px;}}")
        self.setMinimumHeight(150)
        self._data: Optional[List[tuple]] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(10)
        head = QHBoxLayout()
        self.title_lbl = GroupTitleLabel(
            tr("weread.cover_wall"), "mondstadt", WR_ORANGE_L, font_size=14)
        self.title_lbl._nation_fixed_color = True
        head.addWidget(self.title_lbl)
        head.addStretch()
        self.count_lbl = _lbl("", WR_DIM, 11)
        head.addWidget(self.count_lbl)
        lay.addLayout(head)

        self.wall_box = QHBoxLayout()
        self.wall_box.setSpacing(12)
        lay.addLayout(self.wall_box)

    def set_data(self, books: List[tuple]) -> None:
        self._data = books or []
        self.count_lbl.setText(tr("weread.cover_count", n=len(self._data)))
        while self.wall_box.count():
            item = self.wall_box.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        for title, url in self._data[:12]:
            cell = QVBoxLayout()
            cell.setSpacing(4)
            cover = CoverLabel(title, url, size=(self.COVER_W, self.COVER_H), radius=6)
            cover.setCursor(Qt.PointingHandCursor)
            cell.addWidget(cover, 0, Qt.AlignHCenter)
            name = _lbl(title, WR_DIM, 10)
            name.setAlignment(Qt.AlignHCenter)
            name.setMaximumWidth(self.COVER_W + 8)
            name.setToolTip(title)
            cell.addWidget(name, 0, Qt.AlignHCenter)
            holder = QWidget()
            holder.setLayout(cell)
            self.wall_box.addWidget(holder, 0, Qt.AlignTop)
        self.wall_box.addStretch(1)
        if not self._data:
            self.wall_box.addWidget(_lbl(tr("weread.cover_empty"), WR_FAINT, 11))

    def retranslate(self) -> None:
        self.title_lbl.setText(tr("weread.cover_wall"))
        if self._data is not None:
            self.set_data(self._data)

    def apply_colors(self) -> None:
        self.setStyleSheet(
            f"QFrame#wrPanel{{background-color:{WR_CARD};"
            f"border:1px solid {WR_LINE};border-radius:14px;}}")
        self.title_lbl.set_icon_color(WR_ORANGE_L)
        self.count_lbl.setStyleSheet(
            f"color:{WR_DIM};font-size:11px;background:transparent;border:none;padding:0;")
        if self._data is not None:
            self.set_data(self._data)


# ------------------------------------------------------------------ 后台线程

class DashboardWorker(QThread):
    """一次拉取仪表盘全部数据。"""

    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, api_key: str, store: DataStore, parent=None) -> None:
        super().__init__(parent)
        self.api_key = api_key
        self.store = store

    def run(self) -> None:
        try:
            self.progress.emit("正在拉取微信读书阅读数据…")
            client = WeReadClient(self.api_key)
            data = build_dashboard(client)
            cont = data.get("continue")
            if cont and cont.get("book_id"):
                self.store.shelf_add({
                    "book_id": cont["book_id"],
                    "title": cont.get("title", ""),
                    "author": cont.get("author", ""),
                    "format": "weread",
                    "words_num": "",
                    "file_path": cont.get("reader_link", ""),
                    "source": "weread",
                })
                self.store.progress_save(
                    f"weread:{cont['book_id']}", 0, 0,
                    float(cont.get("progress") or 0),
                    {"title": cont.get("title", ""), "source": "weread"},
                )
                self.store.settings_set("weread_last_sync", 1)
            self.finished.emit(data)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class UploadWorker(QThread):
    """批量导入本地书到微信读书。"""

    finished = Signal(object)
    progress = Signal(int)
    message = Signal(str)

    def __init__(
        self,
        cookie: str,
        files: List[str],
        parent=None,
        mobile_session: Optional[WeReadMobileSession] = None,
    ) -> None:
        super().__init__(parent)
        self.cookie = cookie
        self.files = files
        self.mobile_session = mobile_session

    def run(self) -> None:
        uploader = WeReadUploader(self.cookie, mobile_session=self.mobile_session)
        results: List[Dict[str, Any]] = []
        for i, path in enumerate(self.files):
            base = int(i * 100 / max(len(self.files), 1))
            self.message.emit(f"正在导入（{i + 1}/{len(self.files)}）：{os.path.basename(path)}")

            def cb(p, base=base, n=len(self.files)):
                self.progress.emit(min(100, base + int(p / max(n, 1))))

            r = uploader.upload(path, cb)
            r["file"] = path
            results.append(r)
            self.progress.emit(int((i + 1) * 100 / max(len(self.files), 1)))
        self.finished.emit(results)


# ------------------------------------------------------------------ 主视图

class WereadView(QWidget):
    """微信读书阅读数据仪表盘。"""

    def __init__(self, store: DataStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.setObjectName("wereadPage")
        self._mobile_session: Optional[WeReadMobileSession] = load_session(store)
        self._worker: Optional[DashboardWorker] = None
        self._upload_worker: Optional[UploadWorker] = None
        self._dash_data: Optional[Dict[str, Any]] = None
        # 先按主程序主题生成配色，再构建 UI
        key = str(store.settings_get("accent_color", "purple") or "purple")
        mode = str(store.settings_get("theme_mode", "dark") or "dark")
        _apply_palette(mode != "light", accent_color(key))
        self._build_ui()
        self._load_settings()
        self.apply_colors()
        if self.api_key_edit.text().strip():
            QTimer.singleShot(300, self.refresh)

    def apply_colors(self) -> None:
        """跟随主程序深浅主题 + 主题色，刷新整页与所有子卡片。"""
        key = str(self.store.settings_get("accent_color", "purple") or "purple")
        mode = str(self.store.settings_get("theme_mode", "dark") or "dark")
        _apply_palette(mode != "light", accent_color(key))
        # 页面与滚动区背景
        self.setStyleSheet(f"QWidget#wereadPage{{background-color:{WR_BG};}}")
        if getattr(self, "_wr_scroll", None):
            self._wr_scroll.setStyleSheet(f"QScrollArea{{background-color:{WR_BG};border:none;}}")
        if getattr(self, "_wr_content", None):
            self._wr_content.setStyleSheet(f"QWidget#wrContent{{background-color:{WR_BG};}}")
        # 顶部文字
        self.title_lbl.setStyleSheet(
            f"color:{WR_TEXT};font-size:23px;font-weight:bold;background:transparent;border:none;padding:0;")
        if getattr(self, "_greet_lbl", None):
            self._greet_lbl.setStyleSheet(f"color:{WR_DIM};font-size:12px;background:transparent;border:none;padding:0;")
        self.status_lbl.setStyleSheet(
            f"color:{WR_DIM};font-size:11px;background:transparent;border:none;padding:0;")
        # 凭据区
        self._style_cred_box()
        # 各子卡片
        self.hero.apply_colors()
        self.rhythm.apply_colors()
        self.highlight.apply_colors()
        self.preference.apply_colors()
        self.note_stats.apply_colors()
        self.cover_wall.apply_colors()
        for card in self.stat_cards:
            card.apply_colors()
        for b in (self.btn_login, self.btn_logout, self.btn_import, self.btn_refresh):
            self._style_ghost(b)
        # 图标重新染色
        from ..widgets import apply_nation_icon
        apply_nation_icon(self.btn_login, "fontaine", WR_ORANGE_L, 16)
        self.btn_login._nation_fixed_color = True
        apply_nation_icon(self.btn_logout, "snezhnaya", WR_ORANGE_L, 16)
        self.btn_logout._nation_fixed_color = True
        apply_nation_icon(self.btn_import, "liyue", WR_ORANGE_L, 16)
        self.btn_import._nation_fixed_color = True
        apply_nation_icon(self.btn_refresh, "inazuma", WR_ORANGE_L, 16)
        self.btn_refresh._nation_fixed_color = True

    def _style_ghost(self, b: QPushButton) -> None:
        b.setStyleSheet(
            f"QPushButton{{background-color:{WR_CARD2};color:{WR_TEXT};"
            f"border:1px solid {WR_LINE};border-radius:8px;padding:6px 14px;}}"
            f"QPushButton:hover{{background-color:{WR_HOVER};}}"
            f"QPushButton:disabled{{color:{WR_FAINT};}}")

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        self._wr_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"QScrollArea{{background-color:{WR_BG};border:none;}}")
        content = QWidget()
        self._wr_content = content
        content.setObjectName("wrContent")
        content.setStyleSheet(f"QWidget#wrContent{{background-color:{WR_BG};}}")
        scroll.setWidget(content)
        root.addWidget(scroll)
        lay = QVBoxLayout(content)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(14)

        # ---- 顶部标题 + 按钮 ----
        head = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(2)
        self.title_lbl = _lbl(tr("weread.title"), WR_TEXT, 23, bold=True)
        left.addWidget(self.title_lbl)
        self._greet_lbl = _lbl(greeting_by_time(), WR_DIM, 12)
        left.addWidget(self._greet_lbl)
        self.login_lbl = _lbl("", WR_DIM, 11)
        left.addWidget(self.login_lbl)
        head.addLayout(left)
        head.addStretch()
        self.btn_login = _ghost_btn(tr("weread.qr_login"))
        apply_nation_icon(self.btn_login, "fontaine", WR_ORANGE_L, 16)
        self.btn_login._nation_fixed_color = True
        self.btn_login.clicked.connect(self.start_qr_login)
        self.btn_logout = _ghost_btn(tr("weread.qr_login.logout"))
        apply_nation_icon(self.btn_logout, "snezhnaya", WR_ORANGE_L, 16)
        self.btn_logout._nation_fixed_color = True
        self.btn_logout.clicked.connect(self.logout)
        self.btn_import = _ghost_btn(tr("weread.import"))
        apply_nation_icon(self.btn_import, "liyue", WR_ORANGE_L, 16)
        self.btn_import._nation_fixed_color = True
        self.btn_import.clicked.connect(self.import_books)
        self.btn_refresh = _ghost_btn(tr("weread.refresh"))
        apply_nation_icon(self.btn_refresh, "inazuma", WR_ORANGE_L, 16)
        self.btn_refresh._nation_fixed_color = True
        self.btn_refresh.clicked.connect(self.refresh)
        head.addWidget(self.btn_login)
        head.addWidget(self.btn_logout)
        head.addWidget(self.btn_import)
        head.addWidget(self.btn_refresh)
        lay.addLayout(head)

        self.status_lbl = _lbl("", WR_DIM, 11)
        self.status_lbl.setWordWrap(True)
        lay.addWidget(self.status_lbl)

        # ---- 第一行：继续阅读 + 阅读节奏 ----
        row1 = QHBoxLayout()
        row1.setSpacing(14)
        self.hero = OrangeHero()
        self.hero.setObjectName("wrHero")
        self.hero.setStyleSheet("QFrame#wrHero{background:transparent;border:none;}")
        self.hero.mousePressEvent = lambda e: self._open_continue()
        row1.addWidget(self.hero, 13)

        self.rhythm = RhythmCard(goal=int(self.store.settings_get("weread_goal", 20) or 20))
        self.rhythm.goal_changed.connect(
            lambda v: self.store.settings_set("weread_goal", v)
        )
        row1.addWidget(self.rhythm, 10)
        lay.addLayout(row1)

        # ---- 第二行：6 个统计卡 ----
        row2 = QHBoxLayout()
        row2.setSpacing(12)
        self.stat_cards: List[StatCard] = []
        self._stat_card_keys = (
            "weread.stat_total", "weread.stat_days", "weread.stat_books",
            "weread.stat_finished", "weread.stat_notes", "weread.stat_daily",
        )
        for icon, key in zip(("snezhnaya", "liyue", "inazuma", "sumeru", "fontaine", "mondstadt"), self._stat_card_keys):
            card = StatCard(icon, tr(key))
            self.stat_cards.append(card)
            row2.addWidget(card, 1)
        lay.addLayout(row2)

        # ---- 第三行：划线 + 偏好 ----
        row3 = QHBoxLayout()
        row3.setSpacing(14)
        self.highlight = HighlightCard()
        row3.addWidget(self.highlight, 10)
        self.preference = PreferenceCard()
        row3.addWidget(self.preference, 13)
        lay.addLayout(row3)

        # ---- 第四行：书籍封面墙（占整行，方案 C）----
        self.cover_wall = CoverWallCard()
        lay.addWidget(self.cover_wall)

        # ---- 第五行：书籍划线统计 ----
        self.note_stats = NoteStatsCard()
        lay.addWidget(self.note_stats)

        # ---- 凭据配置（默认折叠）----
        self.cred_box = QGroupBox(tr("weread.cred_title"))
        self.cred_box.setCheckable(True)
        self.cred_box.setChecked(False)
        form = QVBoxLayout(self.cred_box)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)

        # 用两列网格保证 label 与输入框严格对齐（输入框左边缘统一）
        LABEL_W = 88
        r1 = QHBoxLayout()
        self.api_key_lbl = _lbl(tr("weread.api_key"), WR_DIM, 11)
        self.api_key_lbl.setFixedWidth(LABEL_W)
        self.api_key_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        r1.addWidget(self.api_key_lbl, 0)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText(tr("weread.api_key_ph"))
        r1.addWidget(self.api_key_edit, 1)
        form.addLayout(r1)

        r2 = QHBoxLayout()
        self.cookie_lbl = _lbl(tr("weread.web_cookie"), WR_DIM, 11)
        self.cookie_lbl.setFixedWidth(LABEL_W)
        self.cookie_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        r2.addWidget(self.cookie_lbl, 0)
        self.cookie_edit = QTextEdit()
        self.cookie_edit.setMaximumHeight(52)
        self.cookie_edit.setPlaceholderText(tr("weread.cookie_ph"))
        r2.addWidget(self.cookie_edit, 1)
        form.addLayout(r2)

        # 日志行也左对齐到输入框起点
        r3 = QHBoxLayout()
        spacer_lbl = _lbl(tr("weread.log"), WR_DIM, 11)
        spacer_lbl.setFixedWidth(LABEL_W)
        spacer_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        r3.addWidget(spacer_lbl, 0)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(72)
        r3.addWidget(self.log_text, 1)
        form.addLayout(r3)

        r4 = QHBoxLayout()
        r4.addSpacing(LABEL_W + 10)
        self.btn_save = _ghost_btn(tr("weread.save_cred"))
        self.btn_save.clicked.connect(self.save_settings)
        self.btn_web = _ghost_btn(tr("weread.open_upload"))
        self.btn_web.clicked.connect(self._open_upload_page)
        r4.addWidget(self.btn_save)
        r4.addWidget(self.btn_web)
        r4.addStretch()
        form.addLayout(r4)
        lay.addWidget(self.cred_box)

    def _style_cred_box(self) -> None:
        """主题切换后刷新凭据配置区样式（输入框左边缘已由固定宽度 label 对齐）。"""
        self.cred_box.setStyleSheet(
            f"QGroupBox{{color:{WR_DIM};font-size:12px;border:1px solid {WR_LINE};"
            f"border-radius:8px;margin-top:6px;}}"
            f"QGroupBox::title{{subcontrol-origin:margin;left:10px;padding:0 4px;}}")
        for lbl in (self.api_key_lbl, self.cookie_lbl):
            lbl.setStyleSheet(f"color:{WR_DIM};font-size:11px;background:transparent;border:none;padding:0;")
        self.api_key_edit.setStyleSheet(
            f"QLineEdit{{background-color:{WR_CARD2};color:{WR_TEXT};"
            f"border:1px solid {WR_LINE};border-radius:6px;padding:5px 8px;}}")
        self.cookie_edit.setStyleSheet(
            f"QTextEdit{{background-color:{WR_CARD2};color:{WR_TEXT};"
            f"border:1px solid {WR_LINE};border-radius:6px;padding:5px 8px;font-size:12px;}}")
        self.log_text.setStyleSheet(
            f"QTextEdit{{background-color:{WR_CARD};color:{WR_DIM};"
            f"border:1px solid {WR_LINE};border-radius:6px;font-size:11px;}}")

    def retranslate(self) -> None:
        """语言切换后刷新静态文案；用缓存数据重绘子卡片，不触发网络请求。"""
        self.title_lbl.setText(tr("weread.title"))
        self.btn_login.setText(tr("weread.qr_login"))
        self.btn_logout.setText(tr("weread.qr_login.logout"))
        self.btn_import.setText(tr("weread.import"))
        self.btn_refresh.setText(tr("weread.refresh"))
        self._update_login_status()
        self.cred_box.setTitle(tr("weread.cred_title"))
        self.api_key_lbl.setText(tr("weread.api_key"))
        self.api_key_edit.setPlaceholderText(tr("weread.api_key_ph"))
        self.cookie_lbl.setText(tr("weread.web_cookie"))
        self.cookie_edit.setPlaceholderText(tr("weread.cookie_ph"))
        self.btn_save.setText(tr("weread.save_cred"))
        self.btn_web.setText(tr("weread.open_upload"))
        for card, key in zip(self.stat_cards, self._stat_card_keys):
            card.label_lbl.setText(tr(key))
        self.hero.retranslate()
        self.rhythm.retranslate()
        self.highlight.retranslate()
        self.preference.retranslate()
        self.note_stats.retranslate()
        self.cover_wall.retranslate()

    # ------------------------------------------------------------------ 凭据

    def _load_settings(self) -> None:
        self.api_key_edit.setText(str(self.store.settings_get("weread_api_key", "")))
        self.cookie_edit.setPlainText(str(self.store.settings_get("weread_cookie", "")))
        self._mobile_session = load_session(self.store)
        self._update_login_status()

    def save_settings(self) -> None:
        self.store.settings_set("weread_api_key", self.api_key_edit.text().strip())
        self.store.settings_set("weread_cookie", self.cookie_edit.toPlainText().strip())
        self.log("凭据已保存到本机")
        QMessageBox.information(self, "已保存", "微信读书凭据已保存到本机，不会上传。")

    # ------------------------------------------------------------------ 扫码登录

    def _update_login_status(self) -> None:
        session = self._mobile_session
        if session is not None:
            self.login_lbl.setText(
                tr("weread.qr_login.connected", vid=session.masked_vid)
            )
            self.btn_logout.setVisible(True)
        else:
            self.login_lbl.setText(tr("weread.qr_login.not_connected"))
            self.btn_logout.setVisible(False)

    def start_qr_login(self) -> None:
        dlg = WereadLoginDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.session is not None:
            session = dlg.session
            try:
                save_session(self.store, session)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(
                    self, tr("weread.qr_login.title"),
                    tr("weread.qr_login.save_failed", msg=str(exc)),
                )
                return
            self._mobile_session = session
            self._update_login_status()
            self.status_lbl.setText(
                tr("weread.qr_login.success", vid=session.masked_vid)
            )
            self.log(tr("weread.qr_login.log_success", vid=session.masked_vid))

    def logout(self) -> None:
        if self._mobile_session is None:
            return
        clear_session(self.store)
        self._mobile_session = None
        self._update_login_status()
        self.status_lbl.setText(tr("weread.qr_login.logout_done"))
        self.log(tr("weread.qr_login.logout_done"))

    def _active_upload_session(self) -> Optional[WeReadMobileSession]:
        if self._mobile_session is not None:
            return self._mobile_session
        self._mobile_session = load_session(self.store)
        self._update_login_status()
        return self._mobile_session

    # ------------------------------------------------------------------ 刷新

    def refresh(self) -> None:
        # 防止定时刷新与手动点击叠加，避免旧 worker 被覆盖后仍在运行
        if self._worker is not None and self._worker.isRunning():
            return
        key = self.api_key_edit.text().strip()
        if not key:
            self.status_lbl.setText(
                "提示：展开下方「凭据设置」并填写 API Key（wrk- 开头）后点击刷新，即可展示真实阅读数据。"
            )
            return
        self.btn_refresh.setEnabled(False)
        self.status_lbl.setText("正在同步微信读书阅读数据…")
        self._worker = DashboardWorker(key, self.store, self)
        self._worker.progress.connect(lambda m: self.status_lbl.setText(m))
        self._worker.finished.connect(self._on_dashboard_done)
        self._worker.failed.connect(self._on_dashboard_failed)
        self._worker.start()

    def _on_dashboard_done(self, data: Dict[str, Any]) -> None:
        self.btn_refresh.setEnabled(True)
        self._render(data)
        errs = data.get("errors") or []
        if errs:
            self.status_lbl.setText("部分数据获取失败：" + "；".join(errs[:3]))
        else:
            self.status_lbl.setText("✓ 已同步微信读书阅读数据")

    def _on_dashboard_failed(self, message: str) -> None:
        self.btn_refresh.setEnabled(True)
        self.status_lbl.setText(f"同步失败：{message}")
        self.log(f"同步失败：{message}")

    def _render(self, data: Dict[str, Any]) -> None:
        self._dash_data = data
        # 继续阅读
        self.hero.set_book(data.get("continue"))
        self._continue_data = data.get("continue")
        # 阅读节奏
        self.rhythm.set_data(
            int(data.get("today_min") or 0),
            int(data.get("week_total_min") or 0),
            int(data.get("week_days") or 0),
            data.get("week_bars") or [],
        )
        # 统计卡
        stats = data.get("stats") or []
        for i, card in enumerate(self.stat_cards):
            if i < len(stats):
                label, value, unit, _icon = stats[i]
                card.set_value(value, unit)
            else:
                card.set_value("—", "")
        # 划线
        cont = data.get("continue") or {}
        self.highlight.set_highlights(
            data.get("highlights") or [],
            str(cont.get("reader_link") or ""),
        )
        # 偏好
        self.preference.set_data(
            data.get("prefer_titles") or [],
            data.get("prefer_categories") or [],
            data.get("prefer_time") or [],
        )
        # 书籍划线统计（新接口缺字段时回退到 note_count 与划线卡推导）
        note_total = int(data.get("note_count") or 0)
        note_books = int(data.get("note_book_count") or 0)
        note_top = data.get("note_top") or []
        if not note_top and note_total:
            # 兼容旧数据：从划线卡里按书名聚合出简单排行
            agg: Dict[str, int] = {}
            for h in (data.get("highlights") or []):
                t = str(h.get("title") or "")
                if t:
                    agg[t] = agg.get(t, 0) + 1
            note_top = sorted(agg.items(), key=lambda x: x[1], reverse=True)
            if not note_books and note_top:
                note_books = len(note_top)
        self.note_stats.set_data(
            note_total, note_books, note_top, data.get("note_month") or [])
        # 书籍封面墙（缺字段时从继续阅读的书补一张）
        cover_books = data.get("cover_books") or []
        if not cover_books and cont.get("title"):
            cover_books = [(str(cont.get("title")), str(cont.get("cover") or ""))]
        self.cover_wall.set_data(cover_books)
        # 实时联动：把笔记本数据同步给「书籍笔记」页，避免重复拉取
        self._sync_notebook(data.get("notebook_books") or [])

    def _sync_notebook(self, books: List[Dict[str, Any]]) -> None:
        """微信读书刷新后，把笔记本列表同步到书籍笔记页（若主窗口已注册）。"""
        if not books:
            return
        try:
            main_win = self.window()
            note_view = getattr(main_win, "notebook_view", None)
            if note_view is not None and hasattr(note_view, "apply_external_books"):
                note_view.apply_external_books(books)
        except Exception:  # noqa: BLE001
            pass

    def _open_continue(self) -> None:
        cont = getattr(self, "_continue_data", None) or {}
        link = str(cont.get("reader_link") or "")
        if link:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(link))
        else:
            self.status_lbl.setText("当前没有在读的书，去微信读书开始读一本吧。")

    # ------------------------------------------------------------------ 导入

    def import_books(self) -> None:
        if self._upload_worker is not None and self._upload_worker.isRunning():
            return
        mobile_session = self._active_upload_session()
        cookie = self.cookie_edit.toPlainText().strip()
        if not cookie and mobile_session is None:
            self.cred_box.setChecked(True)
            QMessageBox.warning(
                self, "缺少凭据", tr("weread.qr_login.need_cred"),
            )
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要导入微信读书的书",
            os.path.expanduser("~/七猫小说"),
            "支持的格式 (*.epub *.txt *.pdf *.mobi *.azw3);;所有文件 (*.*)",
        )
        if not paths:
            return
        oversized = [p for p in paths if os.path.getsize(p) / (1024 * 1024) > MAX_SIZE_MB]
        if oversized:
            QMessageBox.warning(
                self, "文件过大",
                f"以下文件超过 {MAX_SIZE_MB} MB，已跳过：\n"
                + "\n".join(os.path.basename(p) for p in oversized),
            )
            paths = [p for p in paths if p not in oversized]
        if not paths:
            return
        self.btn_import.setEnabled(False)
        self.status_lbl.setText(f"正在导入 {len(paths)} 本书到微信读书…")
        self._upload_worker = UploadWorker(
            cookie, paths, self, mobile_session=mobile_session
        )
        self._upload_worker.progress.connect(
            lambda v: self.status_lbl.setText(f"导入进度 {v}%")
        )
        self._upload_worker.message.connect(self.log)
        self._upload_worker.finished.connect(self._on_upload_done)
        self._upload_worker.start()

    def _on_upload_done(self, results: List[Dict[str, Any]]) -> None:
        self.btn_import.setEnabled(True)
        ok = sum(1 for r in results if r.get("success"))
        for r in results:
            self.log(f"{'✓' if r.get('success') else '✗'} "
                     f"{os.path.basename(r.get('file', ''))}: {r.get('message')}")
        self.status_lbl.setText(f"导入已提交：成功 {ok}/{len(results)} 本，入架由微信读书异步完成。")
        QMessageBox.information(
            self, "导入已提交",
            f"成功提交 {ok}/{len(results)} 本。\n\n"
            "提交成功不代表立即入架，微信读书会在后台解析，请稍后到官方书架查看；"
            "不要重复导入同一本书。",
        )

    # ------------------------------------------------------------------ 工具

    def _open_upload_page(self) -> None:
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl("https://weread.qq.com/web/upload"))

    def log(self, message: str) -> None:
        ts = __import__("time").strftime("%H:%M:%S")
        self.log_text.append(f"[{ts}] {message}")

    def import_from_shelf(self, entries: List[Dict[str, Any]]) -> None:
        """供书架右键调用：把指定书架条目导入微信读书。"""
        if self._upload_worker is not None and self._upload_worker.isRunning():
            return
        paths = [
            e.get("file_path", "") for e in entries
            if e.get("file_path") and os.path.isfile(e["file_path"])
            and os.path.splitext(e["file_path"])[1].lower() in SUPPORTED_FORMATS
        ]
        if not paths:
            QMessageBox.information(self, "提示", "所选书籍没有可导入的本地文件")
            return
        mobile_session = self._active_upload_session()
        cookie = self.cookie_edit.toPlainText().strip()
        if not cookie and mobile_session is None:
            self.cred_box.setChecked(True)
            QMessageBox.warning(self, "缺少凭据", tr("weread.qr_login.need_cred"))
            return
        self.btn_import.setEnabled(False)
        self._upload_worker = UploadWorker(
            cookie, paths, self, mobile_session=mobile_session
        )
        self._upload_worker.progress.connect(
            lambda v: self.status_lbl.setText(f"导入进度 {v}%")
        )
        self._upload_worker.message.connect(self.log)
        self._upload_worker.finished.connect(self._on_upload_done)
        self._upload_worker.start()
