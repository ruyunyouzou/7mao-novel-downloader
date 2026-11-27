# -*- coding: utf-8 -*-
"""一次性诊断：在境外 runner 上请求文库8 App API，打印真实响应。

仅用于定位协议方向，不属于产品运行路径。运行：python3 tools/wk8_probe.py
"""
import base64
import time
import urllib.parse
import urllib.request

URL = "http://app.wenku8.com/android.php"
UA = "Dalvik/2.1.0 (Linux; U; Android 11; IN2010 Build/RP1A.201005.001)"


def pct(text, enc):
    return "".join("%%%02X" % b for b in text.encode(enc))


def post(param):
    form = urllib.parse.urlencode({
        "appver": "1.13",
        "request": base64.b64encode(param.encode("utf-8")).decode("ascii"),
        "timetoken": str(int(time.time() * 1000)),
    }).encode("ascii")
    req = urllib.request.Request(URL, data=form, headers={
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, raw
    except Exception as e:  # noqa: BLE001
        return -1, ("EXC %s: %s" % (type(e).__name__, e)).encode("utf-8", "replace")


def show(label, param):
    status, raw = post(param)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gbk", "replace")
    print("=" * 70)
    print("[%s] status=%s len=%d" % (label, status, len(raw)))
    print("param:", param)
    print("body:", text[:1500])


for kw in ("恶魔高校", "紫罗兰"):
    show("search utf8pct %s" % kw,
         "action=search&searchtype=articlename&searchkey=%s&t=0" % pct(kw, "utf-8"))
    show("search gbkpct %s" % kw,
         "action=search&searchtype=articlename&searchkey=%s&t=0" % pct(kw, "gbk"))
    show("search raw %s" % kw,
         "action=search&searchtype=articlename&searchkey=%s&t=0" % kw)

for aid in ("2580", "1034"):
    show("meta %s" % aid, "action=book&do=meta&aid=%s&t=0" % aid)
    show("info %s" % aid, "action=book&do=info&aid=%s&t=0" % aid)
    show("list %s" % aid, "action=book&do=list&aid=%s&t=0" % aid)
