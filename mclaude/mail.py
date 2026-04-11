"""
High-level mail API for inter-session communication.

Thin wrapper over mclaude.messages that adds:
- State tracking (which messages have been shown to the agent)
- Convenience methods (reply with auto-threading, ask with thread tracking)
- Polling wait for reply
- Digest (summary of unread by sender/type)

Usage:

    from mclaude.mail import Mail

    mail = Mail(identity="ani")

    # Check for new messages (only unseen ones)
    new = mail.check()
    for msg in new:
        print(f"[{msg.type}] from {msg.from_}: {msg.subject}")

    # Ask another Claude a question
    thread_id = mail.ask("vasya", "What's the API for auth?")

    # Reply to a message
    mail.reply(msg, "Use JWT with 15-min expiry")

    # Wait for a reply (blocking, with timeout)
    answer = mail.wait_for_reply(thread_id, timeout=60)

    # Get summary
    digest = mail.digest()
    # {'total': 5, 'by_sender': {'vasya': 3, 'system': 2}, 'by_type': {'question': 2, ...}}
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .messages import Message, MessageStore

# State file for tracking which messages have been shown
_STATE_FILENAME = ".watcher_state.json"


class Mail:
    """High-level mail interface for a single identity."""

    def __init__(
        self,
        identity: str | None = None,
        project_root: str | Path | None = None,
        mailbox: str = "inbox",
    ) -> None:
        self.identity = identity or os.environ.get("MCLAUDE_IDENTITY", "")
        self.store = MessageStore(project_root)
        self.mailbox = mailbox
        self._state_path = self.store.root / _STATE_FILENAME

    # -- State management ---------------------------------------------------

    def _load_state(self) -> dict:
        if self._state_path.exists():
            try:
                return json.loads(self._state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return {"seen_files": [], "last_check": 0}

    def _save_state(self, state: dict) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # -- Core API -----------------------------------------------------------

    def check(self, mark_seen: bool = True) -> list[Message]:
        """Return new (unseen) messages for this identity.

        Messages that were already returned by a previous check() call
        are skipped (tracked via .watcher_state.json). Pass mark_seen=False
        to peek without advancing the state.
        """
        if not self.identity:
            return []

        state = self._load_state()
        seen = set(state.get("seen_files", []))

        new_msgs: list[Message] = []
        new_seen: list[str] = []

        for path in self.store.list_mailbox(self.mailbox):
            if path.name in seen:
                continue
            try:
                parsed = Message.parse(path)
            except (OSError, ValueError):
                continue
            if parsed.to != self.identity and parsed.to != "*":
                continue
            if parsed.status != "unread":
                continue
            new_msgs.append(parsed)
            new_seen.append(path.name)

        if mark_seen and new_seen:
            state["seen_files"] = list(seen | set(new_seen))
            state["last_check"] = time.time()
            self._save_state(state)

        return new_msgs

    def check_all(self) -> list[Message]:
        """Return ALL unread messages (ignoring seen state)."""
        if not self.identity:
            return []
        return self.store.inbox(
            recipient=self.identity,
            mailbox=self.mailbox,
            include_read=False,
        )

    def reply(self, original: Message, body: str, subject: str | None = None) -> Path:
        """Reply to a message with auto-threading."""
        reply_subject = subject or f"Re: {original.subject}"
        # Determine thread: use original's thread or its filename as thread start
        thread = original.thread or original.filename()

        msg = Message(
            from_=self.identity,
            to=original.from_,
            type="answer",
            subject=reply_subject,
            body=body,
            reply_to=original.filename(),
            thread=thread,
            mailbox=self.mailbox,
        )
        return self.store.send(msg)

    def ask(self, to: str, question: str, body: str = "", urgent: bool = False) -> str:
        """Send a question and return the thread_id for tracking.

        The thread_id can be passed to wait_for_reply() to poll for an answer.
        """
        msg = Message(
            from_=self.identity,
            to=to,
            type="question",
            subject=question,
            body=body or question,
            urgent=urgent,
            mailbox=self.mailbox,
        )
        path = self.store.send(msg)
        # Thread ID = the filename stem of the original question
        return path.stem

    def send(
        self,
        to: str,
        body: str,
        subject: str = "",
        type: str = "update",
        urgent: bool = False,
        thread: str | None = None,
    ) -> Path:
        """Send a generic message."""
        msg = Message(
            from_=self.identity,
            to=to,
            type=type,
            subject=subject,
            body=body,
            urgent=urgent,
            thread=thread,
            mailbox=self.mailbox,
        )
        return self.store.send(msg)

    def wait_for_reply(
        self,
        thread_id: str,
        timeout: float = 120,
        poll_interval: float = 2.0,
    ) -> Message | None:
        """Poll for a reply in a thread. Returns the first reply or None on timeout."""
        deadline = time.time() + timeout
        seen_replies: set[str] = set()

        while time.time() < deadline:
            msgs = self.store.thread(thread_id, mailbox=self.mailbox)
            for msg in msgs:
                fname = msg.filename()
                if fname in seen_replies:
                    continue
                # Skip the original question (from us)
                if msg.from_ == self.identity and msg.type == "question":
                    seen_replies.add(fname)
                    continue
                # Found a reply
                if msg.type in ("answer", "update", "error"):
                    return msg
            time.sleep(poll_interval)

        return None

    def digest(self) -> dict:
        """Summary of unread messages: count by sender and type."""
        msgs = self.check_all()
        by_sender: dict[str, int] = {}
        by_type: dict[str, int] = {}
        urgent_count = 0

        for msg in msgs:
            by_sender[msg.from_] = by_sender.get(msg.from_, 0) + 1
            by_type[msg.type] = by_type.get(msg.type, 0) + 1
            if msg.urgent:
                urgent_count += 1

        return {
            "total": len(msgs),
            "urgent": urgent_count,
            "by_sender": by_sender,
            "by_type": by_type,
        }

    def reset_state(self) -> None:
        """Clear the seen-messages state (re-show everything)."""
        if self._state_path.exists():
            self._state_path.unlink()
