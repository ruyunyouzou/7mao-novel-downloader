# -*- coding: utf-8 -*-
"""
文库8 问题诊断（单文件自包含版）—— 零依赖，只用 Python 标准库。

它内联了软件「真正在用」的 App API 协议与解析逻辑（与 src/gui/wenku8_core/
wenku8_app_api.py 完全一致），所以这里的结果 = 软件里的真实表现。
不依赖任何项目目录、不需要 pip 装任何库，把这个 .py 单独放到任何文件夹都能跑。

运行：
    py -3 wenku8_issue_check.py
或双击同目录「运行问题诊断.bat」。
可选：手动指定书号补测详情/封面/下载，如 py -3 wenku8_issue_check.py 1034

检测项：
    [1] 搜索「紫罗兰」    -> 是否命中《薇尔莉特·伊芙加登(紫罗兰永恒花园)》
    [2] 搜索「恶魔高校」  -> 是否有带封面的结果
    [3] 详情              -> 完整括号别名 / 作者 / 标签 / 简介
    [4] 封面下载          -> 图片字节是否拿得到
    [5] 下载链路          -> 目录章节数 + 正文长度（定位「目录为空」「卡 2%」）

只读本机已保存的登录 Cookie，不上传、不改文件、不保存任何账号密码。
把整段输出复制给开发者即可。
"""

from __future__ import annotations

import base64
import concurrent.futures
import html
import json
import os
import re
import ssl
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

# ---------------- App API 协议常量（与软件一致）----------------
APP_API_URL = "http://app.wenku8.com/android.php"
APP_VER = "1.13"


def api_url() -> str:
    """软件端若配置了中继则走中继，否则走官方端点（与软件逻辑一致）。"""
    try:
        p = os.path.join(os.path.expanduser("~"), ".qimao_gui", "settings.json")
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        relay = str(data.get("wenku8_api_relay", "") or "").strip().rstrip("/")
        if relay.startswith(("http://", "https://")):
            return relay
    except Exception:  # noqa: BLE001
        pass
    return APP_API_URL
ANDROID_UA = "Dalvik/2.1.0 (Linux; U; Android 11; IN2010 Build/RP1A.201005.001)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0")
NOVELLIST_SORTS = ("allvisit", "allvote", "monthvisit", "monthvote",
                   "postdate", "lastupdate", "goodnum", "size")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

LINE = "=" * 72


def out(msg: str = "") -> None:
    print(msg, flush=True)


def verdict(ok: bool, label: str, detail: str = "") -> None:
    out(f"  {'[通过]' if ok else '[失败]'} {label}" + (f"  —— {detail}" if detail else ""))


# ---------------- 归一化匹配（与 wenku8_match.py 一致）----------------
_SPACING = re.compile(
    r"[\s·・_\-—–，,。.:：()（）\[\]【】「」『』《》〈〉、;；!！?？'\"“”‘’/\\|]+")
_FILLER = str.maketrans("", "", "的之")


def to_halfwidth(value: str) -> str:
    buf = []
    for ch in str(value or ""):
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            buf.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            buf.append(" ")
        else:
            buf.append(ch)
    return "".join(buf)


def normalize(value: str) -> str:
    text = to_halfwidth(value)
    text = _SPACING.sub("", text).lower()
    text = text.translate(_FILLER)
    text = unicodedata.normalize("NFKC", text)
    return _SPACING.sub("", text).lower()


def is_subsequence(needle: str, haystack: str) -> bool:
    it = iter(haystack)
    return all(ch in it for ch in needle)


def matches(query: str, title: str) -> bool:
    q = normalize(query)
    t = normalize(title)
    if not q:
        return True
    return q in t or (len(q) >= 2 and is_subsequence(q, t))


# ---------------- 网络基础 ----------------
def http_post(url: str, form: dict, ua: str, timeout: int = 8) -> bytes:
    data = urllib.parse.urlencode(form).encode("ascii")
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": ua, "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.read()
    except Exception:  # noqa: BLE001
        return b""


def http_get(url: str, ua: str, timeout: int = 10, cookie: str = "") -> bytes:
    headers = {"User-Agent": ua, "Accept-Language": "zh-CN,zh;q=0.9"}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.read()
    except Exception:  # noqa: BLE001
        return b""


