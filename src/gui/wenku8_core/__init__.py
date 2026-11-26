# -*- coding: utf-8 -*-
"""文库8（轻小说文库）核心内核，本项目自研。

不依赖任何第三方开源内核：Wenku8_Flutter 的在线实现未开源（仓库内
LegacyWenkuRepository 全为 unavailableOnline 空壳），因此这里按文库8
真实站点结构从零实现，全部基于实测接口（2026-11）：

- 详情页    https://www.wenku8.cc/book/{id}.htm
- 目录页    https://www.wenku8.cc/novel/{n}/{id}/index.htm  （章节为相对链接 {cid}.htm）
- 正文页    https://www.wenku8.cc/novel/{n}/{id}/{cid}.htm  （正文在 #content，明文无字体混淆）
- 分类列表  https://www.wenku8.cc/modules/article/articlelist.php?class={c}&page={p}
- 站内搜索  so.php 被 WAF 拦截（403），故搜索走「分类页抓取 + 本地过滤」

全站 GBK 编码；正文无 PUA / woff 字体混淆，无需字体解码。

对上层只暴露稳定接口；插件层 wenku8_downloader_plugin.py 做薄适配，
把这里的目录/正文能力包装成本项目的 BookSource，复用 build_txt 全部导出能力。
"""

from __future__ import annotations

__all__ = [
    "resolve_book_id",
    "search_wenku8",
    "fetch_detail",
    "fetch_catalog",
    "fetch_chapter_text",
    "fetch_book_meta",
    "get_cookie",
    "set_cookie",
    "is_logged_in",
    "make_session",
]


def __getattr__(name: str):
    """惰性导入，避免启动期加载网络依赖。"""
    if name == "resolve_book_id":
        from .wenku8_reader import resolve_book_id
        return resolve_book_id
    if name == "search_wenku8":
        from .wenku8_search import search_wenku8
        return search_wenku8
    if name in ("fetch_detail", "fetch_catalog", "fetch_chapter_text",
                "fetch_book_meta", "make_session"):
        from . import wenku8_reader
        return getattr(wenku8_reader, name)
    if name in ("get_cookie", "set_cookie", "is_logged_in"):
        from .wenku8_auth import (
            get_cookie, set_cookie, is_logged_in,
        )
        return {
            "get_cookie": get_cookie,
            "set_cookie": set_cookie,
            "is_logged_in": is_logged_in,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
