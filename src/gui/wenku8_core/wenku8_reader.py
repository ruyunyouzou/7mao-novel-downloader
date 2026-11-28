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


def _get_with_retry(sess, url: str, timeout: int = 20, attempts: int = 3) -> Any:
    """带退避重试的 GET（下载丢章根治）。

    httpcloak 在网络抖动时会抛 ``unexpected EOF`` / 连接重置，普通 requests
    也会有偶发超时。正文抓取是逐章单请求，任何一次抖动都会让该章静默变空、
    最终产出「9/29 章却显示 100%」的残缺文件。这里对每次请求做有限重试 +
    短退避，让瞬态故障自愈；重试仍失败才返回最后一次结果/抛异常交给上层。

    :return: 成功返回响应对象；全部失败时返回最后一次的响应（供调用方检查），
        若连一次都没成功（每次都抛异常）则抛出最后一次异常。
    """
    last_exc: Exception | None = None
    last_resp: Any = None
    for i in range(max(1, int(attempts))):
        try:
            resp = sess.get(url, timeout=timeout)
            last_resp = resp
            # 有响应体但明显是网关错误页（502/503/504/429）时也重试
            status = int(getattr(resp, "status_code", 0) or 0)
            if status in (429, 502, 503, 504):
                if i < attempts - 1:
                    _time.sleep(0.4 * (i + 1))
                    continue
            return resp
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if i < attempts - 1:
                _time.sleep(0.4 * (i + 1))
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
    html = ""
    # hikari_novel_flutter 等参考项目用 articleinfo.php 取详情并解析标签；
    # 旧 /book/{id}.htm 保留兜底。任一页面可用即可，全部失败仍能走 App meta。
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
    # App API meta：补完整书名（含括号别名）、作者、字数、简介。
    # 挑刺 #29：App API 已关停，每次详情无条件调用会白等两次失败请求。
    # 改为「按需补缺」——网页已拿到完整字段时不再请求；只有关键字段缺失
    # （网页被风控拦/结构变化）时才兜底调用，既保留能力又不拖慢常规详情。
    need_meta = (not title or not author or not words_num or not intro)
    if need_meta:
        try:
            from .wenku8_app_api import fetch_meta, meta_to_book
            meta = fetch_meta(bid)
            if meta:
                merged = meta_to_book(meta, {
                    "title": title, "author": author, "intro": intro, "cover": cover,
                })
                title = merged.get("title") or title
                author = author or merged.get("author", "")
                intro = intro or merged.get("intro", "")
                cover = cover or merged.get("cover", "")
                words_num = words_num or merged.get("words_num", "")
                tags = tags or merged.get("tags", "")
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
    """取下载所需元信息：含 novel 路径前缀，供目录/正文拼 URL。"""
    bid = resolve_book_id(book_id)
    sess = session or make_session()
    resp = sess.get(f"{_base()}/book/{bid}.htm", timeout=20)
    html = _decode(resp)
    prefix = _novel_prefix(html, bid)
    detail = fetch_detail(bid, sess)
    detail["book_id"] = bid
    detail["novel_prefix"] = prefix
    return detail


def _catalog_prefix_candidates(detail_html: str, book_id: str) -> List[str]:
    """生成目录页候选前缀：先信详情页，再按常见分类号逐个探测。

    详情页被拦时 `_novel_prefix` 只能猜 /novel/1/，但《紫罗兰》等书真实
    分类号是 2；只猜一次会 404 → 「目录为空」。这里保留解析结果，再从
    详情页里出现过的所有 /novel/n/{bid}/ 提取候选，最后补常见 1..8/0。
    """
    candidates: List[str] = []
    parsed = _novel_prefix(detail_html, book_id)
    if parsed:
        candidates.append(parsed)
    for m in re.finditer(rf"/novel/(\d+)/{book_id}/", detail_html or ""):
        prefix = f"/novel/{m.group(1)}/{book_id}/"
        if prefix not in candidates:
            candidates.append(prefix)
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
    detail_html = _decode(_get_with_retry(
        sess, f"{_base()}/book/{bid}.htm", timeout=20,
    ))
    for prefix in _catalog_prefix_candidates(detail_html, bid):
        catalog_url = f"{_base()}{prefix}index.htm"
        try:
            html = _decode(_get_with_retry(
                sess, catalog_url, timeout=20, attempts=2,
            ))
        except Exception:  # noqa: BLE001
            continue
        catalog = _parse_catalog_html(html, catalog_url)
        if catalog:
            return catalog

    # 所有网页候选都失败时退回 App API（已关停，保留兼容兜底）。
    try:
        from .wenku8_app_api import fetch_catalog_app
        app_catalog = fetch_catalog_app(bid)
        if app_catalog:
            return app_catalog
    except Exception:  # noqa: BLE001
        pass
    return []


def fetch_chapter_text(url_or_id, session=None, novel_prefix: str = "",
                       book_id: str = "") -> str:
    """取单章正文纯文本。

    接受绝对 URL、章节 id（需给 novel_prefix）或相对文件名。
    正文容器为 #content；去掉站点声明行与空行，每行保留 4 空格缩进
    （与番茄/七猫 chapter_text 输出格式对齐，build_txt 直接复用）。
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
    resp = _get_with_retry(sess, url, timeout=20, attempts=3)
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

    # 图片标记放在文字之后（插图章通常只有图，没有文字）。
    for src in image_urls:
        lines.append("[[WENKU8IMG:" + src + "]]")
    # 纯插图章没有文字也没关系：图片标记本身就是有效正文，不会被判为空章。
    return "\n".join(lines)


def fetch_chapter_text_app(aid: str, cid: str, session=None) -> str:
    """App API 正文通道（do=text），供下载插件按 app_cid 逐章取文。"""
    from .wenku8_app_api import fetch_text_app
    return fetch_text_app(aid, cid, session)
