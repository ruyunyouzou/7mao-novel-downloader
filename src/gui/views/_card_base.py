# -*- coding: utf-8 -*-
"""任务队列 / 历史记录共用的 B2 卡片骨架。

两页卡片结构高度一致（封面位 → 标题 → 标签行 → 状态胶囊 → 分隔线 →
右对齐元信息行），差异只在各自显示哪些字段、状态文案与状态色来源。
本模块把公共部分收敛到 ``_CardBase``，子类只需：
- 声明 ``NATIONS``（各信息项用哪国国徽）
- 实现 ``_build_fields``（往标签行 / 元信息行填字段）
- 实现 ``update_data``（填数据，内部调 ``_apply_status``）
"""

from __future__ import annotations

from typing import Dict

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ..style import ACCENT
from ..widgets import nation_pixmap


#: 各信息项使用的国徽（与顶部工具栏按钮的区分语言保持一致）
DEFAULT_NATIONS: Dict[str, str] = {
    "author": "natlan",
    "fmt": "liyue",
    "status": "mondstadt",
    "progress": "sumeru",
    "chapter": "fontaine",
    "elapsed": "inazuma",
    "time": "snezhnaya",
}


class CardChip:
    """「国徽 + 文字」小块的句柄：持有两个 QLabel，便于成对读写。"""

    __slots__ = ("icon", "text")

    def __init__(self, layout: QHBoxLayout, icon_size: int, text_object_name: str) -> None:
        self.icon = QLabel()
        self.icon.setFixedSize(icon_size, icon_size)
        layout.addWidget(self.icon)
        self.text = QLabel()
        self.text.setObjectName(text_object_name)
        layout.addWidget(self.text)

    def set_icon(self, nation: str) -> None:
        self.icon.setPixmap(nation_pixmap(nation, ACCENT, self.icon.width()))

    def set_text(self, text: str, tip: str | None = None) -> None:
        self.text.setText(text)
        self.text.setToolTip(text if tip is None else tip)


class _CardBase(QFrame):
    """B2 卡片基类：搭好公共骨架，子类填内容。"""

    clicked = Signal(str)

    #: 卡片根 objectName（同时是 QSS 选择器的一部分，子类覆盖）
    CARD_OBJECT = ""
    #: 封面位 objectName（子类覆盖）
    COVER_OBJECT = ""
    #: 标题 objectName（子类覆盖）
    TITLE_OBJECT = ""
    #: 分隔线 objectName（子类覆盖）
    LINE_OBJECT = ""
    #: 元信息文字 objectName（子类覆盖）
    META_OBJECT = ""
    #: 状态胶囊 objectName（子类覆盖）
    PILL_OBJECT = ""
    #: 状态文字 objectName（子类覆盖）
    STATUS_OBJECT = ""
    ICON_SIZE = 13

    def __init__(self, view, item_id: str = "", parent=None) -> None:
        super().__init__(parent)
        self.view = view
        self.item_id = item_id
        self.nations = dict(DEFAULT_NATIONS)
        self.setObjectName(self.CARD_OBJECT)
        self.setCursor(Qt.PointingHandCursor)
        self._build_base()
        self._build_fields()

    # ------------------------------------------------------------------ 骨架

    def _chip(self, layout: QHBoxLayout) -> CardChip:
        return CardChip(layout, self.ICON_SIZE, self.META_OBJECT)

    def _build_base(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # 顶部：封面位 + 标题 + 标签行 + 状态胶囊
        top = QHBoxLayout()
        top.setSpacing(10)
        self.cover = QLabel()
        self.cover.setObjectName(self.COVER_OBJECT)
        self.cover.setFixedSize(34, 46)
        self.cover.setAlignment(Qt.AlignCenter)
        top.addWidget(self.cover)

        mid = QVBoxLayout()
        mid.setSpacing(3)
        self.title = QLabel()
        self.title.setObjectName(self.TITLE_OBJECT)
        mid.addWidget(self.title)
        self.tags = QHBoxLayout()
        self.tags.setSpacing(10)
        mid.addLayout(self.tags)
        top.addLayout(mid, 1)

        self.status_pill = QFrame()
        self.status_pill.setObjectName(self.PILL_OBJECT)
        spl = QHBoxLayout(self.status_pill)
        spl.setContentsMargins(8, 3, 8, 3)
        spl.setSpacing(4)
        self.status_icon = QLabel()
        self.status_icon.setFixedSize(self.ICON_SIZE, self.ICON_SIZE)
        self.status_dot = QLabel("●")
        self.status_text = QLabel()
        self.status_text.setObjectName(self.STATUS_OBJECT)
        spl.addWidget(self.status_icon)
        spl.addWidget(self.status_dot)
        spl.addWidget(self.status_text)
        top.addWidget(self.status_pill, 0, Qt.AlignTop)
        root.addLayout(top)

        # 卡片中部（队列页放进度条，历史页留空）
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        root.addLayout(self.body)

        # 分隔线
        line = QFrame()
        line.setObjectName(self.LINE_OBJECT)
        line.setFixedHeight(1)
        root.addWidget(line)

        # 右对齐元信息行
        self.meta = QHBoxLayout()
        self.meta.setSpacing(18)
        self.meta.addStretch(1)
        root.addLayout(self.meta)

    def _add_tag(self, chip: CardChip) -> None:
        """把标签追加到标签行，并保证末尾始终是 stretch（标签靠左聚拢）。

        标签行不预置 stretch，改由本方法在末尾显式追加，避免先加 stretch
        再插入控件导致 stretch 落到中间、标签被拉散。
        """
        self.tags.addWidget(chip.icon)
        self.tags.addWidget(chip.text)

    def _finish_tags(self) -> None:
        """标签填完后调用：在末尾补 stretch。"""
        self.tags.addStretch(1)

    # ------------------------------------------------------------------ 子类接口

    def _build_fields(self) -> None:
        raise NotImplementedError

    def _set_title(self, title: str) -> None:
        self.title.setText(title)
        self.title.setToolTip(title)
        self.cover.setText(title[:1] or "书")
        self.cover.setToolTip(title)

    def _apply_status(self, text: str, dot_color: str, text_color: str,
                      nation: str | None = None) -> None:
        """统一设置状态徽章：国徽 + 彩色圆点 + 文字 + 胶囊底色。"""
        if nation:
            self.status_icon.setPixmap(nation_pixmap(nation, ACCENT, self.ICON_SIZE))
        self.status_dot.setStyleSheet(f"color: {dot_color};")
        self.status_text.setStyleSheet(f"color: {text_color}; font-weight: 600;")
        self.status_text.setText(text)
        self.status_text.setToolTip(text)
        c = QColor(dot_color)
        c.setAlpha(38)
        self.status_pill.setStyleSheet(
            f"QFrame#{self.PILL_OBJECT} {{ background: rgba("
            f"{c.red()},{c.green()},{c.blue()},{c.alphaF():.2f}); "
            f"border-radius: 10px; }}")

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    # ------------------------------------------------------------------ 交互

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.item_id)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.view._show_context_menu_for(self.item_id, self.mapToGlobal(event.pos()))
