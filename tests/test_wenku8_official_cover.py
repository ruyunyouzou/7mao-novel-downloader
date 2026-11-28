# -*- coding: utf-8 -*-
"""Bangumi 官方正版高清封面回归测试（全部 mock，不触网）。

沙箱出口 IP 连不上 Bangumi 任何域名，故本测试完全用 mock 验证逻辑契约：
严格匹配（宁可回退也不换错）、尺寸校验、镜像 fallback、缓存命中零网络、
负缓存抑制重复请求、URL→aid 提取、括号别名匹配。
"""
import io
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image  # noqa: E402

from gui.wenku8_core import wenku8_official_cover as OC  # noqa: E402


def _jpeg(w=800, h=1200) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 80, 160)).save(buf, "JPEG")
    return buf.getvalue()


def _tiny_jpeg() -> bytes:
    return _jpeg(100, 140)


# --------------------------------------------------------------------------- aid 提取


def test_extract_aid_from_url_and_id():
    assert OC.extract_aid("2580") == "2580"
    assert OC.extract_aid("https://img.wenku8.com/image/2/2580/2580s.jpg") == "2580"
    assert OC.extract_aid("https://www.wenku8.cc/novel/2/2580/86807.htm") == "2580"
    assert OC.extract_aid("https://www.wenku8.cc/book/2580.htm") == "2580"
    assert OC.extract_aid("") == ""


def test_title_variants_include_parenthesized_alias():
    v = OC.title_variants(
        "关于我在无意间被隔壁的天使变成废柴这件事"
        "(关于邻家的天使大人不知不觉把我惯成了废人这档子事)")
    assert len(v) == 2
    assert "关于邻家的天使大人不知不觉把我惯成了废人这档子事" in v


# --------------------------------------------------------------------------- 严格匹配（防错配）


def test_strict_match_accepts_exact_alias():
    """台版译名在括号别名里、与 Bangumi name_cn 完全相同 → 认领。"""
    results = [{
        "id": 12345,
        "name": "俺の隣の天使様",
        "name_cn": "关于邻家的天使大人不知不觉把我惯成了废人这档子事",
        "images": {"large": "https://lain.bgm.tv/pic/cover/l/x.jpg"},
    }]
    title = ("关于我在无意间被隔壁的天使变成废柴这件事"
             "(关于邻家的天使大人不知不觉把我惯成了废人这档子事)")
    sid, url = OC._best_match(results, title)
    assert sid == "12345"
    assert "x.jpg" in url


def test_strict_match_rejects_wrong_book():
    """书名完全不相干的条目必须拒绝——换错封面比没有封面更糟。"""
    results = [{
        "id": 999, "name": "完全无关的书", "name_cn": "另一本小说",
        "images": {"large": "https://lain.bgm.tv/pic/cover/l/wrong.jpg"},
    }]
    sid, _ = OC._best_match(results, "薇尔莉特·伊芙加登(紫罗兰永恒花园)")
    assert sid == ""


def test_strict_match_rejects_short_generic_word():
    """短泛词（如『天使』）不能靠弱包含错配。"""
    results = [{
        "id": 1, "name": "天使", "name_cn": "天使",
        "images": {"large": "https://lain.bgm.tv/pic/cover/l/a.jpg"},
    }]
    sid, _ = OC._best_match(results, "关于我在无意间被隔壁的天使变成废柴这件事")
    assert sid == ""


# --------------------------------------------------------------------------- 尺寸校验 / 镜像


def test_valid_image_requires_min_size():
    ok, w, h = OC._valid_image(_jpeg(800, 1200))
    assert ok and w == 800 and h == 1200
    ok, _, _ = OC._valid_image(_tiny_jpeg())
    assert not ok
    ok, _, _ = OC._valid_image(b"<html>403</html>")
    assert not ok


def test_mirror_urls_keep_original_first():
    urls = OC._mirror_urls("https://lain.bgm.tv/pic/cover/l/x.jpg")
    assert urls[0] == "https://lain.bgm.tv/pic/cover/l/x.jpg"
    assert any("lain.bangumi.one" in u for u in urls)
    assert all("/pic/cover/l/x.jpg" in u for u in urls)


# --------------------------------------------------------------------------- 下载 / 缓存（mock 网络）


class _Resp:
    def __init__(self, content=b"", status=200, json_data=None):
        self.content = content
        self.status_code = status
        self._json = json_data or {}

    def json(self):
        return self._json


def test_download_large_falls_back_to_mirror(monkeypatch):
    """主域名失败、镜像成功 → 用镜像字节。"""
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if "lain.bgm.tv" in url:
            raise RuntimeError("blocked")
        return _Resp(content=_jpeg())

    monkeypatch.setattr("requests.get", fake_get)
    raw, w, h = OC._download_large("https://lain.bgm.tv/pic/cover/l/x.jpg", 3)
    assert raw and w == 800 and h == 1200
    assert len(calls) >= 2


