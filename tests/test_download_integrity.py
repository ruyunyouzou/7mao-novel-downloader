# -*- coding: utf-8 -*-
"""文库8下载完整性回归测试。

用户反馈：任务显示「已完成 100%」但章节数只有 9/29、57/295。
根因有两层：

1. 逐章 GET 遇到 httpcloak unexpected EOF / 连接重置时，单章正文变空；
   空章被静默跳过，进度仍推进到 100%。
2. 失败章节被静默跳过，残缺文件冒充完整成品。

用户要的是「把整本书内容下载全」，不是「失败后报错」。
本测试锁死三层全量补齐：单请求 6 次重试、预取失败 3 轮补抓、
渲染阶段兜底重试；文库8只要缺一章就不出成品，进度不倒退。
"""
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from gui.qimao_core import BookSource, QimaoError, build_txt  # noqa: E402
from gui.wenku8_core import wenku8_reader  # noqa: E402


class FakeSource(BookSource):
    """内存书源：只提供可控的成功/失败章节，不触碰网络。"""

    source_key = "fake"
    name = "测试书源"

    def __init__(self, chapter_count=10, fail_indices=(),
                 source_key="fake", transient_fails=0):
        self.source_key = source_key
        self.fail_indices = set(int(i) for i in fail_indices)
        self.transient_fails = int(transient_fails)
        self._calls = {}
        self.catalog = [
            {
                "id": i + 1,
                "index": i + 1,
                "title": f"第{i + 1}章",
                "url": f"https://example.test/{i + 1}.htm",
            }
            for i in range(chapter_count)
        ]
        self.report_args = []

    def fetch_detail(self, book_id, session):
        return {"title": "测试书", "author": "测试作者"}

    def fetch_catalog(self, book_id, session):
        return list(self.catalog)

    def chapter_text(self, ctx, chapter, session, report):
        self.report_args.append(report)
        idx = int(chapter["index"])
        calls = self._calls.get(idx, 0) + 1
        self._calls[idx] = calls
        if idx in self.fail_indices:
            return ""
        if calls <= self.transient_fails:
            return ""
        return f"    第{chapter['index']}章正文内容"


def _build(source, tmp_path, fmt="txt", progress=None):
    return build_txt(
        "1",
        str(tmp_path),
        fmt,
        progress_cb=(progress.append if progress is not None else None),
        title_hint="测试书",
        chapter_hero=False,
        chapter_tail=False,
        smart_art=False,
        source=source,
    )


def test_high_failure_rate_never_reports_100_percent(tmp_path):
    """20/29 章失败（用户截图场景）必须报错，不能报 100% 成功。"""
    progress = []
    source = FakeSource(29, range(3, 29))  # 仅前 3 章成功，失败率 26/29

    with pytest.raises(QimaoError, match="下载不完整"):
        _build(source, tmp_path, progress=progress)

    assert 100 not in progress
    assert not (tmp_path / "测试书.txt").exists()
    assert not (tmp_path / "测试书.txt.part").exists()
    # 回退逐章抓取不能传 report，否则进度会从 40 倒退到 2。
    assert all(arg is None for arg in source.report_args)


def test_wenku8_transient_failures_are_rescued_in_prefetch(tmp_path):
    """短暂失败必须在并发预取阶段补齐，产出完整全本。"""
    progress = []
    # 每章前 2 次抓取失败，第 3 次成功；补抓在 prefetch 内完成。
    source = FakeSource(29, source_key="wenku8", transient_fails=2)

    def prefetch(ctx, catalog, session, on_progress=None):
        texts = []
        for ch in catalog:
            text = ""
            for _ in range(3):
                text = source.chapter_text(ctx, ch, session, None)
                if text:
                    break
            texts.append(text)
        if on_progress:
            on_progress(len(catalog), len(catalog))
        return texts

    source.prefetch_texts = prefetch

    result = _build(source, tmp_path, progress=progress)

    assert result["chapters"] == 29
    assert result["total"] == 29
    assert progress[-1] == 100
    content = (tmp_path / "测试书.txt").read_text(encoding="utf-8-sig")
    for i in range(1, 30):
        assert f"第{i}章正文内容" in content, f"第{i}章正文缺失"


def test_render_stage_never_refetches_prefetched_chapters(tmp_path):
    """回归 40% 卡死：渲染阶段只读预取结果，绝不能再发网络请求。"""
    source = FakeSource(29, source_key="wenku8")
    source.prefetch_texts = lambda ctx, catalog, session, on_progress=None: [
        f"    第{i + 1}章正文内容" for i in range(len(catalog))
    ]

    def must_not_fetch(*args, **kwargs):
        raise AssertionError("渲染阶段不应再次抓取正文")

    source.chapter_text = must_not_fetch

    result = _build(source, tmp_path)
    assert result["chapters"] == 29


def test_wenku8_any_missing_chapter_blocks_success(tmp_path):
    """文库8零容忍：预取后仍缺一章，也不能出残缺成品。"""
    source = FakeSource(10, range(4), source_key="wenku8")

    with pytest.raises(QimaoError, match="经多轮重试仍未取到"):
        _build(source, tmp_path, fmt="chapter")

    assert not (tmp_path / "测试书").exists()


def test_partial_chapter_directory_is_removed_on_failure(tmp_path):
    """分章 TXT 模式下 result_path 是目录，失败清理必须删目录而非 os.remove。"""
    source = FakeSource(10, range(4))  # 40% 失败

    with pytest.raises(QimaoError, match="下载不完整"):
        _build(source, tmp_path, fmt="chapter")

    assert not (tmp_path / "测试书").exists(), "残缺分章目录未被删除"


