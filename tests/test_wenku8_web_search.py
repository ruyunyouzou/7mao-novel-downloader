# -*- coding: utf-8 -*-
"""文库8浏览器搜索通道的纯逻辑测试（不依赖 WebEngine 渲染）。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gui.wenku8_web_search import build_search_url, parse_search_html  # noqa: E402


def test_build_search_url_gbk_and_charset():
    url = build_search_url("https://www.wenku8.net/", "邻家天使", 2)
    assert "searchtype=articlename" in url
    assert "searchkey=%C1%DA%BC%D2%CC%EC%CA%B9" in url
    assert "charset=gbk" in url
    assert url.endswith("&page=2")


def test_parse_search_html_fuzzy_match():
    html = (
        '<a href="/book/2580.htm">邻家的天使</a>'
        '<a href="/book/1.htm">完全无关的书</a>'
    )
    hits = parse_search_html(html, "邻家天使", "https://www.wenku8.net")
    assert len(hits) == 1
    assert hits[0]["id"] == "2580"
    assert hits[0]["title"] == "邻家的天使"
    assert "_rel" not in hits[0]
    # 回归：结果须含 UI 所需同构字段，否则结果表格硬取键会崩溃
    for key in ("words_num", "tags", "score", "source"):
        assert key in hits[0]


def test_parse_search_html_ignores_welcome_stub():
    hits = parse_search_html("<html><body><h1>Welcome</h1></body></html>",
                             "邻家天使", "https://www.wenku8.cc")
    assert hits == []
