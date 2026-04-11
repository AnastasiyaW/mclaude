# mclaude-hub - Architecture

## What is this

mclaude-hub is the **network + desktop + audio** extension for the file-based [mclaude](https://github.com/AnastasiyaW/mclaude) library. Where mclaude handles coordination between Claude Code sessions via files in `.claude/`, mclaude-hub adds:

- **Hub server** - a central relay so sessions on different machines can share state
- **Desktop client** - system tray icon, notifications, voice I/O, TTS
- **Claude bridge** - a small adapter that lets Claude Code push events to the hub and receive commands back

All five mclaude layers (locks, handoffs, memory, registry, messages) continue to work file-based. The hub is **additive**: if the server is down, everything degrades to local file mode. If the desktop client is off, events queue in files and surface later. Nothing hard-fails just because a piece is missing.

## The three new components

```
┌─────────────────────────────┐         ┌─────────────────────────────┐
│  Machine A (Anastasia)      │         │  Machine B (teammate)       │
│                             │         │                             │
│  ┌───────────────────────┐  │         │  ┌───────────────────────┐  │
│  │  Claude Code session  │  │         │  │  Claude Code session  │  │
│  └──────────┬────────────┘  │         │  └──────────┬────────────┘  │
│             │ stdin/stdout  │         │             │               │
│  ┌──────────▼────────────┐  │         │  ┌──────────▼────────────┐  │
│  │   Claude Bridge       │  │         │  │   Claude Bridge       │  │
│  │   (mclaude-hub.bridge)│  │         │  │   (mclaude-hub.bridge)│  │
│  └──────────┬────────────┘  │         │  └──────────┬────────────┘  │
│             │ HTTPS+WS       │         │             │               │
│             │                │         │             │               │
│  ┌──────────▼────────────┐  │         │  ┌──────────▼────────────┐  │
│  │   Desktop Client      │  │         │  │   Desktop Client      │  │
│  │   - Tray icon         │  │         │  │   - Tray icon         │  │
│  │   - Notifications     │  │         │  │   - Notifications     │  │
│  │   - STT (voice in)    │  │         │  │   - STT (voice in)    │  │
│  │   - TTS (voice out)   │  │         │  │   - TTS (voice out)   │  │
│  └──────────┬────────────┘  │         │  └──────────┬────────────┘  │
└─────────────┼───────────────┘         └─────────────┼───────────────┘
              │                                        │
              │             HTTPS + WebSocket          │
              │                                        │
              └──────────────────┬─────────────────────┘
                                 │
                    ┌────────────▼──────────────┐
                    │    Hub Server             │
                    │    (mclaude-hub.hub)      │
                    │                           │
                    │  - FastAPI REST API       │
                    │  - WebSocket broadcast    │
                    │  - SQLite state           │
                    │  - Event log              │
                    │  - Auth (bearer tokens)   │
                    └────────────┬──────────────┘
                                 │
                    ┌────────────▼──────────────┐
                    │   Cloud/VPS/LAN           │
                    │   (anywhere HTTPS reaches)│
                    └───────────────────────────┘
```

## Data flow scenarios

### Scenario 1 - Claude finishes a long task and wants to notify

1. Claude Code finishes a background task (e.g., `pytest` passes)
2. Claude calls the bridge: `bridge.notify("tests passed, 42/42")`
3. Bridge sends `POST /api/events` to hub with `{session_id, type: "completion", message, urgent: false}`
4. Hub stores the event, broadcasts via WebSocket to all connected clients of the same project
5. Desktop client on Machine A receives the event, shows a native notification via plyer
6. TTS is configured → client speaks "tests passed, 42 of 42" via pyttsx3
7. User hears the notification while making coffee

### Scenario 2 - User responds by voice

1. User presses `Ctrl+Alt+Space` (configurable hotkey) or says a wake word
2. Desktop client starts recording via sounddevice
3. User speaks: "retry the slow test with more workers"
4. Client detects silence (VAD) or user releases hotkey, stops recording
5. faster-whisper (local, CPU or GPU) transcribes the audio
6. Transcript sent to hub via `POST /api/messages` as a `user_input` type message addressed to the active Claude session
7. Hub routes to the bridge, bridge injects it as user input to Claude Code stdin
8. Claude Code processes, responds
9. Response flows back via the same loop, TTS reads it out

### Scenario 3 - Two Claudes coordinate via messages layer

1. Claude A on Machine A calls `bridge.send_message(to="vasya", type="question", subject="How to mock datetime", body="...")`
2. Bridge POSTs to `/api/messages`
3. Hub fans out via WebSocket to every connected client
4. Desktop client on Machine B delivers the message to Claude B's bridge
5. Claude B answers via `bridge.send_message(to="ani", type="answer", reply_to=<original-id>, ...)`
6. Hub broadcasts the answer
7. Desktop client on Machine A notifies: "vasya answered your question"
8. Claude A can continue with the new info

### Scenario 4 - Hub is offline

1. Claude A calls `bridge.send_message(to="vasya", ...)`
2. Bridge HTTP call fails (hub unreachable)
3. Bridge falls back to file mode: writes the message to `.claude/messages/inbox/` using the same format
4. The file is committed to git (project convention)
5. When Machine B later pulls, Claude B's bridge scans the file and processes the message
6. Full fidelity - nothing is lost, just slower

This is the key design property: **hub is an accelerator, not a requirement.**

## File format compatibility

Hub and mclaude file layer share the exact same message schema:

    ---
    from: ani
    to: vasya
    type: question
    subject: ...
    thread: ...
    reply_to: ...
    created: 2026-04-09T14:32:17
    status: unread
    urgent: false
    ---

    # Subject

    Body

Hub stores events as rows in SQLite but the canonical form is always the markdown file. Any event in the hub can be dumped to a file and re-read locally. Any local file can be pushed to the hub. This means:

1. Power users can grep through their message history with `rg` on the filesystem
2. Offline work does not lose information
3. Migration between hub and no-hub is trivial
4. Backup is a simple `tar -czf messages.tar.gz .claude/messages/`

## Authentication model

**Shared bearer tokens per project.** Not full OAuth. The hub is a small service meant to be run by one team on their own infrastructure.

- Hub has a `hub.toml` config with a list of valid bearer tokens
- Each client (bridge or desktop) sends `Authorization: Bearer <token>` on every request
- Each token is associated with a project ID and an identity name
- Tokens can be revoked by removing them from the config
- No token rotation built in for v0.1 - add in v0.2 if the user base grows

For stronger security, users put the hub behind an HTTPS reverse proxy (Caddy, nginx, Cloudflare Tunnel) and add their existing auth on top. Hub does not try to implement TLS itself.

## Audio pipeline

### Voice in (STT)

**Primary backend:** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) - a CTranslate2 reimplementation of OpenAI Whisper, CPU-friendly, fast enough for realtime transcription on modern laptops.

