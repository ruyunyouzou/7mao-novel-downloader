# -*- coding: utf-8 -*-
"""文库8 Cookie 管理。

Cookie 由用户在设置页填入（浏览器 F12 → Console → document.cookie），
持久化到 DataStore 设置项 wenku8_cookie。所有 network 请求统一经
make_session() 注入，避免散落各处的登录态处理。
"""

from __future__ import annotations

from typing import Optional

import requests

_BASE_URL = "https://www.wenku8.cc"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def get_cookie() -> str:
    """读取用户配置的文库8 Cookie 字符串。"""
    try:
        from gui.datastore import DataStore
        return str(DataStore().settings_get("wenku8_cookie", "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def set_cookie(cookie: str) -> None:
    """保存文库8 Cookie（设置页调用）。"""
    try:
        from gui.datastore import DataStore
        DataStore().settings_set("wenku8_cookie", str(cookie or "").strip())
    except Exception:  # noqa: BLE001
        pass


def parse_cookie(cookie: str) -> dict:
    """把 'a=1; b=2' 形式拆成字典。"""
    out = {}
    for part in str(cookie or "").split(";"):
        key, sep, val = part.strip().partition("=")
        if sep and key:
            out[key] = val
    return out


def make_session(cookie: Optional[str] = None, timeout: int = 20) -> requests.Session:
    """构造带登录态、浏览器头的 Session，供详情/目录/正文复用。"""
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": _BASE_URL + "/",
    })
    raw = cookie if cookie is not None else get_cookie()
    if raw:
        for key, val in parse_cookie(raw).items():
            sess.cookies.set(key, val, domain=".wenku8.cc")
    sess.timeout = timeout  # type: ignore[attr-defined]
    return sess


def is_logged_in(session: Optional[requests.Session] = None) -> bool:
    """探测登录态是否有效：访问首页看是否跳 login.php / 是否含会员标记。"""
    sess = session or make_session()
    try:
        resp = sess.get(_BASE_URL + "/", timeout=15, allow_redirects=True)
        resp.encoding = "gbk"
        if "login.php" in resp.url:
            return False
        text = resp.text
        return ("会员中心" in text or "我的书架" in text or "退出" in text)
    except Exception:  # noqa: BLE001
        return False
