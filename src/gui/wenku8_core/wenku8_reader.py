# -*- coding: utf-8 -*-
"""文库8 详情/目录/正文读取与链接归一化。

站点全站 GBK 编码；正文在 #content，明文无字体混淆（实测 2026-11）。
所有网络请求经 wenku8_auth.make_session() 注入登录态。
"""

from __future__ import annotations

import re
import time as _time
from typing import Any, Dict, List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .wenku8_auth import make_session, get_base_url

BASE = "https://www.wenku8.cc"  # 兼容旧引用；实际请求统一走 _base()


def _base() -> str:
    """当前站点根地址（用户设置/探测结果/候选），所有请求动态取用。"""
    try:
        return get_base_url()
    except Exception:  # noqa: BLE001
        return BASE

_RE_BOOK_ID = re.compile(r"/book/(\d+)\.htm")
_RE_NOVEL_PATH = re.compile(r"/novel/(\d+)/(\d+)/")


def _decode(resp) -> str:
    """文库8 为 GBK；用浏览器等价策略解码（gbk 容错）。"""
    resp.encoding = "gbk"
    return resp.text


# 站点拦截 / 限流页特征：这些响应HTTP状态可能是200，旧代码当成成功解析，
# 解析不到 #content 就静默返回空正文 → 大量章节"未取到"、反复补抓触发风暴。
# 识别它们能从根本上把「拦截」与「真的空章」区分开。
_BLOCK_MARKERS = (
    "just a moment", "cf-browser-verification", "cf_chl_", "challenge-platform",
    "checking your browser", "enable javascript and cookies", "attention required",
    "cloudflare", "访问验证", "人机验证", "请求过于频繁", "too many requests",
    "429", "502 bad gateway", "503 service unavailable", "504 gateway timeout",
)


def _looks_like_block_page(resp, html: str = "") -> bool:
    """判断响应是否为拦截/限流/网关错误页（无论状态码是不是 200）。"""
    status = int(getattr(resp, "status_code", 0) or 0)
    if status in (400, 401, 403, 408, 409, 410, 425, 429, 500, 502, 503, 504, 507, 520, 521, 522, 524):
        return True
    sample = (html or "")[:4096].lower()
    if not sample:
        try:
            sample = (getattr(resp, "text", "") or "")[:4096].lower()
        except Exception:  # noqa: BLE001
            sample = ""
    #  Challenge 页通常很短且带这些标记；正文页很长，避免误伤正文里提到这些词。
    if len(sample) < 4096 and any(m in sample for m in _BLOCK_MARKERS):
        return True
    return False


def _retry_delay(i: int, resp=None) -> float:
    """指数退避 + 抖动；尊重 Retry-After。避免固定短间隔引发重试风暴。"""
    import random as _rnd
    delay = min(4.0, 0.5 * (2 ** i)) + _rnd.uniform(0, 0.4)
    if resp is not None:
        try:
            headers = getattr(resp, "headers", {}) or {}
            key = next((k for k in headers if str(k).lower() == "retry-after"), None)
            if key is not None:
                retry_after = float(headers[key])
                delay = min(10.0, max(delay, retry_after))
        except (TypeError, ValueError):  # noqa: BLE001
            pass
    return delay


def _get_with_retry(sess, url: str, timeout: int = 20, attempts: int = 3,
                    validate=None) -> Any:
    """带退避重试的 GET（下载丢章根治）。

    httpcloak 在网络抖动时会抛 ``unexpected EOF`` / 连接重置，普通 requests
    也会有偶发超时；站点还会用 HTTP 200 的 Cloudflare 挑战页 / 限流页来挡爬虫。
    这些都必须当成"本次失败"重试，否则静默产出空章节。这里对每次请求做有限
    重试 + 指数退避（尊重 Retry-After），让瞬态故障自愈；重试仍失败才返回最后
    一次结果/抛异常交给上层。

    :param validate: 可选回调 ``f(resp) -> bool``，False 表示响应不是目标页（如
        正文页没有 #content），也触发重试。只在有响应体时调用。
    :return: 成功返回响应对象；全部失败时返回最后一次的响应（供调用方检查），
        若连一次都没成功（每次都抛异常）则抛出最后一次异常。
    """
    last_exc: Exception | None = None
    last_resp: Any = None
    tries = max(1, int(attempts))
    for i in range(tries):
        try:
            resp = sess.get(url, timeout=timeout)
            last_resp = resp
            bad = _looks_like_block_page(resp)
            if not bad and validate is not None:
                try:
                    bad = not bool(validate(resp))
                except Exception:  # noqa: BLE001 - validate 异常不当成功
                    bad = True
            if bad:
                if i < tries - 1:
                    _time.sleep(_retry_delay(i, resp))
                    continue
                return resp  # 最后一次：返回给调用方判定，不无限重试
            return resp
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if i < tries - 1:
                _time.sleep(_retry_delay(i))
    if last_resp is not None:
        return last_resp
    if last_exc is not None:
        raise last_exc
    return None


