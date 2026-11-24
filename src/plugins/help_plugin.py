# -*- coding: utf-8 -*-
"""
使用说明插件：注册「使用说明」导航页。

页面读取项目根目录的《使用说明书.md》，支持目录跳转、关键词搜索、
打开外部 Markdown 与复制当前章节。
"""

import os
import sys
from typing import Any, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.plugin.base import Plugin, PluginMeta
from gui.views.help_view import HelpView


class HelpPlugin(Plugin):
    meta = PluginMeta(
        name="使用说明",
        version="1.0.0",
        author="qimao-gui",
        description="软件内置说明书：项目结构、模块作用、设置项与排障指南。",
    )

    def register_tabs(self, main_window: Any) -> List[Any]:
        view = HelpView(main_window)
        view.setWindowTitle("使用说明")
        main_window.help_view = view
        self.log("使用说明页已接入")
        return [view]
