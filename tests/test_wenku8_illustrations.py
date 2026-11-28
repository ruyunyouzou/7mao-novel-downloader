# -*- coding: utf-8 -*-
"""文库8插图回归测试。

用户反馈：文库有插图，但下载的 EPUB 里没有。
根因两层：目录解析主动过滤了「插图」章；正文抓取只取 get_text() 丢掉 <img>。
本测试锁死四层：目录保留插图章、正文提取图片标记、EPUB 内嵌、TXT 退化为链接。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from gui.wenku8_core import wenku8_reader as R  # noqa: E402
from gui.wenku8_core import wenku8_volumes as V  # noqa: E402
from gui import qimao_core as C  # noqa: E402


# ---------- 目录解析：插图章必须保留 ----------

def test_catalog_keeps_illustration_chapter():
    html = """
    <table>
      <tr><td class="vcss">第一卷</td></tr>
      <tr><td><a href="177990.htm">第一章 初遇</a></td></tr>
      <tr><td><a href="177991.htm">插图</a></td></tr>
      <tr><td><a href="177992.htm">第二章 约定</a></td></tr>
    </table>
    """
    cats = R._parse_catalog_html(html, "https://www.wenku8.cc/novel/2/1/index.htm")
    assert len(cats) == 3, "插图章不能再被过滤"
    assert cats[1]["title"] == "插图"
    assert cats[1]["is_illustration"] is True
    assert cats[0]["is_illustration"] is False
    # index 连续，正文抓取/切片不受影响
    assert [c["index"] for c in cats] == [0, 1, 2]


def test_catalog_flat_fallback_keeps_illustration():
    html = """
    <div>
      <a href="11.htm">插图一</a>
      <a href="12.htm">第一章</a>
    </div>
    """
    cats = R._parse_catalog_html(html, "https://www.wenku8.cc/novel/1/1/index.htm")
    assert len(cats) == 2
    assert any(c["is_illustration"] for c in cats)


# ---------- 正文抓取：提取 <img> 为标记 ----------

def test_chapter_text_extracts_images(monkeypatch):
    html = """<html><body><div id="content">
      <p>正文第一行</p>
      <img src="/images/1.jpg"/>
      <img src="https://img.wenku8.com/images/2.png"/>
      <p>本文来自 Wenku8.Com 轻小说文库</p>
    </div></body></html>"""

    class _Resp:
        content = html.encode("gbk")  # 文库8真实页面是 GBK
        encoding = None

        @property
        def text(self):
            return self.content.decode(self.encoding or "gbk", "replace")

    monkeypatch.setattr(R, "_get_with_retry", lambda *a, **k: _Resp())

    text = R.fetch_chapter_text(
        "https://www.wenku8.cc/novel/2/1/177990.htm", session=object())
    urls = V.extract_image_urls(text)
    assert len(urls) == 2
    assert any(u.endswith("1.jpg") and u.startswith("https://") for u in urls)
    assert any(u.endswith("2.png") for u in urls)
    assert "正文第一行" in text
    assert "本文来自" not in text  # 推广行被过滤（图片 URL 里的域名不算）


# ---------- 图片工具 ----------

def test_image_bytes_magic():
    assert V.is_image_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF")
    assert V.is_image_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 10)
    assert not V.is_image_bytes(b"<html>403 Forbidden</html>")
    assert not V.is_image_bytes(b"")


def test_extract_image_urls_dedup():
    text = ("[[WENKU8IMG:https://a/x.jpg]]正文"
            "[[WENKU8IMG:https://a/x.jpg]]"
            "[[WENKU8IMG:https://a/y.png]]")
    assert V.extract_image_urls(text) == [
        "https://a/x.jpg", "https://a/y.png"]


# ---------- TXT 退化 ----------

def test_txt_images_become_links():
    text = "正文\n[[WENKU8IMG:https://img.wenku8.com/a.jpg]]"
    out = C._txt_images(text)
    assert "[[WENKU8IMG:" not in out
    assert "[插图: https://img.wenku8.com/a.jpg]" in out


# ---------- EPUB 内嵌 ----------

class _FakeSource:
    def __init__(self, with_images=True):
        self.with_images = with_images

    def get_image(self, url):
        return b"\xff\xd8\xff\xe0\x00\x10JFIF" if self.with_images else b""


def test_render_body_embeds_images():
    importebook = None
    try:
        from ebooklib import epub
        importebook = epub
    except Exception:  # noqa: BLE001
        pass
    if importebook is None:
        return  # 环境无 ebooklib 时跳过内嵌验证

    book = importebook.EpubBook()
    counter = [0]
    fails = []
    raw = ("正文\n[[WENKU8IMG:https://img.wenku8.com/a.jpg]]"
           "[[WENKU8IMG:https://img.wenku8.com/b.png]]")
    html_out = C._render_body(raw, _FakeSource(), book, importebook,
                              False, counter, fails)
    assert "<img" in html_out and 'src="Images/w8img_0001.jpg"' in html_out
    assert 'src="Images/w8img_0002.jpg"' in html_out
    assert counter[0] == 2 and not fails
    assert len(list(book.get_items())) >= 2  # 两张图片作为 item 加入


def test_render_body_without_image_support_unchanged():
    """非文库8书源：正文里有标记也不处理（番茄七猫正文本就没有标记）。"""
    counter = [0]
    fails = []
    out = C._render_body("普通正文", object(), None, None, False, counter, fails)
    assert out == "普通正文"
    assert counter[0] == 0


def test_failed_image_raises_in_strict_mode():
    """文库8严格模式：缺图必须抛错，图和书一起成功，绝不静默出缺图书。"""
    import pytest
    from ebooklib import epub

    class _StrictSource(_FakeSource):
        source_key = "wenku8"

    book = epub.EpubBook()
    counter = [0]
    fails = []
    with pytest.raises(C.QimaoError):
        C._render_body(
            "[[WENKU8IMG:https://img.wenku8.com/missing.jpg]]",
            _StrictSource(with_images=False), book, epub, False, counter, fails,
        )


def test_failed_image_non_wenku8_unchanged():
    """非文库8书源：缺图仍走老的兜底（不影响番茄/七猫）。"""
    from ebooklib import epub
    book = epub.EpubBook()
    counter = [0]
    fails = []
    out = C._render_body(
        "[[WENKU8IMG:https://img.wenku8.com/missing.jpg]]",
        _FakeSource(with_images=False), book, epub, False, counter, fails,
    )
    assert fails == ["https://img.wenku8.com/missing.jpg"]
    assert "<img" not in out


# ---------- 图片下载：多轮重试尽力全下 + 缺图判失败（图和书一起成功） ----------

def test_download_image_retries_until_success(monkeypatch):
    """单张图首两次失败、第三次成功：必须重试到成功，不静默放弃。"""
    import gui.wenku8_core.wenku8_volumes as V

    calls = {"n": 0}

    class _Resp:
        def __init__(self, ok):
            self.content = (b"\xff\xd8\xff\xe0JFIF" if ok
                            else b"<html>403</html>")

    def fake_get(url, timeout=None, headers=None):
        calls["n"] += 1
        return _Resp(calls["n"] >= 3)

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(V.time, "sleep", lambda *_: None) if hasattr(V, "time") else None
    # 模块内是 `import time as _time`，patch 其 sleep 加速
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    data = V.download_image("https://img.wenku8.com/a.jpg", attempts=5)
    assert data.startswith(b"\xff\xd8\xff")
    assert calls["n"] == 3, f"应重试到第 3 次成功，实际 {calls['n']} 次"


def test_download_image_all_fail_returns_empty(monkeypatch):
    """全部失败才返回空，由上层判失败。"""
    import gui.wenku8_core.wenku8_volumes as V
    import requests

    def fake_get(url, timeout=None, headers=None):
        class _R:
            content = b"<html>403 Forbidden</html>"
        return _R()

    monkeypatch.setattr(requests, "get", fake_get)
    # 指纹层兜底也要 mock 掉，避免测试走真实网络（并断言它确实被调用）
    called = {"cloak": 0}

    def fake_cloak(url, timeout=15, referer=""):
        called["cloak"] += 1
        return b""

    monkeypatch.setattr(V, "_download_image_cloak", fake_cloak)
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    assert V.download_image("https://img.wenku8.com/a.jpg", attempts=3) == b""
    assert called["cloak"] == 1, "requests 全失败后应触发一次指纹层兜底"


def test_missing_images_detects_undownloaded():
    """missing_images 必须准确报出没下下来的图。"""
    from plugins.wenku8_downloader_plugin import Wenku8BookSource
    s = Wenku8BookSource()
    s._image_cache["https://img.wenku8.com/a.jpg"] = b"\xff\xd8\xff"
    texts = [
        "正文[[WENKU8IMG:https://img.wenku8.com/a.jpg]]",
        "正文[[WENKU8IMG:https://img.wenku8.com/b.jpg]]",
        "正文[[WENKU8IMG:https://img.wenku8.com/c.jpg]]",
    ]
    missing = s.missing_images(texts)
    assert missing == ["https://img.wenku8.com/b.jpg", "https://img.wenku8.com/c.jpg"]


def test_cache_images_retries_and_records_missing(monkeypatch):
    """逐章下载里的多轮补抓：首轮失败次轮成功；仍失败要记入 _image_missing。"""
    from plugins.wenku8_downloader_plugin import Wenku8BookSource
    s = Wenku8BookSource()

    import gui.wenku8_core.wenku8_volumes as V
    calls = {"a": 0, "b": 0}

    def fake_dl(url, timeout=15):
        key = "a" if url.endswith("a.jpg") else "b"
        calls[key] += 1
        if key == "a":
            return b"\xff\xd8\xff" if calls[key] >= 2 else b""  # 第2轮成功
        return b""  # b 永远失败

    monkeypatch.setattr(V, "download_image", fake_dl)
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    s._cache_images("[[WENKU8IMG:https://x/a.jpg]][[WENKU8IMG:https://x/b.jpg]]", rounds=3)
    assert s._image_cache.get("https://x/a.jpg")  # 补抓成功
    assert "https://x/b.jpg" in s.missing_images()  # 记入失败


def test_txt_writer_writes_images_to_disk(tmp_path):
    """TXT 插图必须真实落地到本地文件，不退化成网络链接文字。"""
    from gui.qimao_core import _ImageWriter, _txt_images, QimaoError
    import pytest

    class _Src:
        source_key = "wenku8"

        def get_image(self, url):
            return b"\xff\xd8\xff\xe0JFIFDATA"

    imgdir = str(tmp_path / "imgs")
    w = _ImageWriter(_Src(), imgdir)
    text = "正文[[WENKU8IMG:https://img.wenku8.com/a.jpg]]结尾"
    out = _txt_images(text, w)
    assert "[插图: 插图_0001.jpg]" in out
    assert "https://" not in out
    assert (tmp_path / "imgs" / "插图_0001.jpg").exists(), "图片必须真实写入磁盘"


def test_txt_writer_missing_image_raises(tmp_path):
    """TXT 严格模式：缺图必须抛错，图和书一起成功，不留占位冒充成功。"""
    import pytest
    from gui.qimao_core import _ImageWriter, _txt_images, QimaoError

    class _Src:
        def get_image(self, url):
            return b""  # 缺图

    w = _ImageWriter(_Src(), str(tmp_path / "imgs"))
    with pytest.raises(QimaoError):
        _txt_images("[[WENKU8IMG:https://x/a.jpg]]", w)


# ---------- 卷封面：取插图章第一张竖图 ----------

def _make_image_bytes(width, height, fmt="JPEG"):
    import io
    from PIL import Image
    img = Image.new("RGB", (width, height), (200, 120, 90))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def test_is_portrait():
    from gui.wenku8_core.wenku8_volumes import is_portrait
    portrait = _make_image_bytes(600, 900)   # 竖版卷封面
    landscape = _make_image_bytes(900, 600)  # 横版插图
    assert is_portrait(portrait) is True
    assert is_portrait(landscape) is False
    assert is_portrait(b"") is False
    assert is_portrait(b"<html>403</html>") is False


def test_pick_cover_bytes_prefers_first_portrait():
    """第一张横图跳过，第二张竖图作为卷封面（参考 Yorag 项目）。"""
    from gui.wenku8_core.wenku8_volumes import pick_cover_bytes
    landscape = _make_image_bytes(900, 600)
    portrait = _make_image_bytes(600, 900)
    portrait2 = _make_image_bytes(500, 800)
    cache = {
        "https://x/landscape.jpg": landscape,
        "https://x/portrait.jpg": portrait,
        "https://x/portrait2.jpg": portrait2,
    }
    picked = pick_cover_bytes(list(cache), lambda u: cache.get(u, b""))
    assert picked == portrait  # 第一张竖图，不是横图、也不是后面的竖图


def test_pick_cover_bytes_falls_back_first_image():
    """没有竖图时退回第一张成功下载的图片，全失败返回空。"""
    from gui.wenku8_core.wenku8_volumes import pick_cover_bytes
    a = _make_image_bytes(900, 600)
    b = _make_image_bytes(800, 500)
    cache = {"u1": a, "u2": b}
    assert pick_cover_bytes(["u1", "u2"], lambda u: cache.get(u, b"")) == a
    assert pick_cover_bytes(["x", "y"], lambda u: b"") == b""


def test_volume_cover_from_illustration_chapter():
    """书源 volume_cover：只从插图章取图，正文章里的图不算卷封面。"""
    from plugins.wenku8_downloader_plugin import Wenku8BookSource
    portrait = _make_image_bytes(600, 900)
    s = Wenku8BookSource()
    s._image_cache["https://x/cover.jpg"] = portrait
    chapters = [
        {"index": 0, "title": "第一章", "is_illustration": False},
        {"index": 1, "title": "插图", "is_illustration": True},
    ]
    texts = [
        "正文[[WENKU8IMG:https://x/notcover.jpg]]",   # 正文章，忽略
        "[[WENKU8IMG:https://x/cover.jpg]]",           # 插图章，取这张
    ]
    got = s.volume_cover(chapters, texts)
    assert got == portrait


def test_pick_cover_retries_missing_first_portrait(monkeypatch):
    """真封面图预取失败时，retry 把它取回，不被后面的目录页竖图顶替。"""
    landscape0 = _make_image_bytes(1200, 700)     # 跨页扫描横图
    true_cover = _make_image_bytes(836, 1300)     # 真卷封面（第一张竖图）
    toc_page = _make_image_bytes(861, 1300)       # 目录页（后面的竖图）
    urls = ["https://x/scan.jpg", "https://x/cover.jpg", "https://x/toc.jpg"]
    cache = {"https://x/scan.jpg": landscape0, "https://x/toc.jpg": toc_page}
    # cover.jpg 缺失，但 retry 能拿回真封面
    retried = {"n": 0}

    def _get(u):
        return cache.get(u, b"")

    def _retry(u):
        retried["n"] += 1
        if u == "https://x/cover.jpg":
            cache[u] = true_cover
            return true_cover
        return b""

    picked = V.pick_cover_bytes(urls, _get, retry=_retry)
    assert picked == true_cover, "必须重试取回真封面，而不是降级到目录页竖图"
    assert retried["n"] >= 1, "首选候选缺失时应触发重试"


def test_pick_cover_no_retry_when_cache_complete():
    """缓存齐全时绝不触发 retry（正常下载零额外网络开销）。"""
    cover = _make_image_bytes(600, 900)
    cache = {"https://x/scan.jpg": _make_image_bytes(900, 600),
             "https://x/cover.jpg": cover}

    def _should_not_call(u):
        raise AssertionError("缓存齐全时不应重试")

    picked = V.pick_cover_bytes(
        ["https://x/scan.jpg", "https://x/cover.jpg"],
        lambda u: cache.get(u, b""), retry=_should_not_call,
    )
    assert picked == cover


def test_volume_cover_retry_writes_back_cache_and_clears_missing(monkeypatch):
    """volume_cover 的直达重试成功写回缓存，并从缺图清单移除该 URL。"""
    from plugins.wenku8_downloader_plugin import Wenku8BookSource
    true_cover = _make_image_bytes(836, 1300)
    toc_page = _make_image_bytes(861, 1300)
    s = Wenku8BookSource()
    # cover.jpg 预取失败：不在缓存，且在缺图清单里
    s._image_cache["https://x/toc.jpg"] = toc_page
    s._image_missing = ["https://x/cover.jpg"]

    import gui.wenku8_core.wenku8_volumes as VV
    monkeypatch.setattr(VV, "download_image",
                        lambda u, attempts=5: true_cover if "cover" in u else b"")
    chapters = [{"index": 0, "title": "插图", "is_illustration": True}]
    texts = ["[[WENKU8IMG:https://x/cover.jpg]][[WENKU8IMG:https://x/toc.jpg]]"]
    got = s.volume_cover(chapters, texts)
    assert got == true_cover
    assert s._image_cache.get("https://x/cover.jpg") == true_cover, "重试结果须写回缓存"
    assert "https://x/cover.jpg" not in s._image_missing, "拿回的图须移出缺图清单"


def test_epub_item_build_failure_raises_in_strict_mode(monkeypatch):
    """图字节有效但 ebooklib 构造条目失败时，严格模式也必须判失败，不静默丢图。"""
    import pytest
    from ebooklib import epub

    class _StrictSource(_FakeSource):
        source_key = "wenku8"

    monkeypatch.setattr(C, "_w8_image_item", lambda *a, **k: None)
    book = epub.EpubBook()
    with pytest.raises(C.QimaoError):
        C._render_body(
            "[[WENKU8IMG:https://img.wenku8.com/a.jpg]]",
            _StrictSource(with_images=True), book, epub, False, [0], [],
        )


def test_download_image_cloak_fallback_recovers(monkeypatch):
    """requests 全失败但指纹层拿到图：必须救回，不能返回空导致误判缺图。"""
    import requests

    def fake_get(url, timeout=None, headers=None):
        class _R:
            content = b"<html>403 Cloudflare</html>"
        return _R()

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(V, "_download_image_cloak",
                        lambda url, timeout=15, referer="": b"\xff\xd8\xff\xe0JFIF")
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    data = V.download_image("https://pic.wenku8.com/a.jpg", attempts=3)
    assert data.startswith(b"\xff\xd8\xff"), "指纹层救回的图片应被采用"


def test_txt_missing_image_cleans_up_image_dir(tmp_path):
    """TXT 缺图失败时，ImageWriter 已落地的部分插图目录必须被清理。"""
    import pytest
    from gui.qimao_core import build_txt, QimaoError, BookSource

    class _Src(BookSource):
        source_key = "wenku8"
        name = "测试书源"

        def fetch_detail(self, book_id, session):
            return {"title": "测试书", "author": "作者"}

        def fetch_catalog(self, book_id, session):
            return [{"id": "1", "index": 1, "title": "第一章", "url": "https://x/1.htm"}]

        def get_image(self, url):
            return b""  # 缺图

        def prefetch_texts(self, ctx, catalog, session, on_progress=None):
            # 第一章有图标记；渲染时缺图抛错
            return ["[[WENKU8IMG:https://x/a.jpg]]", "正文"]

    src = _Src()
    with pytest.raises(QimaoError):
        build_txt("1", str(tmp_path), "txt", title_hint="测试书",
                  chapter_hero=False, chapter_tail=False, smart_art=False,
                  source=src)
    # 插图目录「测试书_插图」应被清理，不留散图
    leftovers = [p for p in tmp_path.iterdir() if "插图" in p.name]
    assert not leftovers, f"失败后不应残留插图目录/散图，实际: {[p.name for p in leftovers]}"
