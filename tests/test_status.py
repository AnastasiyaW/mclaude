"""Tests for mclaude status command."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
from io import StringIO

import pytest

# We test _dispatch_status directly rather than via subprocess
# because subprocess changes cwd and mclaude may not be on the module path.
sys.path.insert(0, str(Path(__file__).parent.parent))
from mclaude.cli import _dispatch_status


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a project with .claude/ and chdir into it."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run_status(project: Path, env_extra: dict | None = None) -> str:
    """Run _dispatch_status capturing stdout."""
    import argparse
    args = argparse.Namespace()
    old_cwd = os.getcwd()
    try:
        os.chdir(project)
        if env_extra:
            for k, v in env_extra.items():
                os.environ[k] = v
        buf = StringIO()
        with patch("sys.stdout", buf):
            _dispatch_status(args)
        return buf.getvalue()
    finally:
        os.chdir(old_cwd)
        if env_extra:
            for k in env_extra:
                os.environ.pop(k, None)


class TestStatusCommand:
    def test_empty_project(self, project: Path):
        out = run_status(project)
        assert "Locks: none" in out
        assert "Handoffs: none" in out
        assert "Messages: none" in out

    def test_with_locks(self, project: Path):
        lock_dir = project / ".claude" / "locks" / "active-work"
        lock_dir.mkdir(parents=True)
        (lock_dir / "test-slug.lock").write_text("sess", encoding="utf-8")
        (lock_dir / "test-slug.heartbeat").touch()
        (lock_dir / "test-slug.metadata.json").write_text(json.dumps({
            "slug": "test-slug", "session_id": "abcd1234", "description": "Testing"
        }), encoding="utf-8")

        out = run_status(project)
        assert "1 active" in out
        assert "test-slug" in out

    def test_with_handoffs(self, project: Path):
        ho_dir = project / ".claude" / "handoffs"
        ho_dir.mkdir()
        (ho_dir / "2026-04-10_14-00_abcd_test.md").write_text("# test", encoding="utf-8")

        out = run_status(project)
        assert "1 total" in out

    def test_shows_identity(self, project: Path):
        out = run_status(project, env_extra={"MCLAUDE_IDENTITY": "ani"})
        assert "ani" in out

    def test_with_registry(self, project: Path):
        reg = project / ".claude" / "registry.json"
        reg.write_text(json.dumps({
            "schema_version": 1,
            "identities": {
                "ani": {"name": "ani", "id": "c0d3-ani-1234", "owner": "Test"},
                "vasya": {"name": "vasya", "id": "c0d3-vasya-5678", "owner": "Test2"},
            }
        }), encoding="utf-8")

        out = run_status(project)
        assert "ani" in out
        assert "vasya" in out
