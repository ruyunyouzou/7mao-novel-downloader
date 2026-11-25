# -*- coding: utf-8 -*-
"""设置页「UI 界面背景」色块式选择器与配色编辑对话框。

与功能卡片背景（nation_theme）完全无关：这里管的是内容区**整体**背景。
- 色块网格：每套背景一个渐变预览块，点击切换，笔按钮编辑该套配色；
- 配色编辑对话框：深色/浅色各一套渐变，支持从当前色自动派生另一主题；
- 动态光效对话框：每个动态效果（流光/极光波浪/星河/涟漪/粒子）的颜色按各自数量单独编辑；
- 自定义背景：用渐变选择器选任意渐变/纯色，深浅各存一套。
"""

from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QDialog, QComboBox,
)

from .i18n import tr
from . import ui_background_theme as ub
from . import nation_theme as nt
from .style import TEXT_DIM


def _preview_qss(colors: List[str]) -> str:
    """生成色块预览背景声明（渐变/纯色）。"""
    if not colors:
        return "background-color: #333;"
    if len(colors) == 1:
        return f"background-color: {colors[0]};"
    stops = " ".join(
        f"stop:{i / (len(colors) - 1):.4f} {c}" for i, c in enumerate(colors)
    )
    return f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1,{stops});"


class _BgSwatch(QFrame):
    """一套背景的预览色块，点击切换；右上角笔按钮编辑配色。"""

    def __init__(self, key: str, name: str, colors: List[str],
                 on_select, on_edit, parent=None) -> None:
        super().__init__(parent)
        self.key = key
        self.on_select = on_select
        self.on_edit = on_edit
        self._colors = colors
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(72)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        # 渐变预览条
        self.preview = QFrame()
        self.preview.setFixedHeight(46)
        # 整块色块（含 preview / 名称）点击都应切换背景：
        # 给子控件装事件过滤器，把鼠标按下转发给外层 _BgSwatch 统一处理，
        # 避免点击落在子控件上被吞掉导致「白框不移动」。
        self.preview.installEventFilter(self)
        lay.addWidget(self.preview)

        # 名称 + 编辑按钮
        name_row = QHBoxLayout()
        name_row.setContentsMargins(2, 0, 2, 0)
        lbl = QLabel(name)
        lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        name_row.addWidget(lbl, 1)
        if key != "__default__":
            edit = QPushButton()
            edit.setToolTip(tr("set.bg_edit"))
            edit.setFixedSize(20, 20)
            edit.setFlat(True)
            # 用国徽图标代替符号（避免字体不支持显示成方框；遵循"只要国徽"）
            try:
                from .widgets import nation_icon
                edit.setIcon(nation_icon("snezhnaya", "#b8b0c8", 16))
                edit.setIconSize(QSize(14, 14))
            except Exception:  # noqa: BLE001
                edit.setText("*")
            # 点国徽编辑按钮时也先选中本色块（让白框立即移动），再打开配色编辑
            edit.clicked.connect(self._on_edit_clicked)
            name_row.addWidget(edit)
        lay.addLayout(name_row)
        self._refresh()

    def _on_edit_clicked(self) -> None:
        """编辑按钮：先切换选中态，再打开配色编辑对话框。"""
        self.on_select(self.key)
        self.on_edit(self.key)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        """把 preview / 名称 / 按钮上的鼠标按下转发为「选中本背景」。"""
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self.on_select(self.key)
            return True
        return super().eventFilter(obj, event)

    def _refresh(self) -> None:
        border = "2px solid #ffffff" if self._selected else "1px solid #666"
        self.preview.setStyleSheet(
            f"QFrame {{ {_preview_qss(self._colors)} border-radius: 6px; "
            f"border: {border}; }}"
        )

    def set_colors(self, colors: List[str]) -> None:
        self._colors = colors
        self._refresh()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._refresh()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.on_select(self.key)


