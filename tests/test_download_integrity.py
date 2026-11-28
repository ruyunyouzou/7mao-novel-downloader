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


def test_wenku8_transient_failures_are_rescued_to_complete(tmp_path, monkeypatch):
    """用户真正要的结果：短暂失败必须被多轮补抓补齐，产出完整全本。"""
    monkeypatch.setattr("gui.qimao_core.time.sleep", lambda _s: None)
    progress = []
    # 每章前 2 次抓取失败，第 3 次成功；文库8渲染兜底会重试 4 次。
    source = FakeSource(29, source_key="wenku8", transient_fails=2)

    result = _build(source, tmp_path, progress=progress)

    assert result["chapters"] == 29
    assert result["total"] == 29
    assert progress[-1] == 100
    content = (tmp_path / "测试书.txt").read_text(encoding="utf-8-sig")
    for i in range(1, 30):
        assert f"第{i}章正文内容" in content, f"第{i}章正文缺失"


def test_wenku8_any_missing_chapter_blocks_success(tmp_path, monkeypatch):
    """文库8零容忍：多轮补抓后仍缺一章，也不能出残缺成品。"""
    monkeypatch.setattr("gui.qimao_core.time.sleep", lambda _s: None)
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


def test_chapter_fetch_uses_six_attempts():
    """正文单次请求必须显式使用 6 次重试（比目录/详情更激进）。"""
    with open(
        os.path.join(ROOT, "src", "gui", "wenku8_core", "wenku8_reader.py"),
        encoding="utf-8",
    ) as f:
        src = f.read()
    assert "_get_with_retry(sess, url, timeout=20, attempts=6)" in src


def test_wenku8_reuses_one_session_across_threads(monkeypatch):
    """根因回归：全书必须共用一个 httpcloak 会话，不能每线程各建一个。

    每建一次会话就多一次 TLS 指纹握手；实测每章新建会话会让约 16% 章节
    随机 unexpected EOF。单会话 8 线程实测 295/295 成功。
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
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        seen.append(source._session(None))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(created) == 1, "全书只能创建一次 httpcloak 会话"
    assert len(seen) == 8
    assert all(s is seen[0] for s in seen), "所有线程必须拿到同一个会话对象"
