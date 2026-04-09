"""Tests for mclaude.messages - live inter-session messaging."""
from __future__ import annotations

from pathlib import Path

import pytest

from mclaude.messages import (
    DEFAULT_MAILBOX,
    FilenameParts,
    Message,
    MessageStore,
    slugify,
)


def test_slugify_short_subject() -> None:
    assert slugify("How to mock datetime in pytest") == "how-to-mock-datetime-in-pytest"
    assert slugify("") == "untitled"


def test_message_validation() -> None:
    # Missing from_
    with pytest.raises(ValueError, match="from_"):
        Message(from_="", to="vasya", subject="Hi")

    # Missing to
    with pytest.raises(ValueError, match="to"):
        Message(from_="ani", to="", subject="Hi")

    # Bad type
    with pytest.raises(ValueError, match="Invalid type"):
        Message(from_="ani", to="vasya", type="gossip", subject="Hi")

    # Valid
    m = Message(from_="ani", to="vasya", type="question", subject="Hi")
    assert m.from_ == "ani"


def test_filename_format() -> None:
    m = Message(
        from_="ani",
        to="vasya",
        type="question",
        subject="How to mock datetime",
        timestamp="2026-04-09_14-32-17",
    )
    fn = m.filename()
    assert fn == "2026-04-09_14-32-17_ani_vasya_question_how-to-mock-datetime.md"


def test_filename_parse_roundtrip() -> None:
    parts = FilenameParts.from_name("2026-04-09_14-32-17_ani_vasya_question_how-to-mock-datetime.md")
    assert parts is not None
    assert parts.timestamp == "2026-04-09_14-32-17"
    assert parts.from_ == "ani"
    assert parts.to == "vasya"
    assert parts.type == "question"
    assert parts.slug == "how-to-mock-datetime"

    # Bad filenames return None
    assert FilenameParts.from_name("garbage.md") is None
    assert FilenameParts.from_name("no-extension") is None


def test_render_and_parse_roundtrip(tmp_path: Path) -> None:
    store = MessageStore(project_root=tmp_path)
    original = Message(
        from_="ani",
        to="vasya",
        type="question",
        subject="Datetime mocking",
        body="How do I freeze time in pytest?\n\nI've tried freezegun but it's flaky.",
        urgent=True,
    )
    path = store.send(original)
    assert path.exists()

    parsed = Message.parse(path)
    assert parsed.from_ == "ani"
    assert parsed.to == "vasya"
    assert parsed.type == "question"
    assert parsed.subject == "Datetime mocking"
    assert "freeze time" in parsed.body
    assert "flaky" in parsed.body
    assert parsed.urgent is True


def test_inbox_filters_by_recipient(tmp_path: Path) -> None:
    store = MessageStore(project_root=tmp_path)
    store.send(Message(from_="ani", to="vasya", type="update", subject="msg1"))
    store.send(Message(from_="vasya", to="ani", type="answer", subject="msg2"))
    store.send(Message(from_="bot", to="*", type="broadcast", subject="msg3"))

    vasya_inbox = store.inbox(recipient="vasya")
    assert len(vasya_inbox) == 2  # direct + broadcast
    subjects = {m.subject for m in vasya_inbox}
    assert "msg1" in subjects
    assert "msg3" in subjects

    ani_inbox = store.inbox(recipient="ani")
    assert len(ani_inbox) == 2  # direct + broadcast
    subjects = {m.subject for m in ani_inbox}
    assert "msg2" in subjects
    assert "msg3" in subjects


def test_threading(tmp_path: Path) -> None:
    store = MessageStore(project_root=tmp_path)

    # Original question
    q = Message(
        from_="ani",
        to="vasya",
        type="question",
        subject="Datetime mocking",
        body="How to freeze time?",
        timestamp="2026-04-09_14-32-17",
    )
    q_path = store.send(q)
    thread_id = q_path.stem  # use original filename as thread ID

    # Answer references thread
    a = Message(
        from_="vasya",
        to="ani",
        type="answer",
        subject="Re: Datetime mocking",
        body="Use freezegun.",
        thread=thread_id,
        reply_to=q_path.name,
        timestamp="2026-04-09_14-33-05",
    )
    store.send(a)

    # Unrelated message in same mailbox
    store.send(Message(
        from_="bot",
        to="*",
        type="update",
        subject="Unrelated",
        timestamp="2026-04-09_14-40-00",
    ))

    thread_msgs = store.thread(thread_id)
    # Thread returns both the original (matched by stem) and the reply
    assert len(thread_msgs) >= 2
    types = {m.type for m in thread_msgs}
    assert "question" in types
    assert "answer" in types


def test_broadcast_reaches_all(tmp_path: Path) -> None:
    store = MessageStore(project_root=tmp_path)
    store.send(Message(
        from_="system",
        to="*",
        type="broadcast",
        subject="Server restart at 15:00",
    ))
    # Everyone should see it
    for recipient in ["ani", "vasya", "anyone"]:
        assert len(store.inbox(recipient=recipient)) == 1


def test_multiple_mailboxes(tmp_path: Path) -> None:
    store = MessageStore(project_root=tmp_path)
    store.send(Message(
        from_="ani",
        to="review-team",
        type="request",
        subject="PR review needed",
        mailbox="review",
    ))
    store.send(Message(
        from_="ani",
        to="infra",
        type="request",
        subject="Deploy please",
        mailbox="infra-requests",
    ))

    mailboxes = store.list_mailboxes()
    assert "review" in mailboxes
    assert "infra-requests" in mailboxes

    review_msgs = store.list_mailbox("review")
    assert len(review_msgs) == 1


def test_no_overwrite_on_collision(tmp_path: Path) -> None:
    store = MessageStore(project_root=tmp_path)
    m1 = Message(
        from_="ani",
        to="vasya",
        type="update",
        subject="Collision",
        body="first",
        timestamp="2026-04-09_14-32-17",
    )
    m2 = Message(
        from_="ani",
        to="vasya",
        type="update",
        subject="Collision",
        body="second",
        timestamp="2026-04-09_14-32-17",
    )
    p1 = store.send(m1)
    p2 = store.send(m2)
    assert p1 != p2
    assert "_2" in p2.name
    # Both files exist with different content
    assert "first" in p1.read_text(encoding="utf-8")
    assert "second" in p2.read_text(encoding="utf-8")


def test_mark_status_creates_ack(tmp_path: Path) -> None:
    store = MessageStore(project_root=tmp_path)
    q = Message(
        from_="ani",
        to="vasya",
        type="question",
        subject="Quick question",
    )
    p = store.send(q)
    store.mark_status(p, "read")

    # The original message file is unchanged
    original = Message.parse(p)
    assert original.status == "unread"  # we did NOT edit it

    # A new ack marker file exists
    all_msgs = store.list_mailbox()
    assert len(all_msgs) == 2  # original + ack
    ack_msgs = [m for m in all_msgs if "ack" in m.name]
    assert len(ack_msgs) == 1
