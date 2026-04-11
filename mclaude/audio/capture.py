"""Microphone audio capture using sounddevice.

Requires: pip install mclaude-hub[audio-stt]
  (installs sounddevice + numpy)

Provides a simple start/stop recorder that buffers audio in memory.
The captured PCM bytes can be passed directly to any SttBackend.transcribe().

Usage:
    from mclaude.audio.capture import AudioRecorder

    recorder = AudioRecorder()
    recorder.start()
    # ... user speaks ...
    recorder.stop()

    pcm = recorder.get_audio_bytes()
    result = stt.transcribe(pcm, sample_rate=recorder.sample_rate)
"""
from __future__ import annotations

import io
import logging
import struct
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

# Default recording parameters
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_DTYPE = "int16"


def _has_sounddevice() -> bool:
    try:
        import sounddevice  # noqa: F401
        return True
    except (ImportError, OSError):
        # OSError can occur if PortAudio is not installed
        return False


class AudioRecorder:
    """Records audio from the default microphone using sounddevice.

    Thread-safe: start/stop can be called from any thread.
    The recording itself runs in sounddevice's internal callback thread.
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._buffer: list[bytes] = []
        self._stream = None
        self._lock = threading.Lock()
        self._recording = False

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        """Begin recording from the default microphone.

        Raises RuntimeError if already recording or sounddevice unavailable.
        """
        if not _has_sounddevice():
            raise RuntimeError(
                "sounddevice is required for audio capture. "
                "Install with: pip install mclaude-hub[audio-stt]"
            )

        with self._lock:
            if self._recording:
                raise RuntimeError("Already recording")

            import sounddevice as sd

            self._buffer = []

            def _callback(indata, frames, time_info, status):  # noqa: ANN001
                if status:
                    logger.warning("sounddevice status: %s", status)
                # indata is a numpy array; copy the raw bytes
                self._buffer.append(indata.tobytes())

            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=DEFAULT_DTYPE,
                callback=_callback,
                blocksize=1024,
            )
            self._stream.start()
            self._recording = True
            logger.info("Recording started at %d Hz, %d ch", self.sample_rate, self.channels)

    def stop(self) -> None:
        """Stop recording and close the stream."""
        with self._lock:
            if not self._recording:
                return
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            self._recording = False
            logger.info("Recording stopped, %d chunks captured", len(self._buffer))

    def get_audio_bytes(self) -> bytes:
        """Return the captured audio as raw PCM int16 bytes.

        Can be called after stop(). Returns empty bytes if nothing was recorded.
        """
        with self._lock:
            return b"".join(self._buffer)

    def get_audio_wav(self) -> bytes:
        """Return the captured audio as a complete WAV file in memory.

        Useful for saving to disk or sending to APIs that expect WAV format.
        """
        pcm = self.get_audio_bytes()
        return self._pcm_to_wav(pcm, self.sample_rate, self.channels)

    def get_duration_sec(self) -> float:
        """Return the duration of captured audio in seconds."""
        pcm = self.get_audio_bytes()
        bytes_per_sample = 2  # int16
        total_samples = len(pcm) // (bytes_per_sample * self.channels)
        return total_samples / max(self.sample_rate, 1)

    def clear(self) -> None:
        """Discard any buffered audio."""
        with self._lock:
            self._buffer = []

    @staticmethod
    def is_available() -> bool:
        """Check if audio capture is available on this system."""
        return _has_sounddevice()

    @staticmethod
    def _pcm_to_wav(pcm: bytes, sample_rate: int, channels: int) -> bytes:
        """Wrap raw PCM int16 bytes in a WAV header."""
        bits_per_sample = 16
        byte_rate = sample_rate * channels * bits_per_sample // 8
        block_align = channels * bits_per_sample // 8
        data_size = len(pcm)

        buf = io.BytesIO()
        # RIFF header
        buf.write(b"RIFF")
        buf.write(struct.pack("<I", 36 + data_size))
        buf.write(b"WAVE")
        # fmt chunk
        buf.write(b"fmt ")
        buf.write(struct.pack("<I", 16))  # chunk size
        buf.write(struct.pack("<HH", 1, channels))  # PCM, channels
        buf.write(struct.pack("<II", sample_rate, byte_rate))
        buf.write(struct.pack("<HH", block_align, bits_per_sample))
        # data chunk
        buf.write(b"data")
        buf.write(struct.pack("<I", data_size))
        buf.write(pcm)

        return buf.getvalue()
