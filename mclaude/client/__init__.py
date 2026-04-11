"""Desktop client - PyQt6 tray app with notifications and audio I/O.

v0.1 ships the skeleton: tray icon, notification display, hotkey handling,
WebSocket connection to hub, audio backend interfaces. Real STT/TTS is
loaded lazily from mclaude.audio when the user configures them.

Importing this module does NOT import PyQt6 at module top level, so the
package is safe to install on machines without Qt (CI, headless servers,
the hub-only deployment). The PyQt6 import happens inside `run_client()`
and related functions.
"""
from mclaude.client.app import ClientConfig, run_client

__all__ = ["ClientConfig", "run_client"]
