# -*- coding: utf-8 -*-
"""文库8「浏览器搜索」兜底通道。

参考 mikufoxxx/Wenku8_Flutter：在线书源的搜索页在真实 WebView 里打开，
由浏览器完成 Cloudflare 质询并携带登录 Cookie，再把页面结果解析成书目。

普通 requests 拿不到 `.net` 的搜索结果时（403 / <h1>Welcome</h1> 占位页），
改用本模块在软件内嵌 Chromium 中执行同源 fetch，按 GBK 解码后返回 HTML，
交给 wenku8_search._extract_books() 复用同一套解析与模糊匹配逻辑。
"""

from __future__ import annotations

import json
import re
from typing import List
from urllib.parse import quote

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkCookie

from .wenku8_core.wenku8_auth import get_base_url, get_cookie, parse_cookie
from .wenku8_core.wenku8_match import matches as _matches, relevance as _relevance
from .wenku8_core.wenku8_search import _extract_books

# 与参考 App 发行包一致的桌面浏览器指纹（Cloudflare 放行 WebView 时使用）
_WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
)


def build_search_url(base: str, keyword: str, page: int = 1) -> str:
    """构造与官网搜索表单一致的 GBK 查询地址。"""
    kw = quote(str(keyword or "").strip().encode("gbk"))
    url = (f"{base.rstrip('/')}/modules/article/search.php"
           f"?searchtype=articlename&searchkey={kw}&charset=gbk")
    if page > 1:
        url += f"&page={page}"
    return url


def parse_search_html(html: str, keyword: str, base: str) -> List[dict]:
    """把搜索页 HTML 解析为命中书目（含相关性排序）。"""
    books = _extract_books(html or "", base)
    hits = [b for b in books if _matches(keyword, b.get("title", ""))]
    for b in hits:
        b["_rel"] = _relevance(keyword, b.get("title", ""))
        b.pop("_rel", None)
    hits.sort(key=lambda b: _relevance(keyword, b.get("title", "")) , reverse=True)
    return hits


class Wenku8WebSearch(QObject):
    """在 GUI 线程的 WebEngine 中完成一次文库8搜索。

    用法：创建对象 → 连接 finished/failed → start()。对象需由调用方保持引用，
    完成后自动 deleteLater()。
    """

    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, keyword: str, page: int = 1, base: str = "", parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView

        self.keyword = str(keyword or "").strip()
        self.page_no = max(1, int(page or 1))
        self.base = (base or get_base_url()).rstrip("/")
        self._done = False

        # 隐藏的浏览器窗口：必须有 QWebEngineView 才能正常执行 JS / Cookie 质询
        self.view = QWebEngineView()
        self.view.setWindowFlags(self.view.windowFlags())
        self.view.resize(1, 1)
        self.view.setVisible(False)
        self.profile = QWebEngineProfile(self)
        self.profile.setHttpUserAgent(_WEB_UA)
        self.store = self.profile.cookieStore()
        self.webpage = QWebEnginePage(self.profile, self.view)
        self.view.setPage(self.webpage)

        self._inject_cookies()
        self.webpage.loadFinished.connect(self._on_home_loaded)
        self.view.setUrl(QUrl(self.base + "/index.php"))
        # 兜底超时：WebEngine 在某些网络下不回调 loadFinished
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._fail("浏览器搜索超时"))
        self._timer.start(30000)

    def _inject_cookies(self) -> None:
        """把软件保存的登录 Cookie 注入浏览器 Profile。"""
        try:
            host = self.base.split("//", 1)[-1].split("/", 1)[0]
            domain = "." + (host[4:] if host.startswith("www.") else host)
            cookies = []
            for key, val in parse_cookie(get_cookie()).items():
                c = QNetworkCookie(key.encode(), val.encode("utf-8", "ignore"))
                c.setDomain(domain)
                c.setPath("/")
                cookies.append(c)
            if cookies:
                self.store.setCookies(cookies, QUrl(self.base + "/"))
        except Exception:  # noqa: BLE001
            pass

    def _finish(self, result: List[dict] | None = None, err: str = "") -> None:
        if self._done:
            return
        self._done = True
        self._timer.stop()
        poll = getattr(self, "_poll", None)
        if poll is not None:
            poll.stop()
        try:
            self.webpage.loadFinished.disconnect(self._on_home_loaded)
        except Exception:  # noqa: BLE001
            pass
        if err:
            self.failed.emit(err)
        else:
            self.finished.emit(result or [])
        self.deleteLater()

    def _fail(self, msg: str) -> None:
        self._finish(err=msg)

    def _on_home_loaded(self, ok: bool) -> None:
        # 首次加载首页：建立 Cloudflare clearance；随后同源 fetch 搜索接口
        url = build_search_url(self.base, self.keyword, self.page_no)
        # Qt 的 runJavaScript 不会等待 Promise：把结果写入页面全局变量再轮询。
        js = (
            "window.__wk8result='PENDING';"
            "(async function(){"
            f"const u={json.dumps(url)};"
            "try{const r=await fetch(u,{credentials:'include',"
            "headers:{'Accept':'text/html,application/xhtml+xml'}});"
            "const b=await r.arrayBuffer();"
            "window.__wk8result=new TextDecoder('gbk').decode(b);}"
            "catch(e){window.__wk8result='ERR:'+e;}})();"
        )
        try:
            self.webpage.runJavaScript(js)
            self._poll_count = 0
            self._poll = QTimer(self)
            self._poll.setInterval(300)
            self._poll.timeout.connect(self._poll_result)
            self._poll.start()
        except Exception as exc:  # noqa: BLE001
            self._fail(f"浏览器搜索执行失败：{exc}")

    def _poll_result(self) -> None:
        self._poll_count = getattr(self, "_poll_count", 0) + 1
        if self._poll_count > 80:  # 24 秒
            self._poll.stop()
            self._fail("浏览器搜索响应超时")
            return
        self.webpage.runJavaScript("window.__wk8result || ''", self._on_search_html)

    def _on_search_html(self, html: str) -> None:
        if self._done:
            return
        if html in ("", "PENDING"):
            return  # 异步 fetch 尚未完成，继续轮询
        poll = getattr(self, "_poll", None)
        if poll is not None:
            poll.stop()
        if not isinstance(html, str) or html.startswith("ERR:"):
            self._fail("浏览器搜索未返回页面内容")
            return
        if "<h1>Welcome</h1>" in html:
            self._fail("站点搜索接口返回占位页")
            return
        try:
            result = parse_search_html(html, self.keyword, self.base)
        except Exception as exc:  # noqa: BLE001
            self._fail(f"搜索结果解析失败：{exc}")
            return
        self._finish(result)
