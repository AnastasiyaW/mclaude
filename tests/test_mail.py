"""Tests for mclaude high-level mail API."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from mclaude.mail import Mail
from mclaude.messages import Message, MessageStore


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "messages" / "inbox").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MCLAUDE_IDENTITY", "ani")
    return tmp_path


class TestMailCheck:
    def test_empty_inbox(self, project: Path):
        mail = Mail(identity="ani", project_root=project)
        assert mail.check() == []

    def test_sees_new_message(self, project: Path):
        store = MessageStore(project)
        store.send(Message(
            from_="vasya", to="ani", type="question",
            subject="How to test?", body="What framework?",
        ))
        mail = Mail(identity="ani", project_root=project)
        msgs = mail.check()
        assert len(msgs) == 1
        assert msgs[0].subject == "How to test?"

    def test_dedup_on_second_check(self, project: Path):
        store = MessageStore(project)
        store.send(Message(
            from_="vasya", to="ani", type="question",
            subject="First question", body="Body",
        ))
        mail = Mail(identity="ani", project_root=project)
        assert len(mail.check()) == 1
        # Second check should return empty (already seen)
        assert len(mail.check()) == 0

    def test_new_message_after_check(self, project: Path):
        store = MessageStore(project)
        store.send(Message(
            from_="vasya", to="ani", type="update",
            subject="First", body="1",
        ))
        mail = Mail(identity="ani", project_root=project)
        mail.check()  # sees first

        # New message arrives
        store.send(Message(
            from_="vasya", to="ani", type="update",
            subject="Second", body="2",
        ))
        msgs = mail.check()
        assert len(msgs) == 1
        assert msgs[0].subject == "Second"

    def test_broadcast_visible(self, project: Path):
        store = MessageStore(project)
        store.send(Message(
            from_="system", to="*", type="broadcast",
            subject="Rebase in 5 min", body="Heads up",
        ))
        mail = Mail(identity="ani", project_root=project)
        msgs = mail.check()
        assert len(msgs) == 1

    def test_check_all_ignores_state(self, project: Path):
        store = MessageStore(project)
        store.send(Message(
            from_="vasya", to="ani", type="question",
            subject="Q1", body="B",
        ))
        mail = Mail(identity="ani", project_root=project)
        mail.check()  # marks as seen
        # check_all should still return it
        assert len(mail.check_all()) == 1


class TestMailReply:
    def test_reply_auto_threads(self, project: Path):
        store = MessageStore(project)
        path = store.send(Message(
            from_="vasya", to="ani", type="question",
            subject="API schema?", body="What format?",
        ))
        original = Message.parse(path)

        mail = Mail(identity="ani", project_root=project)
        reply_path = mail.reply(original, "Use JSON with JWT")

        reply = Message.parse(reply_path)
        assert reply.type == "answer"
        assert reply.from_ == "ani"
        assert reply.to == "vasya"
        assert "Re:" in reply.subject
        assert reply.reply_to == original.filename()

    def test_reply_preserves_thread(self, project: Path):
        store = MessageStore(project)
        path = store.send(Message(
            from_="vasya", to="ani", type="question",
            subject="Q", body="B", thread="existing-thread-123",
        ))
        original = Message.parse(path)

        mail = Mail(identity="ani", project_root=project)
        reply_path = mail.reply(original, "Answer")
        reply = Message.parse(reply_path)
        assert reply.thread == "existing-thread-123"


class TestMailAsk:
    def test_ask_returns_thread_id(self, project: Path):
        mail = Mail(identity="ani", project_root=project)
        thread_id = mail.ask("vasya", "What's the auth API?")
        assert thread_id  # non-empty string
        assert "auth" in thread_id.lower() or len(thread_id) > 5

    def test_ask_creates_question_message(self, project: Path):
        mail = Mail(identity="ani", project_root=project)
        mail.ask("vasya", "How to deploy?", body="Need step by step")

        # Check that vasya sees it
        vasya_mail = Mail(identity="vasya", project_root=project)
        msgs = vasya_mail.check()
        assert len(msgs) == 1
        assert msgs[0].type == "question"
        assert msgs[0].from_ == "ani"
        assert "deploy" in msgs[0].subject.lower()


class TestMailDigest:
    def test_empty_digest(self, project: Path):
        mail = Mail(identity="ani", project_root=project)
        d = mail.digest()
        assert d["total"] == 0
        assert d["urgent"] == 0

    def test_digest_counts(self, project: Path):
        store = MessageStore(project)
        store.send(Message(from_="vasya", to="ani", type="question", subject="Q1", body="B"))
        store.send(Message(from_="vasya", to="ani", type="question", subject="Q2", body="B"))
        store.send(Message(from_="system", to="ani", type="update", subject="Up", body="B", urgent=True))

        mail = Mail(identity="ani", project_root=project)
        d = mail.digest()
        assert d["total"] == 3
        assert d["urgent"] == 1
        assert d["by_sender"]["vasya"] == 2
        assert d["by_sender"]["system"] == 1
        assert d["by_type"]["question"] == 2


class TestMailSend:
    def test_generic_send(self, project: Path):
        mail = Mail(identity="ani", project_root=project)
        path = mail.send("vasya", "FYI the build is green", subject="Build status")
        assert path.exists()
        msg = Message.parse(path)
        assert msg.type == "update"
        assert msg.from_ == "ani"


class TestMailResetState:
    def test_reset_shows_all_again(self, project: Path):
        store = MessageStore(project)
        store.send(Message(from_="vasya", to="ani", type="update", subject="Old", body="B"))

        mail = Mail(identity="ani", project_root=project)
        mail.check()  # marks as seen
        assert len(mail.check()) == 0

        mail.reset_state()
        assert len(mail.check()) == 1
