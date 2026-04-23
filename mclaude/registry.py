"""
Identity Registry - give Claude instances human-readable names.

Without identity, every Claude session is a bare UUID. You cannot say
"Claude Ani is fixing auth, Claude Vasya is on the frontend" - you have
no way to connect a session to a person or purpose.

This module lets a user register an identity for themselves and the Claude
instances they run. Each identity has:

- **name** - human-readable (e.g. "ani", "vasya", "design-team")
- **id** - stable UUID, used internally for locks and handoffs
- **owner** - the human who operates this Claude instance
- **notify** - optional contact info for notifications (email, telegram, webhook)
- **machine** - hostname/fingerprint, helps detect cross-machine collaboration
- **roles** - optional list (e.g. ["backend", "auth"]) for routing work

The registry is stored as a single JSON file per project:

    .claude/registry.json

    {
      "schema": 1,
      "identities": [
        {
          "name": "ani",
          "id": "c0d3-ani-2026-04-09",
          "owner": "Anastasia",
          "notify": {"telegram_chat_id": "..."},
          "machine": "ani-laptop",
          "roles": ["infra", "ml", "product"],
          "registered_at": "2026-04-09T14:00:00",
          "last_seen": "2026-04-09T15:30:00"
        },
        {
          "name": "vasya",
          "id": "c0d3-vasya-2026-04-09",
          "owner": "Vasily",
          "notify": {"email": "..."},
          "machine": "vasya-workstation",
          "roles": ["frontend", "design"],
          "registered_at": "2026-04-08T11:00:00",
          "last_seen": "2026-04-09T12:15:00"
        }
      ]
    }

The registry is NOT for authentication. It is a naming directory that lets
humans and agents refer to each other by name instead of UUID. Trust between
instances comes from the transport layer (git, ssh, or whatever syncs the
project directory), not from this file.

## Notifications (future layer)

Once identities are registered, the notify field can be used by a separate
notification system to fan out events like:

- "Claude ani claimed work on auth-middleware-rewrite"
- "Claude vasya finished the dashboard redesign, PR opened"
- "Claude ani handoff: blocked on review of Vasya's style guide"

The notification layer is NOT in this module - we keep registry simple and
let notification backends plug in on top. See examples/notifications/ for
reference implementations (telegram bot, desktop toast, webhook fanout).

## Usage

From Python:

    from mclaude.registry import Registry, Identity

    reg = Registry(project_root="/path/to/project")

    # Register an identity
    reg.register(Identity(
        name="ani",
        owner="Anastasia",
        notify={"telegram_chat_id": "123456"},
        roles=["infra", "ml"],
    ))

    # Look up by name
    ani = reg.get("ani")
    print(ani.id)

    # List all
    for identity in reg.list_all():
        print(identity.name, identity.owner, identity.last_seen)

    # Update last_seen (called automatically by other mclaude components)
    reg.touch("ani")

CLI (see mclaude.cli):

    mclaude identity register ani --owner "Anastasia" --notify telegram:123
    mclaude identity list
    mclaude identity whoami
    mclaude identity remove vasya
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Valid identity names: lowercase, letters/digits/hyphens, 2-32 chars
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")

SCHEMA_VERSION = 1


@dataclass
class Identity:
    """A named Claude instance owned by a human."""

    name: str
    owner: str = ""
    id: str = ""  # auto-generated if empty
    notify: dict = field(default_factory=dict)  # {"telegram_chat_id": ..., "email": ..., "webhook": ...}
    machine: str = ""
    roles: list[str] = field(default_factory=list)
    registered_at: str = ""
    last_seen: str = ""
    # Runtime the identity is driving. Optional but useful in heterogeneous
    # teams where one human runs both Claude Code and Codex sessions, and
    # tasks may be routed by runtime (see mclaude.tasks.runtime_hint).
    # Common values: "claude-code", "codex", "cursor", "opencode", "hermes".
    runtime: str = ""

    def __post_init__(self) -> None:
        if not NAME_PATTERN.match(self.name):
            raise ValueError(
                f"Invalid identity name {self.name!r}: must be lowercase, "
                f"letters/digits/hyphens, 2-32 chars"
            )
        if not self.id:
            self.id = f"c0d3-{self.name}-{uuid.uuid4().hex[:8]}"
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        if not self.registered_at:
            self.registered_at = now
        if not self.last_seen:
            self.last_seen = now


def _known_fields(entry: dict) -> dict:
    """Keep only keys that Identity accepts.

    Protects against forward-compat drift: an older mclaude reading a
    registry.json written by a newer version should ignore unknown fields
    rather than crash with `TypeError: unexpected keyword argument`.
    """
    allowed = set(Identity.__dataclass_fields__.keys())
    return {k: v for k, v in entry.items() if k in allowed}


class Registry:
    """Project-local identity registry.

    Stored as a single JSON file. Loads lazily, writes atomically.
    Concurrent writes are NOT fully safe - if two sessions register at
    exactly the same moment, one write may be lost. In practice, registration
    is rare enough that this is acceptable. If you need stronger guarantees,
    use the locks layer to serialize access:

        lock("registry-write") -> reg.register(...) -> unlock
    """

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.path = self.project_root / ".claude" / "registry.json"

    def _load(self) -> dict:
        if not self.path.exists():
            return {"schema": SCHEMA_VERSION, "identities": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Corrupt registry at {self.path}: {e}")
        if data.get("schema") != SCHEMA_VERSION:
            # In the future, migrate here
            raise RuntimeError(
                f"Registry schema {data.get('schema')} does not match "
                f"expected {SCHEMA_VERSION}"
            )
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".json.tmp.{uuid.uuid4().hex[:8]}")
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def register(self, identity: Identity) -> Identity:
        """Add or update an identity. Returns the stored identity."""
        data = self._load()
        existing = None
        for i, entry in enumerate(data["identities"]):
            if entry["name"] == identity.name:
                existing = i
                break
        if existing is not None:
            # Update existing entry but preserve immutable fields
            old = data["identities"][existing]
            identity.id = old["id"]
            identity.registered_at = old.get("registered_at", identity.registered_at)
            data["identities"][existing] = asdict(identity)
        else:
            data["identities"].append(asdict(identity))
        self._save(data)
        return identity

    def get(self, name: str) -> Identity | None:
        """Look up an identity by name. Returns None if not found."""
        data = self._load()
        for entry in data["identities"]:
            if entry["name"] == name:
                return Identity(**_known_fields(entry))
        return None

    def list_all(self) -> list[Identity]:
        data = self._load()
        return [Identity(**_known_fields(entry)) for entry in data["identities"]]

    def remove(self, name: str) -> bool:
        """Remove an identity by name. Returns True if it was present."""
        data = self._load()
        before = len(data["identities"])
        data["identities"] = [i for i in data["identities"] if i["name"] != name]
        if len(data["identities"]) == before:
            return False
        self._save(data)
        return True

    def touch(self, name: str) -> bool:
        """Update last_seen to now. Returns False if identity does not exist."""
        data = self._load()
        for entry in data["identities"]:
            if entry["name"] == name:
                entry["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                self._save(data)
                return True
        return False

    def whoami(self) -> Identity | None:
        """Detect current identity from the MCLAUDE_IDENTITY env var.

        This is the recommended way for a Claude session to know its own
        name: set MCLAUDE_IDENTITY=ani in the environment when you start
        the session, and every mclaude component picks it up.
        """
        name = os.environ.get("MCLAUDE_IDENTITY")
        if not name:
            return None
        return self.get(name)
