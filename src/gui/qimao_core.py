# -*- coding: utf-8 -*-
"""
七猫小说真实下载核心（从用户提供的 v4.0.4 源码 + 作者继任项目公开协议还原）。

作者开源的 v4.0.4 版本把章节下载内核移除了（声明"核心代码保持私有"），
但保留了完整的签名算法、AES 解密密钥与目录接口。本模块据此还原了完整下载链路：

    搜索 -> 目录(chapter-list) -> 缓存ZIP(book/download) -> AES解密每章 -> 写出文件

协议来源：
  - 用户提供的 七猫小说下载器-源码-v4.0.4.zip（SLQimao/book.py 的 _sign/_decrypt/get_catalog）
  - 作者继任开源项目 shing-yu/swiftcat-downloader-flutter 的 api_client.dart（端点与参数）

本模块被 qimao_downloader_plugin.py 调用，也可独立使用。
"""

from __future__ import annotations

import contextlib
import html
import io
import os
import re
import zipfile
from base64 import b64decode
from typing import Callable, Dict, List, Optional

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

# 七猫接口签名（全项目唯一实现，与 SLQimao 内核逐位等价）
from . import qimao_sign as _qimao_sign

# 字体资源目录（随软件分发的内置字体，OFL 授权；兼容源码运行与 PyInstaller 打包）
from .resource_path import resource_path as _resource_path
_FONT_ASSETS_DIR = _resource_path("assets", "fonts")

# 随软件分发的内置字体：key -> (相对 assets/fonts 的路径, 显示名, @font-face 家族名)
# 全部为 Google Fonts 开源字体（SIL OFL 1.1，允许嵌入与再分发），随软件分发。
EPUB_BUILTIN_FONTS = {
    "mashan": ("MaShanZheng-Regular.ttf", "马善政毛笔楷书", "MaShanZheng"),
    "zhimang": ("ZhiMangXing-Regular.ttf", "钟齐志莽行书", "ZhiMangXing"),
    # --- 免费开源字体库（下载前可选）---
    "kuaile": ("free/ZCOOLKuaiLe-Regular.ttf", "站酷快乐体", "ZCOOLKuaiLe"),
    "qingke": ("free/ZCOOLQingKeHuangYou-Regular.ttf", "站酷庆科黄油体", "ZCOOLQingKeHuangYou"),
    "xiaowei": ("free/ZCOOLXiaoWei-Regular.ttf", "站酷小薇体", "ZCOOLXiaoWei"),
    "longcang": ("free/LongCang-Regular.ttf", "龙藏体", "LongCang"),
    "liujian": ("free/LiuJianMaoCao-Regular.ttf", "柳建毛草体", "LiuJianMaoCao"),
}

# EPUB 排版字体方案：值为 CSS font-family 栈（系统字体，不嵌入）。
EPUB_FONT_SCHEMES = {
    "song": ('"Noto Serif CJK SC","Source Han Serif SC","Songti SC","SimSun","宋体",serif', "宋体书卷"),
    "kai": ('"LXGW WenKai","Kaiti SC","KaiTi","楷体","STKaiti",serif', "霞鹜文楷"),
    "hei": ('"Noto Sans CJK SC","Source Han Sans SC","PingFang SC","Microsoft YaHei","微软雅黑",sans-serif', "黑体清朗"),
    "mono": ('"Noto Sans Mono CJK SC","Sarasa Mono SC","Menlo",monospace', "等宽格调"),
}


def font_combo_items():
    """返回字体下拉的全部可选项 [(配置值, 显示名)]：内置嵌入字体在前，系统预设在后。"""
    items = []
    for key, (_, disp, _) in EPUB_BUILTIN_FONTS.items():
        items.append((f"builtin:{key}", disp))
    for key, (_, label) in EPUB_FONT_SCHEMES.items():
        items.append((key, label))
    return items


def font_label(cfg: str) -> str:
    """把字体配置值转成可读显示名。"""
    if not cfg:
        return ""
    if cfg.startswith("builtin:"):
        key = cfg.split(":", 1)[1]
        if key in EPUB_BUILTIN_FONTS:
            return EPUB_BUILTIN_FONTS[key][1]
    if cfg in EPUB_FONT_SCHEMES:
        return EPUB_FONT_SCHEMES[cfg][1]
    return os.path.basename(cfg)

# 分卷标题拆分：以"第X卷 卷名"开头，后接"第X章 ..."章节部分。
# 覆盖变体：第872 谁吞谁章 / 第979、980章 / 第一百四十一章 等。
# 不匹配"风卷决""残卷"这类只含"卷"字的普通章节名（无卷前缀）。
_VOLUME_SPLIT_RE = re.compile(
    r"^(第[一二三四五六七八九十百千零〇两0-9]+卷\s*[^第]{0,20}?)"
    r"[\s\u3000]+"
    r"(第[一二三四五六七八九十百千零〇两0-9、]+\s*.*)$"
)

# 分卷默认字体：当未显式指定分卷字体时，选一个与章节预设不同的字体栈
_VOLUME_FONT_CHOICE = {"song": "hei", "kai": "hei", "hei": "song", "mono": "hei"}

# 可嵌入 EPUB 的字体文件格式 -> MIME / @font-face format()
_EMBED_FONT_MIME = {
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}
_EMBED_FONT_FORMAT = {
    ".ttf": "truetype",
    ".otf": "opentype",
    ".woff": "woff",
    ".woff2": "woff2",
}
# 内嵌字体被精简内核（如微信读书）忽略时的系统回退栈：
# 毛笔/楷书类优先回退到系统楷体，否则宋体，保证字体不崩、风格接近。
_EMBED_FALLBACK_STACK = '"Kaiti SC","KaiTi","楷体","STKaiti","Songti SC","SimSun","宋体",serif'


