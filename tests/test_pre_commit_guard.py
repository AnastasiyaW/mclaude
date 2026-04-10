"""Tests for mclaude pre-commit guard."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GUARD_SCRIPT = Path(__file__).parent.parent / "hooks" / "pre_commit_guard.py"


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    """Create a git repo with .claude/ locks structure."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                    cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                    cwd=str(tmp_path), capture_output=True, check=True)

    # Create initial commit so we have a HEAD
    readme = tmp_path / "README.md"
    readme.write_text("# test", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True, check=True)

    # Create .claude/locks structure
    lock_dir = tmp_path / ".claude" / "locks" / "active-work"
    lock_dir.mkdir(parents=True)
    (tmp_path / ".claude" / "locks" / "completed").mkdir(parents=True)

    return tmp_path


def run_guard(cwd: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(GUARD_SCRIPT)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


class TestPreCommitGuard:
    def test_no_locks_allows_commit(self, git_project: Path):
        """No active locks -> commit allowed."""
        # Stage a file
        (git_project / "src.py").write_text("x = 1", encoding="utf-8")
        subprocess.run(["git", "add", "src.py"], cwd=str(git_project), capture_output=True)

        r = run_guard(git_project)
        assert r.returncode == 0

    def test_locked_file_blocks_commit(self, git_project: Path):
        """Committing a locked file should block."""
        # Create lock on src/main.py
        lock_dir = git_project / ".claude" / "locks" / "active-work"
        slug = "fix-main"
        (lock_dir / f"{slug}.lock").write_text("other-sess", encoding="utf-8")
        (lock_dir / f"{slug}.metadata.json").write_text(json.dumps({
            "slug": slug,
            "session_id": "other-session-xyz",
            "description": "Working on main",
            "files": ["src/main.py"],
        }), encoding="utf-8")

        # Stage src/main.py
        src_dir = git_project / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "main.py").write_text("print('hi')", encoding="utf-8")
        subprocess.run(["git", "add", "src/main.py"], cwd=str(git_project), capture_output=True)

        r = run_guard(git_project)
        assert r.returncode == 1
        assert "COMMIT BLOCKED" in r.stdout
        assert "fix-main" in r.stdout

    def test_own_lock_allows_commit(self, git_project: Path):
        """Committing a file locked by ourselves should be allowed."""
        lock_dir = git_project / ".claude" / "locks" / "active-work"
        slug = "fix-main"
        (lock_dir / f"{slug}.lock").write_text("ani-sess", encoding="utf-8")
        (lock_dir / f"{slug}.metadata.json").write_text(json.dumps({
            "slug": slug,
            "session_id": "ani-session-1234",
            "description": "My work",
            "files": ["src/main.py"],
        }), encoding="utf-8")

        src_dir = git_project / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "main.py").write_text("print('mine')", encoding="utf-8")
        subprocess.run(["git", "add", "src/main.py"], cwd=str(git_project), capture_output=True)

        r = run_guard(git_project, env_extra={"MCLAUDE_IDENTITY": "ani"})
        assert r.returncode == 0

    def test_unlocked_file_not_blocked(self, git_project: Path):
        """Committing a file NOT in any lock should pass."""
        lock_dir = git_project / ".claude" / "locks" / "active-work"
        slug = "fix-auth"
        (lock_dir / f"{slug}.lock").write_text("other", encoding="utf-8")
        (lock_dir / f"{slug}.metadata.json").write_text(json.dumps({
            "slug": slug,
            "session_id": "other-1234",
            "description": "Auth work",
            "files": ["src/auth.py"],
        }), encoding="utf-8")

        # Stage a DIFFERENT file
        (git_project / "config.py").write_text("c = True", encoding="utf-8")
        subprocess.run(["git", "add", "config.py"], cwd=str(git_project), capture_output=True)

        r = run_guard(git_project)
        assert r.returncode == 0
