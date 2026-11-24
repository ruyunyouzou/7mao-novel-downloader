# -*- coding: utf-8 -*-
"""
微信读书核心封装：只读同步（官方 Agent Gateway）+ 本地书导入（网页 Cookie 传书）。

两类能力、两套凭据，互不混用：

1. 书架 / 阅读进度同步 —— 官方 Agent Skill
   POST https://i.weread.qq.com/api/agent/gateway
   Header: Authorization: Bearer wrk-xxx
   Body:   {"api_name": "/shelf/sync", "skill_version": "1.0.6", ...}
   业务参数平铺，不能包在 params 里。

2. 本地书导入微信读书 —— 网页端传书（Cookie）
   GET  https://i.weread.qq.com/cos/getcredential?name=<书名>&from=
   PUT  https://<bucket>.cos.accelerate.myqcloud.com/<ObjectName>
        （COS v5 签名，仅签 host；x-cos-security-token；先零字节预热再流式上传；CRC64 校验）
   POST https://i.weread.qq.com/cos/notify?name=<文件名>&path=<ObjectName>&cancel=0
   注意：/cos/notify 不可重放；成功仅代表已提交解析，入架由服务端异步完成。

参考：微信读书官方 weread-skills v1.0.6、OlibTauri weread/import.rs 实现。
仅供个人学习与自用同步，请遵守微信读书服务条款。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from .weread_login import VERSION_HEADERS, WeReadMobileSession

# ---------------------------------------------------------------- 常量

GATEWAY_URL = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.6"
I_WEREAD = "https://i.weread.qq.com"
SUPPORTED_FORMATS = (".epub", ".pdf", ".txt", ".mobi", ".azw3")
MAX_SIZE_MB = 200

UA_WEB = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class WeReadError(Exception):
    """微信读书接口错误。"""


# ---------------------------------------------------------------- 工具

def parse_cookie(cookie_str: str) -> Dict[str, str]:
    """把浏览器复制的整段 Cookie 解析为字典。"""
    result: Dict[str, str] = {}
    for part in (cookie_str or "").split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k:
            result[k] = v
    return result


def cookie_is_valid(cookies: Dict[str, str]) -> bool:
    """ Cookie 至少需要 wr_vid + wr_skey。"""
    return bool(cookies.get("wr_vid") and cookies.get("wr_skey"))


# ---------------------------------------------------------------- CRC64

# 生成 CRC64/XZ 查表（COS x-cos-hash-crc64ecma 使用该多项式）
_CRC64_POLY = 0xC96C5795D7870F42
_CRC64_TABLE: Optional[List[int]] = None


def _crc64_table() -> List[int]:
    global _CRC64_TABLE
    if _CRC64_TABLE is not None:
        return _CRC64_TABLE
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ (_CRC64_POLY if crc & 1 else 0)
        table.append(crc)
    _CRC64_TABLE = table
    return table


def crc64(data: bytes, crc: int = 0) -> int:
    """增量 CRC64/XZ 计算。"""
    table = _crc64_table()
    crc ^= 0xFFFFFFFFFFFFFFFF
    for b in data:
        crc = table[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFFFFFFFFFF


# ---------------------------------------------------------------- COS v5 签名

def cos_authorization(
    secret_id: str, secret_key: str, object_name: str,
    host: str, expired_time: int,
) -> Tuple[str, int, int]:
    """生成 COS v5 签名（仅签 host），返回 (Authorization, start, end)。"""
    now = int(time.time())
    start = max(0, now - 60)
    end = min(now + 3600, int(expired_time) - 1)
    if end <= start:
        end = start + 3600
    key_time = f"{start};{end}"

    sign_key = hmac.new(
        secret_key.encode("utf-8"), key_time.encode("utf-8"), hashlib.sha1
    ).hexdigest()

    # 对象名按段 URI 编码，保证以 / 开头
    uri = "/" + "/".join(
        urllib.parse.quote(seg, safe="-_.!~*'()") for seg in object_name.split("/") if seg != ""
    )
    http_string = f"put\n{uri}\n\nhost={host}\n"
    http_str_sha1 = hashlib.sha1(http_string.encode("utf-8")).hexdigest()
    string_to_sign = f"sha1\n{key_time}\n{http_str_sha1}\n"
    signature = hmac.new(
        sign_key.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1
    ).hexdigest()

    auth = (
        "q-sign-algorithm=sha1"
        f"&q-ak={secret_id}"
        f"&q-sign-time={key_time}"
        f"&q-key-time={key_time}"
        "&q-header-list=host"
        "&q-url-param-list="
        f"&q-signature={signature}"
    )
    return auth, start, end


# ---------------------------------------------------------------- 只读同步客户端

class WeReadClient:
    """官方 Agent Gateway 客户端（只读：书架 / 进度 / 书籍信息）。"""

    def __init__(self, api_key: str, timeout: int = 20) -> None:
        self.api_key = (api_key or "").strip()
        self.timeout = timeout

    def call(self, api_name: str, **params: Any) -> Dict[str, Any]:
        if not self.api_key:
            raise WeReadError("尚未配置微信读书 API Key（wrk- 开头）")
        body: Dict[str, Any] = {
            "api_name": api_name,
            "skill_version": SKILL_VERSION,
        }
        body.update(params)
        try:
            resp = requests.post(
                GATEWAY_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "qimao-gui/1.0 weread-skills/1.0.6",
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise WeReadError(f"请求失败：{exc}") from exc

        err = data.get("errcode", data.get("errCode", 0))
        try:
            err_int = int(err)
        except (TypeError, ValueError):
            err_int = 0
        if err_int != 0:
            raise WeReadError(str(data.get("errmsg") or data.get("errMsg") or f"错误码 {err}"))
        return data

    def get_shelf(self) -> Dict[str, Any]:
        """同步书架，返回 {'books': [...], 'albums': [...], 'total': n}。"""
        data = self.call("/shelf/sync")
        books = data.get("books") or []
        albums = data.get("albums") or []
        mp = data.get("mp")
        total = len(books) + len(albums) + (1 if mp else 0)
        return {"books": books, "albums": albums, "total": total, "raw": data}

    def get_progress(self, book_id: str) -> Dict[str, Any]:
        """获取单本书阅读进度。progress 为 0-100 整数。"""
        data = self.call("/book/getprogress", bookId=book_id)
        book = data.get("book") or {}
        return {
            "bookId": book_id,
            "progress": int(book.get("progress") or 0),
            "chapterUid": book.get("chapterUid", ""),
            "chapterOffset": book.get("chapterOffset", 0),
            "updateTime": book.get("updateTime", 0),
            "readingTime": int(book.get("recordReadingTime") or 0),
            "finishTime": book.get("finishTime"),
        }

    def get_book_info(self, book_id: str) -> Dict[str, Any]:
        return self.call("/book/info", bookId=book_id)

    # ------------------------------------------------------------ 阅读数据仪表盘

    def get_readdata(self, mode: str = "overall", base_time: int = 0) -> Dict[str, Any]:
        """阅读统计详情。mode: weekly/monthly/annually/overall。

        注意：所有时长字段单位为秒（totalReadTime / dayAverageReadTime /
        readTimes 的 value / preferCategory[].readingTime / preferTime[]）。
        """
        return self.call("/readdata/detail", mode=mode, baseTime=base_time)

    def get_notebooks(self) -> Dict[str, Any]:
        """笔记本概览（所有有笔记的书）。noteCount=划线数，reviewCount=想法数。"""
        data = self.call("/user/notebooks")
        books = data.get("books") or []
        total = int(data.get("totalNoteCount") or 0)
        if not total:
            total = sum(
                int(b.get("reviewCount") or 0)
                + int(b.get("noteCount") or 0)
                + int(b.get("bookmarkCount") or 0)
                for b in books
            )
        return {"books": books, "total": total, "raw": data}

    def get_notes(self, book_id: str) -> List[Dict[str, Any]]:
        """单本书的个人想法/点评（注意接口参数是小写 bookid）。"""
        data = self.call("/review/list/mine", bookid=book_id, count=50)
        result: List[Dict[str, Any]] = []
        for item in (data.get("reviews") or []):
            rv = (item.get("review") or {}) if isinstance(item, dict) else {}
            content = str(rv.get("content") or "").strip()
            if not content:
                continue
            result.append({
                "content": content,
                "abstract": str(rv.get("abstract") or ""),
                "chapter": str(rv.get("chapterName") or ""),
                "time": int(rv.get("createTime") or 0),
                "star": int(rv.get("star") or 0),
            })
        return result

    def get_highlights(self, book_id: str) -> List[Dict[str, Any]]:
        """单本书的划线内容（已自动过滤书签）。"""
        data = self.call("/book/bookmarklist", bookId=book_id)
        items = data.get("updated") or []
        chapters = {str(c.get("chapterUid")): c.get("title", "") for c in (data.get("chapters") or [])}
        result: List[Dict[str, Any]] = []
        for it in items:
            text = str(it.get("markText") or "").strip()
            if not text:
                continue
            result.append({
                "text": text,
                "book_id": str(it.get("bookId") or book_id),
                "chapter": chapters.get(str(it.get("chapterUid")), ""),
                "time": int(it.get("createTime") or 0),
            })
        book = data.get("book") or {}
        if book:
            for item in result:
                item.setdefault("title", str(book.get("title") or ""))
                item.setdefault("author", str(book.get("author") or ""))
        return result


# ---------------------------------------------------------------- 仪表盘数据聚合

def build_dashboard(client: WeReadClient) -> Dict[str, Any]:
    """一次拉取并整理仪表盘所需数据。任何子接口失败都不影响其它模块。

    返回结构：
    {
      'greeting': str, 'continue': dict|None,
      'today_min': int, 'goal_min': int, 'week_bars': [(label, minutes)],
      'week_total_min', 'week_days',
      'stats': [(label, value, icon)],
      'prefer_titles': [str], 'prefer_categories': [(name, hours, weight)],
      'prefer_time': [(hour_label, seconds)],
      'highlights': [(text, title, author)], 'note_count': int,
      'note_book_count': int, 'note_top': [(title, count)],
      'cover_books': [(title, cover_url)], 'note_month': [(label, count)],
      'errors': [str],
    }
    """
    result: Dict[str, Any] = {
        "continue": None, "today_min": 0, "goal_min": 0,
        "week_bars": [], "week_total_min": 0, "week_days": 0,
        "stats": [], "prefer_titles": [], "prefer_categories": [],
        "prefer_time": [], "highlights": [], "note_count": 0,
        "note_book_count": 0, "note_top": [],
        "cover_books": [], "note_month": [],
        "notebook_books": [],  # 完整笔记本列表，供「书籍笔记」页实时联动
        "errors": [],
    }

    def safe(fn, label):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"{label}：{exc}")
            return None

    # --- 周 / 总阅读统计 ---
    week = safe(lambda: client.get_readdata("weekly"), "本周阅读数据")
    overall = safe(lambda: client.get_readdata("overall"), "累计阅读数据") or {}

    if isinstance(week, dict):
        total = int(week.get("totalReadTime") or 0)
        result["week_total_min"] = total // 60
        result["week_days"] = int(week.get("readDays") or 0)
        times = week.get("readTimes") or {}
        # 按周一到周日排列
        bars = []
        for i, label in enumerate(["一", "二", "三", "四", "五", "六", "日"]):
            day_min = 0
            for key, val in times.items():
                try:
                    d = time.localtime(int(key))
                    if time.strftime("%w", d) == str((i + 1) % 7):
                        day_min += int(val) // 60
                except (ValueError, TypeError, OSError):
                    continue
            bars.append((label, day_min))
        result["week_bars"] = bars
        # 今日：取今天日期对应的桶
        today_key = time.strftime("%Y-%m-%d")
        for key, val in times.items():
            try:
                if time.strftime("%Y-%m-%d", time.localtime(int(key))) == today_key:
                    result["today_min"] += int(val) // 60
            except (ValueError, TypeError, OSError):
                continue

    # --- 累计统计卡片 ---
    def fmt_hours(seconds: int) -> str:
        # 保留一位小数并去掉末尾的 .0：1414 / 5.5
        return f"{seconds / 3600:.1f}".rstrip("0").rstrip(".")

    def clean_count(text: str) -> str:
        # counts 自带 "104本" / "2.6条"，去掉单位后由 UI 统一拼接
        return re.sub(r"(本|条|天|小时|次|周|月|年)$", "", text).strip()

    read_stat = {
        str(s.get("stat")): clean_count(str(s.get("counts") or ""))
        for s in (overall.get("readStat") or [])
    }
    result["stats"] = [
        ("累计阅读", fmt_hours(int(overall.get("totalReadTime") or 0)), "小时", "🕐"),
        ("阅读天数", str(int(overall.get("readDays") or 0)), "天", "📅"),
        ("读过", read_stat.get("读过", "—"), "本", "📖"),
        ("读完", read_stat.get("读完", "—"), "本", "✓"),
        ("笔记", read_stat.get("笔记", "—"), "条", "✎"),
        ("日均阅读", fmt_hours(int(overall.get("dayAverageReadTime") or 0)), "小时", "◷"),
    ]

    # --- 阅读偏好 ---
    titles: List[str] = []
    cat_word = str(overall.get("preferCategoryWord") or "").strip()
    time_word = str(overall.get("preferTimeWord") or "").strip()
    if cat_word:
        titles.append(cat_word)
    if time_word:
        titles.append(time_word)
    result["prefer_titles"] = titles[:2]

    cats = []
    for c in (overall.get("preferCategory") or [])[:5]:
        cats.append((
            str(c.get("categoryTitle") or c.get("parentCategoryTitle") or "未分类"),
            round(int(c.get("readingTime") or 0) / 3600, 1),
            float(c.get("val") or 0),
        ))
    result["prefer_categories"] = cats

    prefer_time = overall.get("preferTime") or []
    if prefer_time:
        # 接口顺序：从 6 点开始到次日 5 点
        hour_bars = []
        for idx, sec in enumerate(prefer_time[:24]):
            hour = (idx + 6) % 24
            hour_bars.append((f"{hour:02d}", int(sec) // 60))
        result["prefer_time"] = hour_bars

    # --- 继续阅读（书架里最近读的一本）---
    shelf = safe(lambda: client.get_shelf(), "书架")
    if shelf and shelf.get("books"):
        books = sorted(
            shelf["books"],
            key=lambda b: int(b.get("readUpdateTime") or 0),
            reverse=True,
        )
        if books:
            b = books[0]
            prog = safe(lambda: client.get_progress(str(b.get("bookId", ""))), "阅读进度")
            result["continue"] = {
                "title": str(b.get("title") or ""),
                "author": str(b.get("author") or ""),
                "cover": str(b.get("cover") or ""),
                "progress": int((prog or {}).get("progress") or 0),
                "reader_link": str(b.get("readerLink") or ""),
                "book_id": str(b.get("bookId") or ""),
            }
            # 书籍封面墙：最近读的前 12 本（书名 + 封面 URL，去重）
            seen = set()
            covers: List[Tuple[str, str]] = []
            for sb in books:
                t = str(sb.get("title") or "").strip()
                if not t or t in seen:
                    continue
                seen.add(t)
                covers.append((t, str(sb.get("cover") or "")))
                if len(covers) >= 12:
                    break
            result["cover_books"] = covers

    # --- 划线（取有笔记的书里第一本，拉它的划线）---
    notebooks = safe(lambda: client.get_notebooks(), "笔记本")
    if notebooks:
        result["note_count"] = int(notebooks.get("total") or 0)
        # 供书籍笔记页实时联动：转成卡片所需字段
        result["notebook_books"] = [
            {
                "bookId": str(b.get("bookId") or ""),
                "title": str(b.get("title") or ""),
                "author": str(b.get("author") or ""),
                "cover": str(b.get("cover") or ""),
                "noteCount": int(b.get("noteCount") or 0),
                "reviewCount": int(b.get("reviewCount") or 0),
                "bookmarkCount": int(b.get("bookmarkCount") or 0),
            }
            for b in (notebooks.get("books") or [])
        ]
        nb_books = sorted(
            notebooks.get("books") or [],
            key=lambda b: int(b.get("noteCount") or 0),
            reverse=True,
        )
        # 划线统计：有划线的书籍数 + 划线最多的前若干本（书名, 划线数）
        books_with_notes = [
            b for b in nb_books if int(b.get("noteCount") or 0) > 0
        ]
        result["note_book_count"] = len(books_with_notes)
        result["note_top"] = [
            (str(b.get("title") or "未命名"), int(b.get("noteCount") or 0))
            for b in books_with_notes[:8]
        ]
        # 月度划线趋势：统计每本书划线时间，按最近 6 个 5 天段聚合
        now_ts = int(time.time())
        day = 5 * 24 * 3600
        buckets = [0] * 6
        for nb in nb_books[:5]:
            bids = str(nb.get("bookId") or "")
            marks = safe(lambda bid=bids: client.get_highlights(bid), "趋势划线")
            if not marks:
                continue
            for m in marks:
                try:
                    ts = int(m.get("time") or 0)
                except (TypeError, ValueError):
                    continue
                if ts <= 0:
                    continue
                age = now_ts - ts
                if age < 0 or age >= day * 6:
                    continue
                idx = 5 - int(age // day)
                if 0 <= idx < 6:
                    buckets[idx] += 1
            if not result["highlights"]:
                title = str(nb.get("title") or "")
                author = str(nb.get("author") or "")
                for m in marks[:20]:
                    m.setdefault("title", title)
                    m.setdefault("author", author)
                result["highlights"] = marks
        if any(buckets):
            result["note_month"] = [
                (tr_month(i), buckets[i]) for i in range(6)
            ]
    return result


def tr_month(idx: int) -> str:
    """最近 6 段里第 idx 段的标签（0 最早 → 5 最近）。"""
    labels = ["30天前", "25天前", "20天前", "15天前", "10天前", "最近5天"]
    return labels[idx] if 0 <= idx < len(labels) else ""


def greeting_by_time() -> str:
    """按当前时段返回问候语，复刻截图副标题。"""
    hour = time.localtime().tm_hour
    if 5 <= hour < 11:
        return "早上好，读一会儿吧"
    if 11 <= hour < 14:
        return "午间小憩，读一会儿吧"
    if 14 <= hour < 18:
        return "下午好，读一会儿吧"
    if 18 <= hour < 23:
        return "晚上好，读一会儿吧"
    return "夜深了，读一会儿吧"


# ---------------------------------------------------------------- 传书（导入）客户端

class WeReadUploader:
    """传书客户端：支持网页 Cookie 与扫码登录态，流程均为凭证 -> COS PUT -> notify。"""

    def __init__(
        self,
        cookie_str: str = "",
        timeout: int = 30,
        mobile_session: Optional[WeReadMobileSession] = None,
    ) -> None:
        self.cookies = parse_cookie(cookie_str)
        self.mobile_session = mobile_session
        self.timeout = timeout
        self.session = requests.Session()
        if mobile_session is not None:
            # 扫码登录得到的是移动端 accessToken，不是网页 wr_skey。
            # 必须走移动端请求头，不能伪装成网页 Cookie。
            headers = dict(VERSION_HEADERS)
            headers.update({
                "vid": mobile_session.vid,
                "accessToken": mobile_session.access_token,
            })
            self.session.headers.update(headers)
        else:
            self.session.headers.update({
                "User-Agent": UA_WEB,
                "Referer": "https://weread.qq.com/web/upload",
                "Origin": "https://weread.qq.com",
            })
            if self.cookies:
                self.session.cookies.update(self.cookies)

    @property
    def auth_mode(self) -> str:
        return "扫码登录态" if self.mobile_session is not None else "网页 Cookie"

    def validate(self) -> None:
        if self.mobile_session is not None:
            session = self.mobile_session
            if not (session.vid and session.access_token and session.refresh_token):
                raise WeReadError("扫码登录态不完整，请重新扫码登录")
            return
        if not cookie_is_valid(self.cookies):
            raise WeReadError("Cookie 缺少 wr_vid / wr_skey，请重新从浏览器复制完整 Cookie")

    def check_login(self) -> bool:
        """快速校验登录态：扫码登录用 /shelf/sync，网页 Cookie 用 /web/shelf/sync。"""
        self.validate()
        try:
            if self.mobile_session is not None:
                resp = self.session.get(
                    f"{I_WEREAD}/shelf/sync",
                    params={"synckey": 0, "teenmode": 0, "album": 1, "onlyBookid": 0},
                    timeout=self.timeout,
                )
                if resp.status_code != 200:
                    return False
                text = resp.text[:500]
                return "errCode\":-2012" not in text and "errcode\":-2012" not in text
            resp = self.session.get(
                f"{I_WEREAD}/web/shelf/sync",
                params={"synckey": 0, "teenmode": 0, "album": 1, "onlyBookid": 0},
                timeout=self.timeout,
            )
            return resp.status_code == 200 and "errCode" not in resp.text[:200]
        except Exception:  # noqa: BLE001
            return False

    def _get_credential(self, name: str) -> Dict[str, Any]:
        """取 COS 上传凭证。"""
        resp = self.session.get(
            f"{I_WEREAD}/cos/getcredential",
            params={"name": name, "from": ""},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise WeReadError(f"获取上传凭证失败：HTTP {resp.status_code}")
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise WeReadError("上传凭证响应不是合法 JSON") from exc
        cred = data.get("Response") or {}
        if not cred:
            # 有些回包直接平铺
            cred = data
        bucket = str(cred.get("Bucket") or cred.get("bucket") or "")
        object_name = str(cred.get("ObjectName") or cred.get("objectName") or "")
        creds = cred.get("Credentials") or {}
        tmp_id = str(creds.get("TmpSecretId") or cred.get("TmpSecretId") or "")
        tmp_key = str(creds.get("TmpSecretKey") or cred.get("TmpSecretKey") or "")
        token = str(creds.get("Token") or cred.get("Token") or "")
        expired = cred.get("ExpiredTime") or cred.get("expiredTime") or 0

        # 安全校验：bucket 仅允许字母数字和 -，防 host 注入
        if not bucket or not all(c.isalnum() or c == "-" for c in bucket):
            raise WeReadError("上传凭证的 Bucket 不合法")
        if not object_name or object_name.startswith("//") or ".." in object_name:
            raise WeReadError("上传凭证的 ObjectName 不合法")
        if not (tmp_id and tmp_key and token):
            raise WeReadError("上传凭证缺少临时密钥（TmpSecretId/TmpSecretKey/Token）")
        return {
            "bucket": bucket,
            "object_name": object_name,
            "secret_id": tmp_id,
            "secret_key": tmp_key,
            "token": token,
            "expired": int(expired),
            "domain": str(cred.get("Domain") or cred.get("domain") or ""),
        }

    def _cos_put(
        self,
        cred: Dict[str, Any],
        file_path: str,
        progress_cb: Optional[Callable[[int], None]] = None,
    ) -> None:
        """COS 直传：先零字节预热，再分块流式上传并做 CRC64 校验。"""
        host = f"{cred['bucket']}.cos.accelerate.myqcloud.com"
        if cred.get("domain"):
            # 用 removeprefix 精确去除协议前缀；lstrip 是按字符集剥离，
            # 会把 static./s3./test. 之类域名的首字母误删
            host = cred["domain"].strip().removeprefix("https://").removeprefix("http://")
            host = host.rstrip("/")
        url = f"https://{host}/{cred['object_name'].lstrip('/')}"

        def make_headers() -> Dict[str, str]:
            auth, _, _ = cos_authorization(
                cred["secret_id"], cred["secret_key"], cred["object_name"],
                host, cred["expired"] or int(time.time()) + 3600,
            )
            return {
                "Authorization": auth,
                "x-cos-security-token": cred["token"],
                "Host": host,
            }

        # 1) 零字节预热：避免首次直接 PUT 出现 CRC/ETag 不一致
        resp = self.session.put(url, data=b"", headers=make_headers(), timeout=self.timeout)
        if resp.status_code != 200:
            raise WeReadError(f"COS 初始化失败：HTTP {resp.status_code}")
        if resp.headers.get("x-cos-hash-crc64ecma", "0") not in ("0", ""):
            raise WeReadError("COS 初始化校验失败，请重试")

        # 2) 分块流式上传（256 KiB），边传边算 CRC
        size = os.path.getsize(file_path)
        done = 0
        crc = 0
        last_resp: Optional[requests.Response] = None  # 最后一次上传响应，含文件 CRC
        chunk = 256 * 1024
        with open(file_path, "rb") as f:
            while True:
                block = f.read(chunk)
                if not block:
                    break
                crc = crc64(block, crc)
                # 每块都需重新签名（时间窗可能跨秒，重签更稳妥）
                last_resp = self.session.put(url, data=block, headers=make_headers(), timeout=self.timeout)
                if last_resp.status_code != 200:
                    raise WeReadError(f"COS 上传失败：HTTP {last_resp.status_code}")
                done += len(block)
                if progress_cb:
                    progress_cb(min(95, int(done * 95 / max(size, 1))))

        # 3) CRC 核对：必须用最后一次上传响应的 CRC（预热响应恒为 0，无校验意义）
        remote = str(last_resp.headers.get("x-cos-hash-crc64ecma", "")) if last_resp is not None else ""
        if remote and remote != "0" and remote != str(crc):
            raise WeReadError("上传内容校验失败，未提交导入，请重试")

    def _notify(self, file_name: str, object_name: str) -> Dict[str, Any]:
        """通知微信读书解析入架（不可重放）。"""
        resp = self.session.post(
            f"{I_WEREAD}/cos/notify",
            params={"name": file_name, "path": object_name, "cancel": 0},
            json={},
            timeout=self.timeout,
        )
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            raise WeReadError(f"提交解析失败：HTTP {resp.status_code}")
        status = data.get("status")
        if status != 1 and not data.get("bookId"):
            raise WeReadError(f"提交解析未成功：{data.get('errMsg') or data}")
        return data

    def upload(
        self,
        file_path: str,
        progress_cb: Optional[Callable[[int], None]] = None,
    ) -> Dict[str, Any]:
        """导入单个本地文件到微信读书。返回 {'success', 'message', ...}。"""
        self.validate()
        if not os.path.isfile(file_path):
            return {"success": False, "message": f"文件不存在：{file_path}"}
        name = os.path.basename(file_path)
        ext = os.path.splitext(name)[1].lower()
        if ext not in SUPPORTED_FORMATS:
            return {"success": False, "message": f"不支持的格式：{ext}"}
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if size_mb > MAX_SIZE_MB:
            return {"success": False, "message": f"文件过大：{size_mb:.1f} MB（上限 {MAX_SIZE_MB} MB）"}
        if progress_cb:
            progress_cb(1)

        try:
            cred = self._get_credential(os.path.splitext(name)[0])
            self._cos_put(cred, file_path, progress_cb)
            data = self._notify(name, cred["object_name"])
            if progress_cb:
                progress_cb(100)
            return {
                "success": True,
                "message": "已提交微信读书解析，请稍后到官方书架查看",
                "book_id": data.get("bookId", ""),
            }
        except WeReadError as exc:
            return {"success": False, "message": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "message": f"导入失败：{exc}"}