def test_build_result_carries_author_for_queue_backfill(tmp_path):
    """EPUB 内作者正确但队列卡片显示 — 的修复：结果必须带 author。"""
    source = FakeSource(5)
    result = _build(source, tmp_path)
    assert result["author"] == "测试作者"


def test_all_chapters_success_still_reports_complete(tmp_path):
    """正常书不能被失败率拦截误伤：29/29 + 100%。"""
    progress = []
    source = FakeSource(29)

    result = _build(source, tmp_path, progress=progress)

    assert result["chapters"] == 29
    assert result["total"] == 29
    assert progress[-1] == 100
    (tmp_path / "测试书.txt").exists()
    assert all(arg is None for arg in source.report_args)


def test_get_with_retry_recovers_from_transient_eof(monkeypatch):
    """httpcloak 偶发 EOF 必须自动重试，不能让这一章直接变空。"""
    monkeypatch.setattr(wenku8_reader._time, "sleep", lambda _s: None)

    final = type("Resp", (), {"status_code": 200})()

    class FlakySession:
        def __init__(self):
            self.calls = 0

        def get(self, url, timeout=None):
            self.calls += 1
            if self.calls < 3:
                raise OSError("unexpected EOF")
            return final

    sess = FlakySession()
    resp = wenku8_reader._get_with_retry(sess, "https://example.test/1.htm", attempts=4)

    assert resp is final
    assert sess.calls == 3


def test_get_with_retry_retries_gateway_errors(monkeypatch):
    """502/503/504/429 是瞬态网关错误，也需要重试。"""
    monkeypatch.setattr(wenku8_reader._time, "sleep", lambda _s: None)

    responses = [
        type("Resp", (), {"status_code": 502})(),
        type("Resp", (), {"status_code": 200})(),
    ]
    calls = {"n": 0}

    def fake_get(url, timeout=None):
        i = calls["n"]
        calls["n"] += 1
        return responses[min(i, len(responses) - 1)]

    class Sess:
        get = staticmethod(fake_get)

    resp = wenku8_reader._get_with_retry(Sess(), "https://example.test/1.htm", attempts=3)

    assert resp is responses[1]
    assert calls["n"] == 2


def test_chapter_fetch_uses_bounded_retry_with_validate():
    """正文请求有界重试(4次)且带 validate：拦截页/无#content页会被识别重试，
    避免 HTTP 200 的挑战页被当成功、静默产出空章节（用户34/70章失败根因）。"""
    with open(
        os.path.join(ROOT, "src", "gui", "wenku8_core", "wenku8_reader.py"),
        encoding="utf-8",
    ) as f:
        src = f.read()
    assert "attempts=4, validate=_has_content" in src
    assert "def _looks_like_block_page" in src


def test_wenku8_thread_local_session_reuse(monkeypatch):
    """根因回归：每线程一个会话并复用，既不能每章重建也不能跨线程共享。

    每章新建会话：295 章 49 次 EOF；每线程一个会话：295 章仅 1 次 EOF
    （由重试补齐）；跨线程共享单会话：非线程安全，Windows 上会卡住。
    """
    from gui.wenku8_core import wenku8_auth
    from plugins.wenku8_downloader_plugin import Wenku8BookSource

    created = []

    class FakeSession:
        pass

    def fake_make_session(cookie=None, *args, **kwargs):
        created.append(cookie)
        return FakeSession()

    monkeypatch.setattr(wenku8_auth, "make_session", fake_make_session)
    monkeypatch.setattr(wenku8_auth, "get_cookie", lambda: "")

    source = Wenku8BookSource()
    seen = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker():
        first = source._session(None)
        barrier.wait()
        second = source._session(None)
        with lock:
            seen.append((threading.get_ident(), first, second))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(seen) == 8
    assert all(first is second for _tid, first, second in seen), "同一线程必须复用会话"
    assert len({id(first) for _tid, first, _second in seen}) == 8, "不同线程不能共享会话"
    assert len(created) == 8, "每个线程只创建一次会话"


def test_catalog_prefix_fallback_finds_novel_2(monkeypatch):
    """详情页被拦时不能只猜 /novel/1/，必须探测到真实 /novel/2/。"""
    from gui.wenku8_core import wenku8_reader as reader

    good_html = (
        '<html><body><table><tr><td class="ccss">'
        '<a href="86807.htm">「小说家与自动书记人偶」</a>'
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
            if "/novel/2/2353/index.htm" in url:
                return Resp(good_html)
            return Resp("<html>404</html>", 404)

    monkeypatch.setattr(reader, "_base", lambda: "https://www.wenku8.cc")
    catalog = reader.fetch_catalog("2353", session=Sess())

    assert len(catalog) == 1
    assert catalog[0]["url"].startswith("https://www.wenku8.cc/novel/2/2353/")
    # 修复「目录为空耗时100s」：首选本地索引 cover 分类号(/image/2/ → /novel/2/)，
    # 详情页被拦也能一击命中，不再瞎猜 /novel/1/../8/。第一个请求即真实分类号。
    assert any("/novel/2/2353/index.htm" in u for u in calls)
    index_calls = [u for u in calls if "/index.htm" in u]
    # 第一个目录请求就是真实分类号（索引 cover 的 /image/2/），无 /novel/1/ 瞎猜
    assert index_calls and index_calls[0].endswith("/novel/2/2353/index.htm"), calls[:4]
    assert not any("/novel/1/2353/index.htm" in u for u in calls), calls[:4]
