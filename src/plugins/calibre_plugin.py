# -*- coding: utf-8 -*-
"""Calibre 插件：把 Calibre 书库管理页接入左侧导航。

功能：检测本机 Calibre、读取书库、用 ebook-edit 编辑 EPUB、格式转换、加入书库。
"""

import os
import sys

# 保证能 import 到 gui 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.plugin.base import Plugin, PluginMeta, declare_nav
from gui.views.calibre_view import CalibreView


class CalibrePlugin(Plugin):
    meta = PluginMeta(
        name="Calibre 书库",
        version="1.0.0",
        author="星隅",
        description="接入本机 Calibre：随时编辑 EPUB、格式转换、书库管理。",
    )

    def on_load(self, context) -> None:
        self.context = context

    def register_tabs(self, main_window):
        store = getattr(main_window, "store", None)
        if store is None:
            self.log("无法注册 Calibre 页：主窗口未提供数据存储")
            return []
        self.view = CalibreView(store, main_window)
        # 主窗口持有引用，便于书架右键「用 Calibre 编辑」直接调用
        main_window.calibre_view = self.view
        self.view.setWindowTitle("Calibre 书库")
        declare_nav(self.view, "nav.calibre", "📖")
        self.log("Calibre 页已接入")
        return [self.view]
