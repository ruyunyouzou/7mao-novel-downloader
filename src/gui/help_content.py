# -*- coding: utf-8 -*-
"""内置说明书文本源。

优先读取项目根目录的 ``使用说明书.md``；打包时该文件由 build.spec 收集到
资源根目录。若外部文件缺失，则返回简短兜底说明，保证帮助页永远可打开。
"""

from __future__ import annotations

import os

from .resource_path import resource_path


def manual_path() -> str:
    """返回说明书文件路径；不存在时返回空串。"""
    candidates = [
        resource_path("使用说明书.md"),
        resource_path("docs", "使用说明书.md"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""


def load_manual() -> str:
    """读取说明书全文。读取失败时返回兜底 Markdown。"""
    path = manual_path()
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            pass
    return """# GUI-novel下载器使用说明

内置说明书文件未找到。源码运行时请确认项目根目录存在 `使用说明书.md`；
打包运行请确认打包配置已收集该文件。

你仍然可以通过左侧导航使用搜索、书架、阅读、任务队列、历史、统计、设置、
功能说明等模块。各页面均有按钮提示与状态栏日志。
"""
