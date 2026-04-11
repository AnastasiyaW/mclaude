"""Tests for mclaude MCP server - tool handlers return correct structured data."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Import handlers directly to avoid stdio protocol overhead
from mclaude.mcp_server import (
    HANDLERS,
    TOOLS,
    _handle_lock_claim,
    _handle_lock_list,
    _handle_lock_release,
    _handle_lock_status,
    _handle_handoff_write,
    _handle_handoff_latest,
    _handle_memory_save,
    _handle_memory_search,
    _handle_message_send,
    _handle_message_inbox,
    _handle_identity_whoami,
    _handle_status,
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a minimal .claude/ project and chdir into it."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "locks" / "active-work").mkdir(parents=True)
    (claude_dir / "locks" / "completed").mkdir(parents=True)
    (claude_dir / "handoffs").mkdir()
    (claude_dir / "messages" / "inbox").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestToolDefinitions:
    def test_all_tools_have_handlers(self):
        for tool in TOOLS:
            assert tool["name"] in HANDLERS, f"Tool {tool['name']} has no handler"

    def test_all_handlers_have_tools(self):
        tool_names = {t["name"] for t in TOOLS}
        for handler_name in HANDLERS:
            assert handler_name in tool_names, f"Handler {handler_name} has no tool definition"

    def test_tools_have_required_fields(self):
        for tool in TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool


class TestLockTools:
    def test_claim_and_status(self, project: Path):
        result = _handle_lock_claim({"slug": "test-work", "description": "Testing"})
        assert result["success"] is True
        assert "session_id" in result

        status = _handle_lock_status({"slug": "test-work"})
        assert status["status"] == "ACTIVE"
        assert status["description"] == "Testing"

    def test_double_claim_returns_holder(self, project: Path):
        _handle_lock_claim({"slug": "busy-task", "description": "First"})
        result = _handle_lock_claim({"slug": "busy-task", "description": "Second"})
        assert result["success"] is False
        assert result["reason"] == "held"
        assert "holder_session" in result

    def test_claim_and_release(self, project: Path):
        claim = _handle_lock_claim({"slug": "temp-work", "description": "Temp"})
        session = claim["session_id"]

        release = _handle_lock_release({"slug": "temp-work", "session": session, "summary": "Done"})
        assert release["success"] is True

        status = _handle_lock_status({"slug": "temp-work"})
        assert status["status"] == "FREE"

    def test_list_empty(self, project: Path):
        result = _handle_lock_list({})
        assert result["count"] == 0
        assert result["locks"] == []

    def test_list_with_locks(self, project: Path):
        _handle_lock_claim({"slug": "task-one", "description": "First"})
        _handle_lock_claim({"slug": "task-two", "description": "Second"})
        result = _handle_lock_list({})
        assert result["count"] == 2


class TestHandoffTools:
    def test_write_and_latest(self, project: Path):
        result = _handle_handoff_write({
            "session": "abcd1234abcd1234",
            "goal": "Fix the auth bug",
            "done": ["Fixed middleware.py"],
            "not_worked": ["Tried mutex - deadlocked"],
        })
        assert result["success"] is True
        assert "auth" in result["filename"].lower() or "fix" in result["filename"].lower()

        latest = _handle_handoff_latest({})
        assert latest["found"] is True
        assert "Fix the auth bug" in latest["content"]

    def test_latest_empty(self, project: Path):
        result = _handle_handoff_latest({})
        assert result["found"] is False


class TestMemoryTools:
    def test_save_and_search(self, project: Path):
        save = _handle_memory_save({
            "wing": "myproject",
            "room": "auth",
            "hall": "decisions",
            "title": "Use JWT tokens",
            "content": "We decided to use JWT with 15-min expiry because stateless.",
        })
        assert save["success"] is True

        search = _handle_memory_search({"query": "JWT"})
        assert search["count"] > 0
        assert "JWT" in search["results"][0]["match"]


class TestMessageTools:
    def test_send_and_inbox(self, project: Path):
        send = _handle_message_send({
            "from_": "ani",
            "to": "vasya",
            "type": "question",
            "subject": "How to test?",
            "body": "What testing framework?",
        })
        assert send["success"] is True

        inbox = _handle_message_inbox({"recipient": "vasya"})
        assert inbox["count"] == 1
        assert inbox["messages"][0]["subject"] == "How to test?"
        assert inbox["messages"][0]["from"] == "ani"

    def test_inbox_empty(self, project: Path):
        inbox = _handle_message_inbox({"recipient": "nobody"})
        assert inbox["count"] == 0


class TestIdentityTool:
    def test_whoami_not_set(self, project: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MCLAUDE_IDENTITY", raising=False)
        result = _handle_identity_whoami({})
        assert result["found"] is False


class TestStatusTool:
    def test_empty_project(self, project: Path):
        result = _handle_status({})
        assert "project_root" in result
        assert result["locks"] == []
        assert result["handoffs"]["total"] == 0
        assert result["messages"]["total"] == 0

    def test_with_data(self, project: Path):
        _handle_lock_claim({"slug": "some-work", "description": "Working"})
        _handle_handoff_write({
            "session": "abcd1234abcd1234",
            "goal": "Test session",
        })
        _handle_message_send({
            "from_": "ani",
            "to": "*",
            "type": "broadcast",
            "subject": "Hello",
        })

        result = _handle_status({})
        assert len(result["locks"]) == 1
        assert result["handoffs"]["total"] == 1
        assert result["messages"]["total"] == 1
