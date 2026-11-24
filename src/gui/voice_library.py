# -*- coding: utf-8 -*-
"""二次元风格听书音色库。

重要说明（合规）：
    本音色库里的“角色名”是**风格标签**，用于描述声线气质（如“傲娇少女”
    “清冷大小姐”），全部基于 edge-tts 提供的微软神经网络真实声线，通过语速、
    音调、音量参数调校出接近某类二次元角色的听感。它们是**风格演绎**，
    **不是任何版权角色的原声克隆**，也不使用任何未经授权的角色语音模型。

    底层依赖 edge-tts（GitHub: rany2/edge-tts，GPL-3.0，免费、无需 API Key）。
    合成在微软云端完成，需联网；不可用时自动回退系统 TTS。

扩展接口（留好接口）：
    VoiceProfile.provider 指定合成后端（默认 "edge"）。tts_engine.TTSEngine
    维护一个 provider 注册表，任何实现 TTSProvider 接口的后端（本地声音模型、
    第三方 API 等）都可以 register_provider() 后，通过新音色的 provider 字段
    直接接入，无需改动阅读器或对话框。model_uri 字段预留给未来的本地模型
    路径 / API 端点，当前留空。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class VoiceProfile:
    """一个二次元风格音色。

    id: 持久化标识
    name: 显示角色/风格名（中文，i18n 另有映射）
    desc: 声线描述
    tag: 风格标签
    voice: edge-tts 真实声线 ShortName
    rate: 语速，如 "-5%" / "+10%"
    pitch: 音调，如 "+8Hz" / "-10Hz"
    volume: 音量，如 "+0%"
    nation: 用于图标着色的国家 key（视觉区分）
    i18n_name: i18n 名称键
    i18n_desc: i18n 描述键
    provider: 合成后端 id，对应 TTSEngine 注册的 TTSProvider（默认 edge）
    model_uri: 预留：本地模型路径 / API 端点，当前未使用
    """

    id: str
    name: str
    desc: str
    tag: str
    voice: str
    rate: str
    pitch: str
    volume: str
    nation: str
    i18n_name: str
    i18n_desc: str
    provider: str = "edge"
    model_uri: str = ""


# 全部基于实测存在的 edge-tts 声线（见 list_voices）：
#   zh-CN: Xiaoxiao(Warm) Xiaoyi(Lively) Yunjian(Passion)
#          Yunxi(Lively/Sunshine) Yunxia(Cute) Yunyang(Professional)
#   ja-JP: Nanami(Friendly) Keita(Friendly)
VOICES: List[VoiceProfile] = [
    VoiceProfile(
        "sakura", "傲娇少女 · 绘梨", "明快俏皮、尾音轻扬，傲娇学姐的既视感",
        "少女", "zh-CN-XiaoyiNeural", "-3%", "+12Hz", "+0%", "inazuma",
        "voice.sakura.name", "voice.sakura.desc"),
    VoiceProfile(
        "sunshine", "阳光少年 · 云希", "清朗阳光、元气满满，热血主角声线",
        "少年", "zh-CN-YunxiNeural", "+2%", "+5Hz", "+0%", "mondstadt",
        "voice.sunshine.name", "voice.sunshine.desc"),
    VoiceProfile(
        "gentle", "温柔学姐 · 晓晓", "温暖柔和、娓娓道来，治愈系女声",
        "温柔", "zh-CN-XiaoxiaoNeural", "-8%", "+2Hz", "+0%", "fontaine",
        "voice.gentle.name", "voice.gentle.desc"),
    VoiceProfile(
        "cute", "可爱萝莉 · 云侠", "软萌可爱、轻快跳脱，吉祥物般的声线",
        "萝莉", "zh-CN-YunxiaNeural", "-2%", "+18Hz", "+0%", "sumeru",
        "voice.cute.name", "voice.cute.desc"),
    VoiceProfile(
        "cool", "冷酷剑客 · 云健", "低沉有力、气场十足，高冷男主声线",
        "冷酷", "zh-CN-YunjianNeural", "-5%", "-12Hz", "+0%", "snezhnaya",
        "voice.cool.name", "voice.cool.desc"),
    VoiceProfile(
        "narrator", "沉稳旁白 · 云扬", "专业沉稳、字正腔圆，史诗旁白声线",
        "旁白", "zh-CN-YunyangNeural", "-10%", "-2Hz", "+0%", "liyue",
        "voice.narrator.name", "voice.narrator.desc"),
    VoiceProfile(
        "lively", "活跃少女 · 晓伊", "活泼灵动、元气十足，青春女主角声线",
        "少女", "zh-CN-XiaoyiNeural", "+5%", "+8Hz", "+0%", "natlan",
        "voice.lively.name", "voice.lively.desc"),
    VoiceProfile(
        "warm", "暖心大哥哥 · 云希", "阳光可靠、亲和力强，暖男声线",
        "暖男", "zh-CN-YunxiNeural", "-4%", "-5Hz", "+0%", "mondstadt",
        "voice.warm.name", "voice.warm.desc"),
    VoiceProfile(
        "anime_jp", "动漫女声 · Nanami", "日系动漫腔、明快可爱，二次元浓度拉满",
        "日系", "ja-JP-NanamiNeural", "-2%", "+6Hz", "+0%", "inazuma",
        "voice.anime_jp.name", "voice.anime_jp.desc"),
    VoiceProfile(
        "anime_jp_male", "动漫男声 · Keita", "日系少年音、清爽阳光，番剧男主声线",
        "日系", "ja-JP-KeitaNeural", "+0%", "+4Hz", "+0%", "fontaine",
        "voice.anime_jp_male.name", "voice.anime_jp_male.desc"),
    VoiceProfile(
        "soft", "轻声细语 · 晓晓", "压低音量与语速，耳语般的轻柔女声",
        "温柔", "zh-CN-XiaoxiaoNeural", "-18%", "+4Hz", "-10%", "nodkrai",
        "voice.soft.name", "voice.soft.desc"),
    VoiceProfile(
        "heroic", "热血青年 · 云健", "昂扬激越、掷地有声，燃系主角声线",
        "热血", "zh-CN-YunjianNeural", "+6%", "+4Hz", "+5%", "natlan",
        "voice.heroic.name", "voice.heroic.desc"),
    # ---- 角色风格系列（风格演绎，非原声克隆）----
    VoiceProfile(
        "mahiru", "温柔天使 · 真昼", "温暖治愈、干净通透，像总会照顾人的邻家天使",
        "温柔", "zh-CN-XiaoxiaoNeural", "-5%", "+6Hz", "+0%", "fontaine",
        "voice.mahiru.name", "voice.mahiru.desc"),
    VoiceProfile(
        "yukino", "清冷大小姐 · 雪乃", "清冷理性、字句克制，拒人千里的大小姐气质",
        "冷艳", "zh-CN-XiaoyiNeural", "-8%", "-14Hz", "-3%", "snezhnaya",
        "voice.yukino.name", "voice.yukino.desc"),
    VoiceProfile(
        "violet", "优雅人偶 · 薇尔莉特", "缓慢优雅、字正腔圆，人偶般精确又温柔的吐息",
        "优雅", "zh-CN-XiaoxiaoNeural", "-12%", "-2Hz", "+0%", "fontaine",
        "voice.violet.name", "voice.violet.desc"),
    VoiceProfile(
        "mai", "成熟学姐 · 麻衣", "从容温柔、略带成熟的御姐气场，偶像学姐的余韵",
        "成熟", "zh-CN-XiaoyiNeural", "-3%", "-6Hz", "+0%", "inazuma",
        "voice.mai.name", "voice.mai.desc"),
]

# 全部风格标签（供对话框 i18n 映射用，新增标签只需在此与 i18n 同步）
ALL_TAGS = ("少女", "少年", "温柔", "萝莉", "冷酷", "旁白", "暖男", "日系",
            "热血", "冷艳", "优雅", "成熟")

DEFAULT_VOICE_ID = "sakura"

# 试听示例句（三语），让用户在选音色时能立刻听到风格
SAMPLE_ZH = "风起的时候，我会一直在这里等你。准备好，一起出发了吗？"
SAMPLE_EN = "When the wind rises, I'll be waiting here for you. Ready to set off together?"
SAMPLE_RU = "Когда поднимется ветер, я буду ждать тебя здесь. Готовы отправиться вместе?"

_BY_ID = {v.id: v for v in VOICES}


def get_voice(voice_id: str) -> VoiceProfile:
    """按 id 取音色，找不到回退默认。"""
    return _BY_ID.get(voice_id, _BY_ID[DEFAULT_VOICE_ID])


def sample_for_language(lang: str) -> str:
    """按界面语言返回试听示例句。"""
    code = (lang or "").lower()
    if code.startswith("en"):
        return SAMPLE_EN
    if code.startswith("ru"):
        return SAMPLE_RU
    return SAMPLE_ZH


def edge_available() -> bool:
    """检测 edge-tts 是否可用（已安装）。"""
    import importlib.util
    return importlib.util.find_spec("edge_tts") is not None
