# -*- coding: utf-8 -*-
"""Wenku8 搜索探测 - 单文件自包含，纯标准库，零依赖。

复现软件改好后的搜索数据源：带本机登录 Cookie，抓 .cc 首页/榜单/分类页，
提取书名后用与软件一致的归一化匹配算法，验证「恶魔高校」「紫罗兰」能否命中。

Usage:
    python wenku8_search_probe.py
或双击 "probe_search.bat"。
"""
from __future__ import annotations

import json
import os
import re
import ssl
import sys
import unicodedata
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

LINE = "=" * 60
BASE = "https://www.wenku8.cc"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
SETTINGS = os.path.join(os.path.expanduser("~"), ".qimao_gui", "settings.json")


def out(msg=""):
    print(msg, flush=True)


def load_cookie():
    try:
        with open(SETTINGS, "r", encoding="utf-8") as fh:
            return str(json.load(fh).get("wenku8_cookie", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def fetch(path, cookie):
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
               "Accept": "text/html,*/*"}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(BASE + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12, context=CTX) as r:
            raw = r.read()
        try:
            return raw.decode("gbk")
        except UnicodeDecodeError:
            return raw.decode("gbk", "replace")
    except Exception as exc:  # noqa: BLE001
        return "EXC:" + type(exc).__name__


_PUNCT = re.compile(r"[\s\u3000·・\-—_~～!！?？,，.。:：;；'\"“”‘’()（）\[\]【】]")


def normalize(s):
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return _PUNCT.sub("", s)


def matches(query, title):
    q, t = normalize(query), normalize(title)
    if not q:
        return True
    return q in t


def extract_books(html):
    books, seen = [], set()
    for href, name in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>([^<]{2,80})</a>',
                                 html, re.I):
        m = re.search(r"/book/(\d+)\.htm", href) or re.search(r"/novel/\d+/(\d+)/", href)
        if not m:
            continue
        bid, name = m.group(1), name.strip()
        if bid in seen or not name or name in ("我要阅读", "返回书页", "点击阅读"):
            continue
        seen.add(bid)
        books.append((bid, name))
    return books


# 与软件一致的热门别名直达索引：括号别名站内必空，按 aid 直接取详情页
ALIAS_AID = {
    "紫罗兰": "2353",
    "薇尔莉特": "2353",
    "violet evergarden": "2353",
    "恶魔高校": "1034",
    "high school dxd": "1034",
}


def alias_detail_title(aid, cookie):
    """抓详情页取 <title> 里的书名，验证别名直达是否真能拿到数据。"""
    html = fetch(f"/book/{aid}.htm", cookie)
    if html.startswith("EXC:"):
        return "", html
    m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    if not m:
        return "", "无 <title>"
    parts = [p.strip() for p in m.group(1).split(" - ")]
    return (parts[0] if parts else ""), f"{len(html)}字节"


def main():
    out(LINE)
    out("Wenku8 搜索探测（单文件 · 复现软件搜索数据源）")
    out(LINE)
    cookie = load_cookie()
    out(f"域名: {BASE}")
    out(f"登录 Cookie: {'已读取 (%d 字节)' % len(cookie) if cookie else '无（未登录，榜单可能空）'}")

    paths = [
        ("/", "首页"),
        ("/modules/article/toplist.php?sort=allvisit", "热度榜"),
        ("/modules/article/toplist.php?sort=lastupdate", "更新榜"),
        ("/modules/article/articlelist.php?class=1", "分类1"),
    ]

    all_books = []
    out()
    out("=== 数据源抓取 ===")
    for path, label in paths:
        html = fetch(path, cookie)
        if html.startswith("EXC:"):
            out(f"  [{label}] {html}")
            continue
        books = extract_books(html)
        out(f"  [{label}] {len(html)}字节, 抓到 {len(books)} 本书")
        all_books.extend(books)

    # 去重
    uniq = {}
    for bid, name in all_books:
        uniq.setdefault(bid, name)
    out(f"\n合计去重: {len(uniq)} 本")

    out()
    out("=== 搜索验证（数据源匹配 + 别名直达，软件同款）===")
    for kw in ("恶魔高校", "紫罗兰"):
        hits = [(bid, name) for bid, name in uniq.items() if matches(kw, name)]
        # 别名直达：命中已知别名时按 aid 抓详情页补结果（冷门书不在热门榜单里）
        alias_hit = None
        q = normalize(kw)
        for alias, aid in ALIAS_AID.items():
            na = normalize(alias)
            if len(q) >= 2 and len(na) >= 2 and (na in q or q in na):
                title, info = alias_detail_title(aid, cookie)
                alias_hit = (aid, title, info)
                break
        out(f"\n「{kw}」-> 数据源 {len(hits)} 条" + (" + 别名直达 1 条" if alias_hit else ""))
        for bid, name in hits[:6]:
            out(f"   [数据源] ID {bid} | {name}")
        if alias_hit:
            aid, title, info = alias_hit
            ok = bool(title) and matches(kw, title)
            out(f"   [别名直达] ID {aid} | {title or '（取标题失败）'} | {info}"
                f" -> {'命中' if ok else '标题未命中'}")

    out()
    out(LINE)
    out("判读：")
    out("  · 「恶魔高校」出结果 -> 站内/榜单数据源可用")
    out("  · 「紫罗兰」别名直达拿到《薇尔莉特·伊芙加登(紫罗兰永恒花园)》 -> 别名修复生效")
    out("  · 两栏都有结果 -> 搜索已修好；若别名直达取标题失败，把输出发我。")
    out("  把本段完整输出发我即可。")


if __name__ == "__main__":
    main()
