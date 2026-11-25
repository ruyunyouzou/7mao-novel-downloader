#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键同步 xiaocse/Fanqie-novel-Downloader 上游实现。

用法：
    python3 src/plugins/vendor/fanqie_upstream/sync_upstream.py

行为：
    1. 查询上游 main 分支最新 commit；
    2. 仅覆盖 UPSTREAM_FILES 列出的核心逻辑文件（不碰任何适配代码）；
    3. 把新 SHA / 时间写入 UPSTREAM_INFO.md；
    4. 输出差异文件清单，方便判断是否会影响内核适配层。

设计目的：上游实现与本项目的适配层彻底分离，上游更新时无需手工改插件代码。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

REPO = "xiaocse/Fanqie-novel-Downloader"
BRANCH = "main"
HERE = os.path.dirname(os.path.abspath(__file__))

# 只同步内核运行所需的核心逻辑；GUI / 书籍管理等与本项目无关的文件不同步。
UPSTREAM_FILES = ("request_handler.py", "config.py")


def _api(path: str) -> dict:
    url = f"https://api.github.com/repos/{REPO}/{path}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "7mao-fanqie-upstream-sync",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main() -> int:
    try:
        commit = _api(f"commits/{BRANCH}")
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 无法查询上游最新 commit: {exc}")
        return 1

    sha = str(commit.get("sha", ""))[:40]
    date = ""
    try:
        date = commit["commit"]["committer"]["date"]
    except Exception:  # noqa: BLE001
        pass
    message = str(commit.get("commit", {}).get("message", "")).splitlines()[0]

    changed = []
    for name in UPSTREAM_FILES:
        try:
            data = _api(f"contents/{name}?ref={sha}")
            content = base64.b64decode(data["content"]).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"✗ 拉取 {name} 失败: {exc}")
            return 1
        target = os.path.join(HERE, name)
        old = ""
        if os.path.isfile(target):
            with open(target, "r", encoding="utf-8") as fh:
                old = fh.read()
        if old != content:
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(content)
            changed.append(name)

    info = (
        "# 番茄内核上游实现·同步记录\n\n"
        f"- 上游仓库：https://github.com/{REPO}\n"
        f"- 许可证：MIT\n"
        f"- 同步分支：{BRANCH}\n"
        f"- 当前 commit：`{sha}`\n"
        f"- 提交时间：{date or '未知'}\n"
        f"- 提交说明：{message or '无'}\n"
        f"- 最近同步：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        "## 同步方式\n\n"
        "```bash\n"
        "python3 src/plugins/vendor/fanqie_upstream/sync_upstream.py\n"
        "```\n\n"
        "脚本只覆盖 `request_handler.py` / `config.py`，适配层 `__init__.py` "
        "和 `../fanqie_downloader_plugin.py` 不会被修改。\n\n"
        "## 目录约定\n\n"
        "- `request_handler.py`：上游原样核心接口（书籍信息 / 目录 / 正文）。\n"
        "- `config.py`：上游原样配置（请求超时、重试、UA）。\n"
        "- `__init__.py`：导入适配（不污染全局 `config` 模块名）。\n"
        "- 本目录任何上游文件都不要手工修改；需要改动请改适配层。\n\n"
        "## 已知风险与兜底\n\n"
        "上游 `down_text()` 指向第三方公开 API（`https://fanqie.tutuxka.top/`），"
        "并非番茄官方接口。实测该域名 DNS 已无解析，正文无法获取。\n\n"
        "因此适配层默认使用 `../fanqie_reader.py`：从番茄官网读者页 "
        "`window.__INITIAL_STATE__` 提取正文，解析 woff2 字体的 glyph gid 并用内置 "
        "`FONT_MAP` 还原被 PUA 替换的汉字。映射表来源："
        "`zhoulianglen/fanqiexiaoshuo-Download`（其源自 "
        "`romcere/fanqienovel-decryptor` / RChow 博客），按字体 URL 缓存，"
        "不依赖第三方 API。\n\n"
        "若上游日后修复正文接口，可在 DataStore 设置 "
        "`fanqie_text_backend=upstream` 切回上游实现；书籍信息与目录仍始终取自"
        "上游原样代码，随本目录同步自动更新。\n"
    )
    with open(os.path.join(HERE, "UPSTREAM_INFO.md"), "w", encoding="utf-8") as fh:
        fh.write(info)

    if changed:
        print(f"✓ 已同步到 {sha[:8]}（{date}）")
        print("  更新文件: " + ", ".join(changed))
        print("  请运行一次内核回归验证，确认适配层仍然兼容。")
    else:
        print(f"✓ 已是最新版本 {sha[:8]}（{date}），无需更新。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
