# -*- coding: utf-8 -*-
"""EPUB 标题颜色端到端测试（离线）。

验证微信读书「有卷名时 h3 章名变黑」问题的修复：
卷 h1 / 集 h2 / 章 h3 三级标题都内联 color + 内层 span 双保险，
保证任意内核、任意书（有无卷/集）都正确上色。用假 BookSource 生成真实
EPUB，解压逐章检查 HTML，不访问网络。
"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gui import qimao_core as core  # noqa: E402

CH_COLOR = "#c62828"   # 章名中国红
VOL_COLOR = "#1565c0"  # 卷名深蓝


class FakeSource(core.BookSource):
    """无网络的假书源，目录带卷/集/章三级元数据。"""

    def __init__(self, chapters):
        self.chapters = chapters

    def fetch_detail(self, book_id, session):
        return {"title": "测试书", "author": "测试"}

    def fetch_catalog(self, book_id, session):
        return self.chapters

    def prefetch_texts(self, ctx, catalog, session, on_progress=None):
        return ["正文内容。" for _ in catalog]

    def context(self, book_id, session, report):
        class _Ctx:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False
        return _Ctx()

    def chapter_text(self, ctx, ch, session, report):
        return "正文内容。"


def _build_and_read(chapters):
    with tempfile.TemporaryDirectory() as td:
        result = core.build_txt(
            book_id="test", save_path=td, fmt="epub",
            title_hint="测试书", source=FakeSource(chapters),
            chapter_color=CH_COLOR, volume_color=VOL_COLOR,
            chapter_hero=False, chapter_tail=False, smart_art=False,
            colorize_dialogue=False,
        )
        epub_path = result["file_path"]
        html_parts = []
        with zipfile.ZipFile(epub_path) as z:
            for name in z.namelist():
                base = os.path.basename(name)
                if base.startswith("ch_") and base.endswith(".xhtml"):
                    html_parts.append(z.read(name).decode("utf-8", "replace"))
        return "".join(html_parts)


def _assert_inline(html, tag, cls, color):
    """确认 class 匹配的元素同时带 h 级内联色和内层 span 内联色。"""
    needle_open = f'<{tag} class="{cls}"'
    assert needle_open in html, f"缺少 <{tag} class={cls}>"
    # 该元素开标签内必须含 color
    i = html.index(needle_open)
    close = html.index(">", i)
    open_tag = html[i:close]
    assert f"color:{color}" in open_tag, f"{tag}.{cls} 开标签缺内联色: {open_tag}"
    # 内层 span 也必须带内联色
    assert f'<span style="color:{color}' in html[i:close + 200], \
        f"{tag}.{cls} 内层 span 缺内联色"


def test_three_levels_colored():
    """卷 h1 / 集 h2 / 章 h3 三级都带内联色。"""
    chapters = [
        {"index": 1, "title": "第1章 开端", "vol": "第一卷 起步", "sub": "上篇"},
        {"index": 2, "title": "第2章 发展", "vol": "第一卷 起步", "sub": "上篇"},
        {"index": 3, "title": "第3章 高潮", "vol": "第二卷 巅峰", "sub": "下篇"},
    ]
    html = _build_and_read(chapters)
    _assert_inline(html, "h1", "volume-name", VOL_COLOR)
    _assert_inline(html, "h2", "volume-name", VOL_COLOR)
    _assert_inline(html, "h3", "chapter-title", CH_COLOR)


def test_volume_chapter_no_sub_colored():
    """只有卷+章（无集）时，卷 h1 与章 h3 都带内联色。"""
    chapters = [
        {"index": 1, "title": "第1章 开端", "vol": "第一卷 起步", "sub": ""},
        {"index": 2, "title": "第2章 发展", "vol": "第一卷 起步", "sub": ""},
    ]
    html = _build_and_read(chapters)
    _assert_inline(html, "h1", "volume-name", VOL_COLOR)
    assert '<h2 class="volume-name"' not in html, "无集不应渲染 h2 集名"
    _assert_inline(html, "h3", "chapter-title", CH_COLOR)


def test_flat_no_volume_colored():
    """纯章目录（无卷无集）回退 h2 章名，同样带内联色。"""
    chapters = [
        {"index": 1, "title": "第1章 开端"},
        {"index": 2, "title": "第2章 发展"},
    ]
    html = _build_and_read(chapters)
    assert '<h1 class="volume-name"' not in html
    _assert_inline(html, "h2", "chapter-title", CH_COLOR)


def test_inline_helpers():
    """辅助函数对纯色与渐变的取值正确。"""
    assert core._plain_title_color("#c62828") == "#c62828"
    assert core._plain_title_color("  #1565c0  ") == "#1565c0"
    assert core._plain_title_color("linear:90|#ff0000,#00ff00") == "#ff0000"
    style = core.title_inline_style(CH_COLOR)
    assert style == f' style="color:{CH_COLOR};-webkit-text-fill-color:{CH_COLOR};"'
    assert core.title_inline_style("") == ""


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
