# -*- coding: utf-8 -*-
"""统一封面加载工具（历史记录 / 任务队列 / 阅读页封面墙共用）。

背景：这三处此前的封面位都只用「书名首字」占位——
- 历史记录 / 书架落库时没存 cover；
- 队列 book 也可能没有 cover；
- 阅读页封面墙虽然会按 cover_url 联网取图，但 cover_url 恒为空。

而下载 EPUB 时 ``qimao_core`` 已把封面以内嵌图片写进文件
（``book.set_cover("cover.jpg", img)``），所以本地文件才是最可靠的封面来源：
离线可用、不依赖网络、不会卡顿。本模块负责：

1. ``local_cover(path)``——从本地 EPUB / 同名图片提取封面（带缓存）；
2. ``fallback_pixmap(title, w, h)``——无封面时的渐变首字兜底；
3. ``AsyncCover``——仅在需要时按 URL 异步补图（复用 book_card 的崩溃防护机制）。

所有函数均容忍空路径 / 坏文件 / 不存在的图片，绝不抛异常到调用方。
"""

from __future__ import annotations

import os
import re
import zipfile
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPixmap

# 封面图 CDN（如 img.wenku8.com）会拦截无浏览器指纹的裸 requests（403 返回
# HTML 页面）。所有页面共用这一套浏览器头：UA + 站点 Referer，必要时配合
# Accept 头。这是"全部书封面都显示占位图"的根因修复——之前多个加载点都用
# requests.get(url) 裸请求，统一改走这里。
_COVER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _is_image_bytes(content: bytes) -> bool:
    """魔数校验，拒绝把 403 的 HTML 错误页当图片喂给 QImage。"""
    c = (content or b"")[:512]
    low = c.lower()
    if (b"<html" in low or b"<!doctype" in low or b"<h1>welcome</h1>" in low
            or b"attention required" in low or b"just a moment" in low):
        return False
    return (
        content.startswith(b"\xff\xd8")              # JPEG
        or content.startswith(b"\x89PNG\r\n\x1a\n")  # PNG
        or content.startswith(b"GIF8")               # GIF
        or (content[:4] == b"RIFF" and content[8:12] == b"WEBP")
        or content.startswith(b"BM")                 # BMP
    )


def _fetch_cover_cloak(url: str, timeout: int, referer: str = "") -> bytes:
    """httpcloak 浏览器指纹兜底取封面（每线程一个会话，失败返回 b''）。

    根因修复：img.wenku8.com 等图床会按 TLS/HTTP2 指纹拦截裸 requests，
    只加 UA/Referer 头不够——沙箱出口能过、用户本机被拦，表现就是"全部书
    封面变首字占位图"。下载正文/插图链路早已走 httpcloak，唯独封面链路漏了。
    这里只借用浏览器指纹绕过 CDN 拦截，不碰任何登录态。会话放 threading.local
    长期复用（httpcloak 非线程安全，绝不能跨线程共享），与下载链路模型一致。
    仅在裸 requests 失败后才调用，正常情况零开销。
    """
    import threading
    _tls = getattr(_fetch_cover_cloak, "_tls", None)
    if _tls is None:
        _tls = threading.local()
        _fetch_cover_cloak._tls = _tls
    sess = getattr(_tls, "session", None)
    for attempt in range(2):
        try:
            if sess is None:
                from .wenku8_core.wenku8_http import CloakSession
                sess = CloakSession(timeout=timeout)
                _tls.session = sess
            resp = sess.get(url, timeout=timeout, headers={
                "User-Agent": _COVER_UA,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*:q=0.8",
                "Referer": referer or "https://www.wenku8.cc/",
            })
            raw = resp.content or b""
            if _is_image_bytes(raw):
                return raw
        except Exception:  # noqa: BLE001
            sess = None
            try:
                delattr(_tls, "session")
            except Exception:  # noqa: BLE001
                pass
        if attempt < 1:
            import time as _time
            _time.sleep(0.4)
    return b""


def fetch_cover_bytes(url: str, timeout: int = 10) -> bytes:
    """带浏览器头下载封面字节，含魔数校验；失败再走 httpcloak 指纹兜底。

    这是所有页面封面加载的统一入口：搜索卡片、详情页、历史/队列/封面墙
    都复用它。裸 requests 被图床按 TLS 指纹拦截时（用户本机被拦、沙箱能过的
    典型场景），指纹层救回真实图片，彻底消除"全部书封面变占位图"。
    """
    import requests
    if not url or not url.lower().startswith(("http://", "https://")):
        return b""
    headers = {"User-Agent": _COVER_UA, "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"}
    try:
        host = urlparse(url).netloc
        # 站点 Referer：让 CDN 认为是站内图片请求
        headers["Referer"] = f"https://{host}/"
    except Exception:  # noqa: BLE001
        host = "www.wenku8.cc"
    try:
        resp = requests.get(url, timeout=timeout, headers=headers)
        content = resp.content or b""
        if _is_image_bytes(content):
            return content
    except Exception:  # noqa: BLE001
        pass
    # 指纹兜底：裸请求被 CDN 按 TLS 指纹拦掉时救回（正常情况不走到这里）
    return _fetch_cover_cloak(url, timeout, headers.get("Referer", ""))


