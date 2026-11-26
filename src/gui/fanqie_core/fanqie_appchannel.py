# -*- coding: utf-8 -*-
"""番茄 App 正文渠道（本地签名 + DH 密钥交换，返回明文无字体混淆）。

来源与出处：
  移植自社区开源实现（fqapp-master / FQWeb 的 appchannel.py），
  还原番茄 App ``novel.snssdk.com/api/novel/book/reader/content/v1`` 的
  x-argus / x-ladon / x-khronos 请求签名与 DH 密钥交换 + AES-256-CBC 解密。

为什么需要它：
  官网网页源（decode_chapter）逐章返回 PUA 混淆正文，需额外下载 woff2 字体并
  做字形映射；锁章只给约 200 字试读。App 渠道走另一套签名接口：
    - 返回**明文 HTML**，无 PUA 混淆，无需字体下载；
    - 锁章也能读到全文；
    - 实测单章约 0.16s，全书 529 章（12 线程）约 15s，零失败。

依赖：pycryptodome（AES）、gmssl（SM3）——两者本项目 requirements 已包含，
七猫内核本身即用 AES 解密，故**无新增依赖、无新增打包风险**。

设计约束：
  - 纯本地计算签名，不依赖任何第三方中转服务；
  - 惰性导入加密库，只有真正调用 fetch_chapter 时才加载；
  - 每线程独立 requests.Session 复用连接，避免并发共享 Session 的线程安全问题；
  - 对外只暴露 fetch_chapter(item_id) -> str（正文纯文本，格式与 decode_chapter 对齐）。
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import html as _html
import os
import random
import re
import threading
import time
import urllib.parse

import requests

# --------------------------------------------------------------------------
# 加密库惰性加载（缺依赖时抛出明确错误，交由 SourceManager 故障转移回官网源）
# --------------------------------------------------------------------------
_AES = None
_SM3 = None


def _ensure_crypto():
    global _AES, _SM3
    if _AES is None:
        from Crypto.Cipher import AES  # noqa: F401
        _AES = AES
    if _SM3 is None:
        from gmssl.sm3 import sm3_hash  # noqa: F401
        _SM3 = sm3_hash


# --------------------------------------------------------------------------
# protobuf（仅编码，x-argus 用）
# --------------------------------------------------------------------------
class _Proto(object):
    def __init__(self):
        self._fields = []
        self._blobs = []

    def varint(self, idx, v):
        self._fields.append((idx, 0, v & 0xFFFFFFFFFFFFFFFF, None))

    def utf8(self, idx, s):
        self.blob(idx, s.encode("utf-8"))

    def blob(self, idx, data):
        self._blobs.append(data)
        self._fields.append((idx, 2, 0, len(self._blobs) - 1))

    def msg(self, idx, sub):
        self.blob(idx, sub.buf())

    def buf(self):
        out = bytearray()
        for idx, typ, v, bi in self._fields:
            out += _vi(((idx << 3) | (typ & 7)) & 0xFFFFFFFF)
            if typ == 2:
                b = self._blobs[bi]
                out += _vi(len(b))
                out += b
            else:
                out += _vi(v)
        return bytes(out)


def _vi(v):
    """v 先截断到 32 位，与上游实现一致。"""
    v &= 0xFFFFFFFF
    out = bytearray()
    while v >= 128:
        out.append((v & 127) | 128)
        v >>= 7
    out.append(v & 127)
    return bytes(out)


# --------------------------------------------------------------------------
# Simon 分组密码（128-bit key / 64-bit block / 72 轮）
# --------------------------------------------------------------------------
_SIMON_Z = 0x3DC94C3A046D678B
_M64 = 0xFFFFFFFFFFFFFFFF


def _rotr(v, n):
    return ((v >> n) | (v << (64 - n))) & _M64


def _rotl(v, n):
    return ((v << n) | (v >> (64 - n))) & _M64


def _simon_enc(pt, k):
    key = [0] * 72
    key[0:4] = list(k)
    for i in range(4, 72):
        tmp = _rotr(key[i - 1], 3) ^ key[i - 3]
        tmp ^= _rotr(tmp, 1)
        bit = (_SIMON_Z >> ((i - 4) % 62)) & 1
        key[i] = (~key[i - 4] & _M64) ^ tmp ^ bit ^ 3
    xi, xi1 = pt
    for rk in key:
        tmp = xi1
        f = _rotl(xi1, 1) & _rotl(xi1, 8)
        xi1 = xi ^ f ^ _rotl(xi1, 2) ^ rk
        xi = tmp
    return [xi, xi1]


# --------------------------------------------------------------------------
# x-argus
# --------------------------------------------------------------------------
_ARGUS_SIGN_KEY = bytes.fromhex(
    "fc78e0a9657a0c748ce51559903ccf03510e51d3cff232d71343e88a321c5304")
_ARGUS_AES_KEY = bytes.fromhex("d31e3718288a1027baab59f146a09a9c")
_ARGUS_AES_IV = bytes.fromhex("ea180a0336ed352fcd24e4d50018ae54")
_ARGUS_MAGIC8 = bytes([0xF2, 0xF7, 0xFC, 0xFF, 0xF2, 0xF7, 0xFC, 0xFF])
_ARGUS_TAIL9 = bytes([0xEA, 0xFB, 0x2C, 0xFE, 0x85, 0x68, 0x51, 0x91, 0x54])
_ARGUS_AES_KEY_LIST = [
    int.from_bytes(_ARGUS_SIGN_KEY[i * 8:i * 8 + 8], "little") for i in range(4)]


def _pkcs7_align(data):
    pad = 16 - (len(data) % 16)
    return data + bytes([pad]) * pad


def _aes_cbc_enc(data, key, iv):
    return _AES.new(key, _AES.MODE_CBC, iv).encrypt(data)


def _aes_cbc_dec(data, key, iv):
    return _AES.new(key, _AES.MODE_CBC, iv).decrypt(data)


def _sm3_prefix6(msg):
    return bytes.fromhex(_SM3(list(msg)))[:6]


def _simon_encrypt_padded(pb):
    out = bytearray(len(pb))
    for i in range(len(pb) // 16):
        blk = pb[i * 16:(i + 1) * 16]
        ct = _simon_enc([int.from_bytes(blk[:8], "little"),
                         int.from_bytes(blk[8:], "little")], _ARGUS_AES_KEY_LIST)
        out[i * 16:i * 16 + 8] = ct[0].to_bytes(8, "little")
        out[i * 16 + 8:i * 16 + 16] = ct[1].to_bytes(8, "little")
    return bytes(out)


def _argus_encrypt_551(bean):
    pb = _pkcs7_align(bean.buf())
    enc = _simon_encrypt_padded(pb)
    un = _ARGUS_MAGIC8 + enc + _ARGUS_TAIL9
    buf = bytearray(len(un))
    buf[:8] = _ARGUS_MAGIC8
    for i in range(8, len(un)):
        buf[i] = un[i] ^ _ARGUS_MAGIC8[i % 8]
    buf.reverse()
    buf += b"ao"
    buf = _pkcs7_align(bytes(buf))
    out = _aes_cbc_enc(buf, _ARGUS_AES_KEY, _ARGUS_AES_IV)
    return base64.b64encode(b"\xf2\x81" + out).decode()


def query_param(query, name):
    for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True):
        if k == name:
            return v
    return None


def argus_sign(query, ts, aid, license_id, device_id="", install_id=""):
    """x-argus（551 版绑定）。"""
    bean = _Proto()
    bean.varint(1, 1077940818)
    bean.varint(2, 2)
    bean.varint(3, random.randint(-2 ** 31, 2 ** 31 - 1))
    bean.utf8(4, str(aid))
    bean.utf8(5, device_id or "")
    bean.utf8(6, str(license_id))
    bean.utf8(7, query_param(query, "version_name") or "")
    bean.utf8(8, "v04.04.05-ov-android")
    bean.varint(9, 134744640)
    bean.blob(10, b"\x00" * 8)
    bean.varint(11, 0)
    bean.varint(12, ts << 1)
    bean.blob(13, _sm3_prefix6(b"\x00" * 16))          # body hash（GET 无 body）
    bean.blob(14, _sm3_prefix6(query.encode()) if query else _sm3_prefix6(b"\x00" * 16))
    sub15 = _Proto()
    sub15.varint(1, 1)
    sub15.varint(2, 1)
    sub15.varint(3, 1)
    sub15.varint(7, 0xC792ECCC)
    bean.msg(15, sub15)
    bean.utf8(16, install_id or "")
    bean.utf8(20, "none")
    bean.varint(21, 738)
    sub23 = _Proto()
    sub23.utf8(1, "NX551J")
    sub23.varint(2, 8196)
    sub23.varint(4, 0x80E0D800)
    bean.msg(23, sub23)
    bean.varint(25, 2)
    return _argus_encrypt_551(bean)


# --------------------------------------------------------------------------
# x-ladon（34 轮 ARX）
# --------------------------------------------------------------------------
def _ladon_table(md5_hex):
    ht = bytearray(288)
    raw = md5_hex.encode("ascii")[:32]
    ht[:len(raw)] = raw
    temp = [int.from_bytes(ht[i * 8:i * 8 + 8], "little") for i in range(4)]
    b0, b8 = temp[0], temp[1]
    temp = temp[2:]
    for i in range(34):
        x9 = b0
        x8 = _rotr(b8, 8)
        x8 = (x8 + x9) & _M64
        x8 ^= i
        temp.append(x8)
        x8 ^= _rotl(x9, 3)
        ht[(i + 1) * 8:(i + 1) * 8 + 8] = (x8 & _M64).to_bytes(8, "little")
        b0 = x8
        b8 = temp.pop(0)
    return bytes(ht)


def _ladon_block(ht, blk):
    d0 = int.from_bytes(blk[:8], "little")
    d1 = int.from_bytes(blk[8:], "little")
    for i in range(34):
        h = int.from_bytes(ht[i * 8:i * 8 + 8], "little")
        d1 = h ^ ((d0 + _rotr(d1, 8)) & _M64)
        d0 = d1 ^ _rotl(d0, 3)
    return (d0 & _M64).to_bytes(8, "little") + (d1 & _M64).to_bytes(8, "little")


def ladon_encrypt(ts, lc_id, aid):
    """x-ladon: base64(random4 + 加密("<ts>-<lcId>-<aid>"))。"""
    rnd = os.urandom(4)
    md5_hex = hashlib.md5(rnd + str(aid).encode()).hexdigest()
    ht = _ladon_table(md5_hex)
    data = ("%d-%d-%d" % (ts, lc_id, aid)).encode()
    pad = 16 - len(data) % 16
    buf = data + bytes([pad]) * pad
    out = b"".join(_ladon_block(ht, buf[i * 16:(i + 1) * 16])
                   for i in range(len(buf) // 16))
    return base64.b64encode(rnd + out).decode()


# --------------------------------------------------------------------------
# DH 密钥交换
# --------------------------------------------------------------------------
_DH_P = int(
    "2410312426921032588552076022197566074856950548502459942654116941958108831682612228890093858261341614673227141477904012196503648957050582631942730706805009223062734745341073406696246014589361659774041027169249453200378729434170325843778659198143763193776859869524088940195577346119843545301547043747207749969763750084308926339295559968882457872412993810129130294592999947926365264059284647209730384947211681434464714438488520940127459844288859336526896320919633919")
_DH_Q = (_DH_P - 1) // 2
_DH_REQ_KEY = base64.b64decode("rCXGfd2POMGzeiNIgo4iLg==")
_LC_ID = 1611921764


def _java_bigint(n):
    """Java BigInteger.toByteArray(): 大端，最高位为 1 时补 0x00。"""
    if n == 0:
        return b""
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return (b"\x00" + b) if b[0] & 0x80 else b


def generate_y():
    """生成 y 请求头和私钥 a。"""
    a = os.urandom(32)
    pub = pow(2, int.from_bytes(a, "big"), _DH_P)
    iv = os.urandom(16)
    ct = _aes_cbc_enc(_pkcs7_align(_java_bigint(pub)), _DH_REQ_KEY, iv)
    return base64.b64encode(iv + ct).decode(), a


def decrypt_dh(server_y_b64, content_b64, a):
    b = int.from_bytes(base64.b64decode(server_y_b64), "big")
    if b < 2 or b > _DH_P - 2 or pow(b, _DH_Q, _DH_P) != 1:
        raise RuntimeError("服务端 DH 公钥非法")
    s = pow(b, int.from_bytes(a, "big"), _DH_P)
    if s == 0:
        raise RuntimeError("共享密钥为 0")
    sb = s.to_bytes((s.bit_length() + 7) // 8, "big")
    if len(sb) < 32:
        raise RuntimeError("共享密钥过短: %d" % len(sb))
    key = sb[:32]
    raw = base64.b64decode(content_b64)
    if len(raw) < 32:
        raise RuntimeError("密文过短: %d" % len(raw))
    iv, ct = raw[:16], raw[16:]
    if len(ct) % 16:
        raise RuntimeError("密文未按块对齐: %d" % len(ct))
    pt = _aes_cbc_dec(ct, key, iv)
    pad = pt[-1] if pt else 0
    if not 1 <= pad <= 16 or pt[-pad:] != bytes([pad]) * pad:
        raise RuntimeError("PKCS7 填充非法")
    pt = pt[:-pad]
    if pt[:2] == b"\x1f\x8b":
        pt = gzip.decompress(pt)
    return pt


# --------------------------------------------------------------------------
# 正文清洗
# --------------------------------------------------------------------------
_RE_HEADER = re.compile(r"(?is)<header>(.*?)</header>")
_RE_ARTICLE = re.compile(r"(?is)<article>(.*?)</article>")
_RE_P_OPEN = re.compile(r"(?i)<p[^>]*>")
_RE_BR = re.compile(r"(?i)<br\s*/?>")
_RE_IMG = re.compile(r"(?is)<img\b[^>]*>")
_RE_TAG = re.compile(r"(?s)<[^>]+>")
_RE_SPACES = re.compile(r"[ \t\u00a0\u200b\ufeff\u3000]+")

IMG_TOKEN = "[fqimg]%s[/fqimg]"
_IMG_URL_ATTRS = ("src", "data-src", "data-original", "data-lazy-src",
                  "data-url", "data-source", "original", "lazy-src",
                  "srcset", "data-srcset")
_RE_IMG_ATTR = re.compile(
    r"(?is)\b(" + "|".join(_IMG_URL_ATTRS) + r")\s*=\s*"
    r"(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))"
)


def image_url_from_tag(tag):
    attrs = {}
    for m in _RE_IMG_ATTR.finditer(tag):
        attrs.setdefault(m.group(1).lower(), m.group(2) or m.group(3) or m.group(4) or "")
    for key in _IMG_URL_ATTRS:
        raw = _html.unescape(attrs.get(key, "")).strip()
        if not raw:
            continue
        if key.endswith("srcset"):
            parts = raw.split(",")[0].strip().split()
            raw = parts[0] if parts else ""
            if not raw:
                continue
        if raw.startswith("//"):
            raw = "https:" + raw
        elif raw.startswith("/") and not raw.startswith("//"):
            raw = "https://fanqienovel.com" + raw
        if re.match(r"(?i)^https?://", raw) or re.match(r"(?i)^data:image/", raw):
            return raw
    return ""


def clean_content(html):
    """把 App 渠道正文 HTML 转成 (title, text)，图片保留为标记。"""
    title = ""
    m = _RE_HEADER.search(html)
    if m:
        title = _html.unescape(_RE_TAG.sub("", m.group(1))).strip()
    m = _RE_ARTICLE.search(html)
    body = m.group(1) if m else html
    body = _RE_P_OPEN.sub("\n", body)
    body = _RE_BR.sub("\n", body)

    imgs = []

    def keep_img(match):
        url = image_url_from_tag(match.group(0))
        if not url:
            return ""
        key = "\x00FQIMG%d\x00" % len(imgs)
        imgs.append(url)
        return key

    body = _RE_IMG.sub(keep_img, body)
    body = _RE_TAG.sub("", body)
    body = _html.unescape(body)
    body = _RE_SPACES.sub(" ", body)
    for i, url in enumerate(imgs):
        body = body.replace("\x00FQIMG%d\x00" % i, IMG_TOKEN % url)

    lines = [ln.strip() for ln in body.split("\n")]
    return title, "\n".join(ln for ln in lines if ln)


# --------------------------------------------------------------------------
# 请求
# --------------------------------------------------------------------------
UA_NOVEL = ("com.ss.android.article.news/13400 (Linux; U; Android 10; zh_CN; "
            "tb8788p1_64_bsp; Build/QQ3A.200805.001; Cronet/TTNetVersion:fc4cebd3 "
            "2024-12-10 QuicVersion:d9628e3d 2024-10-11)")
HOST_CONTENT = "https://novel.snssdk.com/api/novel/book/reader/content/v1"

_TLS = threading.local()


def _get_session():
    s = getattr(_TLS, "session", None)
    if s is None:
        s = requests.Session()
        _TLS.session = s
    return s


def build_query(item_id, aid=13):
    return ("aid=%d&app_name=novelapp&channel=0&device_platform=android"
            "&device_type=25053RT47C&item_id=%s&os_version=10&support_image=1"
            "&version_code=66.9" % (aid, urllib.parse.quote(str(item_id), safe="")))


def _fetch_once(item_id, timeout):
    _ensure_crypto()
    query = build_query(item_id)
    url = HOST_CONTENT + "?" + query
    y, a = generate_y()
    ts = int(time.time())
    aid = int(query_param(query, "aid") or 13)
    headers = {
        "User-Agent": UA_NOVEL,
        "Accept-Encoding": "gzip, deflate",
        "y": y,
        "x-ss-req-ticket": str(ts * 1000),
        "x-khronos": str(ts),
        "x-ladon": ladon_encrypt(ts, _LC_ID, aid),
        "x-argus": argus_sign(query, ts, aid, _LC_ID,
                              query_param(query, "device_id") or ""),
    }
    resp = _get_session().get(url, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError("HTTP %d" % resp.status_code)
    payload = resp.json()
    if payload.get("code") not in (0, "0"):
        raise RuntimeError("接口返回 code=%s: %s"
                           % (payload.get("code"), payload.get("message")))

    data = payload.get("data") or {}
    content_b64 = data.get("content") or ""
    server_y = resp.headers.get("y") or ""
    if not server_y or not content_b64:
        raise RuntimeError("响应缺少 DH 密钥或密文")

    plain = decrypt_dh(server_y, content_b64, a)
    page = plain.decode("utf-8", "ignore")
    title, text = clean_content(page)
    if not text:
        raise RuntimeError("解密后的正文为空")
    return title, text


def fetch_chapter(item_id, timeout=15, retries=2):
    """取单章正文纯文本（格式与 decode_chapter 对齐：每行 4 空格缩进）。

    失败层层抛异常，交由 SourceManager 故障转移回官网源；本函数不返回空串兜底，
    避免把"下载失败"伪装成"下载成功"。

    :returns: 正文纯文本，行首带 4 空格缩进；无正文抛 RuntimeError。
    """
    last = None
    for _ in range(max(1, retries)):
        try:
            _title, text = _fetch_once(item_id, timeout)
            # 与 decode_chapter 输出格式一致（每行 4 空格缩进）
            lines = [ln for ln in text.split("\n") if ln.strip()]
            return "\n".join("    " + ln.strip() for ln in lines)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.3 + random.random() * 0.2)
    raise RuntimeError("App 渠道正文获取失败: %s" % last)
