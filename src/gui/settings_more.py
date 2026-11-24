# -*- coding: utf-8 -*-
"""补充设置卡片：对照 olib 风格设置页新增的功能。

包含：
- 主题模式（深色 / 浅色 / 跟随系统 / 动态光效）
- 主题颜色（强调色圆点）
- 下载行为（跳过重复文件 / 下载方式 / 完成通知 / 提示音）
- 通用（启动页面 / 封面缓存清除）
- 快捷键（全局快速搜索 Ctrl+Space）

做成独立 QWidget，主窗口只需 layout.addWidget(build_settings_more(win))。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QComboBox,
    QCheckBox, QFrame, QMessageBox, QColorDialog, QDialog, QScrollArea,
)

from .i18n import tr
from .style import (
    TEXT_DIM, ACCENT_PALETTES, ACCENT_LABELS, accent_color,
    COLOR_NAV_TEXT, COLOR_NAV_ICON, COLOR_NAV_BG, COLOR_APP_TEXT, COLOR_APP_BG,
)
from . import notify_core


def _title(text: str, nation: str = "", color: str = "") -> QLabel:
    from .widgets import GroupTitleLabel
    color = color or accent_color('purple')
    lab = GroupTitleLabel(text, nation, color, font_size=14)
    return lab


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(f"color: {TEXT_DIM};")
    lab.setWordWrap(True)
    return lab


class _SwatchButton(QFrame):
    """单个可点击色块：支持纯色与渐变值，选中态描边。"""

    def __init__(self, value: str, on_pick, width: int = 44, height: int = 26,
                 tooltip: str = "") -> None:
        super().__init__()
        self.value = value
        self.on_pick = on_pick
        self.setFixedSize(width, height)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip or value)
        self.setStyleSheet(self._qss(False))
        # 悬停放大预览（取色器内预设块）
        from .swatch_preview import attach_preview
        attach_preview(self, lambda: self.value, tooltip or "颜色", value)

    def _qss(self, selected: bool) -> str:
        from . import nation_theme as nt
        bg = nt.bg_qss_decl(self.value, "#888")
        border = "2px solid #ffffff" if selected else "1px solid #777"
        return f"QFrame {{ {bg} border: {border}; border-radius: 5px; }}"

    def set_selected(self, selected: bool) -> None:
        self.setStyleSheet(self._qss(selected))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.on_pick(self.value)


class _PaintPickerDialog(QDialog):
    """纯色 + 渐变选择器：八国官方色 / 渐变预设 / 自定义纯色。"""

    def __init__(self, current: str, parent=None,
                 default_color: str = "", is_nation: bool = False) -> None:
        super().__init__(parent)
        from . import nation_theme as nt
        self.nt = nt
        self.is_nation = is_nation
        self.result_value = None  # None=取消, ""=跟随主题, 其它=色值/渐变
        self.setWindowTitle(tr("more.paint_pick_title"))
        self.setModal(True)
        self.resize(520, 480)
        # 显式设置主题背景，避免滚动区 viewport 用系统浅色造成割裂
        from . import style as _style
        bg = getattr(_style, "BG_CARD", "#242424") or "#242424"
        self.setStyleSheet(
            f"QDialog {{ background-color: {bg}; }}"
            f"QScrollArea {{ background-color: transparent; border: none; }}"
            f"QScrollArea > QWidget > QWidget {{ background-color: transparent; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        hint = _hint(tr("more.paint_hint"))
        root.addWidget(hint)

        # 当前值 + 跟随主题
        cur_row = QHBoxLayout()
        cur_row.addWidget(QLabel(tr("more.paint_current")))
        self.current_lbl = QLabel(current or tr("more.paint_follow"))
        self.current_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-weight: bold;")
        cur_row.addWidget(self.current_lbl)
        cur_row.addStretch(1)
        follow = QPushButton(tr("more.paint_follow"))
        follow.setFlat(True)
        follow.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        follow.clicked.connect(self._follow)
        cur_row.addWidget(follow)
        root.addLayout(cur_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        col = QVBoxLayout(content)
        col.setContentsMargins(2, 2, 2, 2)
        col.setSpacing(12)

        # --- 八国官方主题色 + 官方渐变 ---
        # 每行 4 国，8 列：纯色块 / 官方渐变块 交替，国名下占两列
        col.addWidget(_hint(tr("more.paint_nation")))
        grid_n = QGridLayout()
        grid_n.setSpacing(7)
        col.addLayout(grid_n)
        for i, nation in enumerate(nt.NATIONS):
            base = nt.nation_default(nation)
            grad = nt.nation_official_gradient(nation)
            name = nt.NATION_LABELS_ZH.get(nation, nation)
            row = i // 4
            grid_n.addWidget(_SwatchButton(base, self._pick, 40, 22,
                             f"{name} · {tr('more.paint_solid')}"), row * 2, i % 4 * 2)
            grid_n.addWidget(_SwatchButton(grad, self._pick, 40, 22,
                             f"{name} · {tr('more.paint_gradient')}"), row * 2, i % 4 * 2 + 1)
            lbl = QLabel(name)
            lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px;")
            lbl.setAlignment(Qt.AlignHCenter)
            grid_n.addWidget(lbl, row * 2 + 1, i % 4 * 2, 1, 2)

        # --- 渐变预设 ---
        col.addWidget(_hint(tr("more.paint_gradient")))
        grid_g = QGridLayout()
        grid_g.setSpacing(7)
        col.addLayout(grid_g)
        presets = nt.general_presets()
        for i, preset in enumerate(presets):
            colors = preset.get("colors") or []
            if len(colors) < 2:
                continue
            value = nt.make_gradient(135, colors)
            r, c = divmod(i, 6)
            grid_g.addWidget(_SwatchButton(value, self._pick, 68, 26,
                             preset.get("name", "")), r, c)

        # --- 自定义纯色入口 ---
        col.addWidget(_hint(tr("more.paint_solid")))
        custom_btn = QPushButton(tr("more.paint_custom"))
        custom_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {TEXT_DIM}; "
            f"border: 1px dashed #888; border-radius: 5px; padding: 6px; }}")
        custom_btn.clicked.connect(self._custom)
        col.addWidget(custom_btn)
        col.addStretch(1)

        scroll.setWidget(content)
        root.addWidget(scroll, 1)

    def _pick(self, value: str) -> None:
        self.result_value = value
        self.accept()

    def _follow(self) -> None:
        self.result_value = ""
        self.accept()

    def _custom(self) -> None:
        from PySide6.QtGui import QColor
        initial = self.nt.paint_representative(str(self.current_lbl.text()))
        color = QColorDialog.getColor(QColor(initial), self,
                                      tr("more.color_pick_title"))
        if color.isValid():
            self.result_value = color.name()
            self.accept()

    @staticmethod
    def pick(parent, current: str, default_color: str = "",
             is_nation: bool = False):
        dlg = _PaintPickerDialog(current, parent, default_color, is_nation)
        dlg.exec()
        return dlg.result_value


class _ColorDot(QFrame):
    """可点击的强调色圆点。"""

    def __init__(self, key: str, color: str, on_pick) -> None:
        super().__init__()
        self.key = key
        self.on_pick = on_pick
        self.setFixedSize(26, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{ACCENT_LABELS.get(key, key)}{tr('more.theme_gradient_tip')}")
        self._color = color
        self._selected = False
        self._paint()
        # 悬停放大预览（主题色圆点）
        from .swatch_preview import attach_preview
        attach_preview(self, lambda: self._color,
                       ACCENT_LABELS.get(key, key) + " · 主题色", color)

    def _paint(self) -> None:
        border = "#ffffff" if self._selected else "transparent"
        from . import nation_theme as nt
        grad = nt.qss_gradient(self._color) if nt.is_gradient(self._color) else ""
        bg = f"background: {grad};" if grad else f"background-color: {self._color};"
        self.setStyleSheet(
            f"QFrame {{ {bg} border-radius: 13px; "
            f"border: 2px solid {border}; }}")

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._paint()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.on_pick(self.key)


class _ColorPickerRow(QFrame):
    """单个分部件配色行：标签 + 色块（点击取色）+ 跟随主题重置按钮。"""

    def __init__(self, setting_key: str, label: str, fallback_color: str,
                 store, on_change) -> None:
        super().__init__()
        self.setting_key = setting_key
        self.fallback_color = fallback_color
        self.store = store
        self.on_change = on_change

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        lab = QLabel(label)
        lab.setStyleSheet(f"color: {TEXT_DIM};")
        row.addWidget(lab)
        row.addStretch(1)

        self.swatch = QPushButton()
        self.swatch.setFixedSize(40, 24)
        self.swatch.setCursor(Qt.PointingHandCursor)
        self.swatch.clicked.connect(self._pick)
        row.addWidget(self.swatch)

        self.reset_btn = QPushButton(tr("more.color_follow"))
        self.reset_btn.setFlat(True)
        self.reset_btn.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        self.reset_btn.clicked.connect(self._reset)
        row.addWidget(self.reset_btn)
        self.refresh()
        # 悬停放大预览（分部件配色）：getter 动态读当前有效色值
        from .swatch_preview import attach_preview

        def _current_effective():
            val = self.current_value()
            from . import nation_theme as nt
            return val if nt._valid_paint(val) else self.fallback_color

        attach_preview(self.swatch, _current_effective, label, self.fallback_color)

    def current_value(self) -> str:
        return str(self.store.settings_get(self.setting_key, "") or "")

    def refresh(self) -> None:
        from . import nation_theme as nt
        val = self.current_value()
        effective = val if nt._valid_paint(val) else self.fallback_color
        self.swatch.setStyleSheet(
            f"QPushButton {{ {nt.bg_qss_decl(effective, self.fallback_color)} "
            f"border: 1px solid #888; border-radius: 4px; }}")
        shown = val if val else (self.fallback_color + "（跟随主题）")
        self.swatch.setToolTip(shown)

    def _pick(self) -> None:
        val = self.current_value()
        chosen = _PaintPickerDialog.pick(self, val, self.fallback_color)
        if chosen is None:
            return
        self.store.settings_set(self.setting_key, chosen)
        self.refresh()
        self.on_change()

    def _reset(self) -> None:
        self.store.settings_set(self.setting_key, "")
        self.refresh()
        self.on_change()

    def retranslate(self, label: str, fallback_color: str) -> None:
        """语言切换时刷新标签与重置按钮文案（行内第一个 QLabel）。"""
        self.fallback_color = fallback_color
        first = self.findChild(QLabel)
        if first is not None:
            first.setText(label)
        self.reset_btn.setText(tr("more.color_follow"))
        self.refresh()


class _NationColorRow(QFrame):
    """单个国徽调色行：国徽预览 + 名称 + 色块取色 + 跟随主题重置。"""
    def __init__(self, nation: str, store, on_change, dark: bool) -> None:
        super().__init__()
        from . import nation_theme as nt
        from .widgets import nation_icon
        self.nation = nation
        self.store = store
        self.on_change = on_change
        self.dark = dark
        self.setting_key = nt.nation_key(nation)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        # 国徽预览（24px，跟随当前有效色）
        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(24, 24)
        self.icon_lbl.setStyleSheet("border:none;background:transparent;padding:0;")
        row.addWidget(self.icon_lbl)

        lab = QLabel(tr(f"more.nation.{nation}"))
        lab.setStyleSheet(f"color: {TEXT_DIM}; min-width: 64px;")
        row.addWidget(lab)
        row.addStretch(1)

        self.swatch = QPushButton()
        self.swatch.setFixedSize(40, 24)
        self.swatch.setCursor(Qt.PointingHandCursor)
        self.swatch.clicked.connect(self._pick)
        row.addWidget(self.swatch)

        self.reset_btn = QPushButton(tr("more.color_follow"))
        self.reset_btn.setFlat(True)
        self.reset_btn.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        self.reset_btn.clicked.connect(self._reset)
        row.addWidget(self.reset_btn)
        self.nation_icon_fn = nation_icon
        self.refresh()
        # 悬停放大预览（国徽逐国染色）：getter 动态读当前有效色值
        from .swatch_preview import attach_preview
        from . import nation_theme as nt
        nation_label = nt.NATION_LABELS_ZH.get(nation, nation)

        def _current_effective():
            val = str(self.store.settings_get(self.setting_key, "") or "")
            default = nt.nation_default(self.nation)
            return val if nt._valid_paint(val) else default

        attach_preview(self.swatch, _current_effective,
                       nation_label + " · 国徽色", nt.nation_default(nation))

    def refresh(self) -> None:
        from . import nation_theme as nt
        val = str(self.store.settings_get(self.setting_key, "") or "")
        # 国徽默认色 = 官方主题色（不是主题强调色）
        fallback = nt.nation_default(self.nation)
        effective = val if val else fallback
        self.swatch.setStyleSheet(
            f"QPushButton {{ {nt.bg_qss_decl(effective, fallback)} "
            f"border: 1px solid #888; border-radius: 4px; }}")
        self.swatch.setToolTip((val or fallback + "（官方默认）"))
        # 预览用有效色直接渲染（nation_icon 内部会按配色中心强制解析）
        self.icon_lbl.setPixmap(
            self.nation_icon_fn(self.nation, effective, 24).pixmap(24, 24))

    def _pick(self) -> None:
        from . import nation_theme as nt
        val = str(self.store.settings_get(self.setting_key, "") or "")
        fallback = nt.nation_default(self.nation)
        chosen = _PaintPickerDialog.pick(self, val, fallback, is_nation=True)
        if chosen is None:
            return
        self.store.settings_set(self.setting_key, chosen)
        self.refresh()
        self.on_change()

    def _reset(self) -> None:
        self.store.settings_set(self.setting_key, "")
        self.refresh()
        self.on_change()

    def retranslate(self) -> None:
        self.findChild(QLabel).setText(tr(f"more.nation.{self.nation}"))
        self.reset_btn.setText(tr("more.color_follow"))
        self.refresh()


def _reset_all_parts(win, store, on_change) -> None:
    """清除所有部件自定义设置（国徽/导航文字/字体/功能背景）。"""
    from . import nation_theme as nt
    from PySide6.QtWidgets import QMessageBox
    keys = [nt.KEY_NAV_TEXT, nt.KEY_NAV_FONT, nt.KEY_NAV_BG, nt.KEY_CARD_BG]
    keys += [nt.nation_key(n) for n in nt.NATIONS]
    for key in keys:
        store.settings_set(key, "")
    # 同步控件显示
    combo = getattr(win, "part_font_combo", None)
    if combo is not None:
        idx = combo.findData(nt.DEFAULT_NAV_FONT)
        if idx >= 0:
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)
    for row in getattr(win, "nation_color_rows", {}).values():
        row.refresh()
    card_row = getattr(win, "part_card_row", None)
    if card_row is not None:
        card_row.refresh()
    on_change()
    QMessageBox.information(win, tr("app.name"), tr("more.part_reset_ok"))


def build_settings_more(win) -> QWidget:
    store = win.store
    root = QWidget()
    root.setObjectName("settingsMoreRoot")
    v = QVBoxLayout(root)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(16)

    from .widgets import Card

    # ---------------- 主题模式 + 主题色 ----------------
    theme_card = Card()
    tl = QVBoxLayout(theme_card)
    tl.setContentsMargins(16, 14, 16, 14)
    tl.setSpacing(10)
    win.more_theme_title = _title(tr("more.theme_title"), "snezhnaya")

    tl.addWidget(win.more_theme_title)
    # 主题模式下拉
    mode_row = QHBoxLayout()
    mode_row.addWidget(_hint(tr("more.theme_mode")))
    win.theme_mode_combo = QComboBox()
    for key, label in (("dark", "more.theme_dark"), ("light", "more.theme_light"),
                       ("system", "more.theme_system"), ("dynamic", "more.theme_dynamic")):
        win.theme_mode_combo.addItem(tr(label), key)
    cur_mode = str(store.settings_get("theme_mode", "dark") or "dark")
    mi = win.theme_mode_combo.findData(cur_mode)
    win.theme_mode_combo.setCurrentIndex(max(0, mi))
    win.theme_mode_combo.currentIndexChanged.connect(lambda: _on_theme_mode(win))
    mode_row.addWidget(win.theme_mode_combo)
    tl.addLayout(mode_row)

    # 主题色圆点
    color_row = QHBoxLayout()
    color_row.addWidget(_hint(tr("more.theme_color")))
    dots_row = QHBoxLayout()
    dots_row.setSpacing(8)
    win.color_dots = {}
    cur_color = str(store.settings_get("accent_color", "purple") or "purple")

    def pick_color(key: str) -> None:
        store.settings_set("accent_color", key)
        for k, dot in win.color_dots.items():
            dot.set_selected(k == key)
        # 通知主窗口重绘强调色
        apply = getattr(win, "_apply_theme_color", None)
        if callable(apply):
            apply()

    for key, palette in ACCENT_PALETTES.items():
        # 圆点用同色系渐变显示（palette[3]），无渐变时回退纯色主色
        dot_color = palette[3] if len(palette) > 3 and palette[3] else palette[0]
        dot = _ColorDot(key, dot_color, pick_color)
        dot.set_selected(key == cur_color)
        win.color_dots[key] = dot
        dots_row.addWidget(dot)
    dots_row.addStretch()
    color_row.addLayout(dots_row, 1)
    tl.addLayout(color_row)
    v.addWidget(theme_card)

    # ---------------- 分部件配色 ----------------
    comp_card = Card()
    cl = QVBoxLayout(comp_card)
    cl.setContentsMargins(16, 14, 16, 14)
    cl.setSpacing(8)
    win.more_color_title = _title(tr("more.color_title"), "liyue")
    cl.addWidget(win.more_color_title)
    win.more_color_hint = _hint(tr("more.color_hint"))
    cl.addWidget(win.more_color_hint)

    def _reapply() -> None:
        apply = getattr(win, "_apply_theme_color", None)
        if callable(apply):
            apply()

    accent_now = accent_color(cur_color)
    dark_now = getattr(win, "_dark_theme", True)
    app_text_fb = "#e6e6e6" if dark_now else "#2a2a2a"
    app_bg_fb = "#181818" if dark_now else "#f7f7f8"

    win.color_rows = {}
    color_specs = [
        (COLOR_NAV_TEXT, "more.color_nav_text", accent_now),
        (COLOR_NAV_ICON, "more.color_nav_icon", accent_now),
        (COLOR_NAV_BG, "more.color_nav_bg", accent_now),
        (COLOR_APP_TEXT, "more.color_app_text", app_text_fb),
        (COLOR_APP_BG, "more.color_app_bg", app_bg_fb),
    ]
    for key, label_key, fallback in color_specs:
        row = _ColorPickerRow(key, tr(label_key), fallback, store, _reapply)
        win.color_rows[key] = (row, label_key, fallback)
        cl.addWidget(row)
    v.addWidget(comp_card)

    # ---------------- 部件独立调节（国徽 / 文字字体 / 功能背景）----------------
    from . import nation_theme as nt
    from .style import clear_nav_font_cache
    part_card = Card()
    pl = QVBoxLayout(part_card)
    pl.setContentsMargins(16, 14, 16, 14)
    pl.setSpacing(8)
    win.more_part_title = _title(tr("more.part_title"), "natlan")
    pl.addWidget(win.more_part_title)
    win.more_part_hint = _hint(tr("more.part_hint"))
    pl.addWidget(win.more_part_hint)

    def _reapply_parts() -> None:
        # 字体缓存失效 + 通知主窗口重绘导航文字/图标/卡片
        clear_nav_font_cache()
        apply = getattr(win, "_apply_theme_color", None)
        if callable(apply):
            apply()
        refresh = getattr(win, "_refresh_part_colors", None)
        if callable(refresh):
            refresh()

    # 导航字体下拉
    font_row = QHBoxLayout()
    font_row.addWidget(_hint(tr("more.part_font")))
    win.part_font_combo = QComboBox()
    for key, _fname, label in nt.NAV_FONTS:
        win.part_font_combo.addItem(label, key)
    cur_font = nt.nav_font_key()
    fi = win.part_font_combo.findData(cur_font)
    win.part_font_combo.setCurrentIndex(max(0, fi))

    def _on_font_changed():
        store.settings_set(nt.KEY_NAV_FONT, str(win.part_font_combo.currentData() or "mashan"))
        _reapply_parts()
    win.part_font_combo.currentIndexChanged.connect(_on_font_changed)
    font_row.addWidget(win.part_font_combo, 1)
    pl.addLayout(font_row)

    # 功能卡片背景色
    win.part_card_row = _ColorPickerRow(
        nt.KEY_CARD_BG, tr("more.part_card_bg"), "#242424" if dark_now else "#ffffff",
        store, _reapply_parts)
    pl.addWidget(win.part_card_row)

    # 8 国徽逐个调色
    pl.addWidget(_hint(tr("more.part_nations")))
    win.nation_color_rows = {}
    for nation in nt.NATIONS:
        nrow = _NationColorRow(nation, store, _reapply_parts, dark_now)
        win.nation_color_rows[nation] = nrow
        pl.addWidget(nrow)

    # 全部恢复默认
    win.part_reset_btn = QPushButton(tr("more.part_reset_all"))
    win.part_reset_btn.setStyleSheet(
        f"color: {TEXT_DIM}; border: 1px solid #888; border-radius: 6px; padding: 5px 10px;")
    win.part_reset_btn.clicked.connect(lambda: _reset_all_parts(win, store, _reapply_parts))
    pl.addWidget(win.part_reset_btn)
    v.addWidget(part_card)

    # ---------------- 下载行为 ----------------
    dl_card = Card()
    dl = QVBoxLayout(dl_card)
    dl.setContentsMargins(16, 14, 16, 14)
    dl.setSpacing(10)
    win.more_download_title = _title(tr("more.download_title"), "inazuma")
    dl.addWidget(win.more_download_title)

    # 跳过重复文件
    win.skip_duplicate_check = QCheckBox(tr("more.skip_duplicate"))
    win.skip_duplicate_check.setToolTip(tr("more.skip_duplicate_tip"))
    win.skip_duplicate_check.setChecked(bool(store.settings_get("skip_duplicate", True)))
    win.skip_duplicate_check.stateChanged.connect(
        lambda s: store.settings_set("skip_duplicate", bool(s)))
    dl.addWidget(win.skip_duplicate_check)

    # 下载方式
    method_row = QHBoxLayout()
    method_row.addWidget(_hint(tr("more.download_method")))
    win.download_method_combo = QComboBox()
    for key, label in (("builtin", "more.method_builtin"),
                       ("builtin_retry", "more.method_builtin_retry")):
        win.download_method_combo.addItem(tr(label), key)
    cur_method = str(store.settings_get("download_method", "builtin") or "builtin")
    me = win.download_method_combo.findData(cur_method)
    win.download_method_combo.setCurrentIndex(max(0, me))
    win.download_method_combo.currentIndexChanged.connect(
        lambda: store.settings_set("download_method",
                                   str(win.download_method_combo.currentData() or "builtin")))
    method_row.addWidget(win.download_method_combo)
    dl.addLayout(method_row)

    # 下载完成通知
    win.notify_check = QCheckBox(tr("more.notify"))
    win.notify_check.setToolTip(tr("more.notify_tip"))
    win.notify_check.setChecked(bool(store.settings_get("notify_enabled", True)))
    win.notify_check.stateChanged.connect(
        lambda s: store.settings_set("notify_enabled", bool(s)))
    dl.addWidget(win.notify_check)

    # 下载提示音
    sound_row = QHBoxLayout()
    sound_row.addWidget(_hint(tr("more.notify_sound")))
    win.notify_sound_combo = QComboBox()
    for key in ("default", "chime", "soft", "none"):
        win.notify_sound_combo.addItem(tr("more.sound_" + key), key)
    cur_sound = str(store.settings_get("notify_sound", "default") or "default")
    se = win.notify_sound_combo.findData(cur_sound)
    win.notify_sound_combo.setCurrentIndex(max(0, se))
    win.notify_sound_combo.currentIndexChanged.connect(
        lambda: store.settings_set("notify_sound",
                                   str(win.notify_sound_combo.currentData() or "default")))
    sound_row.addWidget(win.notify_sound_combo)
    # 试听按钮
    win.btn_test_sound = QPushButton(tr("more.sound_test"))
    win.btn_test_sound.clicked.connect(lambda: notify_core.play_download_sound(True))
    sound_row.addWidget(win.btn_test_sound)
    dl.addLayout(sound_row)
    v.addWidget(dl_card)

    # ---------------- 通用 ----------------
    gen_card = Card()
    gl = QVBoxLayout(gen_card)
    gl.setContentsMargins(16, 14, 16, 14)
    gl.setSpacing(10)
    win.more_general_title = _title(tr("more.general_title"), "sumeru")
    gl.addWidget(win.more_general_title)

    # 启动页面
    startup_row = QHBoxLayout()
    startup_row.addWidget(_hint(tr("more.startup_page")))
    win.startup_combo = QComboBox()
    # 用导航页的当前文案填充（i18n key）
    startup_options = [("nav.search", "search"), ("nav.shelf", "shelf"),
                       ("nav.read", "read"), ("nav.queue", "queue"),
                       ("nav.history", "history"), ("nav.statistics", "stats")]
    for label_key, val in startup_options:
        win.startup_combo.addItem(tr(label_key), val)
    cur_startup = str(store.settings_get("startup_page", "search") or "search")
    pe = win.startup_combo.findData(cur_startup)
    win.startup_combo.setCurrentIndex(max(0, pe))
    win.startup_combo.currentIndexChanged.connect(
        lambda: store.settings_set("startup_page",
                                   str(win.startup_combo.currentData() or "search")))
    startup_row.addWidget(win.startup_combo)
    gl.addLayout(startup_row)

    # 封面缓存
    cover_row = QHBoxLayout()
    cover_info = QVBoxLayout()
    win.cover_cache_label = _hint(tr("more.cover_cache_empty"))
    cover_info.addWidget(win.cover_cache_label)
    cover_row.addLayout(cover_info, 1)
    win.btn_clear_cover = QPushButton(tr("more.cover_clear"))
    from .widgets import apply_nation_icon
    apply_nation_icon(win.btn_clear_cover, "liyue", accent_color("purple"), 16)
    win.btn_clear_cover.clicked.connect(lambda: _clear_cover_cache(win))
    cover_row.addWidget(win.btn_clear_cover)
    gl.addLayout(cover_row)
    v.addWidget(gen_card)

    # ---------------- 快捷键 ----------------
    key_card = Card()
    kl = QVBoxLayout(key_card)
    kl.setContentsMargins(16, 14, 16, 14)
    kl.setSpacing(10)
    win.more_shortcut_title = _title(tr("more.shortcut_title"), "fontaine")
    kl.addWidget(win.more_shortcut_title)

    quick_row = QHBoxLayout()
    qi = QVBoxLayout()
    qi.addWidget(QLabel(tr("more.quick_search")))
    qi.addWidget(_hint(tr("more.quick_search_tip")))
    quick_row.addLayout(qi, 1)
    key_lbl = QLabel("Ctrl + Space")
    key_lbl.setStyleSheet(
        f"color: {TEXT_DIM}; border: 1px solid #555; border-radius: 6px; "
        f"padding: 4px 10px; font-family: monospace;")
    quick_row.addWidget(key_lbl)
    kl.addLayout(quick_row)
    v.addWidget(key_card)

    # 初始化封面缓存信息
    _refresh_cover_info(win)
    return root


# ------------------------------------------------------------------ 回调

def _on_theme_mode(win) -> None:
    """主题模式下拉统一交给主窗口处理，避免与其他主题入口逻辑分叉。"""
    combo = getattr(win, "theme_mode_combo", None)
    if combo is None:
        return
    mode = str(combo.currentData() or "dark")
    apply_mode = getattr(win, "set_theme_mode", None)
    if callable(apply_mode):
        apply_mode(mode)
        return
    # 兼容早期自定义窗口：至少保证配置被持久化
    win.store.settings_set("theme_mode", mode)


def _cover_cache_dir(win) -> str:
    import os
    return os.path.join(os.path.expanduser("~"), ".qimao_gui", "covers")


def _refresh_cover_info(win) -> None:
    import os
    label = getattr(win, "cover_cache_label", None)
    if label is None:
        return
    d = _cover_cache_dir(win)
    count, size = 0, 0
    if os.path.isdir(d):
        for fn in os.listdir(d):
            fp = os.path.join(d, fn)
            if os.path.isfile(fp):
                count += 1
                size += os.path.getsize(fp)
    if count == 0:
        label.setText(tr("more.cover_cache_empty"))
    else:
        label.setText(tr("more.cover_cache_info", count=count,
                         size=f"{size / 1024:.1f} KB"))


def _clear_cover_cache(win) -> None:
    import os
    import shutil
    d = _cover_cache_dir(win)
    count = 0
    if os.path.isdir(d):
        count = len([f for f in os.listdir(d)
                     if os.path.isfile(os.path.join(d, f))])
        shutil.rmtree(d, ignore_errors=True)
    _refresh_cover_info(win)
    QMessageBox.information(win, tr("common.tip"),
                            tr("more.cover_cleared", count=count))
