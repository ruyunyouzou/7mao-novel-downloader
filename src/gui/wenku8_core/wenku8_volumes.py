# -*- coding: utf-8 -*-
"""文库8分卷导出：把目录按卷分组，供「整本 / 按卷分文件 / 仅所选卷」三种模式使用。

本模块**只被文库8下载插件调用**，番茄 / 七猫链路完全不引用，零回归。

设计要点
--------
- group_volumes(): 依章节的 vol 字段（wenku8_reader 解析自目录页 td.vcss）分组，
  返回每卷的 name / start / end（1-based 全局章节序号，与 build_txt 的
  start_chapter/end_chapter 语义完全一致）/ chapters / count。
- select_volumes(): 按模式挑出要导出的卷。
- volume_filename_template(): 生成带卷名、章节范围的文件名模板，交给 build_txt
  的 render_filename 渲染，天然支持用户的文件名模板习惯。
- 正文不在此模块抓取：由 Wenku8BookSource 的缓存层保证全书只抓一次。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

# 正文里插图 URL 的占位标记（wenku8_reader.fetch_chapter_text 写入）
IMG_MARK_RE = re.compile(r"\[\[WENKU8IMG:([^\]]+)\]\]")

# 文库8图片服务器要求浏览器头，伪装 Edge；与 cover_helper 保持一致
IMAGE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0")
# 备用 UA：主 UA 连续失败时轮换，规避单一指纹被限流
IMAGE_UA_ALT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36")
# 简短 UA：调研 open source 项目（fateking27/mojimoon）证实完整浏览器 UA
# 反而更容易被 Cloudflare 拦，简短 UA / okhttp 更稳，作为轮换的一环。
IMAGE_UA_SHORT = "Mozilla/5.0"
IMAGE_UA_OKHTTP = "okhttp/4.12.0"
_IMAGE_UAS = (IMAGE_UA, IMAGE_UA_SHORT, IMAGE_UA_OKHTTP, IMAGE_UA_ALT)


def extract_image_urls(text: str) -> List[str]:
    """从正文文本里提取全部插图 URL（按出现顺序，去重）。"""
    if not text:
        return []
    out: List[str] = []
    for m in IMG_MARK_RE.finditer(str(text)):
        url = str(m.group(1) or "").strip()
        if url and url not in out:
            out.append(url)
    return out


def is_image_bytes(raw: bytes) -> bool:
    """校验响应体是不是真实图片（拒绝 403/404 的 HTML 错误页）。"""
    if not raw:
        return False
    return (raw.startswith(b"\xff\xd8\xff") or            # JPEG
            raw.startswith(b"\x89PNG\r\n\x1a\n") or       # PNG
            raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a") or
            (raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"))  # WebP


def _image_session():
    """每个工作线程一个复用的 requests 会话。

    图床每张图都新建 requests.get() 意味着 270 次 TCP+TLS 握手；实测
    40 张图「每次新建」14.0s、「会话复用」7.1s，握手占掉近一半时间。
    requests.Session 非线程安全，必须放 threading.local 每线程独立持有，
    由线程池长期复用（与正文的 CloakSession 模型一致）。
    """
    import threading
    import requests
    tls = getattr(_image_session, "_tls", None)
    if tls is None:
        tls = threading.local()
        _image_session._tls = tls
    sess = getattr(tls, "session", None)
    if sess is None:
        sess = requests.Session()
        tls.session = sess
    return sess


def download_image(url: str, timeout: int = 15, attempts: int = 5) -> bytes:
    """下载一张文库8插图，返回图片字节；全部尝试失败才返回 b''。

    严格实现：多轮重试 + Referer/UA 轮换，单轮拿到真实图片魔数即返回。
    返回的 b'' 代表「这张图确实没下下来」，由调用方决定是否判下载失败——
    本函数绝不静默吞掉错误，也不把 403/404 的错误页当图片返回。
    """
    url = str(url or "").strip()
    if not url.startswith("http"):
        return b""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc or "www.wenku8.cc"
    except Exception:  # noqa: BLE001
        host = "www.wenku8.cc"
    import time as _time
    for attempt in range(max(1, int(attempts))):
        try:
            import requests
            # 轮换 Referer：图片域根 / 官网根目录 / 正文页目录，覆盖不同防盗链策略
            referers = [
                f"https://{host}/",
                "https://www.wenku8.cc/",
                "https://www.wenku8.com/",
            ]
            headers = {
                # 多 UA 轮换：完整 Edge / 简短 Mozilla / okhttp / 完整 Chrome。
                # 首轮用完整 UA（真实成功率最高），后续轮换以防被指纹限流。
                "User-Agent": _IMAGE_UAS[attempt % len(_IMAGE_UAS)],
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": referers[attempt % len(referers)],
            }
            # 会话复用：同一线程内多次下载共享 TCP/TLS 连接，省掉每张图的
            # 握手开销（实测 40 张图 14.0s → 7.1s）。
            resp = _image_session().get(url, timeout=timeout, headers=headers)
            raw = resp.content or b""
            if is_image_bytes(raw):
                return raw
        except Exception:  # noqa: BLE001
            pass
        if attempt < max(1, int(attempts)) - 1:
            _time.sleep(0.3 * (attempt + 1))  # 递增退避，避免瞬时打爆图床

    # 指纹层兜底：图床将来若上 Cloudflare，普通 requests 会全部被拦。
    # 仅当上面所有 attempts 都失败才走这里，正常情况零开销；每个工作线程
    # 复用自己的 CloakSession（httpcloak 非线程安全，绝不能跨线程共享）。
    raw = _download_image_cloak(url, timeout=timeout, referer=referers[0])
    return raw if is_image_bytes(raw) else b""


def _download_image_cloak(url: str, timeout: int = 15,
                          referer: str = "") -> bytes:
    """httpcloak 指纹层兜底取图（每线程一个会话，失败返回 b''）。

    独立于主抓取通道，图床域名与主站不同：这里只借用浏览器 TLS/HTTP2 指纹
    绕过可能的 CDN 拦截，不碰主站登录态。会话放 threading.local 长期复用，
    避免每图新建会话导致握手风暴。
    """
    import threading
    _tls = getattr(_download_image_cloak, "_tls", None)
    if _tls is None:
        _tls = threading.local()
        _download_image_cloak._tls = _tls
    sess = getattr(_tls, "session", None)
    for attempt in range(2):
        try:
            if sess is None:
                from gui.wenku8_core.wenku8_http import CloakSession
                sess = CloakSession(timeout=timeout)
                _tls.session = sess
            resp = sess.get(
                url, timeout=timeout,
                headers={
                    "User-Agent": _IMAGE_UAS[0],
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Referer": referer or "https://www.wenku8.cc/",
                },
            )
            raw = resp.content or b""
            if is_image_bytes(raw):
                return raw
        except Exception:  # noqa: BLE001
            # 会话可能因网络抖动损坏，下次重建
            sess = None
            try:
                delattr(_tls, "session")
            except Exception:  # noqa: BLE001
                pass
        if attempt < 1:
            import time as _time
            _time.sleep(0.5)
    return b""


def is_portrait(data: bytes) -> bool:
    """判断图片是否为竖图（高 >= 宽）。库封面/插图封面通常是竖版长图。

    用 PIL 读尺寸；无法解析时保守返回 False（不当封面，交给调用方回退）。
    """
    if not data:
        return False
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
        return h >= w
    except Exception:  # noqa: BLE001
        return False


def pick_cover_bytes(urls: Sequence[str], get_bytes, retry=None) -> bytes:
    """从图片 URL 列表里挑「第一张竖图」作为卷封面字节。

    参考 Yorag/wenku8-to-epub 与 fateking27/wenku8-downloader：分卷封面取该卷
    插图页第一张竖版长图（width <= height）；该卷没有竖图时退回第一张成功
    下载的图片，保证每卷封面都来自本卷插图页而非全书总封面；全部失败才
    返回 b''。get_bytes(url) 从预取缓存取字节，不发网络。

    参数 retry(url)：可选。当某个候选 URL 在缓存里缺失时，调用它做一次
    「直达重试」（真正发网络、校验图片魔数并返回字节，失败返回 b''）。这是
    卷封面概率性失败的修复关键——预取并发下载时真封面那张图偶发抖动失败，
    旧逻辑直接跳过它、降级到后面的目录页/内页竖图，封面就错了；现在先重试
    把首选候选拿回来，只有重试也失败才降级。正常情况（图都在缓存）零调用、
    零额外网络开销。retry 的调用方负责把结果写回自己的缓存。
    """
    first = b""
    for url in urls or []:
        data = get_bytes(url) if get_bytes is not None else b""
        if not data and retry is not None:
            # 首选候选缺失：直达重试，尽量把这张图拿回来而不是静默降级。
            try:
                retried = retry(url)
            except Exception:  # noqa: BLE001
                retried = b""
            if retried:
                data = retried
        if not data:
            continue
        if not first:
            first = data  # 兜底第一张成功的图
        if is_portrait(data):
            return data  # 第一张竖图即卷封面
    return first


def image_ext(data: bytes) -> str:
    """按图片字节判定扩展名（供 TXT 落地命名复用，与 EPUB 内嵌一致）。"""
    if data.startswith(b"\x89PNG"):
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if len(data) >= 12 and data[8:12] == b"WEBP":
        return "webp"
    return "jpg"


def sanitize_vol_name(s: Any) -> str:
    """把卷名清洗成可做文件名的安全字符串（供书源缓存与命名共用）。"""
    name = _clean(s).replace("{", "").replace("}", "") or "卷"
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()

# 三种导出模式
MODE_WHOLE = "whole"          # 整本导出（默认，等价于旧行为）
MODE_PER_VOLUME = "per_volume"  # 按卷分文件，每卷一个 EPUB/TXT
MODE_SELECTED = "selected"    # 仅导出勾选的卷（每卷一个文件）


def _clean(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def group_volumes(catalog: Sequence[Dict]) -> List[Dict]:
    """把归一后的目录按 vol 字段切成卷。

    目录里 vol 相同时视为同一卷；vol 变化即开新卷（同名卷分开出现也各成一卷，
    用 start 区分，导出文件名带范围不会冲突）。无 vol 的章节归入「正文」卷。

    :return: [{name, start, end, count, chapters}]，start/end 为 1-based 全局序号
    """
    volumes: List[Dict] = []
    for ch in catalog:
        idx = int(ch.get("index", 0) or 0) + 1
        name = _clean(ch.get("vol")) or "正文"
        if not volumes or name != volumes[-1]["name"]:
            volumes.append({
                "name": name,
                "start": idx,
                "end": idx,
                "count": 0,
                "chapters": [],
            })
        vol = volumes[-1]
        vol["end"] = idx
        vol["count"] += 1
        vol["chapters"].append(ch)
    return volumes


def select_volumes(volumes: Sequence[Dict], mode: str,
                   selected: Optional[Sequence[str]] = None) -> List[Dict]:
    """按导出模式挑卷。

    :param mode: MODE_WHOLE / MODE_PER_VOLUME / MODE_SELECTED
    :param selected: MODE_SELECTED 时勾选的卷名列表（卷名去重前先按列表顺序保留）
    :return: 待导出的卷列表
    """
    mode = str(mode or MODE_WHOLE)
    if mode == MODE_PER_VOLUME:
        return [dict(v) for v in volumes]
    if mode == MODE_SELECTED:
        wanted = {_clean(x) for x in (selected or [])}
        if not wanted:
            return []
        # 挑刺 #121（用户「分卷导出有可能会失败」）：精确匹配太脆——GUI 勾选时
        # 的卷名与下载时站点返回的卷名可能因空格/上下册后缀（「第二卷」vs
        # 「第二卷 下」）、全半角、站点波动而不一致，导致「未选择任何卷」整单
        # 失败。三级匹配兜底：①完全相等；②去所有空白后相等；③去空白后一方
        # 是另一方前缀（用户勾「第二卷」应命中「第二卷 下」「第二卷 上」）。
        # 只做「勾选名 → 实际卷」单向包含，避免一个短卷名误命中一堆长卷。
        def _norm(s: str) -> str:
            return re.sub(r"\s+", "", _clean(s)).lower()

        # 优先精确匹配：只要勾选名能精确命中某些卷，就只返回这些卷。
        # 这样「短篇」「杂志短篇」并存时勾「短篇」不会误带出「杂志短篇」。
        exact: List[Dict] = []
        for v in volumes:
            vn = _norm(v.get("name"))
            if any(vn == _norm(w) for w in wanted if _norm(w)):
                exact.append(dict(v))
        if exact:
            return exact
        # 精确全不中才启用容错：勾选名是实际卷名的子串（勾「第二卷」命中
        # 「第二卷 下/上」），覆盖站点波动导致的上下册后缀/空格差异。
        picked: List[Dict] = []
        for v in volumes:
            vn = _norm(v.get("name"))
            if any(wn and wn in vn for wn in (_norm(w) for w in wanted)):
                picked.append(dict(v))
        return picked
    # MODE_WHOLE 及未知模式：整本一卷
    if not volumes:
        return []
    return [{
        "name": "全本",
        "start": volumes[0]["start"],
        "end": volumes[-1]["end"],
        "count": sum(int(v.get("count", 0)) for v in volumes),
        "chapters": [ch for v in volumes for ch in v.get("chapters", [])],
    }]


def volume_filename_template(base_template: str, vol_name: str) -> str:
    """生成单卷文件名模板（含卷名与章节范围占位符）。

    卷名里的花括号会破坏 str.format，统一剔除；斜杠等非法字符由
    build_txt 内的 sanitize_filename 处理。
    """
    vol = _clean(vol_name).replace("{", "").replace("}", "") or "卷"
    base = str(base_template or "").strip() or "{title}"
    return f"{base}_{vol}_{{start}}-{{end}}"


def volume_label(vol: Dict) -> str:
    """卷的简短展示文案，供队列/结果消息用。"""
    return f"{_clean(vol.get('name')) or '卷'}（{vol.get('start', 1)}-{vol.get('end', 1)}章）"
