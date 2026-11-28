# -*- coding: utf-8 -*-
"""封面加载回归测试（全部书封面变占位图的根因修复）。

根因：多个封面加载点用裸 requests.get(url) 访问 img.wenku8.com，
无 User-Agent/Referer 被 CDN 判为脚本 403（返回 HTML 错误页），
QImage.fromData 拿到 HTML 变空图 → 显示"书"字占位。全站目录命中的书
只带 cover URL 不带 cover_aid，恰好都走这条裸 GET 分支，所以"全部书"中招。

修复：统一走 cover_helper.fetch_cover_bytes（浏览器头 + 魔数校验），
详情页新增封面位，文库8按 aid 构造官方 CDN 直链兜底。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def test_is_image_bytes_rejects_html_error_page():
    """403 的 HTML 错误页必须被识别为非图片，不能喂给 QImage。"""
    from gui.cover_helper import _is_image_bytes
    assert not _is_image_bytes(b"<!DOCTYPE html><html><h1>Welcome</h1></html>")
    assert not _is_image_bytes(b"<html><body>Access denied</body></html>")
    assert not _is_image_bytes(b"")
    # 真实图片魔数必须通过
    assert _is_image_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)  # JPEG
    assert _is_image_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)  # PNG


def test_fetch_cover_bytes_sends_browser_headers(monkeypatch):
    """核心：统一加载函数必须带 User-Agent 和 Referer（否则 403）。"""
    from gui import cover_helper

    captured = {}

    class _Resp:
        # 真实 JPEG 魔数 + 足够长度
        content = b"\xff\xd8\xff\xe0" + b"\x00" * 200

    def fake_get(url, timeout=None, headers=None, **kwargs):
        captured["url"] = url
        captured["headers"] = headers or {}
        return _Resp()

    monkeypatch.setattr("requests.get", fake_get)
    raw = cover_helper.fetch_cover_bytes("https://img.wenku8.com/image/2/2353/2353s.jpg")
    assert raw and raw[:2] == b"\xff\xd8"
    h = captured["headers"]
    assert "User-Agent" in h and "Mozilla" in h["User-Agent"]
    assert "Referer" in h and "img.wenku8.com" in h["Referer"]


def test_fetch_cover_bytes_rejects_blocked_html(monkeypatch):
    """即使请求成功但返回的是 HTML 拦截页，也必须返回空字节。"""
    from gui import cover_helper

    class _Resp:
        content = b"<!DOCTYPE html><html><h1>Welcome</h1></html>"

    monkeypatch.setattr("requests.get", lambda *a, **k: _Resp())
    assert cover_helper.fetch_cover_bytes("https://img.wenku8.com/x.jpg") == b""


def test_fetch_cover_bytes_rejects_non_http():
    """非 http(s) 的脏 URL 直接返回空，不发起请求。"""
    from gui.cover_helper import fetch_cover_bytes
    assert fetch_cover_bytes("") == b""
    assert fetch_cover_bytes("cover.jpg") == b""
    assert fetch_cover_bytes("file:///etc/passwd") == b""


def test_book_card_no_bare_requests_get():
    """回归：搜索卡片封面加载不能再出现裸 requests.get(self.url)。

    统一入口升级为 fetch_cover_smart（文库8优先官方大图，失败回退
    fetch_cover_bytes 带头小图）；两者都算合法，裸 GET 仍禁止。
    """
    with open(os.path.join(ROOT, "src", "gui", "book_card.py"), encoding="utf-8") as f:
        src = f.read()
    assert ("fetch_cover_smart" in src or "fetch_cover_bytes" in src), \
        "封面加载必须走统一带头下载函数（含智能入口）"
    # 裸 GET 形式不应存在（注释里提到无妨，检查实际的调用语句）
    assert "requests.get(self.url" not in src
    # 搜索卡片必须把书名传给加载器，供 Bangumi 严格匹配官方大图
    assert "title=" in src and "CoverLoader(" in src


def test_async_cover_uses_unified_fetcher():
    """回归：AsyncCover（历史/封面墙）也必须走统一加载函数。"""
    with open(os.path.join(ROOT, "src", "gui", "cover_helper.py"), encoding="utf-8") as f:
        src = f.read()
    run_body = src.split("class AsyncCover", 1)[1]
    assert ("fetch_cover_smart" in run_body or "fetch_cover_bytes" in run_body)
    # 实际调用语句不能是裸 requests.get(...)
    assert "resp = requests.get" not in run_body
    assert "requests.get(self.url" not in run_body
    # AsyncCover 需接收书名，供文库8严格匹配 Bangumi 官方大图
    assert "def __init__(self, url: str, title: str" in run_body


def test_wenku8_cover_url_from_aid():
    """文库8按 aid 构造官方 CDN 直链（详情页/卡片兜底用）。"""
    from gui.wenku8_core.wenku8_app_api import build_cover_url
    assert build_cover_url("2353") == "https://img.wenku8.com/image/2/2353/2353s.jpg"
    assert build_cover_url("4468") == "https://img.wenku8.com/image/4/4468/4468s.jpg"
    assert build_cover_url("abc") == ""


def test_detail_pane_has_cover_label():
    """详情页必须有封面位（用户要求所有页面显示封面）。"""
    with open(os.path.join(ROOT, "src", "gui", "main_window.py"), encoding="utf-8") as f:
        src = f.read()
    assert "detail_cover_label" in src
    assert "_show_detail_cover" in src
    # 详情页要调用封面展示逻辑
    assert "self._show_detail_cover(" in src