def resolve_font(value: Optional[str], book, epub_mod, uid: str, family_name: str):
    """解析字体配置，返回 (font_family 值, @font-face 声明)。

    支持四种配置值：
      1. 预设方案 key（song/kai/hei/mono）→ 系统字体栈，不嵌入
      2. builtin:<key>（如 builtin:mashan）→ 随软件分发的内置字体文件，嵌入
      3. 字体文件路径（.ttf/.otf/.woff/.woff2，文件存在）→ 复制进 EPUB 并用 @font-face 嵌入
      4. 其它字符串 → 当作自定义字体名（如 "方正兰亭黑"），依赖阅读器系统字体
    """
    value = (value or "").strip()
    if not value:
        value = "song"
    # 1) 预设方案
    if value in EPUB_FONT_SCHEMES:
        return EPUB_FONT_SCHEMES[value][0], ""
    # 2) 内置字体 builtin:<key>
    if value.startswith("builtin:"):
        bkey = value.split(":", 1)[1]
        if bkey in EPUB_BUILTIN_FONTS:
            fname_disk, _, builtin_family = EPUB_BUILTIN_FONTS[bkey]
            disk_path = os.path.join(_FONT_ASSETS_DIR, fname_disk)
            if os.path.isfile(disk_path):
                ext = os.path.splitext(fname_disk)[1].lower()
                with open(disk_path, "rb") as fh:
                    data = fh.read()
                target = f"Fonts/{uid}{ext}"
                book.add_item(epub_mod.EpubItem(
                    uid=f"font_{uid}", file_name=target,
                    media_type=_EMBED_FONT_MIME.get(ext, "application/octet-stream"),
                    content=data))
                fmt = _EMBED_FONT_FORMAT.get(ext, "truetype")
                at_rule = (
                    f'@font-face {{ font-family: "{builtin_family}"; '
                    f'src: url("{target}") format("{fmt}"); }}'
                )
                # 字体族后带系统回退栈：微信读书等忽略内嵌字体时优雅回退，不会崩
                return f'"{builtin_family}", {_EMBED_FALLBACK_STACK}', at_rule
    # 3) 字体文件 → 嵌入
    ext = os.path.splitext(value)[1].lower()
    if ext in _EMBED_FONT_MIME and os.path.isfile(value):
        with open(value, "rb") as fh:
            data = fh.read()
        fname = f"Fonts/{uid}{ext}"
        item = epub_mod.EpubItem(
            uid=f"font_{uid}", file_name=fname,
            media_type=_EMBED_FONT_MIME[ext], content=data,
        )
        book.add_item(item)
        fmt = _EMBED_FONT_FORMAT.get(ext, "truetype")
        at_rule = (
            f'@font-face {{ font-family: "{family_name}"; '
            f'src: url("Fonts/{uid}{ext}") format("{fmt}"); }}'
        )
        return f'"{family_name}", {_EMBED_FALLBACK_STACK}', at_rule
    # 4) 自定义字体名
    name = value.strip('"\'')
    return f'"{name}"', ""


def split_volume_title(title: str):
    """把含分卷前缀的章节标题拆成 (卷名, 章节名)；无分卷返回 (None, title)。"""
    m = _VOLUME_SPLIT_RE.match(title or "")
    if not m:
        return None, title
    vol = m.group(1).strip()
    ch = m.group(2).strip()
    # 卷名必须真的以"第X卷"开头且不含章节关键词，避免误拆
    if not vol.startswith("第") or "章" in vol:
        return None, title
    return vol, ch

# 签名与请求头统一由 gui.qimao_sign 提供（与 SLQimao 内核逐位等价，306 个
# 书号实测零差异）。这里以具名绑定再导出，既有调用点与外部引用都不受影响。
SIGN_KEY = _qimao_sign.SIGN_KEY
VERSION_LIST = _qimao_sign.VERSION_LIST
sign_params = _qimao_sign.sign_params
get_headers = _qimao_sign.get_headers

AES_KEY_HEX = "32343263636238323330643730396531"
AES_KEY = bytes.fromhex(AES_KEY_HEX)
BASE_BC = "https://api-bc.wtzw.com"
BASE_KS = "https://api-ks.wtzw.com"


class QimaoError(Exception):
    """七猫接口错误。"""


class ProgressThrottle:
    """
    进度回调节流器：只在百分比至少变化 ``step`` 时才转发，
    避免整本书上千章下载时每章都触发信号导致 UI 高频刷新/崩溃。
    """

    def __init__(self, cb: Optional[Callable[[int], None]], step: int = 1) -> None:
        self.cb = cb
        self.step = max(1, step)
        self._last = -1

    def __call__(self, value: int) -> None:
        value = int(value)
        if self.cb and (value >= self._last + self.step or value == 100):
            self._last = value
            self.cb(value)


def clean_html(text: str) -> str:
    """去掉接口返回书名/作者里的高亮 HTML 标签，如 <font color='#ff4242'>斗</font>。"""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", str(text)).strip()


def _get(session: requests.Session, url: str, params: Dict, book_id: str,
         timeout: int = 20) -> Dict:
    resp = session.get(
        url, params=sign_params(params), headers=get_headers(book_id), timeout=timeout
    )
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise QimaoError(f"接口返回非JSON(HTTP {resp.status_code}): {resp.text[:120]}") from exc
    if "errors" in data:
        raise QimaoError(f"接口错误: {data['errors'].get('details', data['errors'])}")
    return data


def search_books(keyword: str, session: Optional[requests.Session] = None) -> List[Dict]:
    """搜索书籍，返回 [{id,title,author,...}]。"""
    session = session or requests.Session()
    params = {
        "extend": "", "tab": "0", "gender": "0", "refresh_state": "8",
        "page": "1", "wd": keyword, "is_short_story_user": "0",
    }
    data = _get(session, f"{BASE_BC}/search/v1/words", params, "00000000")
    books = (data.get("data") or {}).get("books") or []
    result = []
    for b in books:
        if not b.get("id"):
            continue
        result.append({
            "id": str(b["id"]),
            "title": clean_html(b.get("title") or b.get("name") or ""),
            "author": clean_html(b.get("author") or ""),
            "words_num": b.get("words_num") or b.get("word_count") or "",
            "cover": b.get("cover") or b.get("image_link") or "",
        })
    return result


def get_detail(book_id: str, session: Optional[requests.Session] = None) -> Dict:
    """获取书籍详情（新版 v4 接口，带 imei_ip）。"""
    session = session or requests.Session()
    data = _get(session, f"{BASE_BC}/api/v4/book/detail",
                {"id": book_id, "imei_ip": "2937357107", "teeny_mode": "0"}, book_id)
    book = (data.get("data") or {}).get("book") or (data.get("data") or {})
    return {
        "id": book_id,
        "title": book.get("title") or book.get("name") or "",
        "author": book.get("author") or "",
        "intro": book.get("intro") or book.get("description") or "",
        "cover": book.get("image_link") or book.get("cover") or "",
        "words_num": book.get("words_num") or "",
        "latest_chapter_id": book.get("latest_chapter_id") or "",
    }


def get_catalog(book_id: str, session: Optional[requests.Session] = None) -> List[Dict]:
    """获取目录，按 chapter_sort 排序，返回 [{id,title,index}]。"""
    session = session or requests.Session()
    data = _get(session, f"{BASE_KS}/api/v1/chapter/chapter-list",
                {"chapter_ver": "0", "id": book_id}, book_id)
    chapters = (data.get("data") or {}).get("chapter_lists") or []

    def _sort_num(c: Dict, fallback: int = 0) -> int:
        """安全地把 chapter_sort 转成整数，脏数据回退到 fallback，避免整章下载中断。"""
        try:
            return int(c.get("chapter_sort", fallback))
        except (TypeError, ValueError):
            return fallback

    chapters = sorted(chapters, key=lambda c: _sort_num(c, 0))
    return [
        {"id": str(c["id"]), "title": c.get("title") or f"第{c.get('index', i + 1)}章",
         "index": _sort_num(c, i + 1)}
        for i, c in enumerate(chapters)
    ]


