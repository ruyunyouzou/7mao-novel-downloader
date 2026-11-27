# -*- coding: utf-8 -*-
"""文库8「共享浏览器会话」上下文。

为什么需要它
------------
参考项目 mikufoxxx/Wenku8_Flutter 之所以在官网登录后能正常搜索/访问，
关键在于**登录、浏览、搜索全程复用同一个 WebView 会话**：Cloudflare 放行
凭证（cf_clearance）与登录 Cookie（jieqiUserInfo）在同一浏览器上下文内
持续有效。

旧实现把三条通道割裂：
  1. 官网登录用临时 QWebEngineProfile，窗口关闭即销毁，clearance 丢失；
  2. 搜索兜底另建一个独立 profile，把 Cookie 字符串重新注入——
     cf_clearance 与浏览器指纹/IP 绑定，跨 profile 注入后立即失效；
  3. 线路检测/搜索主通道用 requests，指纹不是浏览器，必被 403。
结果是「官网登录成功，却仍显示风控拦截、搜索 0 结果」。

本模块提供进程级**持久化** QWebEngineProfile 单例，登录与搜索共用它，
clearance 不再跨上下文丢失。UA 与参考 App 发行版一致。

纯逻辑（UA、Cookie 拆分/域名推导）不依赖 Qt，便于离线单测。
"""

from __future__ import annotations

from typing import List, Tuple

# 与参考 App 发行包一致的桌面 Edge 指纹（Cloudflare 放行 WebView 时使用）
WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
)

# 进程级共享 profile（GUI 线程惰性创建，模块持有强引用，避免随对话框销毁）
_SHARED_PROFILE = None


# ---------------------------------------------------------------------------
# 纯逻辑（可离线单测，不导入 Qt）
# ---------------------------------------------------------------------------

def host_of(base: str) -> str:
    """从 base 取主机名（去掉协议与路径）。"""
    return str(base or "").split("//", 1)[-1].split("/", 1)[0].lower()


def cookie_domain_of(base: str) -> str:
    """推导可同时覆盖 www 与裸域的 Cookie domain（如 .wenku8.net）。"""
    host = host_of(base)
    bare = host[4:] if host.startswith("www.") else host
    return "." + bare if bare else ""


def split_cookie(cookie: str) -> List[Tuple[str, str]]:
    """把 'a=1; b=2' 拆成 [(a,1), ...]，忽略空段。"""
    out: List[Tuple[str, str]] = []
    for part in str(cookie or "").split(";"):
        key, sep, val = part.strip().partition("=")
        if sep and key:
            out.append((key, val))
    return out


def has_login_marker(cookie: str) -> bool:
    """Cookie 字符串是否含 Jieqi CMS 登录标志。"""
    return any(k.lower() == "jieqiuserinfo" and str(v).strip()
               for k, v in split_cookie(cookie))


# ---------------------------------------------------------------------------
# GUI 线程调用（惰性导入 Qt，避免无 QApplication 环境导入即失败）
# ---------------------------------------------------------------------------

def get_shared_profile():
    """返回进程级共享的持久化 WebEngine profile。

    使用带 storageName 的构造形式（非 off-the-record），Cookie / clearance
    会持久化到磁盘并在同一进程内跨对话框复用。模块级强引用保证它不会随
    登录窗口关闭而被销毁——这是登录态能在后续搜索中复用的前提。
    """
    global _SHARED_PROFILE
    from PySide6.QtWebEngineCore import QWebEngineProfile
    if _SHARED_PROFILE is None:
        _SHARED_PROFILE = QWebEngineProfile("wenku8-shared")
        _SHARED_PROFILE.setHttpUserAgent(WEB_UA)
    return _SHARED_PROFILE


def inject_cookies(base: str, cookie: str) -> int:
    """把 Cookie 字符串注入共享 profile，返回注入条数。

    用于「粘贴 Cookie / 账号密码登录」这类没有经过内嵌浏览器质询的场景：
    此时共享 profile 里没有登录态，需要把完整 Cookie（含 clearance）写入。
    官网浏览器登录场景里 profile 本就持有同一批 Cookie，重复注入是幂等的。
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtNetwork import QNetworkCookie

    profile = get_shared_profile()
    store = profile.cookieStore()
    domain = cookie_domain_of(base)
    url = QUrl((base or "").rstrip("/") + "/")
    cookies = []
    for key, val in split_cookie(cookie):
        c = QNetworkCookie(key.encode(), val.encode("utf-8", "ignore"))
        c.setDomain(domain)
        c.setPath("/")
        cookies.append(c)
    if cookies:
        store.setCookies(cookies, url)
    return len(cookies)
