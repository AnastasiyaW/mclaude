"""Tests for mclaude.bridge.client - offline fallback and online mode."""
from __future__ import annotations

from pathlib import Path

from mclaude.bridge.client import BridgeClient, BridgeConfig


def test_offline_send_message_falls_back_to_file(tmp_path: Path) -> None:
    """With no hub URL, messages go to the file layer."""
    bridge = BridgeClient(BridgeConfig(
        hub_url="",  # no hub
        identity="ani",
        project_id="default",
        project_root=tmp_path,
    ))

    result = bridge.send_message(
        to="vasya",
        type="question",
        subject="How to mock datetime",
        body="I want to freeze time",
    )

    assert result["delivered"] == "file"
    path = Path(result["path"])
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "How to mock datetime" in content
    assert "freeze time" in content
    assert "from: ani" in content
    assert "to: vasya" in content


def test_offline_inbox_reads_files(tmp_path: Path) -> None:
    """With no hub, inbox() scans local .claude/messages/inbox/."""
    bridge_a = BridgeClient(BridgeConfig(hub_url="", identity="ani", project_root=tmp_path))
    bridge_a.send_message(to="vasya", type="question", subject="Q1", body="body1")

    bridge_b = BridgeClient(BridgeConfig(hub_url="", identity="vasya", project_root=tmp_path))
    msgs = bridge_b.inbox()
    assert len(msgs) == 1
    assert msgs[0]["subject"] == "Q1"
    assert msgs[0]["from"] == "ani"


def test_broadcast_offline(tmp_path: Path) -> None:
    bridge = BridgeClient(BridgeConfig(hub_url="", identity="system", project_root=tmp_path))
    result = bridge.send_message(
        to="*",
        type="broadcast",
        subject="Rebasing main",
    )
    assert result["delivered"] == "file"
    # Both recipients see it
    for recipient in ["ani", "vasya"]:
        b = BridgeClient(BridgeConfig(hub_url="", identity=recipient, project_root=tmp_path))
        msgs = b.inbox()
        assert any(m["subject"] == "Rebasing main" for m in msgs)


def test_notify_offline_writes_file(tmp_path: Path) -> None:
    bridge = BridgeClient(BridgeConfig(hub_url="", identity="ani", project_root=tmp_path))
    result = bridge.notify(message="Tests passed", subject="CI")
    assert result["delivered"] == "file"
    assert Path(result["path"]).exists()


def test_online_mode_with_stubbed_hub(tmp_path: Path, app_client) -> None:
    """When a hub is reachable, messages go via HTTP and file is not touched."""
    # Use the fastapi TestClient as a "remote hub" - we wrap its base_url
    # by monkey-patching the BridgeClient's _http attribute.
    app_client.post("/api/projects", json={"id": "default", "name": "P"})

    bridge = BridgeClient(BridgeConfig(
        hub_url="http://testserver",  # dummy
        identity="ani",
        project_root=tmp_path,
    ))
    # Replace the httpx client with the FastAPI TestClient
    bridge._http = app_client  # type: ignore[assignment]

    result = bridge.send_message(
        to="vasya",
        type="question",
        subject="Test online",
        body="online body",
    )

    assert result["delivered"] == "hub"
    assert "event_id" in result
    # File NOT created in offline fallback location
    messages_dir = tmp_path / ".claude" / "messages"
    assert not messages_dir.exists() or not list(messages_dir.rglob("*.md"))


def test_lock_claim_online(tmp_path: Path, app_client) -> None:
    app_client.post("/api/projects", json={"id": "default", "name": "P"})
    bridge = BridgeClient(BridgeConfig(
        hub_url="http://testserver",
        identity="ani",
        session_id="session-1",
        project_root=tmp_path,
    ))
    bridge._http = app_client  # type: ignore[assignment]

    result = bridge.claim_lock(
        slug="fix-auth",
        description="Fixing the auth bug",
        files=["src/auth.py"],
    )
    assert result["status"] == "claimed"
    assert result["lock"]["slug"] == "fix-auth"