def get_cache_zip_link(book_id: str, session: Optional[requests.Session] = None) -> str:
    """获取整本书的加密章节缓存 ZIP 直链。"""
    session = session or requests.Session()
    data = _get(session, f"{BASE_BC}/api/v1/book/download",
                {"id": book_id, "source": 1, "type": 2, "is_vip": 1}, book_id)
    link = (data.get("data") or {}).get("link")
    if not link:
        raise QimaoError("未获取到缓存下载地址（可能该书无缓存/VIP限制）")
    return link


def download_cache_zip(link: str, session: Optional[requests.Session] = None,
                       progress_cb: Optional[Callable[[int], None]] = None,
                       timeout: int = 180) -> bytes:
    """下载缓存 ZIP 的字节内容。"""
    session = session or requests.Session()
    resp = session.get(link, stream=True, timeout=timeout)
    if resp.status_code != 200:
        raise QimaoError(f"缓存下载失败: HTTP {resp.status_code}")
    # Content-Length 可能被代理改成畸形值，转换失败就退化为不显示细分进度
    try:
        total = int(resp.headers.get("Content-Length", 0))
    except (TypeError, ValueError):
        total = 0
    buf = io.BytesIO()
    done = 0
    for chunk in resp.iter_content(chunk_size=65536):
        if chunk:
            buf.write(chunk)
            done += len(chunk)
            if total and progress_cb:
                progress_cb(min(40, int(done / total * 40)))  # 下载阶段占 0-40%
    return buf.getvalue()


def decrypt_chapter(encrypted: bytes) -> str:
    """解密单个章节：整体 base64 解码，前 16 字节为 IV，其余为 AES-CBC 密文。"""
    raw = b64decode(encrypted)
    if len(raw) < 16:
        raise QimaoError("章节数据过短，无法解密")
    iv, data = raw[:16], raw[16:]
    decrypted = unpad(AES.new(AES_KEY, AES.MODE_CBC, iv=iv).decrypt(data), AES.block_size)
    return decrypted.decode("utf-8", errors="replace").strip()


def sanitize_filename(name: str) -> str:
    """替换文件名非法字符。"""
    return re.sub(r'[\/:*?"<>|]', "_", name).strip()


def _discard_partial(path: str) -> None:
    """删除未写完的临时产物（``*.part``）；失败静默，不影响主流程。"""
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


DEFAULT_FILENAME_TEMPLATE = "{title}"

# 标题高亮色（白底 EPUB 用深色字，保证对比与可读）
CHAPTER_TITLE_COLOR = "#c62828"   # 章名：中国红
VOLUME_TITLE_COLOR = "#1565c0"    # 卷名：深蓝（与章名形成对比）

# 标题颜色预设。格式与项目其它渐变一致：纯色 #RRGGBB 或 linear:{angle}|{c1},{c2},...
# 每项 (value, i18n_key)；i18n_key 用于三语文案，值为空串表示"自定义"（取色器）。
TITLE_COLOR_PRESETS = [
    ("#c62828", "set.color_crimson"),
    ("#1565c0", "set.color_royalblue"),
    ("#2e7d32", "set.color_forest"),
    ("#6a1b9a", "set.color_violet"),
    ("#ef6c00", "set.color_amber"),
    ("#37474f", "set.color_ink"),
    ("linear:0|#c62828,#ff6f00", "set.grad_flame"),
    ("linear:0|#1565c0,#26c6da", "set.grad_ocean"),
    ("linear:0|#2e7d32,#aed581", "set.grad_forest"),
    ("linear:0|#6a1b9a,#e040fb", "set.grad_violet"),
    ("linear:0|#f57f17,#ffca28", "set.grad_sunset"),
    ("linear:0|#37474f,#78909c", "set.grad_mist"),
]


def _safe_color(value: str, default: str) -> str:
    """校验标题颜色：接受 #RRGGBB / #RGB / 颜色名 / linear:角度|c1,c2 渐变。

    渐变格式与 nation_theme 保持一致；校验每个色标合法、至少 2 色，否则回退默认值。
    """
    v = (value or "").strip()
    if not v:
        return default
    if v.startswith("linear:"):
        try:
            body = v[len("linear:"):]
            angle_s, colors_s = body.split("|", 1)
            int(angle_s)  # 角度必须是整数
            colors = [c.strip() for c in colors_s.split(",") if c.strip()]
            ok = len(colors) >= 2 and all(
                re.fullmatch(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})", c) or
                re.fullmatch(r"[a-zA-Z]{3,20}", c) for c in colors)
            return v if ok else default
        except Exception:  # noqa: BLE001
            return default
    if re.fullmatch(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})", v):
        return v
    if re.fullmatch(r"[a-zA-Z]{3,20}", v):  # 简单颜色名如 red/blue
        return v
    return default


def title_color_css(selector: str, color: str) -> str:
    """生成标题颜色的 CSS，兼顾微信读书等精简内核的兼容性。

    渐变文字依赖 ``background-clip:text`` + ``-webkit-text-fill-color:transparent``，
    这是网页特效而非 EPUB 标准能力：微信读书/静读天下等精简 WebKit 内核要么整块
    忽略，要么（最坏情况）认了 transparent 却不渲染渐变，导致标题文字消失。

    加固策略（保证任何引擎下标题都清晰可见）：
    1. 基础规则同时写 ``color`` 与 ``-webkit-text-fill-color`` 为渐变首色——
       这是纯色兜底，所有阅读器都能显示；
    2. ``@supports`` 用严格的 **AND** 条件，要求 background-clip 与
       text-fill-color 两者都被声明支持，才应用透明渐变，避免「半支持」错配；
    3. 渐变写在 ``background-image``（不覆盖其它背景声明）。

    效果：完整支持 → 渐变文字；不支持/半支持 → 首色纯色，绝不透明消失。
    """
    v = (color or "").strip()
    if v.startswith("linear:"):
        try:
            angle_s, colors_s = v[len("linear:"):].split("|", 1)
            angle = int(angle_s)
            colors = [c.strip() for c in colors_s.split(",") if c.strip()]
            if len(colors) >= 2:
                stops = ",".join(colors)
                first = colors[0]
                return (
                    # 纯色兜底：color 与 text-fill-color 都设为首色
                    f"{selector} {{ color: {first}; -webkit-text-fill-color: {first}; }}\n"
                    # 严格 AND：两者都支持才启用透明渐变
                    f"@supports (-webkit-background-clip: text) and "
                    f"(-webkit-text-fill-color: transparent) {{\n"
                    f"  {selector} {{ background-image: linear-gradient({angle}deg, {stops}); "
                    f"-webkit-background-clip: text; background-clip: text; "
                    f"-webkit-text-fill-color: transparent; }}\n"
                    f"}}\n"
                )
        except Exception:  # noqa: BLE001
            pass
    # 纯色：color 与 text-fill-color 都写，避免个别内核 text-fill-color 残留透明
    return f"{selector} {{ color: {v}; -webkit-text-fill-color: {v}; }}\n"


