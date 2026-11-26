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

from .wenku8_auth import make_session

BASE = "https://www.wenku8.cc"

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
    """取书籍详情：title / author / intro / cover。"""
    bid = resolve_book_id(book_id)
    sess = session or make_session()
    resp = sess.get(f"{BASE}/book/{bid}.htm", timeout=20)
    html = _decode(resp)
    soup = BeautifulSoup(html, "html.parser")

    title, author, intro, cover = "", "", "", ""

    # 书名/作者：优先 <title>（格式「书名 - 作者 - 分类 - 轻小说文库」）
    if soup.title:
        parts = [p.strip() for p in soup.title.get_text().split(" - ")]
        if len(parts) >= 4:
            title, author = parts[0], parts[1]
        elif len(parts) >= 2:
            title = parts[0]

    # 详情页表格里的「小说作者：xxx」
    if not author:
        m = re.search(r"小说作者[：:]\s*([^<\s]+)", html)
        if m:
            author = m.group(1).strip()

    # 封面：img.wenku8.com/image/{n}/{id}/{id}s.jpg
    for img in soup.select("img[src]"):
        src = img.get("src", "")
        if "wenku8" in src and ("cover" in src.lower() or bid in src):
            cover = urljoin(BASE, src)
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

    return {
        "title": title,
        "author": author,
        "intro": intro,
        "cover": cover,
    }


def fetch_book_meta(book_id: str, session=None) -> Dict[str, str]:
    """取下载所需元信息：含 novel 路径前缀，供目录/正文拼 URL。"""
    bid = resolve_book_id(book_id)
    sess = session or make_session()
    resp = sess.get(f"{BASE}/book/{bid}.htm", timeout=20)
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

    # 先取详情页以确定 novel 前缀（分类号 n）
    d = sess.get(f"{BASE}/book/{bid}.htm", timeout=20)
    prefix = _novel_prefix(_decode(d), bid)
    catalog_url = f"{BASE}{prefix}index.htm"

    resp = sess.get(catalog_url, timeout=20)
    html = _decode(resp)
    soup = BeautifulSoup(html, "html.parser")

    catalog: List[Dict] = []
    seen = set()
    # 文库8把插图作为独立章节（正文容器为空，只有图片），下载后是空章。
    # 这类章节标题为纯图片标记，过滤掉以免产出空章节。
    _image_only = {"插图", "插画", "图片", "图", "封面", "彩页"}
    for a in soup.select("a[href]"):
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
        })
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
            url = urljoin(f"{BASE}{novel_prefix}", raw)
        else:
            url = f"{BASE}{novel_prefix}{raw}.htm"
    else:
        bid = resolve_book_id(book_id)
        url = f"{BASE}/novel/0/{bid}/{raw}.htm"

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
        if "本文来自" in line or "wenku8" in line.lower() and "轻小说文库" in line:
            continue
        lines.append("    " + line)
    return "\n".join(lines)
