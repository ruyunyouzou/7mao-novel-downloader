# -*- coding: utf-8 -*-
"""提速回归：风控页熔断 + 超时缩短 + 正常站点结果覆盖不变。"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from gui.wenku8_core import wenku8_search as ws  # noqa: E402


class _Resp:
    def __init__(self, html):
        self.text = html
        self.encoding = None


class _Session:
    def __init__(self, html):
        self.html = html
        self.urls = []
        self.timeouts = []
        self.cookies = type("C", (), {"get_dict": lambda self: {}})()

    def get(self, url, timeout=None, **kw):
        self.urls.append(url)
        self.timeouts.append(timeout)
        return _Resp(self.html)

    def post(self, url, data=None, **kw):
        self.urls.append(url)
        self.timeouts.append(kw.get("timeout"))
        return _Resp(self.html)


BLOCK = "<html><body><h1>Welcome</h1></body></html>"
HIT = '<html><a href="/book/2580.htm">邻家的天使</a></html>'
EMPTY = "<html><body></body></html>"


def test_request_timeout_shortened():
    assert ws._REQUEST_TIMEOUT <= 8
    assert ws._SEARCH_BUDGET <= 30


def test_block_page_circuit_breaks_all_candidates():
    """风控页出现后应立即熔断，不再逐个尝试 12 个 GET 候选。"""
    s = _Session(BLOCK)
    hits = ws._search_on_site("邻家天使", s, "https://www.wenku8.net")
    assert hits == []
    assert len(s.urls) <= 2, f"风控页应快速熔断，实际请求 {len(s.urls)} 次"


def test_block_detector():
    assert ws._is_blocked("<h1>Welcome</h1>")
    assert ws._is_blocked("Attention Required! | Cloudflare")
    assert not ws._is_blocked(HIT)


def test_normal_site_still_finds_book():
    """站点正常时命中结果不受提速影响。"""
    s = _Session(HIT)
    hits = ws._search_on_site("邻家天使", s, "https://www.wenku8.cc")
    assert hits and hits[0]["id"] == "2580"
