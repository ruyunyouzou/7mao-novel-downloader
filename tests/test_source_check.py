# -*- coding: utf-8 -*-
"""内核状态自检 + 插件分组测试（离线，全 mock）。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_plugin_wenku8_in_core_group():
    """文库8真实下载内核归入 core（核心内核）分组。"""
    from gui.views.plugins_view import _PLUGIN_META
    meta = _PLUGIN_META.get("文库8真实下载内核")
    assert meta is not None, "文库8插件缺少固定映射，会落到「其他」分组"
    assert meta[0] == "core", f"文库8应在核心内核分组，实际 {meta[0]}"


def _run_check(search_books, sources):
    """构造 ProtocolCheckWorker 并以 mock 的 qimao_core 跑 run，返回结果。"""
    from gui.workers import ProtocolCheckWorker
    w = ProtocolCheckWorker()
    emits = []
    w.progress.connect(lambda t: emits.append(t))
    got = {}
    w.finished.connect(lambda r: got.update(r))

    class _FakeSource:
        def __init__(self, catalog):
            self._catalog = catalog

        def fetch_catalog(self, book_id, session):
            return self._catalog

    def fake_book_source_for(key):
        return _FakeSource(sources.get(key, []))

    with patch("gui.qimao_core.search_books", side_effect=search_books), \
         patch("gui.qimao_core.book_source_for", side_effect=fake_book_source_for):
        w.run()
    return got, emits


def test_all_three_sources_ok():
    result, emits = _run_check(
        search_books=lambda kw: [{"id": "1"}],
        sources={"qimao": [{"id": "c1"}], "fanqie": [{"id": "c2"}],
                 "wenku8": [{"id": "c3"}]},
    )
    assert result["search"] is True
    assert result["catalog"] is True
    assert result["fanqie"] is True
    assert result["wenku8"] is True
    assert result["ok"] is True
    assert "七猫 / 番茄 / 文库8 内核均正常" in result["message"]
    assert any("文库8" in e for e in emits), "进度应提示正在检测文库8"


def test_wenku8_failure_reported():
    result, _ = _run_check(
        search_books=lambda kw: [{"id": "1"}],
        sources={"qimao": [{"id": "c1"}], "fanqie": [{"id": "c2"}],
                 "wenku8": []},  # 文库8空目录 = 失败
    )
    assert result["wenku8"] is False
    assert result["ok"] is False
    assert "文库8" in result["message"]
    assert "登录态" in result["detail"] or "风控" in result["detail"]


def test_wenku8_exception_reported():
    def boom(key):
        if key == "wenku8":
            raise RuntimeError("connection refused")
        return MagicMock(fetch_catalog=lambda bid, s: [{"id": "x"}])

    from gui.workers import ProtocolCheckWorker
    w = ProtocolCheckWorker()
    got = {}
    w.finished.connect(lambda r: got.update(r))
    with patch("gui.qimao_core.search_books", lambda kw: [{"id": "1"}]), \
         patch("gui.qimao_core.book_source_for", side_effect=boom):
        w.run()
    assert got["wenku8"] is False
    assert "文库8" in got["message"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
