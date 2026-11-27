# -*- coding: utf-8 -*-
"""验证官网真实搜索通道 POST /so.php 与 Jieqi 老链接解析。"""

import os
import sys
from urllib.parse import unquote_to_bytes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from gui.wenku8_core.wenku8_search import (  # noqa: E402
    _extract_books, _search_via_so_post, search_wenku8,
)


class _Resp:
    def __init__(self, html):
        self.content = html.encode("gbk")
        self.encoding = None
        self.text = html
        self.url = "https://www.wenku8.net/so.php"


class _Session:
    def __init__(self, html):
        self.html = html
        self.posts = []
        self.gets = []
        self.cookies = type("C", (), {"get_dict": lambda self: {}})()

    def post(self, url, data=None, **kw):
        self.posts.append((url, data))
        return _Resp(self.html)

    def get(self, url, **kw):
        self.gets.append(url)
        return _Resp(self.html)


HTML = (
    '<a href="/modules/article/articleinfo.php?id=2580">邻家的天使大人</a>'
    '<a href="/book/999.htm">完全无关</a>'
)


def test_extract_books_articleinfo_link():
    books = _extract_books(HTML, "https://www.wenku8.net")
    ids = {b["id"] for b in books}
    assert "2580" in ids and "999" in ids


def test_post_so_uses_gbk_keyword_and_finds_book():
    s = _Session(HTML)
    hits = _search_via_so_post("邻家天使", s, "https://www.wenku8.net")
    assert len(hits) == 1
    assert hits[0]["id"] == "2580"
    assert s.posts and s.posts[0][0].endswith("/so.php")
    body = s.posts[0][1]
    # body 是 ascii 字节，searchkey 为 GBK 百分号编码
    field = dict(
        part.split("=", 1) for part in body.decode("ascii").split("&") if "=" in part
    )
    assert unquote_to_bytes(field["searchkey"]).decode("gbk") == "邻家天使"
    assert field["searchtype"] == "articlename"
    assert field["charset"] == "gbk"


def test_search_wenku8_prefers_post_and_skips_get_when_hit(monkeypatch):
    s = _Session(HTML)
    monkeypatch.setattr("gui.wenku8_core.wenku8_search.get_base_url",
                        lambda: "https://www.wenku8.net")
    results = search_wenku8("邻家天使", session=s)
    assert results and results[0]["id"] == "2580"
    assert s.posts, "应先走官网表单 POST /so.php"
    assert not s.gets, "POST 已命中时不应再走 GET 候选"
