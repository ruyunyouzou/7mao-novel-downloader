# -*- coding: utf-8 -*-
"""文库8 Cookie 管理。

Cookie 由用户在设置页填入（浏览器 F12 → Console → document.cookie），
持久化到 DataStore 设置项 wenku8_cookie。所有 network 请求统一经
make_session() 注入，避免散落各处的登录态处理。
"""

from __future__ import annotations

from typing import Optional

import requests

# 站点域名候选：搜索引擎收录的活站为 .net；.cc 为历史域名（部分网络返回关站旧页）。
# get_base_url() 按顺序探测，用户可在设置页用 wenku8_base_url 手动固定。
_BASE_CANDIDATES = (
    "https://www.wenku8.net",
    "https://www.wenku8.cc",
    "https://www.wenku8.com",
)
_BASE_URL = _BASE_CANDIDATES[0]  # 兼容旧引用；实际取域名请用 get_base_url()
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def get_cookie() -> str:
    """读取文库8 Cookie：用户设置优先，未配置时回退到软件内置登录态。

    内置值来自用户本人账号的 Cookie，随软件打包，开箱即有登录态；
    用户在设置页填写的新值始终优先，Cookie 过期后可直接覆盖，无需改代码。
    """
    try:
        from gui.datastore import DataStore
        custom = str(DataStore().settings_get("wenku8_cookie", "") or "").strip()
        if custom:
            return custom
    except Exception:  # noqa: BLE001
        pass
    # 用户未配置 → 用内置登录态
    try:
        from ._builtin_cookie import BUILTIN_WENKU8_COOKIE
        return str(BUILTIN_WENKU8_COOKIE or "").strip()
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


def get_base_url() -> str:
    """返回当前文库8站点根地址。

    优先级：用户在设置页固定值 `wenku8_base_url` > 缓存的探测结果 >
    候选列表首个（.net）。探测由 `probe_base_url()` 完成并缓存，避免每次请求都探测。
    """
    try:
        from gui.datastore import DataStore
        custom = str(DataStore().settings_get("wenku8_base_url", "") or "").strip()
        if custom:
            return custom.rstrip("/")
        cached = str(DataStore().settings_get("wenku8_base_cached", "") or "").strip()
        if cached:
            return cached.rstrip("/")
    except Exception:  # noqa: BLE001
        pass
    return _BASE_CANDIDATES[0]


def probe_base_url(session=None, timeout: int = 8) -> str:
    """探测候选域名，返回第一个可正常访问（非关站页/非纯登录跳转）的根地址并缓存。

    探测失败时回退到候选首个，绝不抛异常。桌面端网络正常时应能命中活站。
    """
    sess = session or requests.Session()
    for base in _BASE_CANDIDATES:
        try:
            resp = sess.get(base + "/", timeout=timeout, allow_redirects=True,
                            headers={"User-Agent": _UA})
            text = resp.text or ""
            # 关站旧页：含「本站正式关闭」且无书籍链接
            if "本站正式关闭" in text or "正式关闭" in text:
                continue
            # 正常站点：能到首页、含文库标识或书籍链接
            if "轻小说文库" in text or "/book/" in text or "会员中心" in text:
                try:
                    from gui.datastore import DataStore
                    DataStore().settings_set("wenku8_base_cached", base)
                except Exception:  # noqa: BLE001
                    pass
                return base
        except Exception:  # noqa: BLE001
            continue
    return _BASE_CANDIDATES[0]


def make_session(cookie: Optional[str] = None, timeout: int = 20) -> requests.Session:
    """构造带登录态、浏览器头的 Session，供详情/目录/正文复用。

    Cookie 的 domain 随当前站点域名动态调整（.net/.cc/.com），
    避免旧版写死 `.wenku8.cc` 导致换域名后登录态失效。
    """
    base = get_base_url()
    host = base.split("//", 1)[-1].split("/", 1)[0]
    cookie_domain = host[4:] if host.startswith("www.") else host
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": base + "/",
    })
    raw = cookie if cookie is not None else get_cookie()
    if raw:
        for key, val in parse_cookie(raw).items():
            # 主域 cookie：用不带 www 的域，保证 www/裸域都带上
            sess.cookies.set(key, val, domain="." + cookie_domain)
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
