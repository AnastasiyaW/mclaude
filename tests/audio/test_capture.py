"""Tests for audio capture (microphone recording).

All tests mock sounddevice - real audio hardware is not available in CI.
"""
from __future__ import annotations

import struct
from unittest.mock import MagicMock, patch, call

import pytest

from mclaude.audio.capture import AudioRecorder


class FakeInputStream:
    """Mock sounddevice.InputStream that simulates audio capture."""

    def __init__(self, **kwargs):
        self.callback = kwargs.get("callback")
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True

    def inject_audio(self, data: bytes) -> None:
        """Simulate audio data arriving from the microphone."""
        import numpy as np
        arr = np.frombuffer(data, dtype=np.int16).reshape(-1, 1)
        self.callback(arr, len(arr), None, None)


@pytest.fixture
def mock_sd():
    """Mock sounddevice module."""
    with patch("mclaude.audio.capture._has_sounddevice", return_value=True):
        with patch("mclaude.audio.capture.sd", create=True) as sd:
            fake_stream = FakeInputStream()
            sd.InputStream = MagicMock(return_value=fake_stream)
            yield sd, fake_stream


def test_recorder_start_opens_stream() -> None:
    """start() creates and starts a sounddevice InputStream."""
    fake_stream = FakeInputStream()

    with patch("mclaude.audio.capture._has_sounddevice", return_value=True):
        with patch.dict("sys.modules", {"sounddevice": MagicMock()}):
            import sys
            sd_mock = sys.modules["sounddevice"]
            sd_mock.InputStream = MagicMock(return_value=fake_stream)

            recorder = AudioRecorder(sample_rate=16000, channels=1)
            # Patch the import inside start()
            with patch("mclaude.audio.capture.sd", sd_mock, create=True):
                recorder.start()

            assert recorder.is_recording is True
            assert fake_stream.started is True


def test_recorder_stop_closes_stream() -> None:
    """stop() stops and closes the stream."""
    recorder = AudioRecorder()

    fake_stream = MagicMock()
    recorder._stream = fake_stream
    recorder._recording = True

    recorder.stop()

    assert recorder.is_recording is False
    fake_stream.stop.assert_called_once()
    fake_stream.close.assert_called_once()


def test_recorder_stop_when_not_recording() -> None:
    """stop() is a no-op when not recording."""
    recorder = AudioRecorder()
    recorder.stop()  # should not raise
    assert recorder.is_recording is False


def test_recorder_double_start_raises() -> None:
    """Cannot start recording twice."""
    recorder = AudioRecorder()
    recorder._recording = True

    with patch("mclaude.audio.capture._has_sounddevice", return_value=True):
        with pytest.raises(RuntimeError, match="Already recording"):
            recorder.start()


def test_recorder_no_sounddevice_raises() -> None:
    """start() raises if sounddevice is not available."""
    recorder = AudioRecorder()

    with patch("mclaude.audio.capture._has_sounddevice", return_value=False):
        with pytest.raises(RuntimeError, match="sounddevice is required"):
            recorder.start()


def test_recorder_get_audio_bytes() -> None:
    """get_audio_bytes() concatenates all buffered chunks."""
    recorder = AudioRecorder()
    recorder._buffer = [b"\x01\x00" * 10, b"\x02\x00" * 5]

    pcm = recorder.get_audio_bytes()
    assert len(pcm) == 30  # 10*2 + 5*2


def test_recorder_get_audio_bytes_empty() -> None:
    """get_audio_bytes() returns empty bytes when nothing was recorded."""
    recorder = AudioRecorder()
    assert recorder.get_audio_bytes() == b""


def test_recorder_clear() -> None:
    """clear() discards buffered audio."""
    recorder = AudioRecorder()
    recorder._buffer = [b"\x01\x00" * 100]
    recorder.clear()
    assert recorder.get_audio_bytes() == b""


def test_recorder_get_duration() -> None:
    """get_duration_sec() calculates correct duration from buffer size."""
    recorder = AudioRecorder(sample_rate=16000, channels=1)
    # 1 second of int16 mono at 16kHz = 32000 bytes
    recorder._buffer = [b"\x00\x00" * 16000]

    duration = recorder.get_duration_sec()
    assert duration == pytest.approx(1.0, abs=0.001)


def test_recorder_get_audio_wav() -> None:
    """get_audio_wav() wraps PCM in a valid WAV header."""
    recorder = AudioRecorder(sample_rate=16000, channels=1)
    # 0.5 seconds of silence
    recorder._buffer = [b"\x00\x00" * 8000]

    wav = recorder.get_audio_wav()
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"

    # Parse data size from WAV header
    data_size = struct.unpack_from("<I", wav, 40)[0]
    assert data_size == 16000  # 8000 samples * 2 bytes


def test_recorder_pcm_to_wav_roundtrip() -> None:
    """_pcm_to_wav produces valid WAV that contains the original PCM."""
    pcm = b"\x01\x02\x03\x04" * 100
    wav = AudioRecorder._pcm_to_wav(pcm, sample_rate=16000, channels=1)

    # WAV = 44 byte header + PCM data
    assert wav[:4] == b"RIFF"
    assert wav[44:] == pcm


def test_recorder_is_available_static() -> None:
    """is_available() reflects whether sounddevice can be imported."""
    with patch("mclaude.audio.capture._has_sounddevice", return_value=True):
        assert AudioRecorder.is_available() is True

    with patch("mclaude.audio.capture._has_sounddevice", return_value=False):
        assert AudioRecorder.is_available() is False
