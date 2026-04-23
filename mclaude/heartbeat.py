"""
Heartbeat - lightweight liveness signal for running sessions.

Locks say "who is working on X." But an old lock could mean:
  a) session is still actively working (fine)
  b) session crashed or was closed without release (stale)

Without heartbeats, other sessions cannot tell the difference without asking
the human. Multica solves this with a WebSocket-streamed daemon status. We
solve it the same way locks do: a file with a mtime.

Each running session writes a small JSON file on a regular cadence. Other
sessions read the mtime and decide: fresh (<N minutes) = session is alive,
stale (>N minutes) = session probably dead, lock can be reclaimed by the
human after a manual check.

Storage:

    .claude/heartbeats/
      a1b2c3d4.json              # one file per session_id
      e5f6g7h8.json

Each file content:

    {
      "identity": "ani",
      "session_id": "a1b2c3d4",
      "started_at": "2026-04-23T09:30:00+00:00",
      "last_beat": "2026-04-23T10:47:12+00:00",
      "runtime": "claude-code",
      "task_id": "e5f6g7h8",
      "current_activity": "running tests",
      "lock_slugs": ["fix-auth-race"]
    }

The agent calls `heartbeat.beat()` every few minutes (or on every tool call
if you like). When the session ends, it calls `heartbeat.stop()` to remove
the file. If the session crashes, the file stays - but its mtime shows it
is stale, and `list_live()` filters it out by the `stale_after` threshold.

This is the same pattern as Kubernetes liveness probes - cheap, periodic,
file-based instead of network-based.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union


PathLike = Union[str, Path]


@dataclass
class Beat:
    identity: str
    session_id: str
    started_at: str
    last_beat: str
    runtime: str = "claude-code"
    task_id: Optional[str] = None
    current_activity: str = ""
    lock_slugs: list[str] = field(default_factory=list)


def _heartbeats_dir(project_root: PathLike) -> Path:
    p = Path(project_root) / ".claude" / "heartbeats"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _beat_path(project_root: PathLike, session_id: str) -> Path:
    return _heartbeats_dir(project_root) / f"{session_id}.json"


def beat(
    project_root: PathLike,
    identity: str,
    session_id: str,
    *,
    runtime: str = "claude-code",
    task_id: Optional[str] = None,
    activity: str = "",
    lock_slugs: Optional[list[str]] = None,
    started_at: Optional[str] = None,
) -> Beat:
    """Write/update this session's heartbeat. Call every few minutes."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fp = _beat_path(project_root, session_id)

    # Preserve started_at across beats
    prev_started = started_at
    if prev_started is None and fp.exists():
        try:
            prev = json.loads(fp.read_text(encoding="utf-8"))
            prev_started = prev.get("started_at")
        except (json.JSONDecodeError, OSError):
            pass
    if prev_started is None:
        prev_started = now

    b = Beat(
        identity=identity,
        session_id=session_id,
        started_at=prev_started,
        last_beat=now,
        runtime=runtime,
        task_id=task_id,
        current_activity=activity,
        lock_slugs=lock_slugs or [],
    )
    # Atomic write: unique tmp name prevents collision if the same session
    # beats from two threads at once (matches handoffs._atomic_write pattern).
    tmp = fp.with_suffix(f".json.tmp.{uuid.uuid4().hex[:8]}")
    tmp.write_text(json.dumps(asdict(b), indent=2, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(str(tmp), str(fp))
    return b


def stop(project_root: PathLike, session_id: str) -> bool:
    """Remove this session's heartbeat file (clean session end)."""
    fp = _beat_path(project_root, session_id)
    try:
        fp.unlink()
        return True
    except FileNotFoundError:
        return False


def list_live(project_root: PathLike, *, stale_after: int = 600) -> list[Beat]:
    """Return beats that are fresh (last_beat within stale_after seconds).

    Default: 10 minutes. If a session has not beaten in 10 minutes, we consider
    it stale. Reports it as NOT live.
    """
    root = _heartbeats_dir(project_root)
    now = time.time()
    live = []
    for fp in root.glob("*.json"):
        try:
            age = now - fp.stat().st_mtime
        except OSError:
            continue
        if age > stale_after:
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        # Only pass known fields
        known = {f for f in Beat.__dataclass_fields__.keys()}
        live.append(Beat(**{k: v for k, v in data.items() if k in known}))
    return live


def list_stale(project_root: PathLike, *, stale_after: int = 600) -> list[tuple[Beat, int]]:
    """Return (beat, seconds_since_last_beat) pairs for stale sessions.

    Useful for garbage collection: stale sessions may be holding locks that
    were never released.
    """
    root = _heartbeats_dir(project_root)
    now = time.time()
    stale = []
    for fp in root.glob("*.json"):
        try:
            age = int(now - fp.stat().st_mtime)
        except OSError:
            continue
        if age <= stale_after:
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        known = {f for f in Beat.__dataclass_fields__.keys()}
        stale.append((Beat(**{k: v for k, v in data.items() if k in known}), age))
    return stale
