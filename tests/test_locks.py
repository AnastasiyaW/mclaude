"""Tests for mclaude.locks - the 6 scenarios we need to work."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


LOCK_SCRIPT = Path(__file__).parent.parent / "mclaude" / "locks.py"


def run_lock(cwd: Path, *args: str) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(LOCK_SCRIPT), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode, (result.stdout + result.stderr)


def test_claim_success(tmp_path: Path) -> None:
    rc, out = run_lock(
        tmp_path, "claim", "--slug", "test-work", "--description", "Testing lock", "--files", "a.py",
    )
    assert rc == 0, out
    assert "claimed test-work" in out
    assert (tmp_path / ".claude" / "locks" / "active-work" / "test-work.lock").exists()
    assert (tmp_path / ".claude" / "locks" / "active-work" / "test-work.heartbeat").exists()
    assert (tmp_path / ".claude" / "locks" / "active-work" / "test-work.metadata.json").exists()


def test_double_claim_blocked(tmp_path: Path) -> None:
    rc1, _ = run_lock(
        tmp_path, "claim", "--slug", "test-work", "--description", "First", "--session", "first-sess",
    )
    assert rc1 == 0

    rc2, out = run_lock(
        tmp_path, "claim", "--slug", "test-work", "--description", "Second", "--session", "second-sess",
    )
    assert rc2 == 10, f"expected exit 10 (already held), got {rc2}: {out}"
    assert "already held" in out


def test_status_and_list(tmp_path: Path) -> None:
    run_lock(
        tmp_path, "claim", "--slug", "work-1", "--description", "First task",
    )
    rc, out = run_lock(tmp_path, "status", "work-1")
    assert rc == 0
    assert "ACTIVE" in out

    rc, out = run_lock(tmp_path, "list")
    assert rc == 0
    assert "work-1" in out


def test_release_with_archive(tmp_path: Path) -> None:
    rc, out = run_lock(
        tmp_path, "claim", "--slug", "work-1", "--description", "Task",
        "--session", "my-session",
    )
    assert rc == 0

    rc, out = run_lock(
        tmp_path, "release", "work-1", "--session", "my-session",
        "--summary", "Completed successfully",
    )
    assert rc == 0, out
    assert "released work-1" in out

    # Lock files should be gone
    assert not (tmp_path / ".claude" / "locks" / "active-work" / "work-1.lock").exists()

    # Archive should exist
    archive_dir = tmp_path / ".claude" / "locks" / "completed"
    archives = list(archive_dir.glob("work-1_*.md"))
    assert len(archives) == 1
    content = archives[0].read_text(encoding="utf-8")
    assert "Completed successfully" in content


def test_wrong_session_release_blocked(tmp_path: Path) -> None:
    run_lock(
        tmp_path, "claim", "--slug", "work-1", "--description", "Task", "--session", "owner",
    )
    rc, out = run_lock(
        tmp_path, "release", "work-1", "--session", "intruder",
    )
    assert rc == 13, f"expected exit 13 (wrong session), got {rc}: {out}"
    assert "held by different session" in out

    # Lock still exists
    assert (tmp_path / ".claude" / "locks" / "active-work" / "work-1.lock").exists()


def test_force_release_with_audit(tmp_path: Path) -> None:
    run_lock(
        tmp_path, "claim", "--slug", "work-1", "--description", "Stuck work",
    )
    rc, out = run_lock(
        tmp_path, "force-release", "work-1", "--reason", "session died",
    )
    assert rc == 0, out
    assert "FORCE-RELEASED" in out

    # Archive should contain FORCE and reason
    archive_dir = tmp_path / ".claude" / "locks" / "completed"
    archives = list(archive_dir.glob("work-1_*_FORCE.md"))
    assert len(archives) == 1
    content = archives[0].read_text(encoding="utf-8")
    assert "session died" in content
    assert "FORCE RELEASE" in content


def test_slug_validation(tmp_path: Path) -> None:
    # Too short
    rc, _ = run_lock(tmp_path, "claim", "--slug", "ab", "--description", "X")
    assert rc != 0

    # Uppercase
    rc, _ = run_lock(tmp_path, "claim", "--slug", "Bad-Slug", "--description", "X")
    assert rc != 0

    # Starts with hyphen
    rc, _ = run_lock(tmp_path, "claim", "--slug", "-leading", "--description", "X")
    assert rc != 0

    # Valid
    rc, _ = run_lock(tmp_path, "claim", "--slug", "good-slug-123", "--description", "X")
    assert rc == 0
