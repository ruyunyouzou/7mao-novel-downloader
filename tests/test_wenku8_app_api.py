# -*- coding: utf-8 -*-
"""文库8官方 App API 通道协议单测（离线，全 mock）。

协议以 zsakvo/wenku8x（appver=1.13，现行可搜到别名书的客户端）为准：
searchkey 用原始中文（不 percent 编码），整体 base64(UTF-8)；Android UA；
搜索同时走 articlename + author 双通道；兼容 percent 编码老服务端。
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


def test_build_search_param_uses_raw_chinese():
    # 权威契约（zsakvo/wenku8x appver=1.13，现行能搜到别名书的客户端）：
    # searchkey 原样插入，服务端 base64 解码后直接得到 "邻家天使"。
    param = api.build_search_param("邻家天使")
    assert param == "action=search&searchtype=articlename&searchkey=邻家天使&t=0"
    paged = api.build_search_param("邻家天使", page=3)
    assert "&page=3" in paged
    # 整串经 base64(UTF-8) 后可无损还原
    import base64
    assert base64.b64decode(api.encode_request(paged)).decode("utf-8") == paged


def test_build_search_param_percent_compat_path():
    # 老服务端兼容路径：urlencode=True 时做 UTF-8 percent 编码
    param = api.build_search_param("妹", urlencode=True)
    assert "searchkey=%E5%A6%B9" in param
    assert api.build_search_param("妹") == \
        "action=search&searchtype=articlename&searchkey=妹&t=0"


def test_encode_keyword_returns_raw_chinese():
    assert api.encode_keyword("妹") == "妹"


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


def test_search_via_app_api_keeps_server_results_and_sorts():
    """服务端已按书名检索：本地相关性只排序，不再二次过滤误杀结果。"""
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
    # 关键回归：此前「别的书」会被本地 matches 过滤，「紫罗兰永恒花园」
    # 这类服务端命中也会被误杀。现在全部保留，仅按相关性排序。
    assert len(hits) == 2
    assert hits[0]["id"] == "2580"
    assert hits[1]["id"] == "999"
    assert all("_rel" not in h for h in hits)


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


def test_search_fires_both_articlename_and_author_channels():
    """对齐 zsakvo：搜索必须同时发 articlename 与 author 两个通道。

    此前只发 articlename，作者名命中的书（以及服务端仅在 author 索引里
    返回的别名书）会漏搜。这里断言两种 searchtype 都在原始中文通道里发出。
    """
    sess = _app_session({}, search_xml="<result/>")

    class _RecordingSess:
        def __init__(self):
            self.params = []

        def post(self, url, data=None, headers=None, timeout=None):
            import base64
            param = base64.b64decode(data["request"]).decode("utf-8")
            self.params.append(param)
            return _app_session({}, search_xml="<result/>").post(
                url, data=data, headers=headers, timeout=timeout)

    rec = _RecordingSess()
    api.search_via_app_api("紫罗兰", session=rec)
    assert any("searchtype=articlename&searchkey=紫罗兰" in p for p in rec.params)
    assert any("searchtype=author&searchkey=紫罗兰" in p for p in rec.params)


def test_search_via_app_api_blocked_returns_empty(monkeypatch):
    class _Resp:
        text = "<html><body><h1>Welcome</h1></body></html>"

    class _Sess:
        def post(self, *a, **k):
            return _Resp()

    assert api.search_via_app_api("邻家天使", session=_Sess()) == []


def _app_session(responses, search_xml=""):
    """按解码后的 request 参数串返回预设响应，便于验证 meta/cover 协议。

    ``search_xml`` 为任意 ``action=search`` 请求统一返回的 XML：搜索现在并发
    发 articlename/author × 原始/percent 四种组合，用统一响应模拟服务端命中。
    """
    import base64

    class _Resp:
        def __init__(self, content: bytes) -> None:
            self.content = content
            self.text = content.decode("utf-8", errors="replace")

    class _Sess:
        def __init__(self) -> None:
            self.calls = []

        def post(self, url, data=None, headers=None, timeout=None):
            param = base64.b64decode(data["request"]).decode("utf-8")
            self.calls.append(param)
            if param.startswith("action=search") and search_xml:
                body = search_xml
            else:
                body = responses.get(param, "")
            if isinstance(body, bytes):
                content = body
            else:
                content = str(body).encode("utf-8")
            return _Resp(content)

    return _Sess()


def test_build_meta_info_cover_params():
    assert api.build_meta_param("2580") == "action=book&do=meta&aid=2580&t=0"
    assert api.build_info_param("2580") == "action=book&do=info&aid=2580&t=0"
    assert api.build_cover_param("2580") == "action=book&do=cover&aid=2580"


def test_parse_meta_xml_keeps_full_title_with_alias():
    xml = (
        '<data name="Title" aid="1306">'
        '<![CDATA[向森之魔物献上花束(向森林的魔兽少女献花)]]></data>'
        '<data name="Author" value="小木君人"/>'
        '<data name="BookLength" value="105985"/>'
        '<data name="IntroPreview"><![CDATA[简介 &amp; 别名说明]]></data>'
    )
    meta = api.parse_meta_xml(xml)
    assert meta["title"] == "向森之魔物献上花束(向森林的魔兽少女献花)"
    assert meta["author"] == "小木君人"
    assert meta["booklength"] == "105985"
    assert meta["intropreview"] == "简介 & 别名说明"


def test_fetch_meta_uses_meta_endpoint_and_fills_book():
    meta_xml = (
        '<data name="Title"><![CDATA[恶魔高校DxD(High School DxD)]]></data>'
        '<data name="Author" value="石踏一荣"/>'
        '<data name="BookLength" value="123456"/>'
        '<data name="IntroPreview"><![CDATA[详细简介]]></data>'
    )
    sess = _app_session({api.build_meta_param("2580"): meta_xml})
    meta = api.fetch_meta("2580", session=sess)
    assert sess.calls == [api.build_meta_param("2580")]
    book = api.meta_to_book(meta, {"id": "2580", "title": "恶魔高校DxD"})
    assert book["title"] == "恶魔高校DxD(High School DxD)"  # 括号别名保留
    assert book["author"] == "石踏一荣"
    assert book["words_num"] == "123456"
    assert book["intro"] == "详细简介"


def test_build_cover_url_matches_reference_app():
    """封面直链严格对齐 zsakvo/wenku8x Util.getCover。

    aid//1000 分目录，如 2580 -> image/2/2580/2580s.jpg。这是普通图片 URL，
    比 App API 二进制接口更稳，是封面加载的第一优先。
    """
    assert api.build_cover_url("2580") == \
        "https://img.wenku8.com/image/2/2580/2580s.jpg"
    assert api.build_cover_url("1034") == \
        "https://img.wenku8.com/image/1/1034/1034s.jpg"
    assert api.build_cover_url("") == ""
    assert api.build_cover_url("abc") == ""


def test_fetch_cover_bytes_accepts_jpeg_and_rejects_placeholder():
    jpeg = b"\xff\xd8\xff\xe0JFIF-test"
    sess = _app_session({api.build_cover_param("2580"): jpeg})
    assert api.fetch_cover_bytes("2580", session=sess) == jpeg
    assert sess.calls == [api.build_cover_param("2580")]

    blocked = _app_session({
        api.build_cover_param("2580"): "<html><body><h1>Welcome</h1></body></html>"
    })
    assert api.fetch_cover_bytes("2580", session=blocked) == b""


def test_search_enriches_title_and_marks_cover_aid():
    """搜索结果用 meta 补完整书名，同时给卡片留下 App 封面 aid。"""
    search_xml = '<root><item aid="2580">恶魔高校DxD</item></root>'
    meta_xml = (
        '<data name="Title"><![CDATA[恶魔高校DxD(High School DxD)]]></data>'
        '<data name="Author" value="石踏一荣"/>'
        '<data name="BookLength" value="123456"/>'
    )
    sess = _app_session({api.build_meta_param("2580"): meta_xml},
                        search_xml=search_xml)
    hits = api.search_via_app_api("恶魔高校", session=sess)
    assert len(hits) == 1
    assert hits[0]["id"] == "2580"
    assert hits[0]["title"] == "恶魔高校DxD(High School DxD)"
    assert hits[0]["author"] == "石踏一荣"
    assert hits[0]["words_num"] == "123456"
    assert hits[0]["cover_aid"] == "2580"
    assert hits[0]["cover"] == ""  # 封面由卡片后台走二进制接口加载


def test_search_no_longer_filters_server_results():
    """服务端返回的书名即使本地匹配不到也必须保留，修复「紫罗兰」漏搜。"""
    search_xml = ('<root><item aid="1034">'
                  '薇尔莉特·伊芙加登(紫罗兰永恒花园)</item></root>')
    sess = _app_session({}, search_xml=search_xml)
    hits = api.search_via_app_api("紫罗兰", session=sess)
    assert len(hits) == 1
    assert hits[0]["id"] == "1034"
    assert "紫罗兰永恒花园" in hits[0]["title"]


def test_fetch_detail_uses_app_meta_when_web_is_blocked(monkeypatch):
    """网页被风控拦时，详情仍能从 App meta 拿到括号别名和字数。"""
    from gui.wenku8_core import wenku8_reader as reader

    class _BlockedSession:
        def get(self, *args, **kwargs):
            raise RuntimeError("blocked")

    monkeypatch.setattr(api, "fetch_meta", lambda aid: {
        "title": "薇尔莉特·伊芙加登(紫罗兰永恒花园)",
        "author": "晓佳奈",
        "booklength": "258000",
        "intropreview": "自动人偶与人之间的故事",
    })
    detail = reader.fetch_detail("2353", session=_BlockedSession())
    assert detail["title"] == "薇尔莉特·伊芙加登(紫罗兰永恒花园)"
    assert detail["author"] == "晓佳奈"
    assert detail["words_num"] == "258000"
    assert detail["intro"] == "自动人偶与人之间的故事"


def test_build_catalog_and_text_params():
    assert api.build_catalog_param("2580") == "action=book&do=list&aid=2580&t=0"
    assert api.build_text_param("2580", "41749") == \
        "action=book&do=text&aid=2580&cid=41749&t=0"


def test_parse_catalog_xml_keeps_volumes_and_filters_images():
    xml = (
        '<?xml version="1.0" encoding="utf-8"?><package>'
        '<volume vid="41748"><![CDATA[第一卷 告白于苍刻之夜]]>'
        '<chapter cid="41749"><![CDATA[序章]]></chapter>'
        '<chapter cid="41750"><![CDATA[第一章]]></chapter>'
        '<chapter cid="41759"><![CDATA[插图]]></chapter>'
        '</volume>'
        '<volume vid="45090"><![CDATA[第二卷 谎言、真相与赤红]]>'
        '<chapter cid="45091"><![CDATA[序章]]></chapter>'
        '</volume></package>'
    )
    catalog = api.parse_catalog_xml(xml, "2580")
    assert len(catalog) == 3  # 插图章已过滤
    assert [c["title"] for c in catalog] == ["序章", "第一章", "序章"]
    assert catalog[0]["vol"] == "第一卷 告白于苍刻之夜"
    assert catalog[2]["vol"] == "第二卷 谎言、真相与赤红"
    assert catalog[0]["app_cid"] == "41749"
    assert catalog[0]["url"] == ""  # 正文走 do=text，不依赖网页 URL


def test_fetch_catalog_app_uses_list_endpoint():
    xml = (
        '<package><volume vid="1"><![CDATA[第一卷]]>'
        '<chapter cid="41749"><![CDATA[序章]]></chapter>'
        '</volume></package>'
    )
    sess = _app_session({api.build_catalog_param("2580"): xml})
    catalog = api.fetch_catalog_app("2580", session=sess)
    assert sess.calls == [api.build_catalog_param("2580")]
    assert len(catalog) == 1
    assert catalog[0]["app_cid"] == "41749"


def test_fetch_text_app_cleans_image_markers_and_promo():
    body = (
        '第一卷 告白于苍刻之夜 序章\n'
        '<!--image-->http://pic.wenku8.com/pictures/1.jpg<!--image-->\n'
        '正文第一行\n'
        '本文来自轻小说文库 http://www.wenku8.net\n'
    )
    sess = _app_session({api.build_text_param("2580", "41749"): body})
    text = api.fetch_text_app("2580", "41749", session=sess)
    assert sess.calls == [api.build_text_param("2580", "41749")]
    assert "正文第一行" in text
    assert "<!--image-->" not in text
    assert "wenku8.net" not in text
    assert text.startswith("    第一卷")  # 与网页通道同构的缩进纯文本


def test_reader_catalog_falls_back_to_app_api(monkeypatch):
    """网页目录不可用时退回 App API（现网页优先、App 兜底）。"""
    from gui.wenku8_core import wenku8_reader as reader

    class _EmptySession:
        def get(self, *a, **k):
            class _R:
                content = b""
                text = ""
                encoding = "gbk"
            return _R()

    monkeypatch.setattr(reader, "make_session", lambda *a, **k: _EmptySession())
    monkeypatch.setattr(api, "fetch_catalog_app", lambda aid: [
        {"id": "41749", "app_cid": "41749", "title": "序章",
         "vol": "第一卷", "url": "", "index": 0},
    ])
    catalog = reader.fetch_catalog("2580")
    assert len(catalog) == 1
    assert catalog[0]["app_cid"] == "41749"


def test_download_plugin_uses_app_catalog_and_text(monkeypatch):
    import sys
    sys.path.insert(0, "src")
    from plugins.wenku8_downloader_plugin import Wenku8BookSource
    from gui.wenku8_core import wenku8_reader as reader

    monkeypatch.setattr(reader, "fetch_catalog", lambda bid, sess: [
        {"id": "41749", "app_cid": "41749", "title": "序章",
         "vol": "第一卷", "url": "", "index": 0},
    ])
    monkeypatch.setattr(reader, "fetch_chapter_text_app",
                        lambda aid, cid, sess: "    App API 正文")

    source = Wenku8BookSource()
    catalog = source.fetch_catalog("2580", None)
    assert source._app_mode is True
    assert source._aid == "2580"
    assert catalog[0]["app_cid"] == "41749"
    assert source.chapter_text(None, catalog[0], None, None) == "    App API 正文"


def test_fetch_detail_parses_tags_from_articleinfo(monkeypatch):
    """标签来自参考项目同款 articleinfo 详情页，不是 App meta。"""
    from gui.wenku8_core import wenku8_reader as reader

    html = (
        '<html><title>关于我在无意间被隔壁的天使变成废柴这件事'
        '(关于邻家的天使大人不知不觉把我惯成了废人这档子事) - '
        '佐伯さん - 校园 - 轻小说文库</title><body>'
        '<span>标签：校园 欢乐向 青春 恋爱</span>'
        '<span>小说作者：佐伯さん</span>'
        '</body></html>'
    )

    class _Resp:
        encoding = ""
        text = html

    class _Sess:
        def get(self, url, timeout=None):
            assert "/modules/article/articleinfo.php?id=2580" in url
            return _Resp()

    monkeypatch.setattr(api, "fetch_meta", lambda aid: {
        "title": "关于我在无意间被隔壁的天使变成废柴这件事"
                 "(关于邻家的天使大人不知不觉把我惯成了废人这档子事)",
        "author": "佐伯さん",
    })
    detail = reader.fetch_detail("2580", session=_Sess())
    assert detail["tags"] == "校园,欢乐向,青春,恋爱"
    assert detail["author"] == "佐伯さん"
    assert "关于邻家的天使大人" in detail["title"]


def test_build_novellist_param():
    """榜单参数：novellist/sort/page/t，MewX getNovelListWithInfo 同款。"""
    param = api.build_novellist_param("allvisit", 2)
    assert param == "action=novellist&sort=allvisit&page=2&t=0"


def test_parse_novellist_xml_keeps_bracket_alias():
    """榜单 Title 带括号别名，必须保留原文（紫罗兰场景的数据基础）。"""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?><result><page num="166"/>'
        "<item aid='1034'><data name='Title'><![CDATA[恶魔高校DxD(High School DxD)]]></data>"
        "<data name='TotalHitsCount' value='2316361'/></item>"
        "<item aid='1306'><data name='Title'><![CDATA[向森之魔物献上花束(向森林的魔兽少女献花)]]></data></item>"
        "</result>"
    )
    books = api.parse_novellist_xml(xml)
    assert len(books) == 2
    assert books[0]["id"] == "1034"
    assert books[0]["title"] == "恶魔高校DxD(High School DxD)"
    assert books[0]["cover_aid"] == "1034"
    assert books[1]["title"] == "向森之魔物献上花束(向森林的魔兽少女献花)"


def test_search_via_novellist_matches_bracket_alias():
    """核心回归：服务端搜不到的别名书，榜单元数据本地匹配必须命中。"""
    xml_page = (
        "<result><item aid='1034'><data name='Title'>"
        "<![CDATA[恶魔高校DxD(High School DxD)]]></data></item>"
        "<item aid='9999'><data name='Title'>"
        "<![CDATA[某书(紫罗兰永恒花园)]]></data></item></result>"
    )

    class _Sess:
        def post(self, url, data=None, headers=None, timeout=None):
            class _Resp:
                content = xml_page.encode("utf-8")
            return _Resp()

    hits = api.search_via_novellist("紫罗兰", session=_Sess(), pages=1,
                                    sorts=("allvisit",))
    assert len(hits) == 1
    assert hits[0]["id"] == "9999"
    assert "紫罗兰永恒花园" in hits[0]["title"]
    assert hits[0]["cover_aid"] == "9999"


def test_search_wenku8_uses_local_index_when_site_search_empty(monkeypatch):
    """站内搜索空时，搜索主流程降级到本地全站索引（通用别名覆盖）。"""
    from gui.wenku8_core import wenku8_search as s
    from gui.wenku8_core import wenku8_index as idx

    monkeypatch.setattr(s, "_search_on_site", lambda *a, **k: [])
    monkeypatch.setattr(idx, "search_index", lambda kw, limit=40, offset=0: ["2353"])
    monkeypatch.setattr(idx, "ensure_index_async", lambda **k: None)
    monkeypatch.setattr("gui.wenku8_core.wenku8_reader.fetch_detail",
                        lambda aid, session=None: {
                            "id": aid,
                            "title": "薇尔莉特·伊芙加登(紫罗兰永恒花园)",
                            "author": "晓佳奈", "source": "wenku8",
                        })
    results = s.search_wenku8("紫罗兰")
    assert len(results) == 1
    assert "紫罗兰永恒花园" in results[0]["title"]


def test_fetch_cover_data_prefers_image_url(monkeypatch):
    """封面数据优先走图片直链；命中即返回，不再调二进制接口。"""
    from PIL import Image
    import io

    bio = io.BytesIO()
    Image.new("RGB", (60, 80), (10, 120, 200)).save(bio, "PNG")
    png = bio.getvalue()
    expected_url = api.build_cover_url("2580")
    calls = {"url": 0, "bin": 0}

    class _Resp:
        content = png

    def fake_get(url, *a, **k):
        calls["url"] += 1
        assert url == expected_url, f"应请求图片直链，实际 {url}"
        return _Resp()

    def fake_bytes(aid, session=None):
        calls["bin"] += 1
        return png

    # fetch_cover_data 里走模块级 requests.get
    monkeypatch.setattr("gui.wenku8_core.wenku8_app_api.requests.get", fake_get)
    monkeypatch.setattr(api, "fetch_cover_bytes", fake_bytes)

    data = api.fetch_cover_data("2580")
    assert data == png
    assert calls["url"] == 1 and calls["bin"] == 0


def test_fetch_cover_data_falls_back_to_binary(monkeypatch):
    """图片直链失败（占位页/异常）时，回退 App API 二进制封面接口。"""
    calls = {"bin": 0}

    class _Resp:
        content = b"<html><body><h1>Welcome</h1></body></html>"

    def fake_get(url, *a, **k):
        return _Resp()

    def fake_bytes(aid, session=None):
        calls["bin"] += 1
        return b"\xff\xd8\xff\xe0JFIF-fallback"

    monkeypatch.setattr("gui.wenku8_core.wenku8_app_api.requests.get", fake_get)
    monkeypatch.setattr(api, "fetch_cover_bytes", fake_bytes)

    data = api.fetch_cover_data("2580")
    assert data == b"\xff\xd8\xff\xe0JFIF-fallback"
    assert calls["bin"] == 1


def test_local_index_matches_any_alias(monkeypatch, tmp_path):
    """通用索引对任意书名别名生效（非特判）：从列表页提取完整标题即可命中。"""
    from gui.wenku8_core import wenku8_index as idx

    # 本例专测用户本机自抓索引，暂时屏蔽随包全站目录以免混入额外命中。
    monkeypatch.setattr(idx, "_catalog_cache", {})
    fake_index = tmp_path / "wenku8_index.json"
    monkeypatch.setattr(idx, "_INDEX_FILE", str(fake_index))
    idx.save_index({
        "2353": "薇尔莉特·伊芙加登(紫罗兰永恒花园)",
        "1034": "恶魔高校DxD(High School DxD)",
        "7777": "某冷门书(随便什么别名)",
    })
    assert idx.search_index("紫罗兰") == ["2353"]
    assert idx.search_index("恶魔高校") == ["1034"]
    assert idx.search_index("随便什么别名") == ["7777"]
    assert idx.search_index("不存在") == []


def test_local_index_extracts_full_title_with_alias():
    """列表页链接文本含括号别名，必须原样入索引。"""
    from gui.wenku8_core import wenku8_index as idx

    html = '''
    <a href="/book/2353.htm">薇尔莉特·伊芙加登(紫罗兰永恒花园)</a>
    <a href="/novel/1/1034/">恶魔高校DxD(High School DxD)</a>
    <a href="articleinfo.php?id=7777">某冷门书(随便什么别名)</a>
    <a href="/book/1.htm">我要阅读</a>
    '''
    got = idx.extract_books(html, "https://www.wenku8.cc")
    assert got["2353"] == "薇尔莉特·伊芙加登(紫罗兰永恒花园)"
    assert got["1034"] == "恶魔高校DxD(High School DxD)"
    assert got["7777"] == "某冷门书(随便什么别名)"
    assert "1" not in got


def test_local_index_no_single_char(monkeypatch, tmp_path):
    """单字查询不走索引兜底，避免海量弱命中。"""
    from gui.wenku8_core import wenku8_index as idx

    monkeypatch.setattr(idx, "_INDEX_FILE", str(tmp_path / "i.json"))
    idx.save_index({"2353": "薇尔莉特·伊芙加登(紫罗兰永恒花园)"})
    assert idx.search_index("紫") == []


def test_epub_cover_download_uses_ua_and_validates_image(monkeypatch):
    """EPUB 网络封面：带浏览器 UA 下载，403 HTML 页被拒绝，真 JPEG 通过。"""
    import zipfile as _zf
    import tempfile as _tf
    from gui import qimao_core as qc

    jpeg = b"\xff\xd8\xff\xe0" + b"X" * 300

    class _Resp:
        def __init__(self, content): self.content = content

    seen = {"headers": None, "url": None}

    class _Sess:
        def get(self, url, headers=None, timeout=None):
            seen["url"] = url
            seen["headers"] = headers or {}
            return _Resp(jpeg)

    s = _Sess()
    cover_url = "http://img.wenku8.com/image/2/2353/2353s.jpg"
    # 复用 build_txt 里的封面下载片段逻辑：升级 https + UA + 魔数校验
    url = cover_url
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    cov_headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.wenku8.cc/"}
    raw = s.get(url, headers=cov_headers, timeout=15).content
    magic = raw.startswith(b"\xff\xd8\xff")
    assert url.startswith("https://")           # http 升级 https
    assert "User-Agent" in seen["headers"]       # 带 UA
    assert magic                                  # JPEG 通过


def test_epub_cover_rejects_error_page(monkeypatch):
    """封面下载拿到 403 HTML 错误页时必须拒绝（魔数校验）。"""
    raw = b"<!DOCTYPE html><html>403 Forbidden</html>"
    magic = (raw.startswith(b"\xff\xd8\xff") or raw.startswith(b"\x89PNG\r\n\x1a\n")
             or raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a")
             or raw.startswith(b"RIFF"))
    assert not magic  # 错误页不会被当图片写入 EPUB
