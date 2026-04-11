"""
Hub sync for mclaude messages - bidirectional sync between local files and hub.

When hub is available, messages flow both ways:
- Local → Hub: new local messages are pushed to the hub
- Hub → Local: new hub messages are pulled to local files

When hub is offline, everything works locally. When it comes back,
auto_sync() catches up.

State tracked in .claude/messages/.sync_state.json:
- last_sync_time: epoch timestamp of last successful sync
- pushed_files: set of local filenames already pushed to hub
- pulled_ids: set of hub event IDs already pulled locally

Usage:

    from mclaude.mail_sync import MailSync

    sync = MailSync(
        hub_url="https://hub.example.com",
        token="bearer-token",
        project_id="my-project",
        identity="ani",
    )

    # Bidirectional sync
    result = sync.auto_sync()
    # {'pushed': 3, 'pulled': 2, 'errors': []}

    # Or one direction at a time
    sync.push_to_hub()
    sync.pull_from_hub()
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from .messages import Message, MessageStore

_SYNC_STATE_FILENAME = ".sync_state.json"
_TIMEOUT = 10  # HTTP timeout seconds


class MailSync:
    """Bidirectional sync between local mclaude messages and a hub server."""

    def __init__(
        self,
        hub_url: str | None = None,
        token: str | None = None,
        project_id: str | None = None,
        identity: str | None = None,
        project_root: str | Path | None = None,
        mailbox: str = "inbox",
    ) -> None:
        self.hub_url = (hub_url or os.environ.get("MCLAUDE_HUB_URL", "")).rstrip("/")
        self.token = token or os.environ.get("MCLAUDE_HUB_TOKEN", "")
        self.project_id = project_id or os.environ.get("MCLAUDE_PROJECT_ID", "default")
        self.identity = identity or os.environ.get("MCLAUDE_IDENTITY", "")
        self.store = MessageStore(project_root)
        self.mailbox = mailbox
        self._state_path = self.store.root / _SYNC_STATE_FILENAME

    @property
    def configured(self) -> bool:
        """Whether hub sync is configured (url + token present)."""
        return bool(self.hub_url and self.token)

    # -- State management ---------------------------------------------------

    def _load_state(self) -> dict:
        if self._state_path.exists():
            try:
                return json.loads(self._state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return {"last_sync_time": 0, "pushed_files": [], "pulled_ids": []}

    def _save_state(self, state: dict) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # -- HTTP helpers -------------------------------------------------------

    def _http_request(self, method: str, path: str, body: dict | None = None) -> dict | None:
        """Make an HTTP request to the hub. Returns parsed JSON or None on failure."""
        if not self.configured:
            return None

        url = f"{self.hub_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        data = json.dumps(body).encode("utf-8") if body else None
        req = Request(url, data=data, headers=headers, method=method)

        try:
            with urlopen(req, timeout=_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, OSError, json.JSONDecodeError, TimeoutError):
            return None

    # -- Push: local → hub --------------------------------------------------

    def push_to_hub(self) -> dict:
        """Push new local messages to hub. Returns {'pushed': N, 'errors': [...]}."""
        if not self.configured:
            return {"pushed": 0, "errors": ["hub not configured"]}

        state = self._load_state()
        pushed_set = set(state.get("pushed_files", []))
        errors: list[str] = []
        pushed_count = 0

        for path in self.store.list_mailbox(self.mailbox):
            if path.name in pushed_set:
                continue

            try:
                msg = Message.parse(path)
            except (OSError, ValueError) as e:
                errors.append(f"parse error {path.name}: {e}")
                continue

            # Push to hub as event
            event = {
                "project_id": self.project_id,
                "type": f"message_{msg.type}",
                "from_identity": msg.from_,
                "to_identity": msg.to,
                "subject": msg.subject,
                "body": msg.body,
                "urgent": msg.urgent,
                "thread": msg.thread,
                "reply_to": msg.reply_to,
            }

            result = self._http_request("POST", "/api/events", event)
            if result is not None:
                pushed_set.add(path.name)
                pushed_count += 1
            else:
                errors.append(f"push failed: {path.name}")

        state["pushed_files"] = list(pushed_set)
        state["last_sync_time"] = time.time()
        self._save_state(state)

        return {"pushed": pushed_count, "errors": errors}

    # -- Pull: hub → local --------------------------------------------------

    def pull_from_hub(self) -> dict:
        """Pull new messages from hub to local files. Returns {'pulled': N, 'errors': [...]}."""
        if not self.configured:
            return {"pulled": 0, "errors": ["hub not configured"]}

        state = self._load_state()
        pulled_ids = set(state.get("pulled_ids", []))
        errors: list[str] = []
        pulled_count = 0

        # Fetch events addressed to us
        params = f"?to_identity={self.identity}" if self.identity else ""
        result = self._http_request("GET", f"/api/events{params}")
        if result is None:
            return {"pulled": 0, "errors": ["hub unreachable"]}

        events = result.get("events", [])
        for event in events:
            event_id = str(event.get("id", ""))
            if event_id in pulled_ids:
                continue

            # Convert hub event to local message
            event_type = event.get("type", "message_update")
            msg_type = event_type.replace("message_", "") if event_type.startswith("message_") else "update"
            if msg_type not in ("question", "answer", "request", "update", "error", "broadcast", "ack"):
                msg_type = "update"

            try:
                msg = Message(
                    from_=event.get("from_identity", "hub"),
                    to=event.get("to_identity", self.identity or "*"),
                    type=msg_type,
                    subject=event.get("subject", ""),
                    body=event.get("body", ""),
                    thread=event.get("thread"),
                    reply_to=event.get("reply_to"),
                    urgent=bool(event.get("urgent", False)),
                    mailbox=self.mailbox,
                )
                self.store.send(msg)
                pulled_ids.add(event_id)
                pulled_count += 1
            except (ValueError, OSError) as e:
                errors.append(f"pull error event {event_id}: {e}")

        state["pulled_ids"] = list(pulled_ids)
        state["last_sync_time"] = time.time()
        self._save_state(state)

        return {"pulled": pulled_count, "errors": errors}

    # -- Auto sync ----------------------------------------------------------

    def auto_sync(self) -> dict:
        """Bidirectional sync: push local → hub, then pull hub → local."""
        if not self.configured:
            return {"pushed": 0, "pulled": 0, "errors": ["hub not configured"]}

        push_result = self.push_to_hub()
        pull_result = self.pull_from_hub()

        return {
            "pushed": push_result["pushed"],
            "pulled": pull_result["pulled"],
            "errors": push_result["errors"] + pull_result["errors"],
        }

    def reset_state(self) -> None:
        """Clear sync state (re-sync everything)."""
        if self._state_path.exists():
            self._state_path.unlink()
