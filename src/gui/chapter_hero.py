# -*- coding: utf-8 -*-
"""章首题图 + 章末尾图：从内置资源库取无文字角色图，烘焙渐隐，注入 EPUB。

两套图库各司其职（``library``）：
* ``genshin``：原神七神/月神/五星祈愿立绘（透明 PNG，2048×1024，
              assets/chapter_hero/）——**章首题图**，插在章节标题上方。
* ``naruto`` ：火影忍者手游高级招募 S/A 忍者横屏带字海报（2.2:1，1200×545，
              assets/chapter_tail_naruto_poster/）——**章末尾图**，插在正文之后。
              海报 = 官方 bg4 无字画面裁横屏 + 本地叠加毛笔书法角色名、
              称号印章、星级与 S/A 品级标（保留文字，非去字）。

设计要点
--------
* 渐隐**烘焙进 PNG 的 alpha 通道**，而不是依赖 CSS mask —— 苹果 Books、
  静读天下等主流 EPUB 阅读器对 CSS mask 支持不一；把渐隐预先画进图片，任何
  阅读器打开都能看到自然融入纸张的效果（透明处露出阅读器底色）。
* 原神题图本身透明，底部渐隐到透明（题图在标题上方，底部融入下方纸张）；
  火影尾图是实心暗色 JPG，转成 RGBA 后把**顶部** alpha 渐隐到 0，让画面从
  上方正文处自然浮现，底部保持实心收尾，深浅纸张都自然（已实测）。
* 渐隐方向由每条图库的 ``fade_side`` 决定：题图 ``bottom`` / 尾图 ``top``。
* 火影横屏图由官方 750×670 无字方图智能定位角色面部后裁成 2.2:1 横条
  （1200×545），无书法字/S 标识/水印，角色面部完整，无拉伸变形。
* 图库资源均为官方美术素材，版权归各自版权方，仅用于个人非商用 EPUB 阅读。
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
            "Barbara": ("芭芭拉", "four"), "Beidou": ("北斗", "four"),
            "Bennett": ("班尼特", "four"), "Candace": ("坎蒂丝", "four"),
            "Charlotte": ("夏洛蒂", "four"), "Chasca": ("恰斯卡", "five"),
            "Chevreuse": ("夏沃蕾", "four"), "Chiori": ("千织", "five"),
            "Chongyun": ("重云", "four"), "Citlali": ("茜特菈莉", "five"),
            "Clorinde": ("克洛琳德", "five"), "Collei": ("柯莱", "four"),
            "Cyno": ("赛诺", "five"), "Dehya": ("迪希雅", "five"),
            "Diluc": ("迪卢克", "five"), "Diona": ("迪奥娜", "four"),
            "Dori": ("多莉", "four"), "Emilie": ("艾梅莉埃", "five"),
            "Eula": ("优菈", "five"), "Faruzan": ("法露珊", "four"),
            "Fischl": ("菲谢尔", "four"), "Freminet": ("菲米尼", "four"),
            "Ganyu": ("甘雨", "five"), "Hutao": ("胡桃", "five"),
            "Itto": ("荒泷一斗", "five"), "Kaeya": ("凯亚", "four"),
            "Kaveh": ("卡维", "four"), "Kazuha": ("枫原万叶", "five"),
            "Keqing": ("刻晴", "five"), "Kinich": ("基尼奇", "five"),
            "Kokomi": ("珊瑚宫心海", "five"), "Lisa": ("丽莎", "four"),
            "Mona": ("莫娜", "five"), "Mualani": ("玛拉妮", "five"),
            "Navia": ("娜维娅", "five"), "Neuvillette": ("那维莱特", "five"),
            "Nilou": ("妮露", "five"), "Ningguang": ("凝光", "four"),
            "Qiqi": ("七七", "five"), "Razor": ("雷泽", "four"),
            "Rosaria": ("罗莎莉亚", "four"), "Sara": ("九条裟罗", "four"),
            "Sayu": ("早柚", "four"), "Shenhe": ("申鹤", "five"),
            "Sigewinne": ("希格雯", "four"), "Sucrose": ("砂糖", "four"),
            "Tartaglia": ("达达利亚", "five"), "Tighnari": ("提纳里", "five"),
            "Wanderer": ("流浪者", "five"), "Wriothesley": ("莱欧斯利", "five"),
            "Xiangling": ("香菱", "four"), "Xiao": ("魈", "five"),
            "Xilonen": ("希诺宁", "five"), "Xingqiu": ("行秋", "four"),
            "Xinyan": ("辛焱", "four"), "Yae": ("八重神子", "five"),
            "Yaoyao": ("瑶瑶", "four"), "Yoimiya": ("宵宫", "five"),
        },
    },
    "naruto": {
        "label": "火影·高招 S/A（横屏尾图）",
        "dir": "assets/chapter_tail_naruto_poster",
        "fade_side": "top",   # 尾图：顶部渐隐融入正文，底部保持实心
        "fade_start": 0.70,
        "fade_end": 0.98,
        "width": 1000,
        "priority": [],
        "names": {},  # 名字从 manifest.json 读取（188 个，按 id 命名）
    },
}

# 用途固定：题图=原神，尾图=火影（横屏）
HERO_LIBRARY = "genshin"
TAIL_LIBRARY = "naruto"

DEFAULT_LIBRARY = "genshin"


def library_keys() -> List[str]:
    return list(LIBRARIES.keys())


def library_label(library: str) -> str:
    return LIBRARIES.get(library, LIBRARIES[DEFAULT_LIBRARY])["label"]


def _lib(library: str) -> dict:
    return LIBRARIES.get(library) or LIBRARIES[DEFAULT_LIBRARY]


def _load_naruto_meta(library: str) -> Dict[str, dict]:
    """火影库的元数据（名字/品级/年份）从 manifest.json 动态读取。"""
    result: Dict[str, dict] = {}
    if library != "naruto":
        return result
    try:
        import json
        mf_path = os.path.join(hero_dir(library), "manifest.json")
        if os.path.isfile(mf_path):
            with open(mf_path, encoding="utf-8") as _mf:
                data = json.load(_mf)
            for h in data.get("heroes", []):
                key = str(h.get("key", ""))
                info = {
                    "zh": str(h.get("zh", key)),
                    "rank": str(h.get("rank", "A")).lower(),
                    "year": int(h.get("year", 0) or 0),
                    "date": str(h.get("date", "") or ""),
                }
                result[key] = info
                # 海报文件名形如 90026_S_猿飞日斩，用数字 id 也建一份索引，
                # 供 available_heroes 以文件 key 查询年份。
                fid = str(h.get("id", "") or key).split("_")[0]
                result[fid] = info
                result[str(h.get("file", "")).rsplit(".", 1)[0]] = info
    except Exception as exc:  # noqa: BLE001
        from .logutil import logger
        logger.warning("火影题图 manifest 读取失败，年份/品级筛选可能不完整",
                       exc=exc, tag="chapter_hero")
    return result


def _load_naruto_names(library: str) -> Dict[str, Tuple[str, str]]:
    """兼容旧接口：火影库名字映射。"""
    return {k: (v["zh"], v["rank"]) for k, v in _load_naruto_meta(library).items()}


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
    """列出指定图库所有可用题图。

    每项：{'key': 文件名标识, 'zh': 显示名, 'group': 分组, 'file': 文件名,
          'library': 图库key, 'year': 上架年份(仅火影有值)}
    min_year: 火影库专用，>0 时只返回上架年份 >= 该值的忍者（如 2020）。
    """
    cfg = _lib(library)
    d = hero_dir(library)
    result: List[dict] = []
    if not os.path.isdir(d):
        return result
    names = dict(cfg["names"])
    meta: Dict[str, dict] = {}
    if library == "naruto":
        meta = _load_naruto_meta(library)
        names.update({k: (v["zh"], v["rank"]) for k, v in meta.items()})
    for fn in sorted(os.listdir(d)):
        if not fn.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        key = os.path.splitext(fn)[0]
        zh, group = names.get(key, (key, "other"))
        year = 0
        if library == "naruto":
            # 火影文件名形如 90272_S_猿飞日斩，显示名拼接称号与品级
            parts = key.split("_")
            zh = parts[2] if len(parts) >= 3 else zh
            rank = parts[1] if len(parts) >= 2 else group
            zh = f"{zh}·{rank}"
            group = rank.lower()
            year = meta.get(key, {}).get("year", 0)
            if min_year and year < min_year:
                continue
        result.append({"key": key, "zh": zh, "group": group, "file": fn,
                       "library": library, "year": year})

    priority = cfg.get("priority") or []
    order = {k: i for i, k in enumerate(priority)}
    if library == "naruto":
        # S 优先，再按 id 排
        result.sort(key=lambda x: (0 if x["group"] == "s" else 1, x["key"]))
    else:
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
#   章末尾图：从 2020 年及之后的高招 S/A 随机抽 10 张；按章轮换。
# 用书标识做随机种子，保证同一本书重下时选题一致（可复现）。
SMART_HERO_COUNT = 30
SMART_GOD_COUNT = 7       # 温迪/钟离/雷电将军/纳西妲/芙宁娜/玛薇卡/月神
SMART_TAIL_COUNT = 10
SMART_TAIL_MIN_YEAR = 2020
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


def smart_tail_pool(book_key: str, heroes: List[dict]) -> List[dict]:
    """尾图智能池：优先 2020+ 高招，随机抽 10 张（不足则按可用数）。"""
    import random
    rng = random.Random("tail|" + str(book_key))
    recent = [h for h in heroes if h.get("year", 0) >= SMART_TAIL_MIN_YEAR]
    if len(recent) < SMART_TAIL_COUNT:
        recent = list(heroes)  # 兜底：数据缺失时用全库
    pool = list(recent)
    rng.shuffle(pool)
    return pool[:SMART_TAIL_COUNT]


def make_faded_png(src_path: str, width: int = 0,
                   fade_start: float = 0.0, fade_end: float = 0.0,
                   library: str = DEFAULT_LIBRARY,
                   fade_side: str = "") -> bytes:
    """读取角色图，缩放，并把渐隐烘焙进 alpha 通道。

    ``fade_side`` 决定渐隐方向（未传时取图库 ``fade_side``，缺省 ``bottom``）：
    * ``bottom``（题图）：底部从实心淡出到透明，融入下方纸张；
    * ``top``（尾图）：顶部从透明淡入到实心，从上方正文自然浮现，底部保持实心。

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


def tail_css() -> str:
    """章末尾图相关的 CSS。渐隐已烘焙进图片，这里只负责布局。

    尾图插在正文之后，整图居中、满宽显示；**顶部渐隐**已烘焙进 alpha，
    画面从上方正文处自然浮现融入纸张，底部保持实心收尾，不遮挡任何文字。
    """
    return (
        "div.chapter-tail { text-align: center; margin: 1.4em 0 0; "
        "padding: 0; line-height: 0; }\n"
        "div.chapter-tail img { display: block; width: 100%; max-width: 100%; "
        "height: auto; margin: 0 auto; border: none; }\n"
    )
