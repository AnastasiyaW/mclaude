"""Unit tests for mclaude.common.models."""
from __future__ import annotations

from mclaude.common.models import (
    Event,
    EventType,
    IdentityInfo,
    LockClaim,
    MessagePayload,
    SessionInfo,
)


def test_event_defaults() -> None:
    event = Event(
        project_id="p1",
        type=EventType.MESSAGE_UPDATE,
        from_identity="ani",
    )
    assert event.to_identity == "*"
    assert event.id  # auto-generated
    assert event.created_at  # auto-generated
    assert event.delivered is False


def test_event_to_dict_serializes_enum() -> None:
    event = Event(
        project_id="p1",
        type=EventType.MESSAGE_QUESTION,
        from_identity="ani",
        to_identity="vasya",
    )
    d = event.to_dict()
    assert d["type"] == "message_question"
    assert d["from_identity"] == "ani"


def test_event_from_dict_roundtrip() -> None:
    original = Event(
        project_id="p1",
        type=EventType.NOTIFY_TASK_COMPLETE,
        from_identity="ani",
        subject="done",
        urgent=True,
    )
    d = original.to_dict()
    restored = Event.from_dict(d)
    assert restored.project_id == original.project_id
    assert restored.type == original.type
    assert restored.urgent is True


def test_identity_info_defaults() -> None:
    identity = IdentityInfo(project_id="p1", name="ani")
    assert identity.owner == ""
    assert identity.roles == []
    assert identity.notify == {}


def test_session_info_auto_timestamps() -> None:
    session = SessionInfo(id="s1", project_id="p1", identity="ani")
    assert session.started_at
    assert session.last_heartbeat == session.started_at
    assert session.status == "active"


def test_lock_claim_defaults() -> None:
    lock = LockClaim(
        project_id="p1",
        slug="fix-auth",
        session_id="s1",
        identity="ani",
    )
    assert lock.files == []
    assert lock.claimed_at
    assert lock.released_at is None


def test_message_payload_maps_to_event_type() -> None:
    for msg_type, expected in [
        ("question", EventType.MESSAGE_QUESTION),
        ("answer", EventType.MESSAGE_ANSWER),
        ("request", EventType.MESSAGE_REQUEST),
        ("update", EventType.MESSAGE_UPDATE),
        ("error", EventType.MESSAGE_ERROR),
        ("broadcast", EventType.MESSAGE_BROADCAST),
        ("ack", EventType.MESSAGE_ACK),
    ]:
        p = MessagePayload(from_="ani", to="vasya", type=msg_type)
        assert p.to_event_type() == expected


def test_message_payload_unknown_type_defaults_to_update() -> None:
    p = MessagePayload(from_="ani", to="vasya", type="weird")
    assert p.to_event_type() == EventType.MESSAGE_UPDATE


def test_message_payload_dict_renames_from() -> None:
    p = MessagePayload(from_="ani", to="vasya", type="question", subject="q")
    d = p.to_dict()
    assert "from" in d
    assert "from_" not in d
    restored = MessagePayload.from_dict(d)
    assert restored.from_ == "ani"
