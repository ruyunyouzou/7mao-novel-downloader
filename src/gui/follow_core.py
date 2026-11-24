# -*- coding: utf-8 -*-
"""
增量追更核心：对比本地已下载章节数与线上目录，只取新增章节。

策略：
- TXT：真正增量——下载缓存 ZIP 后，仅解密新章，追加写入原 .txt 文件。
- EPUB / 分章：EPUB 重建代价高且需保持排版一致，采用"重新生成整本"（缓存 ZIP
  已含全书，速度很快），保证排版/字体/颜色与首次下载完全一致。

对外主入口：check_updates(follows, ...) -> [{...}] 与 perform_follow(rec, ...)
"""

from __future__ import annotations

import io
import os
import zipfile
from typing import Any, Callable, Dict, List, Optional

import requests

from . import qimao_core as core
from .logutil import logger


def estimate_local_chapters(file_path: str, fmt: str = "txt") -> int:
    """估算本地文件已有章数，作为首次追更的基准。"""
    import re
    if not file_path or not os.path.isfile(file_path):
        return 0
    try:
        if fmt == "epub":
            with zipfile.ZipFile(file_path) as z:
                # 章节 xhtml 文件数（排除 nav/toc）
                return sum(1 for n in z.namelist()
                           if re.search(r"ch_\d+\.xhtml$", n)
                           or re.search(r"chapter\d+\.xhtml$", n, re.I))
        # TXT：统计匹配章节标题的行
        ch_re = re.compile(
            r"^\s*(第[零一二三四五六七八九十百千万两〇0-9]+\s*[章节回部篇卷]"
            r"|第[零一二三四五六七八九十百千万两〇0-9]{2,}"
            r"|[Cc]hapter\s*\d+|序章|楔子|番外|尾声|后记)")
        count = 0
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if ch_re.match(line):
                    count += 1
        return count
    except Exception as exc:  # noqa: BLE001
        logger.error(f"估算本地章数失败 {file_path}", exc=exc, tag="follow")
        return 0


def check_one(rec: Dict[str, Any], session=None) -> Dict[str, Any]:
    """检查单本追更记录是否有新章。返回带 new_chapters 的字典。"""
    session = session or requests.Session()
    bid = str(rec.get("book_id", ""))
    out = dict(rec)
    out["has_new"] = False
    out["new_chapters"] = 0
    out["error"] = ""
    try:
        catalog = core.get_catalog(bid, session)
        remote_total = len(catalog)
        local_total = int(rec.get("last_chapter", 0) or 0)
        out["remote_total"] = remote_total
        out["local_total"] = local_total
        if remote_total > local_total:
            out["has_new"] = True
            out["new_chapters"] = remote_total - local_total
            try:
                detail = core.get_detail(bid, session)
                out["title"] = detail.get("title") or rec.get("title", "")
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
        logger.error(f"追更检查失败 {bid}", exc=exc, tag="follow")
    return out


def check_updates(follows: List[Dict[str, Any]],
                  progress_cb: Optional[Callable[[int, int], None]] = None) -> List[Dict[str, Any]]:
    """批量检查追更。"""
    session = requests.Session()
    results: List[Dict[str, Any]] = []
    for i, rec in enumerate(follows, 1):
        results.append(check_one(rec, session))
        if progress_cb:
            progress_cb(i, len(follows))
    return results


def follow_txt(rec: Dict[str, Any], path: str,
               progress_cb: Optional[Callable[[int], None]] = None) -> Dict[str, Any]:
    """TXT 真增量：只下新章并追加到已有 txt。"""
    bid = str(rec.get("book_id", ""))
    local_total = int(rec.get("last_chapter", 0) or 0)
    session = requests.Session()
    catalog = core.get_catalog(bid, session)
    remote_total = len(catalog)
    if remote_total <= local_total:
        return {"changed": False, "total": remote_total, "message": "暂无新章节"}
    new_catalog = catalog[local_total:]  # 索引从 local_total 起的都是新章（前 local_total 章已下）

    link = core.get_cache_zip_link(bid, session)
    zip_bytes = core.download_cache_zip(link, session, progress_cb)
    appended = 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        name_map = {os.path.splitext(os.path.basename(n))[0]: n for n in zf.namelist()}
        with open(path, "a", encoding="utf-8") as f:
            for ch in new_catalog:
                member = name_map.get(ch["id"])
                if not member:
                    continue
                try:
                    text = core.decrypt_chapter(zf.read(member))
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"追更解密失败 {ch['id']}", exc=exc, tag="follow")
                    continue
                f.write(f"\n\n{ch['title']}\n\n{text}\n")
                appended += 1
    if progress_cb:
        progress_cb(100)
    # 更新文件头部的章数标记（追加模式仅在末尾追加，总数写进单独的 sidecar 由 DataStore 记录）
    return {"changed": appended > 0, "appended": appended,
            "total": remote_total, "message": f"已追加 {appended} 章"}
