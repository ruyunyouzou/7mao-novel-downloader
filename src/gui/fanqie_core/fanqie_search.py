# -*- coding: utf-8 -*-
"""番茄小说关键词搜索（自实现，不依赖上游下载内核）。

数据流：
  关键词 → a_bogus 签名 → fanqienovel.com 官方搜索接口
        → SEARCH_MAP 解码 PUA 混淆 → 与七猫搜索同构的书籍字典列表

搜索专用字体映射（SEARCH_MAP）与正文映射（FONT_MAP）不同，切勿混用：
  - 搜索/列表：fanqie_search_upstream/searchmap.py
  - 正文：gui.fanqie_core.fanqie_reader.FONT_MAP

输出字典字段与 gui.api.search_books 对齐，便于搜索页通用渲染：
  id / title / author / words_num / score / cover / tags / intro /
  is_over / sub_title / characters / source / raw
"""

from __future__ import annotations

from typing import Any, Dict, List

import requests

from .fanqie_search_upstream.abogus import ABogus
from .fanqie_search_upstream.searchmap import SEARCH_MAP

_API = "https://fanqienovel.com/api/author/search/search_book/v1"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)


def decode_search(text: Any) -> str:
    """把搜索接口返回文本里的 PUA 私有区字符还原为真实字符。"""
    return "".join(SEARCH_MAP.get(ord(ch), ch) for ch in str(text or ""))


def search_fanqie(keyword: str, page: int = 1, timeout: int = 20) -> List[Dict[str, Any]]:
    """番茄关键词搜索，返回与七猫搜索同构的书籍列表。

    :param keyword: 搜索关键词
    :param page: 页码（从 1 开始）
    :param timeout: 请求超时秒数
    :raises Exception: 网络请求失败或接口返回异常
    """
    params: Dict[str, Any] = {
        "filter": "127,127,127,127",
        "page_count": "10",
        "page_index": str(max(0, int(page) - 1)),
        "query_type": "0",
        "query_word": keyword,
    }
    # a_bogus 必须对最终查询串签名；先用无签名参数生成，再附加到请求上
    params["a_bogus"] = ABogus(_UA).generate_a_bogus(dict(params))

    resp = requests.get(
        _API,
        params=params,
        headers={"User-Agent": _UA, "Referer": "https://fanqienovel.com/",
                 "Accept": "application/json, text/plain, */*"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") not in (0, "0", None):
        raise RuntimeError(f"番茄搜索接口异常: {data.get('message', data.get('code'))}")

    items = ((data.get("data") or {}).get("search_book_data_list") or [])
    results: List[Dict[str, Any]] = []
    for b in items:
        bid = str(b.get("book_id", "") or "").strip()
        if not bid:
            continue
        words = b.get("word_count")
        words_text = f"{int(words)}字" if isinstance(words, (int, float)) else (words or "未知")
        creation = b.get("creation_status")
        # creation_status: 0=连载中 / 1=已完结（与搜索页展示口径一致）
        is_over = str(creation) == "1"
        status = "已完结" if is_over else "连载中"
        results.append({
            "id": bid,
            "title": decode_search(b.get("book_name")) or f"book_{bid}",
            "author": decode_search(b.get("author")) or "未知作者",
            "words_num": words_text,
            "score": "",
            "cover": b.get("thumb_url", ""),
            "tags": decode_search(b.get("category")),
            "intro": decode_search(b.get("book_abstract")),
            "is_over": is_over,
            "sub_title": f"{status} · {decode_search(b.get('category'))} · {words_text}",
            "characters": "",
            "source": "fanqie",  # 关键：标记书源，供内核选择
            "raw": b,
        })
    return results
