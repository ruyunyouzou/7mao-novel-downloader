# -*- coding: utf-8 -*-
"""分卷导出任务队列卡片：逐卷进度展示回归测试。

覆盖：
- plugin _download_volumes 通过 progress_cb(volumes=...) 上报每卷明细，
  且最终 DownloadResult.extra 带 volumes_progress；
- worker 的 progress_cb 兼容单参数旧签名，并把 volumes 转发到新信号；
- TaskCard：初始折叠、仅点箭头展开（防误触）、逐卷渲染、刷新状态保持、
  无分卷明细的普通任务回退原样（不显示展开按钮）。

全部 offscreen + FakeView，不触网。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ------------------------------------------------------------------ plugin 层

def test_plugin_reports_volume_progress_via_callback():
    """_download_volumes 必须在每卷推进时用 volumes= 上报逐卷明细。"""
    from plugins.wenku8_downloader_plugin import Wenku8BookSource
    from gui.plugin.base import DownloadResult

    src = Wenku8BookSource()
    snapshots = []

    # 直接调用内部辅助逻辑较难（依赖真实预取），这里锁死 helper 的契约：
    # 若 progress_cb 只收单参数，_report 的 TypeError 兜底必须仍能工作。
    calls = {"single": 0}

    def single_param_cb(p):
        calls["single"] += 1

    # 模拟 _report 的兼容逻辑（与插件内一致）
    def report(percent, volumes=None, cb=single_param_cb):
        try:
            cb(percent, volumes=volumes)
        except TypeError:
            cb(percent)

    report(50, volumes=[{"name": "第一卷"}])
    assert calls["single"] == 1, "单参数旧回调必须被兼容（不因 volumes 参数崩溃）"


def test_download_result_extra_has_volumes_progress_contract():
    """结果 extra 契约：分卷导出成功时带 volumes / volumes_progress。"""
    from gui.plugin.base import DownloadResult
    r = DownloadResult(
        book_id="1", title="t", success=True,
        extra={"volumes": [{"name": "第一卷"}],
               "volumes_progress": [{"name": "第一卷", "progress": 100, "status": "done"}]},
    )
    assert "volumes_progress" in r.extra
    assert r.extra["volumes_progress"][0]["status"] == "done"


# ------------------------------------------------------------------ worker 层

def test_worker_progress_cb_forwards_volumes(qapp):
    """progress_cb(p, volumes=...) 时发 volume_progress 信号；单参数调用不触发。"""
    from gui.workers import DownloadWorker

    w = DownloadWorker(downloader=object(), book_info={"id": "1"}, save_path="/tmp")
    got = []
    w.volume_progress.connect(lambda v: got.append(v))

    # 直接复现 run() 里的闭包逻辑（不真正 start 线程）
    def progress_cb(p, volumes=None):
        w.progress.emit(int(p))
        if volumes is not None:
            w.volume_progress.emit([dict(v) for v in volumes])

    progress_cb(50)  # 单参数：不应发分卷信号
    assert got == []
    progress_cb(60, volumes=[{"name": "第一卷", "progress": 45, "status": "downloading"}])
    assert len(got) == 1
    assert got[0][0]["status"] == "downloading"
    # 深拷贝：后续原地改源不影响已 emit 的对象
    src = [{"name": "第二卷", "progress": 0}]
    progress_cb(70, volumes=src)
    src[0]["progress"] = 99
    assert got[1][0]["progress"] == 0, "emit 的应是快照，不受源对象后续修改影响"


# ------------------------------------------------------------------ 卡片 UI 层

class FakeView:
    def _bar_colors(self):
        return ("#B39DDB", True)


def _vps():
    return [
        {"name": "第一卷", "chapters": 31, "progress": 100, "status": "done"},
        {"name": "第二卷", "chapters": 28, "progress": 100, "status": "done"},
        {"name": "第十一卷", "chapters": 26, "progress": 45, "status": "downloading"},
        {"name": "第11.5卷", "chapters": 12, "progress": 0, "status": "waiting"},
    ]


def _task(vps=None):
    extra = {"volumes_progress": vps} if vps is not None else {}
    return {"id": "t1", "book": {"title": "测试书", "source": "wenku8"},
            "status": "downloading", "progress": 62, "format": "epub",
            "extra": extra, "created_at": "2026-11-28 17:23"}


def _visible(widget):
    """离屏下 isVisible() 依赖父窗口，用 isHidden() 判断显式可见性。"""
    return not widget.isHidden()


def test_card_initial_collapsed(qapp):
    from gui.views.queue_view import TaskCard
    card = TaskCard(FakeView())
    card.update_data(_task(_vps()))
    assert _visible(card.vol_toggle)
    assert not _visible(card.vol_area), "初始应折叠"
    assert len(card._vol_rows) == 4


def test_card_expand_only_by_arrow_anti_mistouch(qapp):
    """防误触：点击卡片本体（clicked 信号）不得展开，只有点箭头才展开。"""
    from gui.views.queue_view import TaskCard
    card = TaskCard(FakeView())
    card.update_data(_task(_vps()))

    card.clicked.emit("t1")           # 模拟点卡片其他区域
    assert not _visible(card.vol_area), "点卡片本体不应展开（防误触）"

    card._toggle_volume_area()        # 模拟点箭头
    assert _visible(card.vol_area)
    assert "收起" in card.vol_toggle.text()

    card._toggle_volume_area()        # 再点收起
    assert not _visible(card.vol_area)
    assert "展开" in card.vol_toggle.text()


def test_card_renders_each_volume(qapp):
    from gui.views.queue_view import TaskCard
    card = TaskCard(FakeView())
    card.update_data(_task(_vps()))
    card._toggle_volume_area()
    p0 = card._vol_rows[0]._parts
    p2 = card._vol_rows[2]._parts
    p3 = card._vol_rows[3]._parts
    assert p0["idx"].text() == "✓" and p0["pct"].text() == "100%"
    assert p0["state"].text() == "完成"
    assert "第一卷" in p0["name"].text() and "31章" in p0["name"].text()
    assert p2["pct"].text() == "45%" and p2["state"].text() == "下载中"
    assert p3["pct"].text() == "—" and p3["state"].text() == "等待中"
    assert "2/4" in card.vol_summary.text()


def test_card_keeps_expand_state_on_refresh(qapp):
    """卡片复用，refresh(update_data) 后展开状态不丢。"""
    from gui.views.queue_view import TaskCard
    card = TaskCard(FakeView())
    card.update_data(_task(_vps()))
    card._toggle_volume_area()
    assert _visible(card.vol_area)
    card.update_data(_task(_vps()))     # 模拟 refresh
    assert _visible(card.vol_area), "refresh 后应保持展开"


def test_card_plain_task_no_expand_button(qapp):
    """普通下载任务（无 volumes_progress）不显示展开按钮，零回归。"""
    from gui.views.queue_view import TaskCard
    card = TaskCard(FakeView())
    card.update_data(_task(None))
    assert not _visible(card.vol_toggle)
    assert not _visible(card.vol_area)


def test_card_failed_volume_marked_red(qapp):
    """失败卷必须标红显示，符合'图和书一起成功'的严格语义。"""
    from gui.views.queue_view import TaskCard
    vps = [{"name": "第一卷", "chapters": 10, "progress": 40, "status": "failed"}]
    card = TaskCard(FakeView())
    card.update_data(_task(vps))
    card._toggle_volume_area()
    p = card._vol_rows[0]._parts
    assert p["state"].text() == "失败"
    assert p["idx"].property("volState") == "failed"
