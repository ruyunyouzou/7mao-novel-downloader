# -*- coding: utf-8 -*-
"""文库8 官方正版高清封面检测脚本（用户本机运行）。

沙箱出口 IP 连不上 Bangumi 任何域名，所以官方大图通道没法在开发环境真机
验证。这个脚本在你本机跑，直接回答三个问题：

1. 你本机能不能连上 Bangumi API / 图片域？
2. 书名能不能严格匹配到 Bangumi 书籍条目（单本 / 批量抽样）？
3. 大图能不能下载、尺寸多少？

用法（命令行）：
    python 文库8官方封面检测.py                      # 默认测天使那本
    python 文库8官方封面检测.py "书名"                # 测指定一本
    python 文库8官方封面检测.py --batch               # 抽 12 本代表书批量测

运行完会在同目录生成「文库8封面检测结果.txt」，把这个文件发回来即可。
只读取网络与本地缓存，不修改软件数据、不上传任何信息。
"""
import io
import json
import os
import sys
import time
import urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

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
# 搜索只走官方域名：第三方镜像的 search 忽略关键词，不能按书名匹配。
SEARCH_BASES = ("https://api.bgm.tv", "https://api.bangumi.one")
RESULT_PATH = os.path.join(ROOT, "文库8封面检测结果.txt")

_LOG = []


def log(msg=""):
    line = str(msg)
    print(line)
    _LOG.append(line)


def save_result():
    try:
        with open(RESULT_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(_LOG) + "\n")
        print(f"\n[已保存] 完整结果：{RESULT_PATH}  —— 把这个文件发回给我即可。")
    except Exception as e:  # noqa: BLE001
        print(f"[保存失败] {e}；请直接复制终端输出发回。")


def reachable_bases() -> list:
    ok = []
    for base in BASES:
        try:
            r = requests.get(f"{base}/v0/subjects/1", timeout=8,
                             headers={"User-Agent": UA, "Accept": "application/json"})
            log(f"[API] {base}: HTTP {r.status_code}")
            if r.status_code == 200:
                ok.append(base)
        except Exception as e:  # noqa: BLE001
            log(f"[API] {base}: 不可达 ({type(e).__name__})")
    return ok


def match_one(OC, title: str, search_bases):
    """返回 (sid, large, items, variants)；未命中返回 (None, ...)。

    Bangumi v0 搜索是 POST + JSON body；GET 会返回 405。
    """
    variants = OC.title_variants(title)
    for base in search_bases:
        for kw in variants:
            try:
                r = requests.post(
                    base + "/v0/search/subjects",
                    json={"keywords": kw, "type": 1, "limit": 10},
                    timeout=10,
                    headers={"User-Agent": UA, "Accept": "application/json"},
                )
                if r.status_code != 200:
                    log(f"[搜索] {base.split('/')[2]} HTTP {r.status_code}，换下一个域名")
                    break
                items = (r.json().get("data") or [])
                log(f"[搜索] {base.split('/')[2]} 关键词「{kw[:24]}」→ {len(items)} 条候选")
                sid, large = OC._best_match(items, title)
                if sid:
                    return sid, large, items, variants
            except Exception as exc:  # noqa: BLE001
                log(f"[搜索] {base.split('/')[2]} 失败：{type(exc).__name__}")
                break
    return None, "", [], variants


def large_ok(OC, large: str):
    """返回 (host, size, nbytes)；全部失败返回 None。"""
    for url in OC._mirror_urls(large):
        try:
            r = requests.get(url, timeout=10, headers={
                "User-Agent": UA, "Referer": "https://bgm.tv/"})
            im = Image.open(io.BytesIO(r.content))
            if im.size[0] >= 300 and im.size[1] >= 420:
                return url.split("/")[2], im.size, len(r.content)
        except Exception:  # noqa: BLE001
            continue
    return None


