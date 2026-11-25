# -*- coding: utf-8 -*-
"""xiaocse/Fanqie-novel-Downloader 上游实现的隔离包装。

本目录内 request_handler.py / config.py 均为上游原样文件，禁止手改。
上游更新时运行同目录的 sync_upstream.py 覆盖即可，本适配文件无需改动。

上游 request_handler.py 使用 ``from config import CONFIG`` 的顶层导入，
这里用 importlib 以唯一模块名加载并临时注入 sys.modules，加载后立即恢复，
既不污染全局 config 模块名，也不需要修改上游源码一行。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from types import ModuleType
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_unique(name: str, filename: str) -> ModuleType:
    path = os.path.join(_HERE, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载上游模块: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


# 先加载上游 config，临时别名到 sys.modules['config'] 满足其顶层导入
_CONFIG = _load_unique("fanqie_upstream_config", "config.py")
_PREV_CONFIG = sys.modules.get("config")
sys.modules["config"] = _CONFIG
try:
    _HANDLER = _load_unique("fanqie_upstream_request_handler", "request_handler.py")
finally:
    if _PREV_CONFIG is None:
        sys.modules.pop("config", None)
    else:
        sys.modules["config"] = _PREV_CONFIG

RequestHandler: Any = _HANDLER.RequestHandler
CookieGenerationError: Any = _HANDLER.CookieGenerationError
CONFIG: Any = _CONFIG.CONFIG

__all__ = ["RequestHandler", "CookieGenerationError", "CONFIG"]