def app_post(param: str, timeout: int = 8) -> str:
    form = {
        "appver": APP_VER,
        "request": base64.b64encode(param.encode("utf-8")).decode("ascii"),
        "timetoken": str(int(time.time() * 1000)),
    }
    raw = http_post(api_url(), form, ANDROID_UA, timeout)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gbk", "replace")


def is_blocked(text: str) -> bool:
    low = (text or "").lower()
    return ("<h1>welcome</h1>" in low or "attention required" in low
            or "just a moment" in low or "cf-error" in low or "cf-ray" in low)


def clean_xml_text(value: str) -> str:
    v = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", value or "", flags=re.I | re.S)
    v = re.sub(r"<[^>]+>", "", v)
    return html.unescape(v).strip()


def build_search_param(kw: str, search_type: str, urlencode: bool) -> str:
    raw = str(kw or "").strip()
    key = urllib.parse.quote(raw, safe="", encoding="utf-8") if urlencode else raw
    return f"action=search&searchtype={search_type}&searchkey={key}&t=0"


def parse_search_xml(xml_text: str) -> list:
    result, seen = [], set()
    for m in re.finditer(r"<item\b([^>]*?)(?:/>|>(.*?)</item>)", xml_text or "", re.I | re.S):
        am = re.search(r"\baid\s*=\s*[\"'](\d+)[\"']", m.group(1) or "", re.I)
        if not am:
            continue
        aid = am.group(1)
        if aid in seen:
            continue
        seen.add(aid)
        title = re.sub(r"<[^>]+>", "", m.group(2) or "").strip()
        result.append({"id": aid, "title": title})
    return result


def parse_novellist_xml(xml_text: str) -> list:
    result, seen = [], set()
    for m in re.finditer(r"<item\b([^>]*?)(?:/>|>(.*?)</item>)", xml_text or "", re.I | re.S):
        am = re.search(r"\baid\s*=\s*[\"'](\d+)[\"']", m.group(1) or "", re.I)
        if not am:
            continue
        aid = am.group(1)
        if aid in seen:
            continue
        inner = m.group(2) or ""
        tm = re.search(r"<data\b[^>]*\bname\s*=\s*[\"']title[\"'][^>]*>(.*?)</data>",
                       inner, re.I | re.S)
        if not tm:
            continue
        title = clean_xml_text(tm.group(1))
        if not title:
            continue
        seen.add(aid)
        result.append({"id": aid, "title": title})
    return result


def parse_meta_xml(xml_text: str) -> dict:
    meta = {}
    for m in re.finditer(r"<data\b([^>]*?)(?:/>|>(.*?)</data>)", xml_text or "", re.I | re.S):
        nm = re.search(r"\bname\s*=\s*[\"']([^\"']+)[\"']", m.group(1) or "", re.I)
        if not nm:
            continue
        key = nm.group(1).strip().lower()
        vm = re.search(r"\bvalue\s*=\s*[\"']([^\"']*)[\"']", m.group(1) or "", re.I)
        value = vm.group(1).strip() if vm else ""
        if not value and m.group(2):
            value = clean_xml_text(m.group(2))
        if value and key not in meta:
            meta[key] = value
    return meta


def pick(meta: dict, *keys: str) -> str:
    for k in keys:
        v = str(meta.get(k, "") or "").strip()
        if v:
            return v
    return ""


def fetch_meta(aid: str) -> dict:
    for param in (f"action=book&do=meta&aid={aid}&t=0",
                  f"action=book&do=info&aid={aid}&t=0"):
        text = app_post(param, 5)
        if is_blocked(text):
            continue
        meta = parse_meta_xml(text)
        if meta:
            return meta
    return {}


def fetch_cover(aid: str) -> bytes:
    url = f"https://img.wenku8.com/image/{int(aid) // 1000}/{aid}/{aid}s.jpg"
    raw = http_get(url, BROWSER_UA, 10)
    if raw[:2] == b"\xff\xd8" or raw[:4] == b"\x89PNG" or raw[:3] == b"GIF":
        return raw
    form = {"appver": APP_VER,
            "request": base64.b64encode(f"action=book&do=cover&aid={aid}".encode()).decode(),
            "timetoken": str(int(time.time() * 1000))}
    raw = http_post(api_url(), form, ANDROID_UA, 8)
    return raw if (raw[:2] == b"\xff\xd8" or raw[:4] == b"\x89PNG") else b""


