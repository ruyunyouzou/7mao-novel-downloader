# -*- coding: utf-8 -*-
"""
元数据刮削：从七猫 API 批量重新拉取封面/作者/简介，更新本地书架条目。

对每条书架记录（需含 book_id），调用 v4 详情接口，把 title/author/intro/
img_url(封面)/words_num/tags 写回书架。保留本地已有字段（file_path/format 等）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .api import get_book_info
from .logutil import logger


def scrape_one(entry: Dict[str, Any], fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """刮削单条书架记录，返回更新后的 changes 字典。"""
    bid = str(entry.get("book_id", ""))
    fb = fallback or {
        "title": entry.get("title", ""),
        "author": entry.get("author", ""),
        "cover": entry.get("cover", ""),
        "intro": entry.get("intro", ""),
    }
    try:
        info = get_book_info(bid, fallback=fb)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"元数据刮削失败 {bid}", exc=exc, tag="scrape")
        return {"_error": str(exc)}
    changes: Dict[str, Any] = {}
    if info.get("title"):
        changes["title"] = info["title"]
    if info.get("author"):
        changes["author"] = info["author"]
    if info.get("intro"):
        changes["intro"] = info["intro"]
    cover = info.get("img_url") or info.get("cover")
    if cover:
        changes["cover"] = cover
    if info.get("words_num"):
        changes["words_num"] = info["words_num"]
    if info.get("tags"):
        changes["tags"] = info["tags"]
    return changes


def scrape_many(entries: List[Dict[str, Any]], store,
                progress_cb: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """批量刮削并写回 DataStore。返回统计。"""
    ok, fail = 0, 0
    total = len(entries)
    for i, entry in enumerate(entries, 1):
        changes = scrape_one(entry)
        if "_error" in changes:
            fail += 1
        else:
            if changes:
                store.shelf_update(entry.get("id", ""), changes)
            ok += 1
        if progress_cb:
            progress_cb(i, total, entry.get("title", ""))
    return {"ok": ok, "fail": fail, "total": total}
