# -*- coding: utf-8 -*-
"""人物对话彩色高亮（纯渲染层）。

设计原则
========
- 只把阅读器显示的文本变成带颜色的 HTML，**不修改原始章节文本**。
  下载/导出/导入走的是 qimao_core 与原始 content，完全不受影响。
- 用正则识别中文小说常见的引号对话与说话人标注，启发式判断"谁在说话"。
- 同一人物用稳定哈希映射到固定颜色（跨章、跨次运行一致）。
- 人物之间选用彼此有区分度、且与背景有足够对比度的色板：
  深色主题用亮色字，浅色/护眼/羊皮纸主题用深色字。

识别范围（尽力而为，不能保证 100% 准确）：
- 对话引号：「」『』“”‘’""
- 说话动词：说道/道/问/答/喊/叫/笑道/冷声道/喝道/喃喃道… 等
- 说话人位置：引号后紧邻（"…"萧炎说道）或引号前（萧炎说道："…"）
- 找不到人名时沿用本段最近的人名（处理连续对话中的"他/她"）
"""

from __future__ import annotations

import hashlib
import html
import re
from typing import List, Optional, Tuple


# 对话引号（成对）。顺序：先长后短、先中文后英文。
_DIALOGUE_RE = re.compile(
    r"(「[^」\n]{1,400}」)"      # 「」
    r"|(『[^』\n]{1,400}』)"      # 『』
    r"|(“[^”\n]{1,400}”)"      # 中文双引号
    r"|(‘[^’\n]{1,200}’)"      # 中文单引号
    r'|("[^"\n]{1,400}")'       # 英文双引号
    r"|('[^'\n]{1,200}')"       # 英文单引号
)

# 说话动词：长的放前面，保证优先匹配
_SPEECH_VERBS = (
    "喃喃道|呢喃道|嘟囔道|自言自语道|低声说道|沉声说道|冷声说道|大声说道"
    "|淡淡说道|缓缓说道|微笑道|笑道|冷笑道|冷哼道|喝道|吼道|叫道|喊道"
    "|问道|答道|回道|应道|说道| tell me"
    "|低声道|沉声道|冷声道|淡然道|漠然道|传音道|传声道"
    "|呢喃|喃喃|嘟囔|自语"
    "|说道|说|道|问|答|喊|叫|吼"
).replace("| tell me", "")

_VERB_RE = re.compile(r"(" + _SPEECH_VERBS + r")")

# 句首副词（剥离后才是人名）
_ADVERBS = [
    "不由得", "忍不住", "连忙", "急忙", "赶忙", "赶紧", "忽然", "猛然", "骤然",
    "随即", "于是", "然后", "接着", "随后", "当即", "立刻", "马上", "只听",
    "只见", "却听", "却见", "不由", "微微", "淡淡", "冷冷", "哈哈", "呵呵",
    "嘿嘿", "嘻嘻", "小声", "低声", "大声", "悄悄", "缓缓", "慢慢", "轻轻",
    "不禁", "下意识", "下意思", "条件反射", "再次", "重新", "终于", "这才",
]
# 泛指代词/群体词（识别到它们时沿用上文人名）
_PRONOUNS = {
    "他", "她", "它", "他们", "她们", "它们", "众人", "大家", "对方", "有人",
    "某人", "自己", "两人", "三人", "四人", "身旁之人", "身旁人", "来人",
    "老者", "青年", "少年", "少女", "男子", "女子", "孩童", "掌柜", "店员",
}
# 明显不是人名的段首词
_NOT_NAME = {
    "声音", "话音", "语音", "回答", "回应", "传说", "据说", "后面", "里面",
    "外面", "此时", "此刻", "这时", "那时", "顿时", "瞬间", "片刻", "良久",
}

# 高对比色板（浅背景用深字 / 深背景用亮字）
_PALETTE_LIGHT = [
    "#c62828",  # 红
    "#1565c0",  # 蓝
    "#2e7d32",  # 绿
    "#6a1b9a",  # 紫
    "#e65100",  # 橙
    "#00838f",  # 青
    "#ad1457",  # 品红
    "#4527a0",  # 深紫蓝
    "#37474f",  # 蓝灰
    "#5d4037",  # 棕
]
_PALETTE_DARK = [
    "#ff8a80",  # 亮红
    "#82b1ff",  # 亮蓝
    "#a5d6a7",  # 亮绿
    "#ce93d8",  # 亮紫
    "#ffcc80",  # 亮橙
    "#80deea",  # 亮青
    "#f48fb1",  # 亮品红
    "#b0bec5",  # 亮蓝灰
    "#fff59d",  # 亮黄
    "#d7ccc8",  # 亮棕
]
_UNKNOWN_COLOR = {"dark": "#90a4ae", "light": "#757575"}