**Flow:**

1. User presses hotkey or speaks wake word (Porcupine or similar)
2. Desktop client starts sounddevice recording at 16 kHz mono
3. Silero VAD detects speech vs silence
4. On silence for 500ms or hotkey release, recording stops
5. Audio buffer passed to faster-whisper
6. Transcript returned to client
7. Client sends transcript to hub as a `user_input` message

**Fallback backends (pluggable):**

- Azure Speech SDK (paid, cloud, lowest latency)
- Google Cloud Speech (paid, cloud)
- OpenAI Whisper API (paid, cloud, same quality as local Whisper)
- Vosk (local, lightweight, lower quality than Whisper)

### Voice out (TTS)

**Primary backend:** [pyttsx3](https://github.com/nateshmbhat/pyttsx3) - cross-platform wrapper around native TTS engines (SAPI on Windows, NSSpeechSynthesizer on macOS, espeak on Linux). Zero dependencies beyond the system, works offline, voices are OK (not great).

**Fallback backends (pluggable):**

- [Coqui TTS](https://github.com/coqui-ai/TTS) - higher quality, larger models, Python, fully offline
- Azure Speech SDK - cloud, very high quality, fast
- ElevenLabs - cloud, most natural voices, paid
- [Piper](https://github.com/rhasspy/piper) - local, fast, neural voices, no Python binding but we can exec the binary

The audio module defines abstract `SttBackend` and `TtsBackend` interfaces so swapping is a config change, not a code change.

## Desktop client architecture

Built with PyQt6 because:

1. We already know it works (notes-widget proved it)
2. Native look on all three OSes
3. `QSystemTrayIcon` is the standard way to live in the tray
4. Rich text rendering for notification panels
5. Threads and signals/slots are clean for audio callbacks
6. Rock-solid event loop

**Threading model:**

- **Main thread** - Qt event loop, UI, tray icon
- **Network thread** - `QThread` running the WebSocket client
- **Audio recording thread** - sounddevice callback (native)
- **STT worker thread** - faster-whisper transcription (CPU-bound)
- **TTS worker thread** - pyttsx3 playback

All cross-thread communication via Qt signals/slots. No shared mutable state.

**Notification backends (cross-platform):**

- Windows 10/11: `winotify` or `win10toast` (native toast)
- macOS: `osascript` shell out (reliable, boring)
- Linux: `notify-send` or `dbus-python` (libnotify)
- Fallback: Qt's own `QSystemTrayIcon.showMessage()` (works everywhere but ugly)

`plyer` wraps all three with one API - we use plyer as primary and fall back to Qt if plyer fails.

## Hub server architecture

**Stack:**

- [FastAPI](https://fastapi.tiangolo.com/) - REST API + WebSocket support in one framework
- [uvicorn](https://www.uvicorn.org/) - ASGI server
- [SQLite](https://www.sqlite.org/) via `sqlite3` stdlib - state storage
- [Pydantic](https://docs.pydantic.dev/) - request/response schemas

**Why SQLite not Postgres:** simplicity. One file to back up. Zero configuration. Handles thousands of messages per minute easily. If a team outgrows SQLite, switching to Postgres is a schema migration, not a rewrite.

**Schema (simplified):**

```sql
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE identities (
    project_id TEXT REFERENCES projects(id),
    name TEXT NOT NULL,
    owner TEXT,
    machine TEXT,
    last_seen TEXT,
    notify_json TEXT,
    PRIMARY KEY (project_id, name)
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    identity TEXT NOT NULL,
    started_at TEXT NOT NULL,
    last_heartbeat TEXT NOT NULL,
    status TEXT CHECK(status IN ('active', 'idle', 'ended'))
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT REFERENCES projects(id),
    session_id TEXT,
    type TEXT NOT NULL,
    from_identity TEXT,
    to_identity TEXT,
    subject TEXT,
    body TEXT,
    urgent INTEGER DEFAULT 0,
    thread TEXT,
    reply_to TEXT,
    created_at TEXT NOT NULL,
    delivered INTEGER DEFAULT 0
);

CREATE TABLE locks (
    project_id TEXT REFERENCES projects(id),
    slug TEXT NOT NULL,
    session_id TEXT NOT NULL,
    identity TEXT NOT NULL,
    description TEXT,
    files_json TEXT,
    claimed_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    released_at TEXT,
    PRIMARY KEY (project_id, slug)
);

CREATE INDEX idx_events_project_created ON events(project_id, created_at DESC);
CREATE INDEX idx_events_undelivered ON events(delivered, project_id) WHERE delivered = 0;
```

**Endpoints:**

```
POST   /api/projects                    create a project
GET    /api/projects/:id                get project info

POST   /api/identities                  register an identity
GET    /api/identities                  list identities in project
DELETE /api/identities/:name            remove identity

POST   /api/sessions                    start a session (returns session_id)
POST   /api/sessions/:id/heartbeat      refresh heartbeat
POST   /api/sessions/:id/end            end a session

POST   /api/locks/claim                 atomic lock claim
POST   /api/locks/:slug/release         release a lock
POST   /api/locks/:slug/heartbeat       refresh lock heartbeat
GET    /api/locks                       list active locks in project

POST   /api/messages                    send a message
GET    /api/messages/inbox              get unread messages for an identity
POST   /api/messages/:id/ack            mark as read

POST   /api/events                      post an event (notification, status update, etc.)
GET    /api/events                      poll events for a project
WS     /ws                              WebSocket subscription for realtime events
```

Every endpoint requires `Authorization: Bearer <token>`. The token identifies the project and the calling identity.

## Testing strategy

**Unit tests** - pure functions, schema validation, data classes. Run on every commit, milliseconds each.

**Integration tests with in-memory hub** - start FastAPI with `TestClient`, stub SQLite with `:memory:`, test full request/response flows. Includes WebSocket connect/publish/receive.

**Audio pipeline tests with fixture audio files** - pre-recorded WAV files for STT, synthesize known text via TTS and verify audio length. Does NOT touch the actual microphone or speaker during CI.

**GUI tests with Qt's QTest framework** - headless mode (`QT_QPA_PLATFORM=offscreen`), verify tray menu items, notification dispatch, hotkey handling. Runs on Linux CI without a real display.

**Cross-platform CI matrix** - GitHub Actions runs on Ubuntu, Windows, macOS. Every test suite on every OS. Python 3.9, 3.10, 3.11, 3.12.

**E2E smoke test** - spawn hub + bridge + client as subprocesses, send a message through the full pipeline, assert the desktop client receives a notification. Runs once per release, not per commit.

## What is NOT in v0.1

Keeping the first release small and provable:

- No wake word detection (user must press hotkey to record)
- No speaker diarization
- No mobile client (separate project for later)
- No end-to-end encryption beyond TLS (hub sees message content)
- No clustering/HA for hub (single instance)
- No real-time collaborative editing
- No voice cloning for TTS
- No custom wake phrases

All of these are reasonable v0.2+ features once the core proves itself in real use.

## Security threat model

**Assume:** the hub is a single-user or small-team service. Not a public API.

**Threats considered:**

| Threat | Mitigation |
|---|---|
| Unauthorized access to hub | Bearer tokens, one per identity |
| MitM on HTTPS | TLS via reverse proxy (not hub itself) |
| Token leak | Revocation via config edit + restart |
| Malicious bridge injecting bad events | Token scoped to identity, audit log |
| DoS via flood of messages | Rate limit per token (SlowAPI) |
| Audio eavesdropping | STT is local by default; cloud STT is opt-in per config |
| Desktop hotkey hijack | Standard OS permissions |
| SQLite corruption | Journal mode WAL, nightly backup script |

**Explicitly not in scope:**

- Protection against a malicious hub operator (you trust whoever runs your hub)
- Protection against compromised client machines (the client is fully trusted)
- Long-term data retention compliance (users handle their own GDPR/etc.)

## Roadmap

- **v0.1** (this sprint) - Hub server, desktop client skeleton, Claude bridge, audio stubs with faster-whisper + pyttsx3 as first backends, tests, cross-platform CI
- **v0.2** - Audio pipeline wired end to end, hotkey recording, TTS notifications, docs for deployment on VPS
- **v0.3** - Wake word detection, mobile companion (Flutter), E2E encryption option
- **v0.4** - Team features: dashboards, activity feed, on-call rotations
