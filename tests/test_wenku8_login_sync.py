# -*- coding: utf-8 -*-
"""回归测试：账号密码/Cookie 登录成功后，登录态必须同步到官网线路。

用户实测 bug：账号密码页显示「登录成功，登录态已保存」，但官网线路页
wenku8.cc 仍显示「可访问（未登录）/跳转登录页」。根因是登录只写全局
wenku8_cookie，线路管理的 per-line cookie 未同步，检测时带空 Cookie 访问。

覆盖：
  1. _save_cookie 把登录态写入「使用中」线路的独立 Cookie 并标记 ok
  2. 线路无独立 Cookie 时回退全局登录态（兼容历史数据）
  3. 单条/批量检测都使用有效 Cookie，不再带空登录态去检测

注意：对话框内会连接全局 i18n 信号，测试进程内只构造一次并持久持有，
避免反复构造/析构导致 Qt 侧悬挂引用（进程退出报 QObject 警告）。
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui import datastore as ds_mod  # noqa: E402
from gui import wenku8_login_dialog as dlg_mod  # noqa: E402
from gui.wenku8_core import wenku8_auth as auth  # noqa: E402


class FakeStore:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def settings_get(self, key, default=None):
        return self.data.get(key, default)

    def settings_set(self, key, value):
        self.data[key] = value


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


COOKIE = "PHPSESSID=abc123; jieqiUserInfo=abc%3D123; jieqiVisitInfo=visit"


@pytest.fixture(scope="module")
def dlg(qapp):
    """整个测试模块复用一个对话框与一个 FakeStore，避免重复构造/析构。"""
    store = FakeStore({"wenku8_base_url": "https://www.wenku8.cc"})
    with patch.object(ds_mod, "DataStore", return_value=store), \
         patch.object(dlg_mod, "DataStore", return_value=store):
        dialog = dlg_mod.Wenku8LoginDialog()
    yield dialog, store


def _patch_store(store):
    return patch.object(ds_mod, "DataStore", return_value=store)


def test_save_cookie_syncs_to_active_line(dlg):
    """登录后：使用中线路的独立 cookie 被写入，且状态标记为已登录。"""
    dialog, store = dlg
    with _patch_store(store):
        auth.get_lines_state()  # 初始化默认线路
        auth.set_active_base("https://www.wenku8.cc")
        dialog._save_cookie(COOKIE)
        state = auth.get_lines_state()
    ln = next(l for l in state["lines"] if l["base"] == "https://www.wenku8.cc")
    assert ln["cookie"] == COOKIE, "登录态未同步到使用中线路的独立 Cookie"
    assert ln["state"] == "ok", "登录后线路状态应标记为已登录"
    assert store.settings_get("wenku8_cookie") == COOKIE


def test_effective_cookie_falls_back_to_global(dlg):
    """线路自身无 Cookie 但全局已登录时，检测应带上全局登录态。"""
    dialog, store = dlg
    store.settings_set("wenku8_cookie", COOKIE)
    with _patch_store(store):
        got = dialog._effective_line_cookie({"base": "https://www.wenku8.cc", "cookie": ""})
        own = dialog._effective_line_cookie({"base": "https://www.wenku8.net", "cookie": "OWN=1"})
    assert got == COOKIE
    assert own == "OWN=1"  # 线路自身 Cookie 优先


def test_check_line_uses_effective_cookie(dlg):
    """点「检测」时传给后台检测线程的必须是有效 Cookie，而不是空串。"""
    dialog, store = dlg
    captured = {}

    def fake_check_one(base, cookie, on_done):
        captured.update(base=base, cookie=cookie)

    with patch.object(dialog, "_check_one_line", side_effect=fake_check_one):
        dialog._on_check_line("https://www.wenku8.cc")
    assert captured.get("cookie") == COOKIE, "检测时必须携带全局登录态回退"


def test_check_all_uses_effective_cookie(dlg):
    """批量检测同样要为每条线路带上有效 Cookie。"""
    dialog, store = dlg
    seen = []
    with _patch_store(store):
        dialog.check_all_btn.setEnabled(True)
        with patch.object(dialog, "_check_one_line",
                          side_effect=lambda b, c, on_done: seen.append((b, c))):
            dialog._on_check_all()
    assert seen and all(c == COOKIE for _b, c in seen), "批量检测每条线路都应带有效 Cookie"
