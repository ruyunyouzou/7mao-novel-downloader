# -*- coding: utf-8 -*-
"""回归测试：完整 Cookie 粘贴后仍被误判「登录态无效」。

用户实测 bug：浏览器 document.cookie 复制出完整 Cookie（含 jieqiUserInfo），
粘贴到软件却显示「登录态无效」。根因有两层：
  1. 验证默认用 get_base_url()（可能是 .net），Cookie 来自 .cc → 域名不匹配；
  2. is_logged_in 放宽后把游客首页特征当登录有效，判据不可靠。

本轮修复：
  - is_logged_in 以 Cookie 内 jieqiUserInfo 为权威判据，strict 模式只认首页会员标记；
  - verify_cookie_on_lines 逐线路严格探测推断来源域名，网络失败时用登录标志兜底；
  - 登录弹窗与设置页校验都改接 verify_cookie_on_lines。

覆盖：
  1. 宽松/严格两种登录判据的差异
  2. 跨线路探测命中来源域名
  3. 网络全失败时凭登录标志兜底判有效
  4. 无标志且探测失败才判无效
  5. 带换行的 Cookie 正确解析
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
from gui.wenku8_core import wenku8_auth as auth  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


COOKIE = "PHPSESSID=abc123; jieqiUserInfo=abc%3D123; jieqiVisitInfo=visit"
NO_MARKER = "PHPSESSID=abc123; Hm_lvt_1=123456"


class FakeStore:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def settings_get(self, key, default=None):
        return self.data.get(key, default)

    def settings_set(self, key, value):
        self.data[key] = value


class FakeCookies:
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get_dict(self):
        return dict(self._data)

    def set(self, key, val, domain=None):
        self._data[key] = val


class FakeResp:
    def __init__(self, url, text):
        self.url = url
        self._text = text
        self.encoding = None

    @property
    def text(self):
        return self._text


class FakeSession:
    """最小 Session 替身： cookies.get_dict + get()，不触发真实网络。"""

    def __init__(self, text="", url="https://www.wenku8.cc/", cookies=None):
        self.cookies = FakeCookies(cookies or {})
        self._resp = FakeResp(url, text)

    def get(self, *args, **kwargs):
        return self._resp


@pytest.fixture
def store():
    return FakeStore()


def _patch_store(store):
    return patch.object(ds_mod, "DataStore", return_value=store)


# ---------------------------------------------------------------------------
# 登录判据
# ---------------------------------------------------------------------------

def test_loose_login_accepts_jieqi_marker():
    """宽松模式：Cookie 含 jieqiUserInfo 即判有效，完全不依赖网络。"""
    sess = FakeSession(cookies={"jieqiUserInfo": "x"})

    def _boom(*a, **kw):
        raise AssertionError("宽松模式不应发起网络请求")

    sess.get = _boom
    assert auth.is_logged_in(sess) is True


def test_strict_login_ignores_jieqi_marker_on_guest_page():
    """严格模式：即使有登录标志，游客首页（无会员标记）也判无效。"""
    sess = FakeSession(
        text="<html>轻小说文库 /book/ 小说排行榜</html>",
        url="https://www.wenku8.net/",
        cookies={"jieqiUserInfo": "x"},
    )
    assert auth.is_logged_in(sess, strict=True) is False


def test_strict_login_accepts_member_marker():
    """严格模式：首页含会员标记才判有效。"""
    sess = FakeSession(text="<html>会员中心 我的书架</html>",
                       url="https://www.wenku8.cc/")
    assert auth.is_logged_in(sess, strict=True) is True


def test_strict_login_rejects_redirect_to_login():
    """严格模式：跳转 login.php 判无效。"""
    sess = FakeSession(text="登录", url="https://www.wenku8.net/login.php")
    assert auth.is_logged_in(sess, strict=True) is False


# ---------------------------------------------------------------------------
# 跨线路验证
# ---------------------------------------------------------------------------

def test_verify_hits_source_domain(store):
    """逐线路严格探测：命中 Cookie 真正来源域名，并设为使用中线路。"""
    net_guest = FakeSession(text="轻小说文库", url="https://www.wenku8.net/login.php")
    cc_ok = FakeSession(text="会员中心", url="https://www.wenku8.cc/")

    def fake_make_session(cookie, timeout=None, base=None):
        return cc_ok if "wenku8.cc" in base else net_guest

    with _patch_store(store), \
         patch.object(auth, "make_session", side_effect=fake_make_session), \
         patch.object(auth, "set_active_base") as set_active, \
         patch.object(auth, "save_line_cookie") as save_cookie:
        ok, base, msg = auth.verify_cookie_on_lines(COOKIE, timeout=1)

    assert ok is True
    assert base == "https://www.wenku8.cc"
    assert "wenku8.cc" in msg
    set_active.assert_called_once_with("https://www.wenku8.cc")
    save_cookie.assert_called_once_with("https://www.wenku8.cc", COOKIE)


def test_verify_falls_back_on_marker_when_all_blocked(store):
    """所有线路探测失败（如 Cloudflare 风控）时，凭登录标志仍判有效。"""
    blocked = FakeSession(text="", url="https://www.wenku8.net/login.php")

    def fake_make_session(cookie, timeout=None, base=None):
        return blocked

    with _patch_store(store), \
         patch.object(auth, "make_session", side_effect=fake_make_session), \
         patch.object(auth, "get_active_base", return_value="https://www.wenku8.net"), \
         patch.object(auth, "set_active_base") as set_active, \
         patch.object(auth, "save_line_cookie") as save_cookie:
        ok, base, msg = auth.verify_cookie_on_lines(COOKIE, timeout=1)

    assert ok is True
    assert base == "https://www.wenku8.net"
    assert "网络探测受限" in msg
    set_active.assert_not_called()  # 无法推断域名时不改动使用中线路
    save_cookie.assert_called_once_with("https://www.wenku8.net", COOKIE)


def test_verify_fails_without_marker_when_all_blocked(store):
    """无登录标志且所有线路都失败 → 判无效，提示重新获取。"""
    blocked = FakeSession(text="", url="https://www.wenku8.net/login.php")

    with _patch_store(store), \
         patch.object(auth, "make_session", return_value=blocked):
        ok, base, msg = auth.verify_cookie_on_lines(NO_MARKER, timeout=1)

    assert ok is False
    assert base == ""
    assert "jieqiUserInfo" in msg


def test_verify_empty_cookie(store):
    with _patch_store(store):
        ok, base, msg = auth.verify_cookie_on_lines("   ", timeout=1)
    assert (ok, base) == (False, "")
    assert msg == "Cookie 为空"


def test_verify_normalizes_newlines(store):
    """带换行分隔的 Cookie 仍能解析出登录标志并判有效。"""
    multiline = "PHPSESSID=abc123\njieqiUserInfo=abc%3D123"
    blocked = FakeSession(text="", url="https://www.wenku8.net/login.php")

    with _patch_store(store), \
         patch.object(auth, "make_session", return_value=blocked), \
         patch.object(auth, "get_active_base", return_value="https://www.wenku8.net"), \
         patch.object(auth, "save_line_cookie"):
        ok, _base, _msg = auth.verify_cookie_on_lines(multiline, timeout=1)

    assert ok is True, "换行分隔的 Cookie 应被规范化为 ';' 后再解析"


def test_verify_exported_from_package():
    """verify_cookie_on_lines 已从 gui.wenku8_core 包导出（惰性导入可解析）。"""
    from gui import wenku8_core
    assert callable(wenku8_core.verify_cookie_on_lines)


def test_verify_worker_wires_cross_line_verify(qapp):
    """登录弹窗的后台校验线程调用跨线路验证，并把命中域名提示带给用户。"""
    from gui import wenku8_login_dialog as dlg_mod
    worker = dlg_mod._VerifyWorker(COOKIE)
    emitted = {}
    worker.finished_ok.connect(lambda ok, msg: emitted.update(ok=ok, msg=msg))

    with patch.object(auth, "verify_cookie_on_lines",
                      return_value=(True, "https://www.wenku8.cc",
                                    "登录态有效（线路：https://www.wenku8.cc）")) as mocked:
        worker.run()

    mocked.assert_called_once_with(COOKIE)
    assert emitted.get("ok") is True
    assert "wenku8.cc" in emitted.get("msg", "")
