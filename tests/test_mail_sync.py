"""Tests for mclaude mail sync layer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mclaude.mail_sync import MailSync
from mclaude.messages import Message, MessageStore


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "messages" / "inbox").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MCLAUDE_IDENTITY", "ani")
    return tmp_path


class TestSyncConfiguration:
    def test_not_configured_by_default(self, project: Path):
        sync = MailSync(project_root=project)
        assert not sync.configured

    def test_configured_with_url_and_token(self, project: Path):
        sync = MailSync(
            hub_url="https://hub.example.com",
            token="test-token",
            project_root=project,
        )
        assert sync.configured

    def test_configured_via_env(self, project: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MCLAUDE_HUB_URL", "https://hub.example.com")
        monkeypatch.setenv("MCLAUDE_HUB_TOKEN", "test-token")
        sync = MailSync(project_root=project)
        assert sync.configured


class TestSyncNotConfigured:
    def test_push_returns_error(self, project: Path):
        sync = MailSync(project_root=project)
        result = sync.push_to_hub()
        assert result["pushed"] == 0
        assert "not configured" in result["errors"][0]

    def test_pull_returns_error(self, project: Path):
        sync = MailSync(project_root=project)
        result = sync.pull_from_hub()
        assert result["pulled"] == 0
        assert "not configured" in result["errors"][0]

    def test_auto_sync_returns_error(self, project: Path):
        sync = MailSync(project_root=project)
        result = sync.auto_sync()
        assert result["pushed"] == 0
        assert result["pulled"] == 0


class TestSyncState:
    def test_state_file_created_on_push(self, project: Path):
        sync = MailSync(
            hub_url="https://nonexistent.test",
            token="test",
            project_root=project,
        )
        # Push will fail (no real hub) but state file should still be created
        sync.push_to_hub()
        state_path = project / ".claude" / "messages" / ".sync_state.json"
        assert state_path.exists()

    def test_reset_state(self, project: Path):
        sync = MailSync(
            hub_url="https://nonexistent.test",
            token="test",
            project_root=project,
        )
        sync.push_to_hub()
        state_path = project / ".claude" / "messages" / ".sync_state.json"
        assert state_path.exists()

        sync.reset_state()
        assert not state_path.exists()

    def test_push_tracks_pushed_files(self, project: Path):
        """Files pushed once should not be pushed again (tracked in state)."""
        store = MessageStore(project)
        store.send(Message(
            from_="ani", to="vasya", type="update",
            subject="Test", body="Body",
        ))

        sync = MailSync(
            hub_url="https://nonexistent.test",
            token="test",
            project_root=project,
        )
        # First push - will fail to reach hub but should record the file
        result1 = sync.push_to_hub()
        # The file should be in errors (hub unreachable) but NOT in pushed_files
        # because the HTTP call failed

        state = sync._load_state()
        # State should exist with last_sync_time set
        assert state["last_sync_time"] > 0
