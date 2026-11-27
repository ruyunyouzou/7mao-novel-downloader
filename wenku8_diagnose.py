# -*- coding: utf-8 -*-
"""
文库8 本机诊断脚本 v1.0 —— 零第三方依赖，只用 Python 标准库。

在你自己的电脑上运行（双击同目录的「运行文库8检测.bat」也可以）：
    python wenku8_diagnose.py

检测内容，对应软件里那几个一直没法在沙箱实测的功能：
    [1] 官网线路：.net / .cc / .com 的真实状态（是否被 Cloudflare 拦、是否跳登录）
    [2] 站内搜索：4 个候选接口 × GBK / UTF-8 两种编码，找出真正可用的组合
    [3] 分类页兜底 + 归一化匹配：验证「邻家天使」能否命中「邻家的天使」
    [4] 登录：Cookie 登录态 + 账号密码登录（可选，只在内存中使用，不写盘不上传）
    [5] 目录抓取：验证内核能否拿到章节列表（可选）

脚本只发请求、打印结果，不修改任何文件、不保存账号密码。
最后把整段输出复制给开发者即可。
"""

from __future__ import annotations

import concurrent.futures
import getpass
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

LINES = ("https://www.wenku8.net", "https://www.wenku8.cc",
         "https://www.wenku8.com")
KEYWORD = "邻家天使"
TARGET_TITLE = "邻家的天使"
ENDPOINTS = (
    "/modules/article/search.php?searchtype=articlename&searchkey={kw}",
    "/modules/article/search.php?searchkey={kw}",
    "/search.php?keyword={kw}",
    "/so.php?keyword={kw}",
    "/so.php?searchkey={kw}",
)

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE  # 文库8证书链在部分网络下不完整


def out(msg: str = "") -> None:
    print(msg, flush=True)


