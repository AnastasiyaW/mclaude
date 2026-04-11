"""Real TTS backend using pyttsx3 (cross-platform native speech synthesis).

Requires: pip install mclaude-hub[audio-tts]
  (installs pyttsx3)

Uses the OS native speech engine:
  - Windows: SAPI5
  - macOS: NSSpeechSynthesizer
  - Linux: espeak (requires libespeak1)

Usage:
    import mclaude.audio.tts_pyttsx3  # registers "pyttsx3"
    tts = audio_registry.get_tts("pyttsx3")
    tts.speak("Hello!")              # plays through speakers
    wav = tts.synthesize("Hello!")   # returns WAV bytes
"""
from __future__ import annotations

import logging
import os
import tempfile

from mclaude.audio.base import TtsBackend
from mclaude.audio.registry import audio_registry

logger = logging.getLogger(__name__)


def _has_pyttsx3() -> bool:
    try:
        import pyttsx3  # noqa: F401
        return True
    except ImportError:
        return False


class Pyttsx3Tts(TtsBackend):
    """Cross-platform text-to-speech via pyttsx3."""

    name = "pyttsx3"

    def __init__(self, rate: int | None = None, volume: float | None = None) -> None:
        self._rate = rate
        self._volume = volume

    def _make_engine(self):  # noqa: ANN202
        """Create a fresh engine instance.

        pyttsx3 engines are NOT thread-safe and should not be reused across
        calls in multi-threaded environments. Creating a fresh engine per call
        is cheap (~5ms) and avoids subtle deadlocks.
        """
        import pyttsx3
        engine = pyttsx3.init()
        if self._rate is not None:
            engine.setProperty("rate", self._rate)
        if self._volume is not None:
            engine.setProperty("volume", self._volume)
        return engine

    def speak(self, text: str) -> None:
        """Speak text through the default audio device. Blocks until done."""
        engine = self._make_engine()
        engine.say(text)
        engine.runAndWait()

    def synthesize(self, text: str) -> bytes:
        """Synthesize text to WAV bytes without playing.

        Creates a temp file, renders speech to it, reads the bytes, cleans up.
        """
        fd, tmp_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            engine = self._make_engine()
            engine.save_to_file(text, tmp_path)
            engine.runAndWait()

            with open(tmp_path, "rb") as f:
                wav_bytes = f.read()

            if len(wav_bytes) < 44:
                logger.warning("pyttsx3 produced an empty or invalid WAV file")
                # Return minimal valid WAV header as fallback
                return self._minimal_wav()

            return wav_bytes
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def is_available(self) -> bool:
        """Check if pyttsx3 is importable and can initialize an engine."""
        if not _has_pyttsx3():
            return False
        try:
            import pyttsx3
            engine = pyttsx3.init()
            # Quick sanity check - engine should have voices
            voices = engine.getProperty("voices")
            return voices is not None and len(voices) > 0
        except Exception:
            return False

    @staticmethod
    def _minimal_wav() -> bytes:
        """Return a minimal valid but empty WAV file."""
        return (
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


# Register at import time
audio_registry.register_tts("pyttsx3", Pyttsx3Tts)
