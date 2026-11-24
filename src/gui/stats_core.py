# -*- coding: utf-8 -*-
"""
本地阅读统计：基于 read_history / progress / shelf 数据做聚合，纯本地计算。

提供：
- 总览：阅读次数、在读书籍数、书架书籍数、累计字数（估算）
- 按日阅读次数（近 N 天）
- 按书阅读次数排行
- 阅读进度分布（未读/在读/读完）
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List


def _to_percent(value: Any) -> float:
    """安全地把进度值转为 0-100 浮点，脏数据返回 0。"""
    try:
        return float(str(value or "0").rstrip("%").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_day(time_str: str) -> str:
    """从 '2026-11-23 16:00:00' 提取 '2026-11-23'。"""
    s = str(time_str or "")
    return s[:10] if len(s) >= 10 else s


def _words_to_int(value: Any) -> int:
    """把 '536万字' / '120.5万' / 1234 之类解析为字数整数。"""
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value or "")
    import re
    m = re.search(r"([\d.]+)\s*万", s)
    if m:
        try:
            return int(float(m.group(1)) * 10000)
        except ValueError:
            return 0
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else 0


def build_stats(store) -> Dict[str, Any]:
    """聚合所有统计数据。"""
    read_history = store.read_history_all()
    progress = store.progress_all()
    shelf = store.shelf_all()

    # 按日阅读次数（近 14 天）
    day_counts: Counter = Counter(_parse_day(h.get("time", "")) for h in read_history)
    today = datetime.now()
    daily: List[Dict[str, Any]] = []
    for i in range(13, -1, -1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        daily.append({"day": d, "count": int(day_counts.get(d, 0))})

    # 按书阅读次数排行
    book_counts: Counter = Counter(h.get("title", "") for h in read_history if h.get("title"))
    top_books = [{"title": t, "count": c} for t, c in book_counts.most_common(10)]

    # 进度分布
    total_words = sum(_words_to_int(e.get("words_num", "")) for e in shelf)
    finished = sum(1 for p in progress if _to_percent(p.get("percent", 0)) >= 99)
    reading = sum(1 for p in progress if 0 < _to_percent(p.get("percent", 0)) < 99)

    return {
        "read_events": len(read_history),
        "reading_books": len(progress),
        "shelf_books": len(shelf),
        "finished_books": finished,
        "in_progress": reading,
        "total_words": total_words,
        "daily": daily,
        "top_books": top_books,
        "active_days": len([d for d, c in day_counts.items() if c > 0]),
    }
