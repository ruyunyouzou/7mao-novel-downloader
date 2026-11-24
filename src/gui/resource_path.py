# -*- coding: utf-8 -*-
"""
资源路径适配：兼容「源码运行」与「PyInstaller 打包」两种模式。

- 源码运行：资源在项目根目录（src 的上级），如 <项目>/assets、<项目>/main.ico、<项目>/src/plugins
- PyInstaller 打包：资源被收集到临时解压目录 sys._MEIPASS，或外置在 exe 同级目录

规则：
  1) 打包后优先用 exe 同级目录（external，便于用户替换字体/插件）；
  2) 同级没有则用 sys._MEIPASS（内嵌资源）；
  3) 源码模式用基于 __file__ 的项目根目录。
"""

from __future__ import annotations

import os
import sys
from typing import Union


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包环境中。"""
    return bool(getattr(sys, "frozen", False))


def app_root() -> str:
    """返回资源根目录（源码模式=项目根；打包模式=exe 目录或 _MEIPASS）。"""
    if is_frozen():
        # 打包后 exe 所在目录优先（外置资源可更新）
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        # 用一个"探针"判断外置资源目录是否存在：assets 或 src/plugins
        if os.path.isdir(os.path.join(exe_dir, "assets")) or \
                os.path.isdir(os.path.join(exe_dir, "src", "plugins")):
            return exe_dir
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass and os.path.isdir(meipass):
            return meipass
        return exe_dir
    # 源码模式：<项目根>/src/gui/resource_path.py -> 上溯三级到项目根
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def resource_path(*parts: Union[str, os.PathLike]) -> str:
    """拼接资源绝对路径。例：resource_path('assets', 'fonts')。"""
    return os.path.join(app_root(), *[str(p) for p in parts])


def plugin_dir() -> str:
    """插件目录：源码=<项目>/src/plugins；打包=exe 同级 src/plugins 或 _MEIPASS 内。"""
    if is_frozen():
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        external = os.path.join(exe_dir, "src", "plugins")
        if os.path.isdir(external):
            return external
        return os.path.join(app_root(), "src", "plugins")
    return os.path.join(app_root(), "src", "plugins")
