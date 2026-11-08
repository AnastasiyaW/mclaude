"""Tests for mclaude.channel against an in-memory fake of the Vikunja API."""
from __future__ import annotations

import json

import pytest

from mclaude.channel import (
    Channel,
    ChannelConfig,
    ChannelError,
    html_to_text,
    parse_title,
    text_to_html,
    title_for,
)
from mclaude.messages import Message, MessageStore

ME, PEER = "acme", "anya"
USERS = {"acme-login": 5, "anya-login": 2}


class FakeVikunja:
    """Just enough of Vikunja v0.24 for the channel: tasks, comments, assignees."""

    def __init__(self, scoped: bool = True) -> None:
        self.scoped = scoped  # scoped API tokens cannot list users
        self.acting = "acme-login"
        self.tasks: dict[int, dict] = {}
        self.comments: dict[int, list[dict]] = {}
        self.next_id = 100
        self.drop_assignee = False

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id

    def _user(self, name: str) -> dict:
        return {"id": USERS[name], "username": name}

    def request(self, method, path, payload=None, auth=True):
        path, _, _query = path.partition("?")
        parts = path.strip("/").split("/")
        if path == "/info":
            return {"version": "v0.24.6"}
        if parts[0] == "projects" and len(parts) == 2:
            return {"id": int(parts[1]), "title": "partner"}
        if parts[0] == "projects" and parts[2] == "projectusers":
            if self.scoped:
                raise ChannelError("GET projectusers -> HTTP 401", status=401)
            return [self._user(n) for n in USERS]
        if parts[0] == "users":
            raise ChannelError("GET users -> HTTP 401", status=401)
        if parts[0] == "projects" and parts[2] == "tasks":
            if method == "PUT":
                tid = self._id()
                self.tasks[tid] = {
                    "id": tid, "title": payload["title"], "description": payload.get("description", ""),
                    "done": False, "assignees": None, "created_by": self._user(self.acting),
                    "updated": f"u{tid}", "created": "now",
                }
                return dict(self.tasks[tid])
            if "page=1" in _query or "page" not in _query:
                return [dict(t) for t in sorted(self.tasks.values(), key=lambda t: -t["id"])]
            return []
        if parts[0] == "projects" and parts[2] == "users":
            return {"ok": True}
        if parts[0] == "tasks":
            tid = int(parts[1])
            task = self.tasks[tid]
            if len(parts) == 2:
                if method == "POST":
                    task.update(payload)
                    task["updated"] = f"u{self._id()}"
                return dict(task)
            if parts[2] == "assignees":
                uid = payload["user_id"]
                name = next(n for n, i in USERS.items() if i == uid)
                if not self.drop_assignee:
                    task["assignees"] = (task["assignees"] or []) + [self._user(name)]
                return {"user_id": uid}
            if parts[2] == "comments":
                if method == "PUT":
                    cid = self._id()
                    c = {"id": cid, "comment": payload["comment"], "author": self._user(self.acting),
                         "created": "now"}
                    self.comments.setdefault(tid, []).append(c)
                    task["updated"] = f"u{cid}"
                    return dict(c)
                if "page=1" in _query:
                    return list(self.comments.get(tid, []))
                return []
        raise AssertionError(f"unexpected {method} {path}")


@pytest.fixture
def config() -> ChannelConfig:
    return ChannelConfig(
        base_url="https://tasks.example.com", project_id=9, me=ME, peer=PEER,
        me_username="acme-login", peer_username="anya-login",
    )


@pytest.fixture
def setup(tmp_path, config):
    fake = FakeVikunja()
    return fake, Channel(config, fake, tmp_path)


def test_title_roundtrip():
    assert title_for("acme", "anya", " Export ") == "[acme -> anya] Export"
    assert parse_title("[acme -> anya] Export format") == ("acme", "anya", "Export format")
    assert parse_title("Plain task") is None


def test_html_roundtrip_escapes_and_keeps_code():
    text = "Hi <b>there</b>\nline 2\n\n```\nx = 1 < 2\n```\nПривет"
    markup = text_to_html(text)
    assert "<b>" not in markup and "&lt;b&gt;" in markup
    assert "<pre><code>x = 1 &lt; 2</code></pre>" in markup
    back = html_to_text(markup)
    assert "Hi <b>there</b>" in back and "x = 1 < 2" in back and "Привет" in back


def test_config_rejects_bad_identity_and_never_stores_token(tmp_path, monkeypatch):
    with pytest.raises(ChannelError):
        ChannelConfig("https://x.example", 1, "bad name", "p", "a", "b")
    cfg = ChannelConfig("https://x.example/", 1, "me", "peer", "a", "b")
    assert cfg.base_url == "https://x.example/api/v1"
    monkeypatch.setenv("MCLAUDE_CHANNEL_TOKEN", "dummy-token")
    path = cfg.save(tmp_path)
    assert "dummy-token" not in path.read_text(encoding="utf-8")
    assert ChannelConfig.load(tmp_path).token() == "dummy-token"
    monkeypatch.delenv("MCLAUDE_CHANNEL_TOKEN")
    with pytest.raises(ChannelError):
        cfg.token()


