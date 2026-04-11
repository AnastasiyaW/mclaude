"""Audio I/O backends - STT and TTS with pluggable implementations.

The audio subsystem is defined as abstract interfaces here, with concrete
implementations in sibling modules:

- `stt_faster_whisper.py` - local Whisper via faster-whisper (recommended default)
- `stt_vosk.py`            - smaller local STT
- `stt_azure.py`           - cloud Azure Speech
- `stt_openai.py`          - OpenAI Whisper API
- `tts_pyttsx3.py`         - cross-platform native TTS (default)
- `tts_coqui.py`           - local neural TTS
- `tts_piper.py`           - local neural TTS via Piper binary
- `tts_azure.py`           - cloud Azure Speech
- `tts_elevenlabs.py`      - cloud ElevenLabs

Each backend implements SttBackend or TtsBackend and registers itself in a
global registry so the desktop client can select one by name from config
without importing every heavy dependency.

v0.1 ships with the two default backends (faster-whisper stub and pyttsx3 stub).
Real audio capture via sounddevice is in `capture.py`.
"""
from mclaude.audio.base import SttBackend, TtsBackend, TranscriptionResult
from mclaude.audio.registry import audio_registry

__all__ = ["SttBackend", "TtsBackend", "TranscriptionResult", "audio_registry"]
