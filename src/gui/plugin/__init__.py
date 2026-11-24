# -*- coding: utf-8 -*-
"""插件扩展机制：基类、挂载点、管理器。"""

from .base import Plugin, PluginMeta, Hook, DownloaderBase, DownloadResult
from .manager import PluginManager

__all__ = [
    "Plugin", "PluginMeta", "Hook", "DownloaderBase", "DownloadResult", "PluginManager",
]
