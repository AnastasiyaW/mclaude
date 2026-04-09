"""
Live inter-session messaging - the "desktop dead drop" pattern.

Handoffs are for end-of-session. Messages are for "I need help from another
Claude *right now* while I'm working." The user has been doing this manually
for months - writing questions to a file, asking another Claude to read and
answer, getting an answer back in the same file. This module formalizes it.

## Model

Each message is a standalone markdown file in `.claude/messages/<mailbox>/`.
A mailbox is a destination - "inbox" is the common one, but you can route to
named mailboxes (`assistants`, `review`, `infra-team`) to keep different
kinds of traffic separate.

Filename format:

    YYYY-MM-DD_HH-MM-SS_<from-session8>_<to>_<msg-type>_<slug>.md

Examples:

    2026-04-09_14-32-17_373d1618_inbox_question_how-to-mock-datetime.md
    2026-04-09_14-33-02_ani_vasya_answer_re-how-to-mock-datetime.md
    2026-04-09_14-45-00_vasya_ani_request-review_auth-middleware-pr.md

The granularity is seconds (HH-MM-SS), not minutes, because multiple messages
can fly inside the same minute.

Message types:

    question      - asks something, expects answer
    answer        - replies to a question
    request       - asks recipient to do something (not just tell)
    update        - status update, no response expected
    error         - reports a problem
    broadcast     - goes to all mailboxes
    ack           - acknowledges receipt (auto or manual)

Headers as YAML frontmatter:

    ---
    from: ani                        # identity name or session short ID
    to: vasya                        # name, mailbox, or * for broadcast
    type: question
    subject: How to mock datetime in pytest
    thread: 2026-04-09_14-32-17_373d1618_inbox_question_how-to-mock-datetime
    reply_to: 2026-04-09_14-30-00_... # optional - filename of the message this replies to
    created: 2026-04-09T14:32:17
    status: unread                    # unread | read | answered | archived
    urgent: false
    ---

    # Subject here

    Message body in markdown.

## Append-only semantics

Messages are never edited in place. Status transitions are new files:

    2026-04-09_14-32-17_ani_vasya_question_datetime-mocking.md    <- original
    2026-04-09_14-33-05_vasya_ani_answer_re-datetime-mocking.md   <- answer
    2026-04-09_14-33-06_system_vasya_ack_re-datetime-mocking.md   <- optional ack

The `thread` field ties them together. Sort by timestamp to reconstruct a
conversation. If you need to mark a message as "handled", write an ack or
answer - do not mutate the original.

## Mailbox scanning

A session scans its own mailbox at start, and optionally periodically during
long work. The scan finds unread messages addressed to it by name OR session
ID, surfaces them, and the agent decides whether to respond now, queue for
later, or defer to the user.

## Compatibility with hub

The hub version of this module (in mclaude-hub) uses the same filename format
and frontmatter schema. A local file-based exchange and a network exchange
can interoperate: dump hub messages to local files and they work as local
messages, or scan local files and push them to hub.

## Usage

    from mclaude.messages import Message, MessageStore

    store = MessageStore()

    # Ask another Claude a question
    msg = Message(
        from_="ani",
        to="vasya",
        type="question",
        subject="How to mock datetime in pytest",
        body="I want to freeze time for a test. What's the cleanest way?",
    )
    path = store.send(msg)

    # Check for messages addressed to me
    unread = store.inbox(recipient="ani")
    for m in unread:
        print(m.subject, m.from_)
        if m.type == "question":
            # Answer it
            reply = Message(
                from_="ani",
                to=m.from_,
                type="answer",
                subject=f"Re: {m.subject}",
                reply_to=m.filename(),
                thread=m.thread or m.filename(),
                body="Use freezegun or pytest-freezer. See below...",
            )
            store.send(reply)
"""
from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# Same slug rules as other mclaude modules
_SLUG_STRIP = re.compile(r"[^\w\s-]+", re.UNICODE)
_SLUG_WHITESPACE = re.compile(r"[\s_]+")

VALID_TYPES = frozenset({"question", "answer", "request", "update", "error", "broadcast", "ack"})
VALID_STATUS = frozenset({"unread", "read", "answered", "archived"})

DEFAULT_MAILBOX = "inbox"

