# -*- coding: utf-8 -*-
"""七猫（qimao）接口签名：全项目唯一实现。

历史上签名逻辑存在两份：

* ``SLQimao.book``（CLI 内核自带，``sign_url`` / ``get_headers``）
* ``gui.qimao_core``（GUI 侧重写，``sign_params`` / ``get_headers``）

两者算法逐字等价（同样的 SIGN_KEY、同样的版本列表与顺序、同样的字典序
``k=v`` 拼接 + MD5），但维护时要同时改两处，容易出现"改一处、另一处签名
失效"的隐性故障。本模块把这份逻辑收敛为单一来源，GUI 的搜索 / 详情 / 目录
与内核下载都从这里取。

CLI 侧的 ``SLQimao`` 保持原样（不参与打包），以保证命令行版本行为不变。

算法：参数按 key 字典序拼接成 ``k1=v1k2=v2...``，末尾追加 ``SIGN_KEY``，
取 MD5 十六进制摘要作为 ``sign``。
"""

from __future__ import annotations

import hashlib
import random
from typing import Any, Dict

# 签名盐值，与七猫 App 客户端一致
SIGN_KEY = "d3dGiJc651gSQ8w1"

# App 版本列表：``get_headers`` 以书号为随机种子从这里挑一个。
# 顺序必须与 SLQimao 内核保持一致——顺序变化会改变生成的请求头，
# 进而影响接口的兼容性判断。
VERSION_LIST = [
    "73720", "73700", "73620", "73600", "73500", "73420", "73400",
    "73328", "73325", "73320", "73300", "73220", "73200", "73100",
    "73000", "72900", "72820", "72800", "70720", "62010", "62112",
]


def _sign(fields: Dict[str, Any]) -> str:
    """对一组字段按字典序签名，返回 MD5 十六进制串。"""
    keys = sorted(fields.keys())
    raw = "".join(f"{k}={fields[k]}" for k in keys) + SIGN_KEY
    return hashlib.md5(raw.encode()).hexdigest()


def sign_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """为 URL 参数追加 ``sign`` 字段。

    不修改传入的字典（返回新字典），避免调用方被"原地改参"绊倒。
    """
    signed = dict(params)
    signed["sign"] = _sign(signed)
    return signed


def get_headers(book_id: Any) -> Dict[str, str]:
    """按书号生成带签名的 App 请求头。

    以书号为随机种子，保证同一本书每次拿到的 App 版本一致（接口侧校验需要）。
    """
    random.seed(str(book_id))
    version = random.choice(VERSION_LIST)
    headers: Dict[str, str] = {
        "AUTHORIZATION": "",
        "app-version": version,
        "application-id": "com.****.reader",
        "channel": "unknown",
        "net-env": "1",
        "platform": "android",
        "qm-params": "",
        "reg": "0",
    }
    headers["sign"] = _sign(headers)
    return headers
