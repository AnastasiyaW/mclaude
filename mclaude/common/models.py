"""
Data models shared between hub server, client, bridge, and tests.

We deliberately use dataclasses with a `to_dict` method instead of Pydantic BaseModel
for the parts that are used outside the hub server. This keeps `common/` free of
any runtime dependency on Pydantic, so the bridge (which runs in Claude Code
environment) does not need to install Pydantic just to talk to the hub.

The hub server code wraps these in Pydantic models at its boundary.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum


class EventType(str, Enum):
    """What kind of event is flowing through the hub."""

    # Session lifecycle
    SESSION_STARTED = "session_started"
    SESSION_HEARTBEAT = "session_heartbeat"
    SESSION_ENDED = "session_ended"

    # Work locks
    LOCK_CLAIMED = "lock_claimed"
    LOCK_RELEASED = "lock_released"
    LOCK_FORCE_RELEASED = "lock_force_released"

    # Messages (mirrors mclaude.messages.Message types)
    MESSAGE_QUESTION = "message_question"
    MESSAGE_ANSWER = "message_answer"
    MESSAGE_REQUEST = "message_request"
    MESSAGE_UPDATE = "message_update"
    MESSAGE_ERROR = "message_error"
    MESSAGE_BROADCAST = "message_broadcast"
    MESSAGE_ACK = "message_ack"

    # User-facing notifications
    NOTIFY_TASK_COMPLETE = "notify_task_complete"
    NOTIFY_ERROR = "notify_error"
    NOTIFY_ATTENTION_NEEDED = "notify_attention_needed"

    # Voice I/O
    VOICE_TRANSCRIPT = "voice_transcript"  # user said something, transcribed
    TTS_REQUEST = "tts_request"  # a client asks any other client to speak text

    # Handoff
    HANDOFF_WRITTEN = "handoff_written"
    HANDOFF_RESUMED = "handoff_resumed"


@dataclass
class Event:
    """A single event flowing through the hub.

    Events are the lowest-level primitive: everything else (message, lock,
    notification) is modeled as a specific EventType plus a payload.
    """

    project_id: str
    type: EventType
    from_identity: str
    to_identity: str = "*"  # "*" = broadcast to all listeners
    subject: str = ""
    body: str = ""
    urgent: bool = False
    thread: str | None = None
    reply_to: str | None = None
    session_id: str | None = None
    id: str = ""
    created_at: str = ""
    delivered: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex
        if not self.created_at:
            self.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value if isinstance(self.type, EventType) else self.type
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Event:
        event_type = data.get("type", EventType.MESSAGE_UPDATE)
        if isinstance(event_type, str):
            event_type = EventType(event_type)
        return cls(
            project_id=data["project_id"],
            type=event_type,
            from_identity=data["from_identity"],
            to_identity=data.get("to_identity", "*"),
            subject=data.get("subject", ""),
            body=data.get("body", ""),
            urgent=bool(data.get("urgent", False)),
            thread=data.get("thread"),
            reply_to=data.get("reply_to"),
            session_id=data.get("session_id"),
            id=data.get("id", ""),
            created_at=data.get("created_at", ""),
            delivered=bool(data.get("delivered", False)),
        )


@dataclass
class IdentityInfo:
    """A registered identity in the hub - mirrors mclaude.registry.Identity."""

    project_id: str
    name: str
    owner: str = ""
    machine: str = ""
    roles: list[str] = field(default_factory=list)
    notify: dict = field(default_factory=dict)
    registered_at: str = ""
    last_seen: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SessionInfo:
    """An active Claude session registered with the hub."""

    id: str
    project_id: str
    identity: str
    status: str = "active"  # active | idle | ended
    started_at: str = ""
    last_heartbeat: str = ""
    machine: str = ""

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        if not self.last_heartbeat:
            self.last_heartbeat = self.started_at

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LockClaim:
    """A work lock held by a session, mirrored from mclaude.locks."""

    project_id: str
    slug: str
    session_id: str
    identity: str
    description: str = ""
    files: list[str] = field(default_factory=list)
    claimed_at: str = ""
    heartbeat_at: str = ""
    released_at: str | None = None

    def __post_init__(self) -> None:
        if not self.claimed_at:
            self.claimed_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        if not self.heartbeat_at:
            self.heartbeat_at = self.claimed_at

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MessagePayload:
    """The subset of Event fields that look like a mclaude.messages.Message.

    Used by the bridge to convert between file-based mclaude messages and
    hub events without losing information.
    """

    from_: str
    to: str
    type: str
    subject: str = ""
    body: str = ""
    thread: str | None = None
    reply_to: str | None = None
    urgent: bool = False
    mailbox: str = "inbox"

    def to_event_type(self) -> EventType:
        """Map a message type string to the corresponding EventType."""
        mapping = {
            "question": EventType.MESSAGE_QUESTION,
            "answer": EventType.MESSAGE_ANSWER,
            "request": EventType.MESSAGE_REQUEST,
            "update": EventType.MESSAGE_UPDATE,
            "error": EventType.MESSAGE_ERROR,
            "broadcast": EventType.MESSAGE_BROADCAST,
            "ack": EventType.MESSAGE_ACK,
        }
        return mapping.get(self.type, EventType.MESSAGE_UPDATE)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["from"] = d.pop("from_")
        return d

    @classmethod
    def from_dict(cls, data: dict) -> MessagePayload:
        d = dict(data)
        if "from" in d and "from_" not in d:
            d["from_"] = d.pop("from")
        return cls(**{k: v for k, v in d.items() if k in cls.__annotations__})
