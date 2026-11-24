# -*- coding: utf-8 -*-
"""
诊断日志：把异常、API 失败、下载报错写入本地文件，便于排查问题。

- 日志目录：~/.qimao_gui/logs/
- 按天分文件：qimao_YYYY-MM-DD.log
- 单文件超过 MAX_BYTES 时轮转为 .1 / .2
- 线程安全；同时保留一个内存环形缓冲供 UI 展示最近日志
- 默认级别 INFO；可通过 set_level 调整

本模块不依赖 PySide6，核心层与插件也可安全导入。
"""

from __future__ import annotations

import os
import threading
import traceback
from collections import deque
from datetime import datetime
from typing import Deque, Dict, List, Optional

DEBUG, INFO, WARNING, ERROR = 10, 20, 30, 40
_LEVEL_NAMES = {DEBUG: "DEBUG", INFO: "INFO", WARNING: "WARN", ERROR: "ERROR"}

MAX_BYTES = 2 * 1024 * 1024   # 单文件 2MB 后轮转
KEEP_FILES = 3
_MEM_LIMIT = 300


class DiagnosticLogger:
    """线程安全的诊断日志记录器（单例见 module-level ``logger``）。"""

    def __init__(self, log_dir: Optional[str] = None) -> None:
        self.log_dir = log_dir or os.path.join(
            os.path.expanduser("~"), ".qimao_gui", "logs")
        self._level = INFO
        self._lock = threading.RLock()
        self._mem: Deque[Dict[str, str]] = deque(maxlen=_MEM_LIMIT)
        os.makedirs(self.log_dir, exist_ok=True)

    def set_level(self, level: int) -> None:
        with self._lock:
            self._level = level

    def level_name(self) -> str:
        return _LEVEL_NAMES.get(self._level, "INFO")

    def set_dir(self, log_dir: str) -> None:
        with self._lock:
            self.log_dir = log_dir
            os.makedirs(self.log_dir, exist_ok=True)

    def _today_path(self) -> str:
        return os.path.join(
            self.log_dir, f"qimao_{datetime.now():%Y-%m-%d}.log")

    def _rotate_if_needed(self, path: str) -> None:
        try:
            if os.path.getsize(path) < MAX_BYTES:
                return
            for i in range(KEEP_FILES - 1, 0, -1):
                src = f"{path}.{i - 1}" if i > 1 else path
                dst = f"{path}.{i}"
                if os.path.exists(src):
                    if os.path.exists(dst):
                        os.remove(dst)
                    os.replace(src, dst)
        except Exception:  # noqa: BLE001
            pass

    def log(self, level: int, message: str, *,
            exc: Optional[BaseException] = None,
            tag: str = "") -> None:
        if level < self._level:
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{_LEVEL_NAMES.get(level, 'INFO')}]"
        if tag:
            line += f" [{tag}]"
        line += f" {message}"
        if exc is not None:
            line += f" | {type(exc).__name__}: {exc}"
            line += "\n" + traceback.format_exc()
        with self._lock:
            self._mem.append({"time": ts, "level": _LEVEL_NAMES.get(level, "INFO"),
                              "tag": tag, "message": message})
            try:
                path = self._today_path()
                self._rotate_if_needed(path)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:  # noqa: BLE001
                pass

    # 便捷方法
    def debug(self, message: str, **kw) -> None:
        self.log(DEBUG, message, **kw)

    def info(self, message: str, **kw) -> None:
        self.log(INFO, message, **kw)

    def warning(self, message: str, **kw) -> None:
        self.log(WARNING, message, **kw)

    def error(self, message: str, **kw) -> None:
        self.log(ERROR, message, **kw)

    def exception(self, message: str, exc: BaseException, **kw) -> None:
        self.log(ERROR, message, exc=exc, **kw)

    def recent(self, limit: int = 100) -> List[Dict[str, str]]:
        with self._lock:
            items = list(self._mem)
            return items[-limit:]

    def clear_memory(self) -> None:
        with self._lock:
            self._mem.clear()

    def open_log_dir(self) -> None:
        """在文件管理器中打开日志目录（跨平台，失败静默）。"""
        try:
            import subprocess
            import sys
            if sys.platform == "win32":
                os.startfile(self.log_dir)  # noqa
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self.log_dir])
            else:
                subprocess.Popen(["xdg-open", self.log_dir])
        except Exception:  # noqa: BLE001
            pass


logger = DiagnosticLogger()


def log_error(message: str, exc: Optional[BaseException] = None, tag: str = "") -> None:
    """全局错误记录快捷函数。"""
    logger.error(message, exc=exc, tag=tag)
