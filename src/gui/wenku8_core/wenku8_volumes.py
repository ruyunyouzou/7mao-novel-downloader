# -*- coding: utf-8 -*-
"""文库8分卷导出：把目录按卷分组，供「整本 / 按卷分文件 / 仅所选卷」三种模式使用。

本模块**只被文库8下载插件调用**，番茄 / 七猫链路完全不引用，零回归。

设计要点
--------
- group_volumes(): 依章节的 vol 字段（wenku8_reader 解析自目录页 td.vcss）分组，
  返回每卷的 name / start / end（1-based 全局章节序号，与 build_txt 的
  start_chapter/end_chapter 语义完全一致）/ chapters / count。
- select_volumes(): 按模式挑出要导出的卷。
- volume_filename_template(): 生成带卷名、章节范围的文件名模板，交给 build_txt
  的 render_filename 渲染，天然支持用户的文件名模板习惯。
- 正文不在此模块抓取：由 Wenku8BookSource 的缓存层保证全书只抓一次。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

# 正文里插图 URL 的占位标记（wenku8_reader.fetch_chapter_text 写入）
IMG_MARK_RE = re.compile(r"\[\[WENKU8IMG:([^\]]+)\]\]")

# 文库8图片服务器要求浏览器头，伪装 Edge；与 cover_helper 保持一致
IMAGE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0")


def extract_image_urls(text: str) -> List[str]:
    """从正文文本里提取全部插图 URL（按出现顺序，去重）。"""
    if not text:
        return []
    out: List[str] = []
    for m in IMG_MARK_RE.finditer(str(text)):
        url = str(m.group(1) or "").strip()
        if url and url not in out:
            out.append(url)
    return out


def is_image_bytes(raw: bytes) -> bool:
    """校验响应体是不是真实图片（拒绝 403/404 的 HTML 错误页）。"""
    if not raw:
        return False
    return (raw.startswith(b"\xff\xd8\xff") or            # JPEG
            raw.startswith(b"\x89PNG\r\n\x1a\n") or       # PNG
            raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a") or
            (raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"))  # WebP


def download_image(url: str, timeout: int = 15) -> bytes:
    """下载一张文库8插图，返回图片字节；失败返回 b''。"""
    if not str(url or "").startswith("http"):
        return b""
    try:
        import requests
        from urllib.parse import urlparse
        host = urlparse(url).netloc or "www.wenku8.cc"
        headers = {
            "User-Agent": IMAGE_UA,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": f"https://{host}/",
        }
        resp = requests.get(url, timeout=timeout, headers=headers)
        raw = resp.content or b""
        return raw if is_image_bytes(raw) else b""
    except Exception:  # noqa: BLE001
        return b""


def sanitize_vol_name(s: Any) -> str:
    """把卷名清洗成可做文件名的安全字符串（供书源缓存与命名共用）。"""
    name = _clean(s).replace("{", "").replace("}", "") or "卷"
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()

# 三种导出模式
MODE_WHOLE = "whole"          # 整本导出（默认，等价于旧行为）
MODE_PER_VOLUME = "per_volume"  # 按卷分文件，每卷一个 EPUB/TXT
MODE_SELECTED = "selected"    # 仅导出勾选的卷（每卷一个文件）


def _clean(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def group_volumes(catalog: Sequence[Dict]) -> List[Dict]:
    """把归一后的目录按 vol 字段切成卷。

    目录里 vol 相同时视为同一卷；vol 变化即开新卷（同名卷分开出现也各成一卷，
    用 start 区分，导出文件名带范围不会冲突）。无 vol 的章节归入「正文」卷。

    :return: [{name, start, end, count, chapters}]，start/end 为 1-based 全局序号
    """
    volumes: List[Dict] = []
    for ch in catalog:
        idx = int(ch.get("index", 0) or 0) + 1
        name = _clean(ch.get("vol")) or "正文"
        if not volumes or name != volumes[-1]["name"]:
            volumes.append({
                "name": name,
                "start": idx,
                "end": idx,
                "count": 0,
                "chapters": [],
            })
        vol = volumes[-1]
        vol["end"] = idx
        vol["count"] += 1
        vol["chapters"].append(ch)
    return volumes


def select_volumes(volumes: Sequence[Dict], mode: str,
                   selected: Optional[Sequence[str]] = None) -> List[Dict]:
    """按导出模式挑卷。

    :param mode: MODE_WHOLE / MODE_PER_VOLUME / MODE_SELECTED
    :param selected: MODE_SELECTED 时勾选的卷名列表（卷名去重前先按列表顺序保留）
    :return: 待导出的卷列表
    """
    mode = str(mode or MODE_WHOLE)
    if mode == MODE_PER_VOLUME:
        return [dict(v) for v in volumes]
    if mode == MODE_SELECTED:
        wanted = {_clean(x) for x in (selected or [])}
        if not wanted:
            return []
        return [dict(v) for v in volumes if _clean(v.get("name")) in wanted]
    # MODE_WHOLE 及未知模式：整本一卷
    if not volumes:
        return []
    return [{
        "name": "全本",
        "start": volumes[0]["start"],
        "end": volumes[-1]["end"],
        "count": sum(int(v.get("count", 0)) for v in volumes),
        "chapters": [ch for v in volumes for ch in v.get("chapters", [])],
    }]


def volume_filename_template(base_template: str, vol_name: str) -> str:
    """生成单卷文件名模板（含卷名与章节范围占位符）。

    卷名里的花括号会破坏 str.format，统一剔除；斜杠等非法字符由
    build_txt 内的 sanitize_filename 处理。
    """
    vol = _clean(vol_name).replace("{", "").replace("}", "") or "卷"
    base = str(base_template or "").strip() or "{title}"
    return f"{base}_{vol}_{{start}}-{{end}}"


def volume_label(vol: Dict) -> str:
    """卷的简短展示文案，供队列/结果消息用。"""
    return f"{_clean(vol.get('name')) or '卷'}（{vol.get('start', 1)}-{vol.get('end', 1)}章）"
