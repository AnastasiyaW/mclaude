"""Tests for mclaude hooks (SessionStart, PreToolUse, Stop)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent / "hooks"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Create a minimal .claude/ project structure."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "locks" / "active-work").mkdir(parents=True)
    (claude_dir / "locks" / "completed").mkdir(parents=True)
    (claude_dir / "handoffs").mkdir()
    (claude_dir / "messages" / "inbox").mkdir(parents=True)
    return tmp_path


def run_hook(script_name: str, cwd: Path, env_extra: dict | None = None,
             stdin_data: str | None = None) -> subprocess.CompletedProcess:
    """Run a hook script in the given project directory."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    script = HOOKS_DIR / script_name
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        input=stdin_data,
        timeout=10,
    )
    return result


# -- SessionStart hook tests -------------------------------------------------

class TestSessionStartHook:
    def test_empty_project_silent(self, project: Path):
        """No handoffs, no locks, no messages -> no output."""
        r = run_hook("session_start.py", project)
        assert r.returncode == 0
        assert r.stdout.strip() == ""

    def test_shows_latest_handoff(self, project: Path):
        """If a recent handoff exists, it should appear in output."""
        handoff = project / ".claude" / "handoffs" / "2026-04-10_14-00_abcd1234_test-fix.md"
        handoff.write_text(
            "# Session Handoff - 2026-04-10 14:00\n\n"
            "**Session ID:** abcd1234\n\n"
            "## Goal\n\nFix the test\n\n"
            "## Done\n\n- Fixed test_foo.py\n",
            encoding="utf-8",
        )
        r = run_hook("session_start.py", project)
        assert r.returncode == 0
        assert "Recent handoff" in r.stdout
        assert "test-fix" in r.stdout
        assert "Fix the test" in r.stdout

    def test_shows_active_locks(self, project: Path):
        """Active locks should appear in output."""
        slug = "fix-auth-bug"
        lock_dir = project / ".claude" / "locks" / "active-work"
        (lock_dir / f"{slug}.lock").write_text("session123", encoding="utf-8")
        (lock_dir / f"{slug}.heartbeat").touch()
        (lock_dir / f"{slug}.metadata.json").write_text(json.dumps({
            "slug": slug,
            "session_id": "session123abc",
            "description": "Fixing auth middleware",
            "files": ["src/auth.py"],
        }), encoding="utf-8")

        r = run_hook("session_start.py", project)
        assert r.returncode == 0
        assert "Active locks" in r.stdout
        assert "fix-auth-bug" in r.stdout
        assert "Fixing auth middleware" in r.stdout

    def test_shows_unread_messages(self, project: Path):
        """Unread messages for current identity should appear."""
        msg = project / ".claude" / "messages" / "inbox" / "2026-04-10_14-00-00_vasya_ani_question_test.md"
        msg.write_text(
            "---\nfrom: vasya\nto: ani\ntype: question\n"
            "subject: How to test\nstatus: unread\nurgent: false\n---\n\nBody\n",
            encoding="utf-8",
        )
        r = run_hook("session_start.py", project, env_extra={"MCLAUDE_IDENTITY": "ani"})
        assert r.returncode == 0
        assert "Unread messages" in r.stdout
        assert "How to test" in r.stdout

    def test_no_messages_without_identity(self, project: Path):
        """Without MCLAUDE_IDENTITY, messages section is skipped."""
        msg = project / ".claude" / "messages" / "inbox" / "2026-04-10_14-00-00_vasya_ani_question_test.md"
        msg.write_text(
            "---\nfrom: vasya\nto: ani\ntype: question\nstatus: unread\nurgent: false\n---\n\nBody\n",
            encoding="utf-8",
        )
        r = run_hook("session_start.py", project, env_extra={"MCLAUDE_IDENTITY": ""})
        assert r.returncode == 0
        assert "Unread messages" not in r.stdout


# -- PreToolUse lock check tests ---------------------------------------------

