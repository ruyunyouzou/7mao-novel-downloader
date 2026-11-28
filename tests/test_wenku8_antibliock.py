# -*- coding: utf-8 -*-
"""文库8 站点拦截/限流容错回归测试。

用户真实反馈三张图：
- 玩玩的恋爱关系：2%「目录为空」（耗时102s）
- 同书：99%「70章中34章正文经多轮重试仍未取到」（耗时263s）
- 天使书：卡40%

根因：
1. HTTP 200 的 Cloudflare 挑战页/限流页被当成成功解析，静默产出空章节；
2. 详情页被拦拿不到分类号 → 瞎猜9个前缀×3次×15s = 100 秒仍空；
3. 外层 5 轮补抓风暴，越重试越被限流，进度卡死。

本测试锁定修复契约（全部 mock，不触网、不真的 sleep 很久）。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from gui.wenku8_core import wenku8_reader as R  # noqa: E402


class Resp:
    def __init__(self, text="", status=200, headers=None):
        self._text = text
        self.status_code = status
        self.encoding = None
        self.headers = headers or {}

    @property
    def text(self):
        return self._text


class SeqSession:
    """按序返回预设响应，统计请求次数。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, *_a, **_k):
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


CF_PAGE = "<html><title>Just a moment...</title><body>cf-challenge</body></html>"
GOOD_CHAPTER = "<html><body><div id='content'>正文内容<br>第二行文字</div></body></html>"


def test_block_page_detected_even_with_200(monkeypatch):
    """HTTP 200 的 Cloudflare 挑战页必须被识别为拦截，不当成功。"""
    assert R._looks_like_block_page(Resp(CF_PAGE, 200)) is True
    assert R._looks_like_block_page(Resp("<html>429 Too Many Requests</html>", 200)) is True


def test_normal_page_not_mistaken_for_block(monkeypatch):
    """正常正文页（即使很长）不能被误判为拦截页。"""
    long_body = "<html><body><div id='content'>" + ("正文内容" * 2000) + "</div></body></html>"
    assert R._looks_like_block_page(Resp(long_body, 200)) is False


def test_chapter_retries_after_challenge_page(monkeypatch):
    """前两次是挑战页、第三次正常 → 必须重试拿到正文，而非静默空。"""
    monkeypatch.setattr(R._time, "sleep", lambda *_a, **_k: None)
    sess = SeqSession([Resp(CF_PAGE), Resp(CF_PAGE), Resp(GOOD_CHAPTER)])
    text = R.fetch_chapter_text("https://www.wenku8.cc/novel/2/2580/97958.htm", sess)
    assert "正文内容" in text
    assert sess.calls == 3


def test_chapter_validate_retries_when_no_content(monkeypatch):
    """200 但没有 #content 的页（非典型挑战文案）也必须靠 validate 重试。"""
    monkeypatch.setattr(R._time, "sleep", lambda *_a, **_k: None)
    empty200 = "<html><body>some interstitial page</body></html>"
    sess = SeqSession([Resp(empty200), Resp(GOOD_CHAPTER)])
    text = R.fetch_chapter_text("https://www.wenku8.cc/novel/2/2580/97958.htm", sess)
    assert "正文内容" in text
    assert sess.calls == 2


def test_retry_after_header_respected(monkeypatch):
    """429 带 Retry-After 时退避应至少等待该秒数。"""
    slept = []
    monkeypatch.setattr(R._time, "sleep", lambda s: slept.append(s))
    delay = R._retry_delay(0, Resp("<html>429</html>", 429, {"Retry-After": "3"}))
    assert delay >= 3.0


def test_catalog_prefix_prefers_index_class_number():
    """目录候选前缀首选本地索引 cover 里的分类号（零网络，全书通用）。"""
    cats = R._catalog_prefix_candidates("", "3988")
    assert cats[0] == "/novel/3/3988/", cats[:3]
    cats2 = R._catalog_prefix_candidates("", "2580")
    assert cats2[0] == "/novel/2/2580/", cats2[:3]


def test_rescue_rounds_reduced_to_three():
    """外层补抓轮次收敛为3（旧值5会引发重试风暴）；源码级断言防回退。"""
    import inspect
    src = inspect.getsource(
        __import__("plugins.wenku8_downloader_plugin", fromlist=["x"])
        .Wenku8BookSource.prefetch_texts
    )
    assert "range(1, 4)" in src, "补抓轮次应收敛为3轮"
    assert "range(1, 6)" not in src, "不应回到5轮风暴"
