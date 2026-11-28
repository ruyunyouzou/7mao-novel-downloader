# -*- coding: utf-8 -*-
"""官网线路管理单测（离线，全 mock）。

验证多线路存储、切换、增删、逐线路检测状态归类。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gui.wenku8_core import wenku8_auth as auth  # noqa: E402


class _FakeStore:
    """内存版 DataStore，替代真实设置存储。"""
    def __init__(self):
        self.data = {}

    def settings_get(self, key, default=None):
        return self.data.get(key, default)

    def settings_set(self, key, value):
        self.data[key] = value


def _with_store(fn):
    store = _FakeStore()
    with patch("gui.datastore.DataStore", return_value=store):
        return fn(store)


def test_default_lines_have_three_candidates():
    def run(store):
        state = auth.get_lines_state()
        bases = [ln["base"] for ln in state["lines"]]
        assert "https://www.wenku8.net" in bases
        assert "https://www.wenku8.cc" in bases
        assert state["active"] == "https://www.wenku8.cc"  # 默认优先活站 .cc
        assert state["keep_login"] is True
    _with_store(run)


def test_add_and_remove_custom_line():
    def run(store):
        ok, base, msg = auth.add_line("www.example.com")
        assert ok and base == "https://www.example.com"
        state = auth.get_lines_state()
        assert any(ln["base"] == base for ln in state["lines"])

        # 重复添加被拒
        ok2, _, _ = auth.add_line("https://www.example.com")
        assert not ok2

        # 内置线路不可删，自定义可删
        assert auth.remove_line("https://www.wenku8.net") is False
        assert auth.remove_line(base) is True
        assert not any(ln["base"] == base
                       for ln in auth.get_lines_state()["lines"])
    _with_store(run)


def test_switch_line_sets_active_and_cookie():
    def run(store):
        auth.save_line_cookie("https://www.wenku8.cc", "PHPSESSID=abc")
        auth.set_active_base("https://www.wenku8.cc")
        state = auth.get_lines_state()
        assert state["active"] == "https://www.wenku8.cc"
        assert store.settings_get("wenku8_base_url") == "https://www.wenku8.cc"
        # 当前生效 cookie 同步为该线路 cookie
        assert "PHPSESSID=abc" in auth.get_cookie()
    _with_store(run)


def test_classify_response_states():
    assert auth.classify_line_response(200, "https://www.wenku8.net/", "会员中心")[0] == "ok"
    assert auth.classify_line_response(302, "https://www.wenku8.net/login.php", "")[0] == "guest"
    assert auth.classify_line_response(200, "https://www.wenku8.cc/", "本站正式关闭")[0] == "closed"
    assert auth.classify_line_response(403, "https://www.wenku8.net/", "cloudflare")[0] == "blocked"
    assert auth.classify_line_response(200, "https://www.wenku8.net/", "轻小说文库")[0] == "guest"


def test_check_line_records_state():
    def run(store):
        class _Resp:
            status_code = 200
            url = "https://www.wenku8.net/"
            text = "会员中心"

        class _Sess:
            headers = {}
            cookies = type("C", (), {"set": lambda *a, **k: None})()

            def get(self, *a, **k):
                return _Resp()

        with patch("gui.wenku8_core.wenku8_auth.make_session", return_value=_Sess()):
            state, detail = auth.check_line("https://www.wenku8.net", "")
        assert state == "ok"
        ln = next(l for l in auth.get_lines_state()["lines"]
                  if l["base"] == "https://www.wenku8.net")
        assert ln["state"] == "ok"
    _with_store(run)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