def _clean_name(candidate: str) -> Optional[str]:
    """从动词前的候选串里提取人名；泛指/无效返回 None。"""
    cand = candidate.strip("，。！？、：: ,.!?；;…—-")
    changed = True
    while changed:
        changed = False
        for adv in _ADVERBS:
            if cand.startswith(adv) and len(cand) > len(adv):
                cand = cand[len(adv):]
                changed = True
                break
    if not cand:
        return None
    if cand in _PRONOUNS or cand in _NOT_NAME:
        return None
    # 人名一般 2-4 字；过长时取末尾（去掉残留修饰）
    if len(cand) > 5:
        cand = cand[-4:]
    return cand


def _speaker_near(text: str) -> Optional[str]:
    """在一小段文字里找 '人名+说话动词'；找不到具体人名返回 None。"""
    m = _VERB_RE.search(text)
    if not m:
        return None
    before = text[:m.start()]
    # 只取动词前最后一个标点片段，避免跨过前一句/前一段对话把无关文字当人名
    seg = re.split(r"[。！？，、：:；;\"'“”‘’「」『』()（）\s]", before)[-1]
    cn = re.findall(r"[\u4e00-\u9fa5]", seg)
    candidate = "".join(cn[-6:])
    return _clean_name(candidate)


def _detect_speaker(line: str, start: int, end: int, current: Optional[str]) -> Optional[str]:
    """判断某段对话的说话人；优先人名，否则沿用 current。"""
    # 引号后：如  "……"萧炎说道。
    after = line[end:end + 26]
    name = _speaker_near(after)
    if name:
        return name
    # 引号前：如  萧炎说道："……"  或  萧炎点点头："……"
    before = line[max(0, start - 26):start]
    name = _speaker_near(before)
    if name:
        return name
    # 冒号引导：取冒号前紧邻片段的人名（处理"萧炎点点头："这类无说话动词的情况）
    colon_seg = re.split(r"[。！？，、；;\n]", before)[-1].rstrip("：: ")
    if colon_seg:
        cn = re.findall(r"[\u4e00-\u9fa5]", colon_seg)
        name = _clean_name("".join(cn[-6:]))
        if name:
            return name
    # 没识别到具体人名：沿用上文（处理 "他/她" 连续对话）
    return current


def _color_for(name: Optional[str], dark: bool) -> str:
    if not name:
        return _UNKNOWN_COLOR["dark" if dark else "light"]
    palette = _PALETTE_DARK if dark else _PALETTE_LIGHT
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()
    idx = int(digest[:8], 16) % len(palette)
    return palette[idx]


def _highlight_line(line: str, dark: bool, current: Optional[str]) -> Tuple[str, Optional[str]]:
    """处理单行：返回 (带 span 的安全 HTML, 更新后的 current 说话人)。"""
    out: List[str] = []
    pos = 0
    cur = current
    for m in _DIALOGUE_RE.finditer(line):
        segment = m.group(0)
        # 英文引号且内部无中文的（如代码/特殊符号 '<script>'）不当作对话
        if segment[0] in "\"'" and not re.search(r"[\u4e00-\u9fa5]", segment[1:-1]):
            out.append(html.escape(line[pos:m.end()], quote=False))
            pos = m.end()
            continue
        out.append(html.escape(line[pos:m.start()], quote=False))
        speaker = _detect_speaker(line, m.start(), m.end(), cur)
        if speaker:
            cur = speaker
        color = _color_for(speaker, dark)
        out.append(
            f'<span style="color:{color};">'
            f"{html.escape(segment, quote=False)}</span>"
        )
        pos = m.end()
    out.append(html.escape(line[pos:], quote=False))
    return "".join(out), cur


def highlight_text(text: str, dark: bool = True) -> str:
    """把纯文本渲染成带对话彩色高亮的安全 HTML。

    :param text: 原始章节纯文本（未转义）
    :param dark: 是否深色主题（决定用亮色字还是深色字）
    :return: 可直接 setHtml 的 HTML 字符串，换行转为 <br/>
    """
    lines = text.split("\n")
    parts: List[str] = []
    current: Optional[str] = None
    for line in lines:
        rendered, current = _highlight_line(line, dark, current)
        parts.append(rendered)
    return "<br/>".join(parts)


def collect_speakers(text: str) -> List[str]:
    """调试/统计用：返回文本中识别到的说话人列表（按出现顺序，去重）。"""
    result: List[str] = []
    seen = set()
    current: Optional[str] = None
    for line in text.split("\n"):
        for m in _DIALOGUE_RE.finditer(line):
            seg = m.group(0)
            if seg[0] in "\"'" and not re.search(r"[\u4e00-\u9fa5]", seg[1:-1]):
                continue
            speaker = _detect_speaker(line, m.start(), m.end(), current)
            if speaker:
                current = speaker
            if speaker and speaker not in seen:
                seen.add(speaker)
                result.append(speaker)
    return result
