# -*- coding: utf-8 -*-
"""文库8下载开局提速回归测试（#130）。

用户反馈：天使书 311 章最终 100% 成功，但耗时 1149.8s。慢网络上开局
详情页/目录页重复请求 + 单章重试风暴（4 次内层 × 4 轮外层 = 16 次请求）
是主要浪费。本测试锁定三条零网络契约 + 重试上界：

1. 本地索引字段齐全时 fetch_detail 零网络；
2. 索引 cover 带分类号时 fetch_book_meta 不再请求 /book/{id}.htm；
3. 索引分类号可用时 fetch_catalog 不再先请求详情页；
4. rescue 轮内层 attempts 降为 2，单章请求上界 10 次。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gui.wenku8_core import wenku8_index as WX  # noqa: E402
from gui.wenku8_core import wenku8_reader as R  # noqa: E402


_FULL_LOCAL = {
    "title": "测试书(别名)",
    "author": "测试作者",
    "cover": "https://img.wenku8.com/image/3/99999/99999s.jpg",
    "intro": "本地简介",
    "tags": "校园 欢乐向 青春",
    "length": "1705K",
}


class _NoNetworkSession:
    """任何 GET 都算失败：用来证明被测路径零网络。"""

    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        raise AssertionError(f"索引齐全时不应发网络请求：{url}")


def test_fetch_detail_zero_network_when_index_complete(monkeypatch):
    monkeypatch.setattr(WX, "search_index_book", lambda aid: dict(_FULL_LOCAL))
    sess = _NoNetworkSession()
    d = R.fetch_detail("99999", session=sess)
    assert d["title"] == "测试书(别名)"
    assert d["author"] == "测试作者"
    assert d["intro"] == "本地简介"
    assert d["words_num"] == "1705K"
    # 索引里空格分隔 -> 与网页同款逗号分隔，UI/筛选格式一致
    assert d["tags"] == "校园,欢乐向,青春"
    assert sess.calls == []


def test_fetch_book_meta_uses_index_prefix_without_detail_request(monkeypatch):
    monkeypatch.setattr(WX, "search_index_book", lambda aid: dict(_FULL_LOCAL))
    sess = _NoNetworkSession()
    meta = R.fetch_book_meta("99999", session=sess)
    assert meta["novel_prefix"] == "/novel/3/99999/"
    assert meta["book_id"] == "99999"
    assert sess.calls == []


def test_fetch_catalog_skips_detail_page_when_index_has_class(monkeypatch):
    """索引给出真实分类号时第一个请求就是目录页，不先抓 /book/ 详情。"""
    good = (
        '<html><body><table><tr><td class="ccss">'
        '<a href="86807.htm">第一章</a>'
        '</td></tr></table></body></html>'
    )

    class Resp:
        def __init__(self, text, status=200):
            self.text = text
            self.status_code = status
            self.encoding = ""

    calls = []

    class Sess:
        def get(self, url, timeout=None):
            calls.append(url)
            if "/novel/3/99999/index.htm" in url:
                return Resp(good)
            return Resp("<html>404</html>", 404)

    monkeypatch.setattr(WX, "search_index_book", lambda aid: dict(_FULL_LOCAL))
    monkeypatch.setattr(R, "_base", lambda: "https://www.wenku8.cc")
    cat = R.fetch_catalog("99999", session=Sess())
    assert len(cat) == 1
    assert cat[0]["url"].startswith("https://www.wenku8.cc/novel/3/99999/")
    assert calls and calls[0].endswith("/novel/3/99999/index.htm"), calls[:3]
    assert not any("/book/99999.htm" in u for u in calls), calls[:3]


def test_rescue_rounds_use_bounded_inner_attempts():
    """rescue 轮必须显式降 attempts=2，杜绝单章 16 次请求风暴。"""
    import inspect
    import plugins.wenku8_downloader_plugin as P

    src = inspect.getsource(P.Wenku8BookSource.prefetch_texts)
    assert "_grab(failed, pool2, attempts=2)" in src
    assert "attempts=4" in src or "attempts=4)" in src
    # 不允许 rescue 轮重新使用默认 4 次
    assert "_grab(failed, pool2)" not in src


def test_chapter_text_accepts_attempts_override(monkeypatch):
    """单章抓取把 attempts 透传给 _get_with_retry，供 rescue 轮降档。"""
    seen = {}

    class Resp:
        status_code = 200
        encoding = "gbk"
        text = "<html><div id='content'>正文</div></html>"

    def fake_retry(sess, url, timeout=20, attempts=3, validate=None):
        seen["attempts"] = attempts
        seen["url"] = url
        return Resp()

    monkeypatch.setattr(R, "_get_with_retry", fake_retry)
    text = R.fetch_chapter_text(
        "https://www.wenku8.cc/novel/2/99999/123.htm",
        session=object(), attempts=2,
    )
    assert "正文" in text
    assert seen["attempts"] == 2
