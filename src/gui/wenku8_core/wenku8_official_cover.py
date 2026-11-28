# -*- coding: utf-8 -*-
"""文库8 官方正版高清封面（Bangumi 条目大图）。

为什么需要这一层
----------------
文库8 图床 ``img.wenku8.com/image/{n}/{aid}/{aid}s.jpg`` 里的图**本身就是
官方日版封面**，但被压成约 217×310 的小图（URL 里的 ``s`` 即 small），
且实测大图地址 ``{aid}.jpg`` / ``{aid}l.jpg`` 全部 404——站点只提供小图。

参考 kaiwen.work 轻小说库与 Bangumi API 文档：按书名匹配 Bangumi 书籍
条目（type=1），取 ``images.large``（350×500+ 官方正版大图），校验尺寸后
落本地磁盘缓存。这是目前不依赖人工、全书通用的高清官方封面来源。

设计契约
--------
* **严格匹配**：归一化后完全相等，或一侧包含另一侧且短侧≥8字、覆盖率≥0.6
  才认领。宁可不换（回退文库8小图），绝不换错封面（错图比小图更糟）。
* **零网络热路径**：aid→条目映射 + 大图都落 ``~/.qimao_gui/wenku8_official_covers/``，
  命中缓存直接返回字节；失败条目写负缓存（7 天）避免反复打 Bangumi。
* **全书通用**：无任何书名/书号硬编码，匹配完全靠规范化书名 + 括号别名。
* **失败可控**：任何环节失败都返回 b""，由调用方回退既有文库8小图通道。
  本模块绝不抛异常到 UI 线程。
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import time
from typing import Dict, List, Optional, Tuple

# Bangumi 官方 API + 国内可达镜像（顺序 fallback，与 kaiwen.work 一致）
_API_BASES = (
    "https://api.bgm.tv",
    "https://api.bangumi.one",
    "https://bgmapi.anibt.net",
)
# 搜索只能走官方域名：第三方镜像(bgmapi.anibt.net)的 /v0/search/subjects
# 忽略 keywords、始终返回全站列表（实测 total≈81707），按书名匹配会全错。
# 镜像仅用于 subject 详情与大图下载。
_SEARCH_BASES = (
    "https://api.bgm.tv",
    "https://api.bangumi.one",
)
# images.large 的 host 镜像改写（保留 path/query），应对单一域名不可达
_IMG_HOSTS = (
    "lain.bgm.tv",
    "lain.bangumi.one",
    "bgmimg.anibt.net",
)
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_MIN_W, _MIN_H = 300, 420          # 大图有效性阈值（对齐 kaiwen.work）
_NEG_TTL = 7 * 24 * 3600           # 失败负缓存 7 天
_OK_TTL = 30 * 24 * 3600           # 成功映射 30 天
_SEARCH_LIMIT = 10

_lock = threading.RLock()
_sem = threading.Semaphore(2)       # 全局限制 Bangumi 并发，避免风控
_map_cache: Optional[Dict[str, dict]] = None


# --------------------------------------------------------------------------- 缓存路径


def cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".qimao_gui", "wenku8_official_covers")


def _map_path() -> str:
    return os.path.join(cache_dir(), "map.json")


def _image_path(aid: str) -> str:
    return os.path.join(cache_dir(), f"{aid}.jpg")


def _load_map() -> Dict[str, dict]:
    global _map_cache
    with _lock:
        if _map_cache is not None:
            return _map_cache
        data: Dict[str, dict] = {}
        try:
            with open(_map_path(), "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                data = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
        except Exception:  # noqa: BLE001 - 缓存损坏就当空，绝不崩 UI
            data = {}
        _map_cache = data
        return data


def _save_map(data: Dict[str, dict]) -> None:
    """原子写入，避免多个线程同时读 half-file。"""
    with _lock:
        global _map_cache
        _map_cache = data
        try:
            os.makedirs(cache_dir(), exist_ok=True)
            tmp = _map_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, _map_path())
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- 工具


def extract_aid(url_or_id) -> str:
    """从封面 URL / 章节 URL / 裸数字中提取文库8 aid；失败返回空串。"""
    text = str(url_or_id or "").strip()
    if text.isdigit():
        return text
    # /image/2/2580/2580s.jpg 或 /novel/2/2580/12345.htm
    m = re.search(r"/(?:image|novel)/\d+/(\d+)/", text)
    if m:
        return m.group(1)
    m = re.search(r"/book/(\d+)\.htm", text)
    return m.group(1) if m else ""


def title_variants(title: str) -> List[str]:
    """书名 + 括号别名（台版译名常在括号里，更容易命中 Bangumi name_cn）。"""
    out: List[str] = []
    raw = str(title or "").strip()
    if raw:
        out.append(raw)
    for alias in re.findall(r"[（(]([^）)]+)[）)]", raw):
        a = alias.strip()
        if a and a not in out:
            out.append(a)
    return out


def _strict_score(candidate: str, variant: str) -> int:
    """归一化后的匹配强度：完全相等 100，包含且够长 60，其余 0。"""
    try:
        from .wenku8_match import normalize
    except Exception:  # noqa: BLE001
        return 0
    a, b = normalize(candidate), normalize(variant)
    if not a or not b:
        return 0
    if a == b:
        return 100
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    # 短侧至少 8 字且覆盖率≥0.6，才认领；避免「天使」这类泛词错配。
    if len(short) >= 8 and short in long and len(short) / len(long) >= 0.6:
        return 60
    return 0


def _best_match(results: List[dict], title: str) -> Tuple[str, str]:
    """从 Bangumi 搜索结果里挑最可信的书籍条目，返回 (sid, large_url)。

    返回空 sid 表示不匹配——调用方回退小图，绝不换错封面。
    """
    variants = title_variants(title)
    if not variants or not results:
        return "", ""
    best_sid, best_url, best_score = "", "", 0
    for item in results:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "").strip()
        if not sid.isdigit():
            continue
        images = item.get("images") or {}
        large = str(images.get("large") or "").strip()
        names = [str(item.get("name_cn") or ""), str(item.get("name") or "")]
        score = 0
        for name in names:
            for var in variants:
                score = max(score, _strict_score(name, var))
        if score > best_score:
            best_sid, best_url, best_score = sid, large, score
    # 只有完全相等(100)或强包含(60)才认领；泛匹配一律放弃
    return (best_sid, best_url) if best_score >= 60 else ("", "")


def _get_json(url: str, timeout: int) -> dict:
    import requests
    resp = requests.get(url, timeout=timeout, headers={
        "User-Agent": _UA,
        "Accept": "application/json",
    })
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    return resp.json()


def _search_subjects(keyword: str, timeout: int) -> List[dict]:
    """跨官方 API base 搜索书籍条目（type=1）。

    Bangumi v0 搜索是 **POST + JSON body**，GET 会返回 405 Not Allowed；
    旧实现用 GET 拼 query，导致搜索永远失败、封面永远回退小图。
    第三方镜像的 search 不过滤关键词，因此这里只走官方域名。
    """
    import requests
    last: Optional[Exception] = None
    payload = {"keywords": keyword, "type": 1, "limit": _SEARCH_LIMIT}
    for base in _SEARCH_BASES:
        try:
            resp = requests.post(
                f"{base}/v0/search/subjects",
                json=payload,
                timeout=timeout,
                headers={"User-Agent": _UA, "Accept": "application/json"},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            data = resp.json()
            items = data.get("data") if isinstance(data, dict) else data
            return [x for x in (items or []) if isinstance(x, dict)]
        except Exception as exc:  # noqa: BLE001
            last = exc
            continue
    raise last or RuntimeError("Bangumi 搜索全部失败")


def _subject_detail(sid: str, timeout: int) -> dict:
    last: Optional[Exception] = None
    for base in _API_BASES:
        try:
            data = _get_json(f"{base}/v0/subjects/{sid}", timeout)
            if isinstance(data, dict):
                return data
        except Exception as exc:  # noqa: BLE001
            last = exc
            continue
    raise last or RuntimeError("Bangumi 详情全部失败")


def _mirror_urls(large_url: str) -> List[str]:
    """把 images.large 的 host 换成各镜像，原域名优先。"""
    out = [large_url]
    m = re.match(r"^(https?://)([^/]+)(/.*)$", large_url)
    if not m:
        return out
    scheme, host, path = m.groups()
    for mirror in _IMG_HOSTS:
        if mirror != host:
            out.append(f"{scheme}{mirror}{path}")
    return out


def _valid_image(data: bytes) -> Tuple[bool, int, int]:
    """PIL 校验图片有效且尺寸≥阈值，返回 (ok, w, h)。"""
    if not data:
        return False, 0, 0
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            return (w >= _MIN_W and h >= _MIN_H), w, h
    except Exception:  # noqa: BLE001
        return False, 0, 0


def _download_large(large_url: str, timeout: int) -> Tuple[bytes, int, int]:
    """多镜像下载官方大图，校验尺寸；全失败返回 (b"",0,0)。"""
    import requests
    for url in _mirror_urls(large_url):
        try:
            resp = requests.get(url, timeout=timeout, headers={
                "User-Agent": _UA,
                "Referer": "https://bgm.tv/",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            })
            raw = resp.content or b""
            ok, w, h = _valid_image(raw)
            if ok:
                return raw, w, h
        except Exception:  # noqa: BLE001
            continue
    return b"", 0, 0


def _write_image(aid: str, data: bytes) -> bool:
    try:
        os.makedirs(cache_dir(), exist_ok=True)
        tmp = _image_path(aid) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, _image_path(aid))
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- 公共入口


def cached_official_cover(aid) -> bytes:
    """只读本地缓存（零网络）。UI 快速路径用：命中返回大图字节，否则 b""。"""
    aid = extract_aid(aid)
    if not aid:
        return b""
    try:
        with open(_image_path(aid), "rb") as f:
            return f.read()
    except Exception:  # noqa: BLE001
        return b""


def fetch_official_cover(aid, title: str = "", timeout: int = 6) -> bytes:
    """取官方正版大图：缓存命中直接返回；否则搜索 Bangumi 严格匹配后下载。

    任何失败返回 b""（调用方回退文库8小图），绝不抛异常。
    """
    aid = extract_aid(aid)
    if not aid:
        return b""
    # 缓存命中（含本地大图文件）——零网络
    hit = cached_official_cover(aid)
    if hit:
        return hit
    mapping = _load_map()
    entry = mapping.get(aid) or {}
    now = time.time()
    ts = float(entry.get("ts") or 0)
    # 负缓存：此前确认 Bangumi 无此书/匹配失败，7 天内不再重试
    if entry.get("fail") and (now - ts) < _NEG_TTL:
        return b""
    # 已有 sid 但大图文件丢失（被用户清理）：直接用 sid 重取详情
    sid = str(entry.get("sid") or "").strip()
    large = str(entry.get("url") or "").strip()

    with _sem:
        try:
            if not (sid.isdigit() and large):
                # 按书名严格匹配；搜索词依次试完整书名与括号别名
                for kw in title_variants(title) or [aid]:
                    results = _search_subjects(kw, timeout)
                    sid, large = _best_match(results, title)
                    if sid:
                        break
            if not (sid.isdigit() and large):
                _record_fail(mapping, aid, now)
                return b""
            # 搜索结果里的 large 可能是缩略图，取详情再确认一次
            try:
                detail = _subject_detail(sid, timeout)
                imgs = detail.get("images") or {}
                large = str(imgs.get("large") or large).strip()
            except Exception:  # noqa: BLE001
                pass  # 详情失败不致命，仍用搜索结果里的 large 试
            raw, w, h = _download_large(large, timeout)
            if not raw:
                _record_fail(mapping, aid, now)
                return b""
            if not _write_image(aid, raw):
                return b""
            mapping[aid] = {"sid": sid, "url": large, "w": w, "h": h, "ts": now}
            _save_map(mapping)
            return raw
        except Exception:  # noqa: BLE001
            return b""


def _record_fail(mapping: Dict[str, dict], aid: str, now: float) -> None:
    mapping[aid] = {"fail": 1, "ts": now}
    _save_map(mapping)


def clear_fail_cache() -> int:
    """清掉负缓存（供用户手动重试用），返回清除条数。"""
    mapping = _load_map()
    failed = [k for k, v in mapping.items() if v.get("fail")]
    for k in failed:
        mapping.pop(k, None)
    if failed:
        _save_map(mapping)
    return len(failed)
