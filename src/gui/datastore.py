# -*- coding: utf-8 -*-
"""
本地数据持久化：书架、任务队列、历史记录。

数据以 JSON 文件保存在用户目录 ``~/.qimao_gui/`` 下，读写带线程锁，
每次变更自动落盘。工具类方法均返回浅拷贝，避免外部修改内部状态。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from copy import deepcopy
from typing import Any, Dict, List, Optional

from .logutil import logger


def _now() -> str:
    """当前时间的可读字符串。"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


class DataStore:
    """统一管理三类持久化数据。"""

    def __init__(self, base_dir: Optional[str] = None) -> None:
        self.base_dir = base_dir or os.path.join(os.path.expanduser("~"), ".qimao_gui")
        os.makedirs(self.base_dir, exist_ok=True)
        self.shelf_path = os.path.join(self.base_dir, "shelf.json")
        self.queue_path = os.path.join(self.base_dir, "queue.json")
        self.history_path = os.path.join(self.base_dir, "history.json")
        self.progress_path = os.path.join(self.base_dir, "progress.json")
        self.read_history_path = os.path.join(self.base_dir, "read_history.json")
        self.settings_path = os.path.join(self.base_dir, "settings.json")
        # --- 扩展数据（增量追更/排版预设/搜索历史/书签/追更记录）---
        self.presets_path = os.path.join(self.base_dir, "epub_presets.json")
        self.search_history_path = os.path.join(self.base_dir, "search_history.json")
        self.bookmarks_path = os.path.join(self.base_dir, "bookmarks.json")
        self.follow_path = os.path.join(self.base_dir, "follow.json")
        self._lock = threading.RLock()

        self._shelf: List[Dict[str, Any]] = self._load(self.shelf_path, [])
        self._queue: List[Dict[str, Any]] = self._load(self.queue_path, [])
        self._history: List[Dict[str, Any]] = self._load(self.history_path, [])
        self._progress: List[Dict[str, Any]] = self._load(self.progress_path, [])
        self._read_history: List[Dict[str, Any]] = self._load(self.read_history_path, [])
        self._presets: List[Dict[str, Any]] = self._load(self.presets_path, [])
        self._search_history: List[Dict[str, Any]] = self._load(self.search_history_path, [])
        self._bookmarks: List[Dict[str, Any]] = self._load(self.bookmarks_path, [])
        self._follow: List[Dict[str, Any]] = self._load(self.follow_path, [])
        # 运行时任务状态不持久化（暂停/进度等），只持久化任务描述
        for t in self._queue:
            if t.get("status") in ("downloading", "waiting", "paused"):
                t["status"] = "interrupted"
                t["progress"] = 0

    # ------------------------------------------------------------------ 通用

    @staticmethod
    def _load(path: str, default: Any) -> Any:
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # 按默认值的类型校验：list 对 list，dict 对 dict
                if isinstance(default, dict) and isinstance(data, dict):
                    return data
                if isinstance(default, list) and isinstance(data, list):
                    return data
                # 类型不匹配：可能是旧版本残留或损坏，记录以便排查
                logger.warning(
                    f"数据结构不符，已使用默认值: {os.path.basename(path)} "
                    f"(期望 {type(default).__name__}, 实际 {type(data).__name__})",
                    tag="datastore")
        except FileNotFoundError:
            pass  # 首次运行，文件尚未创建，属正常
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"读取数据失败，已使用默认值: {os.path.basename(path)}",
                           exc=exc, tag="datastore")
        return deepcopy(default)

    @staticmethod
    def _atomic_write(path: str, data: Any) -> bool:
        """原子写入 JSON：先写同目录临时文件再 os.replace，避免写入中断导致文件损坏。

        直接 ``open(path, 'w')`` 在写入过程中若进程退出/断电，会留下半截 JSON，
        下次启动 _load 解析失败并用默认值覆盖，造成书架/历史永久丢失。
        同目录临时文件保证 replace 在同一文件系统内，是原子操作。
        """
        tmp = f"{path}.tmp.{os.getpid()}"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error(f"写入数据失败: {os.path.basename(path)}",
                         exc=exc, tag="datastore")
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
            return False

    def _save(self, path: str, data: List[Dict[str, Any]]) -> bool:
        return self._atomic_write(path, data)

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]

    # ------------------------------------------------------------------ 书架

    def shelf_add(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """添加书籍到书架；若同一 book_id+format 已存在则更新。"""
        with self._lock:
            entry = dict(entry)
            entry.setdefault("id", self.new_id())
            entry.setdefault("added_at", _now())
            for i, existing in enumerate(self._shelf):
                if existing.get("book_id") == entry.get("book_id") and existing.get("format") == entry.get("format"):
                    entry["id"] = existing["id"]
                    entry["added_at"] = existing["added_at"]
                    self._shelf[i] = entry
                    self._save(self.shelf_path, self._shelf)
                    return deepcopy(entry)
            self._shelf.append(entry)
            self._save(self.shelf_path, self._shelf)
            return deepcopy(entry)

    def shelf_remove(self, entry_id: str) -> bool:
        with self._lock:
            before = len(self._shelf)
            self._shelf = [e for e in self._shelf if e.get("id") != entry_id]
            if len(self._shelf) != before:
                self._save(self.shelf_path, self._shelf)
                return True
            return False

    def shelf_update(self, entry_id: str, changes: Dict[str, Any]) -> bool:
        with self._lock:
            for e in self._shelf:
                if e.get("id") == entry_id:
                    e.update(changes)
                    self._save(self.shelf_path, self._shelf)
                    return True
            return False

    def shelf_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return deepcopy(self._shelf)

    # ------------------------------------------------------------------ 任务队列

    def queue_add(self, task: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            task = dict(task)
            task.setdefault("id", self.new_id())
            task.setdefault("created_at", _now())
            task.setdefault("status", "waiting")
            task.setdefault("progress", 0)
            self._queue.append(task)
            self._save(self.queue_path, self._queue)
            return deepcopy(task)

    def queue_update(self, task_id: str, changes: Dict[str, Any]) -> bool:
        with self._lock:
            for t in self._queue:
                if t.get("id") == task_id:
                    t.update(changes)
                    self._save(self.queue_path, self._queue)
                    return True
            return False

    def queue_remove(self, task_id: str) -> bool:
        with self._lock:
            before = len(self._queue)
            self._queue = [t for t in self._queue if t.get("id") != task_id]
            if len(self._queue) != before:
                self._save(self.queue_path, self._queue)
                return True
            return False

    def queue_clear_finished(self) -> int:
        """清除已完成/失败/取消的任务，返回清除数量。"""
        with self._lock:
            before = len(self._queue)
            self._queue = [t for t in self._queue if t.get("status") not in ("done", "failed", "cancelled")]
            removed = before - len(self._queue)
            if removed:
                self._save(self.queue_path, self._queue)
            return removed

    def queue_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return deepcopy(self._queue)

    def queue_find(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for t in self._queue:
                if t.get("id") == task_id:
                    return deepcopy(t)
            return None

    # ------------------------------------------------------------------ 历史记录

    def history_add(self, record: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            record = dict(record)
            record.setdefault("id", self.new_id())
            record.setdefault("time", _now())
            self._history.insert(0, record)  # 最新在前
            if len(self._history) > 1000:
                self._history = self._history[:1000]
            self._save(self.history_path, self._history)
            return deepcopy(record)

    def history_remove(self, record_id: str) -> bool:
        with self._lock:
            before = len(self._history)
            self._history = [h for h in self._history if h.get("id") != record_id]
            if len(self._history) != before:
                self._save(self.history_path, self._history)
                return True
            return False

    def history_clear(self) -> None:
        with self._lock:
            self._history = []
            self._save(self.history_path, self._history)

    def history_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return deepcopy(self._history)

    # ------------------------------------------------------------------ 阅读进度

    def progress_get(self, book_key: str) -> Optional[Dict[str, Any]]:
        """按书籍标识（book_id）读取阅读进度。"""
        with self._lock:
            for p in self._progress:
                if p.get("book_id") == book_key:
                    return deepcopy(p)
            return None

    def progress_save(self, book_key: str, chapter_index: int, scroll: int = 0,
                      percent: float = 0.0, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """保存/更新阅读进度。"""
        with self._lock:
            rec: Dict[str, Any] = {
                "book_id": book_key,
                "chapter_index": chapter_index,
                "scroll": scroll,
                "percent": percent,
                "updated_at": _now(),
            }
            if extra:
                rec.update(extra)
            for i, p in enumerate(self._progress):
                if p.get("book_id") == book_key:
                    rec["id"] = p.get("id", self.new_id())
                    self._progress[i] = rec
                    self._save(self.progress_path, self._progress)
                    return deepcopy(rec)
            rec["id"] = self.new_id()
            self._progress.append(rec)
            self._save(self.progress_path, self._progress)
            return deepcopy(rec)

    def progress_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return deepcopy(self._progress)

    # ------------------------------------------------------------------ 阅读历史

    def read_history_add(self, record: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            record = dict(record)
            record.setdefault("id", self.new_id())
            record.setdefault("time", _now())
            self._read_history.insert(0, record)
            if len(self._read_history) > 1000:
                self._read_history = self._read_history[:1000]
            self._save(self.read_history_path, self._read_history)
            return deepcopy(record)

    def read_history_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return deepcopy(self._read_history)

    def read_history_clear(self) -> None:
        with self._lock:
            self._read_history = []
            self._save(self.read_history_path, self._read_history)

    # ------------------------------------------------------------------ 设置

    def settings_get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            data = self._load(self.settings_path, {})
            if isinstance(data, dict):
                return data.get(key, default)
            return default

    def settings_set(self, key: str, value: Any) -> None:
        with self._lock:
            data = self._load(self.settings_path, {})
            if not isinstance(data, dict):
                data = {}
            data[key] = value
            self._atomic_write(self.settings_path, data)

    def files(self) -> List[str]:
        """返回所有持久化文件名（供备份/恢复使用）。"""
        return [
            os.path.basename(self.shelf_path),
            os.path.basename(self.queue_path),
            os.path.basename(self.history_path),
            os.path.basename(self.progress_path),
            os.path.basename(self.read_history_path),
            os.path.basename(self.settings_path),
            os.path.basename(self.presets_path),
            os.path.basename(self.search_history_path),
            os.path.basename(self.bookmarks_path),
            os.path.basename(self.follow_path),
        ]

    # ------------------------------------------------------------ EPUB 排版预设

    def preset_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return deepcopy(self._presets)

    def preset_save(self, preset: Dict[str, Any]) -> Dict[str, Any]:
        """保存预设（按 name 覆盖）。"""
        with self._lock:
            preset = dict(preset)
            preset.setdefault("id", self.new_id())
            preset.setdefault("updated_at", _now())
            name = str(preset.get("name", "")).strip() or "未命名方案"
            preset["name"] = name
            for i, p in enumerate(self._presets):
                if p.get("name") == name:
                    preset["id"] = p.get("id", preset["id"])
                    self._presets[i] = preset
                    self._save(self.presets_path, self._presets)
                    return deepcopy(preset)
            self._presets.append(preset)
            self._save(self.presets_path, self._presets)
            return deepcopy(preset)

    def preset_remove(self, preset_id: str) -> bool:
        with self._lock:
            before = len(self._presets)
            self._presets = [p for p in self._presets if p.get("id") != preset_id]
            if len(self._presets) != before:
                self._save(self.presets_path, self._presets)
                return True
            return False

    # ------------------------------------------------------------ 搜索历史

    def search_history_all(self, limit: int = 30) -> List[str]:
        with self._lock:
            return [str(h.get("keyword", "")) for h in self._search_history[:limit]
                    if h.get("keyword")]

    def search_history_add(self, keyword: str) -> None:
        keyword = str(keyword or "").strip()
        if not keyword:
            return
        with self._lock:
            self._search_history = [h for h in self._search_history
                                    if h.get("keyword") != keyword]
            self._search_history.insert(0, {"keyword": keyword, "time": _now()})
            if len(self._search_history) > 100:
                self._search_history = self._search_history[:100]
            self._save(self.search_history_path, self._search_history)

    def search_history_clear(self) -> None:
        with self._lock:
            self._search_history = []
            self._save(self.search_history_path, self._search_history)

    # ------------------------------------------------------------ 书签

    def bookmark_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return deepcopy(self._bookmarks)

    def bookmark_add(self, book: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            bid = str(book.get("id", ""))
            for b in self._bookmarks:
                if b.get("id") == bid:
                    return deepcopy(b)
            rec = {
                "id": bid,
                "title": book.get("title", ""),
                "author": book.get("author", ""),
                "cover": book.get("cover", ""),
                "added_at": _now(),
            }
            self._bookmarks.insert(0, rec)
            self._save(self.bookmarks_path, self._bookmarks)
            return deepcopy(rec)

    def bookmark_remove(self, book_id: str) -> bool:
        with self._lock:
            before = len(self._bookmarks)
            self._bookmarks = [b for b in self._bookmarks if b.get("id") != book_id]
            if len(self._bookmarks) != before:
                self._save(self.bookmarks_path, self._bookmarks)
                return True
            return False

    def bookmark_has(self, book_id: str) -> bool:
        with self._lock:
            return any(b.get("id") == book_id for b in self._bookmarks)

    # ------------------------------------------------------------ 追更记录

    def follow_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return deepcopy(self._follow)

    def follow_upsert(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        """记录/更新某本书的追更状态：last_chapter / file_path / format。"""
        with self._lock:
            rec = dict(rec)
            bid = str(rec.get("book_id", ""))
            rec.setdefault("updated_at", _now())
            for i, f in enumerate(self._follow):
                if f.get("book_id") == bid and f.get("format") == rec.get("format"):
                    rec["id"] = f.get("id", self.new_id())
                    self._follow[i] = rec
                    self._save(self.follow_path, self._follow)
                    return deepcopy(rec)
            rec["id"] = self.new_id()
            self._follow.append(rec)
            self._save(self.follow_path, self._follow)
            return deepcopy(rec)

    def follow_remove(self, follow_id: str) -> bool:
        with self._lock:
            before = len(self._follow)
            self._follow = [f for f in self._follow if f.get("id") != follow_id]
            if len(self._follow) != before:
                self._save(self.follow_path, self._follow)
                return True
            return False

    # ------------------------------------------------------------------ 统计

    def stats(self) -> Dict[str, int]:
        with self._lock:
            successes = sum(1 for h in self._history if h.get("success"))
            return {
                "shelf": len(self._shelf),
                "queue_active": sum(1 for t in self._queue if t.get("status") in ("waiting", "downloading", "paused")),
                "history": len(self._history),
                "history_success": successes,
                "history_failed": len(self._history) - successes,
                "reading": len(self._progress),
                "read_history": len(self._read_history),
                "bookmarks": len(self._bookmarks),
                "presets": len(self._presets),
                "follow": len(self._follow),
            }
