# -*- coding: utf-8 -*-
"""下载选项对话框：选择章节范围、格式，并预览输出文件名。"""

from __future__ import annotations

from typing import Any, Dict

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QCheckBox, QLineEdit, QColorDialog, QGroupBox, QMenu,
)

from .qimao_core import (render_filename, font_combo_items,
                         CHAPTER_TITLE_COLOR, VOLUME_TITLE_COLOR,
                         TITLE_COLOR_PRESETS)
from .nation_theme import color_swatch_icon
from .i18n import tr


class DownloadDialog(QDialog):
    """让用户确认下载范围与输出文件名。

    接受 book 字典与全局默认（格式、模板）， confirms 后通过 options() 取参数。
    """

    def __init__(self, book: Dict[str, Any], fmt: str = "txt",
                 template: str = "", parent=None,
                 chapter_font: str = "", volume_font: str = "",
                 chapter_color: str = "", volume_color: str = "") -> None:
        super().__init__(parent)
        self.book = book
        self.setWindowTitle(tr("dlg.title"))
        self.setMinimumWidth(440)
        # EPUB 排版默认值（来自全局设置，可在本次下载临时改）
        self._default_chapter_font = chapter_font or "builtin:mashan"
        self._default_volume_font = volume_font or "builtin:zhimang"
        self._default_chapter_color = chapter_color or CHAPTER_TITLE_COLOR
        self._default_volume_color = volume_color or VOLUME_TITLE_COLOR
        lay = QVBoxLayout(self)

        title = book.get("title", "")
        lay.addWidget(QLabel(tr("dlg.book", title=title)))

        # 格式
        row_fmt = QHBoxLayout()
        row_fmt.addWidget(QLabel(tr("dlg.format")))
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(["txt", "chapter", "epub"])
        idx = self.fmt_combo.findText(fmt)
        if idx >= 0:
            self.fmt_combo.setCurrentIndex(idx)
        row_fmt.addWidget(self.fmt_combo)
        row_fmt.addStretch()
        lay.addLayout(row_fmt)

        # 章节范围
        self.range_check = QCheckBox(tr("dlg.range_check"))
        lay.addWidget(self.range_check)
        row_range = QHBoxLayout()
        row_range.addWidget(QLabel(tr("dlg.from")))
        self.start_spin = QSpinBox()
        self.start_spin.setRange(1, 999999)
        self.start_spin.setValue(1)
        self.end_spin = QSpinBox()
        self.end_spin.setRange(1, 999999)
        self.end_spin.setValue(1)
        row_range.addWidget(self.start_spin)
        row_range.addWidget(QLabel(tr("dlg.to")))
        row_range.addWidget(self.end_spin)
        row_range.addWidget(QLabel(tr("dlg.chapter")))
        row_range.addStretch()
        lay.addLayout(row_range)
        self.start_spin.setEnabled(False)
        self.end_spin.setEnabled(False)
        self.range_check.toggled.connect(self.start_spin.setEnabled)
        self.range_check.toggled.connect(self.end_spin.setEnabled)

        # 文件名模板（可在此临时修改）
        row_tpl = QHBoxLayout()
        row_tpl.addWidget(QLabel(tr("dlg.filename")))
        self.template_edit = QLineEdit(template or "{title}")
        row_tpl.addWidget(self.template_edit)
        lay.addLayout(row_tpl)
        lay.addWidget(QLabel(tr("dlg.placeholders")))

        # ===== EPUB 排版选项（仅 EPUB 生效，只影响书本身）=====
        epub_group = QGroupBox(tr("dlg.epub_group"))
        epub_lay = QVBoxLayout(epub_group)
        # 章名字体
        row_ch_font = QHBoxLayout()
        row_ch_font.addWidget(QLabel(tr("dlg.chapter_font")))
        self.dlg_chapter_font = QComboBox()
        for value, label in font_combo_items():
            self.dlg_chapter_font.addItem(label, value)
        idx = self.dlg_chapter_font.findData(self._default_chapter_font)
        if idx >= 0:
            self.dlg_chapter_font.setCurrentIndex(idx)
        row_ch_font.addWidget(self.dlg_chapter_font, 1)
        epub_lay.addLayout(row_ch_font)
        # 卷名字体
        row_vol_font = QHBoxLayout()
        row_vol_font.addWidget(QLabel(tr("dlg.volume_font")))
        self.dlg_volume_font = QComboBox()
        self.dlg_volume_font.addItem(tr("dlg.font_auto"), "__auto__")
        for value, label in font_combo_items():
            self.dlg_volume_font.addItem(label, value)
        idx = self.dlg_volume_font.findData(self._default_volume_font)
        if idx >= 0:
            self.dlg_volume_font.setCurrentIndex(idx)
        row_vol_font.addWidget(self.dlg_volume_font, 1)
        epub_lay.addLayout(row_vol_font)
        # 颜色
        row_color = QHBoxLayout()
        row_color.addWidget(QLabel(tr("dlg.title_color")))
        self.dlg_chapter_color = self._default_chapter_color
        self.dlg_volume_color = self._default_volume_color
        self.btn_chapter_color = QPushButton()
        self.btn_volume_color = QPushButton()
        self.btn_chapter_color.clicked.connect(lambda: self._pick_color("chapter"))
        self.btn_volume_color.clicked.connect(lambda: self._pick_color("volume"))
        row_color.addWidget(self.btn_chapter_color, 1)
        row_color.addWidget(self.btn_volume_color, 1)
        epub_lay.addLayout(row_color)
        self._refresh_color_buttons()
        lay.addWidget(epub_group)

        # 预览
        self.preview = QLabel()
        self.preview.setStyleSheet("color: #888;")
        lay.addWidget(self.preview)
        self._update_preview()
        self.template_edit.textChanged.connect(self._update_preview)
        self.range_check.toggled.connect(self._update_preview)
        self.start_spin.valueChanged.connect(self._update_preview)
        self.end_spin.valueChanged.connect(self._update_preview)
        self.fmt_combo.currentTextChanged.connect(self._update_preview)

        # 按钮
        btns = QHBoxLayout()
        ok = QPushButton(tr("dlg.ok"))
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel = QPushButton(tr("dlg.cancel"))
        cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)

    def _refresh_color_buttons(self) -> None:
        """把当前颜色显示在按钮上（色块图标 + 标签）；渐变显示图标而非色值文本。"""
        for btn, color, label in (
            (self.btn_chapter_color, self.dlg_chapter_color, tr("dlg.chapter_color")),
            (self.btn_volume_color, self.dlg_volume_color, tr("dlg.volume_color")),
        ):
            btn.setIcon(color_swatch_icon(color))
            btn.setText(f" {label}")
            btn.setStyleSheet("padding: 3px 8px;")

    def _pick_color(self, which: str) -> None:
        """弹出颜色菜单：预设纯色/渐变 + 自定义取色器。"""
        menu = QMenu(self)
        current = self.dlg_chapter_color if which == "chapter" else self.dlg_volume_color
        for value, i18n_key in TITLE_COLOR_PRESETS:
            action = menu.addAction(color_swatch_icon(value), tr(i18n_key))
            action.triggered.connect(
                lambda _=False, v=value: self._apply_color(which, v))
        menu.addSeparator()
        custom = menu.addAction(tr("set.color_custom"))
        custom.triggered.connect(lambda _=False: self._pick_custom_color(which, current))
        menu.exec(self.sender().mapToGlobal(self.sender().rect().center()))

    def _pick_custom_color(self, which: str, fallback: str) -> None:
        """自定义取色器（仅纯色）；渐变请用预设。"""
        base = fallback if not fallback.startswith("linear:") else "#c62828"
        c = QColorDialog.getColor(QColor(base), self, tr("dlg.color_title"))
        if c.isValid():
            self._apply_color(which, c.name())

    def _apply_color(self, which: str, value: str) -> None:
        if which == "chapter":
            self.dlg_chapter_color = value
        else:
            self.dlg_volume_color = value
        self._refresh_color_buttons()

    def _update_preview(self) -> None:
        start = self.start_spin.value() if self.range_check.isChecked() else 0
        end = self.end_spin.value() if self.range_check.isChecked() else 0
        name = render_filename(
            self.template_edit.text(), self.book.get("title", ""),
            author=self.book.get("author", ""),
            book_id=str(self.book.get("id", "")),
            fmt=self.fmt_combo.currentText(),
            start=start, end=end,
        )
        ext = self.fmt_combo.currentText()
        if ext == "chapter":
            self.preview.setText(tr("dlg.preview_dir", name=name))
        else:
            self.preview.setText(tr("dlg.preview_file", name=name, ext=ext))

    def options(self) -> Dict[str, Any]:
        """返回下载选项：range/template + EPUB 排版（字体/颜色）。"""
        scoped = self.range_check.isChecked()
        vol = str(self.dlg_volume_font.currentData() or "__auto__")
        return {
            "start_chapter": self.start_spin.value() if scoped else 0,
            "end_chapter": self.end_spin.value() if scoped else 0,
            "filename_template": self.template_edit.text().strip(),
            # 仅 EPUB 生效；非 EPUB 格式下载核心会忽略这些参数
            "chapter_font": str(self.dlg_chapter_font.currentData() or ""),
            "volume_font": "" if vol == "__auto__" else vol,
            "chapter_color": self.dlg_chapter_color,
            "volume_color": self.dlg_volume_color,
        }

    def format(self) -> str:
        return self.fmt_combo.currentText()
