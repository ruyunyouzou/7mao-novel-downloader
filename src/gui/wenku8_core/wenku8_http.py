# -*- coding: utf-8 -*-
"""文库8网络层：全栈浏览器指纹通道（httpcloak）+ requests 兜底。

为什么需要这一层
----------------
本机诊断已经证明：普通 requests / urllib 的 TLS + HTTP/2 指纹会被
Cloudflare 识别，`.net/.com` 全线 403，`.cc` 的 search.php 返回 42 字节
nginx Welcome；同一台机器换 httpcloak 的 ``chrome-latest-windows`` 指纹后，
详情页 / 目录页 / search.php / 分类页全部 200，搜索还能正确 302 到
``/book/2353.htm``。因此网络层必须换指纹会话，而不是继续堆 UA 或 Cookie。

本模块把 httpcloak 包装成项目其它代码已经熟悉的 requests 风格接口：
``session.get/post``、``session.cookies.get_dict()``、``resp.encoding/text``。
httpcloak 不可用时自动退回 requests，老功能和离线测试仍可正常运行。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

try:  # httpcloak 是可选运行依赖；缺失时全链路退回 requests
    import httpcloak as _hc
except Exception:  # noqa: BLE001
    _hc = None


_PREFERRED_PRESETS = (
    "chrome-latest-windows",
    "chrome-143-windows",
    "chrome-latest",
)


def _pick_preset() -> str:
    """选一个可用的 Windows Chrome 指纹；都不可用时交给 httpcloak 默认值。"""
    if _hc is None:
        return ""
    try:
        presets = set(_hc.available_presets())
    except Exception:  # noqa: BLE001
        return _PREFERRED_PRESETS[0]
    for name in _PREFERRED_PRESETS:
        if name in presets:
            return name
    return _PREFERRED_PRESETS[0]


_MODE_CACHE: Optional[str] = None


def network_mode() -> str:
    """返回当前**实际可用**的通道模式，供诊断信息显示。

    挑刺 #4：不能只看 ``_hc is not None`` 就报 httpcloak——装了包但所有
    preset 都不可用、或 Session 构造失败时，make_session 会回退 requests，
    此时诊断仍报 httpcloak 就是撒谎。这里做一次轻量可用性校验并缓存结果。
    """
    global _MODE_CACHE
    if _MODE_CACHE is not None:
        return _MODE_CACHE
    if _hc is None:
        _MODE_CACHE = "requests"
        return _MODE_CACHE
    try:
        probe = CloakSession(timeout=2)
        probe.close()
        _MODE_CACHE = "httpcloak"
    except Exception:  # noqa: BLE001
        _MODE_CACHE = "requests"
    return _MODE_CACHE


class CloakCookies:
    """把 httpcloak 的 Cookie 列表适配成 requests 风格的 CookieJar。"""

    def __init__(self, session: Any) -> None:
        self._session = session

    def set(self, key: str, value: str, domain: str = "", path: str = "/",
            **_: Any) -> None:
        self._session.set_cookie(
            str(key), str(value), domain=str(domain or ""), path=str(path or "/")
        )

    def get_dict(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        try:
            for cookie in self._session.cookies:
                if getattr(cookie, "name", ""):
                    out[cookie.name] = str(getattr(cookie, "value", "") or "")
        except Exception:  # noqa: BLE001
            pass
        return out

    def get(self, key: str, default: Any = None) -> Any:
        return self.get_dict().get(str(key), default)

    def clear(self, *_: Any, **__: Any) -> None:
        try:
            self._session.clear_cookies()
        except Exception:  # noqa: BLE001
            pass


class CloakResponse:
    """把 httpcloak Response 适配成 requests Response 的常用字段。"""

    def __init__(self, raw: Any, encoding: str = "") -> None:
        self._raw = raw
        self.encoding = encoding or None
        self.status_code = int(getattr(raw, "status_code", 0) or 0)
        self.url = str(getattr(raw, "url", "") or "")
        self.headers = getattr(raw, "headers", {}) or {}
        self.reason = getattr(raw, "reason", "")

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    @property
    def content(self) -> bytes:
        body = getattr(self._raw, "content", None)
        if body is not None:
            return bytes(body)
        return bytes(getattr(self._raw, "body", b"") or b"")

    @property
    def text(self) -> str:
        if self.encoding:
            return self.content.decode(self.encoding, "replace")
        return str(getattr(self._raw, "text", "") or "")

    def iter_content(self, chunk_size: int = 1):  # pragma: no cover - 兼容用
        body = self.content
        size = max(1, int(chunk_size or 1))
        for i in range(0, len(body), size):
            yield body[i:i + size]

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code} {self.url}")


class CloakSession:
    """requests 风格的 httpcloak 会话（仅供文库8内核使用）。"""

    def __init__(self, preset: str = "", timeout: int = 20, verify: bool = True) -> None:
        # 挑刺 #1：默认开启 TLS 证书校验。实测 verify=True 对 .cc 详情页仍 200
        #（与 verify=False 字节数一致），过 Cloudflare 靠的是指纹而非关校验，
        # 全局 verify=False 只会徒增中间人攻击面。
        if _hc is None:  # pragma: no cover - 防御性分支
            raise RuntimeError("httpcloak is not installed")
        self._session = _hc.Session(
            preset=preset or _pick_preset(), timeout=timeout, verify=verify
        )
        self.preset = preset or _pick_preset()
        self.cookies = CloakCookies(self._session)
        self.headers: Dict[str, str] = {}
        self.timeout = int(timeout or 20)
        # 供插件判断跨线程复用是否安全；httpcloak Session 不是线程安全对象。
        import threading

        self.owner_thread = threading.get_ident()

    def _merged_headers(self, headers: Optional[Dict[str, str]]) -> Dict[str, str]:
        merged = dict(self.headers or {})
        if headers:
            merged.update(headers)
        return merged

    def get(self, url: str, **kwargs: Any) -> CloakResponse:
        """requests 风格 GET；挑刺 #2：透传 httpcloak 支持的参数。

        支持的键：timeout / headers / params / cookies / allow_redirects /
        stream。其余未知键忽略而非静默塞进底层导致 TypeError。
        """
        timeout = kwargs.pop("timeout", self.timeout)
        headers = self._merged_headers(kwargs.pop("headers", None))
        params = kwargs.pop("params", None)
        cookies = kwargs.pop("cookies", None)
        allow_redirects = kwargs.pop("allow_redirects", None)
        stream = kwargs.pop("stream", False)
        call_kwargs: Dict[str, Any] = {
            "headers": headers, "timeout": timeout, "stream": stream,
        }
        # 只在显式给出时传，避免 None 覆盖 httpcloak 自身的默认值。
        if params is not None:
            call_kwargs["params"] = params
        if cookies is not None:
            call_kwargs["cookies"] = cookies
        if allow_redirects is not None:
            call_kwargs["allow_redirects"] = allow_redirects
        raw = self._session.get(url, **call_kwargs)
        return CloakResponse(raw, encoding=str(kwargs.pop("encoding", "") or ""))

    def post(self, url: str, data: Any = None, **kwargs: Any) -> CloakResponse:
        """requests 风格 POST；挑刺 #2：透传 json/params 等参数。"""
        timeout = kwargs.pop("timeout", self.timeout)
        headers = self._merged_headers(kwargs.pop("headers", None))
        params = kwargs.pop("params", None)
        allow_redirects = kwargs.pop("allow_redirects", None)
        json_body = kwargs.pop("json", None)
        call_kwargs: Dict[str, Any] = {
            "data": data, "headers": headers, "timeout": timeout,
        }
        if params is not None:
            call_kwargs["params"] = params
        if allow_redirects is not None:
            call_kwargs["allow_redirects"] = allow_redirects
        if json_body is not None:
            call_kwargs["json"] = json_body
        raw = self._session.post(url, **call_kwargs)
        return CloakResponse(raw, encoding=str(kwargs.pop("encoding", "") or ""))

    def close(self) -> None:  # pragma: no cover - httpcloak 暂无显式 close
        return None


