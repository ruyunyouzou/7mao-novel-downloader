# -*- coding: utf-8 -*-
"""统一听书引擎：edge-tts（高质量，联网）优先，系统 TTS 兜底。

- edge-tts 模式：把文本合成 MP3 临时文件，用 QMediaPlayer 播放；支持二次元
  风格音色的语速/音调参数，播放结束自动清理临时文件。
- 系统模式：若 edge-tts 不可用或合成失败，回退到 PySide6 QTextToSpeech，
  保证离线也能朗读（此时音色由系统决定）。

扩展接口（留好接口）：
    TTSProvider 是合成后端的抽象基类。任何新后端（本地声音模型、第三方 API）
    只需实现 available() 与 synthesize()，再调用 TTSEngine.register_provider()
    注册，即可在 voice_library.VoiceProfile.provider 里按 id 引用。
    EdgeTTSProvider 为内置实现；系统 TTS 作为引擎级兜底保留。
"""

from __future__ import annotations

import os
import tempfile
import uuid
from abc import ABC, abstractmethod

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

from .voice_library import VoiceProfile, edge_available


class TTSProvider(ABC):
    """听书合成后端接口。

    实现一个新后端只需两步：
      1. 继承本类，设置唯一的 id，实现 available() 与 synthesize()；
      2. 在运行时调用 engine.register_provider(MyProvider())。

    synthesize() 需把 text 合成为音频文件写入 out_path，成功返回 True。
    引擎负责播放与临时文件清理。
    """

    id: str = "base"
    name: str = "Base Provider"

    @abstractmethod
    def available(self) -> bool:
        """当前环境是否可用（依赖是否安装、配置是否就绪等）。"""

    @abstractmethod
    def synthesize(self, text: str, profile: VoiceProfile, out_path: str) -> bool:
        """把 text 合成为音频写入 out_path，成功返回 True。"""


class EdgeTTSProvider(TTSProvider):
    """内置：edge-tts 微软神经网络声线（联网、免费）。"""

    id = "edge"
    name = "edge-tts"

    def available(self) -> bool:
        return edge_available()

    def synthesize(self, text: str, profile: VoiceProfile, out_path: str) -> bool:
        import asyncio
        import edge_tts

        async def _gen() -> None:
            comm = edge_tts.Communicate(
                text, profile.voice, rate=profile.rate,
                volume=profile.volume, pitch=profile.pitch)
            await comm.save(out_path)

        try:
            asyncio.get_event_loop().run_until_complete(_gen())
        except RuntimeError:
            asyncio.run(_gen())
        return True


