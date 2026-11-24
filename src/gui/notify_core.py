# -*- coding: utf-8 -*-
"""系统通知 + 提示音核心。

- 系统通知：优先用 QSystemTrayIcon.showMessage（跨平台，无额外依赖）；
  托盘不可用时降级为窗口标题闪烁（由调用方处理）。
- 提示音：用 PySide6 QtMultimedia.QSoundEffect 播放内置生成的短提示音
  （WAV 数据运行时生成，不依赖外部音频文件，打包无负担）；
  QtMultimedia 缺失时降级为 QApplication.beep()。

所有行为通过 DataStore.settings 配置：
- notify_enabled      下载完成系统通知（默认 True）
- notify_sound        提示音方案："default" / "chime" / "soft" / "none"
- notify_sound_volume 音量 0-100（默认 70）
"""

from __future__ import annotations

import atexit
import io
import math
import os
import struct
import tempfile
import wave
from typing import Optional

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

_getter = None

# 进程退出时清理本模块创建的临时 wav，避免 temp 目录持续累积
_TEMP_WAVS: list = []


def _cleanup_temp_wavs() -> None:
    for path in _TEMP_WAVS:
        try:
            if os.path.isfile(path):
                os.unlink(path)
        except OSError:
            pass
    _TEMP_WAVS.clear()


atexit.register(_cleanup_temp_wavs)


def bind_settings(getter) -> None:
    global _getter
    _getter = getter


def _setting(key, default):
    if _getter is None:
        return default
    try:
        return _getter(key, default)
    except Exception:  # noqa: BLE001
        return default


# ------------------------------------------------------------------ 提示音 WAV 生成

def _make_wav(notes, sample_rate: int = 22050) -> bytes:
    """根据 (频率, 时长秒) 序列合成一段 PCM WAV 字节。"""
    frames = bytearray()
    for freq, duration in notes:
        count = int(sample_rate * duration)
        for i in range(count):
            # 加淡入淡出包络，避免爆音
            env = min(1.0, i / (sample_rate * 0.01),
                      (count - i) / (sample_rate * 0.03))
            val = int(12000 * env * math.sin(2 * math.pi * freq * i / sample_rate))
            frames.extend(struct.pack("<h", val))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


_SOUNDS = {
    # 两声上行 "叮咚"
    "default": [(880, 0.12), (1174.66, 0.18)],
    # 三音和弦上升
    "chime": [(659.25, 0.10), (830.61, 0.10), (1046.50, 0.22)],
    # 轻柔单音
    "soft": [(523.25, 0.25)],
}


class SoundPlayer:
    """懒加载的提示音播放器。"""

    def __init__(self) -> None:
        self._effect = None
        self._cache: dict = {}

    def play(self, scheme: Optional[str] = None) -> None:
        scheme = scheme if scheme is not None else str(_setting("notify_sound", "default"))
        if scheme == "none" or not scheme:
            return
        app = QApplication.instance()
        # 无头/无音频环境：QtMultimedia 在无音频设备时播放会段错误，直接降级
        try:
            if app is not None and str(app.platformName()).lower() in ("offscreen", "minimal"):
                QApplication.beep()
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            from PySide6.QtMultimedia import QSoundEffect
            if self._effect is None:
                self._effect = QSoundEffect(app)
            wav = self._cache.get(scheme)
            if wav is None:
                wav = _make_wav(_SOUNDS.get(scheme, _SOUNDS["default"]))
                self._cache[scheme] = wav
                # 写入临时 wav 文件（data URI 在部分 QtMultimedia 版本不稳定）
                fd, path = tempfile.mkstemp(suffix=".wav", prefix="qimao_snd_")
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(wav)
                except Exception:  # noqa: BLE001
                    pass
                _TEMP_WAVS.append(path)
                self._cache[scheme + "_path"] = path
            path = self._cache.get(scheme + "_path", "")
            if path and os.path.isfile(path):
                self._effect.setSource(QUrl.fromLocalFile(path))
                self._effect.setVolume(float(_setting("notify_sound_volume", 70)) / 100.0)
                self._effect.play()
                return
        except Exception:  # noqa: BLE001
            pass
        # 降级：系统 beep
        try:
            QApplication.beep()
        except Exception:  # noqa: BLE001
            pass


_player = SoundPlayer()


def play_download_sound(success: bool = True) -> None:
    """按设置播放下载完成提示音。失败时若启用则用 low 音提示。"""
    scheme = str(_setting("notify_sound", "default"))
    if scheme == "none":
        return
    if not success and scheme != "none":
        scheme = "soft"
    _player.play(scheme)


def notify_download(title: str, message: str, parent=None) -> bool:
    """显示下载完成系统通知。返回是否成功显示。"""
    if not bool(_setting("notify_enabled", True)):
        return False
    try:
        tray = getattr(parent, "tray", None)
        if tray is not None and isinstance(tray, QSystemTrayIcon) and tray.isVisible():
            tray.showMessage(title, message,
                             QSystemTrayIcon.Information, 4000)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


SOUND_LABELS = {
    "default": "默认 (Notify 8)",
    "chime": "清脆和弦",
    "soft": "轻柔",
    "none": "静音",
}