def _cookie_domain(base: str) -> str:
    host = urlparse(str(base or "")).netloc or str(base or "").split("/")[0]
    bare = host[4:] if host.startswith("www.") else host
    return "." + bare if bare else ""


def make_fingerprint_session(cookie: str, base: str, timeout: int = 20) -> CloakSession:
    """构造带登录态与站点 Referer 的指纹会话。"""
    sess = CloakSession(timeout=timeout)
    sess.headers.update({
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": str(base or "").rstrip("/") + "/",
    })
    domain = _cookie_domain(base)
    for key, val in _parse_cookie(cookie).items():
        sess.cookies.set(key, val, domain=domain)
    return sess


def make_requests_session(cookie: str, base: str, timeout: int = 20,
                          user_agent: str = "") -> requests.Session:
    """httpcloak 不可用时的兼容通道，保持原有 requests 行为。"""
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": str(base or "").rstrip("/") + "/",
    })
    domain = _cookie_domain(base)
    for key, val in _parse_cookie(cookie).items():
        sess.cookies.set(key, val, domain=domain)
    sess.timeout = timeout  # type: ignore[attr-defined]
    return sess


def selftest_report() -> Dict[str, str]:
    """打包自检：确认发布版 exe 里 httpcloak 原生库被正确加载。

    #49 的落地验证。原生库缺失时 ``CloakSession()`` 构造会抛异常，
    ``network_mode()`` 随之回退 ``requests``——发布版就会静默全线 403。
    本函数不访问网络（CI 机器 IP 会被站点拦，测网络会得到假阴性），只验证：
      1. httpcloak 模块可导入
      2. 原生库文件确实存在于 ``httpcloak/lib``
      3. 能用它构造出一个真实的指纹 Session（构造即加载原生库）

    :return: key->str 报告，含 ``network_mode`` 与 ``native_libs`` 等字段，
        调用方（CLI / CI）据此判断 PASS/FAIL。
    """
    report: Dict[str, str] = {"httpcloak_imported": str(_hc is not None)}
    if _hc is None:
        report.update(network_mode="requests", native_libs="",
                      session_ok="False", result="FAIL",
                      error="httpcloak module not importable")
        return report
    try:
        mod_dir = os.path.dirname(str(getattr(_hc, "__file__", "") or ""))
        lib_dir = os.path.join(mod_dir, "lib")
        libs: list[str] = []
        if os.path.isdir(lib_dir):
            libs = sorted(
                f for f in os.listdir(lib_dir)
                if f.lower().endswith((".so", ".dll", ".dylib"))
            )
        report["native_libs"] = ",".join(libs)
        report["preset"] = _pick_preset()
        probe = CloakSession(timeout=2)
        report["session_ok"] = "True"
        probe.close()
    except Exception as exc:  # noqa: BLE001
        report["session_ok"] = "False"
        report["error"] = f"{type(exc).__name__}: {exc}"

    # 强制重新探测（清掉进程内缓存），让报告反映真实加载结果。
    global _MODE_CACHE
    _MODE_CACHE = None
    mode = network_mode()
    report["network_mode"] = mode
    # 严格以 network_mode 为准——这正是 CI 与用户要确认的核心指标。
    report["result"] = "PASS" if mode == "httpcloak" else "FAIL"
    return report


def _parse_cookie(cookie: str) -> Dict[str, str]:
    """解析 'k=v; k2=v2' 会话串。挑刺 #5：兼容换行与逗号两种分隔符，
    与 auth.parse_cookie / web_context.split_cookie 行为拉齐。"""
    normalized = str(cookie or "").replace("\r", "\n").replace(";", "\n").replace(",", "\n")
    out: Dict[str, str] = {}
    for part in normalized.split("\n"):
        key, sep, val = part.strip().partition("=")
        if sep and key:
            out[key] = val.strip()
    return out
