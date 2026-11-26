# -*- coding: utf-8 -*-
"""
更新日志插件：注册「更新日志」导航页。

页面本地读取 update-manifest.json 的 notes 打底，后台联网补充 GitHub
Releases 历史；顶部提供「检查更新」按钮，复用内置更新四镜像渠道。
"""

import os
import sys
from typing import Any, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.plugin.base import Plugin, PluginMeta, declare_nav
from gui.views.changelog_view import ChangelogView


class ChangelogPlugin(Plugin):
    meta = PluginMeta(
        name="更新日志",
        version="1.0.0",
        author="qimao-gui",
        description="按版本展示功能更新记录：本地 notes + 联网历史 + 检查更新。",
    )

    def register_tabs(self, main_window: Any) -> List[Any]:
        view = ChangelogView(main_window)
        view.setWindowTitle("更新日志")
        declare_nav(view, "nav.changelog", "📜")
        main_window.changelog_view = view
        self.log("更新日志页已接入")
        return [view]
