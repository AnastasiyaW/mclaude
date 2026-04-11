"""
SQLite-backed storage layer for the hub server.

Design notes:

- **Thread-safe writes via a single connection + `check_same_thread=False`.**
  FastAPI's async model means multiple requests may land on different threads;
  we serialize writes with a `threading.Lock` at the Store level. For reads we
  rely on SQLite's internal consistency.

- **WAL journal mode** - better concurrency for mixed read/write workloads,
  and the DB file survives crashes without corrupting.

- **`:memory:` mode for tests.** Pass `db_path=":memory:"` to get a transient
  in-memory store that never touches disk.

- **Idempotent schema creation.** Calling `Store.__init__` against an existing
  database does not drop or modify existing data - it just makes sure the
  tables exist.

- **Raw SQL, no ORM.** The schema is small and stable. An ORM would add more
  moving parts than it would remove.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from mclaude.common.models import Event, EventType, IdentityInfo, LockClaim, SessionInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS identities (
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    owner TEXT DEFAULT '',
    machine TEXT DEFAULT '',
    roles_json TEXT DEFAULT '[]',
    notify_json TEXT DEFAULT '{}',
    registered_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (project_id, name),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    identity TEXT NOT NULL,
    machine TEXT DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('active', 'idle', 'ended')),
    started_at TEXT NOT NULL,
    last_heartbeat TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT,
    type TEXT NOT NULL,
    from_identity TEXT NOT NULL,
    to_identity TEXT NOT NULL DEFAULT '*',
    subject TEXT DEFAULT '',
    body TEXT DEFAULT '',
    urgent INTEGER DEFAULT 0,
    thread TEXT,
    reply_to TEXT,
    created_at TEXT NOT NULL,
    delivered INTEGER DEFAULT 0,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS locks (
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    session_id TEXT NOT NULL,
    identity TEXT NOT NULL,
    description TEXT DEFAULT '',
    files_json TEXT DEFAULT '[]',
    claimed_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    released_at TEXT,
    PRIMARY KEY (project_id, slug),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE INDEX IF NOT EXISTS idx_events_project_created
    ON events(project_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_undelivered
    ON events(project_id, delivered);

CREATE INDEX IF NOT EXISTS idx_sessions_identity
    ON sessions(project_id, identity);
"""


