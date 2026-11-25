# -*- coding: utf-8 -*-
"""xiaocse/Fanqie-novel-Downloader 上游实现的隔离包装（内置核心层）。

本目录内 request_handler.py / config.py 均为上游原样文件，禁止手改。
上游更新时运行同目录的 sync_upstream.py 覆盖即可，本适配文件无需改动。

上游 request_handler.py 使用 ``from config import CONFIG`` 的顶层导入。
这里用标准包相对导入加载本包 config，并在导入 handler 期间临时把它别名到
sys.modules['config']，导入完成后立即恢复原值：
既不污染全局 config 模块名，也不需要修改上游源码一行。

采用标准包导入（而非文件路径 importlib）是为了兼容 PyInstaller：
打包后模块位于 PYZ 内，不存在可读取的物理 .py 文件。
"""

from __future__ import annotations

import sys
from typing import Any

# 先加载本包 config，临时别名到 sys.modules['config'] 满足上游顶层导入
from . import config as _CONFIG

_PREV_CONFIG = sys.modules.get("config")
sys.modules["config"] = _CONFIG
try:
    from . import request_handler as _HANDLER
finally:
    if _PREV_CONFIG is None:
        sys.modules.pop("config", None)
    else:
        sys.modules["config"] = _PREV_CONFIG

RequestHandler: Any = _HANDLER.RequestHandler
CookieGenerationError: Any = _HANDLER.CookieGenerationError
CONFIG: Any = _CONFIG.CONFIG

__all__ = ["RequestHandler", "CookieGenerationError", "CONFIG"]
