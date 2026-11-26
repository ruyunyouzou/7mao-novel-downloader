# -*- coding: utf-8 -*-
"""文库8账号密码登录单测（离线，全部 mock，不访问真实站点）。

验证登录协议字段、URL 构造、成功/失败判定；沙箱 IP 被站点风控，无法真实登录，
故用 mock 严格验证协议层逻辑。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gui.wenku8_core.wenku8_auth import (  # noqa: E402
    _build_login_form,
    login_with_password,
)


def _fake_session(status: int = 200, cookies: dict | None = None,
                  text: str = ""):
    sess = MagicMock()
    sess.headers = {}  # 代码调用 headers.update，需真实 dict
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    sess.cookies.get_dict.return_value = dict(cookies or {})
    sess.post.return_value = resp
    return sess, resp


class TestBuildLoginForm(unittest.TestCase):
    def test_required_fields(self) -> None:
        form = _build_login_form("  user  ", "pass123")
        self.assertEqual(
            set(form), {"username", "password", "usecookie", "action", "submit"}
        )
        self.assertEqual(form["username"], "user")  # 用户名去空白
        self.assertEqual(form["password"], "pass123")
        self.assertEqual(form["usecookie"], "315360000")
        self.assertEqual(form["action"], "login")
        self.assertEqual(form["submit"], "登录".encode("gbk"))  # GBK 字节

    def test_password_kept_verbatim(self) -> None:
        # 密码不做 strip，避免改变用户真实密码
        self.assertEqual(_build_login_form("u", " p " )["password"], " p ")

    def test_empty_username(self) -> None:
        self.assertEqual(_build_login_form("   ", "p")["username"], "")


class TestLoginWithPassword(unittest.TestCase):
    def test_empty_credentials_rejected_before_network(self) -> None:
        for u, p in (("", "x"), ("u", ""), ("  ", "p")):
            ok, cookie, msg = login_with_password(u, p)
            self.assertFalse(ok, (u, p))
            self.assertEqual(cookie, "")

    def test_success_by_cookie(self) -> None:
        sess, _ = _fake_session(
            cookies={"PHPSESSID": "abc123", "jieqiUserInfo": "uid=7"},
            text="",  # 响应体可能是挑战页，靠 Cookie 判定
        )
        with patch("gui.wenku8_core.wenku8_auth.requests.Session",
                   return_value=sess):
            ok, cookie, msg = login_with_password(
                "user", "pass", base="https://www.wenku8.net"
            )
        self.assertTrue(ok)
        self.assertIn("PHPSESSID=abc123", cookie)
        self.assertIn("jieqiUserInfo=uid=7", cookie)
        self.assertIn("成功", msg)

        # URL / 表单协议
        args, kwargs = sess.post.call_args
        self.assertIn("/login.php?do=submit", args[0])
        self.assertIn("jumpurl=", args[0])
        self.assertEqual(kwargs["data"], _build_login_form("user", "pass"))
        self.assertIn("www.wenku8.net", sess.headers["Referer"])

    def test_success_by_text_without_cookie(self) -> None:
        # 某些响应 Set-Cookie 在 history 里，正文含会员标记也算成功
        sess, _ = _fake_session(cookies={}, text="欢迎回来，退出登录")
        with patch("gui.wenku8_core.wenku8_auth.requests.Session",
                   return_value=sess):
            ok, cookie, msg = login_with_password("user", "pass")
        self.assertTrue(ok)
        self.assertEqual(cookie, "")

    def test_forbidden_403(self) -> None:
        sess, _ = _fake_session(status=403)
        with patch("gui.wenku8_core.wenku8_auth.requests.Session",
                   return_value=sess):
            ok, cookie, msg = login_with_password("user", "pass")
        self.assertFalse(ok)
        self.assertEqual(cookie, "")
        self.assertIn("403", msg)
        self.assertIn("Cookie", msg)

    def test_wrong_password(self) -> None:
        sess, _ = _fake_session(cookies={}, text="密码错误，请重新输入")
        with patch("gui.wenku8_core.wenku8_auth.requests.Session",
                   return_value=sess):
            ok, cookie, msg = login_with_password("user", "wrong")
        self.assertFalse(ok)
        self.assertIn("密码错误", msg)

    def test_network_exception(self) -> None:
        sess, _ = _fake_session()
        sess.post.side_effect = RuntimeError("connection refused")
        with patch("gui.wenku8_core.wenku8_auth.requests.Session",
                   return_value=sess):
            ok, cookie, msg = login_with_password("user", "pass")
        self.assertFalse(ok)
        self.assertIn("登录请求失败", msg)


if __name__ == "__main__":
    unittest.main()
