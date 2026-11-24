# -*- coding: utf-8 -*-
"""受限功能占位说明页。

展示番茄小说下载器有、但七猫版当前未接入的功能，并说明原因与可行的接入路径。
这不是功能缺陷，而是数据源 / 授权 / 技术栈差异导致的取舍。
"""

from __future__ import annotations

from typing import List, Tuple

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QFrame, QLabel, QSizePolicy,
)

from ..style import BG_CARD, BORDER, TEXT, TEXT_DIM, ACCENT, WARN
from ..widgets import nation_icon


# (图标, 标题, 状态, 状态色, 说明, 接入路径)
ITEMS: List[Tuple[str, str, str, str, str, str]] = [
    (
        "sumeru", "漫画 / 短剧下载", "暂无稳定数据源", WARN,
        "七猫官方没有公开的漫画与短剧接口，现有开源协议也无法覆盖这类资源。"
        "因此没有可靠的下载链路，不做假的占位下载。",
        "接入路径：需先拿到稳定的章节/资源列表与文件接口，再复用本项目的任务队列与输出模块。",
    ),
    (
        "fontaine", "AI 音色 / 有声书", "官方授权音色不可得", WARN,
        "七猫的官方听书音色属于在线服务，需要账号体系与音频接口授权，无法直接复用。"
        "当前阅读器已提供 12 款基于 edge-tts 神经网络声线的二次元风格音色（风格演绎，非版权角色原声克隆），联网可用，离线回退系统 TTS。",
        "真正的角色原声克隆模型（如 GPT-SoVITS 角色音色）多为 CC-BY-NC 协议、体积数百 GB 且需显卡推理，无法合法打包进本软件；如需官方音色请走正规授权渠道。",
    ),
    (
        "inazuma", "移动端 Android / iOS", "技术栈不同", ACCENT,
        "本项目是 PySide6 桌面应用，无法直接运行在手机上。"
        "移动端需要独立的工程与构建流程。",
        "接入路径：可复用 gui/qimao_core.py 的协议层，用移动端框架另行开发界面。",
    ),
    (
        "natlan", "镜像加速", "七猫链路无需", TEXT_DIM,
        "番茄下载器的镜像加速是针对其 CDN 的分流策略。"
        "七猫走的是缓存 ZIP 下载链路，目前稳定性足够，没有加速需求。",
        "接入路径：若未来出现访问故障，可在 qimao_core.py 中增加备用域名自动切换。",
    ),
]


class LimitationsView(QWidget):
    """受限功能说明页（占位，不做无效功能）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("功能说明")
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(14)

        title = QLabel("尚未接入的功能")
        title.setStyleSheet(f"color: {TEXT}; font-size: 22px; font-weight: bold;")
        outer.addWidget(title)

        intro = QLabel(
            "以下功能来自番茄小说下载器，但在七猫版中暂未实现。"
            "这里说明原因与可行的接入路径 —— 不做无法真正工作的假功能。"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {TEXT_DIM}; font-size: 13px;")
        outer.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        content = QWidget()
        cards = QVBoxLayout(content)
        cards.setContentsMargins(2, 4, 8, 4)
        cards.setSpacing(12)

        for icon, name, status, status_color, desc, path in ITEMS:
            cards.addWidget(self._make_card(icon, name, status, status_color, desc, path))

        cards.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        footer = QLabel("提示：这些能力也可以通过编写插件自行扩展，见「插件」页。")
        footer.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        outer.addWidget(footer)

    def _make_card(self, icon: str, name: str, status: str, status_color: str,
                   desc: str, path: str) -> QFrame:
        card = QFrame()
        card.setObjectName("limitCard")
        card.setStyleSheet(f"""
            QFrame#limitCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 10px;
            }}
        """)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        # 头部：图标 + 标题 + 状态徽章
        head = QHBoxLayout()
        ico = QLabel()
        ico.setPixmap(nation_icon(icon, ACCENT, 24).pixmap(24, 24))
        head.addWidget(ico)

        name_lbl = QLabel(name)
        name_lbl.setStyleSheet(f"color: {TEXT}; font-size: 15px; font-weight: bold;")
        head.addWidget(name_lbl)

        badge = QLabel(status)
        badge.setStyleSheet(
            f"color: {status_color}; border: 1px solid {status_color}; "
            f"border-radius: 10px; padding: 2px 10px; font-size: 12px;"
        )
        head.addWidget(badge)
        head.addStretch()
        lay.addLayout(head)

        desc_lbl = QLabel(desc)
        desc_lbl.setWordWrap(True)
        desc_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        desc_lbl.setStyleSheet(f"color: {TEXT}; font-size: 13px; line-height: 150%;")
        lay.addWidget(desc_lbl)

        path_lbl = QLabel(path)
        path_lbl.setWordWrap(True)
        path_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        lay.addWidget(path_lbl)
        return card