def _plain_title_color(color: str) -> str:
    """标题内联色的取值：渐变取首色作纯色兜底。

    微信读书等精简内核在「有卷名」的层级 EPUB 里会把 h3 章名当目录标题，
    用自己的黑色样式覆盖外部 CSS 类，导致章名变黑（无卷名时 h2 不受影响、
    颜色正常）。内联 style 优先级最高，可绕过该覆盖；但内联无法可靠渲染
    background-clip 渐变文字，故渐变一律退回首色，保证任何内核都清晰可见。
    """
    v = (color or "").strip()
    if v.startswith("linear:"):
        try:
            colors = [c.strip() for c in v[len("linear:"):].split("|", 1)[1]
                      .split(",") if c.strip()]
            if colors:
                v = colors[0]
        except Exception:  # noqa: BLE001
            v = ""
    return v


def title_inline_style(color: str) -> str:
    """生成可直接拼进标题元素的 style 属性（含引号），空色返回空串。

    color 与 -webkit-text-fill-color 双写，兼容部分内核只认后者的行为。
    """
    v = _plain_title_color(color)
    if not v:
        return ""
    return f' style="color:{v};-webkit-text-fill-color:{v};"'


def render_filename(template: str, title: str, author: str = "", book_id: str = "",
                    fmt: str = "txt", start: int = 0, end: int = 0,
                    total: int = 0) -> str:
    """按模板渲染输出文件名（不含扩展名）。

    支持占位符：
      {title} 书名  {author} 作者  {id} 书籍ID  {format} 格式
      {start} 起始章  {end} 结束章  {total} 总章数
    非法文件名字符会被替换；模板为空时回退为书名。
    """
    tpl = (template or "").strip() or DEFAULT_FILENAME_TEMPLATE
    values = {
        "title": title or f"book_{book_id}",
        "author": author or "",
        "id": book_id,
        "format": fmt,
        "start": str(start),
        "end": str(end),
        "total": str(total),
    }
    try:
        name = tpl.format(**values)
    except (KeyError, ValueError):
        name = values["title"]
    # 防止模板产生路径穿越或空名
    name = sanitize_filename(name.replace("/", "_").replace("\\", "_"))
    return name or values["title"]


class BookSource:
    """章节源抽象：把「取详情 / 取目录 / 取章节正文」与「导出渲染」解耦。

    build_txt 负责所有通用导出逻辑（文件名模板、章节范围、TXT/分章/EPUB、
    题图尾图、字体颜色、对话着色）；具体站点只需实现本接口即可复用全部能力。
    默认实现为 QimaoBookSource，保持七猫内核原有行为完全不变。

    子类实现：
      - source_key: 书源标识（如 qimao / fanqie）
      - fetch_detail(book_id, session) -> {title, author, intro, cover}
      - fetch_catalog(book_id, session) -> [{id, title, index}]
      - context(book_id, session, report) -> 上下文管理器，产出可供
        chapter_text 使用的 ctx（七猫用它持有缓存 ZIP）
      - chapter_text(ctx, chapter, session, report) -> 章节正文字符串
    """

    source_key: str = ""
    name: str = "未命名书源"

    def fetch_detail(self, book_id: str, session) -> Dict:
        raise NotImplementedError

    def fetch_catalog(self, book_id: str, session) -> List[Dict]:
        raise NotImplementedError

    def context(self, book_id: str, session, report):
        return contextlib.nullcontext(None)

    def chapter_text(self, ctx, chapter: Dict, session, report) -> str:
        raise NotImplementedError

    def prefetch_texts(self, ctx, catalog, session, on_progress=None):
        """可选：并发预取全部章节正文，返回与 catalog 等长的 list。

        默认返回 None 表示不支持，build_txt 仍逐章调用 chapter_text（七猫走此路径，
        其正文来自本地缓存 ZIP 解密，已足够快）。支持并发的书源（番茄）覆写此方法：
        工作线程只做纯网络抓取，on_progress 由主线程回调以安全触发进度/取消检查。
        """
        return None

    def fetch_source_info(self, book_id: str, session) -> Dict:
        """可选：站点专属的额外元信息（番茄简介等），默认空。"""
        return {}


class QimaoBookSource(BookSource):
    """七猫章节源：缓存 ZIP + AES 解密（与原有链路逐行等价）。"""

    source_key = "qimao"
    name = "七猫小说"

    def fetch_detail(self, book_id: str, session) -> Dict:
        return get_detail(book_id, session)

    def fetch_catalog(self, book_id: str, session) -> List[Dict]:
        catalog = get_catalog(book_id, session)
        # 归一层级：绝大多数七猫标题是纯「第X章」→ 保持一层零回归；
        # 极少数标题自带「第X卷」前缀 → 解析成卷+章两级。
        try:
            from .hierarchy import normalize_catalog_safe
        except ImportError:  # 插件以顶层模块加载时的兜底（与其他核心一致）
            from gui.hierarchy import normalize_catalog_safe
        return normalize_catalog_safe(catalog)

    @contextlib.contextmanager
    def context(self, book_id: str, session, report):
        link = get_cache_zip_link(book_id, session)
        zip_bytes = download_cache_zip(link, session, report)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            name_map = {
                os.path.splitext(os.path.basename(n))[0]: n for n in zf.namelist()
            }
            yield {"zf": zf, "name_map": name_map}

    def chapter_text(self, ctx, chapter: Dict, session, report) -> str:
        member = ctx["name_map"].get(chapter["id"])
        if not member:
            return ""
        return decrypt_chapter(ctx["zf"].read(member))


def book_source_for(source_key: str = "") -> BookSource:
    """按书源标识构造章节源；空值/未知值回退七猫（兼容旧书与旧记录）。"""
    key = str(source_key or "").strip().lower()
    if key == "fanqie":
        try:
            from plugins.fanqie_downloader_plugin import FanqieBookSource
            return FanqieBookSource()
        except Exception:  # noqa: BLE001
            pass
    if key == "wenku8":
        try:
            from plugins.wenku8_downloader_plugin import Wenku8BookSource
            return Wenku8BookSource()
        except Exception:  # noqa: BLE001
            pass
    return QimaoBookSource()