# Filename regex for parsing
FILENAME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})_([a-zA-Z0-9-]+)_([a-zA-Z0-9-]+)_([a-z-]+)_(.+)\.md$"
)


def slugify(text: str, max_words: int = 6) -> str:
    if not text:
        return "untitled"
    cleaned = _SLUG_STRIP.sub(" ", text.lower())
    cleaned = _SLUG_WHITESPACE.sub(" ", cleaned).strip()
    words = cleaned.split()[:max_words]
    return "-".join(words) or "untitled"


@dataclass
class Message:
    """A single inter-session message."""

    from_: str
    to: str
    type: str = "update"
    subject: str = ""
    body: str = ""
    thread: str | None = None  # original message filename that started the thread
    reply_to: str | None = None  # filename of the message this directly replies to
    urgent: bool = False
    status: str = "unread"
    created: str | None = None  # ISO timestamp, defaults to now
    mailbox: str = DEFAULT_MAILBOX
    timestamp: str | None = None  # YYYY-MM-DD_HH-MM-SS for filename
    slug_override: str | None = None

    def __post_init__(self) -> None:
        if self.type not in VALID_TYPES:
            raise ValueError(f"Invalid type {self.type!r}, must be one of {sorted(VALID_TYPES)}")
        if self.status not in VALID_STATUS:
            raise ValueError(f"Invalid status {self.status!r}, must be one of {sorted(VALID_STATUS)}")
        if not self.from_:
            raise ValueError("from_ is required")
        if not self.to:
            raise ValueError("to is required")

    def slug(self) -> str:
        if self.slug_override:
            return self.slug_override
        return slugify(self.subject or self.body[:60])

    def filename(self) -> str:
        ts = self.timestamp or time.strftime("%Y-%m-%d_%H-%M-%S")
        # Sanitize from_/to for filesystem: * is illegal on Windows, replace
        # with ALL (which is the broadcast convention in the filename layer).
        # The frontmatter still contains the real value.
        from_safe = self.from_ if self.from_ != "*" else "ALL"
        to_safe = self.to if self.to != "*" else "ALL"
        return f"{ts}_{from_safe}_{to_safe}_{self.type}_{self.slug()}.md"

    def render(self) -> str:
        created = self.created or time.strftime("%Y-%m-%dT%H:%M:%S")
        lines = ["---"]
        lines.append(f"from: {self.from_}")
        lines.append(f"to: {self.to}")
        lines.append(f"type: {self.type}")
        if self.subject:
            lines.append(f"subject: {self.subject}")
        if self.thread:
            lines.append(f"thread: {self.thread}")
        if self.reply_to:
            lines.append(f"reply_to: {self.reply_to}")
        lines.append(f"created: {created}")
        lines.append(f"status: {self.status}")
        lines.append(f"urgent: {'true' if self.urgent else 'false'}")
        lines.append("---")
        lines.append("")
        if self.subject:
            lines.append(f"# {self.subject}")
            lines.append("")
        lines.append(self.body)
        lines.append("")
        return "\n".join(lines)

    @classmethod
    def parse(cls, path: Path) -> Message:
        """Read a message file and return a Message instance."""
        text = path.read_text(encoding="utf-8")
        # Split frontmatter
        if not text.startswith("---"):
            raise ValueError(f"Message file missing frontmatter: {path}")
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ValueError(f"Malformed frontmatter in {path}")
        frontmatter, body = parts[1], parts[2]
        # Parse key: value lines
        meta: dict = {}
        for line in frontmatter.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body_text = body.strip()
        # Strip the first # subject line from body if duplicated
        body_lines = body_text.splitlines()
        if body_lines and body_lines[0].startswith("# "):
            body_text = "\n".join(body_lines[1:]).lstrip()
        return cls(
            from_=meta.get("from", "unknown"),
            to=meta.get("to", "unknown"),
            type=meta.get("type", "update"),
            subject=meta.get("subject", ""),
            body=body_text,
            thread=meta.get("thread") or None,
            reply_to=meta.get("reply_to") or None,
            urgent=meta.get("urgent", "false").lower() == "true",
            status=meta.get("status", "unread"),
            created=meta.get("created"),
        )

    def parse_filename_timestamp(self, filename: str) -> str | None:
        """Extract the YYYY-MM-DD_HH-MM-SS portion from a message filename."""
        m = FILENAME_RE.match(filename)
        if not m:
            return None
        return f"{m.group(1)}_{m.group(2)}"


