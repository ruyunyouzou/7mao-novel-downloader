# -*- coding: utf-8 -*-
"""
微信读书笔记本页：复刻微信读书「笔记」界面。

顶部：📝 笔记 + N本书 + 搜索书或作者 + 导出全部
主体：3 列网格卡片，每卡显示封面、书名、作者、「划线 N」「想法 N」标签、放大镜图标。
点击卡片 → 查看该书的划线与想法详情。
数据来自 /user/notebooks（官方 Agent Gateway，只读）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor, QFont, QLinearGradient
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QLabel,
    QPushButton, QLineEdit, QScrollArea, QMessageBox, QDialog,
    QTabWidget, QSizePolicy,
)

from ..datastore import DataStore
from .. import filedialogs as _fd
from ..i18n import tr
from .. import render_quality
from ..widgets import GroupTitleLabel, apply_nation_icon
from ..weread_core import WeReadClient
from ..book_card import CoverLoader
from . import weread_view as _wr

# 配色与仪表盘统一：默认深色值，主题切换时由 _sync_palette 从仪表盘调色板同步
NB_BG = "#17131d"
NB_CARD = "#241e2e"
NB_CARD2 = "#2b2438"
NB_ORANGE = "#ff6b00"
NB_ORANGE_L = "#ff9d4d"
NB_TEXT = "#f5f0fa"
NB_DIM = "#8e859c"
NB_FAINT = "#645b74"
NB_LINE = "#332a42"
NB_HOVER = "#3a3048"
NB_TAG_BG = "#3a2a1c"


def _sync_palette(store: DataStore) -> None:
    """按主程序深浅主题 + 主题色刷新配色，与微信读书仪表盘保持同一套。"""
    from ..style import accent_color
    global NB_BG, NB_CARD, NB_CARD2, NB_ORANGE, NB_ORANGE_L, NB_TEXT
    global NB_DIM, NB_FAINT, NB_LINE, NB_HOVER, NB_TAG_BG
    key = str(store.settings_get("accent_color", "purple") or "purple")
    mode = str(store.settings_get("theme_mode", "dark") or "dark")
    _wr._apply_palette(mode != "light", accent_color(key))
    NB_BG = _wr.WR_BG
    NB_CARD = _wr.WR_CARD
    NB_CARD2 = _wr.WR_CARD2
    NB_ORANGE = _wr.WR_ORANGE
    NB_ORANGE_L = _wr.WR_ORANGE_L
    NB_TEXT = _wr.WR_TEXT
    NB_DIM = _wr.WR_DIM
    NB_FAINT = _wr.WR_FAINT
    NB_LINE = _wr.WR_LINE
    NB_HOVER = _wr.WR_HOVER
    NB_TAG_BG = _wr.WR_TAG_BG


def _lbl(text: str = "", color: str = NB_TEXT, size: int = 13, bold: bool = False) -> QLabel:
    q = QLabel(text)
    css = f"color:{color};font-size:{size}px;background:transparent;border:none;padding:0;"
    if bold:
        css += "font-weight:bold;"
    q.setStyleSheet(css)
    return q


def _btn(text: str, primary: bool = False) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    if primary:
        b.setStyleSheet(
            f"QPushButton{{background-color:{NB_ORANGE};color:#ffffff;border:none;"
            "border-radius:8px;padding:6px 14px;font-weight:bold;}"
            f"QPushButton:hover{{background-color:{NB_ORANGE_L};}}"
        )
    else:
        b.setStyleSheet(
            f"QPushButton{{background-color:{NB_CARD2};color:{NB_TEXT};"
            f"border:1px solid {NB_LINE};border-radius:8px;padding:6px 14px;}}"
            f"QPushButton:hover{{background-color:{NB_HOVER};}}"
        )
    return b


# ------------------------------------------------------------------ 封面小组件

class CoverThumb(QLabel):
    """异步加载书籍封面，失败时显示「书名首字」渐变占位图（与仪表盘同款）。

    方案 A：封面加大，占位图用主题色渐变 + 首字，避免空洞的 ▤ 方块。
    """

    def __init__(self, url: str, title: str = "", size: tuple = (72, 100),
                 parent=None) -> None:
        super().__init__(parent)
        self._w, self._h = size
        self._title = title or "书"
        self.setFixedSize(self._w, self._h)
        self.setAlignment(Qt.AlignCenter)
        self.setScaledContents(True)
        self._loader: Optional[CoverLoader] = None
        self._url = url
        self._real_cover = False  # 是否已加载真实封面
        self._set_placeholder()
        if url:
            self._load(url)

    def _set_placeholder(self) -> None:
        """主题色渐变 + 书名首字占位图。"""
        pm = QPixmap(self._w * 2, self._h * 2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        render_quality.apply_painter(p, text=True, smooth=True)
        grad = QLinearGradient(0, 0, pm.width(), pm.height())
        a = QColor(NB_ORANGE)
        grad.setColorAt(0.0, QColor(
            int(a.red() * 0.45 + 30), int(a.green() * 0.40 + 26), int(a.blue() * 0.5 + 40)))
        grad.setColorAt(1.0, QColor(
            int(a.red() * 0.28 + 20), int(a.green() * 0.25 + 18), int(a.blue() * 0.35 + 32)))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(pm.rect(), 12, 12)
        f = QFont()
        f.setPointSize(max(16, self._w // 3))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(pm.rect(), Qt.AlignCenter, self._title[:1])
        p.end()
        self.setPixmap(pm)
        self.setToolTip(self._title)

    def _load(self, url: str) -> None:
        loader = CoverLoader(url)
        self._loader = loader
        loader.loaded.connect(self._on_loaded)
        # CoverLoader 现在在 __init__ 里自动纳入 _orphan_loaders 并在 finished 时释放，
        # 这里不再重复连接/添加（避免与内建释放逻辑重复）
        loader.start()

    def _on_loaded(self, url: str, img: QImage) -> None:
        if url != self._url or img.isNull():
            return
        pix = QPixmap.fromImage(img).scaled(
            self._w, self._h, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.setPixmap(pix)
        self._real_cover = True

    def apply_colors(self) -> None:
        """主题切换后刷新占位图（已加载真实封面则不覆盖）。"""
        if not self._real_cover:
            self._set_placeholder()

    def clear_loader(self) -> None:
        self._loader = None


# ------------------------------------------------------------------ 后台线程

class NotebooksWorker(QThread):
    finished = Signal(object)   # list[dict]
    failed = Signal(str)

    def __init__(self, api_key: str, parent=None) -> None:
        super().__init__(parent)
        self.api_key = api_key

    def run(self) -> None:
        try:
            client = WeReadClient(self.api_key)
            data = client.get_notebooks()
            books = []
            for b in data.get("books") or []:
                books.append({
                    "bookId": str(b.get("bookId") or ""),
                    "title": str(b.get("title") or ""),
                    "author": str(b.get("author") or ""),
                    "cover": str(b.get("cover") or ""),
                    "noteCount": int(b.get("noteCount") or 0),   # 划线数
                    "reviewCount": int(b.get("reviewCount") or 0),  # 想法数
                    "bookmarkCount": int(b.get("bookmarkCount") or 0),
                })
            self.finished.emit(books)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class DetailWorker(QThread):
    """拉取单本书的划线与想法。"""

    finished = Signal(object, object)  # highlights, notes
    failed = Signal(str)

    def __init__(self, api_key: str, book_id: str, parent=None) -> None:
        super().__init__(parent)
        self.api_key = api_key
        self.book_id = book_id

    def run(self) -> None:
        try:
            client = WeReadClient(self.api_key)
            highlights = client.get_highlights(self.book_id)
            notes = client.get_notes(self.book_id)
            self.finished.emit(highlights, notes)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class ExportWorker(QThread):
    """导出所有书的划线与想法为 Markdown。"""

    finished = Signal(str)   # 输出文件路径
    progress = Signal(str)
    failed = Signal(str)

    def __init__(self, api_key: str, books: List[Dict[str, Any]], out_path: str, parent=None) -> None:
        super().__init__(parent)
        self.api_key = api_key
        self.books = books
        self.out_path = out_path

    def run(self) -> None:
        try:
            client = WeReadClient(self.api_key)
            lines: List[str] = ["# 微信读书笔记导出\n"]
            for i, b in enumerate(self.books):
                self.progress.emit(f"正在导出（{i + 1}/{len(self.books)}）：{b['title']}")
                lines.append(f"\n## 《{b['title']}》\n")
                if b.get("author"):
                    lines.append(f"**作者**：{b['author']}\n")
                try:
                    highlights = client.get_highlights(b["bookId"])
                except Exception:  # noqa: BLE001
                    highlights = []
                if highlights:
                    lines.append("\n### 划线\n")
                    for h in highlights:
                        text = str(h.get("text") or "").replace("\n", " ")
                        lines.append(f"> {text}\n")
                try:
                    notes = client.get_notes(b["bookId"])
                except Exception:  # noqa: BLE001
                    notes = []
                if notes:
                    lines.append("\n### 想法\n")
                    for n in notes:
                        content = str(n.get("content") or "").replace("\n", " ")
                        abstract = str(n.get("abstract") or "")
                        if abstract:
                            lines.append(f"- 原文：{abstract}\n  想法：{content}\n")
                        else:
                            lines.append(f"- {content}\n")
            with open(self.out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self.finished.emit(self.out_path)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


# ------------------------------------------------------------------ 书籍卡片

class NotebookCard(QFrame):
    clicked = Signal(dict)

    def __init__(self, book: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.book = book
        self._hovered = False
        self.setObjectName("notebookCard")
        self.setFixedHeight(124)  # 方案 A：加大卡片以容纳 72×100 封面
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(12)

        title = book.get("title") or tr("nb.untitled")
        self.cover = CoverThumb(book.get("cover", ""), title, size=(72, 100))
        lay.addWidget(self.cover)

        info = QVBoxLayout()
        info.setSpacing(5)
        self.title_lbl = _lbl(title, NB_TEXT, 14, bold=True)
        self.title_lbl.setWordWrap(True)
        info.addWidget(self.title_lbl)
        self.author_lbl = _lbl(book.get("author") or "", NB_DIM, 11)
        info.addWidget(self.author_lbl)

        tags = QHBoxLayout()
        tags.setSpacing(6)
        n1 = int(book.get("noteCount") or 0)
        n2 = int(book.get("reviewCount") or 0)
        self.tag1 = QLabel(tr("nb.highlights", n=n1))
        self.tag2 = QLabel(tr("nb.ideas", n=n2))
        tags.addWidget(self.tag1)
        tags.addWidget(self.tag2)
        tags.addStretch()
        info.addLayout(tags)
        info.addStretch()
        lay.addLayout(info, 1)

        self.magnify_lbl = _lbl("⌕", NB_FAINT, 20)
        self.magnify_lbl.setAlignment(Qt.AlignTop)
        lay.addWidget(self.magnify_lbl, 0, Qt.AlignTop)

        # 顶部主题色渐变条（方案 A 标识）
        self.accent_bar = QWidget(self)
        self.accent_bar.setFixedHeight(3)
        self.accent_bar.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.accent_bar.setAttribute(Qt.WA_StyledBackground, True)  # 让 QSS 渐变生效

        self.apply_colors()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # 顶部条贴合卡片上沿（留出圆角）
        if hasattr(self, "accent_bar"):
            self.accent_bar.setGeometry(10, 0, max(0, self.width() - 20), 3)

    def enterEvent(self, event) -> None:  # noqa: N802
        """悬停高亮：改用纯 QSS 描边 + 背景提亮。

        原实现用 QGraphicsDropShadowEffect 做投影，但在 Qt6 + QScrollArea
        网格中动态挂 QGraphicsEffect 会让该卡片离屏合成失败、整块渲染成空白
        （鼠标移上去卡片"消失"）。纯 QSS 无此问题。
        """
        self._hovered = True
        self.setStyleSheet(
            f"QFrame#notebookCard{{background-color:{NB_HOVER};"
            f"border:1px solid {NB_ORANGE};border-radius:10px;}}"
        )
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.setStyleSheet(
            f"QFrame#notebookCard{{background-color:{NB_CARD};border:1px solid {NB_LINE};border-radius:10px;}}"
        )
        super().leaveEvent(event)

    def apply_colors(self) -> None:
        """跟随主题刷新卡片配色（深浅主题 + 主题色）。"""
        self.cover.apply_colors()
        # 主题切换时若鼠标正悬停，保留悬停高亮态，避免被默认样式覆盖
        if self._hovered:
            self.setStyleSheet(
                f"QFrame#notebookCard{{background-color:{NB_HOVER};"
                f"border:1px solid {NB_ORANGE};border-radius:10px;}}"
            )
        else:
            self.setStyleSheet(
                f"QFrame#notebookCard{{background-color:{NB_CARD};border:1px solid {NB_LINE};border-radius:10px;}}"
            )
        self.accent_bar.setStyleSheet(
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {NB_ORANGE},stop:1 {NB_ORANGE_L});border-radius:2px;"
        )
        self.title_lbl.setStyleSheet(
            f"color:{NB_TEXT};font-size:14px;font-weight:bold;background:transparent;border:none;padding:0;")
        self.author_lbl.setStyleSheet(
            f"color:{NB_DIM};font-size:11px;background:transparent;border:none;padding:0;")
        self.tag1.setStyleSheet(
            f"color:{NB_ORANGE_L};background-color:{NB_TAG_BG};border-radius:8px;"
            "padding:2px 8px;font-size:11px;")
        self.tag2.setStyleSheet(
            f"color:{NB_DIM};background-color:{NB_CARD2};border-radius:8px;"
            "padding:2px 8px;font-size:11px;")
        self.magnify_lbl.setStyleSheet(
            f"color:{NB_FAINT};font-size:20px;background:transparent;border:none;padding:0;")

    def retranslate(self) -> None:
        """只刷新标签文案，不重新加载数据。"""
        self.tag1.setText(tr("nb.highlights", n=int(self.book.get("noteCount") or 0)))
        self.tag2.setText(tr("nb.ideas", n=int(self.book.get("reviewCount") or 0)))

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.book)
        super().mousePressEvent(event)


# ------------------------------------------------------------------ 详情对话框

class ChapterGroup(QFrame):
    """方案 B：按章节分组的可折叠卡片，组头显示章节名与条数。

    点击组头切换展开/折叠，组头带主题色左边框；组内每条仍用卡片式富文本
    （引号贴文字前后），保持与仪表盘笔记模块一致的观感。
    """

    def __init__(self, chapter: str, count: int, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("chapterGroup")
        self._chapter = (chapter or "").strip() or tr("nb.untitled_chapter")
        self._count = int(count)
        self._expanded = True
        self._items: List[str] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = QPushButton()
        self.header.setObjectName("chapterHeader")
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.clicked.connect(self.toggle)
        root.addWidget(self.header)

        self.body = QWidget()
        self.body.setObjectName("chapterBody")
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(10, 8, 10, 10)
        self.body_lay.setSpacing(8)
        root.addWidget(self.body)

        self._update_header()
        self.apply_colors()

    def _update_header(self) -> None:
        arrow = "▾" if self._expanded else "▸"
        self.header.setText(
            f" {arrow}  {self._chapter}　·　{tr('nb.chapter_items', n=self._count)}"
        )

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.body.setVisible(self._expanded)
        self._update_header()

    def add_item(self, html: str) -> None:
        """往组内追加一条卡片式内容（html 仅含颜色/字号等可靠富文本）。"""
        card = QFrame()
        card.setObjectName("chapterItemCard")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(10, 8, 10, 8)
        card_lay.setSpacing(2)
        body = QLabel()
        body.setObjectName("chapterItemText")
        body.setText(html)  # QLabel 自动识别富文本
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card_lay.addWidget(body)
        self.body_lay.addWidget(card)
        self._items.append(html)

    def item_html(self) -> str:
        """返回组内全部条目的 HTML（供回归检查引号位置）。"""
        return "".join(self._items)

    def apply_colors(self) -> None:
        self.setStyleSheet(
            f"QFrame#chapterGroup{{background-color:{NB_CARD};border:1px solid {NB_LINE};"
            f"border-left:3px solid {NB_ORANGE};border-radius:10px;}}"
        )
        self.header.setStyleSheet(
            f"QPushButton#chapterHeader{{background-color:{NB_CARD2};color:{NB_TEXT};"
            "border:none;border-radius:8px;padding:9px 12px;text-align:left;"
            "font-size:13px;font-weight:bold;}"
            f"QPushButton#chapterHeader:hover{{background-color:{NB_HOVER};}}"
        )
        for i in range(self.body_lay.count()):
            w = self.body_lay.itemAt(i).widget()
            if isinstance(w, QFrame):
                w.setStyleSheet(
                    f"QFrame#chapterItemCard{{background-color:{NB_CARD2};"
                    f"border:1px solid {NB_LINE};border-radius:8px;}}"
                )


class NotebookDetailDialog(QDialog):
    def __init__(self, api_key: str, book: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.api_key = api_key
        self.book = book
        self.setWindowTitle(tr("nb.dialog_title", title=book.get("title", "")))
        self.resize(680, 600)
        self.setStyleSheet(f"QDialog{{background-color:{NB_BG};}}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)

        head = QHBoxLayout()
        head.addWidget(_lbl(f"《{book.get('title', '')}》", NB_TEXT, 17, bold=True))
        head.addStretch()
        self.btn_detail_refresh = _btn(tr("nb.refresh"))
        self.btn_detail_refresh.clicked.connect(self._reload_detail)
        head.addWidget(self.btn_detail_refresh)
        lay.addLayout(head)
        self.author_lbl = _lbl(
            tr("nb.author_line", author=book.get("author") or "—",
               n1=book.get("noteCount", 0), n2=book.get("reviewCount", 0)),
            NB_DIM, 11,
        )
        lay.addWidget(self.author_lbl)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            f"QTabWidget::pane{{border:1px solid {NB_LINE};background-color:{NB_CARD};border-radius:8px;}}"
            f"QTabBar::tab{{background-color:{NB_CARD2};color:{NB_DIM};padding:6px 14px;}}"
            f"QTabBar::tab:selected{{color:{NB_ORANGE_L};font-weight:bold;}}"
        )
        # 方案 B：两个可滚动容器，内部按章节分组折叠展示
        self.hl_host, self.hl_lay = self._make_chapter_host()
        self.note_host, self.note_lay = self._make_chapter_host()
        self.tabs.addTab(
            self._wrap_chapter_scroll(self.hl_host, "hl_scroll"), tr("nb.tab_highlights"))
        self.tabs.addTab(
            self._wrap_chapter_scroll(self.note_host, "note_scroll"), tr("nb.tab_ideas"))
        lay.addWidget(self.tabs, 1)

        self.status = _lbl(tr("nb.loading"), NB_DIM, 11)
        lay.addWidget(self.status)

        self.worker = None
        self._reload_detail()

    @staticmethod
    def _make_chapter_host() -> tuple:
        """创建分组承载 widget + 顶部对齐的纵向布局。"""
        host = QWidget()
        host.setObjectName("nbChapterHost")
        lay = QVBoxLayout(host)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        lay.setAlignment(Qt.AlignTop)
        return host, lay

    def _wrap_chapter_scroll(self, host: QWidget, name: str) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName(name)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(host)
        scroll.setStyleSheet(f"QScrollArea{{background-color:{NB_CARD};}}")
        return scroll

    def _reload_detail(self) -> None:
        """主动重新拉取划线/想法（实时同步微信读书最新数据）。"""
        self.btn_detail_refresh.setEnabled(False)
        self.status.setText(tr("nb.loading"))
        self.worker = DetailWorker(self.api_key, str(self.book.get("bookId", "")), self)
        self.worker.finished.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def retranslate(self) -> None:
        """刷新对话框静态文案（不重新拉取数据）。"""
        self.setWindowTitle(tr("nb.dialog_title", title=self.book.get("title", "")))
        self.author_lbl.setText(
            tr("nb.author_line", author=self.book.get("author") or "—",
               n1=self.book.get("noteCount", 0), n2=self.book.get("reviewCount", 0))
        )
        self.tabs.setTabText(0, tr("nb.tab_highlights"))
        self.tabs.setTabText(1, tr("nb.tab_ideas"))
        for g in self.findChildren(ChapterGroup):
            g._update_header()

    def set_data(self, highlights: List[Dict[str, Any]], notes: List[Dict[str, Any]]) -> None:
        """注入划线/想法数据并渲染（供后台线程与外部模拟数据调用）。"""
        self._highlights = highlights or []
        self._notes = notes or []
        self._render_content()

    def _render_content(self) -> None:
        """方案 B：按章节分组渲染，组头显示条数，默认展开第一组。"""
        self._render_groups(self.hl_lay, getattr(self, "_highlights", []), "hl")
        self._render_groups(self.note_lay, getattr(self, "_notes", []), "note")

    @staticmethod
    def _group_by_chapter(items: List[Dict[str, Any]]) -> List[tuple]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        order: List[str] = []
        for item in items:
            chapter = str(item.get("chapter") or "").strip() or tr("nb.untitled_chapter")
            if chapter not in groups:
                groups[chapter] = []
                order.append(chapter)
            groups[chapter].append(item)
        return [(ch, groups[ch]) for ch in order]

    def _render_groups(self, lay, items: List[Dict[str, Any]], kind: str) -> None:
        # 同步移除旧分组，避免 deleteLater 在无事件循环时残留
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        grouped = self._group_by_chapter(items)
        if not grouped:
            lay.addWidget(_lbl(
                tr("nb.empty_highlights") if kind == "hl" else tr("nb.empty_ideas"),
                NB_FAINT, 12))
            return

        for idx, (chapter, group_items) in enumerate(grouped):
            g = ChapterGroup(chapter, len(group_items))
            g.set_expanded(idx == 0)  # 默认展开第一组，其余折叠
            for n, item in enumerate(group_items, 1):
                g.add_item(self._item_html(item, kind, n))
            lay.addWidget(g)

    @staticmethod
    def _item_html(item: Dict[str, Any], kind: str, idx: int) -> str:
        if kind == "hl":
            text = str(item.get("text") or "").replace("\n", "<br>")
            return (
                f"<div style='color:{NB_TEXT};font-size:13px;line-height:1.7;'>"
                f"<span style='color:{NB_ORANGE};font-size:15px;font-weight:bold;'>“</span>"
                f"{text}"
                f"<span style='color:{NB_ORANGE};font-size:15px;font-weight:bold;'>”</span>"
                f"</div>"
                f"<div style='color:{NB_FAINT};font-size:10px;margin-top:4px;'>#{idx}</div>"
            )
        abstract = str(item.get("abstract") or "").replace("\n", " ")
        content = str(item.get("content") or "").replace("\n", "<br>")
        star = int(item.get("star") or 0)
        meta = ""
        if star:
            meta += "<span style='color:#ffb454;'>★" * min(star, 5) + "</span>"
        quote = (
            f"<div style='color:{NB_DIM};font-size:11px;border-left:3px solid {NB_ORANGE};"
            f"padding-left:8px;margin-bottom:6px;'>{abstract}</div>"
            if abstract else ""
        )
        return (
            f"{quote}"
            f"<div style='color:{NB_TEXT};font-size:13px;line-height:1.7;'>{content}</div>"
            f"<div style='color:{NB_FAINT};font-size:10px;margin-top:4px;'>{meta}</div>"
        )

    def collect_item_html(self, which: str = "hl") -> str:
        """供回归检查：返回指定 tab 内全部条目的 HTML。"""
        host = self.hl_host if which == "hl" else self.note_host
        return "".join(g.item_html() for g in host.findChildren(ChapterGroup))

    def _on_done(self, highlights: List[Dict[str, Any]], notes: List[Dict[str, Any]]) -> None:
        self.btn_detail_refresh.setEnabled(True)
        self.set_data(highlights, notes)
        self.status.setText(tr("nb.loaded", h=len(highlights), n=len(notes)))

    def apply_colors(self) -> None:
        """主题切换后刷新对话框配色（模态对话框通常关闭，保险起见提供）。"""
        self.setStyleSheet(f"QDialog{{background-color:{NB_BG};}}")
        self.tabs.setStyleSheet(
            f"QTabWidget::pane{{border:1px solid {NB_LINE};background-color:{NB_CARD};border-radius:8px;}}"
            f"QTabBar::tab{{background-color:{NB_CARD2};color:{NB_DIM};padding:6px 14px;}}"
            f"QTabBar::tab:selected{{color:{NB_ORANGE_L};font-weight:bold;}}"
        )
        self.status.setStyleSheet(
            f"color:{NB_DIM};font-size:11px;background:transparent;border:none;padding:0;")
        self.btn_detail_refresh.setStyleSheet(
            f"QPushButton{{background-color:{NB_CARD2};color:{NB_TEXT};"
            f"border:1px solid {NB_LINE};border-radius:8px;padding:6px 14px;}}"
            f"QPushButton:hover{{background-color:{NB_HOVER};}}"
        )
        for name in ("hl_scroll", "note_scroll"):
            scroll = self.findChild(QScrollArea, name)
            if scroll is not None:
                scroll.setStyleSheet(f"QScrollArea{{background-color:{NB_CARD};}}")
        # 重建分组，让卡片内嵌颜色同步跟随主题
        self._render_content()

    def _on_failed(self, message: str) -> None:
        self.btn_detail_refresh.setEnabled(True)
        self.status.setText(tr("nb.load_failed", msg=message))


# ------------------------------------------------------------------ 主视图

class NotebookView(QWidget):
    """微信读书笔记本页。"""

    def __init__(self, store: DataStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.setObjectName("notebookPage")
        self.books: List[Dict[str, Any]] = []
        self._worker: Optional[NotebooksWorker] = None
        self._export_worker: Optional[ExportWorker] = None
        _sync_palette(store)  # 先按主程序主题生成配色，再构建 UI
        self._build_ui()
        self.apply_colors()
        if str(self.store.settings_get("weread_api_key", "")).strip():
            from PySide6.QtCore import QTimer
            QTimer.singleShot(300, self.reload)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setStyleSheet(f"QWidget#notebookPage{{background-color:{NB_BG};}}")

        head = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(2)
        title_row = QHBoxLayout()
        self.title_lbl = GroupTitleLabel(tr("nb.title"), "inazuma", NB_ORANGE_L, font_size=23)
        self.title_lbl._nation_fixed_color = True
        title_row.addWidget(self.title_lbl)
        self.count_lbl = _lbl(tr("nb.books", n=0), NB_DIM, 12)
        self.count_lbl.setContentsMargins(6, 0, 0, 0)
        title_row.addWidget(self.count_lbl)
        title_row.addStretch()
        left.addLayout(title_row)
        head.addLayout(left)
        head.addStretch()

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("nb.search_ph"))
        self.search_edit.setFixedWidth(220)
        self.search_edit.setStyleSheet(
            f"QLineEdit{{background-color:{NB_CARD2};color:{NB_TEXT};"
            f"border:1px solid {NB_LINE};border-radius:16px;padding:6px 12px;}}"
        )
        self.search_edit.returnPressed.connect(self.reload)
        head.addWidget(self.search_edit)
        self.btn_export = _btn(tr("nb.export_all"), primary=True)
        apply_nation_icon(self.btn_export, "liyue", "#ffffff", 16)
        self.btn_export._nation_fixed_color = True
        self.btn_export.clicked.connect(self.export_all)
        head.addWidget(self.btn_export)
        self.btn_refresh = _btn(tr("nb.refresh"))
        self.btn_refresh.clicked.connect(self.reload)
        head.addWidget(self.btn_refresh)
        root.addLayout(head)

        self.status_lbl = _lbl(tr("nb.hint"), NB_DIM, 11)
        root.addWidget(self.status_lbl)

        # 网格区（可滚动）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"QScrollArea{{background-color:{NB_BG};border:none;}}")
        grid_host = QWidget()
        grid_host.setStyleSheet(f"QWidget{{background-color:{NB_BG};}}")
        self.grid = QGridLayout(grid_host)
        self.grid.setSpacing(12)
        self.grid.setContentsMargins(2, 4, 2, 4)
        scroll.setWidget(grid_host)
        root.addWidget(scroll, 1)
        self._scroll = scroll
        self._grid_host = grid_host

    def apply_colors(self) -> None:
        """跟随主程序深浅主题 + 主题色，刷新整页与所有卡片。"""
        _sync_palette(self.store)
        self.setStyleSheet(f"QWidget#notebookPage{{background-color:{NB_BG};}}")
        if getattr(self, "_scroll", None):
            self._scroll.setStyleSheet(f"QScrollArea{{background-color:{NB_BG};border:none;}}")
        if getattr(self, "_grid_host", None):
            self._grid_host.setStyleSheet(f"QWidget{{background-color:{NB_BG};}}")
        # 顶部标题国徽与文案
        self.title_lbl.set_icon_color(NB_ORANGE_L)
        self.count_lbl.setStyleSheet(
            f"color:{NB_DIM};font-size:12px;background:transparent;border:none;padding:0;"
            "margin-left:6px;")
        self.status_lbl.setStyleSheet(
            f"color:{NB_DIM};font-size:11px;background:transparent;border:none;padding:0;")
        # 搜索框
        self.search_edit.setStyleSheet(
            f"QLineEdit{{background-color:{NB_CARD2};color:{NB_TEXT};"
            f"border:1px solid {NB_LINE};border-radius:16px;padding:6px 12px;}}"
        )
        # 按钮：重建样式（_btn 读取的是模块级动态色）+ 国徽重染
        self.btn_export.setStyleSheet(
            f"QPushButton{{background-color:{NB_ORANGE};color:#ffffff;border:none;"
            "border-radius:8px;padding:6px 14px;font-weight:bold;}"
            f"QPushButton:hover{{background-color:{NB_ORANGE_L};}}"
        )
        apply_nation_icon(self.btn_export, "liyue", "#ffffff", 16)
        self.btn_export._nation_fixed_color = True
        self.btn_refresh.setStyleSheet(
            f"QPushButton{{background-color:{NB_CARD2};color:{NB_TEXT};"
            f"border:1px solid {NB_LINE};border-radius:8px;padding:6px 14px;}}"
            f"QPushButton:hover{{background-color:{NB_HOVER};}}"
        )
        # 所有卡片跟随
        for i in range(self.grid.count()):
            w = self.grid.itemAt(i).widget()
            if isinstance(w, NotebookCard):
                w.apply_colors()

    def retranslate(self) -> None:
        """语言切换后刷新静态文案；只更新现有卡片标签，不触发网络请求。"""
        self.title_lbl.setText(tr("nb.title"))
        self.count_lbl.setText(tr("nb.books", n=getattr(self, "_shown_count", len(self.books))))
        self.search_edit.setPlaceholderText(tr("nb.search_ph"))
        self.btn_export.setText(tr("nb.export_all"))
        self.btn_refresh.setText(tr("nb.refresh"))
        for i in range(self.grid.count()):
            w = self.grid.itemAt(i).widget()
            if isinstance(w, NotebookCard):
                w.retranslate()

    # ------------------------------------------------------------------ 加载

    def reload(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        key = str(self.store.settings_get("weread_api_key", "")).strip()
        if not key:
            self.status_lbl.setText(tr("notebook.need_key"))
            return
        self.btn_refresh.setEnabled(False)
        self.status_lbl.setText(tr("notebook.loading"))
        self._worker = NotebooksWorker(key, self)
        self._worker.finished.connect(self._on_books)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_books(self, books: List[Dict[str, Any]]) -> None:
        self.btn_refresh.setEnabled(True)
        self.books = books
        kw = self.search_edit.text().strip().lower()
        shown = [b for b in books if not kw or kw in b["title"].lower() or kw in b["author"].lower()]
        self._shown_count = len(shown)
        self.count_lbl.setText(tr("nb.books", n=self._shown_count))
        self._render_grid(shown)
        self.status_lbl.setText(f"✓ 共 {len(books)} 本有笔记的书" if books else "还没有笔记")
        self.btn_export.setEnabled(bool(books))

    def _on_failed(self, message: str) -> None:
        self.btn_refresh.setEnabled(True)
        self.status_lbl.setText(f"加载失败：{message}")

    def set_books(self, books: List[Dict[str, Any]]) -> None:
        """供外部（如模拟数据/仪表盘）直接注入书籍列表。"""
        self.books = books or []
        self._shown_count = len(self.books)
        self.count_lbl.setText(tr("nb.books", n=self._shown_count))
        self._render_grid(self.books)
        self.btn_export.setEnabled(bool(self.books))

    def apply_external_books(self, books: List[Dict[str, Any]]) -> None:
        """微信读书仪表盘刷新后实时联动：无网络请求，直接拿共享数据重绘。"""
        self.set_books(books)
        self.status_lbl.setText(f"✓ 已与微信读书同步：{len(books)} 本有笔记的书")

    def _render_grid(self, books: List[Dict[str, Any]]) -> None:
        # 清空旧卡片（安全释放封面线程）；同步移除避免无事件循环时残留叠加
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w:
                if isinstance(w, NotebookCard):
                    w.cover.clear_loader()
                w.setParent(None)
                w.deleteLater()
        cols = 3
        for i, b in enumerate(books):
            card = NotebookCard(b)
            card.clicked.connect(self._open_detail)
            self.grid.addWidget(card, i // cols, i % cols)
        # 底部弹性占位
        for c in range(cols):
            spacer = QWidget()
            spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.grid.addWidget(spacer, (len(books) + cols - 1) // cols, c)

    def _open_detail(self, book: Dict[str, Any]) -> None:
        key = str(self.store.settings_get("weread_api_key", "")).strip()
        if not key:
            QMessageBox.information(self, tr("common.tip"), tr("nb.need_api_key"))
            return
        dlg = NotebookDetailDialog(key, book, self)
        dlg.exec()

    # ------------------------------------------------------------------ 导出

    def export_all(self) -> None:
        if self._export_worker is not None and self._export_worker.isRunning():
            return
        if not self.books:
            QMessageBox.information(self, tr("common.tip"), tr("nb.no_notes"))
            return
        key = str(self.store.settings_get("weread_api_key", "")).strip()
        if not key:
            QMessageBox.information(self, tr("common.tip"), tr("nb.need_api_key"))
            return
        path = _fd.save_file(
            self, "导出笔记", os.path.expanduser("~/微信读书笔记.md"),
            "Markdown 文件 (*.md);;文本文件 (*.txt)",
        )
        if not path:
            return
        self.btn_export.setEnabled(False)
        self.status_lbl.setText(tr("notebook.exporting"))
        self._export_worker = ExportWorker(key, self.books, path, self)
        self._export_worker.progress.connect(lambda m: self.status_lbl.setText(m))
        self._export_worker.finished.connect(self._on_export_done)
        self._export_worker.failed.connect(self._on_export_failed)
        self._export_worker.start()

    def _on_export_done(self, path: str) -> None:
        self.btn_export.setEnabled(True)
        self.status_lbl.setText(f"✓ 笔记已导出：{path}")
        QMessageBox.information(self, tr("common.export_done"), tr("nb.exported_to", path=path))

    def _on_export_failed(self, message: str) -> None:
        self.btn_export.setEnabled(True)
        self.status_lbl.setText(f"导出失败：{message}")
