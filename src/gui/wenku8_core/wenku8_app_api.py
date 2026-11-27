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

搜索结果只给 aid/书名，完整书名（含括号别名）、作者、字数、简介要走
MewX 同款元数据接口：

    action=book&do=meta&aid={aid}&t=0
    action=book&do=info&aid={aid}&t=0   （meta 无结果时的短信息兜底）

封面是独立的二进制接口，返回 jpg 字节，不是普通图片 URL：

    action=book&do=cover&aid={aid}

下载链路同样有 App 专用接口（网页目录/正文被 Cloudflare 拦时仍可用）：

    action=book&do=list&aid={aid}&t=0       目录，volume/chapter XML
    action=book&do=text&aid={aid}&cid={cid}&t=0  正文纯文本

封面不在搜索线程里抓取（避免搜索变慢），只在 book 上标 ``cover_aid``，
由 BookCard 的后台线程按需调用 ``fetch_cover_bytes()``。

响应统一是 UTF-8，必须显式按 UTF-8 解码；requests 对未声明 charset 的
XML 默认用 ISO-8859-1，会把中文书名解成乱码进而匹配失败。

沙箱出口 IP 被文库8全系域名拦截，无法在此实时验证；协议正确性由离线单测保证。
"""

from __future__ import annotations

import base64
import html
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional
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
_META_TIMEOUT = 5
_INFO_TIMEOUT = 4
_COVER_TIMEOUT = 8
_LIST_TIMEOUT = 8
_TEXT_TIMEOUT = 10
# 搜索结果中最多补几本元数据：保证「恶魔高校」这类少量结果完整，
# 同时避免热门关键词返回几十本时串行放大搜索延迟。
_META_LIMIT = 8
# 榜单别名兜底的总时间预算（秒）：多榜单并发，全被拦也只是等这些秒。
_NOVELLIST_BUDGET = 10


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


def build_meta_param(aid: str) -> str:
    """构造完整元数据参数（MewX getNovelFullMeta）。"""
    return f"action=book&do=meta&aid={str(aid or '').strip()}&t=0"


def build_info_param(aid: str) -> str:
    """构造短元数据参数（MewX getNovelShortInfo，meta 不可用时的兜底）。"""
    return f"action=book&do=info&aid={str(aid or '').strip()}&t=0"


def build_cover_param(aid: str) -> str:
    """构造封面二进制参数（MewX getNovelCover）。"""
    return f"action=book&do=cover&aid={str(aid or '').strip()}"


def build_catalog_param(aid: str) -> str:
    """构造目录参数（MewX getNovelIndex，实际 do=list）。"""
    return f"action=book&do=list&aid={str(aid or '').strip()}&t=0"


def build_text_param(aid: str, cid: str) -> str:
    """构造正文参数（MewX getNovelContent）。"""
    return (f"action=book&do=text&aid={str(aid or '').strip()}"
            f"&cid={str(cid or '').strip()}&t=0")


# 榜单排序值（MewX getNOVELSORTBY / zsakvo getNovelList 通用）
NOVELLIST_SORTS = (
    "allvisit", "allvote", "monthvisit", "monthvote",
    "postdate", "lastupdate", "goodnum", "size",
)


def build_novellist_param(sort: str, page: int = 1) -> str:
    """构造榜单书目参数（MewX getNovelListWithInfo / zsakvo getNovelList）。

    关键：``novellist`` 返回的 ``Title`` 是**带括号别名的完整书名**
    （MewX 权威样例 ``恶魔高校DxD(High School DxD)``、
    ``约会大作战(DATE A LIVE)``），而 ``search&searchtype=articlename``
    只按主标题索引——这正是「紫罗兰」搜不到《薇尔莉特·伊芙加登
    (紫罗兰永恒花园)》的根因。榜单元数据本地匹配可覆盖别名。
    """
    s = str(sort or "allvisit").strip() or "allvisit"
    return f"action=novellist&sort={s}&page={max(1, int(page or 1))}&t=0"


def build_form(param: str, timetoken_ms: int | None = None) -> dict:
    """构造 App API POST 表单三件套。"""
    if timetoken_ms is None:
        timetoken_ms = int(time.time() * 1000)
    return {
        "appver": APP_VER,
        "request": encode_request(param),
        "timetoken": str(timetoken_ms),
    }


def _post(param: str, session=None, timeout: int = _TIMEOUT):
    """POST 参数串；返回 response 对象，网络异常返回 None（不抛给 UI）。"""
    sess = session or requests.Session()
    try:
        return sess.post(
            APP_API_URL,
            data=build_form(param),
            headers={"User-Agent": ANDROID_UA,
                     "Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001
        return None


def _decode_response(resp) -> str:
    """App API 返回 UTF-8；显式解码，避免 requests 误判 ISO-8859-1。"""
    if resp is None:
        return ""
    content = getattr(resp, "content", None)
    if content:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("gbk", errors="replace")
    return getattr(resp, "text", "") or ""


def _is_blocked_text(text: str) -> bool:
    low = (text or "").lower()
    return ("<h1>welcome</h1>" in low or "attention required" in low
            or "just a moment" in low or "cf-error" in low
            or "cf-ray" in low)


def parse_search_xml(xml_text: str) -> List[dict]:
    """解析 App API 搜索结果，兼容两种历史格式：

    * ``<item aid="2580">书名</item>``  —— 带书名
    * ``<item aid='1699'/>``            —— 仅 aid（MewX 1.x）
    """
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


def parse_novellist_xml(xml_text: str) -> List[dict]:
    """解析榜单书目 XML：``<item aid='X'><data name='Title'>书名(别名)</data>``。

    返回 [{id, title, author, cover_aid, source}]，title 保留括号别名原文。
    与 parse_search_xml 类似谨慎去重，兼容 ``<item/>`` 与 ``<item>...</item>``。
    """
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
        inner = m.group(2) or ""
        title = ""
        tm = re.search(
            r"<data\b[^>]*\bname\s*=\s*[\"']title[\"'][^>]*>(.*?)</data>",
            inner, re.I | re.S)
        if tm:
            title = _clean_xml_text(tm.group(1))
        if not title:
            # 无 Title 的旧榜单项：跳过（无法做别名匹配）
            continue
        seen.add(aid)
        out.append({
            "id": aid, "title": title, "author": "", "intro": "",
            "cover": "", "cover_aid": aid, "source": "wenku8", "_rel": 0,
        })
    return out


def parse_meta_xml(xml_text: str) -> Dict[str, str]:
    """解析 App API meta/info XML，键名统一转为小写。

    MewX 权威样例：
        <data name="Title" aid="1306"><![CDATA[书名(别名)]]></data>
        <data name="Author" value="作者"/>
        <data name="BookLength" value="105985"/>
    """
    out: Dict[str, str] = {}
    if not xml_text:
        return out
    for m in re.finditer(r"<data\b([^>]*?)(?:/>|>(.*?)</data>)",
                         xml_text, re.I | re.S):
        attrs = m.group(1) or ""
        nm = re.search(r"\bname\s*=\s*[\"']([^\"']+)[\"']", attrs, re.I)
        if not nm:
            continue
        key = nm.group(1).strip().lower()
        vm = re.search(r"\bvalue\s*=\s*[\"']([^\"']*)[\"']", attrs, re.I)
        value = vm.group(1).strip() if vm else ""
        if not value and m.group(2):
            inner = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1",
                           m.group(2), flags=re.I | re.S)
            inner = re.sub(r"<[^>]+>", "", inner)
            value = html.unescape(inner).strip()
        if value and key not in out:
            out[key] = value
    return out


def _pick(meta: Dict[str, str], *keys: str) -> str:
    for key in keys:
        val = str(meta.get(key, "") or "").strip()
        if val:
            return val
    return ""


def meta_to_book(meta: Dict[str, str], fallback: Optional[dict] = None) -> dict:
    """把 meta/info 字段并入搜索结果，保留括号别名原文。"""
    out = dict(fallback or {})
    if not meta:
        return out
    title = _pick(meta, "title", "name", "articlename")
    if title:
        out["title"] = title
    author = _pick(meta, "author", "authorname")
    if author:
        out["author"] = author
    intro = _pick(meta, "intropreview", "intro", "description", "summary")
    if intro:
        out["intro"] = intro
    words = _pick(meta, "booklength", "words", "length", "wordcount")
    if words:
        out["words_num"] = words
    tags = _pick(meta, "tags", "keyword", "keywords", "category")
    if tags:
        out["tags"] = tags
    return out


def fetch_meta(aid: str, session=None) -> Dict[str, str]:
    """取完整元数据；meta 接口失败时退回短信息接口。失败返回空字典。"""
    bid = str(aid or "").strip()
    if not bid.isdigit():
        return {}
    text = _decode_response(_post(build_meta_param(bid), session,
                                  timeout=_META_TIMEOUT))
    if _is_blocked_text(text):
        return {}
    meta = parse_meta_xml(text)
    if meta:
        return meta
    text = _decode_response(_post(build_info_param(bid), session,
                                  timeout=_INFO_TIMEOUT))
    if _is_blocked_text(text):
        return {}
    return parse_meta_xml(text)


def fetch_cover_bytes(aid: str, session=None) -> bytes:
    """取封面 jpg 字节；风控占位页或非图片内容一律返回空字节。"""
    bid = str(aid or "").strip()
    if not bid.isdigit():
        return b""
    resp = _post(build_cover_param(bid), session, timeout=_COVER_TIMEOUT)
    if resp is None:
        return b""
    content = getattr(resp, "content", None) or b""
    if not content:
        return b""
    head = content[:512].lower()
    if (b"<h1>welcome</h1>" in head or b"attention required" in head
            or b"just a moment" in head or b"cf-error" in head):
        return b""
    is_image = (
        content.startswith(b"\xff\xd8")                    # JPEG
        or content.startswith(b"\x89PNG\r\n\x1a\n")        # PNG
        or content.startswith(b"GIF8")                     # GIF
        or (content[:4] == b"RIFF" and content[8:12] == b"WEBP")
    )
    return content if is_image else b""


def _clean_xml_text(value: str) -> str:
    """清理 CDATA/HTML 标签与转义，供目录 XML 使用。"""
    text = str(value or "")
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def parse_catalog_xml(xml_text: str, aid: str = "") -> List[dict]:
    """解析 App API 目录 XML（MewX getNovelIndex 样例）。

        <package>
          <volume vid="41748"><![CDATA[第一卷 ...]]>
            <chapter cid="41749"><![CDATA[序章]]></chapter>
          </volume>
        </package>

    保留 vol 层级，过滤插图章节（与网页目录一致），并写入 app_cid 供
    正文接口 do=text 使用。没有 volume 包裹的旧格式也能兜底解析。
    """
    out: List[dict] = []
    image_only = {"插图", "插画", "图片", "图", "封面", "彩页"}
    if not xml_text:
        return out

    def _append_chapters(fragment: str, volume: str) -> None:
        for cm in re.finditer(r"<chapter\b([^>]*)>(.*?)</chapter>",
                              fragment, re.I | re.S):
            am = re.search(r"\bcid\s*=\s*[\"'](\d+)[\"']", cm.group(1) or "", re.I)
            if not am:
                continue
            cid = am.group(1)
            title = _clean_xml_text(cm.group(2))
            if not title or title in image_only:
                continue
            out.append({
                "id": cid,
                "title": title,
                "index": len(out),
                "vol": volume,
                "sub": "",
                "url": "",
                "aid": str(aid or ""),
                "app_cid": cid,
                "source": "wenku8",
            })

    for vm in re.finditer(r"<volume\b([^>]*)>(.*?)</volume>",
                          xml_text, re.I | re.S):
        inner = vm.group(2) or ""
        first = re.search(r"<chapter\b", inner, re.I)
        volume = _clean_xml_text(inner[:first.start()] if first else inner)
        _append_chapters(inner, volume)

    # 兜底：没有 volume 包裹时按整篇扫描，章节仍能下载（仅丢失卷层级）
    if not out:
        _append_chapters(xml_text, "")
    return out


def fetch_catalog_app(aid: str, session=None) -> List[dict]:
    """通过 App API 取目录；失败/风控返回空列表，由调用方回退网页。"""
    bid = str(aid or "").strip()
    if not bid.isdigit():
        return []
    text = _decode_response(_post(build_catalog_param(bid), session,
                                  timeout=_LIST_TIMEOUT))
    if _is_blocked_text(text):
        return []
    return parse_catalog_xml(text, bid)


def fetch_text_app(aid: str, cid: str, session=None) -> str:
    """通过 App API 取正文，输出与网页通道同构的缩进纯文本。"""
    bid, chapter_id = str(aid or "").strip(), str(cid or "").strip()
    if not bid.isdigit() or not chapter_id.isdigit():
        return ""
    text = _decode_response(_post(build_text_param(bid, chapter_id), session,
                                  timeout=_TEXT_TIMEOUT))
    if _is_blocked_text(text):
        return ""
    text = re.sub(r"<\?xml[^>]*\?>", "", text)
    text = html.unescape(text)
    lines: List[str] = []
    for raw in text.splitlines():
        # App API 用 <!--image-->URL<!--image--> 标记插图；正文不要混入图片标记
        line = re.sub(r"<!--image-->.*?<!--image-->", "", raw).strip()
        if not line:
            continue
        low = line.lower()
        if ("本文来自" in line or "wenku8.com" in low or "wenku8.net" in low
                or ("轻小说文库" in line and ("http" in low or "www." in low))):
            continue
        lines.append("    " + line)
    return "\n".join(lines)


def fetch_novellist_page(sort: str, page: int, session=None) -> List[dict]:
    """取单页榜单书目（带括号别名的完整书名）；失败/风控返回空。"""
    text = _decode_response(_post(build_novellist_param(sort, page), session,
                                  timeout=_TIMEOUT))
    if _is_blocked_text(text):
        return []
    return parse_novellist_xml(text)


def search_via_novellist(keyword: str, session=None, pages: int = 3,
                         sorts: tuple = NOVELLIST_SORTS) -> List[dict]:
    """按榜单书目元数据做本地别名匹配（覆盖服务端搜索命不中的别名书）。

    服务端 ``searchtype=articlename`` 只按主标题索引：搜「紫罗兰」找不到
    《薇尔莉特·伊芙加登(紫罗兰永恒花园)》。但榜单 ``novellist`` 的 Title
    带完整括号别名，本地归一化匹配即可命中。参考 App 的 SEARCH.md 明确
    写了「从可分页的书目元数据中继续筛选」，此函数即该机制的实现。

    并发拉多个榜单的前若干页，全部按同一套 wenku8_match 匹配；命中即
    保留完整 Title（含别名），并打 cover_aid 供卡片异步加载封面。
    """
    from .wenku8_match import matches as _matches, relevance as _relevance

    kw = str(keyword or "").strip()
    if not kw:
        return []
    pages = max(1, min(5, int(pages or 1)))
    tasks = [(s, p) for s in sorts for p in range(1, pages + 1)]

    raw: List[dict] = []
    if session is not None:
        # 单测/外部传入 session：串行，规避 requests.Session 跨线程竞态
        for s, p in tasks:
            raw.extend(fetch_novellist_page(s, p, session))
    else:
        # 并发但设总预算：全部请求被风控拦时也只等 _NOVELLIST_BUDGET 秒，
        # 不让别名兜底把搜索拖回「半分钟」（延续第 90 条提速要求）。
        from concurrent.futures import as_completed, TimeoutError as FTimeout
        deadline = time.monotonic() + _NOVELLIST_BUDGET
        pool = ThreadPoolExecutor(max_workers=min(8, len(tasks)))
        try:
            fmap = {pool.submit(fetch_novellist_page, s, p): (s, p)
                    for s, p in tasks}
            try:
                for fut in as_completed(fmap, timeout=_NOVELLIST_BUDGET):
                    try:
                        raw.extend(fut.result() or [])
                    except Exception:  # noqa: BLE001
                        pass
                    if time.monotonic() > deadline:
                        break
            except FTimeout:
                pass
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    hits: List[dict] = []
    seen = set()
    for b in raw:
        bid = b["id"]
        if bid in seen:
            continue
        seen.add(bid)
        if _matches(kw, b["title"]):
            b["_rel"] = _relevance(kw, b["title"])
            hits.append(b)
    hits.sort(key=lambda b: -b["_rel"])
    for b in hits:
        b.pop("_rel", None)
    return hits


def _enrich_one(book: dict, session=None) -> dict:
    """单本补元数据；失败时只保留搜索结果原有字段，绝不让搜索整体失败。"""
    out = dict(book or {})
    aid = str(out.get("id", "") or "").strip()
    if aid:
        # 封面走独立二进制接口，由卡片后台线程按 aid 加载，不阻塞搜索
        out["cover_aid"] = aid
    try:
        meta = fetch_meta(aid, session=session)
    except Exception:  # noqa: BLE001
        meta = {}
    return meta_to_book(meta, out)


def enrich_app_books(books: List[dict], session=None,
                     max_items: int = _META_LIMIT) -> List[dict]:
    """并发补全前若干条搜索结果的元数据，保持原有顺序。

    服务端搜索结果可信，这里只补字段、不再做本地书名过滤。并发度保守
    设为 4，避免热门关键词一次补几十本把搜索拖慢；调用方传入 session
    时改为串行，规避 requests.Session 跨线程竞态（单测也走这条路径）。
    """
    if not books:
        return []
    out = [dict(b or {}) for b in books]
    count = min(max(0, int(max_items or 0)), len(out))
    if count <= 0:
        return out
    if session is not None or count == 1:
        for i in range(count):
            out[i] = _enrich_one(out[i], session=session)
        return out
    with ThreadPoolExecutor(max_workers=min(4, count)) as pool:
        enriched = list(pool.map(lambda b: _enrich_one(b), out[:count]))
    out[:count] = enriched
    return out


def search_via_app_api(keyword: str, session=None, page: int = 1) -> List[dict]:
    """通过官方 App API 搜索，命中返回书目列表，失败返回空列表（不抛异常）。

    重要：服务端已经按 articlename 做了检索，这里不能再做本地 ``matches``
    二次过滤。此前搜「紫罗兰」时服务端返回了《紫罗兰永恒花园》，却被本地
    模糊匹配误杀，造成 0 结果。本地相关性只用于排序，不用于裁剪。
    """
    from .wenku8_match import relevance as _relevance

    param = build_search_param(keyword, page=page)
    text = _decode_response(_post(param, session, timeout=_TIMEOUT))
    if _is_blocked_text(text):
        return []
    books = parse_search_xml(text)
    if not books:
        return []
    named = [b for b in books if b["title"]]
    aid_only = [b for b in books if not b["title"]]
    for b in named:
        b["_rel"] = _relevance(keyword, b["title"])
    named.sort(key=lambda b: -b["_rel"])
    hits = named + aid_only
    for b in hits:
        b.pop("_rel", None)
        # 卡片据此走 App API 封面接口异步加载；搜索本身不额外发请求
        b["cover_aid"] = b["id"]
    return enrich_app_books(hits, session=session)