def parse_catalog(xml_text: str) -> list:
    chapters = []
    for cm in re.finditer(r"<chapter\b([^>]*)>(.*?)</chapter>", xml_text or "", re.I | re.S):
        am = re.search(r"\bcid\s*=\s*[\"'](\d+)[\"']", cm.group(1) or "", re.I)
        if not am:
            continue
        cid = am.group(1)
        title = clean_xml_text(cm.group(2))
        if not title or title in {"插图", "插画", "图片", "图", "封面", "彩页"}:
            continue
        chapters.append({"id": cid, "title": title})
    return chapters


def fetch_catalog(aid: str) -> list:
    text = app_post(f"action=book&do=list&aid={aid}&t=0", 8)
    if is_blocked(text):
        return []
    return parse_catalog(text)


def fetch_text(aid: str, cid: str) -> str:
    text = app_post(f"action=book&do=text&aid={aid}&cid={cid}&t=0", 10)
    if is_blocked(text):
        return ""
    text = re.sub(r"<\?xml[^>]*\?>", "", text)
    text = html.unescape(text)
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"<!--image-->.*?<!--image-->", "", raw).strip()
        if not line:
            continue
        low = line.lower()
        if ("本文来自" in line or "wenku8.com" in low or "wenku8.net" in low
                or ("轻小说文库" in line and ("http" in low or "www." in low))):
            continue
        lines.append(line)
    return "\n".join(lines)


def load_cookie() -> str:
    path = os.path.join(os.path.expanduser("~"), ".qimao_gui", "settings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cookie = str(data.get("wenku8_cookie", "") or "").strip()
        raw = str(data.get("wenku8_lines", "") or "").strip()
        if raw:
            lines = json.loads(raw)
            if isinstance(lines, dict):
                for ln in lines.get("lines", []):
                    if isinstance(ln, dict) and ln.get("cookie"):
                        cookie = str(ln["cookie"]).strip() or cookie
        return cookie
    except Exception:  # noqa: BLE001
        return ""


COOKIE = load_cookie()


# ---------------- 五项诊断 ----------------
def search_app(kw: str) -> list:
    combos = [("articlename", False), ("author", False),
              ("articlename", True), ("author", True)]
    raw = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(app_post, build_search_param(kw, st, ue), 8)
                for st, ue in combos]
        for fut in concurrent.futures.as_completed(futs, timeout=20):
            try:
                text = fut.result()
                if not is_blocked(text):
                    raw.extend(parse_search_xml(text))
            except Exception:  # noqa: BLE001
                pass
    merged = {}
    for b in raw:
        old = merged.get(b["id"])
        if old is None or (not old.get("title") and b.get("title")):
            merged[b["id"]] = b
    return list(merged.values())


def search_novellist(kw: str) -> list:
    tasks = [(s, p) for s in NOVELLIST_SORTS for p in range(1, 4)]
    raw = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(app_post, f"action=novellist&sort={s}&page={p}&t=0", 8): (s, p)
                for s, p in tasks}
        for fut in concurrent.futures.as_completed(futs, timeout=15):
            try:
                text = fut.result()
                if not is_blocked(text):
                    raw.extend(parse_novellist_xml(text))
            except Exception:  # noqa: BLE001
                pass
    hits, seen = [], set()
    for b in raw:
        if b["id"] in seen:
            continue
        seen.add(b["id"])
        if matches(kw, b["title"]):
            hits.append(b)
    return hits


def enrich(books: list) -> list:
    for b in books[:8]:
        meta = fetch_meta(b["id"])
        if meta:
            b["title"] = pick(meta, "title", "name", "articlename") or b.get("title", "")
            b["author"] = pick(meta, "author", "authorname")
    return books


def sec0_raw_probe() -> None:
    out("[0] App API 原始返回诊断（确认是网络拦截还是代码解析问题）")
    probes = [
        ("搜索articlename", "action=search&searchtype=articlename&searchkey=恶魔高校&t=0"),
        ("详情meta      ", "action=book&do=meta&aid=1034&t=0"),
        ("目录list      ", "action=book&do=list&aid=1034&t=0"),
    ]
    for label, param in probes:
        form = {"appver": APP_VER,
                "request": base64.b64encode(param.encode("utf-8")).decode("ascii"),
                "timetoken": str(int(time.time() * 1000))}
        raw = http_post(api_url(), form, ANDROID_UA, 8)
        low = raw.lower()
        if b"<h1>welcome</h1>" in low or b"attention required" in low:
            note = "拦截页(Welcome/Cloudflare)"
        elif b"<item" in low or b"<data" in low or b"<package" in low:
            note = "正常XML（有数据）"
        elif not raw:
            note = "空响应（连接被断开/超时）"
        else:
            note = "其它内容"
        out(f"  {label}: {len(raw)} 字节 | {note}")
        out(f"      前 90 字节: {raw[:90]!r}")
    out("  说明：若全是「拦截页」或「空响应」，则是本机网络到 app.wenku8.com 不通，")
    out("        不是软件代码问题（协议已与 zsakvo/wenku8x 权威源码逐字核对一致）。")
    out()


