# -*- coding: utf-8 -*-
"""统一的文件/目录选择对话框封装。

为什么需要这一层
================
PySide6 的 ``QFileDialog.getExistingDirectory`` / ``getOpenFileName`` 等静态方法
默认使用**操作系统原生对话框**。在 Windows + PyInstaller 打包环境里，原生对话框
有一个已知的坑：当主程序用 ``QApplication.setStyleSheet()`` 施加了全局 QSS 后，
原生对话框偶尔会出现「点了没反应 / 选完确定路径不变 / 直接不弹」的问题，且与
Windows 版本、DPI、主题设置相关，难以逐一适配。

统一改用 ``QFileDialog.Option.DontUseNativeDialog``——Qt 自绘对话框。它：
  * 与全局 QSS 完全兼容，不再受样式表干扰；
  * 行为在所有平台一致，打包环境稳定；
  * 功能与原生对话框完全相同（选文件/选目录/多选/过滤器）。

另外统一处理起始目录：传入的目录若不存在，回退到用户主目录，避免原生对话框
因起始目录无效而定位失败。

所有选择对话框都应通过本模块调用，不要再直接用 QFileDialog.get* 静态方法。
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from PySide6.QtWidgets import QFileDialog, QWidget

# Qt 自绘对话框选项（关键：绕开 Windows 原生对话框在打包环境的兼容问题）
_NATIVE_OFF = QFileDialog.Option.DontUseNativeDialog


def _safe_dir(start: Optional[str]) -> str:
    """返回一个存在的起始目录，避免无效路径导致对话框定位失败。"""
    if start and os.path.isdir(start):
        return start
    home = os.path.expanduser("~")
    return home if os.path.isdir(home) else os.getcwd()


def select_directory(parent: Optional[QWidget] = None,
                     caption: str = "选择目录",
                     start: Optional[str] = None) -> str:
    """选择一个目录，返回绝对路径；取消返回空串。"""
    return QFileDialog.getExistingDirectory(
        parent, caption, _safe_dir(start),
        QFileDialog.Option.ShowDirsOnly | _NATIVE_OFF,
    )


def open_file(parent: Optional[QWidget] = None,
              caption: str = "选择文件",
              start: Optional[str] = None,
              filter: str = "所有文件 (*.*)") -> str:
    """选择一个文件，返回绝对路径；取消返回空串。"""
    path, _ = QFileDialog.getOpenFileName(
        parent, caption, _safe_dir(start), filter, "", _NATIVE_OFF,
    )
    return path


def open_files(parent: Optional[QWidget] = None,
               caption: str = "选择文件",
               start: Optional[str] = None,
               filter: str = "所有文件 (*.*)") -> List[str]:
    """选择多个文件，返回绝对路径列表；取消返回空列表。"""
    paths, _ = QFileDialog.getOpenFileNames(
        parent, caption, _safe_dir(start), filter, "", _NATIVE_OFF,
    )
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
    if initial and not os.path.isdir(initial):
        # 可能是"目录 + 文件名"：只要父目录存在就原样保留（预填文件名）
        parent_dir = os.path.dirname(initial)
        if not (parent_dir and os.path.isdir(parent_dir)):
            initial = _safe_dir(start)
    elif not initial:
        initial = _safe_dir(start)
    path, _ = QFileDialog.getSaveFileName(
        parent, caption, initial, filter, "", _NATIVE_OFF,
    )
    return path
