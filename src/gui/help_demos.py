# -*- coding: utf-8 -*-
"""说明书演示图映射表。

键为说明书 ``使用说明书.md`` 中的小节标题（三级标题，如 ``5.1 搜索``），
值为演示图文件名（位于 ``assets/demo/``）。HelpView 在渲染每节正文时，
按标题查表，把图片以「缩略图 + 点击放大」的形式追加到正文末尾。

图片不存在时静默跳过，不影响说明书正常阅读。打包时 ``assets`` 目录
由 build.spec 整体收集，故 ``assets/demo`` 会随包分发。
"""
from __future__ import annotations

from typing import Dict

# 章节标题（不含 markdown 的 # 前缀）-> 演示图文件名
DEMOS: Dict[str, str] = {
    # ---- 第 5 节 各功能模块 ----
    "5.1 搜索": "preview_search_light.png",
    "5.2 书架": "shelf.png",
    "5.3 阅读": None,  # 阅读页留空，由第 9 节专门演示
    "5.4 任务队列": "queue.png",
    "5.5 历史记录": "history.png",
    "5.6 统计": "stats.png",
    "5.7 设置": "preview_settings_i18n.png",
    "5.8 功能说明": "preview_help.png",
    "5.9 使用说明": "preview_help_light.png",
    "5.10 Calibre / 微信读书 / 笔记": "preview_weread_dashboard.png",
    "5.10.1 微信读书扫码登录": "preview_weread_integration.png",
    "5.11 插件页": "plugins.png",

    # ---- 第 6 节 设置页 ----
    "6.1 下载设置": "preview_download_font_color.png",
    "6.2 主题与外观": "preview_bg_settings_light.png",
    "6.2.1 部件独立调节（国徽 / 文字 / 功能背景）": "preview_title_colors.png",
    "6.4 背景设置（色块式，可编辑）": "preview_flow_light.png",
    "6.5 EPUB 排版": "preview_fonts_mashan_zhimang.png",

    # ---- 第 7 节 主题/背景/颜色 ----
    "7.3 动态光效": "preview_flow_dark.png",
    "7.4 阅读纸张主题": "preview_dialogue_light.png",

    # ---- 第 8 节 下载链路 ----
    "8.1 支持格式": "preview_detail_fixed.png",

    # ---- 第 9 节 阅读器 ----
    "9.0 整体布局（重排版）": "preview_dialogue_dark.png",

    # ---- 第 10 节 EPUB 排版 ----
    "10.3 人物对话高亮": "preview_dialogue_light.png",
    "10.4 章首题图 / 章末尾图": "preview_title_colors.png",

    # ---- 第 11 节 插件系统 ----
    "11.3 内置插件": "preview_help.png",

    # ---- 第 15 节 故障排查 ----
    "15.7 浅色主题下某个页面显示异常": "preview_limitations_light.png",

    # ---- 第 17 节 功能边界 ----
    "17. 功能边界与已知取舍": "preview_limitations.png",
}

# 值为 None 表示该项明确不配图；查表不存在也视为不配图。
def demo_image(section_title: str) -> str:
    """返回小节对应的演示图文件名；无演示图时返回空串。"""
    name = DEMOS.get(section_title.strip())
    return name or ""
