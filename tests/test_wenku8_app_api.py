# -*- coding: utf-8 -*-
"""文库8官方 App API 通道协议单测（离线，全 mock）。

协议以 MewX 原作者源码为准（previous_versions/v0.5.2.1）：
searchkey 先 UTF-8 percent 编码，整体再 base64(UTF-8)；Android UA；
返回 XML 兼容 <item aid="id">书名</item> 与 <item aid='id'/> 两种格式。
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


def test_build_search_param_urlencodes_keyword():
    # 权威契约（MewX encodeToHttp = URLEncoder.encode(str, "UTF-8")）：
    # searchkey 必须先做 UTF-8 percent 编码，"妹" -> "%E5%A6%B9"。
    param = api.build_search_param("邻家天使")
    assert param.startswith("action=search&searchtype=articlename&searchkey=")
    assert "searchkey=%E9%82%BB%E5%AE%B6%E5%A4%A9%E4%BD%BF" in param
    assert param.endswith("&t=0")
    paged = api.build_search_param("邻家天使", page=3)
    assert "&page=3" in paged
    # 整串经 base64(UTF-8) 后可无损还原
    import base64
    assert base64.b64decode(api.encode_request(paged)).decode("utf-8") == paged


def test_encode_keyword_matches_mewx():
    # MewX 源码注释：encodeToHttp("妹") == "%E5%A6%B9"
    assert api.encode_keyword("妹") == "%E5%A6%B9"


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


def test_parse_search_xml_aid_only_format():
    """MewX 1.x 返回 <item aid='1699'/> 仅 aid 自闭合格式，也要能解析。"""
    xml = "<result><item aid='1699'/><item aid='1638'/><item aid='1699'/></result>"
    books = api.parse_search_xml(xml)
    assert len(books) == 2
    assert books[0]["id"] == "1699"
    assert books[0]["title"] == ""  # 仅 aid，无书名


def test_search_via_app_api_keeps_aid_only_results(monkeypatch):
    """仅 aid 的结果无法本地模糊匹配，信任服务端搜索结果直接保留。"""
    xml = "<result><item aid='1699'/><item aid='1638'/></result>"

    class _Resp:
        text = xml

    class _Sess:
        def post(self, *a, **k):
            return _Resp()

    hits = api.search_via_app_api("邻家天使", session=_Sess())
    assert len(hits) == 2
    assert {h["id"] for h in hits} == {"1699", "1638"}


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
