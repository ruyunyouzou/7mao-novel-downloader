# -*- coding: utf-8 -*-
"""网络请求容错层：应对国内网络访问 GitHub 时的 SSL 证书验证失败与连接抖动。

背景
====
部分用户环境（代理 / 加速器 / 安全软件做 HTTPS 拦截）下，访问 api.github.com、
raw.githubusercontent.com 返回的证书链不含可信根，requests 默认严格校验会抛
``SSLCertVerificationError: CERTIFICATE_VERIFY_FAILED``，导致「检查更新」「一键
升级」在查询阶段直接失败。本模块提供统一的安全 GET：

策略（逐个通道尝试，拿到正常 HTTP 响应即返回，交给调用方判断状态码）
=====================================================================
1. 通道顺序：原始 url 优先，随后调用方给出的镜像 url（如 gh-proxy / jsDelivr）。
2. 每个通道先 ``verify=True``（默认安全）；若因证书链失败（SSLError）则同一
   通道用 ``verify=False`` 再试一次——仅用于公开的只读资源（更新清单 / Release
   元数据 / 安装包），下载安装包另有大小校验，风险可接受。
3. 非证书的异常（超时 / 连接错误）直接切换下一个通道。
4. 静音 urllib3 的 InsecureRequestWarning，避免控制台刷屏。

仅依赖 requests + urllib3（requests 已自带），无 GUI 依赖，可独立测试。
"""

from __future__ import annotations

from typing import Iterable, Optional, Tuple

import requests

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:  # noqa: BLE001 - 老版本 urllib3 无该异常类时忽略
    pass


def _is_cert_error(exc: BaseException) -> bool:
    """判断异常是否属于证书链验证失败（值得用 verify=False 重试）。"""
    try:
        from requests.exceptions import SSLError
        if isinstance(exc, SSLError):
            return True
    except Exception:  # noqa: BLE001
        pass
    text = str(exc).lower()
    return ("certificate" in text and "verify" in text) or "certificate_verify_failed" in text


def safe_get(url: str, *, headers: Optional[dict] = None,
             timeout=10, stream: bool = False,
             extra_urls: Iterable[str] = (),
             verify_fallback: bool = True,
             retry_codes: Iterable[int] = (403, 429)) -> Tuple[Optional[requests.Response], Optional[BaseException]]:
    """安全地发起 GET，返回 ``(response, error)``。

    - 依次尝试 ``url`` 与 ``extra_urls``；
    - 每个通道先严格校验证书，证书失败则用 ``verify=False`` 再试；
    - 状态码属于 ``retry_codes``（默认 403/429，GitHub 匿名限流）时不采纳，
      继续尝试下一个镜像通道；全部通道都是限流响应则返回最后一个，交给调用方；
    - 拿到其余任意 HTTP 响应立即返回 ``(resp, None)``（状态码由调用方判断）；
    - 全部失败返回 ``(None, 最后一个异常)``。

    ``stream=True`` 时由调用方负责关闭 response。
    """
    urls = [url] + [u for u in extra_urls if u and u != url]
    retry_codes = set(retry_codes)
    last_exc: Optional[BaseException] = None
    last_retry_resp: Optional[requests.Response] = None

    for current in urls:
        for verify in (True, False) if verify_fallback else (True,):
            try:
                resp = requests.get(current, headers=headers or {},
                                    timeout=timeout, stream=stream, verify=verify)
                if resp.status_code in retry_codes:
                    last_retry_resp = resp
                    # 该通道被限流：换下一个通道（不必再降级证书）
                    break
                return resp, None
            except BaseException as exc:  # noqa: BLE001 - 需捕获证书/连接/超时各类异常
                last_exc = exc
                if verify and _is_cert_error(exc):
                    # 同一通道降级校验再试一次
                    continue
                # 非证书问题或已降级过：切换下一个通道
                break

    if last_retry_resp is not None:
        return last_retry_resp, None
    return None, last_exc