def test_fetch_official_cover_cache_hit_zero_network(monkeypatch, tmp_path):
    """本地已有大图缓存 → 零网络，直接返回字节。"""
    monkeypatch.setattr(OC, "cache_dir", lambda: str(tmp_path))
    aid = "2580"
    (tmp_path / f"{aid}.jpg").write_bytes(_jpeg())

    def boom(*a, **k):
        raise AssertionError("缓存命中不应发网络请求")

    monkeypatch.setattr("requests.get", boom)
    raw = OC.fetch_official_cover(aid, "书名", timeout=1)
    assert raw and raw[:2] == b"\xff\xd8"


def test_fetch_official_cover_end_to_end_mock(monkeypatch, tmp_path):
    """搜索→严格匹配→详情→下载→落缓存 全链路 mock。"""
    monkeypatch.setattr(OC, "cache_dir", lambda: str(tmp_path))
    OC._map_cache = None

    title = "关于我在无意间被隔壁的天使变成废柴这件事(关于邻家的天使大人不知不觉把我惯成了废人这档子事)"

    # Bangumi v0 搜索是 POST + JSON body；详情/图片才是 GET。
    def fake_post(url, **kwargs):
        assert "/v0/search/subjects" in url
        assert kwargs.get("json", {}).get("type") == 1, "搜索必须带 type=1"
        return _Resp(json_data={"data": [{
            "id": 777, "name": "天使様",
            "name_cn": "关于邻家的天使大人不知不觉把我惯成了废人这档子事",
            "images": {"large": "https://lain.bgm.tv/pic/cover/l/x.jpg"},
        }]})

    def fake_get(url, **kwargs):
        if "/v0/subjects/777" in url:
            return _Resp(json_data={
                "id": 777,
                "images": {"large": "https://lain.bgm.tv/pic/cover/l/x.jpg"},
            })
        if "/pic/cover/l/x.jpg" in url:
            return _Resp(content=_jpeg())
        return _Resp(status=404)

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.get", fake_get)
    raw = OC.fetch_official_cover("2580", title, timeout=1)
    assert raw and raw[:2] == b"\xff\xd8"
    assert (tmp_path / "2580.jpg").exists(), "大图应落盘缓存"
    mapping = OC._load_map()
    assert mapping["2580"]["sid"] == "777"
    # 二次调用零网络
    def boom(*a, **k):
        raise AssertionError("二次调用应命中缓存")
    monkeypatch.setattr("requests.get", boom)
    monkeypatch.setattr("requests.post", boom)
    assert OC.fetch_official_cover("2580", title, timeout=1)


def test_negative_cache_prevents_repeat_search(monkeypatch, tmp_path):
    """Book not on Bangumi → negative cache avoids repeat searches."""
    monkeypatch.setattr(OC, "cache_dir", lambda: str(tmp_path))
    OC._map_cache = None
    count = {"n": 0}

    def fake_post(url, **kwargs):
        count["n"] += 1
        return _Resp(json_data={"data": []})

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.get", lambda url, **kw: _Resp(status=404))
    assert OC.fetch_official_cover("9999", "冷门书", timeout=1) == b""
    first = count["n"]
    assert first > 0
    assert OC.fetch_official_cover("9999", "冷门书", timeout=1) == b""
    assert count["n"] == first, "负缓存内不应再次请求 Bangumi"


def test_official_cover_bytes_for_reads_cache(monkeypatch, tmp_path):
    """整本下载从本地缓存取官方大图（零网络），供 EPUB 内嵌。"""
    sys.path.insert(0, str(ROOT / "src" / "plugins"))
    import plugins.wenku8_downloader_plugin as P
    monkeypatch.setattr(
        "gui.wenku8_core.wenku8_official_cover.cached_official_cover",
        lambda aid: _jpeg(),
    )
    raw = P._official_cover_bytes_for("2580")
    assert raw[:2] == b"\xff\xd8"


def test_official_cover_bytes_for_no_cache_returns_empty(monkeypatch):
    """无缓存返回空，build_txt 回退原网络小图通道（零回归）。"""
    import plugins.wenku8_downloader_plugin as P
    monkeypatch.setattr(
        "gui.wenku8_core.wenku8_official_cover.cached_official_cover",
        lambda aid: b"",
    )
    assert P._official_cover_bytes_for("9999") == b""


def test_build_txt_accepts_cover_bytes_param():
    """build_txt 签名含 cover_bytes（官方大图入参），其他书源不传零影响。"""
    import inspect
    import gui.qimao_core as Q
    params = inspect.signature(Q.build_txt).parameters
    assert "cover_bytes" in params
    assert params["cover_bytes"].default == b""