def fetch_cover_smart(url: str, title: str = "", timeout: int = 10) -> bytes:
    """封面统一智能入口：文库8优先本地缓存的大图，否则走带指纹兜底的小图。

    优先级：
    1. 本地已有 EPUB 内嵌封面由调用方处理（本函数只管联网/缓存部分）；
    2. 文库8图床 URL → 只读本地已缓存的 Bangumi 官方大图（≈800×1200，
       **零网络**）；缓存未命中就直接用文库8小图（≈217×310，本身是官方
       日版封面），绝不因 Bangumi 连不上而阻塞封面加载；
    3. 其他书源 → 原样走带浏览器头 + 指纹兜底的直链下载。

    联网抓取 Bangumi 大图只在用户主动运行检测脚本时发生，不进入 UI 热路径——
    否则国内网络连不上 api.bgm.tv 时每张封面都要白等超时，反而更慢。
    全程吞异常，绝不把网络错误抛到加载线程之外。
    """
    if not url:
        return b""
    try:
        from .wenku8_core.wenku8_official_cover import (
            cached_official_cover, extract_aid,
        )
        aid = extract_aid(url)
        if "wenku8" in url.lower() and aid:
            # 只读本地缓存，零网络；不在这里联网抓 Bangumi
            official = cached_official_cover(aid)
            if official and _is_image_bytes(official):
                return official
    except Exception:  # noqa: BLE001
        pass
    return fetch_cover_bytes(url, timeout=timeout)

# 与 reader_view 保持一致的兜底渐变配色
_COVER_GRADIENTS = [
    ("#7e6aa6", "#3f3560"), ("#5a86ad", "#304a63"), ("#b87a58", "#6e4634"),
    ("#6a9e7a", "#365442"), ("#a06a8f", "#5a3550"), ("#b0935a", "#6b552f"),
    ("#5a8f9e", "#33545c"), ("#9e6a6a", "#5c3a3a"),
]

_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def _first_image(data: bytes) -> Optional[QImage]:
    img = QImage.fromData(data)
    return img if not img.isNull() else None


def _epub_cover(path: str) -> Optional[QImage]:
    """从 EPUB 压缩包里提取封面图。

    依次尝试：OPF 里 ``<meta name="cover">`` / ``properties="cover-image"``
    指向的图片、以及常见封面文件名。返回 ``None`` 表示无内嵌封面。
    """
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            lower = {n.lower(): n for n in names}

            # 1) 解析 container.xml -> OPF 路径
            opf_name = None
            if "meta-inf/container.xml" in lower:
                try:
                    container = zf.read(lower["meta-inf/container.xml"]).decode(
                        "utf-8", "replace")
                    m = re.search(r'full-path=["\']([^"\']+\.opf)["\']', container, re.I)
                    if m:
                        opf_name = m.group(1)
                except Exception:  # noqa: BLE001
                    pass
            if not opf_name:
                for n in names:
                    if n.lower().endswith(".opf"):
                        opf_name = n
                        break

            hrefs: list[str] = []
            if opf_name:
                try:
                    opf = zf.read(opf_name).decode("utf-8", "replace")
                    base = os.path.dirname(opf_name)
                    # <meta name="cover" content="cover-id"/>
                    ids: list[str] = []
                    for m in re.finditer(
                            r'<meta\b[^>]*name=["\']cover["\'][^>]*content=["\']([^"\']+)["\']',
                            opf, re.I):
                        ids.append(m.group(1))
                    for m in re.finditer(
                            r'<meta\b[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']cover["\']',
                            opf, re.I):
                        ids.append(m.group(1))
                    # manifest item -> href
                    for item in re.finditer(r'<item\b[^>]*>', opf, re.I):
                        tag = item.group(0)
                        props = re.search(r'properties=["\']([^"\']+)["\']', tag, re.I)
                        iid = re.search(r'\bid=["\']([^"\']+)["\']', tag, re.I)
                        href = re.search(r'\bhref=["\']([^"\']+)["\']', tag, re.I)
                        if not href:
                            continue
                        if props and "cover-image" in props.group(1).lower():
                            hrefs.append(href.group(1))
                        if iid and iid.group(1) in ids:
                            hrefs.append(href.group(1))
                    if base:
                        hrefs = [os.path.normpath(os.path.join(base, h)).replace("\\", "/")
                                 for h in hrefs]
                except Exception:  # noqa: BLE001
                    pass

            # 2) 按解析出的 href 读取
            for href in hrefs:
                key = href.lstrip("/")
                real = lower.get(key.lower())
                if real:
                    img = _first_image(zf.read(real))
                    if img:
                        return img

            # 3) 兜底：常见封面文件名
            for name in names:
                low = name.lower()
                if "cover" in low and low.endswith(_IMG_EXTS):
                    img = _first_image(zf.read(name))
                    if img:
                        return img
    except Exception:  # noqa: BLE001 - 坏 zip / 不存在 / 权限问题一律忽略
        pass
    return None


