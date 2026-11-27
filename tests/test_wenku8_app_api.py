# -*- coding: utf-8 -*-
"""文库8官方 App API 通道协议单测（离线，全 mock）。

协议来自 zsakvo/wenku8x 的 lib/http/api.dart + ajax.dart 交叉验证：
POST app.wenku8.com/android.php，form 含 appver/request(base64 UTF-8)/timetoken，
Android UA，搜索返回 XML：<item aid="id">书名</item>。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gui.wenku8_core import wenku8_app_api as api  # noqa: E402


def test_encode_request_is_base64_utf8():
    enc = api.encode_request("action=search&searchtype=articlename")
    import base64
    assert base64.b64decode(enc).decode("utf-8") == \
        "action=search&searchtype=articlename"


def test_build_search_param_keeps_raw_keyword():
    # 关键契约：searchkey 必须是原始中文，不能预先 percent 编码。
    # 参考 wenku8x：参数串里是原始中文，整体再做 base64(UTF-8)。
    param = api.build_search_param("邻家天使")
    assert param == "action=search&searchtype=articlename&searchkey=邻家天使&t=0"
    paged = api.build_search_param("邻家天使", page=3)
    assert paged.endswith("&page=3")
    # 整串经 base64(UTF-8) 后必须能无损还原，证明没有双重编码
    import base64
    assert base64.b64decode(api.encode_request(paged)).decode("utf-8") == paged


def test_build_form_has_three_fields():
    form = api.build_form("action=search", timetoken_ms=123456789)
    assert form["appver"] == "1.13"
    assert form["timetoken"] == "123456789"
    assert form["request"] == api.encode_request("action=search")


def test_parse_search_xml_items():
    xml = (
        '<?xml version="1.0" encoding="utf-8"?><root>'
        '<item aid="2580">邻家的天使大人</item>'
        '<item aid="123">完全无关</item>'
        '<item aid="2580">重复项</item>'
        '</root>'
    )
    books = api.parse_search_xml(xml)
    assert len(books) == 2
    assert books[0]["id"] == "2580"
    assert books[0]["title"] == "邻家的天使大人"
    assert books[0]["source"] == "wenku8"


def test_parse_search_xml_empty_on_welcome():
    assert api.parse_search_xml("<html><body><h1>Welcome</h1></body></html>") == []


def test_search_via_app_api_fuzzy_match_and_relevance(monkeypatch):
    xml = (
        '<root><item aid="2580">邻家的天使大人</item>'
        '<item aid="999">别的书</item></root>'
    )

    class _Resp:
        text = xml

    class _Sess:
        def post(self, url, data=None, headers=None, timeout=None):
            assert url == api.APP_API_URL
            assert headers["User-Agent"] == api.ANDROID_UA
            assert "request" in data and "appver" in data and "timetoken" in data
            return _Resp()

    hits = api.search_via_app_api("邻家天使", session=_Sess())
    assert len(hits) == 1
    assert hits[0]["id"] == "2580"
    assert "_rel" not in hits[0]


def test_search_via_app_api_decodes_utf8_content(monkeypatch):
    """响应是 UTF-8 XML，即使 requests 把 resp.text 误判成乱码也要正确解码。"""
    xml = '<root><item aid="2580">邻家的天使大人</item></root>'

    class _Resp:
        content = xml.encode("utf-8")
        # 模拟 requests 对未声明 charset 的 XML 误用 ISO-8859-1 解码的乱码
        text = xml.encode("utf-8").decode("iso-8859-1")

    class _Sess:
        def post(self, *a, **k):
            return _Resp()

    hits = api.search_via_app_api("邻家天使", session=_Sess())
    assert len(hits) == 1
    assert hits[0]["title"] == "邻家的天使大人"


def test_search_via_app_api_blocked_returns_empty(monkeypatch):
    class _Resp:
        text = "<html><body><h1>Welcome</h1></body></html>"

    class _Sess:
        def post(self, *a, **k):
            return _Resp()

    assert api.search_via_app_api("邻家天使", session=_Sess()) == []
