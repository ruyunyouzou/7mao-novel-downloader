# -*- coding: utf-8 -*-
"""章首题图：从内置原神资源库取无文字角色图，烘焙渐隐，注入 EPUB。

图库（``library``）：
* ``genshin``：原神七神/月神/五星祈愿立绘（透明 PNG，2048×1024，
              assets/chapter_hero/）——**章首题图**，插在章节标题上方。
  四星角色图已从资源库移除，仅保留神明（archon/moon）与五星（five）。

设计要点
--------
* 渐隐**烘焙进 PNG 的 alpha 通道**，而不是依赖 CSS mask —— 苹果 Books、
  静读天下等主流 EPUB 阅读器对 CSS mask 支持不一；把渐隐预先画进图片，任何
  阅读器打开都能看到自然融入纸张的效果（透明处露出阅读器底色）。
* 原神题图本身透明，底部渐隐到透明（题图在标题上方，底部融入下方纸张）。
* 图库资源均为官方美术素材，版权归版权方，仅用于个人非商用 EPUB 阅读。
"""
from __future__ import annotations

import io
import os
from typing import Dict, List, Optional, Tuple

# ------------------------------------------------------------------ 图库定义
LIBRARIES: Dict[str, dict] = {
    "genshin": {
        "label": "原神·祈愿立绘",
        "dir": "assets/chapter_hero",
        "fade_start": 0.68,
        "fade_end": 0.96,
        "width": 1100,
        "priority": [
            "Venti", "Zhongli", "Shougun", "Nahida", "Furina", "Mavuika",
            "Columbina",
        ],
        "names": {
            "Venti": ("温迪", "archon"), "Zhongli": ("钟离", "archon"),
            "Shougun": ("雷电将军", "archon"), "Nahida": ("纳西妲", "archon"),
            "Furina": ("芙宁娜", "archon"), "Mavuika": ("玛薇卡", "archon"),
            "Columbina": ("哥伦比娅·月神", "moon"),
            "Albedo": ("阿贝多", "five"), "Arlecchino": ("阿蕾奇诺", "five"),
            "Ayaka": ("神里绫华", "five"), "Ayato": ("神里绫人", "five"),
            "Chasca": ("恰斯卡", "five"), "Chiori": ("千织", "five"),
            "Citlali": ("茜特菈莉", "five"), "Clorinde": ("克洛琳德", "five"),
            "Cyno": ("赛诺", "five"), "Dehya": ("迪希雅", "five"),
            "Diluc": ("迪卢克", "five"), "Emilie": ("艾梅莉埃", "five"),
            "Eula": ("优菈", "five"), "Ganyu": ("甘雨", "five"),
            "Hutao": ("胡桃", "five"), "Itto": ("荒泷一斗", "five"),
            "Kazuha": ("枫原万叶", "five"), "Keqing": ("刻晴", "five"),
            "Kinich": ("基尼奇", "five"), "Kokomi": ("珊瑚宫心海", "five"),
            "Mona": ("莫娜", "five"), "Mualani": ("玛拉妮", "five"),
            "Navia": ("娜维娅", "five"), "Neuvillette": ("那维莱特", "five"),
            "Nilou": ("妮露", "five"), "Qiqi": ("七七", "five"),
            "Shenhe": ("申鹤", "five"), "Tartaglia": ("达达利亚", "five"),
            "Tighnari": ("提纳里", "five"), "Wanderer": ("流浪者", "five"),
            "Wriothesley": ("莱欧斯利", "five"), "Xiao": ("魈", "five"),
            "Xilonen": ("希诺宁", "five"), "Yae": ("八重神子", "five"),
            "Yoimiya": ("宵宫", "five"),
        },
    },
}

# 用途固定：题图=原神
HERO_LIBRARY = "genshin"

DEFAULT_LIBRARY = "genshin"


def library_keys() -> List[str]:
    return list(LIBRARIES.keys())


def library_label(library: str) -> str:
    return LIBRARIES.get(library, LIBRARIES[DEFAULT_LIBRARY])["label"]


def _lib(library: str) -> dict:
    return LIBRARIES.get(library) or LIBRARIES[DEFAULT_LIBRARY]


# 兼容旧代码：模块级 HERO_DIR / HERO_NAMES / _PRIORITY（原神库）
HERO_DIR = LIBRARIES[DEFAULT_LIBRARY]["dir"]
HERO_NAMES = LIBRARIES[DEFAULT_LIBRARY]["names"]
_PRIORITY = LIBRARIES[DEFAULT_LIBRARY]["priority"]

# 缓存：processed png bytes，key = (library, 文件名, 宽度, fade_start, fade_end, fade_side)
_CACHE: Dict[Tuple[str, str, int, float, float, str], bytes] = {}


def hero_dir(library: str = DEFAULT_LIBRARY) -> str:
    """返回指定图库的资源目录绝对路径，兼容源码与打包环境。"""
    rel = _lib(library)["dir"]
    try:
        from .resource_path import resource_path
        return resource_path(*rel.split("/"))
    except Exception:  # noqa: BLE001
        from gui.resource_path import resource_path
        return resource_path(*rel.split("/"))


