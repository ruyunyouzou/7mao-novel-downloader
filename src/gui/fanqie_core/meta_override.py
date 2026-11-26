# -*- coding: utf-8 -*-
"""番茄书籍「官方元数据覆盖表」。

背景：番茄手机 App 展示的书名/封面常常比 Web 端（fanqienovel.com）更新，
Web 端搜索/详情页返回的是旧书名与旧封面。用户在下载对话框里粘贴 App 端
当前书名与封面图片网址后，本模块按 book_id 持久化，下载内核据此覆盖
Web 旧数据，让落盘产物（文件名 / EPUB 标题 / 内嵌封面）与官方 App 一致。

存储：DataStore.settings 下的 ``fanqie_meta_overrides`` 键，结构为：
    {"<book_id>": {"title": "...", "cover_url": "...", "source": "manual"}}

设计原则：
  - 只影响番茄内核下载产物，不改搜索列表/详情展示；
  - 任何字段为空即视为「未覆盖」，回退 Web 抓取；
  - 全部读写带容错，绝不因覆盖表异常中断下载主链路。
"""

from __future__ import annotations

from typing import Any, Dict

_KEY = "fanqie_meta_overrides"


def _store():  # pragma: no cover - 惰性导入避免循环
    from ..datastore import DataStore
    return DataStore()


def _load_all() -> Dict[str, Dict[str, str]]:
    try:
        data = _store().settings_get(_KEY, {})
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def get_override(book_id: str) -> Dict[str, str]:
    """返回某本书的覆盖元数据；无覆盖返回空字典。"""
    bid = str(book_id or "").strip()
    if not bid:
        return {}
    entry = _load_all().get(bid)
    if not isinstance(entry, dict):
        return {}
    return {
        "title": str(entry.get("title") or "").strip(),
        "cover_url": str(entry.get("cover_url") or "").strip(),
    }


def set_override(book_id: str, title: str = "", cover_url: str = "") -> None:
    """写入/更新某本书的官方元数据。空字段不覆盖已有值。"""
    bid = str(book_id or "").strip()
    if not bid:
        return
    title = str(title or "").strip()
    cover_url = str(cover_url or "").strip()
    if not title and not cover_url:
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
        entry["source"] = "manual"
        data[bid] = entry
        store.settings_set(_KEY, data)
    except Exception:  # noqa: BLE001
        pass


def remove_override(book_id: str) -> None:
    """删除某本书的覆盖（下载对话框清空时使用）。"""
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


def all_overrides() -> Dict[str, Dict[str, str]]:
    """返回全部覆盖表（供管理/调试）。"""
    return _load_all()
