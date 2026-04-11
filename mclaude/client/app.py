"""
Desktop client entry point.

This module defines the runtime entry `run_client()` and the `ClientConfig`
dataclass. PyQt6 imports happen INSIDE the function, not at module level, so
the file can be imported on headless machines and in tests without needing
a display.

Architecture:

    +------------------+     +------------------+     +--------------+
    |  Main Qt thread  | <-> |  Network worker  | <-> |  Hub server  |
    |  (UI, tray)      |     |  (WebSocket)     |     |  (remote)    |
    +------------------+     +------------------+     +--------------+
             |
             +--- Audio worker thread (STT/TTS backend)
             +--- Notification dispatcher (plyer / Qt fallback)
             +--- Hotkey listener (pynput)

All cross-thread communication via Qt signals and slots.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ClientConfig:
    """Runtime configuration for the desktop client."""

    hub_url: str = ""
    token: str = ""
    identity: str = "me"
    project_id: str = "default"

    # Audio
    stt_backend: str = "stub"
    tts_backend: str = "stub"
    speak_notifications: bool = False
    voice_hotkey: str = "ctrl+alt+space"

    # Notification backend: "auto" picks plyer -> Qt fallback
    notification_backend: str = "auto"

    # Where the project files live (for file-fallback mclaude calls)
    project_root: Path | str = "."

    # Startup behavior
    start_minimized: bool = True
    show_welcome: bool = True


def run_client(config: ClientConfig) -> int:
    """Launch the PyQt6 tray client. Returns exit code.

    This function imports PyQt6 lazily so the rest of the package works
    on machines without Qt.
    """
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
        from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
    except ImportError as e:
        raise RuntimeError(
            "PyQt6 is required for the desktop client. Install with: pip install PyQt6"
        ) from e

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    # -- Tray icon -----------------------------------------------------

    def _make_icon() -> QIcon:
        pix = QPixmap(32, 32)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(30, 34, 44))
        p.setPen(QColor(200, 200, 220))
        p.drawRoundedRect(4, 4, 24, 24, 5, 5)
        p.setPen(QColor(230, 230, 240))
        p.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "H")
        p.end()
        return QIcon(pix)

    tray = QSystemTrayIcon(_make_icon(), app)
    tray.setToolTip(f"mclaude-hub - {config.identity}")

    menu = QMenu()

    status_action = QAction(f"identity: {config.identity}")
    status_action.setEnabled(False)
    menu.addAction(status_action)

    hub_action = QAction(f"hub: {config.hub_url or '(offline)'}")
    hub_action.setEnabled(False)
    menu.addAction(hub_action)

    menu.addSeparator()

    def _notify_test() -> None:
        _send_notification("mclaude-hub", "Test notification from the tray menu.")

    test_action = QAction("Send test notification")
    test_action.triggered.connect(_notify_test)
    menu.addAction(test_action)

    speak_action = QAction(f"Test TTS ({config.tts_backend})")
    def _tts_test() -> None:
        from mclaude.audio import audio_registry
        _ensure_audio_backends(config)
        backend_name = config.tts_backend
        if backend_name not in audio_registry.tts_names():
            _send_notification("TTS error", f"Backend {backend_name!r} not available, using stub")
            backend_name = "stub"
        tts = audio_registry.get_tts(backend_name)
        tts.speak("This is a test. Voice output is working.")
        _send_notification("TTS test", f"Spoke via {backend_name}: This is a test.")
    speak_action.triggered.connect(_tts_test)
    menu.addAction(speak_action)

    menu.addSeparator()

    quit_action = QAction("Quit")
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)

    tray.setContextMenu(menu)
    tray.show()

    if config.show_welcome:
        _send_notification(
            "mclaude-hub running",
            f"Connected as {config.identity}. Right-click the tray icon for options.",
        )

    return app.exec()


def _ensure_audio_backends(config: ClientConfig) -> None:
    """Import audio backend modules based on config so they register themselves.

    Always imports stubs as a fallback. Then tries to import the real backend
    module matching the config. If import fails (missing dependency), the stub
    remains available.
    """
    import mclaude.audio.stubs  # noqa: F401 - always available

    if config.stt_backend == "faster-whisper":
        try:
            import mclaude.audio.stt_faster_whisper  # noqa: F401
        except ImportError:
            pass

    if config.tts_backend == "pyttsx3":
        try:
            import mclaude.audio.tts_pyttsx3  # noqa: F401
        except ImportError:
            pass


def _send_notification(title: str, message: str) -> None:
    """Try plyer first, fall back to Qt tray message.

    Defined at module level so tests can monkey-patch it without importing Qt.
    """
    try:
        from plyer import notification  # type: ignore[import-not-found]
        notification.notify(title=title, message=message, app_name="mclaude-hub", timeout=5)
        return
    except Exception:
        pass
    # If plyer fails, try Qt tray message. This requires an active QApplication,
    # so it only works when called from inside run_client().
    try:
        from PyQt6.QtWidgets import QApplication, QSystemTrayIcon
        app = QApplication.instance()
        if app is None:
            return
        # Search for any tray icon on the app
        for widget in app.allWidgets():
            if isinstance(widget, QSystemTrayIcon):
                widget.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)
                return
    except Exception:
        pass