def available_heroes(library: str = DEFAULT_LIBRARY,
                     min_year: int = 0) -> List[dict]:
    """列出原神图库所有可用题图。

    每项：{'key': 文件名标识, 'zh': 显示名, 'group': 分组, 'file': 文件名,
          'library': 图库key, 'year': 0}
    ``min_year`` 保留参数以兼容旧调用，现无火影库故忽略。
    """
    cfg = _lib(library)
    d = hero_dir(library)
    result: List[dict] = []
    if not os.path.isdir(d):
        return result
    names = dict(cfg["names"])
    for fn in sorted(os.listdir(d)):
        if not fn.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        key = os.path.splitext(fn)[0]
        zh, group = names.get(key, (key, "other"))
        result.append({"key": key, "zh": zh, "group": group, "file": fn,
                       "library": library, "year": 0})

    priority = cfg.get("priority") or []
    order = {k: i for i, k in enumerate(priority)}
    result.sort(key=lambda x: (0 if x["key"] in order else 1,
                               order.get(x["key"], 999), x["key"]))
    return result


def pick_hero(chapter_index: int, heroes: List[dict], mode: str = "rotate",
              fixed: str = "") -> Optional[dict]:
    """按策略为第 chapter_index 章（0 基）选题图。

    mode: rotate=轮换 / fixed=固定某张 / none=不用
    fixed: mode=fixed 时指定的题图 key
    """
    if not heroes or mode == "none":
        return None
    if mode == "fixed" and fixed:
        for h in heroes:
            if h["key"] == fixed:
                return h
        return heroes[0]
    return heroes[chapter_index % len(heroes)]


# ------------------------------------------------------------------ 智能选题预设
# 规则（用户定义）：
#   章首题图：每本书随机抽 30 张，其中必含全部 7 张神明（6 archon + 月神），
#             其余 23 张从非神明随机补齐；按章轮换。
# 用书标识做随机种子，保证同一本书重下时选题一致（可复现）。
SMART_HERO_COUNT = 30
SMART_GOD_COUNT = 7       # 温迪/钟离/雷电将军/纳西妲/芙宁娜/玛薇卡/月神
GOD_GROUPS = ("archon", "moon")


def smart_hero_pool(book_key: str, heroes: List[dict]) -> List[dict]:
    """题图智能池：7 张神明全取 + 非神明随机补到 30 张。"""
    import random
    rng = random.Random("hero|" + str(book_key))
    gods = [h for h in heroes if h.get("group") in GOD_GROUPS]
    others = [h for h in heroes if h.get("group") not in GOD_GROUPS]
    rng.shuffle(others)
    need = max(0, SMART_HERO_COUNT - len(gods))
    pool = list(gods) + others[:need]
    # 神明打散到各章，避免前 7 章全是神明
    rng.shuffle(pool)
    return pool


def make_faded_png(src_path: str, width: int = 0,
                   fade_start: float = 0.0, fade_end: float = 0.0,
                   library: str = DEFAULT_LIBRARY,
                   fade_side: str = "") -> bytes:
    """读取角色图，缩放，并把渐隐烘焙进 alpha 通道。

    ``fade_side`` 决定渐隐方向（未传时取图库 ``fade_side``，缺省 ``bottom``）：
    * ``bottom``（题图）：底部从实心淡出到透明，融入下方纸张；
    * ``top``：顶部从透明淡入到实心，从上方正文自然浮现，底部保持实心（通用能力，当前题图库使用 ``bottom``）。

    返回 PNG 字节。渐隐到透明，露出阅读器底色，因此深浅主题通用。
    未显式传宽高/渐隐参数时，使用图库默认值。
    """
    cfg = _lib(library)
    if not width:
        width = cfg["width"]
    if fade_start <= 0:
        fade_start = cfg["fade_start"]
    if fade_end <= 0:
        fade_end = cfg["fade_end"]
    if not fade_side:
        fade_side = cfg.get("fade_side", "bottom") or "bottom"
    top_fade = (fade_side == "top")

    cache_key = (library, os.path.basename(src_path), width, fade_start,
                 fade_end, fade_side)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    from PIL import Image

    im = Image.open(src_path).convert("RGBA")
    w, h = im.size
    if width and w != width:
        nh = max(1, int(h * width / w))
        im = im.resize((width, nh), Image.LANCZOS)
    w, h = im.size

    alpha = im.getchannel("A")
    px = alpha.load()
    for y in range(h):
        # t 表示「距渐隐侧边缘的归一化距离」：
        # bottom 时 t=y/h（顶部实心、底部透明）；
        # top 时 t=(h-1-y)/h（底部实心、顶部透明）。
        t = ((h - 1 - y) / h) if top_fade else (y / h)
        if t <= fade_start:
            continue
        if t >= fade_end:
            factor = 0.0
        else:
            factor = 1.0 - (t - fade_start) / (fade_end - fade_start)
        if factor <= 0.0:
            for x in range(w):
                px[x, y] = 0
        else:
            for x in range(w):
                px[x, y] = int(px[x, y] * factor)
    im.putalpha(alpha)

    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    _CACHE[cache_key] = data
    return data


def hero_css() -> str:
    """题图相关的 CSS。渐隐已烘焙进图片，这里只负责布局。"""
    return (
        "div.chapter-hero { text-align: center; margin: 0.2em 0 0; "
        "padding: 0; line-height: 0; }\n"
        "div.chapter-hero img { display: block; width: 100%; max-width: 100%; "
        "height: auto; margin: 0 auto; border: none; }\n"
        # 标题紧跟题图时收紧上边距，让图题更紧凑（普通无题图章节不受影响）
        "div.chapter-hero + h2.chapter-title { margin-top: 0.7em; }\n"
    )
