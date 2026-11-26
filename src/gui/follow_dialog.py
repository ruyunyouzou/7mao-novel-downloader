# -*- coding: utf-8 -*-
"""增量追更对话框：检查书架书籍是否有新章，勾选后只更新新章。"""

from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QProgressBar, QMessageBox,
)

from .datastore import DataStore
from .i18n import tr


class _CheckWorker(QThread):
    progress = Signal(int, int)
    done = Signal(list)

    def __init__(self, records: List[Dict[str, Any]], parent=None):
        super().__init__(parent)
        self.records = records

    def run(self) -> None:
        from .follow_core import check_updates
        results = check_updates(self.records,
                                lambda i, t: self.progress.emit(i, t))
        self.done.emit(results)


class _FollowWorker(QThread):
    progress = Signal(int)
    done = Signal(dict)

    def __init__(self, rec: Dict[str, Any], file_path: str, fmt: str, parent=None):
        super().__init__(parent)
        self.rec, self.file_path, self.fmt = rec, file_path, fmt

    def cancel(self) -> None:
        """协作式取消：进度回调里抛异常中断内核，避免 terminate 打坏半成品文件。"""
        self.requestInterruption()

    def _progress(self, p: int) -> None:
        from .plugin.base import DownloadCancelled
        if self.isInterruptionRequested():
            raise DownloadCancelled()
        self.progress.emit(int(p))

    def run(self) -> None:
        from .follow_core import follow_txt
        from .plugin.base import DownloadCancelled
        try:
            if self.fmt == "txt":
                r = follow_txt(self.rec, self.file_path, self._progress)
                new_total = r["total"] if r.get("changed") else self.rec.get("last_chapter", 0)
            else:
                # EPUB/分章：重建整本（缓存 ZIP 含全书，保证排版一致）
                import os as _os
                from .qimao_core import build_txt, book_source_for
                save_dir = _os.path.dirname(self.file_path) or "."
                res = build_txt(
                    book_id=str(self.rec.get("book_id", "")),
                    save_path=save_dir,
                    fmt=self.fmt,
                    progress_cb=self._progress,
                    title_hint=self.rec.get("title", ""),
                    author_hint=self.rec.get("author", ""),
                    # 按书源重建：番茄书不能再落到七猫缓存 ZIP
                    source=book_source_for(self.rec.get("source", "")),
                )
                r = {"changed": True, "message": "已重新生成", "total": res.get("total_chapters", 0)}
                new_total = res.get("total_chapters", 0)
                self.file_path = res.get("file_path", self.file_path)
            if self.isInterruptionRequested():
                return  # 已取消：丢弃结果，避免对话框关闭后回调
            self.done.emit({"ok": True, "message": r["message"],
                            "new_total": new_total, "file_path": self.file_path})
        except DownloadCancelled:
            return
        except Exception as exc:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.done.emit({"ok": False, "message": str(exc)})


