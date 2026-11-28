# -*- coding: utf-8 -*-
"""应用版本单一真源（Single Source of Truth）。

所有需要显示或比较版本号的地方都必须从这里导入，禁止在 UI / 更新模块
里再写死版本字符串，避免再次出现「标题栏 v1.0.0、CLI v4.0.2、更新检查 1.0.0」
三处不一致的问题。

版本号语义：沿用上游 CLI 的 4.x 主线（CLI 当前 v4.0.2），本 GUI 在其上做了
大量增强。发布新版时只需改 APP_VERSION 一处。
"""

from __future__ import annotations

APP_VERSION = "5.0.22"

# 展示用完整字符串（标题栏 / 关于 / 页脚统一）
VERSION_LABEL = f"v{APP_VERSION}"

# 更新检查使用的最小版本：低于此版本的客户端会被 manifest 判定为强制更新
# （当前仅作占位，实际以远端 manifest 的 min_version 字段为准）
MIN_SUPPORTED_VERSION = "4.0.2"


def version_tuple(text: str = APP_VERSION) -> tuple[int, int, int]:
    """把 'v4.1.0' / '4.1.0-beta' 解析为 (4, 1, 0)，失败返回 (0, 0, 0)。"""
    import re
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(text or ""))
    if not m:
        return (0, 0, 0)
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_newer(remote: str, current: str = APP_VERSION) -> bool:
    """远端版本是否高于本地版本。"""
    return version_tuple(remote) > version_tuple(current)