def test_send_opens_thread_assigns_peer_and_mentions(setup):
    fake, ch = setup
    # The peer must be visible somewhere for a scoped token: a task they created.
    fake.acting = "anya-login"
    fake.request("PUT", "/projects/9/tasks", {"title": "[anya -> acme] hello"})
    fake.acting = "acme-login"

    tid, cid = ch.send("Export format", "Можно пример CSV?")
    task = fake.tasks[tid]
    assert task["title"] == "[acme -> anya] Export format"
    assert [u["username"] for u in task["assignees"]] == ["anya-login"]
    body = fake.comments[tid][0]["comment"]
    assert "@anya-login" in body and "Можно пример CSV?" in body

    tid2, _ = ch.send("export format", "second letter")
    assert tid2 == tid, "same topic must reuse the open thread"
    assert [u["username"] for u in fake.tasks[tid]["assignees"]] == ["anya-login"]


def test_send_reports_assignee_that_did_not_stick(setup):
    fake, ch = setup
    fake.acting = "anya-login"
    fake.request("PUT", "/projects/9/tasks", {"title": "[anya -> acme] hello"})
    fake.acting = "acme-login"
    fake.drop_assignee = True
    with pytest.raises(ChannelError, match="letter delivered"):
        ch.send("Topic", "text")


def test_inbox_returns_only_peer_letters_once(setup):
    fake, ch = setup
    fake.acting = "anya-login"
    task = fake.request("PUT", "/projects/9/tasks",
                        {"title": "[anya -> acme] Contract", "description": "<p>Draft attached</p>"})
    fake.request("PUT", f"/tasks/{task['id']}/comments", {"comment": "<p>Please review</p>"})
    fake.acting = "acme-login"
    ch.send("", "my own reply", task_id=task["id"])

    letters = ch.inbox()
    assert [lt.text for lt in letters] == ["Draft attached", "Please review"]
    assert all(lt.author == "anya-login" and lt.topic == "Contract" for lt in letters)
    assert ch.inbox() == [], "seen letters must not come back"

    fake.acting = "anya-login"
    fake.request("PUT", f"/tasks/{task['id']}/comments", {"comment": "<p>One more</p>"})
    assert [lt.text for lt in ch.inbox(mark=False)] == ["One more"]
    assert [lt.text for lt in ch.inbox()] == ["One more"], "peek must not mark"


def test_pull_writes_regular_mclaude_messages(setup, tmp_path):
    fake, ch = setup
    fake.acting = "anya-login"
    task = fake.request("PUT", "/projects/9/tasks", {"title": "[anya -> acme] Invoice"})
    fake.request("PUT", f"/tasks/{task['id']}/comments", {"comment": "<p>Sent today</p>"})

    paths = ch.pull()
    assert len(paths) == 1
    msg = Message.parse(paths[0])
    assert (msg.from_, msg.to, msg.subject) == (PEER, ME, "Invoice")
    assert "Sent today" in msg.body and f"mclaude channel reply {task['id']}" in msg.body
    assert MessageStore(tmp_path).inbox(recipient=ME)


def test_close_sends_full_task(setup):
    fake, ch = setup
    task = fake.request("PUT", "/projects/9/tasks", {"title": "[acme -> anya] Done topic"})
    ch.close(task["id"])
    assert fake.tasks[task["id"]]["done"] is True
    assert fake.tasks[task["id"]]["title"] == "[acme -> anya] Done topic"


def test_doctor_three_states(setup):
    fake, ch = setup
    checks = {c.name.split(" (")[0]: c.state for c in ch.doctor()}
    assert checks["server reachable"] == "PASS"
    assert checks["comments readable"] == "UNKNOWN"  # no task yet
    assert checks["peer 'anya-login' resolvable"] == "FAIL"  # scoped token, no trace yet

    class Down:
        def request(self, *a, **k):
            raise ChannelError("network error")

    down = Channel(ch.config, Down(), ch.root).doctor()
    assert [c.state for c in down] == ["UNKNOWN"]


def test_cli_setup_writes_config(tmp_path, monkeypatch, capsys):
    import sys

    from mclaude import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "mclaude", "channel", "setup", "--url", "https://tasks.example.com", "--project", "9",
        "--me", ME, "--peer", PEER, "--me-username", "acme-login", "--peer-username", "anya-login",
    ])
    assert cli.main() == 0
    data = json.loads((tmp_path / ".claude" / "mclaude-channel.json").read_text(encoding="utf-8"))
    assert data["base_url"] == "https://tasks.example.com/api/v1" and data["peer"] == PEER
