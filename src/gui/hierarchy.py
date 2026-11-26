# -*- coding: utf-8 -*-
"""统一目录层级模型：把三源（七猫/番茄/文库8）的章节目录归一为「卷/集/章」层级。

设计目标
--------
- 微信读书导入后能显示可折叠目录：卷 → 集 → 章（最多三级）。
- **自动适配层数**：数据里有几级就输出几级，绝不凭空造空层级。
    * 番茄/文库8 的目录接口显式给出卷（volume_name / vcss 分组行）→ 卷+章两级；
      章节标题里若再含「第X集」，升级为卷+集+章三级。
    * 七猫章节标题自带「第X卷 … 第X章 …」→ 从标题解析卷，最多两级。
    * 纯章节标题（无任何分组）→ 一层，行为与旧版完全一致（零回归）。
- **番外当卷**：标题以「番外 / 番外篇 / 特别篇 / 外传 / IF线 / 楔子」等开头的章节，
  统一提升为独立卷级，避免番外混在正文卷的章节堆里。

归一后的章节字典在原有 {id, title, index, url} 基础上新增：
    vol  : str  卷名（一级分组），空串表示无卷
    sub  : str  集名（二级分组），空串表示无集
    title: str  **纯章名**（去掉卷/集前缀后的章标题）

关键约束：title 被精简为纯章名，但「序号 index / id / url」完全不变，
因此下载、追更、章节范围选择、进度计算全部不受影响。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# 中文/阿拉伯数字片段（复用于各前缀）
_NUM = r"[零〇一二三四五六七八九十百千两0-9０-９]+"

# 卷级前缀：第X卷 / 第X篇 / 第X部 / 第X章卷(误) —— 只认卷/篇/部
_RE_VOL = re.compile(rf"^(第{_NUM}[卷篇部])\s*[：:、.．\-—]*\s*(.*)$")
# 集级标记：第X集（不锚定开头，因为可能出现在卷名之后）
_RE_SUB = re.compile(rf"(第{_NUM}集)\s*[：:、.．\-—]*\s*(.*)$")
# 章级标记：第X章/节/回（不锚定开头，用于从「篇→集→章」里切出纯章名）
_RE_CH = re.compile(rf"(第{_NUM}[章节回])\s*[：:、.．\-—]*\s*(.*)$")

# 番外/特辑类：视为独立卷。顺序敏感——长词在前，避免「番外篇」被「番外」先截短。
_SPECIAL_WORDS = (
    "番外篇", "番外", "特别篇", "特别章", "外传", "IF线", "if线",
    "楔子", "序章", "尾声", "后记", "终章", "彩蛋",
)
# 其中「序章/尾声/后记/终章/彩蛋」通常属于某卷内部的开篇/收尾，不独立成卷；
# 只有「番外/番外篇/特别篇/外传/IF线」才提升为卷。
_AS_VOL_WORDS = ("番外篇", "番外", "特别篇", "特别章", "外传", "IF线", "if线")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _is_special_vol(title: str) -> bool:
    """标题是否属于「番外类」——按用户要求当作独立卷。"""
    t = _clean(title)
    for w in _AS_VOL_WORDS:
        if t.startswith(w):
            return True
    return False


def parse_title(title: str) -> Tuple[str, str, str]:
    """从单个章节标题解析 (卷, 集, 纯章名)。

    用「标记位置」切分，支持任意组合形态，自动适配层数：
      「第一篇 一夜觉醒 第一集 深夜觉醒 第1章 罗峰」
          → ('第一篇 一夜觉醒', '第一集 深夜觉醒', '第1章 罗峰')   三级
      「第一卷 第1章 罗峰」   → ('第一卷', '', '第1章 罗峰')         两级
      「第1章 罗峰」          → ('', '', '第1章 罗峰')                一级
      「番外篇 魔女之影」     → ('番外篇 魔女之影', '', '番外篇 魔女之影')
    """
    rest = _clean(title)
    if not rest:
        return "", "", ""
    # 番外类：整条作为卷名，章名保留原文（不强行造章）
    if _is_special_vol(rest):
        return rest, "", rest

    vm = _RE_VOL.match(rest)          # 卷标记只认开头
    sm = _RE_SUB.search(rest)         # 集标记可能在卷名之后
    cm = _RE_CH.search(rest)          # 章标记
    if not (vm or sm or cm):
        return "", "", rest

    ch_start = cm.start() if cm else len(rest)
    vol = ""
    sub = ""
    ch = rest

    # 卷：从开头到「集标记」或「章标记」之前（取更靠前者作为边界）
    if vm:
        bound = sm.start() if (sm and sm.start() < ch_start) else ch_start
        vol = _clean(rest[:bound]) or vm.group(1)
    # 集：位于集标记之后、章标记之前（且必须在章之前才算层级）
    if sm and sm.start() < ch_start:
        sub = _clean(rest[sm.start():ch_start]) or sm.group(1)
    # 章：从章标记到结尾
    if cm:
        ch = _clean(rest[cm.start():])
    elif sub and sm:
        ch = _clean(rest[sm.end():]) or rest
    elif vm:
        ch = _clean(rest[vm.end():]) or rest
    return vol, sub, ch


def normalize_catalog(catalog: List[Dict],
                      explicit_vol: Optional[str] = None) -> List[Dict]:
    """把目录归一为带 vol/sub/纯 title 的新列表。

    :param catalog: 各源 fetch_catalog 的原始章节列表
    :param explicit_vol: 整本统一的卷名（当源只给一个卷时，如番茄单卷书）；
                         为空则逐章从标题解析。
    """
    out: List[Dict] = []
    for ch in catalog:
        item = dict(ch)  # 浅拷贝，绝不改原始对象
        raw_title = _clean(ch.get("title") or "")
        preset_vol = _clean(ch.get("vol"))          # 源已标注的卷（如文库8 vcss / 番茄 volume_name）
        preset_sub = _clean(ch.get("sub"))          # 源已标注的集（若有）
        vol_from_title, sub_from_title, pure = parse_title(raw_title)
        # 显式 sub 优先于标题解析
        sub = preset_sub or sub_from_title
        pure = pure or raw_title
        # 卷优先级：逐章预置 > 整本统一显式卷 > 标题里解析出的卷
        if preset_vol:
            vol = preset_vol
        elif explicit_vol:
            vol = _clean(explicit_vol)
        else:
            vol = vol_from_title
        # 番外类按用户要求提升为卷级；用统一卷名让连续番外聚成一组，
        # 章名保留原文（不强行剥离），sub 清空避免误判层级。
        if _is_special_vol(raw_title) and not preset_vol:
            vol = "番外篇"
            sub = ""
            pure = raw_title
        item["vol"] = vol
        item["sub"] = sub
        item["title"] = pure
        out.append(item)
    return out


def normalize_catalog_safe(catalog: List[Dict],
                           explicit_vol: Optional[str] = None) -> List[Dict]:
    """容错归一：层级解析异常时原样返回目录，保证下载主流程永不因层级失败。

    七猫 / 番茄 / 文库8 三源统一调用本函数，避免各自重复 try/except 且行为不一致。
    """
    try:
        return normalize_catalog(catalog, explicit_vol)
    except Exception:  # noqa: BLE001 - 层级解析失败不影响原始目录
        return catalog


def group_levels(catalog: List[Dict]) -> int:
    """返回目录实际层数：1=纯章 / 2=卷章 / 3=卷集章。用于导出时自动适配。"""
    has_vol = any(_clean(c.get("vol")) for c in catalog)
    if not has_vol:
        return 1
    has_sub = any(_clean(c.get("sub")) for c in catalog)
    return 3 if has_sub else 2


def build_nested_toc(pairs, epub_mod):
    """把 (章节dict, EPUB条目) 序列构造成 ebooklib 嵌套目录。

    自动适配层数：
      - 无任何分组 → 扁平列表（与旧版 book.toc=tuple(章节) 完全一致，零回归）
      - 仅卷       → [(Section(卷), [章...]), ...]
      - 卷+集      → [(Section(卷), [(Section(集), [章...]), ...]), ...]

    :param pairs: [(chapter_dict, epub_item), ...]，按阅读顺序，只含成功写入的章节
    :param epub_mod: ebooklib.epub 模块（惰性传入，避免无谓启动期加载）
    :return: 可直接赋给 book.toc 的列表
    """
    seq = list(pairs)
    # 无分组：保持扁平，行为与旧版一致
    if not any(_clean(c.get("vol")) or _clean(c.get("sub")) for c, _ in seq):
        return [it for _, it in seq]

    root: list = []
    groups: dict = {}    # key -> {'sec': Section, 'children': [], 'first': item}
    subs: dict = {}      # (volkey, sub) -> {'sec': Section, 'children': [], 'first': item}

    def ensure(key, title, table, container, item):
        if key not in table:
            sec = epub_mod.Section(title)
            node = {"sec": sec, "children": [], "first": item}
            table[key] = node
            container.append((sec, node["children"]))
        return table[key]

    for ch, item in seq:
        vol = _clean(ch.get("vol"))
        sub = _clean(ch.get("sub"))
        if vol:
            vg = ensure(("v", vol), vol, groups, root, item)
            if sub:
                sg = ensure((("v", vol), sub), sub, subs, vg["children"], item)
                sg["children"].append(item)
            else:
                vg["children"].append(item)
        elif sub:
            # 有集无卷：把集提升为顶层分组
            sg = ensure(("s", sub), sub, groups, root, item)
            sg["children"].append(item)
        else:
            # 本书有分组但本章无分组：置于根层
            root.append(item)

    # 给每个分组节点补 href=首章文件，兼容严格阅读器（空 href 的分组可能被丢弃）
    for table in (groups, subs):
        for node in table.values():
            first = node.get("first")
            fname = getattr(first, "file_name", "") if first is not None else ""
            if fname:
                node["sec"].href = fname
    return root


def txt_heading_lines(catalog: List[Dict], i: int,
                      prev: Optional[Dict] = None) -> str:
    """返回第 i 章之前应插入的「卷/集」标题行（仅层级变化时输出）。

    微信读书按独立成行的标题识别目录层级；卷名、集名单独占一行并前后留空行，
    使其在导入后被识别为上级目录。纯章目录返回空串（零回归）。
    """
    if i < 0 or i >= len(catalog):
        return ""
    ch = catalog[i]
    vol = _clean(ch.get("vol"))
    sub = _clean(ch.get("sub"))
    if not vol and not sub:
        return ""
    prev = prev or {}
    prev_vol = _clean(prev.get("vol"))
    prev_sub = _clean(prev.get("sub"))
    lines: List[str] = []
    if vol and vol != prev_vol:
        lines.append(f"\n\n{vol}\n")
    if sub and (sub != prev_sub or vol != prev_vol):
        lines.append(f"\n{sub}\n")
    return "".join(lines)


def has_hierarchy(catalog: List[Dict]) -> bool:
    """目录是否带卷/集层级元数据。"""
    return any(_clean(c.get("vol")) or _clean(c.get("sub")) for c in catalog)
