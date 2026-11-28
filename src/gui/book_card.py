# -*- coding: utf-8 -*-
"""
书籍卡片组件 + 卡片列表：复刻番茄小说下载器搜索页的卡片式结果。

每张卡片显示：封面、书名、评分、标签、连载状态、字数、简介。
封面异步加载（QThread），避免阻塞界面；"换一批"时会安全销毁加载线程，
避免 "QThread: Destroyed while thread is still running" 崩溃。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor, QFont
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QFrame,
)

from .style import BG_CARD, BORDER, TEXT, TEXT_DIM, ACCENT, ACCENT_DARK, WARN

# 换一批时若封面线程尚未结束，保留引用直到线程结束，
# 避免销毁仍在运行的 QThread 导致进程崩溃。
_orphan_loaders = set()


def _release_loader(loader: "CoverLoader") -> None:
    _orphan_loaders.discard(loader)
    loader.deleteLater()


class CoverLoader(QThread):
    """异步加载封面图片。"""

    loaded = Signal(str, QImage)  # url, image

    def __init__(self, url: str, app_aid: str = "", title: str = "") -> None:
        # 不设 parent：宿主卡片销毁时不要连带销毁可能仍在运行的线程
        super().__init__()
        self.url = str(url or "")
        # 文库8 App API 封面是 POST + base64 表单返回的二进制图片，
        # 不是普通 HTTP URL。搜索结果只带 aid，封面由这里异步补齐，
        # 避免为了封面拖慢搜索线程。
        self.app_aid = str(app_aid or "").strip()
        # 书名用于严格匹配 Bangumi 官方高清大图（括号别名参与匹配）
        self.title = str(title or "")
        # 从创建起就纳入全局追踪：结束时自动释放；程序退出时由主窗口统一停止。
        # 否则卡片被 deleteLater 后，运行中的线程随即被 GC 销毁，触发
        # "QThread: Destroyed while thread is still running" 警告。
        _orphan_loaders.add(self)
        self.finished.connect(lambda l=self: _release_loader(l))

    def run(self) -> None:
        try:
            # 文库8（无论是否带 app_aid）优先取 Bangumi 官方高清大图，
            # 未命中再回退原通道（图片直链 / App API 二进制接口）。
            from .wenku8_core.wenku8_official_cover import fetch_official_cover
            aid = self.app_aid
            if aid:
                official = fetch_official_cover(aid, self.title)
                if official:
                    img = QImage.fromData(official)
                    if not img.isNull():
                        self.loaded.emit(aid, img)
                        return
            if self.app_aid:
                from .wenku8_core.wenku8_app_api import fetch_cover_data
                # 图片直链优先（参考项目同款），失败回退 App API 二进制接口
                raw = fetch_cover_data(self.app_aid)
                img = QImage.fromData(raw) if raw else QImage()
                if not img.isNull():
                    self.loaded.emit(self.app_aid, img)
                return
            # 无 cover_aid 时走 cover URL。裸 requests.get 会被 img CDN 403
            #（返回 HTML 错误页）——这是全部书封面变占位图的根因。统一走
            # cover_helper 的智能入口（含官方大图 + 带头小图兜底）。
            from .cover_helper import fetch_cover_smart
            raw = fetch_cover_smart(self.url, self.title)
            img = QImage.fromData(raw) if raw else QImage()
            if not img.isNull():
                self.loaded.emit(self.url, img)
        except Exception:  # noqa: BLE001
            pass


class BookCard(QFrame):
    """单本书卡片。"""

    clicked = Signal(dict)

    def __init__(self, book: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.book = book
        self._loader: Optional[CoverLoader] = None
        self._cover_img: Optional[QImage] = None
        self._dark = True
        self.setObjectName("bookCard")
        self._apply_style()
        self.setCursor(Qt.PointingHandCursor)
        self._build()

    # ------------------------------------------------------------------ 样式

    def _apply_style(self) -> None:
        # 玻璃质感（ui_glass 开关）：开启时卡片同色相半透明 + 柔光边 + 大圆角；
        # 关闭时恢复原实心卡片。hover 始终用原强调色（主题色圆点/重染逻辑不变）。
        from .style import glass_effect_enabled, GLASS_SURFACE, GLASS_RADIUS
        hover = ACCENT if self._dark else ACCENT_DARK
        if glass_effect_enabled():
            surface = GLASS_SURFACE[bool(self._dark)]
            bg, bd, radius = surface["page"], surface["edge"], GLASS_RADIUS
        else:
            bg = BG_CARD if self._dark else "#ffffff"
            bd = BORDER if self._dark else "#e5e5e5"
            radius = 8
        self.setStyleSheet(f"""
            QFrame#bookCard {{ background-color: {bg}; border: 1px solid {bd};
                               border-radius: {radius}px; }}
            QFrame#bookCard:hover {{ border: 1px solid {hover}; }}
        """)

    def refresh_theme(self, dark: bool) -> None:
        self._dark = dark
        self._apply_style()
        self._placeholder_cover()
        # 文字颜色也需跟随主题：深色主题的浅灰/淡紫在白底上不可读
        title_c = TEXT if dark else "#2a2a2a"
        meta_c = ACCENT if dark else "#7E6AA6"
        dim_c = TEXT_DIM if dark else "#666666"
        score_c = WARN if dark else "#e65100"
        if getattr(self, "title_label", None):
            self.title_label.setStyleSheet(
                f"color: {title_c}; font-size: 14px; font-weight: bold;")
        if getattr(self, "score_label", None):
            self.score_label.setStyleSheet(f"color: {score_c}; font-size: 12px;")
        if getattr(self, "meta_label", None):
            self.meta_label.setStyleSheet(f"color: {meta_c}; font-size: 11px;")
        if getattr(self, "tag_label", None):
            self.tag_label.setStyleSheet(f"color: {dim_c}; font-size: 11px;")
        if getattr(self, "intro_label", None):
            self.intro_label.setStyleSheet(f"color: {dim_c}; font-size: 11px;")

    # ------------------------------------------------------------------ 内容

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        # 封面
        self.cover = QLabel()
        self.cover.setFixedSize(60, 80)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setStyleSheet("background: transparent; border-radius:4px;")
        self._placeholder_cover()
        layout.addWidget(self.cover, 0, Qt.AlignTop)

        # 文字区
        info = QVBoxLayout()
        info.setSpacing(3)

        # 书名 + 评分
        head = QHBoxLayout()
        self.title_label = QLabel(self.book.get("title", "未知书名"))
        self.title_label.setStyleSheet(
            f"color: {TEXT if self._dark else '#2a2a2a'}; font-size: 14px; font-weight: bold;")
        self.title_label.setWordWrap(True)
        head.addWidget(self.title_label, 1)
        score = self.book.get("score", "")
        self.score_label = None
        if score:
            self.score_label = QLabel(f"⭐ {score}")
            self.score_label.setStyleSheet(
                f"color: {WARN if self._dark else '#e65100'}; font-size: 12px;")
            head.addWidget(self.score_label, 0, Qt.AlignRight)
        info.addLayout(head)

        # 状态 + 字数 + 作者
        status = "已完结" if self.book.get("is_over") else "连载中"
        self.meta_label = QLabel(
            f"{status} · {self.book.get('words_num', '—')} · {self.book.get('author', '')}"
        )
        self.meta_label.setStyleSheet(
            f"color: {ACCENT if self._dark else '#7E6AA6'}; font-size: 11px;")
        info.addWidget(self.meta_label)

        # 标签
        tags = self.book.get("tags", "")
        self.tag_label = None
        if tags:
            tag_text = "  ".join(f"[{t}]" for t in str(tags).split(",")[:6])
            self.tag_label = QLabel(tag_text)
            self.tag_label.setStyleSheet(
                f"color: {TEXT_DIM if self._dark else '#666666'}; font-size: 11px;")
            self.tag_label.setWordWrap(True)
            info.addWidget(self.tag_label)

        # 简介
        intro = self.book.get("intro", "")
        self.intro_label = None
        if intro:
            self.intro_label = QLabel(str(intro)[:80] + ("…" if len(str(intro)) > 80 else ""))
            self.intro_label.setStyleSheet(
                f"color: {TEXT_DIM if self._dark else '#666666'}; font-size: 11px;")
            self.intro_label.setWordWrap(True)
            info.addWidget(self.intro_label)

        layout.addLayout(info, 1)

        # 异步加载封面：文库8 App API 结果用 cover_aid 走专用封面接口，
        # 其他书源仍使用普通 HTTP 封面 URL。
        cover_url = self.book.get("cover", "")
        cover_aid = str(self.book.get("cover_aid", "") or "").strip()
        # 文库8兜底：目录/网页结果可能只给 id 没有 cover_aid，按 aid 构造
        # 官方 CDN 直链（fetch_cover_data 通道最稳），避免走裸 URL 时被 403。
        if not cover_aid and not cover_url \
                and str(self.book.get("source", "")) == "wenku8" \
                and str(self.book.get("id", "")).isdigit():
            cover_aid = str(self.book["id"])
        if cover_aid or cover_url:
            self._loader = CoverLoader(
                cover_url, app_aid=cover_aid,
                title=str(self.book.get("title", "") or ""))
            self._loader.loaded.connect(self._on_cover)
            self._loader.start()

    def _set_cover_pixmap(self, img: QImage) -> None:
        self.cover.setPixmap(
            QPixmap.fromImage(img).scaled(
                60, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

    def _placeholder_cover(self) -> None:
        if self._cover_img is not None and not self._cover_img.isNull():
            self._set_cover_pixmap(self._cover_img)
            return
        img = QImage(60, 80, QImage.Format_RGB32)
        img.fill(QColor("#3a3a3a" if self._dark else "#e8e8e8"))
        p = QPainter(img)
        p.setPen(QColor("#888888" if self._dark else "#aaaaaa"))
        f = QFont("Microsoft YaHei", 20)
        p.setFont(f)
        p.drawText(img.rect(), Qt.AlignCenter, "书")
        p.end()
        self.cover.setPixmap(QPixmap.fromImage(img))

    def _on_cover(self, url: str, img: QImage) -> None:
        if not img.isNull():
            self._cover_img = img
            self._set_cover_pixmap(img)

    def dispose(self) -> None:
        """销毁前安全处理仍在运行的封面线程。

        封面线程可能在 finished 回调里被 _release_loader -> deleteLater 回收，
        此时 C++ 对象已失效，直接 isRunning()/wait() 会抛
        "Internal C++ object already deleted"。先用 shiboken 校验有效性，
        再兜底捕获 RuntimeError，保证快速连续搜索（换书源/连点）不崩溃。
        """
        loader = self._loader
        if loader is None:
            return
        self._loader = None
        try:
            loader.loaded.disconnect(self._on_cover)
        except (RuntimeError, TypeError):  # noqa: BLE001
            pass
        # loader 已在 __init__ 纳入 _orphan_loaders 并连接了自释放；
        # 这里只需尽量等待其自然结束，超时则由退出流程统一停止
        try:
            try:
                from shiboken6 import isValid  # type: ignore
                alive = isValid(loader)
            except Exception:  # noqa: BLE001
                alive = True
            if alive and loader.isRunning():
                loader.wait(1200)
        except RuntimeError:  # noqa: BLE001
            pass

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.book)
        super().mousePressEvent(event)


class BookCardList(QListWidget):
    """卡片式书籍列表。"""

    book_selected = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setStyleSheet("""
            QListWidget { background: transparent; border: none; }
            QListWidget::item { border: none; padding: 2px; }
            QListWidget::item:selected { background: transparent; }
        """)
        self.itemClicked.connect(self._on_item_clicked)

    def set_books(self, books: List[Dict[str, Any]]) -> None:
        # 先释放旧卡片的封面线程，再清空列表
        for i in range(self.count()):
            card = self.itemWidget(self.item(i))
            if isinstance(card, BookCard):
                card.dispose()
        self.clear()
        for book in books:
            item = QListWidgetItem()
            card = BookCard(book, self)
            card.clicked.connect(lambda b: self.book_selected.emit(b))
            item.setSizeHint(card.sizeHint())
            self.addItem(item)
            self.setItemWidget(item, card)

    def refresh_theme(self, dark: bool) -> None:
        for i in range(self.count()):
            card = self.itemWidget(self.item(i))
            if isinstance(card, BookCard):
                card.refresh_theme(dark)

    def refresh_glass(self) -> None:
        """玻璃质感开关切换后调用：重刷每张书卡的内联样式（即时生效）。"""
        for i in range(self.count()):
            card = self.itemWidget(self.item(i))
            if isinstance(card, BookCard):
                card._apply_style()

    def _on_item_clicked(self, item) -> None:
        card = self.itemWidget(item)
        if isinstance(card, BookCard):
            self.book_selected.emit(card.book)
