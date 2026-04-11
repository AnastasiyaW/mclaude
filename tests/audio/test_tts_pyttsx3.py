"""Tests for the pyttsx3 TTS backend.

Unit tests always run (mock the engine). Integration tests only when pyttsx3
is installed and can initialize.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, mock_open

import pytest

from mclaude.audio.base import TtsBackend


def _has_pyttsx3() -> bool:
    try:
        import pyttsx3  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Unit tests (always run, mock the engine)
# ---------------------------------------------------------------------------

def test_pyttsx3_registered() -> None:
    """The backend registers itself at import time."""
    import mclaude.audio.tts_pyttsx3  # noqa: F401
    from mclaude.audio.registry import audio_registry
    assert "pyttsx3" in audio_registry.tts_names()


def test_pyttsx3_is_available_reflects_import() -> None:
    """is_available() depends on pyttsx3 being importable."""
    from mclaude.audio.tts_pyttsx3 import Pyttsx3Tts
    tts = Pyttsx3Tts()
    # If pyttsx3 is not installed, is_available returns False
    if not _has_pyttsx3():
        assert tts.is_available() is False


def test_pyttsx3_speak_with_mock() -> None:
    """Verify speak() calls say + runAndWait on the engine."""
    from mclaude.audio.tts_pyttsx3 import Pyttsx3Tts

    mock_engine = MagicMock()

    tts = Pyttsx3Tts()
    with patch.object(tts, "_make_engine", return_value=mock_engine):
        tts.speak("Hello world")

    mock_engine.say.assert_called_once_with("Hello world")
    mock_engine.runAndWait.assert_called_once()


def test_pyttsx3_synthesize_with_mock() -> None:
    """Verify synthesize() saves to file, reads bytes, cleans up."""
    from mclaude.audio.tts_pyttsx3 import Pyttsx3Tts
    import struct

    # Create a realistic WAV content (header + some silence)
    wav_header = (
        b"RIFF"
        + struct.pack("<I", 36 + 100)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + struct.pack("<HH", 1, 1)
        + struct.pack("<II", 16000, 32000)
        + struct.pack("<HH", 2, 16)
        + b"data"
        + struct.pack("<I", 100)
        + b"\x00" * 100
    )

    mock_engine = MagicMock()

    tts = Pyttsx3Tts()

    import tempfile
    import os

    # Create a real temp file with WAV content
    fd, tmp_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)

    def fake_save(text: str, path: str) -> None:
        with open(path, "wb") as f:
            f.write(wav_header)

    mock_engine.save_to_file.side_effect = fake_save

    with patch("tempfile.mkstemp", return_value=(fd, tmp_path)):
        with patch("os.close"):
            with patch.object(tts, "_make_engine", return_value=mock_engine):
                result = tts.synthesize("Test text")

    assert result[:4] == b"RIFF"
    assert result[8:12] == b"WAVE"
    assert len(result) > 44  # more than just a header

    mock_engine.save_to_file.assert_called_once()
    mock_engine.runAndWait.assert_called_once()


def test_pyttsx3_minimal_wav_fallback() -> None:
    """The fallback WAV is valid."""
    from mclaude.audio.tts_pyttsx3 import Pyttsx3Tts
    wav = Pyttsx3Tts._minimal_wav()
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"


def test_pyttsx3_custom_rate_and_volume() -> None:
    """Custom rate/volume are applied to the engine."""
    from mclaude.audio.tts_pyttsx3 import Pyttsx3Tts

    mock_engine = MagicMock()

    tts = Pyttsx3Tts(rate=150, volume=0.8)
    # pyttsx3 is imported inside _make_engine, so patch at sys.modules level
    mock_pyttsx3 = MagicMock()
    mock_pyttsx3.init.return_value = mock_engine

    with patch.dict("sys.modules", {"pyttsx3": mock_pyttsx3}):
        engine = tts._make_engine()

    mock_engine.setProperty.assert_any_call("rate", 150)
    mock_engine.setProperty.assert_any_call("volume", 0.8)


# ---------------------------------------------------------------------------
# Integration tests (only when pyttsx3 is installed and working)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_pyttsx3(), reason="pyttsx3 not installed")
def test_pyttsx3_real_synthesize() -> None:
    """With real pyttsx3: synthesize should produce valid WAV bytes."""
    from mclaude.audio.tts_pyttsx3 import Pyttsx3Tts

    tts = Pyttsx3Tts()
    if not tts.is_available():
        pytest.skip("pyttsx3 engine not available (no voices?)")

    wav = tts.synthesize("hello")
    assert wav[:4] == b"RIFF"
    assert len(wav) > 1000  # real WAV should be at least a few KB


@pytest.mark.skipif(not _has_pyttsx3(), reason="pyttsx3 not installed")
def test_pyttsx3_real_is_available() -> None:
    """With real pyttsx3: is_available should work without crashing."""
    from mclaude.audio.tts_pyttsx3 import Pyttsx3Tts
    tts = Pyttsx3Tts()
    # Should not raise
    result = tts.is_available()
    assert isinstance(result, bool)
