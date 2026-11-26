# -*- coding: utf-8 -*-
"""
功能说明插件：注册「功能说明」占位页。

说明番茄小说下载器有、但七猫版未接入的功能及原因，不做无法工作的假功能。
"""

import os
import sys
from typing import Any, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.plugin.base import Plugin, PluginMeta, declare_nav
from gui.views.limitations_view import LimitationsView


class LimitationsPlugin(Plugin):
    meta = PluginMeta(
        name="功能说明",
        version="1.0.0",
        author="qimao-gui",
        description="说明暂未接入的番茄功能及其原因与接入路径。",
    )

    def register_tabs(self, main_window: Any) -> List[Any]:
        view = LimitationsView(main_window)
        view.setWindowTitle("功能说明")
        declare_nav(view, "nav.limitations", "ℹ")
        main_window.limitations_view = view
        self.log("功能说明页已接入")
        return [view]
