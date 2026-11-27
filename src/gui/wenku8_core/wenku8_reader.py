# -*- coding: utf-8 -*-
"""文库8 详情/目录/正文读取与链接归一化。

站点全站 GBK 编码；正文在 #content，明文无字体混淆（实测 2026-11）。
所有网络请求经 wenku8_auth.make_session() 注入登录态。
"""

from __future__ import annotations

import re
from typing import Dict, List
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
    return f"/novel/0/{book_id}/"  # 兜底，通常不会用到


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
            resp = sess.get(url, timeout=10)
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

    # 书名/作者：网页 <title> 格式「书名 - 作者 - 分类 - 轻小说文库」
    if soup.title:
        parts = [p.strip() for p in soup.title.get_text().split(" - ")]
        if len(parts) >= 4:
            title, author = parts[0], parts[1]
        elif len(parts) >= 2:
            title = parts[0]

    # 详情页表格里的「小说作者：xxx」/「作者：xxx」
    if not author:
        m = re.search(r"(?:小说)?作者[：:]\s*([^<\s]+)", html)
        if m:
            author = m.group(1).strip()

    # 封面：img.wenku8.com/image/{n}/{id}/{id}s.jpg
    for img in soup.select("img[src]"):
        src = img.get("src", "")
        if "wenku8" in src and ("cover" in src.lower() or bid in src):
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
    # App API meta：补完整书名（含括号别名）、作者、字数、简介；网页失败也仍可用
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
            words_num = merged.get("words_num", "")
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


def fetch_catalog(book_id: str, session=None) -> List[Dict]:
    """取目录：返回 [{id, title, index, url}]。

    id 为章节数字（如 177990），url 为绝对地址。目录页章节是相对链接
    （如 href="177990.htm"），base 为 /novel/{n}/{id}/。
    """
    bid = resolve_book_id(book_id)
    sess = session or make_session()

    # 网页目录优先：官方 App API（app.wenku8.com）已关停，返回 nginx Welcome，
    # 不再是可靠通道。实测 .cc 网页目录（/novel/1/{id}/index.htm）可用且含完整
    # 卷/章结构，故网页优先、App API 仅作兜底。
    d = sess.get(f"{_base()}/book/{bid}.htm", timeout=20)
    prefix = _novel_prefix(_decode(d), bid)
    catalog_url = f"{_base()}{prefix}index.htm"

    try:
        resp = sess.get(catalog_url, timeout=20)
        html = _decode(resp)
    except Exception:  # noqa: BLE001
        html = ""
    soup = BeautifulSoup(html, "html.parser")

    catalog: List[Dict] = []
    seen = set()
    # 文库8把插图作为独立章节（正文容器为空，只有图片），下载后是空章。
    # 这类章节标题为纯图片标记，过滤掉以免产出空章节。
    _image_only = {"插图", "插画", "图片", "图", "封面", "彩页"}
    # 文库8目录结构：<td class="vcss" vid="...">第一卷</td> 是卷标题行，
    # 后续 <td class="ccss"><a href="数字.htm">章名</a></td> 是该卷下的章节。
    # 按文档顺序遍历，遇到 vcss 更新当前卷，遇到章节链接继承它 → 保留层级。
    current_vol = ""
    for tr in soup.select("tr"):
        vol_td = tr.select_one("td.vcss")
        if vol_td is not None:
            current_vol = vol_td.get_text(" ", strip=True)
            continue
        for a in tr.select("a[href]"):
            href = a.get("href", "").strip()
            title = a.get_text(" ", strip=True)
            if not re.fullmatch(r"\d+\.htm", href):
                continue
            if title in _image_only:
                continue
            cid = href[:-4]
            if not cid.isdigit() or cid in seen:
                continue
            seen.add(cid)
            catalog.append({
                "id": cid,
                "title": title or f"第{len(catalog) + 1}章",
                "index": len(catalog),
                "url": urljoin(catalog_url, href),
                "vol": current_vol,
            })
    # 兜底：若页面结构异常（vcss 未识别）导致全部卷为空，退回扁平遍历，
    # 保证任何情况下章节都不丢失（零回归）。
    if not catalog:
        for a in soup.select("a[href]"):
            href = a.get("href", "").strip()
            title = a.get_text(" ", strip=True)
            if not re.fullmatch(r"\d+\.htm", href) or title in _image_only:
                continue
            cid = href[:-4]
            if not cid.isdigit() or cid in seen:
                continue
            seen.add(cid)
            catalog.append({
                "id": cid, "title": title or f"第{len(catalog) + 1}章",
                "index": len(catalog), "url": urljoin(catalog_url, href),
            })
    # 网页目录整体失败时（如全部被拦），退回 App API 兜底。
    if not catalog:
        try:
            from .wenku8_app_api import fetch_catalog_app
            app_catalog = fetch_catalog_app(bid)
            if app_catalog:
                return app_catalog
        except Exception:  # noqa: BLE001
            pass
    return catalog


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

    resp = sess.get(url, timeout=20)
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
    text = node.get_text("\n", strip=True)
    lines: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # 过滤站点版权声明行：只删明显的来源推广句，避免误删正文里恰好提到
        # 「轻小说文库」的正常内容（原文此处 and/or 优先级也易踩坑，改用括号明确）。
        low = line.lower()
        if ("本文来自" in line or "wenku8.com" in low or "wenku8.net" in low
                or ("轻小说文库" in line and ("http" in low or "www." in low))):
            continue
        lines.append("    " + line)
    return "\n".join(lines)


def fetch_chapter_text_app(aid: str, cid: str, session=None) -> str:
    """App API 正文通道（do=text），供下载插件按 app_cid 逐章取文。"""
    from .wenku8_app_api import fetch_text_app
    return fetch_text_app(aid, cid, session)