def probe(title: str, OC, search_bases) -> dict:
    log("=" * 70)
    log(f"检测书名：{title}")
    log("=" * 70)

    sid, large, items, variants = match_one(OC, title, search_bases)
    log(f"[匹配] 书名变体：{variants}")
    if not sid:
        log("[结论] 严格匹配未命中——为避免换错封面，不会替换。软件回退文库8官方小图。")
        return {"title": title, "matched": False, "large": False}

    hit = next((x for x in items if str(x.get("id")) == sid), {})
    log(f"[匹配成功] subject id={sid}")
    log(f"           日文名: {hit.get('name')}")
    log(f"           中文名: {hit.get('name_cn')}")

    got = large_ok(OC, large)
    if got:
        host, size, nbytes = got
        log(f"[大图成功] host={host} 尺寸={size} {nbytes} 字节")
        log("[结论] 官方正版大图可用，软件会自动缓存并显示。")
        return {"title": title, "matched": True, "large": True,
                "sid": sid, "size": size}
    log("[结论] 大图下载/尺寸不达标，软件回退文库8官方小图。")
    return {"title": title, "matched": True, "large": False, "sid": sid}


def sample_titles() -> list:
    """从随包索引抽样：带别名 / 短标题 / 长标题 各 4 本，去重。"""
    path = os.path.join(ROOT, "src", "gui", "wenku8_core", "data", "wenku_catalog.json")
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log(f"[索引读取失败] {e}")
        return [DEFAULT_TITLE]

    rows = []
    for page in (data.get("pages") or {}).values():
        for it in page or []:
            t = str(it.get("title", "")).strip()
            if t:
                rows.append(t)

    alias = [t for t in rows if ("(" in t or "（" in t)]
    short = [t for t in rows if len(t) <= 7 and "(" not in t and "（" not in t]
    long_ = [t for t in rows if len(t) >= 18 and "(" not in t and "（" not in t]

    picked, seen = [], set()
    for group, n in ((alias, 4), (short, 4), (long_, 4)):
        count = 0
        step = max(1, len(group) // n) if group else 1
        for i in range(0, len(group), step):
            if count >= n:
                break
            t = group[i]
            if t not in seen:
                seen.add(t)
                picked.append(t)
                count += 1
        for t in group:
            if count >= n:
                break
            if t not in seen:
                seen.add(t)
                picked.append(t)
                count += 1

    log(f"[抽样] 索引共 {len(rows)} 本，抽取 {len(picked)} 本代表书"
        f"（带别名/短标题/长标题各 4 本）")
    return picked or [DEFAULT_TITLE]


def batch(OC, search_bases) -> None:
    titles = sample_titles()
    results = []
    for i, t in enumerate(titles, 1):
        log("")
        log(f"---- 第 {i}/{len(titles)} 本 ----")
        results.append(probe(t, OC, search_bases))
        time.sleep(0.8)  # 温和限速，避免 Bangumi 风控

    matched = [r for r in results if r["matched"]]
    big = [r for r in results if r["large"]]
    log("")
    log("=" * 70)
    log("批量验证汇总")
    log("=" * 70)
    log(f"严格匹配成功：{len(matched)}/{len(results)}")
    log(f"官方大图可用：{len(big)}/{len(results)}")
    miss = [r["title"] for r in results if not r["matched"]]
    nobig = [r["title"] for r in results if r["matched"] and not r["large"]]
    if miss:
        log("未匹配书名（软件会回退小图，不换错）：")
        for t in miss:
            log(f"  - {t}")
    if nobig:
        log("匹配到但大图不可用：")
        for t in nobig:
            log(f"  - {t}")


def main():
    log("文库8 官方正版高清封面 · 本机真机验证")
    log("=" * 70)
    bases = reachable_bases()
    if not bases:
        log("\n[结论] 本机连不上任何 Bangumi API——官方大图通道在你这台机器上不可用，")
        log("       软件会自动回退文库8官方小图（217×310，仍是官方日版封面）。")
        log("       如需使用，请配置代理或在能访问 bgm.tv 的网络环境下运行。")
        save_result()
        return

    # 只有官方域名能搜索；第三方镜像仅能取详情/图片。
    search_bases = [b for b in bases if b in SEARCH_BASES]
    if not search_bases:
        log("\n[结论] 本机只有第三方镜像可达、连不上官方搜索接口。")
        log("       镜像的搜索不支持按书名过滤，官方大图匹配无法工作；")
        log("       软件会回退文库8官方小图。请配置代理或换能访问 bgm.tv 的网络。")
        save_result()
        return

    from gui.wenku8_core import wenku8_official_cover as OC

    args = sys.argv[1:]
    if args and args[0].lower() in ("--batch", "-b", "batch"):
        batch(OC, search_bases)
    else:
        title = args[0] if args else DEFAULT_TITLE
        probe(title, OC, search_bases)
    save_result()


if __name__ == "__main__":
    main()
