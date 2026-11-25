# -*- coding: utf-8 -*-
"""统一的文件/目录选择对话框封装。

默认使用**操作系统原生对话框**（Windows 上即资源管理器同款）：中文、深浅主题
跟随系统、左侧带「此电脑 / 盘符 / 快速访问」导航，与日常操作习惯一致。

历史背景
========
早期版本因 PyInstaller + 全局 QSS 环境下原生对话框偶发「点了没反应 / 选完路径
不变 / 不弹」的问题，统一改用 ``DontUseNativeDialog``（Qt 自绘）。自绘对话框虽
稳定，但观感是老式英文 Qt 界面、起始目录只能落在用户主目录（满屏隐藏文件夹），
与用户日常使用的资源管理器割裂。现按用户要求改回原生对话框。

降级开关：若打包后原生对话框再次出现兼容问题，设置环境变量
``QIMAO_FORCE_QT_DIALOG=1`` 即可切回 Qt 自绘对话框，无需改代码。

起始目录策略
============
1. 显式传入且存在的目录优先；
2. 否则用本进程内记住的「上次选择目录」（所有选择对话框共享）；
3. 都没有时传空串，交给系统决定——Windows 原生对话框会打开「此电脑 / 上次
   访问位置」，正是用户习惯的视图，而不是主目录里一堆隐藏文件夹。

所有选择对话框都应通过本模块调用，不要再直接用 QFileDialog.get* 静态方法。
"""

from __future__ import annotations

import os
from typing import List, Optional

from PySide6.QtWidgets import QFileDialog, QWidget

# 降级开关：设 QIMAO_FORCE_QT_DIALOG=1 切回 Qt 自绘对话框（打包兼容应急）
_FORCE_QT = os.environ.get("QIMAO_FORCE_QT_DIALOG", "").strip() in ("1", "true", "yes")

# 原生模式下不附加任何自绘 flag；应急时附加 DontUseNativeDialog
_NATIVE_FLAGS: QFileDialog.Option = (
    QFileDialog.Option.DontUseNativeDialog if _FORCE_QT else QFileDialog.Option(0)
)

# 本进程内记住的上次选择目录（所有选择对话框共享）
_last_dir: str = ""
# 可选的持久化 store（由主窗口注入）；注入后上次目录跨重启仍生效
_store = None
_LAST_DIR_KEY = "last_dialog_dir"


def configure_store(store) -> None:
    """注入 DataStore，使「上次目录」持久化到设置文件（跨重启生效）。"""
    global _store, _last_dir
    _store = store
    try:
        saved = str(store.settings_get(_LAST_DIR_KEY, "") or "")
        if saved and os.path.isdir(saved):
            _last_dir = saved
    except Exception:  # noqa: BLE001
        pass


def _safe_dir(start: Optional[str]) -> str:
    """返回起始目录：显式值 > 上次目录 > 空串（交给系统决定）。"""
    if start and os.path.isdir(start):
        return start
    if _last_dir and os.path.isdir(_last_dir):
        return _last_dir
    # 原生对话框传空串会打开系统默认位置（此电脑 / 上次位置），
    # 比强制落到用户主目录露出大量隐藏文件夹更符合习惯。
    return ""


def _remember(path: str) -> None:
    """记住本次选择的目录，供后续对话框复用；有 store 时一并持久化。"""
    global _last_dir
    if not path:
        return
    d = path if os.path.isdir(path) else os.path.dirname(path)
    if not (d and os.path.isdir(d)):
        return
    _last_dir = d
    if _store is not None:
        try:
            _store.settings_set(_LAST_DIR_KEY, d)
        except Exception:  # noqa: BLE001
            pass


def select_directory(parent: Optional[QWidget] = None,
                     caption: str = "选择目录",
                     start: Optional[str] = None) -> str:
    """选择一个目录，返回绝对路径；取消返回空串。"""
    path = QFileDialog.getExistingDirectory(
        parent, caption, _safe_dir(start),
        QFileDialog.Option.ShowDirsOnly | _NATIVE_FLAGS,
    )
    if path:
        _remember(path)
    return path


def open_file(parent: Optional[QWidget] = None,
              caption: str = "选择文件",
              start: Optional[str] = None,
              filter: str = "所有文件 (*.*)") -> str:
    """选择一个文件，返回绝对路径；取消返回空串。"""
    path, _ = QFileDialog.getOpenFileName(
        parent, caption, _safe_dir(start), filter, "", _NATIVE_FLAGS,
    )
    if path:
        _remember(path)
    return path


def open_files(parent: Optional[QWidget] = None,
               caption: str = "选择文件",
               start: Optional[str] = None,
               filter: str = "所有文件 (*.*)") -> List[str]:
    """选择多个文件，返回绝对路径列表；取消返回空列表。"""
    paths, _ = QFileDialog.getOpenFileNames(
        parent, caption, _safe_dir(start), filter, "", _NATIVE_FLAGS,
    )
    if paths:
        _remember(paths[0])
    return list(paths)


def save_file(parent: Optional[QWidget] = None,
              caption: str = "保存文件",
              start: Optional[str] = None,
              filter: str = "所有文件 (*.*)") -> str:
    """选择保存路径，返回绝对路径；取消返回空串。

    ``start`` 既可以是目录，也可以是带默认文件名的完整路径（如
    ``~/backup.zip``）——保存对话框允许这样传以预填文件名。此时只校验其
    父目录存在即可。
    """
    initial = start or ""
    base = _safe_dir(None)  # 上次目录或空串（交给系统）
    if not initial:
        initial = base
    elif not os.path.isdir(initial):
        parent_dir = os.path.dirname(initial)
        if parent_dir and os.path.isdir(parent_dir):
            # "目录 + 文件名"完整路径：原样保留以预填文件名
            pass
        elif not parent_dir:
            # 纯文件名（如 "下载历史.csv"）：拼到兜底目录（若为空则主目录），保留文件名
            fallback = base if base else os.path.expanduser("~")
            initial = os.path.join(fallback, initial)
        else:
            # 父目录不存在：回退到兜底目录
            initial = base
    path, _ = QFileDialog.getSaveFileName(
        parent, caption, initial, filter, "", _NATIVE_FLAGS,
    )
    if path:
        _remember(path)
    return path
