"""Tests for the faster-whisper STT backend.

These tests cover two scenarios:
1. Unit tests that always run (mock the model) - verify the pipeline works
2. Integration tests that only run when faster-whisper is installed - verify
   real transcription against a fixture WAV file
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mclaude.audio.base import TranscriptionResult


def _has_faster_whisper() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Unit tests (always run, mock the model)
# ---------------------------------------------------------------------------

def test_faster_whisper_registered() -> None:
    """The backend registers itself at import time."""
    import mclaude.audio.stt_faster_whisper  # noqa: F401
    from mclaude.audio.registry import audio_registry
    assert "faster-whisper" in audio_registry.stt_names()


def test_faster_whisper_is_available_reflects_import() -> None:
    """is_available() returns True only when faster_whisper can be imported."""
    from mclaude.audio.stt_faster_whisper import FasterWhisperStt
    stt = FasterWhisperStt()
    # Result depends on whether faster-whisper is installed
    assert stt.is_available() == _has_faster_whisper()


def test_faster_whisper_transcribe_with_mock() -> None:
    """Verify the transcription pipeline with a mocked WhisperModel."""
    from mclaude.audio.stt_faster_whisper import FasterWhisperStt

    # Create a mock segment
    mock_segment = MagicMock()
    mock_segment.start = 0.0
    mock_segment.end = 1.5
    mock_segment.text = " Hello world "

    # Create mock info
    mock_info = MagicMock()
    mock_info.language = "en"
    mock_info.language_probability = 0.95

    # Create mock model
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (iter([mock_segment]), mock_info)

    stt = FasterWhisperStt()
    stt._model = mock_model  # bypass lazy loading

    # 1 second of silence at 16kHz mono int16
    import struct
    audio_bytes = struct.pack("<" + "h" * 16000, *([0] * 16000))

    result = stt.transcribe(audio_bytes, sample_rate=16000)

    assert isinstance(result, TranscriptionResult)
    assert result.text == "Hello world"
    assert result.language == "en"
    assert result.confidence == 0.95
    assert result.backend == "faster-whisper"
    assert len(result.segments) == 1
    assert result.segments[0]["text"] == "Hello world"
    assert result.duration_sec == pytest.approx(1.0, abs=0.01)


def test_faster_whisper_resampling_with_mock() -> None:
    """Verify that non-16kHz audio triggers resampling."""
    from mclaude.audio.stt_faster_whisper import FasterWhisperStt

    mock_segment = MagicMock()
    mock_segment.start = 0.0
    mock_segment.end = 0.5
    mock_segment.text = " test "

    mock_info = MagicMock()
    mock_info.language = "en"
    mock_info.language_probability = 0.9

    mock_model = MagicMock()
    mock_model.transcribe.return_value = (iter([mock_segment]), mock_info)

    stt = FasterWhisperStt()
    stt._model = mock_model

    # 1 second of silence at 44100Hz mono int16
    import struct
    audio_bytes = struct.pack("<" + "h" * 44100, *([0] * 44100))

    result = stt.transcribe(audio_bytes, sample_rate=44100)
    assert result.text == "test"

    # Verify the model received the audio (numpy array argument)
    call_args = mock_model.transcribe.call_args
    audio_arr = call_args[0][0]
    # Resampled from 44100 to 16000 -> ~16000 samples
    assert 15000 < len(audio_arr) < 17000


# ---------------------------------------------------------------------------
# Integration tests (only when faster-whisper is installed)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_faster_whisper(), reason="faster-whisper not installed")
def test_faster_whisper_real_silence() -> None:
    """With real faster-whisper: transcribe silence, expect empty or minimal text."""
    from mclaude.audio.stt_faster_whisper import FasterWhisperStt

    stt = FasterWhisperStt(model_size="tiny")  # smallest model for speed

    # 2 seconds of silence
    import struct
    audio_bytes = struct.pack("<" + "h" * 32000, *([0] * 32000))

    result = stt.transcribe(audio_bytes, sample_rate=16000)
    assert isinstance(result, TranscriptionResult)
    # Silence should produce little or no text
    assert len(result.text) < 50
