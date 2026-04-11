"""Zero-dependency stub backends for STT and TTS.

These are placeholder implementations that let tests and the desktop client
run without installing faster-whisper, pyttsx3, sounddevice, etc. They always
return deterministic fake output and do not actually use audio hardware.

Real backends will be added as separate modules (stt_faster_whisper.py,
tts_pyttsx3.py) that register under the same names but with working code.
"""
from __future__ import annotations

from mclaude.audio.base import SttBackend, TranscriptionResult, TtsBackend
from mclaude.audio.registry import audio_registry


class StubSttBackend(SttBackend):
    """Fake STT that always returns '(stub transcription)' regardless of input."""

    name = "stub"

    def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> TranscriptionResult:
        return TranscriptionResult(
            text="(stub transcription)",
            language="en",
            confidence=1.0,
            segments=[{"start": 0.0, "end": 1.0, "text": "(stub transcription)"}],
            duration_sec=float(len(audio_bytes)) / max(sample_rate, 1) / 2,  # rough estimate
            backend=self.name,
        )


class StubTtsBackend(TtsBackend):
    """Fake TTS that records what it was asked to speak but does not play audio."""

    name = "stub"

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def synthesize(self, text: str) -> bytes:
        # Return a minimal WAV header + silence - enough to be valid
        wav_header = (
            b"RIFF"
            b"\x24\x00\x00\x00"
            b"WAVE"
            b"fmt "
            b"\x10\x00\x00\x00"
            b"\x01\x00\x01\x00"
            b"\x44\xac\x00\x00"
            b"\x88\x58\x01\x00"
            b"\x02\x00\x10\x00"
            b"data"
            b"\x00\x00\x00\x00"
        )
        return wav_header


# Register at import time
audio_registry.register_stt("stub", StubSttBackend)
audio_registry.register_tts("stub", StubTtsBackend)
