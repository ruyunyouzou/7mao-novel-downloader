# -*- coding: utf-8 -*-
"""番茄书籍「官方元数据覆盖表」。

背景：番茄手机 App 展示的书名/封面常常比 Web 端（fanqienovel.com）更新，
Web 端搜索/详情页返回的是旧书名与旧封面。用户在下载对话框里填写 App 端
当前书名，并可选择「粘贴封面网址」或「从 App 保存的本地封面图片」，本模块
按 book_id 持久化，下载内核据此覆盖 Web 旧数据，让落盘产物（文件名 /
EPUB 标题 / 内嵌封面）与官方 App 一致。

存储：DataStore.settings 下的 ``fanqie_meta_overrides`` 键，结构为：
    {"<book_id>": {"title": "...", "cover_url": "...",
                   "cover_path": "...", "source": "manual"}}

本地封面图片单独落在 ``~/.qimao_gui/meta_covers/<book_id>.jpg``，不放搜索
封面缓存目录（那个会被「清理缓存」删掉）。任何字段为空即视为未覆盖，回退
Web 抓取；全部读写带容错，绝不因覆盖表异常中断下载主链路。
"""

from __future__ import annotations

import os
import shutil
from typing import Dict

_KEY = "fanqie_meta_overrides"

#: 进程内复用的 DataStore。DataStore() 构造会读取 9 个 JSON 文件，
#: 覆盖表在搜索/详情/下载/反查里会被反复调用，每次新建实例纯属浪费。
_STORE = None


def _store():  # pragma: no cover - 惰性导入避免循环
    global _STORE
    if _STORE is None:
        from ..datastore import DataStore
        _STORE = DataStore()
    return _STORE


def cover_file_path(book_id: str) -> str:
    """某本书本地官方封面图片的落盘路径。"""
    bid = str(book_id or "").strip()
    if not bid:
        return ""
    home = os.path.expanduser("~")
    return os.path.join(home, ".qimao_gui", "meta_covers", f"{bid}.jpg")


def _load_all() -> Dict[str, Dict[str, str]]:
    try:
        data = _store().settings_get(_KEY, {})
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def get_override(book_id: str) -> Dict[str, str]:
    """返回某本书的覆盖元数据；无覆盖返回空字典。

    含三个字段：title / cover_url / cover_path。cover_path 只在文件仍存在时
    返回，避免用户手动删掉图片后指向失效路径。
    """
    bid = str(book_id or "").strip()
    if not bid:
        return {}
    entry = _load_all().get(bid)
    if not isinstance(entry, dict):
        return {}
    result = {
        "title": str(entry.get("title") or "").strip(),
        "cover_url": str(entry.get("cover_url") or "").strip(),
        "cover_path": "",
    }
    cp = str(entry.get("cover_path") or "").strip()
    if cp and os.path.isfile(cp):
        result["cover_path"] = cp
    return result


def save_cover_file(book_id: str, src_path: str) -> str:
    """把用户选的本地封面图片复制到覆盖表目录，返回落盘路径；失败返回空串。

    先写 ``.part`` 再原子替换：直接 copyfile 到目标位置时，若复制中途被打断，
    会留下半张坏图，而 ``os.path.isfile`` 仍为真，之后每次下载都会把坏图
    塞进 EPUB。图片有 Pillow 时统一转成真正的 JPEG（用户可能选 PNG/WEBP，
    而落盘文件名固定 ``.jpg``，扩展名与内容不符会导致部分阅读器不显示封面）。
    """
    bid = str(book_id or "").strip()
    src = str(src_path or "").strip()
    if not bid or not src or not os.path.isfile(src):
        return ""
    dst = cover_file_path(bid)
    tmp = f"{dst}.part"
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, tmp)
        try:
            from PIL import Image
            with Image.open(tmp) as im:
                im.convert("RGB").save(tmp, "JPEG", quality=92)
        except Exception:  # noqa: BLE001 - 无 Pillow 或非图片：保留原始字节
            pass
        os.replace(tmp, dst)
        return dst
    except Exception:  # noqa: BLE001
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return ""


def set_override(book_id: str, title: str = "", cover_url: str = "",
                 cover_path: str = "") -> None:
    """写入/更新某本书的官方元数据。空字段不覆盖已有值。"""
    bid = str(book_id or "").strip()
    if not bid:
        return
    title = str(title or "").strip()
    cover_url = str(cover_url or "").strip()
    cover_path = str(cover_path or "").strip()
    if not (title or cover_url or cover_path):
        return
    try:
        store = _store()
        data = store.settings_get(_KEY, {})
        if not isinstance(data, dict):
            data = {}
        entry = dict(data.get(bid) or {})
        if title:
            entry["title"] = title
        if cover_url:
            entry["cover_url"] = cover_url
        if cover_path:
            entry["cover_path"] = cover_path
        entry["source"] = "manual"
        data[bid] = entry
        store.settings_set(_KEY, data)
    except Exception:  # noqa: BLE001
        pass


def remove_override(book_id: str) -> None:
    """删除某本书的覆盖（下载对话框清空时使用），同时删除本地封面文件。"""
    bid = str(book_id or "").strip()
    if not bid:
        return
    try:
        store = _store()
        data = store.settings_get(_KEY, {})
        if isinstance(data, dict) and bid in data:
            data.pop(bid, None)
            store.settings_set(_KEY, data)
    except Exception:  # noqa: BLE001
        pass
    # 删除本地封面文件（失败不影响主流程）
    try:
        fp = cover_file_path(bid)
        if fp and os.path.isfile(fp):
            os.remove(fp)
    except Exception:  # noqa: BLE001
        pass


def all_overrides() -> Dict[str, Dict[str, str]]:
    """返回全部覆盖表（供管理/调试）。"""
    return _load_all()


def resolve_book_id_by_title(official_title: str) -> str:
    """尽力而为：用 App 端官方书名去 Web 搜索，反查最可能的 bookId。

    同一本书在 App 与 Web 常常书名不同，但 Web 搜索接口能按官方书名命中
    同一本书（返回的显示名可能仍是旧名）。该函数只做「书名→ID」的辅助关联，
    不提供书名/封面数据（那些只存在于 App，Web 抓不到）。失败返回空串。
    """
    title = str(official_title or "").strip()
    if not title:
        return ""
    try:
        from .fanqie_search import search_fanqie
        results = search_fanqie(title, page=1) or []
        if results:
            return str(results[0].get("id") or results[0].get("book_id") or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return ""