def build_txt(book_id: str, save_path: str, fmt: str,
              progress_cb: Optional[Callable[[int], None]] = None,
              title_hint: str = "", max_chapters: int = 0,
              start_chapter: int = 0, end_chapter: int = 0,
              filename_template: str = "", author_hint: str = "",
              colorize_dialogue: bool = True,
              font_scheme: str = "",
              chapter_font: str = "", volume_font: str = "",
              chapter_color: str = "", volume_color: str = "",
              chapter_hero: Optional[bool] = None, hero_mode: Optional[str] = None,
              hero_fixed: str = "", hero_library: str = "genshin",
              chapter_tail: Optional[bool] = None, tail_mode: Optional[str] = None,
              tail_fixed: str = "", smart_art: Optional[bool] = None,
              source: Optional[BookSource] = None) -> Dict:
    """
    完整下载一本书并写出文件。

    :param book_id: 书籍 ID
    :param save_path: 保存目录
    :param fmt: txt(单文件) / chapter(分章) / epub
    :param progress_cb: 进度回调 0-100
    :param title_hint: 书名提示（优先用于文件名）
    :param max_chapters: 兼容参数，仅下载前 N 章（0=全部；优先用 start_chapter/end_chapter）
    :param start_chapter: 起始章节序号（含，从 1 计；0=从第一章）
    :param end_chapter: 结束章节序号（含，0=到最后一章）
    :param filename_template: 输出文件名模板，见 render_filename
    :param author_hint: 作者提示（用于 {author} 占位符）
    :param colorize_dialogue: 仅 EPUB 生效——下载时把人物对话固化为带颜色的 span，
        使文件自带彩色对话（不依赖阅读器的事后渲染）。默认 True。
    :param font_scheme: （兼容旧版）章节字体方案 key，见 EPUB_FONT_SCHEMES。
    :param chapter_font: 章节标题字体配置，可为预设 key / 字体文件路径 / 自定义字体名。
    :param volume_font: 分卷标题字体配置，取值同上；为空时自动取与章节不同的默认字体。
    :param chapter_color: 章名高亮色（#RRGGBB），为空用默认中国红。
    :param volume_color: 卷名高亮色（#RRGGBB），为空用默认深蓝。
        标题与正文同字号、居中，由内嵌 CSS 控制；正文不设字体/颜色，保持阅读器默认。
    :param chapter_hero: 仅 EPUB 生效——每章标题上方插入章首题图（原神祈愿立绘，
        底部渐隐已烘焙进 PNG）。默认 False（关闭）。
    :param hero_mode: 题图分配方式：rotate=按章轮换 / fixed=固定一张 / none=不用。
    :param hero_fixed: hero_mode=fixed 时指定的题图 key（如 Columbina）。
    :param hero_library: 题图图库 key（保留兼容，题图固定用 genshin 原神库）。
    :param chapter_tail: 仅 EPUB 生效——每章正文之后插入章末尾图（原神地理志
        风景图，顶部渐隐已烘焙进 PNG）。默认 False（关闭）。
    :param tail_mode: 尾图分配方式：rotate=按章轮换 / fixed=固定一张 / none=不用。
    :param tail_fixed: tail_mode=fixed 时指定的尾图 key。
    :param smart_art: 智能选题预设：题图每本随机 40 张（必含 7 张神明），尾图随机 100 张。
        True=启用 / False=关闭 / None=读取全局设置。
        缺省（如追更重建未传参）时，题图参数自动回退到全局设置。
    :return: {file_path, title, chapters, ...}
    """
    # ---- 题图参数缺省时回退到全局设置（修复追更重建丢图） ----
    def _g(key: str, default):
        try:
            from .datastore import DataStore
        except ImportError:  # 顶层模块加载兜底
            from gui.datastore import DataStore
        return DataStore().settings_get(key, default)

    if chapter_hero is None:
        chapter_hero = bool(_g("epub_chapter_hero", False))
    if hero_mode is None:
        hero_mode = str(_g("epub_hero_mode", "rotate") or "rotate")
    if smart_art is None:
        smart_art = bool(_g("epub_smart_art", True))
    # 尾图参数缺省时同样回退到全局设置（与题图一致，追更重建不丢图）
    if chapter_tail is None:
        chapter_tail = bool(_g("epub_chapter_tail", False))
    if tail_mode is None:
        tail_mode = str(_g("epub_tail_mode", "rotate") or "rotate")
    hero_mode = str(hero_mode or "rotate")
    tail_mode = str(tail_mode or "rotate")
    book_seed = f"{book_id}|{title_hint}"

    # 节流：全本书上千章时，进度只在整数百分比变化时回调，避免 UI 高频刷新
    report = ProgressThrottle(progress_cb, step=1)
    # 先把进度推到 2%，让用户在「取详情/目录」阶段就能看到任务在动，
    # 而不是停在 0% 误以为死锁（网络慢时这一阶段可能耗时数秒）。
    report(2)
    session = requests.Session()
    source = source or QimaoBookSource()
    # 详情（失败不致命，用提示书名兜底）
    try:
        detail = source.fetch_detail(book_id, session) or {}
        title = title_hint or detail.get("title") or f"book_{book_id}"
    except Exception:  # noqa: BLE001
        detail = {}
        title = title_hint or f"book_{book_id}"
    title = sanitize_filename(title) or f"book_{book_id}"

    # 目录
    catalog = source.fetch_catalog(book_id, session)
    if not catalog:
        raise QimaoError("目录为空，无法下载")
    # 章节范围：优先用 start/end，其次 max_chapters；默认全本
    total_chapters = len(catalog)
    if start_chapter > 0 or end_chapter > 0:
        effective_start = max(1, start_chapter or 1)
        effective_end = min(total_chapters, end_chapter or total_chapters)
        catalog = catalog[effective_start - 1:effective_end]
    elif max_chapters:
        effective_start = 1
        effective_end = min(max_chapters, total_chapters)
        catalog = catalog[:max_chapters]
    else:
        effective_start = 1
        effective_end = total_chapters
    report(2)

    # 目录层级开关：章节带 vol/sub 元数据时启用「卷/集/章」分级导出；
    # 纯章目录（旧七猫/未归一的书源）保持原样，零回归。
    try:
        from .hierarchy import has_hierarchy as _has_hierarchy
        _hierarchy = _has_hierarchy(catalog)
    except Exception:  # noqa: BLE001
        _hierarchy = False

    # 渲染输出文件名（章节范围写入 {start}/{end}）
    out_name = render_filename(
        filename_template, title,
        author=author_hint or str(detail.get("author") or ""),
        book_id=book_id, fmt=fmt,
        start=effective_start,
        end=effective_end,
        total=total_chapters,
    )

    # 章节源上下文：七猫=缓存 ZIP 解密，番茄=直连正文接口；导出逻辑完全共用
    with source.context(book_id, session, report) as ctx:
        os.makedirs(save_path, exist_ok=True)
        chapters_written = 0

        # 可选并发预取正文：番茄逐章请求是整链路瓶颈（1441 章=1441 次网络往返），
        # 由书源覆写 prefetch_texts 一次并发抓完；七猫返回 None 保持原逐章解密路径。
        # 进度在预取收集阶段由主线程推进到 40%，实际渲染阶段再推 40→100。
        prefetched = source.prefetch_texts(
            ctx, catalog, session,
            on_progress=lambda done, total: report(2 + int(done / total * 38)),
        )

        def _chapter_text(i, ch):
            """优先用预取结果；未预取（七猫）或预取失败时回退逐章抓取。"""
            if prefetched is not None and i < len(prefetched) and prefetched[i]:
                return prefetched[i]
            return source.chapter_text(ctx, ch, session, report)

        if fmt == "chapter":
            # 分章 txt：每章一个文件
            book_dir = os.path.join(save_path, out_name)
            os.makedirs(book_dir, exist_ok=True)
            for i, ch in enumerate(catalog):
                text = _chapter_text(i, ch)
                if not text:
                    # 失败章节也推进度，避免进度条卡在某一档让用户误以为死锁
                    report(40 + int((i + 1) / len(catalog) * 60))
                    continue
                fname = sanitize_filename(f"{ch['index']:04d}_{ch['title']}") + ".txt"
                # 与单文件 TXT 统一带 BOM：微信读书等阅读器对无 BOM 的中文 TXT
                # 偶发误判为 GBK 导致乱码。三源共用本导出函数，分章格式同样受益。
                with open(os.path.join(book_dir, fname), "w", encoding="utf-8-sig") as f:
                    # 分章 TXT：章文件内写卷/集归属，导入微信读书后可识别层级
                    hdr = ""
                    if _hierarchy:
                        bits = [p.strip() for p in (
                            str(ch.get("vol") or ""), str(ch.get("sub") or "")) if p.strip()]
                        if bits:
                            hdr = " › ".join(bits) + "\n\n"
                    f.write(f"{hdr}{ch['title']}\n\n{text}\n")
                chapters_written += 1
                report(40 + int((i + 1) / len(catalog) * 60))
            result_path = book_dir

        elif fmt == "epub":
            from ebooklib import epub
            book = epub.EpubBook()
            book.set_identifier(book_id)
            book.set_title(title)
            book.set_language("zh-CN")
            if detail.get("author"):
                book.add_author(detail["author"])
            if detail.get("intro"):
                book.add_metadata("DC", "description", detail["intro"])
            # 封面：本地文件（番茄 App 保存的原图）直接读；网络 URL 才下载。
            # 文库8图片服务器 img.wenku8.com 会拒绝无 UA 的裸请求（返回 403
            # HTML 页），此前用裸 session.get 下载得到错误页被 set_cover 抛异常
            # 吞掉 → EPUB 无封面。这里带浏览器 UA + 站点 Referer 下载，并校验
            # 图片魔数，拒绝错误页；http 明文一律升级为 https（两站均支持）。
            cover_src = detail.get("cover")
            if cover_src:
                try:
                    if os.path.isfile(str(cover_src)):
                        with open(cover_src, "rb") as _f:
                            img = _f.read()
                    else:
                        url = str(cover_src)
                        if url.startswith("http://"):
                            url = "https://" + url[len("http://"):]
                        cov_headers = {
                            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                                           "Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"),
                            "Referer": "https://www.wenku8.cc/",
                        }
                        resp = session.get(url, headers=cov_headers, timeout=15)
                        raw = resp.content or b""
                        # 只接受真实图片；403/404 的 HTML 页直接丢弃
                        magic = (
                            raw.startswith(b"\xff\xd8\xff") or   # JPEG
                            raw.startswith(b"\x89PNG\r\n\x1a\n") or  # PNG
                            raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a") or
                            raw.startswith(b"RIFF")  # WebP
                        )
                        img = raw if magic else b""
                    if img:
                        book.set_cover("cover.jpg", img)
                except Exception:  # noqa: BLE001
                    pass
            # 对话着色：在下载时把人物对话固化为带颜色的 span，写进 EPUB 文件本身。
            # 固定用浅色色板（深色字），因为绝大多数 EPUB 阅读器是白底，兼容性最好。
            # 这与阅读器的事后渲染开关是两回事：这里改的是落盘文件，任何阅读器打开都带色。
            if colorize_dialogue:
                try:
                    from .dialogue_highlight import highlight_text  # 包内相对导入
                except ImportError:  # 插件以顶层模块加载时的兜底
                    from gui.dialogue_highlight import highlight_text
            # 内嵌排版 CSS（严格只动标题字体/颜色，正文保持阅读器默认）：
            # - 正文 p / body 不设 font-family、不设字号、不设颜色 → 完全不变
            # - 章节标题：字号与正文相同(1em)、居中、用所选章节字体 + 章名高亮色
            # - 分卷标题：用所选分卷字体 + 卷名高亮色（与章名不同色），同样居中、同字号
            # 默认：章名=马善政毛笔楷书，卷名=钟齐志莽行书（均内置嵌入）。
            # font_scheme 为旧版单配置，兼容地作为章节字体覆盖。
            chapter_cfg = chapter_font or font_scheme or "builtin:mashan"
            chapter_family, chapter_at = resolve_font(
                chapter_cfg, book, epub, "Chapter", "EmbeddedChapterFont")
            # 分卷字体未指定时：默认志莽行书；章节为预设时取不同预设
            if volume_font:
                vol_cfg = volume_font
            elif chapter_cfg in EPUB_FONT_SCHEMES:
                vol_cfg = _VOLUME_FONT_CHOICE.get(chapter_cfg, "hei")
            else:
                vol_cfg = "builtin:zhimang"
            volume_family, volume_at = resolve_font(
                vol_cfg, book, epub, "Volume", "EmbeddedVolumeFont")
            at_fonts = (chapter_at + "\n" + volume_at + "\n") if (chapter_at or volume_at) else ""
            chapter_color = _safe_color(chapter_color, CHAPTER_TITLE_COLOR)
            volume_color = _safe_color(volume_color, VOLUME_TITLE_COLOR)
            # 内联标题色：层级 EPUB 的 h3 章名在微信读书里会被当目录标题
            # 用默认黑色覆盖外部 CSS 类，故把颜色直接内联到元素上（优先级最高），
            # 保证「有卷名/无卷名」「章名/卷名」「三源」任何组合都正确上色。
            chapter_inline = title_inline_style(chapter_color)
            volume_inline = title_inline_style(volume_color)
            style_css = (
                at_fonts
                + "body { font-size: 1em; line-height: 1.8; margin: 0 5%%; }\n"
                # 防御：段落/旧版 titlecss 不允许背景或边框，从底层杜绝绿色框框回归；
                # 标题(h2/卷名)只禁边框，背景留给渐变文字(background-clip:text)使用。
                "p, div.titlecss, .titlecss { "
                "background: transparent !important; border: none !important; }\n"
                "h2.chapter-title, h3.chapter-title, span.volume-name, "
                "h1.volume-name, h2.volume-name { border: none !important; }\n"
                "h2.chapter-title, h3.chapter-title { font-family: %s; font-size: 1em; "
                "font-weight: normal; text-align: center; margin: 1.6em 0 1.2em; "
                "letter-spacing: 0.08em; }\n"
                "span.volume-name, h1.volume-name, h2.volume-name { font-family: %s; }\n"
                "h1.volume-name { font-size: 1.1em; font-weight: normal; text-align: center; "
                "margin: 2em 0 0.4em; letter-spacing: 0.08em; }\n"
                "h2.volume-name { font-size: 1em; font-weight: normal; text-align: center; "
                "margin: 1.2em 0 0.4em; letter-spacing: 0.06em; }\n"
                "div.volume-line { text-align: center; margin: 0.5em 0 0; line-height: 1.4; }\n"
                "p { text-indent: 2em; text-align: justify; margin: 0 0 0.6em; }\n"
            ) % (chapter_family, volume_family)
            # 标题颜色：纯色直接写 color；渐变写 @supports 增强 + 纯色降级
            style_css += title_color_css("h2.chapter-title", chapter_color)
            style_css += title_color_css("h3.chapter-title", chapter_color)
            style_css += title_color_css("span.volume-name", volume_color)
            style_css += title_color_css("h1.volume-name", volume_color)
            style_css += title_color_css("h2.volume-name", volume_color)

            # ---- 章首题图：准备原神资源库与预处理后的图片 ----
            try:
                from . import chapter_hero as _ch
            except ImportError:  # 顶层模块加载兜底
                import gui.chapter_hero as _ch

            def _prepare_art(enabled: bool, library: str, mode: str, fixed: str,
                             uid_prefix: str, smart: bool = False) -> tuple:
                """准备题图/尾图的 (item_map, pick_fn, order, css)。失败不影响下载。"""
                item_map: dict = {}
                if not enabled:
                    return item_map, None, [], "", "rotate", ""
                is_tail = (library == _ch.TAIL_LIBRARY)
                css = _ch.tail_css() if is_tail else _ch.hero_css()
                try:
                    all_arts = _ch.available_heroes(library)
                    if smart:
                        # 题图=7神明智能池；尾图=地理志按地区均衡池
                        arts = (_ch.smart_tail_pool(book_seed, all_arts) if is_tail
                                else _ch.smart_hero_pool(book_seed, all_arts))
                        pick_mode, pick_fixed = "rotate", ""  # 在智能池内按章轮换
                    else:
                        arts = all_arts
                        pick_mode, pick_fixed = mode, fixed
                    by_key = {a["key"]: a for a in arts}
                    # 只统计实际章节会用到的 key，避免把整库图片塞进 EPUB
                    needed: set = set()
                    for ci in range(len(catalog)):
                        picked = _ch.pick_hero(ci, arts, mode=pick_mode, fixed=pick_fixed)
                        if picked:
                            needed.add(picked["key"])
                    root = _ch.hero_dir(library)
                    for key in needed:
                        a = by_key.get(key)
                        if not a:
                            continue
                        src = os.path.join(root, a["file"])
                        if not os.path.isfile(src):
                            continue
                        png_bytes = _ch.make_faded_png(src, library=library)
                        img_name = "%s_%s.png" % (uid_prefix, key)
                        item = epub.EpubItem(
                            uid="%s_%s" % (uid_prefix, key),
                            file_name="Images/%s" % img_name,
                            media_type="image/png", content=png_bytes,
                        )
                        book.add_item(item)
                        item_map[key] = item
                    return item_map, _ch.pick_hero, arts, css, pick_mode, pick_fixed
                except Exception:  # noqa: BLE001
                    return {}, None, [], "", "rotate", ""

            (hero_item_map, _pick_hero, _hero_order, _hero_css,
             hero_pick_mode, hero_pick_fixed) = _prepare_art(
                chapter_hero, _ch.HERO_LIBRARY, hero_mode, hero_fixed, "hero",
                smart=bool(smart_art))
            style_css += "\n" + _hero_css
            # 题图准备完毕就推一次进度：这一阶段要逐张烘焙渐隐（原实现单张 1.5s，
            # 100 张尾图可达 150s+），发生在章节下载之前。提前上报让进度条动起来，
            # 避免用户误以为卡在 2%。提速后这段通常只需数秒。
            report(3)

            # ---- 章末尾图：准备原神地理志图库（横屏风景，顶部渐隐） ----
            (tail_item_map, _pick_tail, _tail_order, _tail_css,
             tail_pick_mode, tail_pick_fixed) = _prepare_art(
                chapter_tail, _ch.TAIL_LIBRARY, tail_mode, tail_fixed, "tail",
                smart=bool(smart_art))
            style_css += "\n" + _tail_css
            report(4)

            css_item = epub.EpubItem(
                uid="layout_style", file_name="style.css",
                media_type="text/css", content=style_css,
            )
            book.add_item(css_item)
            epub_chapters = []
            toc_pairs = []  # (章节dict, EPUB条目)：供嵌套目录按卷/集分组
            # 层级信息仅在目录整体带卷/集时启用；纯章目录维持原扁平渲染（零回归）
            try:
                from .hierarchy import build_nested_toc
                _toc_builder = build_nested_toc
            except Exception:  # noqa: BLE001
                _toc_builder = None
            for i, ch in enumerate(catalog):
                raw = _chapter_text(i, ch)
                if not raw:
                    # 失败章节也推进度，避免进度条卡在某一档让用户误以为死锁
                    report(40 + int((i + 1) / len(catalog) * 60))
                    continue
                if colorize_dialogue:
                    # highlight_text 内部已做 HTML 转义并生成 <br/>，直接可用
                    body_html = highlight_text(raw, dark=False)
                else:
                    body_html = html.escape(raw).replace("\n", "<br/>")
                safe_title = ch["title"]
                vol_name = str(ch.get("vol") or "").strip() if _hierarchy else ""
                sub_name = str(ch.get("sub") or "").strip() if _hierarchy else ""
                if vol_name or sub_name:
                    # 层级模式：卷名 h1、集名 h2、章名 h3，微信读书可由标题再识别一层。
                    # 首章正文内重复出现卷/集名做视觉分组；后续同组章节不再重复渲染。
                    prev = toc_pairs[-1][0] if toc_pairs else {}
                    show_vol = vol_name and vol_name != str(prev.get("vol") or "").strip()
                    show_sub = sub_name and (sub_name != str(prev.get("sub") or "").strip()
                                             or show_vol)
                    # 微信读书等精简内核会把 h 标题当目录项用自己的黑色样式覆盖
                    # 外部 CSS 类，故卷/集/章三级统一「h 元素内联 + 内层 span 内联」
                    # 双保险，确保任何书（有无卷、有无集）三级标题都正确上色。
                    parts = []
                    if show_vol:
                        parts.append(
                            f'<h1 class="volume-name"{volume_inline}>'
                            f'<span{volume_inline}>{html.escape(vol_name)}</span></h1>'
                        )
                    if show_sub:
                        parts.append(
                            f'<h2 class="volume-name"{volume_inline}>'
                            f'<span{volume_inline}>{html.escape(sub_name)}</span></h2>'
                        )
                    parts.append(
                        f'<h3 class="chapter-title"{chapter_inline}>'
                        f'<span{chapter_inline}>{html.escape(safe_title)}</span></h3>'
                    )
                    title_html = "".join(parts)
                else:
                    # 无分卷：沿用原逻辑（含从标题里拆「第X卷 …」的旧行为）
                    vol_name, ch_name = split_volume_title(safe_title)
                    if vol_name:
                        title_html = (
                            f'<div class="volume-line">'
                            f'<span class="volume-name"{volume_inline}>'
                            f'{html.escape(vol_name)}</span>'
                            f'</div>'
                            f'<h2 class="chapter-title"{chapter_inline}>'
                            f'<span{chapter_inline}>{html.escape(ch_name)}</span></h2>'
                        )
                    else:
                        title_html = (
                            f'<h2 class="chapter-title"{chapter_inline}>'
                            f'<span{chapter_inline}>{html.escape(safe_title)}</span></h2>'
                        )

                # 章首题图：在标题上方插入图片（渐隐已烘焙进 PNG）
                hero_html = ""
                if hero_item_map and _pick_hero is not None:
                    picked = _pick_hero(i, _hero_order, mode=hero_pick_mode, fixed=hero_pick_fixed)
                    if picked and picked["key"] in hero_item_map:
                        img = hero_item_map[picked["key"]]
                        hero_html = (
                            '<div class="chapter-hero">'
                            f'<img src="{img.file_name}" alt=""/>'
                            '</div>'
                        )

                # 章末尾图：在正文之后插入地理志风景图（顶部渐隐已烘焙进 PNG）
                tail_html = ""
                if tail_item_map and _pick_tail is not None:
                    picked_t = _pick_tail(i, _tail_order, mode=tail_pick_mode, fixed=tail_pick_fixed)
                    if picked_t and picked_t["key"] in tail_item_map:
                        img_t = tail_item_map[picked_t["key"]]
                        tail_html = (
                            '<div class="chapter-tail">'
                            f'<img src="{img_t.file_name}" alt=""/>'
                            '</div>'
                        )

                c = epub.EpubHtml(title=ch["title"], file_name=f"ch_{i + 1:05d}.xhtml", lang="zh-CN")
                c.add_item(css_item)  # 让本章引用排版 CSS
                c.content = (
                    f"{hero_html}{title_html}<p>{body_html}</p>{tail_html}"
                )
                book.add_item(c)
                epub_chapters.append(c)
                toc_pairs.append((ch, c))
                chapters_written += 1
                report(40 + int((i + 1) / len(catalog) * 60))
            if _hierarchy and _toc_builder is not None:
                # 三级目录：卷 → 集 → 章（自动适配实际层数）
                book.toc = _toc_builder(toc_pairs, epub)
            else:
                book.toc = tuple(epub_chapters)
            book.add_item(epub.EpubNcx())
            book.add_item(epub.EpubNav())
            book.spine = ["nav"] + epub_chapters
            result_path = os.path.join(save_path, f"{out_name}.epub")
            # 先写 .part 再原子替换：取消/异常时不留下半截 EPUB 被当成成品
            _tmp = result_path + ".part"
            try:
                epub.write_epub(_tmp, book)
            except Exception:
                _discard_partial(_tmp)
                raise
            if chapters_written == 0:
                _discard_partial(_tmp)
            else:
                os.replace(_tmp, result_path)

        else:
            # 单 txt 文件：按顺序写入所有章节（同样先写 .part 再替换）
            result_path = os.path.join(save_path, f"{out_name}.txt")
            _tmp = result_path + ".part"
            try:
                # utf-8-sig（带 BOM）：微信读书等阅读器对纯 UTF-8 无 BOM 的中文
                # TXT 偶发误判为 GBK 导致乱码，BOM 是跨阅读器的标准兼容写法。
                # 软件自身读取端已用 utf-8-sig（向下兼容无 BOM 文件），零影响。
                with open(_tmp, "w", encoding="utf-8-sig") as f:
                    f.write(f"名称：{title}\n")
                    if detail.get("author"):
                        f.write(f"作者：{detail['author']}\n")
                    f.write(f"共 {len(catalog)} 章\n")
                    f.write("=" * 40 + "\n\n")
                    try:
                        from .hierarchy import txt_heading_lines
                    except Exception:  # noqa: BLE001
                        txt_heading_lines = None
                    prev_ch = None
                    for i, ch in enumerate(catalog):
                        text = _chapter_text(i, ch)
                        if not text:
                            # 失败章节也推进度，避免进度条卡在某一档让用户误以为死锁
                            report(40 + int((i + 1) / len(catalog) * 60))
                            continue
                        heads = ""
                        if _hierarchy and txt_heading_lines is not None:
                            heads = txt_heading_lines(catalog, i, prev_ch)
                        f.write(f"{heads}\n\n{ch['title']}\n\n{text}\n")
                        prev_ch = ch
                        chapters_written += 1
                        report(40 + int((i + 1) / len(catalog) * 60))
            except Exception:
                _discard_partial(_tmp)
                raise
            if chapters_written == 0:
                _discard_partial(_tmp)
            else:
                os.replace(_tmp, result_path)

        if chapters_written == 0:
            raise QimaoError("没有任何章节成功写入（章节ID与缓存文件不匹配）")
        report(100)

    return {
        "file_path": result_path,
        "title": title,
        "chapters": chapters_written,
        "total": len(catalog),
        "format": fmt,
        "total_chapters": total_chapters,
        "start_chapter": effective_start,
        "end_chapter": effective_end,
    }

