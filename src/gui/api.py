# -*- coding: utf-8 -*-
"""
对 SLQimao 核心能力的非交互式封装，供 GUI 调用。

原 SEARCH() 是交互式函数（内部 input），无法在 GUI 中使用，
这里复用其签名与请求头逻辑，提供纯函数式接口。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

# 签名与请求头走统一实现（与 SLQimao 内核逐位等价，避免两份逻辑漂移）；
# Book 仍用内核自带的目录解析，保证与 CLI 行为一致。
from SLQimao.book import Book
from .qimao_sign import get_headers, sign_params as sign_url


class ApiError(Exception):
    """API 调用异常。"""


def search_books(keyword: str, timeout: int = 10, tab: str = "0",
                 page: int = 1) -> List[Dict[str, Any]]:
    """
    搜索七猫小说，返回结构化结果列表。

    :param keyword: 搜索关键词
    :param timeout: 请求超时秒数
    :param tab: 搜索类型，0=搜书，1=搜作者
    :param page: 页码，搜索结果分页（接口无总页数，可递增翻页）
    :return: 书籍字典列表，每项含 id / title / author / words_num / score / cover 等
    """
    params = {
        "extend": "",
        "tab": tab,
        "gender": "0",
        "refresh_state": "8",
        "page": str(page),
        "wd": keyword,
        "is_short_story_user": "0",
    }
    try:
        resp = requests.get(
            "https://api-bc.wtzw.com/search/v1/words",
            params=sign_url(params),
            headers=get_headers("00000000"),
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise ApiError(f"搜索请求失败: {exc}") from exc

    import re

    def _clean(text: Any) -> str:
        """去掉接口返回里的高亮 HTML 标签。"""
        return re.sub(r"<[^>]+>", "", str(text or "")).strip()

    books = (data.get("data") or {}).get("books") or []
    results: List[Dict[str, Any]] = []
    for b in books:
        # 过滤非书籍项：接口会混入 show_type="4" 的「大家还在搜」标签项，
        # 这类项没有 id / 书名，旧代码把它当书渲染成「未知书名/未知作者」空卡片。
        # show_type="0" 才是真实书籍（核心库也只认这类）。
        if str(b.get("show_type", "0")) != "0":
            continue
        if not str(b.get("id", "")).strip():
            continue
        # sub_title 形如 "影视・影视原著・完结・536万字"，可提取字数/状态
        sub = _clean(b.get("sub_title"))
        item = {
            "id": str(b.get("id", "")),
            "title": _clean(b.get("original_title") or b.get("title")) or "未知书名",
            "author": _clean(b.get("original_author") or b.get("author")) or "未知作者",
            "words_num": b.get("words_num") or (sub.split("・")[-1] if "・" in sub else "未知"),
            "score": b.get("score", ""),             # 评分
            "cover": b.get("image_link", ""),        # 封面
            "tags": b.get("ptags", ""),              # 标签（逗号分隔）
            "intro": _clean(b.get("intro")),         # 简介
            "is_over": str(b.get("is_over", "0")) == "1",  # 是否完结
            "sub_title": sub,
            "characters": b.get("characters", ""),   # 主角
        }
        # 保留原始字段，供插件使用
        item["raw"] = b
        results.append(item)
    return results


def get_book_info(book_id: str, fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    获取书籍详情（标题、作者、简介、封面等）。

    老接口 ``api/v1/reader/detail`` 已停更并返回 401，改用带签名的新版
    ``api/v4/book/detail``（需 imei_ip / teeny_mode）。若新接口也失败，
    则回退到搜索结果中的 fallback 信息，不抛异常。

    :param book_id: 书籍 ID
    :param fallback: 搜索结果中的书籍信息，用作接口失败时的兜底
    :return: 信息字典
    """
    detail_url = "https://api-bc.wtzw.com/api/v4/book/detail"
    try:
        params = sign_url({
            "id": book_id,
            "imei_ip": "2937357107",
            "teeny_mode": "0",
        })
        resp = requests.get(
            detail_url,
            params=params,
            headers=get_headers(book_id),
            timeout=12,
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise ApiError(str(data.get("errors")))
        node = data.get("data") or {}
        book = node.get("book") or node
        if not book.get("title"):
            raise ApiError("详情接口未返回书名")

        tag_list = book.get("book_tag_list") or []
        tags = "，".join(
            t.get("title", "") for t in tag_list
            if isinstance(t, dict) and t.get("title")
        )
        return {
            "id": book_id,
            "title": Book._rename(str(book.get("title", ""))),
            "author": str(book.get("author") or ""),
            "intro": str(book.get("intro") or ""),
            "words_num": book.get("words_num") or "未知",
            "tags": tags,
            "img_url": book.get("image_link") or "",
            "degraded": False,
        }
    except Exception as exc:  # noqa: BLE001
        if fallback:
            # 搜索结果已带 intro 时直接展示真实简介，不再显示降级提示
            real_intro = str(fallback.get("intro") or "").strip()
            degraded = not bool(real_intro)
            return {
                "id": book_id,
                "title": fallback.get("title", "未知书名"),
                "author": fallback.get("author", "未知作者"),
                "intro": real_intro or (
                    "详情接口暂不可用（上游已停更/鉴权变更），此处仅显示搜索结果中的基础信息。"
                ),
                "words_num": fallback.get("words_num", "未知"),
                "tags": fallback.get("tags", ""),
                "img_url": fallback.get("cover", ""),
                "degraded": degraded,
                "error": str(exc),
            }
        raise ApiError(f"获取书籍信息失败: {exc}") from exc


def get_catalog(book_id: str) -> List[Dict[str, Any]]:
    """获取书籍目录列表。"""
    novel = Book(book_id)
    try:
        novel.get_catalog()
    except Exception as exc:  # noqa: BLE001
        raise ApiError(f"获取目录失败: {exc}") from exc
    return novel.catalog
