# -*- coding: utf-8 -*-
"""文库8（轻小说文库）核心内核，本项目自研。

不依赖任何第三方开源内核：Wenku8_Flutter 的在线实现未开源（仓库内
LegacyWenkuRepository 全为 unavailableOnline 空壳），因此这里按文库8
真实站点结构从零实现，全部基于实测接口（2026-11）：

- 详情页    {BASE}/book/{id}.htm
- 目录页    {BASE}/novel/{n}/{id}/index.htm  （章节为相对链接 {cid}.htm）
- 正文页    {BASE}/novel/{n}/{id}/{cid}.htm  （正文在 #content，明文无字体混淆）
- 分类列表  {BASE}/modules/article/articlelist.php?class={c}&page={p}
- 站内搜索  多候选接口（search.php/so.php × searchkey/keyword）优先，分类页遍历兜底
- 域名      .net/.cc/.com 候选，设置页 wenku8_base_url 可固定，Cookie 域动态跟随

BASE 由 get_base_url() 动态解析（用户设置 > 探测缓存 > 候选首项），不写死单一域名。

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
    "get_base_url",
    "probe_base_url",
    "login_with_password",
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
    if name in ("get_cookie", "set_cookie", "is_logged_in",
                "get_base_url", "probe_base_url", "login_with_password"):
        from .wenku8_auth import (
            get_cookie, set_cookie, is_logged_in, get_base_url, probe_base_url,
            login_with_password,
        )
        return {
            "get_cookie": get_cookie,
            "set_cookie": set_cookie,
            "is_logged_in": is_logged_in,
            "get_base_url": get_base_url,
            "probe_base_url": probe_base_url,
            "login_with_password": login_with_password,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
