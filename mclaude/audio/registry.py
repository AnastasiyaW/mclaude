"""Global registry for audio backends.

A single process-wide registry holds references to all registered STT and TTS
backends. Backends are registered at import time by their module's `register()`
function; the desktop client looks them up by name.

This pattern keeps heavy dependencies (faster-whisper models, Coqui TTS models)
out of the import graph unless the user explicitly imports the relevant module.
"""
from __future__ import annotations

from typing import Callable

from mclaude.audio.base import SttBackend, TtsBackend


class _AudioRegistry:
    def __init__(self) -> None:
        self._stt: dict[str, Callable[[], SttBackend]] = {}
        self._tts: dict[str, Callable[[], TtsBackend]] = {}

    def register_stt(self, name: str, factory: Callable[[], SttBackend]) -> None:
        self._stt[name] = factory

    def register_tts(self, name: str, factory: Callable[[], TtsBackend]) -> None:
        self._tts[name] = factory

    def stt_names(self) -> list[str]:
        return sorted(self._stt)

    def tts_names(self) -> list[str]:
        return sorted(self._tts)

    def get_stt(self, name: str) -> SttBackend:
        if name not in self._stt:
            raise KeyError(f"STT backend not registered: {name!r}; available: {self.stt_names()}")
        return self._stt[name]()

    def get_tts(self, name: str) -> TtsBackend:
        if name not in self._tts:
            raise KeyError(f"TTS backend not registered: {name!r}; available: {self.tts_names()}")
        return self._tts[name]()


audio_registry = _AudioRegistry()
