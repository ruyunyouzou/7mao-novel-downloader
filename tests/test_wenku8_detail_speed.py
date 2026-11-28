# -*- coding: utf-8 -*-
"""文库8详情页加载速度回归测试。

用户反馈「文库8书籍详情页加载很慢」。两个根因：
1. fetch_detail 字段缺失时调用已关停的 App API（android.php 返回 nginx
   Welcome），串行白等 meta(5s)+info(4s)=9s；改为读本地全站索引零网络补齐。
2. _on_detail_done 在 UI 线程同步调七猫 get_catalog 查文库8书号，必然失败
   还阻塞界面；文库8跳过。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def test_fetch_detail_missing_fields_uses_local_index_not_app_api(monkeypatch):
    """网页字段被拦而缺失时，必须用本地索引补齐，绝不调用已关停的 App API。"""
    from gui.wenku8_core import wenku8_reader as R

    # 模拟网页被拦：返回不含任何详情字段的空壳 HTML
    class _Resp:
        status_code = 200
        encoding = "gbk"
        text = "<html><head><title>轻小说文库</title></head><body>拦截</body></html>"

    monkeypatch.setattr(R, "_get_with_retry", lambda *a, **k: _Resp())

    # 本地索引能补出全部字段
    local = {
        "title": "测试书(别名)", "author": "测试作者",
        "intro": "本地简介", "cover": "https://img.wenku8.com/x.jpg",
        "tags": "奇幻,冒险", "length": "123456",
    }
    from gui.wenku8_core import wenku8_index as WX
    monkeypatch.setattr(WX, "search_index_book", lambda aid: local)
    monkeypatch.setattr(sys.modules["gui.wenku8_core.wenku8_reader"],
                        "make_session", lambda *a, **k: object(), raising=False)

    # 埋雷：只要碰到 App API 就失败，证明没有走它
    def _boom(*a, **k):
        raise AssertionError("不应再调用已关停的 App API")
    import gui.wenku8_core.wenku8_app_api as APP
    monkeypatch.setattr(APP, "fetch_meta", _boom)

    d = R.fetch_detail("12345", session=object())
    assert d["title"] == "测试书(别名)"
    assert d["author"] == "测试作者"
    assert d["intro"] == "本地简介"
    assert d["cover"] == "https://img.wenku8.com/x.jpg"
    assert d["words_num"] == "123456"


def test_fetch_detail_no_app_api_import_on_complete_fields(monkeypatch):
    """网页字段齐全时更不该触发任何补缺（连索引也不读）。"""
    from gui.wenku8_core import wenku8_reader as R

    html = (
        "<html><head><title>完整书 - 作者甲 - 轻小说文库</title></head><body>"
        "<td>内容简介：这是一段足够长的简介内容用于演示字段齐全的场景。</td>"
        "<td>全文长度：999,999字</td>"
        "</body></html>"
    )

    class _Resp:
        status_code = 200
        encoding = "gbk"

    _Resp.text = html
    monkeypatch.setattr(R, "_get_with_retry", lambda *a, **k: _Resp())

    from gui.wenku8_core import wenku8_index as WX
    def _boom(*a, **k):
        raise AssertionError("字段齐全时不应读索引")
    monkeypatch.setattr(WX, "search_index_book", _boom)

    d = R.fetch_detail("99999", session=object())
    assert d["title"] == "完整书"
    assert d["author"] == "作者甲"
    assert d["words_num"] == "999999"


def test_on_detail_done_wenku8_skips_qimao_catalog(monkeypatch):
    """文库8详情完成回调不得在 UI 线程同步调七猫 get_catalog。"""
    # 用一个极简替身模拟 MainWindow，只暴露被 _on_detail_done 用到的属性
    class _FakeWin:
        current_book = {"id": "2580", "source": "wenku8"}
        _detail_cache = None
        detail_text = type("T", (), {"setHtml": lambda self, h: None})()

        def _show_detail_cover(self, url, title):
            pass

        def _render_detail_html(self, info, catalog=None):
            return "ok"

    import gui.main_window as MW

    called = {"n": 0}
    def _boom(book_id):
        called["n"] += 1
        raise AssertionError("文库8不应调用七猫 get_catalog")
    monkeypatch.setattr(MW, "get_catalog", _boom, raising=False)
    # main_window 里是 `from .api import get_catalog`，需要 patch api 模块
    import gui.api as API
    monkeypatch.setattr(API, "get_catalog", _boom)

    win = _FakeWin()
    info = {"id": "2580", "title": "测试书", "source": "wenku8", "intro": "简介"}
    MW.MainWindow._on_detail_done(win, win.current_book, info)
    assert called["n"] == 0
    assert win._detail_cache["catalog"] is None