class FollowDialog(QDialog):
    """追更对话框。"""

    def __init__(self, store: DataStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle(tr("follow.title"))
        self.resize(620, 420)
        self._results: List[Dict[str, Any]] = []
        self._workers: List[QThread] = []
        self._build_ui()
        self._load()

    def stop_workers(self) -> None:
        """停止所有仍在运行的追更线程，避免对话框/主窗口销毁时崩溃。

        先协作式取消（进度回调抛 DownloadCancelled，内核自然收尾、清理 .part），
        只有当它卡在网络 I/O 上超过 2 秒才 terminate 兜底。原来一上来就
        terminate，会把正在写盘的内核硬杀，留下半截 EPUB/TXT 或损坏的 ZIP 缓存。
        """
        for w in list(self._workers):
            try:
                if not w.isRunning():
                    continue
                cancel = getattr(w, "cancel", None)
                if callable(cancel):
                    cancel()
                    w.wait(2000)
                if w.isRunning():
                    w.terminate()
                    w.wait(800)
            except RuntimeError:
                # 底层 C++ 对象已销毁时忽略
                pass
        self._workers.clear()

    def closeEvent(self, event) -> None:  # noqa: N802
        # 关闭（含点 Cancel / 右上角 X / 程序退出）时先停后台线程
        self.stop_workers()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.addWidget(QLabel(tr("follow.hint")))
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "", tr("follow.book"), tr("follow.local"), tr("follow.remote"),
            tr("follow.new"), tr("follow.status")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        v.addWidget(self.table)

        self.progress = QProgressBar()
        v.addWidget(self.progress)

        row = QHBoxLayout()
        self.btn_check = QPushButton(tr("follow.check"))
        self.btn_check.clicked.connect(self._check)
        self.btn_follow = QPushButton(tr("follow.update_selected"))
        self.btn_follow.clicked.connect(self._follow_selected)
        self.btn_follow.setEnabled(False)
        row.addWidget(self.btn_check)
        row.addWidget(self.btn_follow)
        row.addStretch()
        btn_close = QPushButton(tr("dlg.cancel"))
        btn_close.clicked.connect(self.reject)
        row.addWidget(btn_close)
        v.addLayout(row)

    def _load(self) -> None:
        # 只列有 book_id 的书架书（本地导入的无 ID 无法追更）
        entries = [e for e in self.store.shelf_all() if e.get("book_id")]
        self._records: List[Dict[str, Any]] = []
        # 复合 key 与 follow_upsert 的匹配规则一致（同书多格式互不覆盖）
        follows = {(str(f.get("book_id", "")), f.get("format", "txt")): f
                   for f in self.store.follow_all()}
        from .follow_core import estimate_local_chapters
        for e in entries:
            bid = str(e["book_id"])
            fmt = e.get("format", "txt")
            f = follows.get((bid, fmt), {})
            last = int(f.get("last_chapter", 0) or 0)
            if not last:
                # 首次追更：从本地文件估算已有章数，避免重复下载整本
                last = estimate_local_chapters(str(e.get("file_path", "")), fmt)
                if last:
                    self.store.follow_upsert({
                        "book_id": bid, "format": fmt,
                        "last_chapter": last,
                        "file_path": e.get("file_path", ""),
                        "title": e.get("title", ""),
                        "source": e.get("source", ""),
                    })
            rec = {
                "book_id": bid,
                "title": e.get("title", ""),
                "author": e.get("author", ""),
                "format": fmt,
                "file_path": e.get("file_path", ""),
                "last_chapter": last,
                "source": e.get("source", ""),
            }
            self._records.append(rec)
        self._render(self._records, checked=False)

    def _render(self, rows: List[Dict[str, Any]], checked: bool) -> None:
        self.table.setRowCount(len(rows))
        for r, rec in enumerate(rows):
            has_new = rec.get("has_new", False)
            values = [
                "✓" if has_new else "",
                rec.get("title", ""),
                str(rec.get("local_total", rec.get("last_chapter", 0))),
                str(rec.get("remote_total", "—")),
                str(rec.get("new_chapters", 0)) if has_new else "—",
                rec.get("error", "") or (tr("follow.new_available") if has_new else tr("follow.no_new")),
            ]
            for c, val in enumerate(values):
                item = QTableWidgetItem(str(val))
                if c == 0:
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Checked if (has_new and checked) else Qt.Unchecked)
                self.table.setItem(r, c, item)

    def _check(self) -> None:
        if not self._records:
            QMessageBox.information(self, tr("follow.title"), tr("follow.empty"))
            return
        self.btn_check.setEnabled(False)
        self.progress.setRange(0, len(self._records))
        w = _CheckWorker(self._records, self)
        w.progress.connect(self.progress.setValue)
        def on_done(results):
            self._results = results
            self._render(results, checked=True)
            self.progress.setValue(len(results))
            self.btn_follow.setEnabled(any(r.get("has_new") for r in results))
            self.btn_check.setEnabled(True)
        w.done.connect(on_done)
        self._workers.append(w)
        w.start()

    def _follow_selected(self) -> None:
        selected: List[Dict[str, Any]] = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.checkState() == Qt.Checked and r < len(self._results):
                rec = self._results[r]
                if rec.get("has_new") and not rec.get("error"):
                    selected.append(rec)
        if not selected:
            return
        self._queue: List[Dict[str, Any]] = list(selected)
        self._idx = 0
        self.btn_follow.setEnabled(False)
        self._follow_next()

    def _follow_next(self) -> None:
        if self._idx >= len(self._queue):
            QMessageBox.information(self, tr("follow.title"), tr("follow.done"))
            self.btn_check.setEnabled(True)
            self._load()
            return
        rec = self._queue[self._idx]
        import os
        fp = str(rec.get("file_path", ""))
        if not fp or not os.path.isfile(fp):
            self.log_skip(rec, tr("follow.no_file"))
            self._idx += 1
            self._follow_next()
            return
        w = _FollowWorker(rec, fp, rec.get("format", "txt"), self)
        self.progress.setRange(0, 100)
        w.progress.connect(self.progress.setValue)
        def on_done(r):
            if r["ok"]:
                self.store.follow_upsert({
                    "book_id": rec["book_id"], "format": rec.get("format", "txt"),
                    "last_chapter": r["new_total"],
                    "file_path": r["file_path"],
                    "title": rec.get("title", ""),
                    # 必须回写书源：follow_core 按 source 选章节源，番茄书追更后
                    # 若丢失 source，下次会回退到七猫内核导致追更失败/串源。
                    "source": rec.get("source", ""),
                })
            self.log_skip(rec, r["message"])
            self._idx += 1
            self._follow_next()
        w.done.connect(on_done)
        self._workers.append(w)
        w.start()

    def log_skip(self, rec: Dict[str, Any], message: str) -> None:
        for r in range(self.table.rowCount()):
            if self.table.item(r, 1) and self.table.item(r, 1).text() == rec.get("title", ""):
                self.table.setItem(r, 5, QTableWidgetItem(message[:40]))
                break
