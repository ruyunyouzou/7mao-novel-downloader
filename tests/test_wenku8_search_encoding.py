# -*- coding: utf-8 -*-
"""回归测试：文库8站内搜索关键词编码（GBK 优先 / UTF-8 兜底）。

用户本机已登录、线路连接正常，但搜「邻家天使」仍 0 结果。根因：
wenku8_search.py 旧代码 quote() 默认 UTF-8，把中文编成 %E9%82%BB...，
文库8全站 GBK、表单按 GBK 提交（searchkey=%C1%DA...），站点按 GBK
解码 UTF-8 字节成乱码 → 站内搜索无命中。

覆盖：
  1. 编码候选：GBK 排首位且字节正确，UTF-8 兜底、去重
  2. _search_on_site：GBK 接口命中时立即返回（不再走 UTF-8 / 分类页）
  3. GBK 接口失败时 UTF-8 兜底命中
  4. 全部接口失败返回空（由分类页兜底）
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gui.wenku8_core import wenku8_search as search_mod  # noqa: E402


def test_encoding_variants_gbk_first():
    variants = search_mod._encode_keyword_variants("邻家天使")
    assert len(variants) == 2
    # GBK 应排第一：%C1%DA%BC%D2%CC%EC%CA%B9
    assert variants[0].lower() == "%c1%da%bc%d2%cc%ec%ca%b9"
    # UTF-8 兜底：%E9%82%BB%E5%AE%B6%E5%A4%A9%E4%BD%BF
    assert "e9%82%bb" in variants[1].lower()


def test_encoding_variants_dedupe_and_empty():
    # 纯 ASCII 在 GBK/UTF-8 下编码相同，应去重只剩 1 个
    assert search_mod._encode_keyword_variants("abc") == ["abc"]
    assert search_mod._encode_keyword_variants("   ") == []


class FakeResp:
    def __init__(self, html):
        self.text = html
        self.encoding = None


class FakeSession:
    """记录请求 URL，按 URL 里的编码返回不同 HTML。"""

    def __init__(self, html_by_fragment=None):
        self.urls = []
        self.html_by_fragment = html_by_fragment or {}

    def get(self, url, timeout=None):
        self.urls.append(url)
        for frag, html in self.html_by_fragment.items():
            if frag.lower() in url.lower():
                return FakeResp(html)
        return FakeResp("<html></html>")


# 搜索结果页：链接 /book/2580.htm + 书名「邻家的天使」
HIT_HTML = '<html><a href="/book/2580.htm">邻家的天使</a></html>'


def test_gbk_hit_returns_immediately():
    """GBK 编码的接口命中 → 直接返回，不再尝试 UTF-8 或分类页。"""
    gbk_kw = search_mod._encode_keyword_variants("邻家天使")[0]
    # 让第一个候选接口（带 searchtype 的真实接口）命中
    first_endpoint = search_mod._SEARCH_ENDPOINTS[0].split("?")[0]
    sess = FakeSession({first_endpoint + "?": HIT_HTML})
    hits = search_mod._search_on_site("邻家天使", sess, "https://www.wenku8.cc")
    assert hits and hits[0]["id"] == "2580"
    assert hits[0]["title"] == "邻家的天使"
    # 命中即停：只发 1 个请求，且是 GBK 编码
    assert len(sess.urls) == 1
    assert gbk_kw.lower() in sess.urls[0].lower()


def test_utf8_fallback_when_gbk_fails():
    """GBK 接口无结果 → UTF-8 兜底命中。"""
    utf8_kw = search_mod._encode_keyword_variants("邻家天使")[1]
    sess = FakeSession({"/so.php?keyword=" + utf8_kw: HIT_HTML})
    hits = search_mod._search_on_site("邻家天使", sess, "https://www.wenku8.cc")
    assert hits and hits[0]["id"] == "2580"
    assert any("e9%82%bb" in u.lower() for u in sess.urls), "应尝试过 UTF-8 编码"


def test_all_interfaces_fail_returns_empty():
    sess = FakeSession({})
    hits = search_mod._search_on_site("不存在的书名xyz", sess, "https://www.wenku8.cc")
    assert hits == []
    # 2 种编码 × 全部接口（每种编码都跑完全部接口）
    assert len(sess.urls) == 2 * len(search_mod._SEARCH_ENDPOINTS)