class TestPreEditLockCheck:
    def test_no_locks_passes(self, project: Path):
        """No active locks -> clean pass."""
        tool_input = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/main.py"},
        })
        r = run_hook("pre_edit_lock_check.py", project, stdin_data=tool_input)
        assert r.returncode == 0
        assert "WARNING" not in r.stdout

    def test_locked_file_warns(self, project: Path):
        """Editing a file that another session locked should warn."""
        slug = "fix-main"
        lock_dir = project / ".claude" / "locks" / "active-work"
        (lock_dir / f"{slug}.lock").write_text("other-session", encoding="utf-8")
        (lock_dir / f"{slug}.metadata.json").write_text(json.dumps({
            "slug": slug,
            "session_id": "other-session-1234",
            "description": "Working on main.py",
            "files": ["src/main.py"],
        }), encoding="utf-8")

        tool_input = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/main.py"},
        })
        r = run_hook("pre_edit_lock_check.py", project, stdin_data=tool_input)
        assert r.returncode == 0
        assert "WARNING" in r.stdout
        assert "fix-main" in r.stdout

    def test_own_lock_no_warning(self, project: Path):
        """Editing a file that we ourselves locked should NOT warn."""
        slug = "fix-main"
        lock_dir = project / ".claude" / "locks" / "active-work"
        (lock_dir / f"{slug}.lock").write_text("ani-session", encoding="utf-8")
        (lock_dir / f"{slug}.metadata.json").write_text(json.dumps({
            "slug": slug,
            "session_id": "ani-session-abcd",
            "description": "Working on main",
            "files": ["src/main.py"],
        }), encoding="utf-8")

        tool_input = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/main.py"},
        })
        r = run_hook("pre_edit_lock_check.py", project,
                     stdin_data=tool_input,
                     env_extra={"MCLAUDE_IDENTITY": "ani"})
        assert r.returncode == 0
        assert "WARNING" not in r.stdout

    def test_empty_stdin_passes(self, project: Path):
        """Empty stdin should not crash."""
        r = run_hook("pre_edit_lock_check.py", project, stdin_data="")
        assert r.returncode == 0

    def test_bad_json_passes(self, project: Path):
        """Malformed JSON should not crash, just pass through."""
        r = run_hook("pre_edit_lock_check.py", project, stdin_data="not json{{{")
        assert r.returncode == 0


# -- Stop hook (remind handoff) tests ----------------------------------------

class TestRemindHandoff:
    def test_no_activity_silent(self, project: Path):
        """Fresh project with no activity -> no reminder."""
        r = run_hook("remind_handoff.py", project)
        assert r.returncode == 0
        assert r.stdout.strip() == ""

    def test_warns_about_active_locks(self, project: Path):
        """Active locks at session end should trigger a warning."""
        slug = "wip-feature"
        lock_dir = project / ".claude" / "locks" / "active-work"
        (lock_dir / f"{slug}.lock").write_text("my-session", encoding="utf-8")

        r = run_hook("remind_handoff.py", project)
        assert r.returncode == 0
        assert "ACTIVE LOCKS" in r.stdout
        assert "wip-feature" in r.stdout

    def test_recent_handoff_suppresses_reminder(self, project: Path):
        """If a handoff was just written, don't nag."""
        # Create a handoff file with current mtime
        handoff = project / ".claude" / "handoffs" / "2026-04-10_15-00_abcd_test.md"
        handoff.write_text("# test handoff", encoding="utf-8")

        # Create old activity to trigger "long session" heuristic
        old_file = project / ".claude" / "locks" / "active-work" / "old-activity.tmp"
        old_file.write_text("old", encoding="utf-8")
        # Set mtime to 20 minutes ago
        old_time = time.time() - 1200
        os.utime(str(old_file), (old_time, old_time))

        r = run_hook("remind_handoff.py", project)
        assert r.returncode == 0
        # Should NOT nag about handoff (recent one exists)
        assert "Consider writing a handoff" not in r.stdout
