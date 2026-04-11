"""Abstract base classes for STT and TTS backends.

The interfaces are deliberately narrow:

- **SttBackend.transcribe(audio_bytes, sample_rate)** -> TranscriptionResult
- **TtsBackend.speak(text)** -> None  (plays audio synchronously)
- **TtsBackend.synthesize(text)** -> bytes  (returns WAV bytes without playing)

Any backend that can satisfy this interface can be plugged in without
touching the desktop client code. The client picks a backend by name from
config and calls only these methods.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TranscriptionResult:
    """What an STT backend returns."""

    text: str
    language: str = "en"
    confidence: float = 0.0
    segments: list[dict] = field(default_factory=list)  # [{start, end, text, ...}]
    duration_sec: float = 0.0
    backend: str = ""


class SttBackend(ABC):
    """Abstract speech-to-text backend."""

    name: str = "abstract-stt"

    @abstractmethod
    def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> TranscriptionResult:
        """Transcribe PCM audio bytes into text. Blocking call."""
        ...

    def is_available(self) -> bool:
        """Return True if this backend can actually run (dependencies installed, models downloaded)."""
        return True


class TtsBackend(ABC):
    """Abstract text-to-speech backend."""

    name: str = "abstract-tts"

    @abstractmethod
    def speak(self, text: str) -> None:
        """Speak the text through the default audio device. Blocking call."""
        ...

    def synthesize(self, text: str) -> bytes:
        """Return WAV bytes without playing. Optional; default raises NotImplementedError."""
        raise NotImplementedError(f"{self.name} does not support synthesize-to-bytes")

    def is_available(self) -> bool:
        return True