def request(url: str, cookie: str = "", post: bytes = None,
            timeout: int = 12) -> tuple:
    """返回 (status, final_url, raw_bytes, headers)。异常时 status=0。"""
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if cookie:
        headers["Cookie"] = cookie
    if post is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        parsed = urllib.parse.urlparse(url)
        headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
        headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
    req = urllib.request.Request(url, data=post, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            return r.status, r.geturl(), r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.geturl() or url, e.read(), e.headers
    except Exception as e:  # noqa: BLE001
        return 0, url, str(e).encode("utf-8", "replace"), None


def decode(raw: bytes) -> str:
    for enc in ("gbk", "gb18030", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gbk", "replace")


# ---- 归一化匹配（与项目 wenku8_match.py 等价）------------------------------

def normalize(text: str) -> str:
    s = unicodedata.normalize("NFKC", str(text or "")).lower()
    s = re.sub(r"[\s\-_.，。、·:：;；!！?？\"'（）()\[\]【】《》]", "", s)
    s = re.sub(r"[的之]", "", s)  # 忽略中文虚词，解决「邻家天使」vs「邻家的天使」
    return s


def matches(query: str, title: str) -> bool:
    nq, nt = normalize(query), normalize(title)
    if not nq:
        return True
    if nq in nt:
        return True
    if len(nq) >= 2:  # 子序列兜底
        it = iter(nt)
        if all(ch in it for ch in nq):
            return True
    return False


def extract_books(html: str) -> list:
    """从列表/搜索页提取 (书号, 书名)，兼容 /book/ 与 /novel/ 链接。"""
    books, seen = [], set()
    for m in re.finditer(r"<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
                         html or "", re.I | re.S):
        href, inner = m.group(1), m.group(2)
        bid = None
        mm = re.search(r"/book/(\d+)\.htm", href)
        if mm:
            bid = mm.group(1)
        else:
            mm = re.search(r"/novel/\d+/(\d+)/", href)
            if mm:
                bid = mm.group(1)
        if not bid or bid in seen:
            continue
        title = re.sub(r"<[^>]+>", "", inner).strip()
        if not title or title in ("我要阅读", "返回书页", "点击阅读"):
            continue
        seen.add(bid)
        books.append((bid, title))
    return books


# ---- 各检测步骤 ------------------------------------------------------------

def step0_ip() -> None:
    out("[0] 当前网络出口")
    st, _u, raw, _h = request("https://myip.ipip.net", timeout=8)
    if st == 200:
        text = decode(raw).strip().replace("\n", " ")
        out(f"    IP 信息：{text[:160]}")
    else:
        st, _u, raw, _h = request("https://api.ipify.org", timeout=8)
        out(f"    IP 地址：{decode(raw).strip()[:80]}" if st == 200
            else "    IP 查询失败（不影响后续检测）")
    out()


def step1_lines() -> list:
    out("[1] 官网线路检测")
    usable = []
    for base in LINES:
        st, final, raw, _h = request(base + "/")
        html = decode(raw)
        title_m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
        title = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else ""
        cf = "cloudflare" in html.lower() or "attention required" in html.lower()
        need_login = "login.php" in final
        if st == 200 and not cf and not need_login:
            verdict = "可用"
            usable.append(base)
        else:
            reasons = []
            if st == 403 or cf:
                reasons.append("Cloudflare/403 拦截")
            if st == 0:
                reasons.append("连接失败")
            if need_login:
                reasons.append("跳转登录页")
            verdict = "不可用（" + "、".join(reasons or [f"HTTP {st}"]) + "）"
        out(f"    {base:28} HTTP {st:<4} {verdict}")
        if title:
            out(f"        标题：{title[:70]}")
    out(f"    => 可用线路：{usable if usable else '无'}")
    out()
    return usable


def step2_search(base: str, cookie: str) -> None:
    out(f"[2] 站内搜索检测（关键词：{KEYWORD}，线路：{base}）")
    out("    —— 重点看 GBK 与 UTF-8 哪一行有「命中」——")
    any_hit = False
    for enc_label, enc in (("GBK ", "gbk"), ("UTF8", "utf-8")):
        kw = urllib.parse.quote(KEYWORD.encode(enc))
        for tpl in ENDPOINTS:
            url = base + tpl.format(kw=kw)
            st, final, raw, _h = request(url, cookie=cookie)
            html = decode(raw)
            books = extract_books(html)
            hits = [b for b in books if matches(KEYWORD, b[1])]
            flag = " ★" if hits else ""
            out(f"    [{enc_label}] {tpl:46} HTTP {st:<4} 书 {len(books):<4} "
                f"命中 {len(hits)}{flag}")
            if hits:
                any_hit = True
                for bid, title in hits[:4]:
                    mark = "  ← 目标" if matches(TARGET_TITLE, title) and title == TARGET_TITLE else ""
                    out(f"          - {title} (ID {bid}){mark}")
    out(f"    => 站内搜索：{'有结果' if any_hit else '全部无结果，走分类页兜底'}")
    out()


def step3_categories(base: str, cookie: str) -> None:
    out(f"[3] 分类页兜底 + 模糊匹配（{KEYWORD} → {TARGET_TITLE}）")

    def fetch(cls: int, page: int):
        url = f"{base}/modules/article/articlelist.php?class={cls}&page={page}"
        st, _f, raw, _h = request(url, cookie=cookie)
        return st, extract_books(decode(raw))

    tasks = [(c, p) for c in range(1, 15) for p in (1, 2)]
    total, hits = 0, {}
    status_count = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(fetch, c, p): (c, p) for c, p in tasks}
        for fut in concurrent.futures.as_completed(futs):
            try:
                st, books = fut.result()
                status_count[st] = status_count.get(st, 0) + 1
                total += len(books)
                for bid, title in books:
                    if matches(KEYWORD, title):
                        hits[bid] = title
            except Exception:  # noqa: BLE001
                status_count[-1] = status_count.get(-1, 0) + 1
    out(f"    分类页 {len(tasks)} 个请求，状态分布：{status_count}，共抓到 {total} 本")
    if hits:
        for bid, title in list(hits.items())[:8]:
            mark = "  ← 目标命中" if title == TARGET_TITLE else ""
            out(f"    - {title} (ID {bid}){mark}")
    found = any(title == TARGET_TITLE for title in hits.values())
    out(f"    => {'成功命中《' + TARGET_TITLE + '》' if found else '未命中《' + TARGET_TITLE + '》'}"
        f"（模糊命中共 {len(hits)} 本）")
    out()


def load_saved_settings() -> tuple[str, str]:
    """自动读取软件已保存的 Cookie 与使用中线路（零依赖，只读 settings.json）。

    返回 (cookie, active_base)。读不到返回空串，不抛异常。
    """
    import json
    import os
    cookie, active = "", ""
    path = os.path.join(os.path.expanduser("~"), ".qimao_gui", "settings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cookie = str(data.get("wenku8_cookie", "") or "").strip()
        active = str(data.get("wenku8_base_url", "") or "").strip().rstrip("/")
        # 线路状态里使用中域名优先（官网登录会写这里）
        raw_lines = str(data.get("wenku8_lines", "") or "").strip()
        if raw_lines:
            lines = json.loads(raw_lines)
            if isinstance(lines, dict) and lines.get("active"):
                active = str(lines["active"]).strip().rstrip("/") or active
            # 使用中线路若自带 Cookie，优先用它
            if isinstance(lines, dict):
                for ln in lines.get("lines", []):
                    if (isinstance(ln, dict) and ln.get("base") == active
                            and ln.get("cookie")):
                        cookie = str(ln["cookie"]).strip() or cookie
    except Exception as exc:  # noqa: BLE001
        out(f"    [读取本机设置失败] {exc}")
    return cookie, active


def ask_cookie(saved: str = "") -> str:
    """优先用软件已保存的 Cookie；否则让用户粘贴。后续搜索/目录会带上。"""
    if saved:
        out(f"    已自动读取软件保存的 Cookie（长度 {len(saved)}，"
            f"含登录标志 {'是' if 'jieqiUserInfo' in saved else '否'}），直接用它检测。")
        return saved
    raw = input("    粘贴已登录的 Cookie（直接回车跳过）：").strip()
    return raw


def step4_login(base: str, cookie: str) -> None:
    out("[4] 登录检测（可选，输入只在本机内存中使用）")
    if cookie:
        st, final, _raw, _h = request(base + "/", cookie=cookie)
        html_text = decode(_raw)
        ok = ("login.php" not in final) and any(
            k in html_text for k in ("会员中心", "我的书架", "退出"))
        out(f"    Cookie 登录态：{'有效' if ok else '无效（仍跳登录页或无会员标记）'}"
            f" [HTTP {st}]")
    else:
        out("    已跳过 Cookie 检测")

    account = input("    测试账号密码登录？输入账号（直接回车跳过）：").strip()
    if account:
        password = getpass.getpass("    密码（不回显）：")
        if not password:
            out("    密码为空，已跳过")
        else:
            jumpurl = base.replace("https://", "http://", 1) + "/index.php"
            login_url = (base + "/login.php?do=submit&jumpurl="
                         + urllib.parse.quote(jumpurl, safe=""))
            fields = (
                ("username", account.encode("utf-8")),
                ("password", password.encode("utf-8")),
                ("usecookie", b"315360000"),
                ("action", b"login"),
                ("submit", "登录".encode("gbk")),
            )
            body = "&".join(
                f"{urllib.parse.quote(k)}={urllib.parse.quote_from_bytes(v)}"
                for k, v in fields).encode("ascii")
            st, final, raw, headers = request(login_url, post=body, timeout=20)
            set_cookie = (headers.get("Set-Cookie", "") if headers else "")
            text = decode(raw)
            ok = "jieqiUserInfo" in set_cookie
            if not ok and any(k in text for k in ("登录成功", "退出登录", account)):
                ok = True
            if st == 403:
                reason = "被风控拦截（403）"
            elif ok:
                reason = "登录成功"
            elif any(k in text for k in ("密码错误", "用户名不存在", "验证码", "登录失败")):
                reason = "账号或密码错误 / 需要验证码"
            else:
                reason = "未成功（站点风控或页面结构变化）"
            out(f"    账号密码登录：{reason} [HTTP {st}]，"
                f"Cookie 含 jieqiUserInfo：{'是' if ok else '否'}")
    else:
        out("    已跳过账号密码检测")
    out()


def step5_catalog(base: str, cookie: str) -> None:
    out("[5] 目录抓取检测（可选，验证内核能否拿到章节列表）")
    bid = input("    输入书号，例如 2580（直接回车跳过）：").strip()
    if not bid:
        out("    已跳过目录检测")
        out()
        return
    st, final, raw, _h = request(f"{base}/book/{bid}.htm", cookie=cookie)
    html = decode(raw)
    m = re.search(rf"/novel/(\d+)/{re.escape(bid)}/\d+\.htm", html)
    prefix = f"/novel/{m.group(1)}/{bid}/" if m else f"/novel/0/{bid}/"
    title_m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    book_title = re.sub(r"\s+", " ", title_m.group(1)).strip()[:60] if title_m else "未知"
    out(f"    详情页 HTTP {st}，书名：{book_title}，novel 前缀：{prefix}")
    st2, final2, raw2, _h2 = request(base + prefix + "index.htm", cookie=cookie)
    html2 = decode(raw2)
    chapters = re.findall(r"/novel/\d+/" + re.escape(bid) + r"/\d+\.htm", html2)
    out(f"    目录页 HTTP {st2}，抓到章节链接 {len(set(chapters))} 个")
    out(f"    => 内核目录读取：{'正常' if chapters else '异常（0 章）'}")
    out()


def step6_appapi() -> None:
    """官方 App API 全通道诊断：直接定位搜索/别名/详情/封面/目录真实返回。"""
    import base64 as _b64
    import datetime as _dt

    app_url = "http://app.wenku8.com/android.php"
    app_ua = "Dalvik/2.1.0 (Linux; U; Android 11; IN2010 Build/RP1A.201005.001)"

    def _pct(s, enc):
        return "".join("%%%02X" % b for b in s.encode(enc))

    def _app_post(param):
        form = urllib.parse.urlencode({
            "appver": "1.13",
            "request": _b64.b64encode(param.encode("utf-8")).decode("ascii"),
            "timetoken": str(int(time.time() * 1000)),
        }).encode("ascii")
        req = urllib.request.Request(app_url, data=form, headers={
            "User-Agent": app_ua,
            "Content-Type": "application/x-www-form-urlencoded",
        })
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                return r.status, r.read()
        except Exception as e:  # noqa: BLE001
            return 0, ("EXC %s: %s" % (type(e).__name__, e)).encode("utf-8", "replace")

    def _show(label, param, limit=600):
        st, raw = _app_post(param)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("gbk", "replace")
        preview = text[:limit].replace("\n", " ")
        blocked = "Welcome" in text or "Attention Required" in text
        out(f"    [{label}] HTTP {st} 字节 {len(raw)} "
            f"{'<风控占位页>' if blocked else ''}")
        out(f"        param: {param}")
        out(f"        body : {preview}")

    out("[6] 官方 App API 全通道诊断（定位搜索/别名/封面/详情/目录）")
    for kw in ("恶魔高校", "紫罗兰"):
        out(f"    ---- 关键词：{kw} ----")
        _show("search utf8", "action=search&searchtype=articlename&searchkey=%s&t=0"
              % _pct(kw, "utf-8"))
        _show("search gbk ", "action=search&searchtype=articlename&searchkey=%s&t=0"
              % _pct(kw, "gbk"))
    out("    ---- 榜单元数据（别名匹配的数据源） ----")
    _show("novellist  ", "action=novellist&sort=allvisit&page=1&t=0")
    out("    ---- 详情/封面/目录 ----")
    _show("meta 1034  ", "action=book&do=meta&aid=1034&t=0")
    _show("cover 1034 ", "action=book&do=cover&aid=1034")
    _show("list 1034  ", "action=book&do=list&aid=1034&t=0")
    out()


def main() -> None:
    out("=" * 68)
    out("文库8 本机诊断 v1.1  （结果请整段复制给开发者）")
    out(f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}   Python：{sys.version.split()[0]}")
    out("=" * 68)
    out()
    step0_ip()
    # App API 诊断最重要且不依赖登录/交互，放最前面
    step6_appapi()
    usable = step1_lines()
    if not usable:
        out("!! 三条线路都不可用。后面步骤仍会尝试，但大概率拿不到正常结果。")
        out("   可以把上面 [1] 的输出直接发给开发者，不需要继续。")
        base = LINES[1]
    else:
        base = usable[0]
    out(f"后续步骤使用线路：{base}")
    out()
    saved_cookie, saved_active = load_saved_settings()
    if saved_active:
        out(f"软件保存的使用中线路：{saved_active}（已用它替换探测线路）")
        base = saved_active
    out()

    skip = "--skip-login" in sys.argv
    if not skip:
        out("登录凭据（优先用软件已保存的 Cookie，无需手动粘贴）")
    cookie = "" if skip else ask_cookie(saved_cookie)
    step2_search(base, cookie)
    step3_categories(base, cookie)
    if not skip:
        step4_login(base, cookie)
        step5_catalog(base, cookie)
    out("=" * 68)
    out("检测结束。请把从「文库8 本机诊断」开始到这里的全部输出复制给开发者。")
    out("=" * 68)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        out("\n已中断。")
