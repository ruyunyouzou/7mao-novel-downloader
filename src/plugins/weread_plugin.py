# -*- coding: utf-8 -*-
"""
微信读书插件：把「微信读书」标签页接入七猫小说下载器。

挂载点：
- register_tabs：注册微信读书页面到左侧导航
- on_download_complete：下载完成后若用户开启了自动导入，可选推送到微信读书
                        （默认关闭，避免意外上传；在页面里手动导入更可控）

接入的三类能力见 gui/weread_core.py 与 gui/weread_login.py：
- 官方 Agent Gateway（wrk- API Key）：书架 / 阅读进度同步
- 微信扫码登录：获取移动端登录态，用于本地书传书
- 网页 Cookie 传书：扫码登录的备用方案
"""

from typing import Any, List

from gui.plugin.base import Plugin, PluginMeta
from gui.views.weread_view import WereadView
from gui.views.notebook_view import NotebookView


class WeReadPlugin(Plugin):
    meta = PluginMeta(
        name="微信读书接入",
        version="1.0.0",
        author="qimao-gui",
        description=(
            "接入微信读书：API Key 同步书架与阅读进度；扫码登录或网页 Cookie 传书导入本地小说。"
            "凭据仅保存在本机。"
        ),
    )

    def register_tabs(self, main_window: Any) -> List[Any]:
        """注册微信读书标签页。"""
        store = getattr(main_window, "store", None)
        if store is None:
            self.log("无法注册微信读书页：主窗口未提供数据存储")
            return []
        self.view = WereadView(store, main_window)
        self.view.setWindowTitle("微信读书")
        # 让主窗口持有引用，便于书架右键「导入到微信读书」直接调用
        main_window.weread_view = self.view

        # 笔记本页（复刻微信读书笔记界面）
        self.notebook_view = NotebookView(store, main_window)
        self.notebook_view.setWindowTitle("笔记")
        main_window.notebook_view = self.notebook_view
        self.log("微信读书页与笔记页已接入")
        return [self.view, self.notebook_view]
