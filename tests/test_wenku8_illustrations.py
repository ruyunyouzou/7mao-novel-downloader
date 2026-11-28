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


def test_failed_image_does_not_break_render():
    from ebooklib import epub
    book = epub.EpubBook()
    counter = [0]
    fails = []
    out = C._render_body(
        "[[WENKU8IMG:https://img.wenku8.com/missing.jpg]]",
        _FakeSource(with_images=False), book, epub, False, counter, fails,
    )
    assert fails == ["https://img.wenku8.com/missing.jpg"]
    assert "<img" not in out  # 失败图不写入空 <img>，但整章照常渲染
