# -*- coding: utf-8 -*-
"""番茄小说核心内核（内置）。

本包是 GUI 层的一等核心内核，与 qimao_core.py 平级，不再作为可选插件的 vendor 依赖：

- fanqie_reader.py          官网正文读取 + woff2 字体解码 + 全形态链接/ID 归一化
- fanqie_search.py          关键词搜索（官方接口 + a_bogus 签名 + SEARCH_MAP 解码）
- fanqie_upstream/          xiaocse/Fanqie-novel-Downloader 上游实现（原样隔离，可一键同步）
- fanqie_search_upstream/   搜索签名与字体映射上游实现（原样隔离）

对上层只暴露稳定接口；插件层的 fanqie_downloader_plugin.py 只做薄适配，
把这里的目录/正文能力包装成本项目的 BookSource。
"""

from __future__ import annotations

__all__ = [
    "decode_chapter",
    "resolve_book_id",
    "resolve_short_link",
    "search_fanqie",
    "RequestHandler",
]


def __getattr__(name: str):
    """惰性导入，避免仅为取一个符号就加载字体映射大表 / 网络依赖。"""
    if name in ("decode_chapter", "resolve_book_id", "resolve_short_link"):
        from . import fanqie_reader
        return getattr(fanqie_reader, name)
    if name == "search_fanqie":
        from .fanqie_search import search_fanqie
        return search_fanqie
    if name == "RequestHandler":
        from .fanqie_upstream import RequestHandler
        return RequestHandler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