def resolve_book_id(raw: str) -> str:
    """把各种输入归一化为纯数字书号。

    支持：
      - 4343
      - https://www.wenku8.cc/book/4343.htm
      - http://www.wenku8.com/book/4343.htm
      - /book/4343.htm
      - novel 路径 /novel/4/4343/xxx.htm
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    m = _RE_BOOK_ID.search(text)
    if m:
        return m.group(1)
    m = _RE_NOVEL_PATH.search(text)
    if m:
        return m.group(2)
    if text.isdigit():
        return text
    return ""


def _index_prefix(book_id: str) -> str:
    """从本地索引 cover 的 ``/image/{n}/`` 反推目录前缀（零网络）。

    全站索引 4471 本里有 4464 本的 cover 携带真实分类号，与详情页一致。
    用它替代开局那次 ``/book/{id}.htm`` 请求——被限流的网络上这一请求
    每次 3-5s，还会触发拦截退避，是下载开局最纯粹的浪费。
    """
    try:
        from .wenku8_index import search_index_book
        cover = str((search_index_book(str(book_id).strip()) or {}).get("cover") or "")
        m = re.search(r"/image/(\d+)/", cover)
        if m:
            return f"/novel/{m.group(1)}/{book_id}/"
    except Exception:  # noqa: BLE001
        pass
    return ""


def _novel_prefix(html: str, book_id: str) -> str:
    """从详情页/目录页推断 novel 路径前缀（如 /novel/4/4343/）。

    文库8 的分类号 n 会变化，不能写死；从页面里的章节链接反推最稳。
    """
    m = re.search(rf"/novel/(\d+)/{book_id}/\d+\.htm", html)
    if m:
        return f"/novel/{m.group(1)}/{book_id}/"
    m = re.search(rf"href=\"([^\"]*novel/\d+/{book_id}/)", html)
    if m:
        return m.group(1)
    # 挑刺 #31：分类号 0 是无效假值，实测主站轻小说分类多为 1；
    # 兜底改用 1，显著降低详情页被拦时目录请求必然 404 的概率。
    return f"/novel/1/{book_id}/"


def fetch_detail(book_id: str, session=None) -> Dict[str, str]:
    """取书籍详情：title / author / intro / cover / words_num / tags。

    网页详情可能被 Cloudflare 拦，也可能从 <title> 拆书名时丢掉括号别名；
    因此网页只作为基础来源，权威字段优先取官方 App API 的 meta 接口
    （MewX 原作者同款，Title 保留「书名(别名)」原文）。
    """
    bid = resolve_book_id(book_id)
    sess = session or make_session()

    # 提速：本地全站索引字段齐全（4471本，含 title/author/tags/intro/cover/length），
    # 读本地 JSON 零网络。用户慢网络下开局详情页请求每次 3-5s + 拦截退避，
    # 下载一本书开局原本白发 2 次详情请求（articleinfo + book.htm）。索引齐全
    # 时直接用，0 网络；只有索引缺失/字段不全才走网页。全书通用，无书号硬编码。
    local: Dict[str, str] = {}
    try:
        from .wenku8_index import search_index_book
        local = search_index_book(bid) or {}
    except Exception:  # noqa: BLE001
        pass

    # 索引字段齐全就直接返回，连 BeautifulSoup 解析都省掉。
    # tags 在索引里是空格分隔（"校园 欢乐向 青春"），统一成网页同款逗号分隔，
    # 保证 UI/筛选/测试看到的格式一致。
    if (local and local.get("title") and local.get("author")
            and local.get("cover") and local.get("intro")):
        _idx_tags = str(local.get("tags") or "").strip()
        if _idx_tags:
            _idx_tags = ",".join(p for p in re.split(r"[\s,，]+", _idx_tags) if p)
        return {
            "title": str(local.get("title") or ""),
            "author": str(local.get("author") or ""),
            "intro": str(local.get("intro") or ""),
            "cover": str(local.get("cover") or ""),
            "words_num": str(local.get("length") or ""),
            "tags": _idx_tags,
        }

    html = ""
    # hikari_novel_flutter 等参考项目用 articleinfo.php 取详情并解析标签；
    # 旧 /book/{id}.htm 保留兜底。任一页面可用即可，全部失败仍能走索引。
    detail_urls = (
        f"{_base()}/modules/article/articleinfo.php?id={bid}",
        f"{_base()}/book/{bid}.htm",
    )
    for url in detail_urls:
        try:
            resp = _get_with_retry(sess, url, timeout=10)
            candidate = _decode(resp)
            low = (candidate or "").lower()
            if ("<h1>welcome</h1>" in low or "attention required" in low
                    or "just a moment" in low or "cf-error" in low):
                continue
            if candidate:
                html = candidate
                break
        except Exception:  # noqa: BLE001
            continue
    soup = BeautifulSoup(html, "html.parser")

    title, author, intro, cover = "", "", "", ""

    # 书名/作者：网页 <title> 格式「书名 - 作者 - 分类 - 轻小说文库」。
    # 挑刺 #32：站点后缀不一定是 4 段（实测有「书名 - 作者 - 轻小说文库」
    # 的 3 段式，旧逻辑 len>=4 才取 author，丢作者）。改为过滤站点/分类
    # 尾巴后，把第二段直接当作者。
    if soup.title:
        parts = [p.strip() for p in soup.title.get_text().split(" - ")]
        site_suffixes = ("轻小说文库", "wenku8", "小说文库")
        # 只剔除「纯站点尾巴」段，避免误伤书名里恰好含这些词的书。
        meaningful = [
            p for p in parts
            if p and not any(p.lower() == s or p.lower().endswith("-" + s)
                             for s in site_suffixes)
        ]
        if meaningful:
            title = meaningful[0]
            if len(meaningful) >= 2:
                author = meaningful[1]

    # 详情页表格里的「小说作者：xxx」/「作者：xxx」
    if not author:
        m = re.search(r"(?:小说)?作者[：:]\s*([^<\s]+)", html)
        if m:
            author = m.group(1).strip()

    # 封面：img.wenku8.com/image/{n}/{id}/{id}s.jpg
    for img in soup.select("img[src]"):
        src = img.get("src", "")
        low = src.lower()
        # 挑刺 #33：既接受 CDN 绝对地址（含 wenku8 + bid/cover），也接受
        # 站内相对路径（/images/cover.jpg 等，不含 wenku8 域名）。
        is_cdn = "wenku8" in low and ("cover" in low or bid in src)
        is_relative = bool(src) and not src.lower().startswith(("http://", "https://")) \
            and ("cover" in low or bid in src)
        if is_cdn or is_relative:
            cover = urljoin(_base(), src)
            break

    # 简介：含「内容简介」的容器
    for td in soup.select("td"):
        txt = td.get_text(" ", strip=True)
        if "内容简介" in txt and len(txt) > 20:
            intro = re.sub(r"^内容简介[：:]\s*", "", txt).strip()
            break
    if not intro:
        m = re.search(r"内容简介[：:]?\s*</td>\s*<td[^>]*>(.*?)</td>", html, re.S)
        if m:
            intro = BeautifulSoup(m.group(1), "html.parser").get_text(" ", strip=True)

    # 标签：hikari_novel_flutter 从详情页第一个含「标签」的 span 解析，
    # 形如「标签：校园 欢乐向 青春 恋爱」。这里兼容 span 文本与原始 HTML。
    tags = ""
    for node in soup.select("span"):
        txt = node.get_text(" ", strip=True)
        if "标签" in txt or re.search(r"(?i)\btag", txt):
            raw = re.sub(r"^.*?(?:标签|tag)[：:]\s*", "", txt, flags=re.I)
            parts = [p.strip() for p in re.split(r"[\s,，]+", raw) if p.strip()]
            tags = ",".join(parts[:12])
            if tags:
                break
    if not tags:
        m = re.search(r"标签[：:]\s*([^<]+)", html)
        if m:
            parts = [p.strip() for p in re.split(r"[\s,，]+", m.group(1)) if p.strip()]
            tags = ",".join(parts[:12])
    if not tags:
        # .cc 详情页实际格式：<b>作品Tags：校园 奇幻 战斗 …</b>
        m = re.search(r"作品\s*Tags[：:]\s*([^<]+)", html, re.I)
        if m:
            parts = [p.strip() for p in re.split(r"[\s,，]+", m.group(1)) if p.strip()]
            tags = ",".join(parts[:12])

    words_num = ""
    # .cc 详情页实际格式：<td …>全文长度：4055626字</td>
    m = re.search(r"全文长度[：:]\s*([\d,]+)\s*字", html)
    if m:
        words_num = m.group(1).replace(",", "")

    # 挑刺 #29 + #117：网页字段缺补只用「本地全站索引」，绝不再调 App API。
    # 根因：app.wenku8.com/android.php 已关停（返回 nginx Welcome），原先
    # need_meta 兜底会串行白等 meta(5s)+info(4s)=9s，网页被风控拦导致字段
    # 缺失时必触发——这就是「详情页加载很慢」的主因。索引随包分发、字段
    # 齐全（title/author/tags/description/cover/length），读本地 JSON 零网络。
    need_meta = (not title or not author or not words_num or not intro)
    if need_meta:
        try:
            from .wenku8_index import search_index_book
            local = search_index_book(bid) or {}
            title = title or str(local.get("title") or "")
            author = author or str(local.get("author") or "")
            intro = intro or str(local.get("intro") or "")
            cover = cover or str(local.get("cover") or "")
            tags = tags or str(local.get("tags") or "")
            words_num = words_num or str(local.get("length") or "")
        except Exception:  # noqa: BLE001
            pass

    return {
        "title": title,
        "author": author,
        "intro": intro,
        "cover": cover,
        "words_num": words_num,
        "tags": tags,
    }


def fetch_book_meta(book_id: str, session=None) -> Dict[str, str]:
    """取下载所需元信息：含 novel 路径前缀，供目录/正文拼 URL。

    提速：分类号优先从本地索引 cover 反推，零网络。只有索引缺 cover 时才
    访问 ``/book/{id}.htm``。此前每次下载开局都白发这一请求（慢网络 3-5s
    + 拦截退避），且与 fetch_detail / fetch_catalog 里的同类请求重复。
    """
    bid = resolve_book_id(book_id)
    sess = session or make_session()
    prefix = _index_prefix(bid)
    if not prefix:
        try:
            resp = sess.get(f"{_base()}/book/{bid}.htm", timeout=20)
            prefix = _novel_prefix(_decode(resp), bid)
        except Exception:  # noqa: BLE001
            prefix = ""
    detail = fetch_detail(bid, sess)
    detail["book_id"] = bid
    detail["novel_prefix"] = prefix
    return detail


def _catalog_prefix_candidates(detail_html: str, book_id: str) -> List[str]:
    """生成目录页候选前缀：本地索引分类号优先，其次详情页解析，最后兜底探测。

    「目录为空」根因：详情页被 Cloudflare 拦截时拿不到分类号，旧逻辑只能
    瞎猜 /novel/1/../8/，9 个前缀 ×3 次重试 ×15s 超时 = 用户端 100 秒后仍空。
    实测全站索引 4471 本书里有 4464 本的 cover 路径 ``/image/{n}/`` 携带真实
    分类号（与详情页 100% 一致），零网络成本、全书通用。把它作为首选，
    详情页被拦也能一击命中，彻底消除瞎猜带来的灾难性耗时。
    """
    candidates: List[str] = []
    # 首选：本地全站索引 cover 里的分类号（零网络，99.8% 覆盖）
    try:
        from . import wenku8_index as _idx
        meta = _idx.search_index_book(str(book_id).strip()) or {}
        cover = str(meta.get("cover") or "")
        m = re.search(r"/image/(\d+)/", cover)
        if m:
            candidates.append(f"/novel/{m.group(1)}/{book_id}/")
    except Exception:  # noqa: BLE001
        pass
    # 其次：详情页解析出的前缀
    parsed = _novel_prefix(detail_html, book_id)
    if parsed and parsed not in candidates:
        candidates.append(parsed)
    for m in re.finditer(rf"/novel/(\d+)/{book_id}/", detail_html or ""):
        prefix = f"/novel/{m.group(1)}/{book_id}/"
        if prefix not in candidates:
            candidates.append(prefix)
    # 最后：常见分类号兜底（索引缺失/冷门书）
    for n in ("1", "2", "3", "4", "5", "6", "7", "8", "0"):
        prefix = f"/novel/{n}/{book_id}/"
        if prefix not in candidates:
            candidates.append(prefix)
    return candidates[:10]


def _parse_catalog_html(html: str, catalog_url: str) -> List[Dict]:
    """从目录页 HTML 解析章节列表；解析不到返回空列表。"""
    soup = BeautifulSoup(html or "", "html.parser")
    catalog: List[Dict] = []
    seen = set()
    # 文库8把插图作为独立章节。以前一律过滤，导致下载的 EPUB 没有插图。
    # 现在保留插图章并打 is_illustration 标记：正文抓取阶段提取 <img>，渲染阶段
    # 内嵌到 EPUB，TXT 退化为「[插图:链接]」。标记同时防止空章被误判为失败。
    _image_re = re.compile(
        r"^(插图|插画|图片|彩页|封面)\s*[\d一二三四五六七八九十]*\s*[（(\-—·:：]?.*$"
    )
    _chap_re = re.compile(r"(?:^|/)(\d+)\.htm(?:\?|$)", re.I)

    def _chapter_id(href: str) -> str:
        m = _chap_re.search(str(href or ""))
        return m.group(1) if m else ""

    def _add(cid: str, title: str, href: str, vol: str) -> None:
        if not cid or cid in seen:
            return
        seen.add(cid)
        catalog.append({
            "id": cid,
            "title": title or f"第{len(catalog) + 1}章",
            "index": len(catalog),
            "url": urljoin(catalog_url, href),
            "vol": vol,
            "is_illustration": bool(_image_re.match(title or "")),
        })

    current_vol = ""
    for tr in soup.select("tr"):
        vol_td = tr.select_one("td.vcss")
        if vol_td is not None:
            current_vol = vol_td.get_text(" ", strip=True)
            continue
        for a in tr.select("a[href]"):
            href = a.get("href", "").strip()
            title = a.get_text(" ", strip=True)
            _add(_chapter_id(href), title, href, current_vol)
    # vcss 卷结构没识别出来时，退回扁平遍历，避免任何情况下丢章。
    if not catalog:
        for a in soup.select("a[href]"):
            href = a.get("href", "").strip()
            title = a.get_text(" ", strip=True)
            _add(_chapter_id(href), title, href, "")
    return catalog


def fetch_catalog(book_id: str, session=None) -> List[Dict]:
    """取目录：返回 [{id, title, index, url}]。

    id 为章节数字（如 177990），url 为绝对地址。目录页章节是相对链接
    （如 href="177990.htm"），base 为 /novel/{n}/{id}/。
    """
    bid = resolve_book_id(book_id)
    sess = session or make_session()

    # 网页目录优先：官方 App API（app.wenku8.com）已关停，返回 nginx Welcome，
    # 不再是可靠通道。详情页可能给不出分类号（被拦/改版），此时不能只猜
    # /novel/1/：《紫罗兰》真实目录在 /novel/2/2353/，猜 1 会 404 → 空目录。
    #
    # 挑刺 #120（用户「紫罗兰又下载不了了·目录为空」）：旧实现两处脆弱——
    # ①详情页 _get_with_retry 无 try/except，一次网络抖动抛异常就整个崩掉；
    # ②候选页只 attempts=2，用户端抖动/风控全失败即返回空 → 「目录为空」。
    # 改为「整体 3 轮重试」，每轮详情页带异常保护、候选页 attempts 提到 3，
    # 只要任意一轮拿到目录就返回。本地实测真机抖动场景可自愈。
    import time as _time
    # 索引已给出真实分类号时，目录候选第一个就命中；此时完全没必要先请求
    # 详情页（慢网络上 /book/{id}.htm 最多 3 次重试 + 退避，是开局最大的浪费）。
    # 索引缺失时才抓详情页补充候选，保留原有兜底强度。
    index_prefix = _index_prefix(bid)
    for whole_round in range(3):
        # 详情页：异常不能让整个目录抓取崩掉，失败就进下一轮
        detail_html = ""
        if not index_prefix:
            try:
                detail_resp = _get_with_retry(
                    sess, f"{_base()}/book/{bid}.htm", timeout=15, attempts=3,
                )
                detail_html = _decode(detail_resp) if detail_resp is not None else ""
            except Exception:  # noqa: BLE001
                pass
        for prefix in _catalog_prefix_candidates(detail_html, bid):
            catalog_url = f"{_base()}{prefix}index.htm"

            def _has_chapters(r, _bid=bid, _url=catalog_url):
                try:
                    return bool(_parse_catalog_html(_decode(r), _url))
                except Exception:  # noqa: BLE001
                    return False

            try:
                html = _decode(_get_with_retry(
                    sess, catalog_url, timeout=15, attempts=3,
                    validate=_has_chapters,
                ))
            except Exception:  # noqa: BLE001
                continue
            catalog = _parse_catalog_html(html, catalog_url)
            if catalog:
                return catalog
        # 本轮一无所获，短暂退避后整轮重试（换一次 TLS 连接，规避抖动）
        if whole_round < 2:
            _time.sleep(0.4 * (whole_round + 1))

    # 所有网页候选都失败时退回 App API（已关停，保留兼容兜底）。
    try:
        from .wenku8_app_api import fetch_catalog_app
        app_catalog = fetch_catalog_app(bid)
        if app_catalog:
            return app_catalog
    except Exception:  # noqa: BLE001
        pass
    return []


# ---- 卷首信息 / 转载信息清洗（全书通用） ----
# 文库8绝大多数书源自轻之国度 / 天使动漫，每卷第一话开头会带一段录入组
# 声明：分隔线 + 作者/插画/图源/扫图/录入/修图/校对 + 免责声明。它不属于
# 正文（lnovel.org、wk.kdays.net 等站把它单独列为「转载信息」章节）。
# 按固定字段标签 + 录入组标记识别，不绑定任何书名 / 人名，全书通用。
# 分隔符兼容冒号与全角斜杠（轻之国度用「：」，百度贴吧系用「／」）。
_CREDIT_LABEL_RE = re.compile(
    r"^\s*(作者|插图|插画|译者|翻译|图源|扫图|扫瞄|录入|修图|校对|台版|原作|责编|首发)"
    r"\s*[：:／/]")
_CREDIT_MARK_RE = re.compile(
    r"轻之国度|天使动漫|录入组|汉化组|翻译组|字幕组|仅供个人学习|禁作商业用途|"
    r"24\s*小时内删除|请(尊重|体谅|理解).{0,24}(扫图|录入|校对|翻译|图源)|"
    r"转载请.{0,4}保留信息|严禁转载|转载信息|卷首信息|"
    r"所录入的每一本书里|扫图者有很大一部分功劳|转自")
_CREDIT_RULE_RE = re.compile(r"^\s*[─—－\-_＝=]{6,}\s*$")


def _is_credit_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    return bool(_CREDIT_LABEL_RE.match(s) or _CREDIT_MARK_RE.search(s)
                or _CREDIT_RULE_RE.match(s))


def strip_credit_block(lines: List[str]) -> List[str]:
    """删除章首录入组「卷首信息」块，保留其后正文。

    只在章首前 12 行内识别；要求连续块内至少 2 个不同字段标签，且带
    录入组标记（或有 3 个以上字段），避免误删正文里偶然的单行「作者：xxx」。
    块内允许空行、长度上限 24 行；块外内容原样保留。
    """
    limit = min(12, len(lines))
    i = 0
    while i < limit:
        if not lines[i].strip():
            i += 1
            continue
        if not _is_credit_line(lines[i]):
            break  # 章首第一行就是正文，后面不用再找
        j = i
        labels: set = set()
        marks = 0
        while j < len(lines) and (j - i) < 24:
            s = lines[j].strip()
            if not s:
                k = j + 1
                while k < len(lines) and not lines[k].strip():
                    k += 1
                if k < len(lines) and _is_credit_line(lines[k]):
                    j = k
                    continue
                break
            if not _is_credit_line(s):
                break
            m = _CREDIT_LABEL_RE.match(s)
            if m:
                labels.add(m.group(1))
            if _CREDIT_MARK_RE.search(s):
                marks += 1
            j += 1
        # 判定必须同时有「录入组标记」和「字段标签」，绝不容忍纯字段块：
        # 宁可漏删一条信息，也不误删正文里偶然出现的「作者：/图源：」行。
        # 精简块（如「网译版 转自 巴哈姆特 / 翻译：只成一事」）只有 1 个字段，
        # 靠首行转自标记触发；多字段块则要求块内出现任意录入组标记。
        if (marks >= 1 and len(labels) >= 1
                and (_CREDIT_MARK_RE.search(lines[i]) or len(labels) >= 2)):
            return lines[:i] + lines[j:]
        i = j if j > i else i + 1
    return lines


def fetch_chapter_text(url_or_id, session=None, novel_prefix: str = "",
                       book_id: str = "", attempts: int = 4) -> str:
    """取单章正文纯文本。

    接受绝对 URL、章节 id（需给 novel_prefix）或相对文件名。
    正文容器为 #content；去掉站点声明行、章首录入组「卷首信息」块与空行，
    每行保留 4 空格缩进（与番茄/七猫 chapter_text 输出格式对齐，build_txt 直接复用）。

    :param attempts: 单章内重试上限。首轮默认 4；外层 rescue 轮次显式传 2，
        避免「内层 4 × 外层 3 轮 = 16 次请求」的重试风暴（用户端 1149.8s 主因）。
    """
    sess = session or make_session()
    raw = str(url_or_id or "").strip()
    if raw.startswith("http"):
        url = raw
    elif novel_prefix:
        if raw.endswith(".htm"):
            url = urljoin(f"{_base()}{novel_prefix}", raw)
        else:
            url = f"{_base()}{novel_prefix}{raw}.htm"
    else:
        bid = resolve_book_id(book_id)
        url = f"{_base()}/novel/0/{bid}/{raw}.htm"

    # 逐章请求是下载丢章的直接位置：一次网络抖动（httpcloak unexpected EOF /
    # 连接重置）就会让这章静默变空，最终「9/29 章却显示 100%」。带退避重试。
    # 单次请求 3 次即可：每线程会话复用后 EOF 已极少，更多补抓放在并发预取层，
    # 不在渲染阶段串行堆积等待（否则进度会卡在 40%）。
    def _has_content(r):
        try:
            return bool(BeautifulSoup(_decode(r), "html.parser").select_one("#content"))
        except Exception:  # noqa: BLE001
            return False

    resp = _get_with_retry(sess, url, timeout=20, attempts=attempts,
                           validate=_has_content)
    html = _decode(resp)
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one("#content")
    if node is None:
        node = soup.select_one("div.contentbox") or soup.select_one("div.content")
    if node is None:
        return ""

    # 去掉脚本/样式与站点版权声明行
    for bad in node.select("script,style"):
        bad.decompose()

    # 提取正文里的插图：文库8把插图做成独立章节或夹在正文中，正文容器里是
    # <img>。以前只取 get_text() 会丢掉全部插图。这里把图片 URL 用专用标记
    # 占位，保留在文中位置；下载插件预取阶段下载图片，渲染阶段把标记替换为
    # EPUB 内嵌 <img>（TXT 退化为「[插图:链接]」）。
    image_urls: List[str] = []
    for img in node.select("img"):
        src = (img.get("src") or img.get("data-src") or img.get("data-original")
               or "").strip()
        if not src:
            continue
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urljoin(_base() + "/", src)
        elif not src.startswith("http"):
            src = urljoin(url, src)
        # 文库8图片服务器常给 http，统一升级 https
        if src.startswith("http://"):
            src = "https://" + src[len("http://"):]
        if src not in image_urls:
            image_urls.append(src)

    text = node.get_text("\n", strip=True)
    lines: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # 过滤站点版权声明行：只删明显的来源推广句，避免误删正文里恰好提到
        # 「轻小说文库」的正常内容（原文此处 and/or 优先级也易踩坑，改用括号明确）。
        low = line.lower()
        # 挑刺 #30：补上 .cc（当前主线路）与裸域名，避免推广行混进正文。
        if ("本文来自" in line or "wenku8.com" in low or "wenku8.net" in low
                or "wenku8.cc" in low
                or ("轻小说文库" in line and ("http" in low or "www." in low))):
            continue
        lines.append("    " + line)

    # 删除章首录入组「卷首信息」块——它不是正文，全书通用。
    lines = strip_credit_block(lines)

    # 图片标记放在文字之后（插图章通常只有图，没有文字）。
    for src in image_urls:
        lines.append("[[WENKU8IMG:" + src + "]]")
    # 纯插图章没有文字也没关系：图片标记本身就是有效正文，不会被判为空章。
    return "\n".join(lines)


def fetch_chapter_text_app(aid: str, cid: str, session=None) -> str:
    """App API 正文通道（do=text），供下载插件按 app_cid 逐章取文。"""
    from .wenku8_app_api import fetch_text_app
    return fetch_text_app(aid, cid, session)
