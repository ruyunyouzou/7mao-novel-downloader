# -*- coding: utf-8 -*-
"""下载选项对话框关闭安全性回归测试。

用户反馈：第一次关闭下载对话框不退出，第二次关闭整个软件退出。
根因：closeEvent 对仍在运行的 QThread 调 deleteLater()，
PySide6 删除运行中线程会 abort 整个进程。

本测试锁定以下契约（全部 offscreen + mock，不触网、不真的 abort）：
- closeEvent 绝不 deleteLater 一个 isRunning() 的 worker；
- 目录 worker 纳入全局集合，finished 时自动释放并从集合移除；
- _on_volume_worker_done 不再重复 deleteLater；
- 连续两次打开/关闭对话框，worker 引用都被妥善持有（无遗留运行中线程被删）。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from PySide6.QtCore import Signal  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

# 持有 Qt 对象，避免父窗口/对话框被 GC 连带删除 C++ 对象
_keep_alive = []


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _MockSignal:
    def __init__(self):
        self.disconnect_calls = 0

    def connect(self, *_a, **_k):
        return None

    def disconnect(self, *_a, **_k):
        self.disconnect_calls += 1


class _FakeWorker:
    """纯 Python mock：不继承 QThread，避免未 start 的线程在退出时报警告。"""

    def __init__(self, running=True):
        self._running = running
        self.loaded = _MockSignal()
        self.failed = _MockSignal()
        self.finished_run = _MockSignal()
        self.delete_calls = 0
        self.interrupt_calls = 0
        self.wait_calls = 0

    def isRunning(self):
        return self._running

    def requestInterruption(self):
        self.interrupt_calls += 1

    def wait(self, ms=3000):
        self.wait_calls += 1
        return not self._running

    def deleteLater(self):
        self.delete_calls += 1


def _make_dialog(qapp, book_id="1"):
    from gui.download_dialog import DownloadDialog
    parent = QMainWindow()
    _keep_alive.append(parent)
    dlg = DownloadDialog(
        {"id": book_id, "title": "测试书", "source": "wenku8"},
        parent=parent,
    )
    _keep_alive.append(dlg)
    return dlg


def test_close_event_never_delete_later_running_worker(qapp):
    """核心回归：closeEvent 面对运行中的 worker 绝不调 deleteLater。"""
    dlg = _make_dialog(qapp)
    worker = _FakeWorker(running=True)
    dlg._volume_worker = worker

    dlg.closeEvent(QCloseEvent())

    assert worker.delete_calls == 0, "closeEvent 删除了运行中的线程——会导致进程 abort"
    assert worker.interrupt_calls == 1, "应协作请求中断"
    assert worker.wait_calls == 1, "应短暂等待线程自然结束"
    assert dlg._volume_worker is None


def test_close_event_finished_worker_not_deleted_here(qapp):
    """即便 worker 已结束，closeEvent 也不手动删除（交给 finished 自释放钩子）。"""
    dlg = _make_dialog(qapp)
    worker = _FakeWorker(running=False)
    dlg._volume_worker = worker

    dlg.closeEvent(QCloseEvent())

    assert worker.delete_calls == 0, "closeEvent 不应手动 deleteLater，统一交给自释放钩子"
    assert dlg._volume_worker is None


def test_on_volume_worker_done_does_not_delete(qapp):
    """_on_volume_worker_done 只清 UI 引用，不重复 deleteLater。"""
    dlg = _make_dialog(qapp)
    worker = _FakeWorker(running=False)
    dlg._volume_worker = worker
    dlg._on_volume_worker_done()
    assert worker.delete_calls == 0
    assert dlg._volume_worker is None


def test_real_worker_self_release_from_global_set(qapp):
    """真实钩子：纳入全局集合，_release 后从集合移除（不启动线程，避免测试噪音）。"""
    import gui.download_dialog as dd

    before = set(dd._orphan_catalog_workers)
    worker = dd._Wenku8CatalogWorker("some-book")  # __init__ 纳入集合并连 finished 自释放
    assert worker in dd._orphan_catalog_workers, "worker 必须纳入全局持有集合"

    # 直接触发全局释放钩子，验证引用清理契约（线程未启动，deleteLater 安全）。
    dd._release_catalog_worker(worker)
    assert worker not in dd._orphan_catalog_workers, "释放后应从全局集合移除"
    assert set(dd._orphan_catalog_workers) == before, "不应残留其它引用"


def test_two_sequential_open_close_keep_no_live_worker_deleted(qapp, monkeypatch):
    """二段式：连续两次打开/关闭，closeEvent 都不删运行中 worker。"""
    import gui.download_dialog as dd

    made = []

    class TrackingWorker(_FakeWorker):
        def __init__(self, book_id):
            super().__init__(running=True)
            made.append(self)

        def start(self):
            pass  # mock：不真的起线程，只验证 closeEvent 行为

    monkeypatch.setattr(dd, "_Wenku8CatalogWorker", TrackingWorker)

    for i in (1, 2):
        dlg = _make_dialog(qapp, book_id=str(i))
        dlg.closeEvent(QCloseEvent())

    assert len(made) == 2
    for worker in made:
        assert worker.delete_calls == 0, "第二次关闭也不得删除运行中线程"
        assert worker.interrupt_calls == 1
