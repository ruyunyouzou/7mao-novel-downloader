# -*- coding: utf-8 -*-
"""官网登录（内嵌浏览器）离线测试。

只测纯逻辑：Cookie 序列化 / 登录标志判定 / 域名匹配 / 累积判定，
不实例化 QWebEngineView（避免网络与 GPU 依赖）。UI 接线另做冒烟。
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gui.wenku8_browser_login import (  # noqa: E402
    BrowserCookieJar, cookie_name, cookie_value, cookie_domain,
    domain_matches, host_of, is_login_cookie, LOGIN_COOKIE_NAME,
)


class FakeCookie:
    """带 name()/value()/domain() 的替身，模拟 QNetworkCookie。"""

    def __init__(self, name, value, domain):
        self._name = name.encode()
        self._value = value.encode()
        self._domain = domain

    def name(self):
        return self._name

    def value(self):
        return self._value

    def domain(self):
        return self._domain


BASE_CC = "https://www.wenku8.cc"


def test_login_cookie_detection():
    assert is_login_cookie(FakeCookie("jieqiUserInfo", "a%3D123", ".wenku8.cc"))
    # 空值不算登录
    assert not is_login_cookie(FakeCookie("jieqiUserInfo", "", ".wenku8.cc"))
    # 其它 cookie 不算
    assert not is_login_cookie(FakeCookie("PHPSESSID", "abc", ".wenku8.cc"))
    assert not is_login_cookie(FakeCookie("jieqiVisitInfo", "x", ".wenku8.cc"))


def test_cookie_accessors():
    c = FakeCookie("PHPSESSID", "abc123", "www.wenku8.cc")
    assert cookie_name(c) == "PHPSESSID"
    assert cookie_value(c) == "abc123"
    assert cookie_domain(c) == "www.wenku8.cc"  # 只去前导点，www 互通由 domain_matches 负责


def test_domain_matches_www_and_bare():
    assert host_of(BASE_CC) == "www.wenku8.cc"
    assert domain_matches("wenku8.cc", BASE_CC)
    assert domain_matches(".wenku8.cc", BASE_CC)
    assert domain_matches("www.wenku8.cc", BASE_CC)
    # 不同域名不匹配
    assert not domain_matches("wenku8.net", BASE_CC)
    assert not domain_matches("evil.com", BASE_CC)


def test_jar_serializes_only_target_domain():
    jar = BrowserCookieJar(BASE_CC)
    jar.add(FakeCookie("PHPSESSID", "abc", ".wenku8.cc"))
    jar.add(FakeCookie(LOGIN_COOKIE_NAME, "u%3D1", ".wenku8.cc"))
    # 无关域名的 cookie 不应被写入
    jar.add(FakeCookie("tracking", "xyz", ".other-site.com"))
    s = jar.cookie_string()
    assert "PHPSESSID=abc" in s
    assert "jieqiUserInfo=u%3D1" in s
    assert "tracking" not in s


def test_jar_detects_login_only_on_target_domain():
    jar = BrowserCookieJar(BASE_CC)
    # 另一个域的 jieqiUserInfo 不算目标站登录
    jar.add(FakeCookie(LOGIN_COOKIE_NAME, "u%3D1", ".wenku8.net"))
    assert not jar.has_login()
    # 目标域的出现才算
    jar.add(FakeCookie(LOGIN_COOKIE_NAME, "u%3D2", ".wenku8.cc"))
    assert jar.has_login()


def test_jar_login_requires_nonempty_value():
    jar = BrowserCookieJar(BASE_CC)
    jar.add(FakeCookie(LOGIN_COOKIE_NAME, "", ".wenku8.cc"))
    assert not jar.has_login()