class TTSEngine(QObject):
    """封装播放控制；state 变化通过 playingChanged 通知 UI。"""

    playingChanged = Signal(bool)
    errorOccurred = Signal(str)
    speedChanged = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # QAudioOutput 在无音频设备的环境（如无头服务器）创建时可能使进程退出，
        # 因此延迟到真正播放时才创建；这里只建播放器并接状态信号。
        self._player = QMediaPlayer(self)
        self._audio = None
        self._player.playbackStateChanged.connect(self._on_state_changed)

        self._tmp_files: list[str] = []
        self._using_edge = False
        # 播放倍速：0.5 ~ 5.0（QMediaPlayer 实时调速，不重新合成音频）
        self._speed = 1.0
        # 当前导入的本地音频路径（None 表示当前为 TTS 合成播放）
        self._imported_audio: str | None = None
        self._system_tts = None
        self._system_available = False
        try:
            from PySide6.QtTextToSpeech import QTextToSpeech
            self._system_tts = QTextToSpeech(self)
            self._system_available = True
        except Exception:  # noqa: BLE001
            self._system_available = False

        # 合成后端注册表：内置 edge-tts，外部可 register_provider() 扩展。
        self._providers: dict[str, TTSProvider] = {}
        self.register_provider(EdgeTTSProvider())

    # ------------------------------------------------------------- 后端注册
    def register_provider(self, provider: TTSProvider) -> None:
        """注册/替换一个合成后端。同名 id 会覆盖，便于热更新。"""
        self._providers[provider.id] = provider

    def unregister_provider(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)

    def provider_ids(self) -> list[str]:
        return list(self._providers)

    def _provider_for(self, profile: VoiceProfile) -> TTSProvider | None:
        p = self._providers.get(getattr(profile, "provider", "edge") or "edge")
        if p is not None and p.available():
            return p
        return None

    # ------------------------------------------------------------------ 状态
    def _ensure_audio(self) -> bool:
        """首次播放时创建音频输出；失败返回 False 并回退系统 TTS。"""
        if self._audio is not None:
            return True
        try:
            self._audio = QAudioOutput(self)
            self._player.setAudioOutput(self._audio)
            return True
        except Exception as exc:  # noqa: BLE001
            self.errorOccurred.emit(str(exc))
            return False

    def _on_state_changed(self, state) -> None:
        from PySide6.QtMultimedia import QMediaPlayer as MP
        self.playingChanged.emit(state == MP.PlayingState)

    def is_playing(self) -> bool:
        from PySide6.QtMultimedia import QMediaPlayer as MP
        return self._player.playbackState() == MP.PlayingState

    @property
    def engine_name(self) -> str:
        if self._imported_audio:
            return "local"
        if self._using_edge:
            return "edge-tts"
        return "system"

    # ------------------------------------------------------- 音频导入接口
    # 支持的本地音频格式，供文件对话框过滤器与校验共用。
    AUDIO_EXTENSIONS = (
        ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus",
        ".wma", ".aiff", ".aif", ".mp4", ".m4b",
    )

    @classmethod
    def audio_filter(cls) -> str:
        """QFileDialog 使用的文件类型过滤器。"""
        exts = " ".join("*" + e for e in cls.AUDIO_EXTENSIONS)
        return f"音频文件 ({exts});;所有文件 (*)"

    @classmethod
    def is_supported_audio(cls, path: str) -> bool:
        """仅按扩展名判断，不依赖文件是否存在（便于测试预校验）。"""
        ext = os.path.splitext(str(path or ""))[1].lower()
        return ext in cls.AUDIO_EXTENSIONS

    def import_audio(self, path: str) -> bool:
        """导入并播放本地音频文件，复用同一播放管线与倍速控制。

        这是预留的音频导入接口：外部（阅读器菜单 / 插件 / 测试）拿到任意本地
        音频路径后，调用本方法即可用同一个 QMediaPlayer 播放，倍速、停止、
        音量控制与 TTS 合成完全一致。导入的音频不会被当作临时文件清理。
        """
        if not path or not os.path.isfile(path):
            return False
        if not self.is_supported_audio(path):
            return False
        self.stop()
        try:
            if not self._ensure_audio():
                # 无音频设备的环境仍把 source 设好，便于测试倍速适配
                self._player.setSource(QUrl.fromLocalFile(path))
                self._player.setPlaybackRate(self._speed)
                self._imported_audio = path
                self._using_edge = True
                return False
            self._player.setSource(QUrl.fromLocalFile(path))
            self._player.setPlaybackRate(self._speed)
            self._using_edge = True
            self._imported_audio = path
            self._player.play()
            return True
        except Exception as exc:  # noqa: BLE001
            self.errorOccurred.emit(str(exc))
            return False

    @property
    def imported_audio(self) -> str | None:
        """当前导入播放的本地音频路径，未导入时为 None。"""
        return self._imported_audio

    def available_engine_name(self) -> str:
        """当前默认可用引擎名（供 UI 状态显示）。"""
        p = self._providers.get("edge")
        if p is not None and p.available():
            return "edge-tts"
        return "system"

    # ------------------------------------------------------------------ 倍速
    @property
    def speed(self) -> float:
        """当前播放倍速，范围 0.5 ~ 5.0。"""
        return self._speed

    @staticmethod
    def clamp_speed(value) -> float:
        """把任意输入规整到 0.5~5.0，保留两位小数；非法值回退 1.0。"""
        try:
            s = float(value)
        except (TypeError, ValueError):
            return 1.0
        if s != s:  # NaN
            return 1.0
        return round(max(0.5, min(5.0, s)), 2)

    @staticmethod
    def system_rate_for(speed: float) -> float:
        """把 0.5~5 倍速近似映射为 QTextToSpeech 的 -1.0~1.0 rate。

        系统 TTS 的 rate 不是倍数而是相对快慢（-1 最慢 / +1 最快），
        这里做分段线性近似：0.5→-1、1.0→0、3.0→+1（更高倍率截断）。
        """
        if speed <= 1.0:
            return max(-1.0, (speed - 1.0) / 0.5)
        return min(1.0, (speed - 1.0) / 2.0)

    def set_speed(self, value: float) -> None:
        """设置播放倍速，立即作用于当前播放（不重新合成）。"""
        self._speed = self.clamp_speed(value)
        try:
            self._player.setPlaybackRate(self._speed)
        except Exception:  # noqa: BLE001
            pass
        if self._system_available and self._system_tts is not None:
            try:
                self._system_tts.setRate(self.system_rate_for(self._speed))
            except Exception:  # noqa: BLE001
                pass
        self.speedChanged.emit(self._speed)

    # ------------------------------------------------------------------ 播放
    def speak(self, text: str, profile: VoiceProfile) -> bool:
        """开始朗读。返回是否已成功发起（同步部分）。"""
        if not (text or "").strip():
            return False
        self.stop()

        provider = self._provider_for(profile)
        if provider is not None:
            ok = self._speak_with(provider, text, profile)
            if ok:
                return True
            self.errorOccurred.emit(
                f"{provider.name} 合成失败，已回退系统朗读")

        if self._system_available and self._system_tts is not None:
            try:
                self._system_tts.setRate(self.system_rate_for(self._speed))
                self._system_tts.say(text)
                self._using_edge = False
                self.playingChanged.emit(True)
                return True
            except Exception as exc:  # noqa: BLE001
                self.errorOccurred.emit(str(exc))
        return False

    def _speak_with(self, provider: TTSProvider, text: str,
                    profile: VoiceProfile) -> bool:
        """调用指定后端合成并播放（异步生成，完成即播放）。"""
        try:
            tmp_dir = tempfile.gettempdir()
            out_path = os.path.join(
                tmp_dir, "7mao_tts_%s.mp3" % uuid.uuid4().hex[:12])

            if not provider.synthesize(text, profile, out_path):
                return False
            if not os.path.isfile(out_path) or os.path.getsize(out_path) < 512:
                return False
            if not self._ensure_audio():
                return False
            self._tmp_files.append(out_path)
            self._cleanup_old()
            self._using_edge = provider.id == "edge"
            self._player.setSource(QUrl.fromLocalFile(out_path))
            # 新 source 会重置速率，必须在 play 前重新应用当前倍速
            self._player.setPlaybackRate(self._speed)
            self._player.play()
            return True
        except Exception as exc:  # noqa: BLE001
            self.errorOccurred.emit(str(exc))
            return False

    # 兼容旧调用名（保留私有别名，减少外部改动面）
    def _speak_edge(self, text: str, profile: VoiceProfile) -> bool:
        provider = self._providers.get("edge")
        if provider is None or not provider.available():
            return False
        return self._speak_with(provider, text, profile)

    def _cleanup_old(self, keep: int = 3) -> None:
        """只保留最近几个临时文件，避免反复朗读堆积。"""
        while len(self._tmp_files) > keep:
            old = self._tmp_files.pop(0)
            try:
                os.remove(old)
            except OSError:
                pass

    def stop(self) -> None:
        """停止播放并清理临时文件。"""
        try:
            if self._using_edge:
                self._player.stop()
            elif self._system_tts is not None and self._system_available:
                self._system_tts.stop()
        except Exception:  # noqa: BLE001
            pass
        self._imported_audio = None
        self.playingChanged.emit(False)
        for path in self._tmp_files:
            try:
                os.remove(path)
            except OSError:
                pass
        self._tmp_files.clear()

    def set_volume(self, value: float) -> None:
        """0.0 ~ 1.0。"""
        try:
            if self._audio is not None:
                self._audio.setVolume(max(0.0, min(1.0, value)))
        except Exception:  # noqa: BLE001
            pass

    def cleanup(self) -> None:
        """退出前调用。"""
        self.stop()