def _sidecar_cover(path: str) -> Optional[QImage]:
    """TXT 等书的同级封面：同名图片或 cover.* / 封面.*。"""
    root, _ = os.path.splitext(path)
    folder = os.path.dirname(root)
    stems = [os.path.basename(root), "cover", "封面"]
    tried = set()
    for stem in stems:
        for ext in _IMG_EXTS:
            cand = os.path.join(folder, stem + ext)
            key = cand.lower()
            if key in tried:
                continue
            tried.add(key)
            if os.path.isfile(cand):
                try:
                    img = QImage(cand)
                    if not img.isNull():
                        return img
                    with open(cand, "rb") as f:
                        img = _first_image(f.read())
                    if img:
                        return img
                except Exception:  # noqa: BLE001
                    pass
    return None


@lru_cache(maxsize=256)
def _cached_local_cover(path: str, mtime: float) -> Optional[QImage]:
    if not path or not os.path.isfile(path):
        return None
    if path.lower().endswith(".epub"):
        img = _epub_cover(path)
        if img:
            return img
    img = _sidecar_cover(path)
    return img


def local_cover(path: str) -> Optional[QImage]:
    """从本地书籍文件提取封面；按 (路径, 修改时间) 缓存。"""
    if not path:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    return _cached_local_cover(path, mtime)


def has_local_cover(path: str) -> bool:
    """是否存在本地封面（供调用方决定是否再走 URL 补图）。"""
    img = local_cover(path)
    return bool(img and not img.isNull())


def scaled_pixmap(img: QImage, w: int, h: int) -> QPixmap:
    return QPixmap.fromImage(img).scaled(
        w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def fallback_pixmap(title: str, w: int, h: int) -> QPixmap:
    """按书名生成渐变色封面 + 首字（无真实封面时兜底）。"""
    idx = sum(ord(c) for c in (title or "书")) % len(_COVER_GRADIENTS)
    c1, c2 = _COVER_GRADIENTS[idx]
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(c2))
    p = QPainter(img)
    grad = QLinearGradient(0, 0, 0, h)
    grad.setColorAt(0, QColor(c1))
    grad.setColorAt(1, QColor(c2))
    p.fillRect(img.rect(), grad)
    p.setPen(QColor("#ffffff"))
    font_size = max(12, int(min(w, h) * 0.45))
    f = QFont("Microsoft YaHei", font_size)
    p.setFont(f)
    ch = (title or "书").strip()[:1] or "书"
    p.drawText(img.rect(), Qt.AlignCenter, ch)
    p.end()
    return QPixmap.fromImage(img)


def pixmap_for(title: str, path: str = "", w: int = 60, h: int = 80) -> QPixmap:
    """封面位统一入口：本地真实封面优先，否则渐变首字兜底。"""
    img = local_cover(path)
    if img and not img.isNull():
        return scaled_pixmap(img, w, h)
    return fallback_pixmap(title, w, h)


class AsyncCover(QThread):
    """按 URL 异步加载封面图（仅在本地无封面且记录带 cover URL 时使用）。

    不设 parent、纳入全局追踪并自释放，避免卡片销毁时
    "QThread: Destroyed while thread is still running" 崩溃。
    """

    loaded = Signal(str, QImage)

    _orphans: set = set()

    def __init__(self, url: str, title: str = "") -> None:
        super().__init__()
        self.url = url
        # 文库8书名用于严格匹配 Bangumi 官方大图（括号别名参与匹配）
        self.title = title
        AsyncCover._orphans.add(self)
        self.finished.connect(lambda l=self: self._release(l))

    @staticmethod
    def _release(loader: "AsyncCover") -> None:
        AsyncCover._orphans.discard(loader)
        loader.deleteLater()

    def run(self) -> None:
        try:
            # 统一智能入口：文库8优先官方高清大图，失败回退小图；
            # 裸 requests.get 会被 img CDN 403，故不走它。
            raw = fetch_cover_smart(self.url, getattr(self, "title", ""))
            img = _first_image(raw) if raw else None
            if img:
                self.loaded.emit(self.url, img)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def stop_all() -> None:
        """程序退出时统一停止仍在运行的加载线程。"""
        for loader in list(AsyncCover._orphans):
            try:
                if loader.isRunning():
                    loader.wait(800)
            except Exception:  # noqa: BLE001
                pass
