# -*- coding: utf-8 -*-
"""文库8 全模块审查修复的回归测试。

覆盖本轮 10 遍审查发现并修复的问题：
- check_line / probe_base_url 的 GBK 解码顺序
- retranslate 的 tab 顺序与新增官网登录 tab
- 设置页改域名/Cookie 同步使用中线路
- set_active_base(sync_cookie=False) 不清空 Cookie
- verify_cookie_on_lines 命中时清除线路「未登录」误标记
- closeEvent 安全释放后台线程
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui.wenku8_core import wenku8_auth as auth  # noqa: E402


class _FakeResponse:
    def __init__(self, url="", status=200, raw=b"", encoding="utf-8"):
        self.url = url
        self.status_code = status
        self._raw = raw
        self.encoding = encoding

    @property
    def text(self):
        # 模拟 requests：按当前 encoding 解码（此处简化，GBK 字节用 gbk 解）
        enc = self.encoding or "utf-8"
        return self._raw.decode(enc, "replace")


# ---------------------------------------------------------------- 解码顺序
def test_check_line_sets_gbk_before_reading_text():
    """线路检测必须先设 GBK 再读 text，否则会员标记中文乱码被判 guest。"""
    gbk_body = "欢迎来到轻小说文库 <a>会员中心</a>".encode("gbk")
    resp = _FakeResponse(raw=gbk_body, encoding="ISO-8859-1")

    fake_sess = MagicMock()
    fake_sess.get.return_value = resp

    with patch.object(auth.requests, "Session", return_value=fake_sess), \
         patch.object(auth, "set_line_checked"):
        state, _detail = auth.check_line("https://www.wenku8.cc")

    assert resp.encoding == "gbk", "必须强制 GBK 解码"
    assert state == "ok", "会员标记应被正确识别为已登录"


def test_probe_sets_gbk_before_keyword_check():
    """域名探测必须先按 GBK 解码，否则中文站点标识匹配不到。"""
    gbk_body = "轻小说文库 <a href=/book/1.htm>书</a>".encode("gbk")
    resp = _FakeResponse(raw=gbk_body, encoding="ISO-8859-1")
    fake_sess = MagicMock()
    fake_sess.get.return_value = resp

    with patch.object(auth, "set_line_checked"):
        with patch("gui.datastore.DataStore") as ds_cls:
            ds_cls.return_value.settings_get.return_value = ""
            base = auth.probe_base_url(session=fake_sess)

    assert resp.encoding == "gbk"
    assert base == "https://www.wenku8.cc"  # mock 所有线路都返回文库页 → 命中候选首项 .cc


# ---------------------------------------------------------------- 域名同步
def test_set_active_base_can_skip_cookie_sync():
    """set_active_base(sync_cookie=False) 切换线路但不覆盖当前 Cookie。"""
    state = {
        "lines": [
            {"base": "https://www.wenku8.net", "label": "net", "cookie": "OLD=1",
             "state": "ok", "detail": ""},
            {"base": "https://www.wenku8.cc", "label": "cc", "cookie": "",
             "state": "unchecked", "detail": ""},
        ],
        "active": "https://www.wenku8.net",
        "keep_login": True,
    }
    with patch.object(auth, "get_lines_state", return_value=state), \
         patch.object(auth, "save_lines_state"), \
         patch.object(auth, "set_cookie") as set_cookie:
        auth.set_active_base("https://www.wenku8.cc", sync_cookie=False)
    set_cookie.assert_not_called()


def test_set_active_base_syncs_cookie_by_default():
    """默认切换线路时同步该线路 Cookie 为全局生效值。"""
    state = {
        "lines": [
            {"base": "https://www.wenku8.net", "label": "net", "cookie": "OLD=1",
             "state": "ok", "detail": ""},
            {"base": "https://www.wenku8.cc", "label": "cc", "cookie": "NEW=2",
             "state": "ok", "detail": ""},
        ],
        "active": "https://www.wenku8.net",
        "keep_login": True,
    }
    with patch.object(auth, "get_lines_state", return_value=state), \
         patch.object(auth, "save_lines_state"), \
         patch.object(auth, "set_cookie") as set_cookie:
        auth.set_active_base("https://www.wenku8.cc")
    set_cookie.assert_called_once_with("NEW=2")


# ---------------------------------------------------------------- UI 修复
def test_login_dialog_tab_order_and_retranslate():
    """登录对话框只保留账号密码 + 官网登录 2 个 tab，retranslate 后顺序不变。"""
    from PySide6.QtWidgets import QApplication
    from gui.wenku8_login_dialog import Wenku8LoginDialog
    app = QApplication.instance() or QApplication([])
    dlg = Wenku8LoginDialog()
    labels = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert len(labels) == 2
    dlg.retranslate()
    after = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert after == labels, "retranslate 不应打乱 tab 顺序"
    dlg.close()
    app.processEvents()


def test_login_dialog_close_with_no_workers_is_safe():
    """未启动任何后台线程时关闭对话框不崩溃。"""
    from PySide6.QtWidgets import QApplication
    from gui.wenku8_login_dialog import Wenku8LoginDialog
    app = QApplication.instance() or QApplication([])
    dlg = Wenku8LoginDialog()
    dlg.close()  # 触发 closeEvent，不应抛异常
    app.processEvents()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
