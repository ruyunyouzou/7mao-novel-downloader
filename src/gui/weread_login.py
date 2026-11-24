# -*- coding: utf-8 -*-
"""
微信读书扫码登录核心（参考 Olib mobile 的 WereadMobileClient 实现）。

流程：
1. GET  i.weread.qq.com/wxticket?nonceStr=weread
       取 signature / timeStamp
2. GET  open.weixin.qq.com/connect/sdk/qrconnect
       appid + noncestr + timestamp + scope + signature -> uuid
3. 二维码内容：open.weixin.qq.com/connect/confirm?uuid=<uuid>
4. 长轮询 long.open.weixin.qq.com/connect/l/qrconnect
       408 等待扫码 / 404 已扫码待确认 / 405 已确认返回 wx_code
       402 过期 / 403 拒绝
5. POST i.weread.qq.com/login 用 wx_code 换取
       vid / accessToken / refreshToken，并保存本机会话。

说明：这是第三方客户端使用的移动端私有流程，并非微信读书官方开放平台接口。
请仅用于个人自用，遵守服务条款；本模块不会把凭证写入日志，也不会上传到任何服务器。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlencode

import requests

# ---------------------------------------------------------------- 常量

WEREAD_BASE = "https://i.weread.qq.com"
WX_APP_ID = "wxab9b71ad2b90ff34"
WX_QR_ENDPOINT = "https://open.weixin.qq.com/connect/sdk/qrconnect"
WX_CONFIRM_ENDPOINT = "https://open.weixin.qq.com/connect/confirm"
WX_POLL_ENDPOINT = "https://long.open.weixin.qq.com/connect/l/qrconnect"
WX_SCOPE = "snsapi_userinfo,snsapi_timeline,snsapi_friend"

MOBILE_UA = (
    "WeRead/2.1.2 WRBrand/Onyx wr_eink Dalvik/2.1.0 "
    "(Linux; U; Android 11; BOOX Build/onyx)"
)
VERSION_HEADERS: Dict[str, str] = {
    "baseapi": "30",
    "appver": "2.1.2.10245900",
    "basever": "2.1.2.10245900",
    "osver": "11",
    "channelId": "900",
    "User-Agent": MOBILE_UA,
}

STATUS_WAITING = 408
STATUS_SCANNED = 404
STATUS_CONFIRMED = 405
STATUS_EXPIRED = 402
STATUS_DECLINED = 403

SESSION_FILENAME = "weread_mobile_session.json"

# ---------------------------------------------------------------- 异常与数据


class WeReadLoginError(Exception):
    """微信读书扫码登录错误。"""


@dataclass(frozen=True)
class WeReadQrSession:
    """一次扫码会话。"""

    uuid: str
    confirm_url: str
    created_at: float = field(default_factory=time.time)

    @property
    def expired(self, ttl: int = 300) -> bool:
        return time.time() - self.created_at > ttl


@dataclass(frozen=True)
class WeReadMobileSession:
    """移动端登录态。敏感字段不进入 repr，避免误打印到日志。"""

    vid: str
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    device_id: str = field(repr=False)
    saved_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vid": self.vid,
            "accessToken": self.access_token,
            "refreshToken": self.refresh_token,
            "deviceId": self.device_id,
            "savedAt": self.saved_at,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "WeReadMobileSession":
        if not isinstance(data, dict):
            raise WeReadLoginError("保存的登录态格式不正确")
        vid = str(data.get("vid") or data.get("wr_vid") or "")
        access = str(data.get("accessToken") or data.get("access_token") or "")
        refresh = str(data.get("refreshToken") or data.get("refresh_token") or "")
        device = str(data.get("deviceId") or data.get("device_id") or "")
        if not (vid and access and refresh and device):
            raise WeReadLoginError("保存的登录态字段不完整")
        return WeReadMobileSession(
            vid=vid,
            access_token=access,
            refresh_token=refresh,
            device_id=device,
            saved_at=float(data.get("savedAt") or data.get("saved_at") or time.time()),
        )

    @property
    def masked_vid(self) -> str:
        if len(self.vid) <= 8:
            return "****"
        return f"{self.vid[:4]}{'*' * (len(self.vid) - 8)}{self.vid[-4:]}"

    def auth_headers(self) -> Dict[str, str]:
        headers = dict(VERSION_HEADERS)
        headers.update({
            "vid": self.vid,
            "accessToken": self.access_token,
        })
        return headers


@dataclass(frozen=True)
class QrPollResult:
    """一次扫码状态轮询结果。"""

    status: int
    wx_code: str = ""
    raw: Optional[Dict[str, Any]] = None

    @property
    def waiting(self) -> bool:
        return self.status in (STATUS_WAITING, STATUS_SCANNED)


# ---------------------------------------------------------------- 工具函数


def _digits(count: int) -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(count))


def _safe_error_code(data: Dict[str, Any]) -> str:
    code = data.get("errcode", data.get("errCode", data.get("wx_errcode")))
    if code in (None, ""):
        return ""
    text = str(code)
    return text if re.fullmatch(r"-?\d{1,10}", text) else ""


def _parse_json(text: str, label: str) -> Dict[str, Any]:
    """解析接口响应，限制大小，不回显敏感原文。"""
    text = (text or "").strip()
    if len(text) > 1024 * 1024:
        raise WeReadLoginError(f"{label}响应过大")
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001
        # 兼容个别微信接口返回 JSONP / key=value 形式
        if "wx_errcode" in text:
            pairs = dict(re.findall(r"(wx_errcode|wx_code)\s*=\s*([^&\"'{]*)", text))
            if "wx_errcode" in pairs:
                return {"wx_errcode": pairs["wx_errcode"].strip(),
                        "wx_code": pairs.get("wx_code", "").strip()}
        raise WeReadLoginError(f"{label}响应不是合法 JSON")
    if not isinstance(data, dict):
        raise WeReadLoginError(f"{label}响应格式不正确")
    return data


def _status_text(status: int) -> str:
    return {
        STATUS_WAITING: "等待扫码",
        STATUS_SCANNED: "已扫码，等待手机确认",
        STATUS_CONFIRMED: "已确认",
        STATUS_EXPIRED: "二维码已过期",
        STATUS_DECLINED: "已拒绝授权",
    }.get(status, f"未知状态（{status}）")


# ---------------------------------------------------------------- 登录核心


class WeReadQrLogin:
    """扫码登录请求封装。"""

    def __init__(self, timeout: int = 20, poll_timeout: int = 30) -> None:
        self.timeout = timeout
        self.poll_timeout = poll_timeout
        self.session = requests.Session()

    def request_qr(self) -> WeReadQrSession:
        """获取 ticket 并创建微信扫码会话。"""
        try:
            ticket_resp = self.session.get(
                f"{WEREAD_BASE}/wxticket",
                params={"nonceStr": "weread"},
                headers=dict(VERSION_HEADERS),
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise WeReadLoginError("获取扫码票据失败：网络异常") from exc
        if ticket_resp.status_code >= 400:
            raise WeReadLoginError(f"获取扫码票据失败：HTTP {ticket_resp.status_code}")
        ticket = _parse_json(ticket_resp.text, "扫码票据")
        signature = str(ticket.get("signature") or "")
        timestamp = ticket.get("timeStamp", ticket.get("timestamp"))
        if not signature or timestamp in (None, ""):
            raise WeReadLoginError("扫码票据不完整")

        query = {
            "appid": WX_APP_ID,
            "noncestr": "weread",
            "timestamp": str(timestamp),
            "scope": WX_SCOPE,
            "signature": signature,
        }
        try:
            qr_resp = self.session.get(
                WX_QR_ENDPOINT,
                params=query,
                headers={"User-Agent": MOBILE_UA},
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise WeReadLoginError("创建微信二维码失败：网络异常") from exc
        if qr_resp.status_code >= 400:
            raise WeReadLoginError(f"创建微信二维码失败：HTTP {qr_resp.status_code}")
        data = _parse_json(qr_resp.text, "微信二维码")
        try:
            errcode = int(data.get("errcode", 0) or 0)
        except (TypeError, ValueError):
            errcode = -1
        if errcode != 0:
            raise WeReadLoginError(f"微信拒绝了二维码请求（错误码 {errcode}）")
        uuid = str(data.get("uuid") or "")
        if not uuid:
            raise WeReadLoginError("微信二维码响应缺少 uuid")
        confirm_url = f"{WX_CONFIRM_ENDPOINT}?{urlencode({'uuid': uuid})}"
        return WeReadQrSession(uuid=uuid, confirm_url=confirm_url)

    def poll_once(self, qr: WeReadQrSession, last: Optional[int] = None) -> QrPollResult:
        """轮询一次扫码状态。网络抖动由上层决定是否重试。"""
        if qr.expired:
            raise WeReadLoginError("二维码已过期，请刷新")
        params: Dict[str, Any] = {"f": "json", "uuid": qr.uuid}
        if last is not None:
            params["last"] = last
        try:
            resp = self.session.get(
                WX_POLL_ENDPOINT,
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=self.poll_timeout,
            )
        except requests.Timeout as exc:
            raise WeReadLoginError("轮询超时，正在重试") from exc
        except Exception as exc:  # noqa: BLE001
            raise WeReadLoginError("轮询扫码状态失败：网络异常") from exc
        if resp.status_code >= 400:
            raise WeReadLoginError(f"轮询扫码状态失败：HTTP {resp.status_code}")
        data = _parse_json(resp.text, "扫码状态")
        try:
            status = int(data.get("wx_errcode", STATUS_WAITING) or STATUS_WAITING)
        except (TypeError, ValueError):
            raise WeReadLoginError("扫码状态格式不正确")
        wx_code = str(data.get("wx_code") or "")
        if status == STATUS_CONFIRMED and not wx_code:
            raise WeReadLoginError("扫码已确认，但未返回授权码")
        return QrPollResult(status=status, wx_code=wx_code, raw=data)

    def exchange_code(self, wx_code: str) -> WeReadMobileSession:
        """用 wx_code 换取 vid / accessToken / refreshToken。"""
        if not wx_code:
            raise WeReadLoginError("授权码为空")
        device_id = f"eink334691225{_digits(19)}"
        install_id = f"eink31{_digits(26)}"
        timestamp = int(time.time() * 1000)
        random_value = secrets.randbelow(1000)
        signature = hashlib.sha256(
            f"{timestamp}{device_id}{random_value}".encode("utf-8")
        ).hexdigest()
        payload = {
            "appFirstInstall": 1,
            "code": wx_code,
            "deviceId": device_id,
            "deviceName": "BOOX",
            "installId": install_id,
            "isAutoLogout": 0,
            "isFromQrcode": 1,
            "random": random_value,
            "signature": signature,
            "timestamp": timestamp,
            "trackId": "",
            "deviceType": 3,
        }
        headers = dict(VERSION_HEADERS)
        headers["content-type"] = "application/json; charset=UTF-8"

        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = self.session.post(
                    f"{WEREAD_BASE}/login",
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
                if resp.status_code >= 500:
                    raise WeReadLoginError(f"换取登录态失败：HTTP {resp.status_code}")
                data = _parse_json(resp.text, "登录结果")
                break
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2)
                    continue
                raise WeReadLoginError("换取登录态失败：网络异常") from exc
        else:  # pragma: no cover - for type checkers
            raise WeReadLoginError("换取登录态失败") from last_exc

        err = _safe_error_code(data)
        access = str(data.get("accessToken") or "")
        refresh = str(data.get("refreshToken") or "")
        vid = str(data.get("vid") or "")
        if not (access and refresh and vid):
            raise WeReadLoginError(
                f"登录未返回完整凭证（错误码 {err or '未知'}）"
            )
        return WeReadMobileSession(
            vid=vid,
            access_token=access,
            refresh_token=refresh,
            device_id=device_id,
        )

    def refresh_session(self, session: WeReadMobileSession) -> WeReadMobileSession:
        """用 refreshToken 刷新登录态；账号 vid 不一致时拒绝保存。"""
        timestamp = int(time.time() * 1000)
        random_value = secrets.randbelow(1000) + 1
        signature = hashlib.sha256(
            f"{timestamp}{session.device_id}{random_value}".encode("utf-8")
        ).hexdigest()
        payload = {
            "deviceId": session.device_id,
            "deviceName": "BOOX",
            "inBackground": 0,
            "kickType": 1,
            "random": random_value,
            "refCgi": "",
            "refreshToken": session.refresh_token,
            "signature": signature,
            "timestamp": timestamp,
            "trackId": "",
            "deviceType": 3,
        }
        headers = dict(VERSION_HEADERS)
        headers["content-type"] = "application/json; charset=UTF-8"
        try:
            resp = self.session.post(
                f"{WEREAD_BASE}/login",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise WeReadLoginError("刷新登录态失败：网络异常") from exc
        if resp.status_code >= 400:
            raise WeReadLoginError(f"刷新登录态失败：HTTP {resp.status_code}")
        data = _parse_json(resp.text, "登录态刷新")
        vid = str(data.get("vid") or session.vid)
        access = str(data.get("accessToken") or "")
        refresh = str(data.get("refreshToken") or session.refresh_token)
        if vid != session.vid or not access or not refresh:
            raise WeReadLoginError("刷新后的账号不一致或凭证不完整")
        return WeReadMobileSession(
            vid=vid,
            access_token=access,
            refresh_token=refresh,
            device_id=session.device_id,
        )


# ---------------------------------------------------------------- 本机会话存储


def session_path(store: Any) -> Optional[str]:
    base_dir = getattr(store, "base_dir", None)
    if not base_dir:
        return None
    return os.path.join(str(base_dir), SESSION_FILENAME)


def save_session(store: Any, session: WeReadMobileSession) -> bool:
    """把会话以 0600 权限写到本机私有目录。"""
    path = session_path(store)
    if not path:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except Exception:  # noqa: BLE001
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def load_session(store: Any) -> Optional[WeReadMobileSession]:
    """读取本机会话；损坏时返回 None，不抛错影响页面加载。"""
    path = session_path(store)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return WeReadMobileSession.from_dict(data)
    except Exception:  # noqa: BLE001
        return None


def clear_session(store: Any) -> bool:
    path = session_path(store)
    if not path:
        return False
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


# ---------------------------------------------------------------- 会话调用封装


class WeReadMobileClient:
    """使用扫码登录态调用微信读书移动端接口（供传书 / 登录校验复用）。"""

    def __init__(
        self,
        session: WeReadMobileSession,
        timeout: int = 20,
        on_session_updated: Optional[Callable[[WeReadMobileSession], None]] = None,
    ) -> None:
        self.session = session
        self.timeout = timeout
        self.on_session_updated = on_session_updated
        self._http = requests.Session()

    def call(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        retry: bool = True,
    ) -> Dict[str, Any]:
        if not path.startswith("/") or path.startswith("//"):
            raise WeReadLoginError("接口路径不合法")
        method = method.upper()
        headers = self.session.auth_headers()
        if json_body is not None:
            headers["content-type"] = "application/json; charset=UTF-8"
        try:
            resp = self._http.request(
                method,
                f"{WEREAD_BASE}{path}",
                params=params,
                json=json_body,
                headers=headers,
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise WeReadLoginError("请求微信读书失败：网络异常") from exc
        data = _parse_json(resp.text, path) if resp.text.strip() else {}
        err = _safe_error_code(data)
        need_refresh = resp.status_code == 401 or err == "-2012"
        if need_refresh and retry and method in ("GET", "HEAD"):
            refreshed = WeReadQrLogin(timeout=self.timeout).refresh_session(self.session)
            self.session = refreshed
            if self.on_session_updated:
                self.on_session_updated(refreshed)
            return self.call(method, path, params=params, json_body=json_body, retry=False)
        if resp.status_code >= 400 or (err and err != "0"):
            raise WeReadLoginError(f"{path} 请求失败（HTTP {resp.status_code}，错误码 {err or '未知'}）")
        return data

    def check_login(self) -> bool:
        try:
            self.call("GET", "/shelf/sync", params={
                "synckey": 0,
                "teenmode": 0,
                "album": 1,
                "onlyBookid": 0,
            })
            return True
        except WeReadLoginError:
            return False


__all__ = [
    "WeReadLoginError",
    "WeReadQrSession",
    "WeReadMobileSession",
    "QrPollResult",
    "WeReadQrLogin",
    "WeReadMobileClient",
    "save_session",
    "load_session",
    "clear_session",
    "STATUS_WAITING",
    "STATUS_SCANNED",
    "STATUS_CONFIRMED",
    "STATUS_EXPIRED",
    "STATUS_DECLINED",
    "VERSION_HEADERS",
    "MOBILE_UA",
]
