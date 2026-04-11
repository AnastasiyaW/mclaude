"""Real STT backend using faster-whisper (CTranslate2-based Whisper).

Requires: pip install mclaude-hub[audio-stt]
  (installs faster-whisper, sounddevice, numpy)

The model is downloaded on first use and cached in the default HuggingFace
cache directory (~/.cache/huggingface/). The "base" model is ~150 MB; for
better accuracy use "small" (~500 MB) or "medium" (~1.5 GB).

Usage:
    import mclaude.audio.stt_faster_whisper  # registers "faster-whisper"
    stt = audio_registry.get_stt("faster-whisper")
    result = stt.transcribe(pcm_bytes, sample_rate=16000)
"""
from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

from mclaude.audio.base import SttBackend, TranscriptionResult
from mclaude.audio.registry import audio_registry

if TYPE_CHECKING:
    import numpy as np
    from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


def _has_faster_whisper() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


class FasterWhisperStt(SttBackend):
    """Local speech-to-text via faster-whisper."""

    name = "faster-whisper"

    def __init__(self, model_size: str = "base", device: str = "cpu") -> None:
        self._model_size = model_size
        self._device = device
        self._model: WhisperModel | None = None

    def _get_model(self) -> WhisperModel:
        """Lazy-load the model on first transcription call."""
        if self._model is None:
            from faster_whisper import WhisperModel
            logger.info("Loading faster-whisper model %r on %s...", self._model_size, self._device)
            self._model = WhisperModel(self._model_size, device=self._device)
            logger.info("Model loaded.")
        return self._model

    def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> TranscriptionResult:
        """Transcribe raw PCM int16 mono audio bytes."""
        import numpy as np

        # Convert raw PCM int16 bytes to float32 array normalized to [-1, 1]
        audio_i16 = np.frombuffer(audio_bytes, dtype=np.int16)
        audio_f32 = audio_i16.astype(np.float32) / 32768.0

        # Resample to 16kHz if needed (faster-whisper expects 16kHz)
        if sample_rate != 16000:
            ratio = 16000 / sample_rate
            new_len = int(len(audio_f32) * ratio)
            indices = np.linspace(0, len(audio_f32) - 1, new_len).astype(np.int64)
            audio_f32 = audio_f32[indices]

        model = self._get_model()
        segments_iter, info = model.transcribe(audio_f32, beam_size=5)

        segments = []
        texts = []
        for seg in segments_iter:
            segments.append({
                "start": seg.start,
                "end": seg.end,
                "text": seg.text.strip(),
            })
            texts.append(seg.text.strip())

        full_text = " ".join(texts)
        duration = len(audio_i16) / max(sample_rate, 1)

        return TranscriptionResult(
            text=full_text,
            language=info.language if info.language else "en",
            confidence=info.language_probability if hasattr(info, "language_probability") else 0.0,
            segments=segments,
            duration_sec=duration,
            backend=self.name,
        )

    def is_available(self) -> bool:
        return _has_faster_whisper()


# Register if the dependency is importable
audio_registry.register_stt("faster-whisper", FasterWhisperStt)
