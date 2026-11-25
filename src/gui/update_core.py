# -*- coding: utf-8 -*-
"""更新检查：manifest 优先 + GitHub Releases 兜底（参考 olib 的更新清单机制）。

设计要点
========
1. 双数据源：
   - 主源：项目仓库里的 ``update-manifest.json``（GitHub raw 直读）。
     可声明 version / min_version 强制更新 / 多下载渠道 / 更新说明。
   - 兜底：GitHub Releases ``/releases/latest``（manifest 缺失或格式错误时）。
   两者都指向项目自己的仓库（由 update_config.json 或 DEFAULT_REPO 指定），不再查已废弃的上游。

2. 强制更新：manifest 的 ``min_version`` 高于本地版本时 ``force=true``，
   UI 据此弹出不可跳过的更新对话框。

3. 缓存与频率：检查成功后把结果写入本地 JSON（~/.qimao_gui/update_cache.json）。
   - ``check_update()`` 默认 24h 内直接返回缓存（避免每次启动都联网）；
   - ``check_update(force_refresh=True)`` 忽略缓存立即联网（设置页「检查更新」用）；
   - 从未成功请求过且联网失败 → 放行（ok=False 但 has_update=False），不阻塞使用。

4. 纯 requests，无 GUI 依赖，可独立测试；网络失败返回明确状态，不抛异常给 UI。

版本号单一真源来自 :mod:`gui.version`。
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict

from .logutil import logger
from .netutil import safe_get
from .version import APP_VERSION, is_newer

# ---- 数据源（可通过外置 update_config.json 覆盖，无需改代码重打包）----
# 默认值仅为占位；实际部署时请把 update_config.json 放到 exe 同目录并填入你的仓库。
DEFAULT_REPO = "ruyunyouzou/7mao-novel-downloader"
_CONFIG_FILE = "update_config.json"


def _load_repo() -> str:
    """读取仓库地址：优先 exe 同目录的 update_config.json，否则用默认值。

    配置文件格式：{"repo": "你的用户名/仓库名", "branch": "main"}
    """
    candidates = []
    try:
        from .resource_path import app_root
        candidates.append(os.path.join(app_root(), _CONFIG_FILE))
    except Exception:  # noqa: BLE001
        pass
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), _CONFIG_FILE))
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            repo = str(data.get("repo") or "").strip()
            if repo and "/" in repo:
                return repo
        except (OSError, ValueError):
            continue
    return DEFAULT_REPO


def _branch() -> str:
    """读取分支名（默认 main）。"""
    try:
        from .resource_path import app_root
        with open(os.path.join(app_root(), _CONFIG_FILE), "r", encoding="utf-8") as f:
            return str(json.load(f).get("branch") or "main").strip() or "main"
    except Exception:  # noqa: BLE001
        return "main"


def _urls() -> tuple[str, str]:
    """返回 (manifest_url, github_api)。每次调用都读配置，保证打包后改配置即生效。"""
    repo = _load_repo()
    branch = _branch()
    manifest = f"https://raw.githubusercontent.com/{repo}/{branch}/update-manifest.json"
    api = f"https://api.github.com/repos/{repo}/releases/latest"
    return manifest, api


# 保留常量名以兼容可能的外部引用（但实际逻辑已走 _urls()）
GITHUB_REPO = DEFAULT_REPO
GITHUB_API = f"https://api.github.com/repos/{DEFAULT_REPO}/releases/latest"

# ---- 缓存 ----
CHECK_INTERVAL = 24 * 60 * 60  # 24 小时
_CACHE_FILE = "update_cache.json"
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "xingyu-novel-updater",
}


def _cache_path() -> str:
    """缓存文件路径：与 DataStore 共用 ~/.qimao_gui 目录。"""
    home = os.path.expanduser("~")
    d = os.path.join(home, ".qimao_gui")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return os.path.join(d, _CACHE_FILE)


def _load_cache() -> Dict[str, Any]:
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(result: Dict[str, Any]) -> None:
    try:
        payload = dict(result)
        payload["cached_at"] = time.time()
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("写入更新缓存失败", exc=exc, tag="update")


# 镜像下载渠道前缀（均为实测可用的 GitHub 加速，逐个包裹原始下载直链）
_MIRROR_PREFIXES = (
    ("ghfast 镜像", "https://ghfast.top/"),
    ("gh-proxy 镜像", "https://gh-proxy.com/"),
    ("ghproxy.net 镜像", "https://ghproxy.net/"),
)


def mirror_channels(download_url: str, official_page: str) -> list:
    """由安装包直链生成「官方 + 多镜像」下载渠道列表（不涉及版本号拼装）。

    ``download_url`` 取 Release asset 的 ``browser_download_url``，因此不受
    Windows runner 中文文件名被吞的影响，也不必在代码里写死版本号。
    """
    channels: list = []
    official_page = str(official_page or "")
    if official_page:
        channels.append({"name": "GitHub 官方下载", "url": official_page})
    raw = str(download_url or "")
    if raw.startswith("http://") or raw.startswith("https://"):
        for name, prefix in _MIRROR_PREFIXES:
            channels.append({"name": name, "url": prefix + raw.lstrip("/")})
    return channels[:8]


def _blank_result(current: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "has_update": False,
        "force": False,
        "latest": "",
        "current": current,
        "url": "",
        "notes": "",
        "channels": [],
        "source": "",
        "message": "",
        "cached": False,
    }


def _finalize(result: Dict[str, Any], current: str) -> Dict[str, Any]:
    """统一计算 has_update / force / message。"""
    result["current"] = current
    latest = str(result.get("latest") or "")
    result["has_update"] = bool(latest) and is_newer(latest, current)
    # 强制更新：manifest 声明的 min_version 高于本地版本
    min_ver = str(result.get("min_version") or "")
    result["force"] = bool(min_ver) and is_newer(min_ver, current)
    if result["ok"]:
        if result["has_update"]:
            prefix = "【必须更新】" if result["force"] else ""
            result["message"] = f"{prefix}发现新版本 {latest}（当前 {current}）"
        else:
            result["message"] = f"当前已是最新版本（{current}）"
    return result


def _fetch_manifest(current: str, timeout: int) -> Dict[str, Any] | None:
    """读取 manifest。成功返回 result 字典，失败返回 None。"""
    try:
        manifest_url, _ = _urls()
        # raw.githubusercontent.com 走 jsDelivr / gh-proxy 镜像回退（均带证书容错）
        raw_path = manifest_url.split("raw.githubusercontent.com/", 1)[-1]
        mirrors = [
            f"https://cdn.jsdelivr.net/gh/{raw_path}",
            f"https://gh-proxy.com/https://raw.githubusercontent.com/{raw_path}",
        ]
        resp, err = safe_get(manifest_url, headers=_HEADERS, timeout=timeout, extra_urls=mirrors)
        if resp is None:
            logger.warning("读取 manifest 失败（所有通道）", exc=err, tag="update")
            return None
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, dict):
            return None
        # 必须有 version 字段，否则视为无效 manifest
        if not data.get("version"):
            return None
        channels = data.get("channels") or []
        clean_channels = []
        for ch in channels:
            if not isinstance(ch, dict):
                continue
            name = str(ch.get("name") or "").strip()
            url = str(ch.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            clean_channels.append({"name": name[:20], "url": url})
        result = _blank_result(current)
        result.update({
            "ok": True,
            "latest": str(data.get("version") or ""),
            "min_version": str(data.get("min_version") or ""),
            "notes": str(data.get("notes") or "")[:2000],
            "channels": clean_channels[:8],
            "url": (clean_channels[0]["url"] if clean_channels
                    else f"https://github.com/{_load_repo()}/releases"),
            "source": "manifest",
        })
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取更新 manifest 失败，将回退 Release", exc=exc, tag="update")
        return None


def _fetch_release(current: str, timeout: int) -> Dict[str, Any] | None:
    """读取 GitHub Release。成功返回 result 字典，失败返回 None。"""
    try:
        _, api_url = _urls()
        api_path = api_url.split("api.github.com/", 1)[-1]
        mirrors = [
            f"https://gh-proxy.com/https://api.github.com/{api_path}",
            f"https://ghfast.top/https://api.github.com/{api_path}",
        ]
        resp, err = safe_get(api_url, headers=_HEADERS, timeout=timeout, extra_urls=mirrors)
        if resp is None:
            logger.warning("读取 Release 失败（所有通道）", exc=err, tag="update")
            return None
        if resp.status_code != 200:
            return None
        data = resp.json()
        tag = str(data.get("tag_name") or "")
        if not tag:
            return None
        page = str(data.get("html_url") or f"https://github.com/{_load_repo()}/releases")
        # 从 asset 里找最大的 zip 直链，生成「官方 + 镜像」多渠道
        assets = data.get("assets") or []
        zips = [a for a in assets
                if str(a.get("name", "")).lower().endswith(".zip")
                and "source" not in str(a.get("name", "")).lower()]
        asset_url = ""
        if zips:
            biggest = max(zips, key=lambda a: int(a.get("size") or 0))
            asset_url = str(biggest.get("browser_download_url") or "")
        channels = mirror_channels(asset_url, page)
        result = _blank_result(current)
        result.update({
            "ok": True,
            "latest": tag,
            "notes": str(data.get("body") or "")[:2000],
            "url": page,
            "channels": channels,
            "source": "release",
        })
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 GitHub Release 失败", exc=exc, tag="update")
        return None


def check_update(current: str = APP_VERSION, timeout: int = 8,
                 force_refresh: bool = False,
                 store: Any = None) -> Dict[str, Any]:
    """检查更新。

    参数
    ----
    current: 本地版本号（默认取 version.APP_VERSION）。
    timeout: 单次网络超时秒数。
    force_refresh: True 忽略缓存立即联网（设置页手动检查用）。
    store: 预留（DataStore），当前缓存直接写本地文件。

    返回字典字段
    ------------
    ok / has_update / force / latest / current / url / notes / channels /
    source / message / cached
    """
    # 1) 非强制刷新时，24h 内命中缓存直接返回
    if not force_refresh:
        cache = _load_cache()
        cached_at = float(cache.get("cached_at") or 0)
        if cache.get("ok") and (time.time() - cached_at) < CHECK_INTERVAL:
            result = dict(cache)
            result["cached"] = True
            return _finalize(result, current)

    # 2) 联网：manifest 优先，失败回退 Release
    result = _fetch_manifest(current, timeout)
    if result is None:
        result = _fetch_release(current, timeout)

    # 3) 两个源都失败
    if result is None:
        out = _blank_result(current)
        # 有历史缓存时用缓存重算（保证强制更新重启后依然生效）
        cache = _load_cache()
        if cache.get("ok"):
            out = dict(cache)
            out["cached"] = True
            out["message"] = "网络不可用，已使用上次的检查结果"
        else:
            out["message"] = "检查失败：无法连接到更新服务器"
        return _finalize(out, current)

    _finalize(result, current)
    _save_cache(result)
    return result