def sec1_violet() -> str:
    out("[1] 搜索「紫罗兰」  -> 期望命中《薇尔莉特·伊芙加登(紫罗兰永恒花园)》")
    t0 = time.time()
    hits = search_app("紫罗兰")
    out(f"  App 搜索：耗时 {time.time()-t0:.1f}s，返回 {len(hits)} 条")
    if not hits:
        t1 = time.time()
        hits = search_novellist("紫罗兰")
        out(f"  榜单别名兜底：耗时 {time.time()-t1:.1f}s，命中 {len(hits)} 条")
        if hits:
            hits = enrich(hits)
    for h in hits[:8]:
        out(f"    - ID {h['id']} | {h.get('title','')} | 作者 {h.get('author','-')}")
    hit = any("薇尔莉特" in h.get("title", "") or "紫罗兰" in h.get("title", "") for h in hits)
    verdict(hit, "紫罗兰命中", "命中目标书" if hit else "未命中")
    return str(hits[0]["id"]) if hits else ""


def sec2_demon() -> list:
    out()
    out("[2] 搜索「恶魔高校」  -> 期望多条结果且带封面链接")
    t0 = time.time()
    hits = search_app("恶魔高校")
    out(f"  耗时 {time.time()-t0:.1f}s，返回 {len(hits)} 条：")
    for h in hits[:8]:
        out(f"    - ID {h['id']} | {h.get('title','')}")
    meta_hits = []
    for h in hits[:8]:
        meta = fetch_meta(h["id"])
        title = pick(meta, "title", "name", "articlename") or h.get("title", "")
        cover_url = f"https://img.wenku8.com/image/{int(h['id'])//1000}/{h['id']}/{h['id']}s.jpg"
        meta_hits.append({"id": h["id"], "title": title, "cover": cover_url})
        out(f"       └ 书名(含别名)：{title}")
        out(f"         封面链接：{cover_url}")
    verdict(len(meta_hits) >= 1, "恶魔高校搜索", f"共 {len(meta_hits)} 条（封面链接见上，第 4 项验证能否下载）")
    return meta_hits


def sec3_detail(aid: str) -> None:
    out()
    out(f"[3] 详情  -> 期望完整括号别名 / 作者 / 标签 / 简介（书号 {aid or '自动'}）")
    if not aid.isdigit():
        out("  [跳过] 无可用书号。")
        return
    raw = http_get("https://www.wenku8.net/modules/article/articleinfo.php?id=" + aid,
                   BROWSER_UA, 10, COOKIE)
    html_text = raw.decode("gbk", "replace") if raw else ""
    web_blocked = is_blocked(html_text)
    out(f"  网页详情：{'被风控拦截' if web_blocked else ('HTTP 正常' if html_text else '连接失败')}")
    meta = fetch_meta(aid)
    title = pick(meta, "title", "name", "articlename")
    author = pick(meta, "author", "authorname")
    intro = pick(meta, "intropreview", "intro", "description", "summary")
    words = pick(meta, "booklength", "words", "length", "wordcount")
    tags = pick(meta, "tags", "keyword", "keywords", "category")
    out(f"  书名   : {title or '【空】'}")
    out(f"  作者   : {author or '【空】'}")
    out(f"  标签   : {tags or '【空（meta 常不返回标签，来自网页解析）】'}")
    out(f"  字数   : {words or '【空】'}")
    out(f"  简介   : {(intro[:80]+'…') if len(intro)>80 else (intro or '【空】')}")
    verdict(bool(title) and ("(" in title or "（" in title), "完整书名含括号别名", title or "书名为空")
    verdict(bool(author), "作者", author or "作者为空")
    verdict(bool(tags), "标签(meta)", tags or "meta 无标签字段（标签实际在网页，见软件）")
    verdict(bool(intro), "简介", "正常" if intro else "简介为空")