class _BgColorsDialog(QDialog):
    """编辑一套背景的深色/浅色配色（各一个渐变选择器入口）。"""

    def __init__(self, key: str, parent=None) -> None:
        super().__init__(parent)
        from .settings_more import _PaintPickerDialog
        self._Picker = _PaintPickerDialog
        self.key = key
        self.setWindowTitle(tr("set.bg_edit_title"))
        self.setModal(True)
        self.resize(440, 260)
        from . import style as _style
        bg = getattr(_style, "BG_CARD", "#242424") or "#242424"
        self.setStyleSheet(f"QDialog {{ background-color: {bg}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        hint = QLabel(tr("set.bg_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        root.addWidget(hint)

        self._swatches = {}
        for dark, label in ((True, "set.bg_dark_colors"), (False, "set.bg_light_colors")):
            row = QHBoxLayout()
            row.addWidget(QLabel(tr(label)))
            colors = ub.resolved_value(key, dark)
            sw = _BgSwatch("__edit__", "", colors, lambda *_: None, lambda *_: None)
            sw.setFixedSize(150, 34)
            sw.preview.setFixedHeight(30)
            sw._refresh()
            row.addWidget(sw, 1)
            pick = QPushButton(tr("set.bg_edit"))
            pick.clicked.connect(lambda d=dark: self._pick(d))
            row.addWidget(pick)
            derive = QPushButton(tr("set.bg_auto_derive"))
            derive.setFlat(True)
            derive.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
            derive.clicked.connect(lambda d=dark: self._derive(d))
            row.addWidget(derive)
            root.addLayout(row)
            self._swatches[dark] = sw

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        reset = QPushButton(tr("set.bg_reset_preset"))
        reset.clicked.connect(self._reset)
        btn_row.addWidget(reset)
        close = QPushButton(tr("common.close") if tr("common.close") != "common.close" else "关闭")
        close.clicked.connect(self.accept)
        btn_row.addWidget(close)
        root.addLayout(btn_row)

    def _pick(self, dark: bool) -> None:
        current = nt.make_gradient(135, ub.resolved_value(self.key, dark)) \
            if len(ub.resolved_value(self.key, dark)) > 1 else ub.resolved_value(self.key, dark)[0]
        chosen = self._Picker.pick(self, current, default_color="#242424")
        if chosen is None:
            return
        self.store_set(dark, chosen)
        parsed = nt.parse_gradient(chosen)
        colors = parsed[1] if parsed else [chosen]
        self._swatches[dark].set_colors(colors)

    def _derive(self, dark: bool) -> None:
        colors = ub.resolved_value(self.key, dark)
        value = ub.derive_pair_value(colors, dark)
        self.store_set(not dark, value)
        parsed = nt.parse_gradient(value)
        derived = parsed[1] if parsed else [value]
        self._swatches[not dark].set_colors(derived)

    def store_set(self, dark: bool, value: str) -> None:
        # 由外部注入的 setter（主窗口 store）
        setter = getattr(self, "_setter", None)
        if setter:
            setter(ub.bg_value_key(self.key, dark), value)

    def _reset(self) -> None:
        setter = getattr(self, "_setter", None)
        if setter:
            for d in (True, False):
                setter(ub.bg_value_key(self.key, d), "")
        for d in (True, False):
            preset = ub.PRESET_DEFAULTS.get(self.key) or {}
            self._swatches[d].set_colors(list(preset.get("dark" if d else "light", [])))


class _EffectColorsDialog(QDialog):
    """编辑某动态效果的光团/光带颜色（颜色个数随效果变化，3~4 个）。"""

    def __init__(self, effect_key: str, parent=None) -> None:
        super().__init__(parent)
        from .settings_more import _PaintPickerDialog
        self._Picker = _PaintPickerDialog
        self.effect_key = effect_key
        preset = ub.PRESET_DEFAULTS.get(effect_key) or {}
        name = preset.get("name", effect_key)
        self.setWindowTitle(tr("set.bg_effect_color_title", name=name))
        self.setModal(True)
        count = ub.effect_color_count(effect_key)
        self.resize(400, 140 + 56 * ((count + 1) // 2))
        from . import style as _style
        bg = getattr(_style, "BG_CARD", "#242424") or "#242424"
        self.setStyleSheet(f"QDialog {{ background-color: {bg}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        hint = QLabel(tr("set.bg_effect_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        root.addWidget(hint)

        self._swatches: List[QFrame] = []
        grid = QGridLayout()
        grid.setSpacing(8)
        colors = ub.effect_colors(effect_key, True)
        for i in range(count):
            sw = QFrame()
            sw.setFixedSize(56, 34)
            c = colors[i] if i < len(colors) else "#888888"
            sw.setStyleSheet(
                f"QFrame {{ background-color: {c}; border: 1px solid #777; border-radius: 5px; }}"
            )
            grid.addWidget(sw, i // 2 * 2, i % 2)
            btn = QPushButton(f"{tr('set.bg_edit')} {i + 1}")
            btn.clicked.connect(lambda idx=i, s=sw: self._pick(idx, s))
            grid.addWidget(btn, i // 2 * 2 + 1, i % 2)
            self._swatches.append(sw)
        root.addLayout(grid)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        reset = QPushButton(tr("set.bg_reset_preset"))
        reset.clicked.connect(self._reset)
        btn_row.addWidget(reset)
        close = QPushButton(tr("common.close") if tr("common.close") != "common.close" else "关闭")
        close.clicked.connect(self.accept)
        btn_row.addWidget(close)
        root.addLayout(btn_row)

    def _pick(self, index: int, sw: QFrame) -> None:
        current = ub.effect_colors(self.effect_key, True)[index]
        chosen = self._Picker.pick(self, current, default_color=current)
        if chosen is None:
            return
        rep = nt.paint_representative(chosen)
        setter = getattr(self, "_setter", None)
        if setter:
            setter(ub.effect_color_key(self.effect_key, index), rep)
        sw.setStyleSheet(
            f"QFrame {{ background-color: {rep}; border: 1px solid #777; border-radius: 5px; }}"
        )

    def _reset(self) -> None:
        setter = getattr(self, "_setter", None)
        if setter:
            for i in range(ub.effect_color_count(self.effect_key)):
                setter(ub.effect_color_key(self.effect_key, i), "")
        preset = ub.PRESET_DEFAULTS.get(self.effect_key) or {}
        colors = list(preset.get("dark", []))
        for i, sw in enumerate(self._swatches):
            c = colors[i] if i < len(colors) else "#888888"
            sw.setStyleSheet(
                f"QFrame {{ background-color: {c}; border: 1px solid #777; border-radius: 5px; }}"
            )


class _FlowBlobsDialog(_EffectColorsDialog):
    """旧流光光团对话框，保留兼容；内部走通用效果编辑器。"""

    def __init__(self, parent=None) -> None:
        super().__init__("flow", parent)


def build_background_swatches(win, combo) -> QWidget:
    """在主窗口设置页构建色块网格，替换原下拉框的可视化部分。

    保留原 combo（用于 i18n/兼容），在它上方插入色块网格；combo 隐藏。
    返回承载色块的 QWidget，供语言切换时定位。
    """
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)

    lay.addWidget(QLabel(tr("set.bg_swatches")))
    grid = QGridLayout()
    grid.setSpacing(10)

    store = win.store

    def setter(key: str, value: str) -> None:
        store.settings_set(key, value)
        win._apply_stylesheet()
        win._refresh_part_colors()

    def select(key: str) -> None:
        win.set_background(key)
        combo.blockSignals(True)
        idx = combo.findData(key)
        combo.setCurrentIndex(max(0, idx))
        combo.blockSignals(False)
        for sw in swatches.values():
            sw.set_selected(sw.key == key)
        # 选中动态背景时，同步光效编辑下拉到对应效果
        if key in ub.dynamic_keys():
            effect_combo.blockSignals(True)
            effect_combo.setCurrentIndex(max(0, effect_combo.findData(key)))
            effect_combo.blockSignals(False)

    def edit(key: str) -> None:
        if ub.shape_of(key) in ("flow", "dynamic"):
            dlg = _EffectColorsDialog(key, win)
        else:
            dlg = _BgColorsDialog(key, win)
        dlg._setter = setter
        dlg.exec()

    def edit_current_effect() -> None:
        key = str(effect_combo.currentData() or "")
        if key:
            edit(key)

    # 默认纯色
    default_sw = _BgSwatch("__default__", tr("set.bg_default"), ["#2a2a30", "#1a1a20"],
                           select, edit)
    swatches = {"": default_sw}
    grid.addWidget(default_sw, 0, 0)

    keys = ub.preset_keys()
    for i, key in enumerate(keys):
        name = (ub.PRESET_DEFAULTS.get(key) or {}).get("name", key)
        colors = ub.resolved_value(key, win._dark_theme)
        sw = _BgSwatch(key, name, colors, select, edit)
        swatches[key] = sw
        grid.addWidget(sw, (i + 1) // 3, (i + 1) % 3)

    # 自定义背景
    custom_colors = ub.resolved_value(ub.CUSTOM_KEY, win._dark_theme)
    custom_sw = _BgSwatch(ub.CUSTOM_KEY, tr("set.bg_custom"), custom_colors, select, edit)
    swatches[ub.CUSTOM_KEY] = custom_sw
    grid.addWidget(custom_sw, (len(keys) + 1) // 3, (len(keys) + 1) % 3)

    lay.addLayout(grid)

    # 动态光效颜色编辑入口：下拉选择任一动态效果，编辑其光效颜色
    effect_row = QHBoxLayout()
    effect_row.setSpacing(8)
    effect_row.addWidget(QLabel(tr("set.bg_effect_colors")))
    effect_combo = QComboBox()
    for dkey in ub.dynamic_keys():
        dname = (ub.PRESET_DEFAULTS.get(dkey) or {}).get("name", dkey)
        effect_combo.addItem(dname, dkey)
    effect_row.addWidget(effect_combo, 1)
    effect_edit = QPushButton(tr("set.bg_edit"))
    effect_edit.clicked.connect(lambda: edit_current_effect())
    effect_row.addWidget(effect_edit)
    lay.addLayout(effect_row)

    hint = QLabel(tr("set.bg_hint"))
    hint.setWordWrap(True)
    hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
    lay.addWidget(hint)

    # 当前选中态
    current = str(store.settings_get("background_key", "") or "")
    for key, sw in swatches.items():
        sw.set_selected(key == current)
    if current in ub.dynamic_keys():
        effect_combo.setCurrentIndex(max(0, effect_combo.findData(current)))

    # 保存引用供刷新
    win._bg_swatch_host = host
    win._bg_swatches = swatches
    return host
