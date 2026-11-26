# -*- coding: utf-8 -*-
"""番茄官网正文读取 + 字体反爬解码（上游正文 API 失效后的可靠 fallback）。

映射表来源：
  - https://github.com/zhoulianglen/fanqiexiaoshuo-Download （FONT_MAP 原样收录）
  - 该表源自 https://github.com/romcere/fanqienovel-decryptor / RChow 博客
逻辑按本项目风格重写，作为 GUI 层内置核心，与 qimao_core.py 平级。

原理：番茄把常用汉字替换为 PUA 私用区字符；页面 CSS 里的 woff2 字体
把 PUA 码点映射到 glyph（如 gid58611），再用本映射表还原为真实字符。
"""

from __future__ import annotations

import html as _html
import io
import json
import re
import time as _time
from functools import lru_cache
from typing import Dict

import random
import threading

import requests

# 探测 brotli 解码能力：PyInstaller 打包环境可能未包含 brotli 库，
# 若声明 Accept-Encoding: br 但无法解码，拿到的是压缩乱码。
# 动态探测（brotli / brotlicffi / urllib3 自带解码器三者任一可用即可），
# 不可用则只声明 gzip/deflate，避免 exe 环境踩坑。
def _has_brotli() -> bool:
    for mod in ("brotli", "brotlicffi"):
        try:
            __import__(mod)
            return True
        except ImportError:
            pass
    try:
        from urllib3.response import BrotliDecoder  # noqa: F401
        return True
    except ImportError:
        return False


_ACCEPT_ENCODING = "gzip, deflate, br" if _has_brotli() else "gzip, deflate"

# 真实浏览器请求头：番茄对只有 UA 的极简请求会返回验证/拦截页
# （该页面不含 __INITIAL_STATE__，正是"读者页缺少数据"报错的根因）。
_UA_POOL = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
)

