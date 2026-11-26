# -*- coding: utf-8 -*-
"""下载选项对话框：选择章节范围、格式，并预览输出文件名。

EPUB 排版选项只影响本次下载（临时覆盖全局设置），不修改设置页的持久化值。
"""

from __future__ import annotations

from typing import Any, Dict

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QCheckBox, QLineEdit, QColorDialog, QGroupBox,
    QScrollArea, QFrame, QFileDialog,
)

from .qimao_core import (render_filename, font_combo_items,
                         CHAPTER_TITLE_COLOR, VOLUME_TITLE_COLOR,
                         TITLE_COLOR_PRESETS)
from .nation_theme import color_swatch_icon
from .i18n import tr


class DownloadDialog(QDialog):
    """确认下载范围、文件名，并临时设置 EPUB 排版参数。

    接受 book 字典与全局默认（格式、模板、EPUB 各项），确认后通过
    options() 取参数；这些参数会透传给下载插件/内核，只影响本次下载。
    """

    def __init__(self, book: Dict[str, Any], fmt: str = "txt",
                 template: str = "", parent=None,
                 chapter_font: str = "", volume_font: str = "",
                 chapter_color: str = "", volume_color: str = "",
                 colorize_dialogue: bool = True, smart_art: bool = True,
                 chapter_hero: bool = False, hero_mode: str = "rotate",
                 hero_fixed: str = "", chapter_tail: bool = False,
                 tail_mode: str = "rotate", tail_fixed: str = "") -> None:
        super().__init__(parent)
        self.book = book
        self.setWindowTitle(tr("dlg.title"))
        self.setMinimumWidth(500)

        # EPUB 排版默认值（来自全局设置，可在本次下载临时改）
        self._default_chapter_font = chapter_font or "builtin:mashan"
        self._default_volume_font = volume_font or "builtin:zhimang"
        self._default_chapter_color = chapter_color or CHAPTER_TITLE_COLOR
        self._default_volume_color = volume_color or VOLUME_TITLE_COLOR
        self.dlg_chapter_color = self._default_chapter_color
        self.dlg_volume_color = self._default_volume_color

        # 内容较多：整体放进滚动区，保证底部按钮始终可见
        outer = QVBoxLayout(self)
        content = QWidget()
        lay = QVBoxLayout(content)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

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

        # 文件名模板
        row_tpl = QHBoxLayout()
        row_tpl.addWidget(QLabel(tr("dlg.filename")))
        self.template_edit = QLineEdit(template or "{title}")
        row_tpl.addWidget(self.template_edit)
        lay.addLayout(row_tpl)
        lay.addWidget(QLabel(tr("dlg.placeholders")))

        # ===== 官方信息修正（仅番茄书；App 端书名/封面比 Web 端新）=====
        self._official_group = None
        self.official_title_edit = None
        self.official_cover_edit = None
        self.official_cover_path = ""
        self.official_cover_label = None
        self._official_cleared = False
        if str(book.get("source", "") or "") == "fanqie":
            from .fanqie_core.meta_override import get_override
            bid = str(book.get("id", "") or "")
            override = get_override(bid)
            self.official_cover_path = override.get("cover_path", "")
            grp = QGroupBox(tr("dlg.official_group"))
            g_lay = QVBoxLayout(grp)
            hint = QLabel(tr("dlg.official_hint2"))
            hint.setWordWrap(True)
            hint.setStyleSheet("color: #888;")
            g_lay.addWidget(hint)
            row_ot = QHBoxLayout()
            row_ot.addWidget(QLabel(tr("dlg.official_title")))
            self.official_title_edit = QLineEdit(override.get("title", ""))
            self.official_title_edit.setPlaceholderText(str(book.get("title", "") or ""))
            row_ot.addWidget(self.official_title_edit, 1)
            g_lay.addLayout(row_ot)
            # 本地封面图（App「保存」得到的原图，推荐）
            row_pick = QHBoxLayout()
            pick_btn = QPushButton(tr("dlg.official_pick"))
            pick_btn.clicked.connect(self._pick_official_cover)
            row_pick.addWidget(pick_btn)
            self.official_cover_label = QLabel(
                tr("dlg.official_cover_set") if self.official_cover_path
                else tr("dlg.official_cover_none"))
            self.official_cover_label.setStyleSheet("color: #888;")
            row_pick.addWidget(self.official_cover_label, 1)
            g_lay.addLayout(row_pick)
            # 封面网址（备用：能拿到图片直链时用）
            row_oc = QHBoxLayout()
            row_oc.addWidget(QLabel(tr("dlg.official_cover")))
            self.official_cover_edit = QLineEdit(override.get("cover_url", ""))
            self.official_cover_edit.setPlaceholderText("https://...")
            row_oc.addWidget(self.official_cover_edit, 1)
            g_lay.addLayout(row_oc)
            clear_btn = QPushButton(tr("dlg.official_clear"))
            clear_btn.clicked.connect(self._clear_official)
            g_lay.addWidget(clear_btn)
            lay.addWidget(grp)
            self._official_group = grp

        # ===== EPUB 排版选项（仅 EPUB 生效，只影响本次下载）=====
        epub_group = QGroupBox(tr("dlg.epub_group"))
        epub_lay = QVBoxLayout(epub_group)
        hint = QLabel(tr("dlg.epub_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        epub_lay.addWidget(hint)

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

        # 章名 / 卷名颜色：改为与字体一致的下拉框（预设色块 + 自定义）
        row_ch_color = QHBoxLayout()
        row_ch_color.addWidget(QLabel(tr("set.chapter_color")))
        self.dlg_chapter_color_combo = self._build_color_combo(
            self._default_chapter_color)
        self.dlg_chapter_color_combo.currentIndexChanged.connect(
            lambda _: self._on_color_changed("chapter"))
        row_ch_color.addWidget(self.dlg_chapter_color_combo, 1)
        epub_lay.addLayout(row_ch_color)

        row_vol_color = QHBoxLayout()
        row_vol_color.addWidget(QLabel(tr("set.volume_color")))
        self.dlg_volume_color_combo = self._build_color_combo(
            self._default_volume_color)
        self.dlg_volume_color_combo.currentIndexChanged.connect(
            lambda _: self._on_color_changed("volume"))
        row_vol_color.addWidget(self.dlg_volume_color_combo, 1)
        epub_lay.addLayout(row_vol_color)

        # 人物对话彩色高亮
        self.colorize_check = QCheckBox(tr("set.epub_colorize"))
        self.colorize_check.setChecked(bool(colorize_dialogue))
        self.colorize_check.setToolTip(tr("set.epub_colorize_tip"))
        epub_lay.addWidget(self.colorize_check)

        # 智能选题预设
        self.smart_art_check = QCheckBox(tr("set.epub_smart_art"))
        self.smart_art_check.setChecked(bool(smart_art))
        self.smart_art_check.setToolTip(tr("set.epub_smart_art_tip"))
        epub_lay.addWidget(self.smart_art_check)
        smart_hint = QLabel(tr("set.epub_smart_art_hint"))
        smart_hint.setWordWrap(True)
        smart_hint.setStyleSheet("color: #888;")
        epub_lay.addWidget(smart_hint)

        # 章首题图（原神祈愿立绘）
        self.hero_check = QCheckBox(tr("set.epub_hero"))
        self.hero_check.setChecked(bool(chapter_hero))
        self.hero_check.setToolTip(tr("set.epub_hero_tip"))
        self.hero_check.toggled.connect(self._sync_hero_ui)
        epub_lay.addWidget(self.hero_check)
        row_hero = QHBoxLayout()
        row_hero.addWidget(QLabel(tr("set.epub_hero_mode")))
        self.hero_mode_combo = QComboBox()
        self.hero_mode_combo.addItem(tr("set.epub_hero_rotate"), "rotate")
        self.hero_mode_combo.addItem(tr("set.epub_hero_fixed"), "fixed")
        mi = self.hero_mode_combo.findData(str(hero_mode or "rotate"))
        self.hero_mode_combo.setCurrentIndex(max(0, mi))
        self.hero_fixed_combo = self._build_fixed_combo("genshin", hero_fixed)
        self.hero_mode_combo.currentIndexChanged.connect(self._sync_hero_ui)
        row_hero.addWidget(self.hero_mode_combo)
        row_hero.addWidget(self.hero_fixed_combo, 1)
        epub_lay.addLayout(row_hero)

        # 章末尾图（原神地理志风景）
        self.tail_check = QCheckBox(tr("set.epub_tail"))
        self.tail_check.setChecked(bool(chapter_tail))
        self.tail_check.setToolTip(tr("set.epub_tail_tip"))
        self.tail_check.toggled.connect(self._sync_tail_ui)
        epub_lay.addWidget(self.tail_check)
        row_tail = QHBoxLayout()
        row_tail.addWidget(QLabel(tr("set.epub_tail_mode")))
        self.tail_mode_combo = QComboBox()
        self.tail_mode_combo.addItem(tr("set.epub_tail_rotate"), "rotate")
        self.tail_mode_combo.addItem(tr("set.epub_tail_fixed"), "fixed")
        ti = self.tail_mode_combo.findData(str(tail_mode or "rotate"))
        self.tail_mode_combo.setCurrentIndex(max(0, ti))
        self.tail_fixed_combo = self._build_fixed_combo("genshin_geo", tail_fixed)
        self.tail_mode_combo.currentIndexChanged.connect(self._sync_tail_ui)
        row_tail.addWidget(self.tail_mode_combo)
        row_tail.addWidget(self.tail_fixed_combo, 1)
        epub_lay.addLayout(row_tail)

        self._sync_hero_ui()
        self._sync_tail_ui()
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
        outer.addLayout(btns)

    # ---------- 标题颜色下拉 ----------
    @staticmethod
    def _build_color_combo(value: str) -> QComboBox:
        """构建颜色下拉：预设色块 + 自定义项，并回填当前值。"""
        combo = QComboBox()
        for preset, i18n_key in TITLE_COLOR_PRESETS:
            combo.addItem(color_swatch_icon(preset), tr(i18n_key), preset)
        combo.addItem(tr("set.color_custom"), "__custom__")
        DownloadDialog._set_color_combo(combo, value)
        return combo

    @staticmethod
    def _set_color_combo(combo: QComboBox, value: str) -> None:
        """回填颜色下拉；预设外的值动态插一项（放在“自定义”之前）。"""
        idx = combo.findData(value)
        if idx < 0:
            combo.insertItem(combo.count() - 1,
                             color_swatch_icon(value), value, value)
            idx = combo.findData(value)
        combo.blockSignals(True)
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _on_color_changed(self, which: str) -> None:
        """颜色下拉变更；选“自定义”则弹取色器。"""
        if which == "chapter":
            combo = self.dlg_chapter_color_combo
            fallback = self._default_chapter_color
        else:
            combo = self.dlg_volume_color_combo
            fallback = self._default_volume_color
        value = str(combo.currentData() or "")
        if value == "__custom__":
            base = fallback if not fallback.startswith("linear:") else "#c62828"
            c = QColorDialog.getColor(QColor(base), self, tr("set.color_pick_title"))
            if not c.isValid():
                current = (self.dlg_chapter_color if which == "chapter"
                           else self.dlg_volume_color)
                self._set_color_combo(combo, current)
                return
            value = c.name()
            self._set_color_combo(combo, value)
        if which == "chapter":
            self.dlg_chapter_color = value
        else:
            self.dlg_volume_color = value

    # ---------- 固定题图 / 尾图下拉 ----------
    @staticmethod
    def _build_fixed_combo(library: str, selected: str) -> QComboBox:
        combo = QComboBox()
        try:
            from . import chapter_hero as _hero
            for item in _hero.available_heroes(library):
                combo.addItem(item["zh"], item["key"])
        except Exception:  # noqa: BLE001 - 图库缺失时留空下拉，不影响下载
            pass
        idx = combo.findData(str(selected or ""))
        combo.setCurrentIndex(max(0, idx))
        return combo

    def _sync_hero_ui(self) -> None:
        """题图开启且方式为“固定一张”时，才启用固定图下拉。"""
        on = self.hero_check.isChecked()
        fixed = self.hero_mode_combo.currentData() == "fixed"
        self.hero_mode_combo.setEnabled(on)
        self.hero_fixed_combo.setEnabled(on and fixed)

    def _sync_tail_ui(self) -> None:
        """尾图开启且方式为“固定一张”时，才启用固定图下拉。"""
        on = self.tail_check.isChecked()
        fixed = self.tail_mode_combo.currentData() == "fixed"
        self.tail_mode_combo.setEnabled(on)
        self.tail_fixed_combo.setEnabled(on and fixed)

    def _pick_official_cover(self) -> None:
        """选择 App「保存」出来的本地封面图片（原图，不失真）。"""
        path, _ = QFileDialog.getOpenFileName(
            self, tr("dlg.official_pick_title"), "",
            "图片文件 (*.jpg *.jpeg *.png *.webp *.bmp);;所有文件 (*.*)")
        if not path:
            return
        self.official_cover_path = path
        # 立即复制到覆盖表目录并持久化，保证关掉对话框也不丢
        bid = str(self.book.get("id", "") or "")
        from .fanqie_core.meta_override import save_cover_file, set_override
        saved = save_cover_file(bid, path)
        if saved:
            self.official_cover_path = saved
        title = self.official_title_edit.text().strip() if self.official_title_edit else ""
        set_override(bid, title=title, cover_path=saved)
        self._official_cleared = False
        if self.official_cover_label is not None:
            self.official_cover_label.setText(tr("dlg.official_cover_set"))

    def _clear_official(self) -> None:
        """清空官方信息修正输入，并标记删除持久化的覆盖表记录。"""
        if self.official_title_edit is not None:
            self.official_title_edit.clear()
        if self.official_cover_edit is not None:
            self.official_cover_edit.clear()
        self.official_cover_path = ""
        if self.official_cover_label is not None:
            self.official_cover_label.setText(tr("dlg.official_cover_none"))
        self._official_cleared = True

    # ---------- 预览与输出 ----------
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
        """返回本次下载的全部选项；EPUB 排版参数会透传给内核。"""
        scoped = self.range_check.isChecked()
        vol = str(self.dlg_volume_font.currentData() or "__auto__")
        opts: Dict[str, Any] = {
            "start_chapter": self.start_spin.value() if scoped else 0,
            "end_chapter": self.end_spin.value() if scoped else 0,
            "filename_template": self.template_edit.text().strip(),
            # 以下仅 EPUB 生效；非 EPUB 格式下载核心会忽略这些参数
            "chapter_font": str(self.dlg_chapter_font.currentData() or ""),
            "volume_font": "" if vol == "__auto__" else vol,
            "chapter_color": self.dlg_chapter_color,
            "volume_color": self.dlg_volume_color,
            "colorize_dialogue": self.colorize_check.isChecked(),
            "smart_art": self.smart_art_check.isChecked(),
            "chapter_hero": self.hero_check.isChecked(),
            "hero_mode": str(self.hero_mode_combo.currentData() or "rotate"),
            "hero_fixed": str(self.hero_fixed_combo.currentData() or ""),
            "chapter_tail": self.tail_check.isChecked(),
            "tail_mode": str(self.tail_mode_combo.currentData() or "rotate"),
            "tail_fixed": str(self.tail_fixed_combo.currentData() or ""),
        }
        # 番茄官方信息修正（透传给内核写入覆盖表）
        if self._official_group is not None:
            opts["official_title"] = (self.official_title_edit.text().strip()
                                      if self.official_title_edit else "")
            opts["official_cover_url"] = (self.official_cover_edit.text().strip()
                                          if self.official_cover_edit else "")
            opts["official_cover_path"] = self.official_cover_path
            opts["official_clear"] = self._official_cleared
        return opts

    def format(self) -> str:
        return self.fmt_combo.currentText()
