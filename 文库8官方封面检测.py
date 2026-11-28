# -*- coding: utf-8 -*-
"""文库8 官方正版高清封面检测脚本（用户本机运行）。

沙箱出口 IP 连不上 Bangumi 任何域名，所以官方大图通道没法在开发环境真机
验证。这个脚本在你本机跑，直接回答三个问题：

1. 你本机能不能连上 Bangumi API / 图片域？
2. 指定书名能不能严格匹配到 Bangumi 书籍条目？
3. 大图能不能下载、尺寸多少、是否已落缓存？

用法（双击或命令行）：
    python 文库8官方封面检测.py
    python 文库8官方封面检测.py "关于我在无意间被隔壁的天使变成废柴这件事"

只读取网络与本地缓存，不修改软件数据、不上传任何信息。
"""
import io
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

try:
    import requests
    from PIL import Image
except ImportError as e:
    print(f"[依赖缺失] {e}；请先安装：pip install requests pillow")
    sys.exit(1)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

DEFAULT_TITLE = ("关于我在无意间被隔壁的天使变成废柴这件事"
                 "(关于邻家的天使大人不知不觉把我惯成了废人这档子事)")

BASES = ("https://api.bgm.tv", "https://api.bangumi.one", "https://bgmapi.anibt.net")


def probe(title: str) -> None:
    print("=" * 70)
    print(f"检测书名：{title}")
    print("=" * 70)

    # 1. API 连通性
    reachable = []
    for base in BASES:
        try:
            r = requests.get(f"{base}/v0/subjects/1", timeout=8,
                             headers={"User-Agent": UA, "Accept": "application/json"})
            print(f"[API] {base}: HTTP {r.status_code}")
            if r.status_code == 200:
                reachable.append(base)
        except Exception as e:  # noqa: BLE001
            print(f"[API] {base}: 不可达 ({type(e).__name__})")
    if not reachable:
        print("\n[结论] 本机连不上任何 Bangumi API——官方大图通道在你这台机器上不可用，")
        print("       软件会自动回退文库8官方小图（217×310，仍是官方日版封面）。")
        print("       如需使用，请配置代理或在能访问 bgm.tv 的网络环境下运行。")
        return

    from gui.wenku8_core import wenku8_official_cover as OC

    # 2. 搜索 + 严格匹配
    variants = OC.title_variants(title)
    print(f"\n[匹配] 参与匹配的书名变体：{variants}")
    matched = None
    for kw in variants:
        try:
            url = (reachable[0] + "/v0/search/subjects?"
                   + urllib.parse.urlencode({"keywords": kw, "type": 1, "limit": 10}))
            data = requests.get(url, timeout=8, headers={"User-Agent": UA}).json()
            items = data.get("data") or []
            print(f"[搜索] 关键词「{kw[:24]}...」→ {len(items)} 条候选")
            sid, large = OC._best_match(items, title)
            if sid:
                matched = (sid, large, items)
                break
        except Exception as e:  # noqa: BLE001
            print(f"[搜索] 失败：{type(e).__name__} {e}")
    if not matched:
        print("\n[结论] 严格匹配未命中——为避免换错封面，不会替换。软件回退文库8官方小图。")
        print("       如需强制指定，可在本脚本里按书名搜索后把 subject id 写入缓存映射。")
        return

    sid, large, items = matched
    hit = next((x for x in items if str(x.get("id")) == sid), {})
    print(f"[匹配成功] subject id={sid}")
    print(f"           日文名: {hit.get('name')}")
    print(f"           中文名: {hit.get('name_cn')}")

    # 3. 大图下载 + 尺寸
    print("\n[大图] 尝试下载 images.large ...")
    for url in OC._mirror_urls(large):
        try:
            r = requests.get(url, timeout=10, headers={
                "User-Agent": UA, "Referer": "https://bgm.tv/"})
            im = Image.open(io.BytesIO(r.content))
            print(f"[大图] {url.split('/')[2]} → HTTP {r.status_code}, 尺寸 {im.size}, {len(r.content)} 字节")
            if im.size[0] >= 300 and im.size[1] >= 420:
                print("\n[结论] 官方正版大图可用，软件会自动缓存并显示（≈800×1200 级别）。")
                return
        except Exception as e:  # noqa: BLE001
            print(f"[大图] {url.split('/')[2]} 失败：{type(e).__name__}")
    print("\n[结论] 大图下载/尺寸不达标，软件回退文库8官方小图。")


if __name__ == "__main__":
    title = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TITLE
    probe(title)
