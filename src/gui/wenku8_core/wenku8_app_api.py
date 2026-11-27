# -*- coding: utf-8 -*-
"""文库8「官方 App API」通道。

多个第三方客户端（zsakvo/wenku8x、go-novel-dl 等）用这条接口实现在线搜索，
它是站点专门提供给 App 的接口，与网页搜索页是两套服务：

    POST http://app.wenku8.com/android.php
    form:
        appver    = 1.13
        request   = base64(UTF-8 的 "action=...&..." 参数串)
        timetoken = 毫秒时间戳
    headers: Android UA

搜索返回 UTF-8 XML：
    <root><item aid="2580">邻家的天使大人</item>...</root>
登录返回纯文本 "1"（成功）/ "4"（会话失效）。

编码要点（参考 wenku8x 实测可用实现）
------------------------------------
* ``searchkey`` 直接放**原始中文**，由 ``build_form`` 对整个参数串做
  base64(UTF-8)。**不能**预先 percent 编码，否则服务器解码后拿到字面的
  ``%E9%82%BB...``，中文书名永远搜不到（这是此前搜索失败的一个根因）。
* 响应是 UTF-8 XML，必须显式按 UTF-8 解码；requests 对未声明 charset 的
  XML 默认用 ISO-8859-1，会把中文书名解成乱码进而匹配失败。

为什么加这条通道
----------------
网页搜索（/so.php、/modules/article/search.php）由 Cloudflare 保护，脚本
请求指纹易被拦；App API 面对的是 Android 客户端，CF 策略不同，用 Android
UA + base64 请求体通常可正常访问。参考 App 发行版正是走这条接口，所以能
「美化官网」般正常搜索。本模块把它作为搜索的第一优先级通道，网页通道
保留兜底。

沙箱出口 IP 被文库8全系域名拦截，无法在此实时验证；本模块的协议严格按
wenku8x 源码（lib/http/api.dart + ajax.dart）实现，可离线单测协议正确性。
"""

from __future__ import annotations

import base64
import time
from typing import List

import requests

# App API 固定端点（Android 客户端接口，不走 www 网页域名）
APP_API_URL = "http://app.wenku8.com/android.php"
APP_VER = "1.13"
# 与 wenku8x 发行版一致的 Android 指纹
ANDROID_UA = (
    "Dalvik/2.1.0 (Linux; U; Android 11; IN2010 Build/RP1A.201005.001)"
)
_TIMEOUT = 8


def encode_request(param: str) -> str:
    """把 'action=...&...' 参数串按 App 协议编码为 base64(UTF-8)。"""
    return base64.b64encode(str(param or "").encode("utf-8")).decode("ascii")


def build_search_param(keyword: str, search_type: str = "articlename",
                       page: int = 1) -> str:
    """构造搜索请求参数串：searchkey 放原始中文，整体交给 base64(UTF-8)。

    与 wenku8x 的 ``searchNovelByNovelName`` 完全一致：参数串里是原始中文，
    再由 ``build_form`` 对整个串做 base64(UTF-8)。切勿预先 percent 编码，
    否则服务器 base64 解码后拿到字面的 ``%E9%82%BB...``，搜不到中文书名。
    """
    raw = str(keyword or "").strip()
    param = f"action=search&searchtype={search_type}&searchkey={raw}&t=0"
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
    """解析 App API 搜索结果 XML：<item aid="id">书名</item>。"""
    import re
    out: List[dict] = []
    seen = set()
    if not xml_text:
        return out
    # 兼容 <item aid="2580">书名</item> 与属性顺序变化
    for m in re.finditer(r"<item\b[^>]*\baid\s*=\s*[\"'](\d+)[\"'][^>]*>(.*?)</item>",
                         xml_text, re.I | re.S):
        aid = m.group(1)
        if aid in seen:
            continue
        seen.add(aid)
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if not title:
            continue
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
    hits = [b for b in books if _matches(keyword, b["title"])]
    for b in hits:
        b["_rel"] = _relevance(keyword, b["title"])
    hits.sort(key=lambda b: -b["_rel"])
    # _rel 只用于内部相关性排序，对外返回保持与其他搜索结果一致的干净结构
    for b in hits:
        b.pop("_rel", None)
    return hits
