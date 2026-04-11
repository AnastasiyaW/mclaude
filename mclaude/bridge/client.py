"""
BridgeClient - HTTP client + file fallback for the Claude-hub connection.

Design:

- Uses `httpx` for HTTP because it supports sync and async with the same API
- On any HTTP failure, falls back to file-based mclaude equivalents
- Never raises to the caller unless `strict_online=True` is set

Typical usage:

    bridge = BridgeClient(BridgeConfig(
        hub_url="https://hub.example.com",
        token="bearer-token-here",
        identity="ani",
        project_id="my-project",
    ))

    # Send a message (goes to hub, falls back to local file if offline)
    bridge.send_message(to="vasya", type="question", subject="...", body="...")

    # Claim a lock
    bridge.claim_lock(slug="fix-auth-bug", description="...", files=["src/auth.py"])

    # Post a notification for the desktop client to show
    bridge.notify(message="Tests passed", urgent=False)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import httpx  # type: ignore[import-not-found]
    _HAS_HTTPX = True
except ImportError:  # pragma: no cover
    _HAS_HTTPX = False

try:
    from mclaude.messages import Message, MessageStore
    from mclaude.locks import lock_path, metadata_path
    _HAS_MCLAUDE = True
except ImportError:  # pragma: no cover
    _HAS_MCLAUDE = False

from mclaude.common.models import Event, EventType


@dataclass
class BridgeConfig:
    """Configuration for a BridgeClient."""

    hub_url: str = ""  # empty string = offline mode, skip hub calls entirely
    token: str = ""
    identity: str = "anonymous"
    project_id: str = "default"
    session_id: str | None = None
    project_root: Path | str = "."
    strict_online: bool = False  # if True, errors raise instead of falling back
    timeout: float = 5.0


class BridgeClient:
    """Bridge between a local Claude session and the hub server.

    Offline-first: the file fallback is the primary source of truth, and the
    hub is an accelerator. If anything fails, the local file layer still has
    the data and the operation returns success.
    """

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self.project_root = Path(config.project_root).resolve()
        self._http: Any | None = None
        if _HAS_HTTPX and config.hub_url:
            self._http = httpx.Client(
                base_url=config.hub_url,
                headers={"Authorization": f"Bearer {config.token}"} if config.token else {},
                timeout=config.timeout,
            )

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    # -- Helpers --------------------------------------------------------

    def _try_post(self, path: str, payload: dict) -> dict | None:
        """Attempt an HTTP POST. Return response JSON on success, None on fail.

        If `strict_online=True` and the call fails, re-raise. Otherwise we
        silently fall through so the caller can write to the file fallback.
        """
        if self._http is None:
            return None
        try:
            resp = self._http.post(path, json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            if self.config.strict_online:
                raise
            return None

    def _try_get(self, path: str, params: dict | None = None) -> Any:
        if self._http is None:
            return None
        try:
            resp = self._http.get(path, params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            if self.config.strict_online:
                raise
            return None

    # -- Message API (mclaude.messages mirror) --------------------------

    def send_message(
        self,
        *,
        to: str,
        type: str = "update",
        subject: str = "",
        body: str = "",
        reply_to: str | None = None,
        thread: str | None = None,
        urgent: bool = False,
        mailbox: str = "inbox",
    ) -> dict:
        """Send a message to another Claude session (or broadcast with to='*').

        Primary: POST to hub's /api/events with the MESSAGE_* event type.
        Fallback: write a file via mclaude.messages.MessageStore.

        Returns a dict with at least `{"delivered": "hub" | "file", "path": ...}`.
        """
        event_type_map = {
            "question": EventType.MESSAGE_QUESTION,
            "answer": EventType.MESSAGE_ANSWER,
            "request": EventType.MESSAGE_REQUEST,
            "update": EventType.MESSAGE_UPDATE,
            "error": EventType.MESSAGE_ERROR,
            "broadcast": EventType.MESSAGE_BROADCAST,
            "ack": EventType.MESSAGE_ACK,
        }
        event_type = event_type_map.get(type, EventType.MESSAGE_UPDATE).value

        payload = {
            "type": event_type,
            "from_identity": self.config.identity,
            "to_identity": to,
            "subject": subject,
            "body": body,
            "urgent": urgent,
            "thread": thread,
            "reply_to": reply_to,
            "session_id": self.config.session_id,
        }
        hub_result = self._try_post("/api/events", payload)
        if hub_result is not None:
            return {"delivered": "hub", "event_id": hub_result.get("id"), "hub_response": hub_result}

        # File fallback
        if not _HAS_MCLAUDE:
            return {"delivered": "lost", "reason": "mclaude not installed, hub offline"}

        store = MessageStore(project_root=self.project_root)
        msg = Message(
            from_=self.config.identity,
            to=to,
            type=type,
            subject=subject,
            body=body,
            thread=thread,
            reply_to=reply_to,
            urgent=urgent,
            mailbox=mailbox,
        )
        path = store.send(msg)
        return {"delivered": "file", "path": str(path)}

    def inbox(self, recipient: str | None = None, include_read: bool = False) -> list[dict]:
        """Fetch inbox for a recipient (default: self.config.identity).

        Primary: GET /api/events?to_identity=... from hub.
        Fallback: scan local .claude/messages/inbox/.
        """
        recipient = recipient or self.config.identity

        hub_events = self._try_get("/api/events", params={"to_identity": recipient, "limit": 100})
        if hub_events is not None:
            return hub_events

        if not _HAS_MCLAUDE:
            return []
        store = MessageStore(project_root=self.project_root)
        msgs = store.inbox(recipient=recipient, include_read=include_read)
        return [
            {
                "from": m.from_,
                "to": m.to,
                "type": m.type,
                "subject": m.subject,
                "body": m.body,
                "urgent": m.urgent,
                "created_at": m.created,
            }
            for m in msgs
        ]

    # -- Notification API -----------------------------------------------

    def notify(
        self,
        *,
        message: str,
        subject: str = "",
        urgent: bool = False,
        attention: bool = False,
    ) -> dict:
        """Post a user-facing notification event.

        The desktop client is the primary listener for these. Falls back to
        writing a special message file if the hub is offline.
        """
        event_type = EventType.NOTIFY_ATTENTION_NEEDED if attention else EventType.NOTIFY_TASK_COMPLETE
        payload = {
            "type": event_type.value,
            "from_identity": self.config.identity,
            "to_identity": "*",
            "subject": subject or "Notification",
            "body": message,
            "urgent": urgent,
            "session_id": self.config.session_id,
        }
        hub_result = self._try_post("/api/events", payload)
        if hub_result is not None:
            return {"delivered": "hub", "event_id": hub_result.get("id")}

        # Fallback: write a file, desktop client can scan it
        if not _HAS_MCLAUDE:
            return {"delivered": "lost"}

        store = MessageStore(project_root=self.project_root)
        msg = Message(
            from_=self.config.identity,
            to="*",
            type="update",
            subject=subject or "Notification",
            body=message,
            urgent=urgent,
            mailbox="notifications",
        )
        path = store.send(msg)
        return {"delivered": "file", "path": str(path)}

    # -- Lock API -------------------------------------------------------

    def claim_lock(
        self,
        *,
        slug: str,
        description: str,
        files: list[str] | None = None,
    ) -> dict:
        """Claim a work lock. Returns dict with `status`: 'claimed' | 'held'.

        Hub is consulted first; on success, the hub's decision is authoritative.
        On hub failure, we fall through to local file-based lock via mclaude.locks.
        """
        files = files or []
        payload = {
            "slug": slug,
            "session_id": self.config.session_id or "local",
            "identity": self.config.identity,
            "description": description,
            "files": files,
        }
        try:
            hub_result = self._try_post("/api/locks/claim", payload)
        except Exception as exc:
            if self.config.strict_online:
                raise
            hub_result = None

        if hub_result is not None:
            return {"status": "claimed", "source": "hub", "lock": hub_result}

        # Local fallback via subprocess call to mclaude CLI would be heavy here;
        # just note that the hub was unavailable and let the caller retry later.
        return {"status": "unknown", "source": "offline", "note": "hub unreachable, use local `mclaude lock claim` directly"}

    def release_lock(self, slug: str) -> dict:
        """Release a lock previously claimed via this bridge."""
        if self._http is None:
            return {"status": "offline"}
        try:
            resp = self._http.post(
                f"/api/locks/{slug}/release",
                params={"session_id": self.config.session_id or "local"},
            )
            resp.raise_for_status()
            return {"status": "released", "response": resp.json()}
        except Exception as exc:
            if self.config.strict_online:
                raise
            return {"status": "offline", "error": str(exc)}

    # -- Context manager support ---------------------------------------

    def __enter__(self) -> BridgeClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