@dataclass
class FilenameParts:
    timestamp: str  # YYYY-MM-DD_HH-MM-SS
    from_: str
    to: str
    type: str
    slug: str

    @classmethod
    def from_name(cls, name: str) -> FilenameParts | None:
        m = FILENAME_RE.match(name)
        if not m:
            return None
        date_part, time_part, from_, to, type_, slug = m.groups()
        return cls(
            timestamp=f"{date_part}_{time_part}",
            from_=from_,
            to=to,
            type=type_,
            slug=slug,
        )


class MessageStore:
    """Reads and writes messages in a project's .claude/messages/ directory."""

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.root = self.project_root / ".claude" / "messages"

    def mailbox_path(self, mailbox: str = DEFAULT_MAILBOX) -> Path:
        return self.root / mailbox

    def ensure(self, mailbox: str = DEFAULT_MAILBOX) -> None:
        self.mailbox_path(mailbox).mkdir(parents=True, exist_ok=True)

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".tmp.{uuid.uuid4().hex[:8]}")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)

    def send(self, message: Message) -> Path:
        """Write a message to the store. Returns the path of the created file.

        The mailbox is determined by `message.mailbox` (default 'inbox').
        If `message.to` starts with a mailbox prefix like 'mailbox:review/vasya',
        the prefix is stripped and the mailbox is used instead.
        """
        mailbox = message.mailbox
        to_target = message.to
        if to_target.startswith("mailbox:"):
            mailbox = to_target.split(":", 1)[1].split("/", 1)[0]
            to_target = to_target.split("/", 1)[1] if "/" in to_target else "*"
            message.to = to_target
            message.mailbox = mailbox

        self.ensure(mailbox)
        base = message.filename()
        path = self.mailbox_path(mailbox) / base
        # Collision handling - should be very rare since timestamp is to seconds
        counter = 2
        while path.exists():
            stem = base[:-3]
            path = self.mailbox_path(mailbox) / f"{stem}_{counter}.md"
            counter += 1
        self._atomic_write(path, message.render())
        return path

    def list_mailbox(self, mailbox: str = DEFAULT_MAILBOX) -> list[Path]:
        d = self.mailbox_path(mailbox)
        if not d.exists():
            return []
        return sorted(p for p in d.glob("*.md") if p.is_file())

    def inbox(
        self,
        recipient: str,
        mailbox: str = DEFAULT_MAILBOX,
        include_read: bool = False,
        include_archived: bool = False,
    ) -> list[Message]:
        """Return messages addressed to `recipient` in the given mailbox.

        A message is addressed to recipient if its `to` field equals recipient
        or is '*' (broadcast).
        """
        results: list[Message] = []
        for path in self.list_mailbox(mailbox):
            try:
                m = Message.parse(path)
            except (OSError, ValueError):
                continue
            if m.to != recipient and m.to != "*":
                continue
            if not include_read and m.status == "read":
                continue
            if not include_archived and m.status == "archived":
                continue
            results.append(m)
        return results

    def thread(self, thread_id: str, mailbox: str = DEFAULT_MAILBOX) -> list[Message]:
        """Return all messages belonging to a thread, in chronological order."""
        results: list[Message] = []
        for path in self.list_mailbox(mailbox):
            try:
                m = Message.parse(path)
            except (OSError, ValueError):
                continue
            # Match by thread field OR by filename being the thread start
            if m.thread == thread_id or path.name == thread_id or path.stem == thread_id:
                results.append(m)
        # Sort by filename timestamp (already in sort order by filename)
        return results

    def list_mailboxes(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def mark_status(self, path: Path, new_status: str) -> None:
        """Write a new status-transition marker file next to the original.

        Append-only semantics: we never edit the original file. Instead, we
        write a short status marker message that tools can aggregate.
        """
        if new_status not in VALID_STATUS:
            raise ValueError(f"Invalid status {new_status}")
        original = Message.parse(path)
        marker = Message(
            from_="system",
            to=original.from_,
            type="ack",
            subject=f"Status: {new_status}",
            body=f"Message `{path.name}` marked as {new_status}.",
            thread=original.thread or path.stem,
            reply_to=path.name,
            status="unread",
            mailbox=path.parent.name,
        )
        self.send(marker)
