# -*- coding: utf-8 -*-
"""二次元风格听书音色选择对话框（卡片式）。

纯 UI：列出 voice_library.VOICES，每个音色一张卡片，可试听、可选用。
合规提示常驻顶部：这些是风格演绎，非版权角色原声克隆。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QScrollArea, QFrame, QSlider,
)

from .i18n import tr
from .style import TEXT_DIM, BG_CARD, accent_color, modern_scrollbar_qss
from .voice_library import VOICES, VoiceProfile, DEFAULT_VOICE_ID

# 风格标签中文 → i18n key（与 voice_library.ALL_TAGS 同步）
_TAG_I18N = {
    "少女": "voice.tag.girl", "少年": "voice.tag.boy",
    "温柔": "voice.tag.gentle", "萝莉": "voice.tag.loli",
    "冷酷": "voice.tag.cool", "旁白": "voice.tag.narrator",
    "暖男": "voice.tag.warm", "日系": "voice.tag.anime",
    "热血": "voice.tag.hotblood", "冷艳": "voice.tag.cool_beauty",
    "优雅": "voice.tag.elegant", "成熟": "voice.tag.mature",
}


def _fmt_speed(speed: float) -> str:
    """倍速显示：0.5× / 1× / 1.25× / 5×。"""
    s = max(0.5, min(5.0, float(speed)))
    return f"{s:.2f}".rstrip("0").rstrip(".") + "×"


class _VoiceCard(QFrame):
    """单个音色卡片。"""

    def __init__(self, profile: VoiceProfile, selected: bool,
                 on_pick, on_preview) -> None:
        super().__init__()
        self.profile = profile
        self.on_pick = on_pick
        self.on_preview = on_preview
        self._selected = selected
        self.setObjectName("voiceCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(92)

        from .widgets import nation_icon
        accent = accent_color("purple")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(10)

        # 角色头像（用国徽图标做视觉区分）
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(40, 40)
        icon_lbl.setPixmap(nation_icon(profile.nation, accent, 40).pixmap(40, 40))
        row.addWidget(icon_lbl, 0, Qt.AlignTop)

        mid = QVBoxLayout()
        mid.setSpacing(3)
        name_lbl = QLabel(tr(profile.i18n_name))
        name_lbl.setStyleSheet("color: #f0f0f0; font-size: 13px; font-weight: bold;")
        mid.addWidget(name_lbl)

        tag_i18n = _TAG_I18N.get(profile.tag, "")
        tag_text = tr(tag_i18n) if tag_i18n else profile.tag
        tag_lbl = QLabel("【%s】" % tag_text)
        tag_lbl.setStyleSheet(f"color: {accent}; font-size: 10px;")
        mid.addWidget(tag_lbl)

        desc_lbl = QLabel(tr(profile.i18n_desc))
        desc_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        desc_lbl.setWordWrap(True)
        mid.addWidget(desc_lbl)
        row.addLayout(mid, 1)

        right = QVBoxLayout()
        right.setSpacing(4)
        preview_btn = QPushButton(tr("voice.preview"))
        preview_btn.setFixedWidth(64)
        preview_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {TEXT_DIM}; "
            f"border: 1px solid #555; border-radius: 6px; padding: 3px 6px; font-size: 11px; }}"
            f"QPushButton:hover {{ color: #fff; border-color: {accent}; }}")
        preview_btn.clicked.connect(lambda: self.on_preview(self.profile))
        right.addWidget(preview_btn, 0, Qt.AlignTop)
        right.addStretch(1)
        row.addLayout(right, 0)

        self._apply_style()

    def _apply_style(self) -> None:
        accent = accent_color("purple")
        if self._selected:
            border = f"2px solid {accent}"
            bg = "rgba(255,255,255,0.06)"
        else:
            border = "1px solid #3a3a3a"
            bg = "transparent"
        self.setStyleSheet(
            f"QFrame#voiceCard {{ background: {bg}; border: {border}; "
            f"border-radius: 10px; }}")

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.on_pick(self.profile)


class VoiceDialog(QDialog):
    """音色选择对话框。选定后 result_voice_id 为音色 id。"""

    def __init__(self, current_id: str, parent=None, current_speed: float = 1.0,
                 speed_cb=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("voice.dialog_title"))
        self.setModal(True)
        self.resize(720, 600)
        self.result_voice_id: str | None = None
        self._current_id = current_id or DEFAULT_VOICE_ID
        self._cards: dict[str, _VoiceCard] = {}
        self._on_preview = None  # 由外部注入 callable(profile)
        self._speed_cb = speed_cb  # 由外部注入 callable(speed)
        self._speed = max(0.5, min(5.0, float(current_speed or 1.0)))

        self.setStyleSheet(
            f"QDialog {{ background: {BG_CARD}; }}"
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
            + modern_scrollbar_qss(True)
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        title = QLabel(tr("voice.dialog_hint"))
        title.setStyleSheet("color: #f0f0f0; font-size: 14px; font-weight: bold;")
        title.setWordWrap(True)
        root.addWidget(title)

        note = QLabel(tr("voice.disclaimer"))
        note.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        note.setWordWrap(True)
        root.addWidget(note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setSpacing(10)
        for i, profile in enumerate(VOICES):
            card = _VoiceCard(profile, profile.id == self._current_id,
                              self._pick, self._preview)
            self._cards[profile.id] = card
            grid.addWidget(card, i // 2, i % 2)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # ---- 倍速控制条 ----
        speed_row = QHBoxLayout()
        speed_row.setContentsMargins(2, 2, 2, 2)
        speed_row.setSpacing(10)
        speed_row.addWidget(QLabel(tr("voice.speed")))
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(50, 500)  # 实际倍速 = 滑块值 / 100
        self.speed_slider.setValue(int(round(self._speed * 100)))
        self.speed_slider.setFixedWidth(260)
        self.speed_slider.setToolTip(tr("voice.speed_hint"))
        self.speed_value = QLabel(_fmt_speed(self._speed))
        self.speed_value.setStyleSheet(f"color: {accent_color('purple')}; font-size: 12px; font-weight: bold;")
        self.speed_value.setFixedWidth(44)
        self.speed_slider.valueChanged.connect(self._on_speed_changed)
        speed_row.addWidget(self.speed_slider)
        speed_row.addWidget(self.speed_value)
        speed_row.addStretch(1)
        root.addLayout(speed_row)

        footer = QHBoxLayout()
        footer.addStretch(1)
        close_btn = QPushButton(tr("dlg.cancel"))
        close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {TEXT_DIM}; "
            f"border: 1px solid #555; border-radius: 8px; padding: 6px 18px; }}"
            f"QPushButton:hover {{ color: #fff; }}")
        close_btn.clicked.connect(self.reject)
        footer.addWidget(close_btn)
        root.addLayout(footer)

    def set_preview_callback(self, cb) -> None:
        self._on_preview = cb

    def _pick(self, profile: VoiceProfile) -> None:
        self.result_voice_id = profile.id
        self.accept()

    def _preview(self, profile: VoiceProfile) -> None:
        if callable(self._on_preview):
            self._on_preview(profile)

    def _on_speed_changed(self, value: int) -> None:
        """滑块拖动时实时更新数值并回调外部（即时生效、无需确认）。"""
        speed = max(0.5, min(5.0, value / 100.0))
        self._speed = speed
        self.speed_value.setText(_fmt_speed(speed))
        if callable(self._speed_cb):
            self._speed_cb(speed)

    @staticmethod
    def pick(current_id: str, parent=None, preview_cb=None,
             speed_cb=None, current_speed: float = 1.0) -> str | None:
        dlg = VoiceDialog(current_id, parent, current_speed=current_speed,
                          speed_cb=speed_cb)
        if preview_cb is not None:
            dlg.set_preview_callback(preview_cb)
        dlg.exec()
        return dlg.result_voice_id
