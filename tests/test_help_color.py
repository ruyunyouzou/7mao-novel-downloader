# -*- coding: utf-8 -*-
"""使用说明页「修改色彩」入口的离线测试。

覆盖：按钮常驻、主题色即时应用、深浅模式切换、更多配色入口、
语言切换后按钮文案同步。全部使用 offscreen + FakeMainWindow，不触网。
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.i18n import manager  # noqa: E402
from gui.views.help_view import HelpColorDialog, HelpView  # noqa: E402


class FakeStore:
    def __init__(self) -> None:
        self.data = {"accent_color": "purple", "theme_mode": "dark"}

    def settings_get(self, key, default=None):
        return self.data.get(key, default)

    def settings_set(self, key, value):
        self.data[key] = value


class FakeMainWindow:
    def __init__(self) -> None:
        self.store = FakeStore()
        self._dark_theme = True
        self.apply_calls = 0
        self.theme_calls = []

    def apply_style_animated(self, **_kwargs):
        self.apply_calls += 1
        self._dark_theme = self.store.settings_get("theme_mode", "dark") != "light"

    def set_theme_mode(self, mode):
        self.theme_calls.append(mode)
        self.store.settings_set("theme_mode", mode)
        self._dark_theme = mode != "light"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_view():
    win = FakeMainWindow()
    view = HelpView(win)
    return win, view


def test_help_view_has_persistent_color_button(qapp):
    """色彩入口必须常驻工具栏，而不是首次引导用完即隐藏。"""
    win, view = _make_view()
    assert view.color_btn.objectName() == "helpColorBtn"
    assert view.color_btn.text() == "修改色彩"
    assert view.color_btn.isEnabled()


def test_color_dialog_has_all_accents(qapp):
    """对话框应提供全部五种主题色，并标记当前选中项。"""
    _win, view = _make_view()
    dlg = HelpColorDialog(view, view)
    assert set(dlg.dots) == {"purple", "cyan", "blue", "green", "orange"}
    assert dlg.dots["purple"]._selected is True
    assert dlg.dots["green"]._selected is False


def test_pick_accent_applies_immediately(qapp):
    """点击主题色圆点后，配置落盘 + 主窗口重绘 + 选中态刷新。"""
    win, view = _make_view()
    dlg = HelpColorDialog(view, view)
    before = win.apply_calls

    dlg._pick_accent("green")

    assert win.store.settings_get("accent_color") == "green"
    assert view._accent is not None
    assert win.apply_calls > before
    assert dlg.dots["green"]._selected is True
    assert dlg.dots["purple"]._selected is False


def test_set_light_mode_applies_immediately(qapp):
    """切换浅色模式后，主题模式、主窗口回调、帮助页配色同步。"""
    win, view = _make_view()
    dlg = HelpColorDialog(view, view)
    before = win.apply_calls

    dlg._set_theme(False)

    assert win.store.settings_get("theme_mode") == "light"
    assert win.theme_calls == ["light"]
    assert view._dark is False
    assert dlg.light_btn.isChecked() is True
    assert dlg.dark_btn.isChecked() is False
    assert win.apply_calls > before


def test_more_colors_entry_is_safe_without_tabs(qapp):
    """窗口没有 tabs 结构时，「更多配色」入口也应安全退出。"""
    _win, view = _make_view()
    dlg = HelpColorDialog(view, view)
    dlg._open_settings()  # 不应抛异常


def test_retranslate_updates_color_button(qapp):
    """语言切换后，色彩按钮文案随之更新。"""
    _win, view = _make_view()
    try:
        manager().set_language("en_US")
        view.retranslate()
        assert view.color_btn.text() == "Colors"
        manager().set_language("ru_RU")
        view.retranslate()
        assert view.color_btn.text() == "Цвета"
    finally:
        manager().set_language("zh_CN")
        view.retranslate()
    assert view.color_btn.text() == "修改色彩"