_HEADERS = {
    "User-Agent": _UA_POOL[0],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": _ACCEPT_ENCODING,
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    # 真实 Chrome 的 Client Hints：番茄风控会据此识别脚本请求。
    # 实测并发高频下，带完整 CH + Cookie + Referer 的风控率约 10%，
    # 仅 UA 的极简头约 20%——减半。这些字段对正常阅读无副作用。
    "sec-ch-ua": '"Chromium";v="121", "Not A(Brand";v="99", "Google Chrome";v="121"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

# 每线程独立 Session：复用 TCP 连接与 cookie，同时避免并发共享 Session 的线程安全问题。
# 并发预取章节时每个工作线程持有自己的连接，互不干扰。
_TLS = threading.local()


def _get_session() -> requests.Session:
    session = getattr(_TLS, "session", None)
    if session is None:
        session = requests.Session()
        _TLS.session = session
    return session


def _browser_headers(url: str = "", ua: str = "") -> dict:
    """构造完整浏览器头，带每线程固定的 novel_web_id cookie 与 Referer。

    Cookie 按线程固定（与 session 生命周期一致），模拟真实浏览器"同一访客连续
    浏览多个页面"，比每次随机 cookie 更像真人，实测更抗风控。
    """
    h = dict(_HEADERS)
    if ua:
        h["User-Agent"] = ua
    # 每线程固定一个 novel_web_id
    cookie = getattr(_TLS, "cookie", None)
    if cookie is None:
        cookie = f"novel_web_id={random.randint(10**18, 10**19 - 1)}"
        _TLS.cookie = cookie
    h["Cookie"] = cookie
    # Referer：模拟从书籍页点进读者页/字体
    if url:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        h["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
    return h


INITIAL_STATE_MARKER = "window.__INITIAL_STATE__="

FONT_MAP = {
    '58670': '0', '58413': '1', '58678': '2', '58371': '3', '58353': '4',
    '58480': '5', '58359': '6', '58449': '7', '58540': '8', '58692': '9',
    '58712': 'a', '58542': 'b', '58575': 'c', '58626': 'd', '58691': 'e',
    '58561': 'f', '58362': 'g', '58619': 'h', '58430': 'i', '58531': 'j',
    '58588': 'k', '58440': 'l', '58681': 'm', '58631': 'n', '58376': 'o',
    '58429': 'p', '58555': 'q', '58498': 'r', '58518': 's', '58453': 't',
    '58397': 'u', '58356': 'v', '58435': 'w', '58514': 'x', '58482': 'y',
    '58529': 'z', '58515': 'A', '58688': 'B', '58709': 'C', '58344': 'D',
    '58656': 'E', '58381': 'F', '58576': 'G', '58516': 'H', '58463': 'I',
    '58649': 'J', '58571': 'K', '58558': 'L', '58433': 'M', '58517': 'N',
    '58387': 'O', '58687': 'P', '58537': 'Q', '58541': 'R', '58458': 'S',
    '58390': 'T', '58466': 'U', '58386': 'V', '58697': 'W', '58519': 'X',
    '58511': 'Y', '58634': 'Z', '58611': '的', '58590': '一', '58398': '是',
    '58422': '了', '58657': '我', '58666': '不', '58562': '人', '58345': '在',
    '58510': '他', '58496': '有', '58654': '这', '58441': '个', '58493': '上',
    '58714': '们', '58618': '来', '58528': '到', '58620': '时', '58403': '大',
    '58461': '地', '58481': '为', '58700': '子', '58708': '中', '58503': '你',
    '58442': '说', '58639': '生', '58506': '国', '58663': '年', '58436': '着',
    '58563': '就', '58391': '那', '58357': '和', '58354': '要', '58695': '她',
    '58372': '出', '58696': '也', '58551': '得', '58445': '里', '58408': '后',
    '58599': '自', '58424': '以', '58394': '会', '58348': '家', '58426': '可',
    '58673': '下', '58417': '而', '58556': '过', '58603': '天', '58565': '去',
    '58604': '能', '58522': '对', '58632': '小', '58622': '多', '58350': '然',
    '58605': '于', '58617': '心', '58401': '学', '58637': '么', '58684': '之',
    '58382': '都', '58464': '好', '58487': '看', '58693': '起', '58608': '发',
    '58392': '当', '58474': '没', '58601': '成', '58355': '只', '58573': '如',
    '58499': '事', '58469': '把', '58361': '还', '58698': '用', '58489': '第',
    '58711': '样', '58457': '道', '58635': '想', '58492': '作', '58647': '种',
    '58623': '开', '58521': '美', '58609': '总', '58530': '从', '58665': '无',
    '58652': '情', '58676': '己', '58456': '面', '58581': '最', '58509': '女',
    '58488': '但', '58363': '现', '58685': '前', '58396': '些', '58523': '所',
    '58471': '同', '58485': '日', '58613': '手', '58533': '又', '58589': '行',
    '58527': '意', '58593': '动', '58699': '方', '58707': '期', '58414': '它',
    '58596': '头', '58570': '经', '58660': '长', '58364': '儿', '58526': '回',
    '58501': '位', '58638': '分', '58404': '爱', '58677': '老', '58535': '因',
    '58629': '很', '58577': '给', '58606': '名', '58497': '法', '58662': '间',
    '58479': '斯', '58532': '知', '58380': '世', '58385': '什', '58405': '两',
    '58644': '次', '58578': '使', '58505': '身', '58564': '者', '58412': '被',
    '58686': '高', '58624': '已', '58667': '亲', '58607': '其', '58616': '进',
    '58368': '此', '58427': '话', '58423': '常', '58633': '与', '58525': '活',
    '58543': '正', '58418': '感', '58597': '见', '58683': '明', '58507': '问',
    '58621': '力', '58703': '理', '58438': '尔', '58536': '点', '58384': '文',
    '58484': '几', '58539': '定', '58554': '本', '58421': '公', '58347': '特',
    '58569': '做', '58710': '外', '58574': '孩', '58375': '相', '58645': '西',
    '58592': '果', '58572': '走', '58388': '将', '58370': '月', '58399': '十',
    '58651': '实', '58546': '向', '58504': '声', '58419': '车', '58407': '全',
    '58672': '信', '58675': '重', '58538': '三', '58465': '机', '58374': '工',
    '58579': '物', '58402': '气', '58702': '每', '58553': '并', '58360': '别',
    '58389': '真', '58560': '打', '58690': '太', '58473': '新', '58512': '比',
    '58653': '才', '58704': '便', '58545': '夫', '58641': '再', '58475': '书',
    '58583': '部', '58472': '水', '58478': '像', '58664': '眼', '58586': '等',
    '58568': '体', '58674': '却', '58490': '加', '58476': '电', '58346': '主',
    '58630': '界', '58595': '门', '58502': '利', '58713': '海', '58587': '受',
    '58548': '听', '58351': '表', '58547': '德', '58443': '少', '58460': '克',
    '58636': '代', '58585': '员', '58625': '许', '58694': '稜', '58428': '先',
    '58640': '口', '58628': '由', '58612': '死', '58446': '安', '58468': '写',
    '58410': '性', '58508': '马', '58594': '光', '58483': '白', '58544': '或',
    '58495': '住', '58450': '难', '58643': '望', '58486': '教', '58406': '命',
    '58447': '花', '58669': '结', '58415': '乐', '58444': '色', '58549': '更',
    '58494': '拉', '58409': '东', '58658': '神', '58557': '记', '58602': '处',
    '58559': '让', '58610': '母', '58513': '父', '58500': '应', '58378': '直',
    '58680': '字', '58352': '场', '58383': '平', '58454': '报', '58671': '友',
    '58668': '关', '58452': '放', '58627': '至', '58400': '张', '58455': '认',
    '58416': '接', '58552': '告', '58614': '入', '58582': '笑', '58534': '内',
    '58701': '英', '58349': '军', '58491': '候', '58467': '民', '58365': '岁',
    '58598': '往', '58425': '何', '58462': '度', '58420': '山', '58661': '觉',
    '58615': '路', '58648': '带', '58470': '万', '58377': '男', '58520': '边',
    '58646': '风', '58600': '解', '58431': '叫', '58715': '任', '58524': '金',
    '58439': '快', '58566': '原', '58477': '吃', '58642': '妈', '58437': '变',
    '58411': '通', '58451': '师', '58395': '立', '58369': '象', '58706': '数',
    '58705': '四', '58379': '失', '58567': '满', '58373': '战', '58448': '远',
    '58659': '格', '58434': '士', '58679': '音', '58432': '轻', '58689': '目',
    '58591': '条', '58682': '呢',
}



@lru_cache(maxsize=8)
def _font_mapping(font_url: str) -> Dict[str, str]:
    """按字体 URL 缓存 PUA 字符 → 真实字符映射（同一字体只下载一次）。"""
    try:
        from fontTools.ttLib import TTFont
    except ImportError:  # pragma: no cover - 打包时由 requirements 保证
        return {}
    try:
        data = requests.get(font_url, headers=_browser_headers(font_url), timeout=20).content
        font = TTFont(io.BytesIO(data))
        cmap = font.getBestCmap() or {}
    except Exception:  # noqa: BLE001
        return {}
    result: Dict[str, str] = {}
    for cp, glyph in cmap.items():
        gid = str(glyph).replace("gid", "")
        if gid in FONT_MAP:
            result[chr(cp)] = FONT_MAP[gid]
    return result


def _initial_state(page: str) -> dict:
    pos = page.find(INITIAL_STATE_MARKER)
    if pos < 0:
        raise ValueError("读者页缺少 __INITIAL_STATE__ 数据")
    raw = page[pos + len(INITIAL_STATE_MARKER):]
    # 番茄新版页面在 __INITIAL_STATE__ 里塞了 JS 字面量 undefined
    # （实测 "_config":{"libra":undefined,...}），标准 JSON 无法解析。
    # 用正则把 :undefined / ,undefined 替换为 null（仅匹配值位置，不碰字符串内容）。
    if "undefined" in raw:
        raw = re.sub(r'(?<=[:\[,])\s*undefined\b', ' null', raw)
    state, _ = json.JSONDecoder().raw_decode(raw)
    return state


# 单章抓正文的总时间预算（秒）：超时/风控时绝不让一章拖死整本书。
# 用户实测曾卡在 85% 十分钟，根因就是旧版重试 5×25s + 失效兜底接口叠加。
_PAGE_FETCH_BUDGET = 24.0


def fetch_page(url: str, retries: int = 4, timeout: int = 8) -> str:
    """请求番茄页面，确保拿到含 __INITIAL_STATE__ 的正常页面。

    番茄会对可疑请求返回验证/拦截页（HTML 里没有 __INITIAL_STATE__），
    这正是"读者页缺少数据"报错的来源。这里更换 User-Agent、带完整浏览器头
    重试拿正常页；但**总耗时受 _PAGE_FETCH_BUDGET 硬约束**——网络超时/风控
    时最多约 24 秒就放弃并抛错，绝不出现单章卡 2 分钟拖死整本书的情况。
    """
    deadline = _time.monotonic() + _PAGE_FETCH_BUDGET
    last_error = ""
    # 随机 UA 起点：并发预取时让不同线程错开 UA，降低被风控识别为脚本的概率
    ua_offset = random.randrange(len(_UA_POOL))
    session = _get_session()
    for attempt in range(retries):
        remaining = deadline - _time.monotonic()
        if remaining <= 0.5:
            break
        headers = _browser_headers(url, _UA_POOL[(ua_offset + attempt) % len(_UA_POOL)])
        try:
            resp = session.get(url, headers=headers, timeout=min(timeout, remaining))
            if resp.status_code == 200 and INITIAL_STATE_MARKER in resp.text:
                return resp.text
            last_error = f"HTTP {resp.status_code} / 页面不含数据 (len={len(resp.text)})"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        gap = min(0.6 * (attempt + 1), max(0.0, deadline - _time.monotonic()))
        if attempt < retries - 1 and gap > 0:
            _time.sleep(gap)
    raise ValueError(f"页面获取失败（已限时 {_PAGE_FETCH_BUDGET:.0f}s）：{last_error}")


@lru_cache(maxsize=128)
def fetch_cover(book_id: str, timeout: int = 15) -> str:
    """从番茄书籍页提取封面直链，供 EPUB 内嵌封面使用。

    上游 get_book_info() 只返回书名/作者/简介，封面藏在页面
    ``window.__INITIAL_STATE__.page.thumbUrl`` 里。该函数惰性调用、
    按书 ID 缓存，失败返回空串，不影响下载主链路。
    """
    bid = str(book_id or "").strip()
    if not bid:
        return ""
    try:
        page = (_initial_state(fetch_page(f"https://fanqienovel.com/page/{bid}", timeout=timeout)).get("page") or {})
        return str(page.get("thumbUrl") or page.get("thumbUri") or "")
    except Exception:  # noqa: BLE001
        return ""


# 番茄分享短链域名：/t/<token> 形态需联网跟随重定向还原为真实 bookId。
# 只对这些白名单域名发起请求，防止把任意 URL 当短链跟随（SSRF 风险）。
_SHORT_LINK_HOSTS = (
    "changdunovel.com", "www.changdunovel.com",
    "fanqienovel.com", "www.fanqienovel.com",
    "fqnovel.com", "www.fqnovel.com",
)
_SHORT_LINK_RE = re.compile(r"https?://([^/\s]+)/t/([A-Za-z0-9_-]+)/?", re.I)


@lru_cache(maxsize=64)
def resolve_short_link(url: str, timeout: int = 15) -> str:
    """把番茄 ``/t/<token>`` 分享短链还原为真实 bookId。

    番茄 App 分享出的短链（如 ``https://changdunovel.com/t/4NZeXnfTfY/``）
    里 token 是字母数字混合串，不是书籍 ID；服务端会 302 到带 ``book_id``
    或 ``/page/<id>`` 的地址。这里跟随重定向，从**最终 URL**（优先）和响应
    正文里提取数字 ID。仅跟随白名单域名，失败返回空串由上层提示。
    """
    match = _SHORT_LINK_RE.search(str(url or "").strip())
    if not match:
        return ""
    host = match.group(1).lower()
    if host not in _SHORT_LINK_HOSTS:
        return ""
    target = match.group(0)
    try:
        resp = requests.get(
            target, headers=_browser_headers(target), timeout=timeout, allow_redirects=True,
        )
        # 1) 最终 URL 里的 book_id=/page/<id>（最可靠）
        final_url = str(getattr(resp, "url", "") or target)
        m = re.search(r"[?&](?:book_id|bookId|item_id)=(\d{6,24})", final_url, re.I)
        if m:
            return m.group(1)
        m = re.search(r"/page/(\d{6,24})", final_url)
        if m:
            return m.group(1)
        # 2) 响应正文/中间页里的 book_id（部分渠道返回 HTML/JSON 跳转页）
        text = resp.text or ""
        m = re.search(r"[?&]?(?:book_id|bookId|bookID)[\"']?\s*[:=]\s*[\"']?(\d{6,24})", text, re.I)
        if m:
            return m.group(1)
        m = re.search(r"fanqienovel\.com/page/(\d{6,24})", text, re.I)
        if m:
            return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return ""


@lru_cache(maxsize=64)
def resolve_book_id(value: str, timeout: int = 20) -> str:
    """把番茄的 bookId / 章节 ID / 完整链接统一归一化为真实 bookId。

    番茄读者页 ``/reader/{chapterId}`` 里的 ID 是**章节 ID**，直接拼
    ``/page/{id}`` 会返回 404（书籍页只认 bookId）。归一化分两步：

      1. 先按 bookId 请求 ``/page/{id}``，``page.bookId`` 非空即命中；
      2. 未命中则按章节 ID 请求 ``/reader/{id}``，从
         ``reader.chapterData.bookId`` 取真实书籍 ID。

    无法归一化时原样返回，由上层给出明确报错。
    """
    value = str(value or "").strip()
    if not value:
        return ""
    # 0) /t/<token> 分享短链：先联网还原为真实 bookId（白名单域名）
    if _SHORT_LINK_RE.search(value):
        short_id = resolve_short_link(value, timeout=timeout)
        if short_id:
            return short_id
    if not value.isdigit():
        # 传入的是完整链接/口令文本，取第一个独立长数字
        match = re.search(r"(?<!\d)(\d{6,24})(?!\d)", value)
        if not match:
            return ""
        value = match.group(1)

    # 1) 当作 bookId：书籍页 __INITIAL_STATE__.page.bookId 非空即命中
    try:
        state = _initial_state(fetch_page(f"https://fanqienovel.com/page/{value}", timeout=timeout))
        page_book_id = str((state.get("page", {}) or {}).get("bookId", "") or "")
        if page_book_id.isdigit() and len(page_book_id) >= 6:
            return page_book_id
    except Exception:  # noqa: BLE001
        pass

    # 2) 当作章节 ID：读者页 __INITIAL_STATE__.reader.chapterData.bookId
    try:
        state = _initial_state(fetch_page(f"https://fanqienovel.com/reader/{value}", timeout=timeout))
        chapter = (state.get("reader", {}) or {}).get("chapterData", {}) or {}
        real_book_id = str(chapter.get("bookId", "") or "")
        if real_book_id.isdigit() and len(real_book_id) >= 6:
            return real_book_id
    except Exception:  # noqa: BLE001
        pass

    # 3) 无法归一化：原样返回，上层 get_book_info 会给出明确失败
    return value


def decode_chapter(item_id: str) -> str:
    """从番茄官网读者页读取正文并解密字体，返回纯文本。"""
    url = f"https://fanqienovel.com/reader/{item_id}"
    state = _initial_state(fetch_page(url))
    data = state.get("reader", {}).get("chapterData", {}) or {}

    content = str(data.get("content", "") or "")
    if not content:
        return ""

    css = str(state.get("common", {}).get("css", "") or "")
    match = re.search(r"url\((https://[^)]+\.woff2)\)", css)
    mapping = _font_mapping(match.group(1)) if match else {}

    # HTML → 纯文本，保留段落换行
    text = re.sub(r"<p[^>]*>", "\n", content)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = _html.unescape(text)
    if mapping:
        text = "".join(mapping.get(ch, ch) for ch in text)

    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    title = str(data.get("title", "") or "").strip()
    if title and lines and title.replace(" ", "") in lines[0].replace(" ", ""):
        lines = lines[1:]
    return "\n".join("    " + ln for ln in lines)
