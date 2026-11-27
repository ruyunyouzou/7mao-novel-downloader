# -*- coding: utf-8 -*-
"""文库8「官方 App API」通道。

多个第三方客户端用这条接口实现在线搜索，它是站点专门提供给 App 的接口，
与网页搜索页是两套服务：

    POST http://app.wenku8.com/android.php
    form:
        appver    = 1.13
        request   = base64(UTF-8 的 "action=...&..." 参数串)
        timetoken = 毫秒时间戳
    headers: Android UA

协议来源与编码要点（重要，此前踩过坑）
------------------------------------
本模块协议严格对齐两个真实客户端的权威源码：

1. MewX/light-novel-library_Wenku8_Android（原作者，2014 起）
   ``previous_versions/v0.5.2.1/.../api/Wenku8Interface.java``:
       searchkey = LightNetwork.encodeToHttp(novelName)
                 = URLEncoder.encode(novelName, "UTF-8")   // 中文 -> %E9%82%BB...
       request   = LightBase64.EncodeBase64(param)         // base64(UTF-8)
   ``util/LightNetwork.java`` 注释明确：encodeToHttp("妹") == "%E5%A6%B9"。

2. zsakvo/wenku8x（appver=1.13）``lib/http/api.dart``:
       "action=search&searchtype=articlename&searchkey=$bookName&t=0"
   表面看是直接拼原始中文，但它经 Dio ``FormData.fromMap`` +
   ``contentType: formUrlEncodedContentType`` 传输，base64 串由 Dio 负责编码。
   二者对服务器而言等价：服务器 base64 解码后会对 searchkey 做 URL 解码，
   ``%E9%82%BB...`` 与 ``邻家...`` 都能命中。

   本模块手动构造 form，因此**必须自己把中文做 UTF-8 percent 编码**，
   不能照搬 zsakvo 的"原始中文"字面写法（那是 Dio 代劳的结果）。

返回 XML 有两种历史格式，都要兼容：
    <result><item aid='1699'/></result>              （MewX 1.x，仅 aid）
    <root><item aid="2580">邻家的天使大人</item>...</root>  （新版，带书名）
仅 aid 的结果无法在本地做书名模糊匹配，此时信任服务端搜索结果直接保留。

响应统一是 UTF-8，必须显式按 UTF-8 解码；requests 对未声明 charset 的
XML 默认用 ISO-8859-1，会把中文书名解成乱码进而匹配失败。

沙箱出口 IP 被文库8全系域名拦截，无法在此实时验证；协议正确性由离线单测保证。
"""

from __future__ import annotations

import base64
import time
from typing import List
from urllib.parse import quote

import requests

# App API 固定端点（Android 客户端接口，不走 www 网页域名）
APP_API_URL = "http://app.wenku8.com/android.php"
APP_VER = "1.13"
# 与客户端发行版一致的 Android 指纹
ANDROID_UA = (
    "Dalvik/2.1.0 (Linux; U; Android 11; IN2010 Build/RP1A.201005.001)"
)
_TIMEOUT = 8


def encode_request(param: str) -> str:
    """把 'action=...&...' 参数串按 App 协议编码为 base64(UTF-8)。"""
    return base64.b64encode(str(param or "").encode("utf-8")).decode("ascii")


def encode_keyword(keyword: str) -> str:
    """按 MewX encodeToHttp：UTF-8 percent 编码（中文 -> %E9%82%BB...）。"""
    return quote(str(keyword or "").strip(), safe="", encoding="utf-8")


def build_search_param(keyword: str, search_type: str = "articlename",
                       page: int = 1) -> str:
    """构造搜索请求参数串：searchkey 先 UTF-8 URL 编码，再由 form 整体 base64。

    对齐 MewX ``Wenku8Interface.searchNovelByNovelName``：
        action=search&searchtype=articlename&searchkey=<urlencoded>&t=0
    t 为语言 id，0 = 简体中文。
    """
    kw = encode_keyword(keyword)
    param = f"action=search&searchtype={search_type}&searchkey={kw}&t=0"
    if page > 1:
        param += f"&page={page}"
    return param


def build_form(param: str, timetoken_ms: int | None = None) -> dict:
    """构造 App API POST 表单三件套。"""
    if timetoken_ms is None:
        timetoken_ms = int(time.time() * 1000)
    return {
        "appver": APP_VER,
        "request": encode_request(param),
        "timetoken": str(timetoken_ms),
    }


def parse_search_xml(xml_text: str) -> List[dict]:
    """解析 App API 搜索结果，兼容两种历史格式：

    * ``<item aid="2580">书名</item>``  —— 带书名
    * ``<item aid='1699'/>``            —— 仅 aid（MewX 1.x）
    """
    import re
    out: List[dict] = []
    seen = set()
    if not xml_text:
        return out
    for m in re.finditer(r"<item\b([^>]*?)(?:/>|>(.*?)</item>)",
                         xml_text, re.I | re.S):
        attrs = m.group(1) or ""
        am = re.search(r"\baid\s*=\s*[\"'](\d+)[\"']", attrs, re.I)
        if not am:
            continue
        aid = am.group(1)
        if aid in seen:
            continue
        seen.add(aid)
        inner = m.group(2) or ""
        title = re.sub(r"<[^>]+>", "", inner).strip()
        out.append({
            "id": aid, "title": title, "author": "", "intro": "",
            "cover": "", "source": "wenku8", "_rel": 0,
        })
    return out


def search_via_app_api(keyword: str, session=None, page: int = 1) -> List[dict]:
    """通过官方 App API 搜索，命中返回书目列表，失败返回空列表（不抛异常）。"""
    from .wenku8_match import matches as _matches, relevance as _relevance

    param = build_search_param(keyword, page=page)
    form = build_form(param)
    sess = session or requests.Session()
    try:
        resp = sess.post(
            APP_API_URL,
            data=form,
            headers={"User-Agent": ANDROID_UA,
                     "Content-Type": "application/x-www-form-urlencoded"},
            timeout=_TIMEOUT,
        )
        # App API 返回 UTF-8 XML，必须显式解码；requests 对未声明 charset 的
        # 响应默认 ISO-8859-1，会把中文书名解成乱码导致匹配失败。
        content = getattr(resp, "content", None)
        if content:
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                text = content.decode("gbk", errors="replace")
        else:
            text = resp.text or ""
    except Exception:  # noqa: BLE001
        return []
    # Welcome 占位页 / 风控页直接判失败
    low = text.lower()
    if ("<h1>welcome</h1>" in low or "attention required" in low
            or "just a moment" in low or "cf-error" in low):
        return []
    books = parse_search_xml(text)
    if not books:
        return []
    # 带书名的结果做本地模糊匹配；仅 aid 的结果信任服务端搜索，直接保留。
    named = [b for b in books if b["title"]]
    aid_only = [b for b in books if not b["title"]]
    hits = [b for b in named if _matches(keyword, b["title"])]
    for b in hits:
        b["_rel"] = _relevance(keyword, b["title"])
    hits.sort(key=lambda b: -b["_rel"])
    # 仅 aid 的结果排在带书名命中之后
    hits.extend(aid_only)
    # _rel 只用于内部相关性排序，对外返回保持与其他搜索结果一致的干净结构
    for b in hits:
        b.pop("_rel", None)
    return hits
