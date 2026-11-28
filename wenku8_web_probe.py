# -*- coding: utf-8 -*-
"""Wenku8 WEB-channel probe - self contained, standard library only.

Tests the WEB channel (www.wenku8.net / .cc / .com) which is a DIFFERENT
server from the dead App API. Run this locally to find out whether the
website itself is reachable from your machine.

Usage:
    python wenku8_web_probe.py
or double-click "probe_web.bat".
"""
from __future__ import annotations

import os
import re
import ssl
import sys
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

DOMAINS = ["https://www.wenku8.net", "https://www.wenku8.cc",
           "https://www.wenku8.com"]
LINE = "=" * 62


def out(msg=""):
    print(msg, flush=True)


def get(url, cookie=""):
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
               "Accept": "text/html,application/xhtml+xml,*/*"}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            return r.read(), r.headers
    except Exception as exc:  # noqa: BLE001
        return b"", None


def load_cookie():
    try:
        p = os.path.join(os.path.expanduser("~"), ".qimao_gui", "settings.json")
        import json
        with open(p, "r", encoding="utf-8") as fh:
            return str(json.load(fh).get("wenku8_cookie", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def decode(raw):
    for enc in ("gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gbk", "replace")


def kind(raw):
    low = raw.lower()
    if not raw:
        return "空响应"
    if b"attention required" in low or b"cf-error" in low:
        return "Cloudflare 403 拦截"
    if b"<h1>welcome</h1>" in low:
        return "nginx Welcome"
    if b"<html" in low or b"<title" in low:
        return "正常HTML"
    return "其它"


def main():
    cookie = load_cookie()
    out(LINE)
    out("Wenku8 网页通道探测（与已失效的 App API 是不同的服务器）")
    out(LINE)
    out(f"本机登录态：{'已读取 Cookie(%d)' % len(cookie) if cookie else '无 Cookie'}")
    out()

    kw = urllib.parse.quote("紫罗兰".encode("gbk"))
    paths = [
        ("首页", lambda b: b + "/"),
        ("搜索-紫罗兰", lambda b: b + "/modules/article/search.php?searchtype=articlename&searchkey=" + kw),
        ("详情-1034", lambda b: b + "/book/1034.htm"),
        ("详情articleinfo", lambda b: b + "/modules/article/articleinfo.php?id=1034"),
        ("目录-1034", lambda b: b + "/book/1034.htm"),
    ]

    for base in DOMAINS:
        out(f"--- {base} ---")
        for label, make in paths:
            raw, hdr = get(make(base), cookie)
            k = kind(raw)
            tag = "OK" if k == "正常HTML" else "--"
            out(f"  [{tag}] {label:16} {len(raw):6}B  {k}")
            if k == "正常HTML" and label in ("搜索-紫罗兰", "详情-1034"):
                txt = decode(raw)
                m = re.search(r"<title>(.*?)</title>", txt, re.I | re.S)
                if m:
                    out(f"          title: {m.group(1).strip()[:60]}")
        out()

    out(LINE)
    out("判读：")
    out("  · 若某域名显示 [OK] 正常HTML 且 title 是书名/搜索结果 -> 网页端可用，")
    out("    我们可以把搜索/详情/下载全部改成走网页端，绕开已死的 App API。")
    out("  · 若全是 Cloudflare 403 -> 网页端也拦，需要你本机先过一次浏览器验证。")
    out("  · 把本段完整输出发给开发者即可。")


if __name__ == "__main__":
    main()