class Store:
    """SQLite-backed persistence layer."""

    def __init__(self, db_path: str | Path = "mclaude_hub.db") -> None:
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            isolation_level=None,  # autocommit mode; we use BEGIN/COMMIT manually
        )
        self._conn.row_factory = sqlite3.Row
        if self.db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Exclusive transaction with the write lock held."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # -- Projects ---------------------------------------------------------

    def create_project(self, project_id: str, name: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO projects (id, name, created_at) VALUES (?, ?, ?)",
                (project_id, name, time.strftime("%Y-%m-%dT%H:%M:%S")),
            )

    def get_project(self, project_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None

    # -- Identities -------------------------------------------------------

    def register_identity(self, identity: IdentityInfo) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO identities (project_id, name, owner, machine, roles_json, notify_json, registered_at, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, name) DO UPDATE SET
                    owner = excluded.owner,
                    machine = excluded.machine,
                    roles_json = excluded.roles_json,
                    notify_json = excluded.notify_json,
                    last_seen = excluded.last_seen
                """,
                (
                    identity.project_id,
                    identity.name,
                    identity.owner,
                    identity.machine,
                    json.dumps(identity.roles),
                    json.dumps(identity.notify),
                    identity.registered_at or now,
                    now,
                ),
            )

    def list_identities(self, project_id: str) -> list[IdentityInfo]:
        rows = self._conn.execute(
            "SELECT * FROM identities WHERE project_id = ? ORDER BY name",
            (project_id,),
        ).fetchall()
        return [
            IdentityInfo(
                project_id=r["project_id"],
                name=r["name"],
                owner=r["owner"],
                machine=r["machine"],
                roles=json.loads(r["roles_json"] or "[]"),
                notify=json.loads(r["notify_json"] or "{}"),
                registered_at=r["registered_at"],
                last_seen=r["last_seen"],
            )
            for r in rows
        ]

    def touch_identity(self, project_id: str, name: str) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._tx() as conn:
            conn.execute(
                "UPDATE identities SET last_seen = ? WHERE project_id = ? AND name = ?",
                (now, project_id, name),
            )

    # -- Sessions ---------------------------------------------------------

    def start_session(self, session: SessionInfo) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, project_id, identity, machine, status, started_at, last_heartbeat)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.project_id,
                    session.identity,
                    session.machine,
                    session.status,
                    session.started_at,
                    session.last_heartbeat,
                ),
            )

    def heartbeat_session(self, session_id: str) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._tx() as conn:
            conn.execute(
                "UPDATE sessions SET last_heartbeat = ?, status = 'active' WHERE id = ?",
                (now, session_id),
            )

    def end_session(self, session_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE sessions SET status = 'ended' WHERE id = ?",
                (session_id,),
            )

    def list_active_sessions(self, project_id: str) -> list[SessionInfo]:
        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE project_id = ? AND status != 'ended' ORDER BY last_heartbeat DESC",
            (project_id,),
        ).fetchall()
        return [
            SessionInfo(
                id=r["id"],
                project_id=r["project_id"],
                identity=r["identity"],
                status=r["status"],
                started_at=r["started_at"],
                last_heartbeat=r["last_heartbeat"],
                machine=r["machine"],
            )
            for r in rows
        ]

    # -- Locks ------------------------------------------------------------

    def claim_lock(self, lock: LockClaim) -> bool:
        """Atomic claim. Returns True if we got it, False if already held."""
        with self._tx() as conn:
            # Check existing
            existing = conn.execute(
                "SELECT * FROM locks WHERE project_id = ? AND slug = ? AND released_at IS NULL",
                (lock.project_id, lock.slug),
            ).fetchone()
            if existing:
                return False
            conn.execute(
                """
                INSERT INTO locks (project_id, slug, session_id, identity, description, files_json, claimed_at, heartbeat_at, released_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(project_id, slug) DO UPDATE SET
                    session_id = excluded.session_id,
                    identity = excluded.identity,
                    description = excluded.description,
                    files_json = excluded.files_json,
                    claimed_at = excluded.claimed_at,
                    heartbeat_at = excluded.heartbeat_at,
                    released_at = NULL
                """,
                (
                    lock.project_id,
                    lock.slug,
                    lock.session_id,
                    lock.identity,
                    lock.description,
                    json.dumps(lock.files),
                    lock.claimed_at,
                    lock.heartbeat_at,
                ),
            )
            return True

    def release_lock(self, project_id: str, slug: str, session_id: str) -> bool:
        """Release a lock if it belongs to the given session. Returns True on success."""
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE locks SET released_at = ? WHERE project_id = ? AND slug = ? AND session_id = ? AND released_at IS NULL",
                (now, project_id, slug, session_id),
            )
            return cur.rowcount > 0

    def get_active_lock(self, project_id: str, slug: str) -> LockClaim | None:
        row = self._conn.execute(
            "SELECT * FROM locks WHERE project_id = ? AND slug = ? AND released_at IS NULL",
            (project_id, slug),
        ).fetchone()
        if not row:
            return None
        return LockClaim(
            project_id=row["project_id"],
            slug=row["slug"],
            session_id=row["session_id"],
            identity=row["identity"],
            description=row["description"] or "",
            files=json.loads(row["files_json"] or "[]"),
            claimed_at=row["claimed_at"],
            heartbeat_at=row["heartbeat_at"],
            released_at=row["released_at"],
        )

    def list_active_locks(self, project_id: str) -> list[LockClaim]:
        rows = self._conn.execute(
            "SELECT * FROM locks WHERE project_id = ? AND released_at IS NULL ORDER BY claimed_at",
            (project_id,),
        ).fetchall()
        return [
            LockClaim(
                project_id=r["project_id"],
                slug=r["slug"],
                session_id=r["session_id"],
                identity=r["identity"],
                description=r["description"] or "",
                files=json.loads(r["files_json"] or "[]"),
                claimed_at=r["claimed_at"],
                heartbeat_at=r["heartbeat_at"],
                released_at=r["released_at"],
            )
            for r in rows
        ]

    # -- Events -----------------------------------------------------------

    def insert_event(self, event: Event) -> Event:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO events (id, project_id, session_id, type, from_identity, to_identity,
                                   subject, body, urgent, thread, reply_to, created_at, delivered)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.project_id,
                    event.session_id,
                    event.type.value if isinstance(event.type, EventType) else event.type,
                    event.from_identity,
                    event.to_identity,
                    event.subject,
                    event.body,
                    1 if event.urgent else 0,
                    event.thread,
                    event.reply_to,
                    event.created_at,
                    0,
                ),
            )
        return event

    def list_events(
        self,
        project_id: str,
        to_identity: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        query = "SELECT * FROM events WHERE project_id = ?"
        params: list[Any] = [project_id]
        if to_identity:
            query += " AND (to_identity = ? OR to_identity = '*')"
            params.append(to_identity)
        if since:
            query += " AND created_at > ?"
            params.append(since)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_event(r) for r in rows]

    def mark_delivered(self, event_id: str) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE events SET delivered = 1 WHERE id = ?", (event_id,))


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        project_id=row["project_id"],
        type=EventType(row["type"]),
        from_identity=row["from_identity"],
        to_identity=row["to_identity"],
        subject=row["subject"] or "",
        body=row["body"] or "",
        urgent=bool(row["urgent"]),
        thread=row["thread"],
        reply_to=row["reply_to"],
        session_id=row["session_id"],
        id=row["id"],
        created_at=row["created_at"],
        delivered=bool(row["delivered"]),
    )
