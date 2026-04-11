"""Tests for the audio backend interfaces and stub implementations."""
from __future__ import annotations

import pytest

from mclaude.audio.base import SttBackend, TranscriptionResult, TtsBackend
from mclaude.audio.registry import audio_registry
import mclaude.audio.stubs  # register stubs


def test_stub_stt_registered() -> None:
    assert "stub" in audio_registry.stt_names()
    stt = audio_registry.get_stt("stub")
    assert isinstance(stt, SttBackend)


def test_stub_tts_registered() -> None:
    assert "stub" in audio_registry.tts_names()
    tts = audio_registry.get_tts("stub")
    assert isinstance(tts, TtsBackend)


def test_stub_stt_returns_deterministic_text() -> None:
    stt = audio_registry.get_stt("stub")
    fake_audio = b"\x00\x00" * 16000  # 1 second of silence at 16kHz mono
    result = stt.transcribe(fake_audio, sample_rate=16000)
    assert isinstance(result, TranscriptionResult)
    assert result.text == "(stub transcription)"
    assert result.backend == "stub"


def test_stub_tts_records_speech() -> None:
    tts = audio_registry.get_tts("stub")
    tts.speak("Hello, world")
    tts.speak("second line")
    # Stub keeps a list on the instance
    assert hasattr(tts, "spoken")
    assert "Hello, world" in tts.spoken
    assert "second line" in tts.spoken


def test_stub_tts_synthesize_returns_valid_wav_header() -> None:
    tts = audio_registry.get_tts("stub")
    data = tts.synthesize("anything")
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"


def test_unknown_backend_raises() -> None:
    with pytest.raises(KeyError):
        audio_registry.get_stt("nonexistent")
    with pytest.raises(KeyError):
        audio_registry.get_tts("nonexistent")


def test_is_available_default_true() -> None:
    stt = audio_registry.get_stt("stub")
    tts = audio_registry.get_tts("stub")
    assert stt.is_available() is True
    assert tts.is_available() is True