def sec4_cover(aid: str) -> None:
    out()
    out(f"[4] 封面下载  -> 期望拿到真实图片字节（书号 {aid or '自动'}）")
    if not aid.isdigit():
        out("  [跳过] 无可用书号。")
        return
    url = f"https://img.wenku8.com/image/{int(aid)//1000}/{aid}/{aid}s.jpg"
    out(f"  直链   : {url}")
    t0 = time.time()
    data = fetch_cover(aid)
    is_img = data[:2] == b"\xff\xd8" or data[:4] == b"\x89PNG" or data[:3] == b"GIF"
    out(f"  耗时 {time.time()-t0:.1f}s，字节数 {len(data)}，图片头 {'是' if is_img else '否'}")
    verdict(len(data) > 200 and is_img, "封面图片可下载",
            "拿到图片" if (len(data) > 200 and is_img) else "空或被拦截 -> 软件显示占位图")


def sec5_download(aid: str) -> None:
    out()
    out(f"[5] 下载链路  -> 期望目录有章节 + 正文可取（书号 {aid or '自动'}）")
    if not aid.isdigit():
        out("  [跳过] 无可用书号。")
        return
    catalog = fetch_catalog(aid)
    out(f"  目录章节数 : {len(catalog)}")
    if catalog:
        out("  前三章     : " + " / ".join(c["title"][:16] for c in catalog[:3]))
    verdict(len(catalog) > 0, "目录非空",
            f"{len(catalog)} 章" if catalog else "0 章 -> 这就是「目录为空，无法下载」")
    if not catalog:
        return
    text, used = "", catalog[0]
    t0 = time.time()
    for ch in catalog[:3]:
        cand = fetch_text(aid, ch["id"])
        if len(cand) > 30:
            text, used = cand, ch
            break
        if not text:
            used = ch
    out(f"  测试章节   : {used['title']}（cid {used['id']}）")
    out(f"  正文长度   : {len(text)} 字，耗时 {time.time()-t0:.1f}s")
    out(f"  正文开头   : {text[:80].strip() or '【空】'}")
    verdict(len(text) > 30, "正文可下载",
            "正常" if len(text) > 30 else "前三章正文均为空 -> 下载会卡在 2%")


def main() -> None:
    out(LINE)
    out("文库8 问题诊断（单文件自包含 · 与软件核心逻辑一致）")
    out(f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}   Python：{sys.version.split()[0]}")
    out(LINE)
    out()
    out(f"本机登录态：{'已读取（Cookie 长度 %d）' % len(COOKIE) if COOKIE else '未登录（未检测到已保存的 Cookie）'}")
    used = api_url()
    out(f"当前 API 端点：{used}{'  [中继]' if used != APP_API_URL else '  [官方直连]'}")

    # 自动自检中继（若已配置）
    if used != APP_API_URL:
        try:
            req = urllib.request.Request(used.rstrip("/") + "/?check=1",
                                         headers={"User-Agent": BROWSER_UA})
            with urllib.request.urlopen(req, timeout=20, context=CTX) as resp:
                chk = resp.read().decode("utf-8", "replace")
            out(f"中继自检：{'PASS - 中继可用' if 'PASS' in chk.upper() else 'FAIL - 检查 Worker 部署'}")
        except Exception as exc:  # noqa: BLE001
            out(f"中继自检：FAIL - {type(exc).__name__}")
    out()

    sec0_raw_probe()
    violet_aid = sec1_violet()
    demon_hits = sec2_demon()
    aid = demon_hits[0]["id"] if demon_hits else (violet_aid if violet_aid.isdigit() else "")
    if not aid.isdigit():
        out()
        out("搜索没拿到书号。若要补测详情/封面/下载，可输入一个已知书号。")
        manual = ""
        if len(sys.argv) > 1 and sys.argv[1].strip().isdigit():
            manual = sys.argv[1].strip()
        else:
            try:
                manual = input("  输入书号（如 1034，回车跳过）：").strip()
            except Exception:  # noqa: BLE001
                manual = ""
        aid = manual if manual.isdigit() else ""
        if aid:
            out(f"  使用手动书号：{aid}")
    sec3_detail(aid)
    sec4_cover(aid)
    sec5_download(aid)
    out()
    out(LINE)
    out("检测结束。请把从「文库8 问题诊断」到这里的全部输出复制给开发者。")
    out(LINE)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        out("\n已中断。")
