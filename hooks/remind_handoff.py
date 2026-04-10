#!/usr/bin/env python3
"""
mclaude Stop hook - reminds to write a handoff before session ends.

Checks if the session has been active for a while (based on .claude/
modification times) and whether a recent handoff already exists.
If no recent handoff found, prints a reminder.

Usage in settings.json:
  {
    "hooks": {
      "Stop": [{
        "hook_command": "python hooks/remind_handoff.py",
        "timeout": 3000
      }]
    }
  }
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Don't nag if the last handoff was written less than 30 minutes ago
RECENT_THRESHOLD_SECONDS = 1800

# Don't nag for very short sessions - only if .claude/ was modified > 10 min ago
MIN_SESSION_AGE_SECONDS = 600


def find_project_root() -> Path | None:
    p = Path.cwd()
    for _ in range(10):
        if (p / ".claude").is_dir():
            return p
        parent = p.parent
        if parent == p:
            break
        p = parent
    return None


def has_recent_handoff(root: Path) -> bool:
    """Check if a handoff was written recently."""
    handoffs_dir = root / ".claude" / "handoffs"
    if not handoffs_dir.exists():
        return False

    md_files = sorted(
        (p for p in handoffs_dir.glob("*.md") if p.name != "INDEX.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not md_files:
        return False

    age = time.time() - md_files[0].stat().st_mtime
    return age < RECENT_THRESHOLD_SECONDS


def session_seems_long(root: Path) -> bool:
    """Heuristic: if .claude/ has files modified > MIN_SESSION_AGE_SECONDS ago,
    this was probably a non-trivial session."""
    claude_dir = root / ".claude"
    try:
        # Check if any lock/handoff/message activity happened
        for subdir in ["locks/active-work", "handoffs", "messages/inbox"]:
            d = claude_dir / subdir
            if not d.exists():
                continue
            for f in d.iterdir():
                if f.is_file():
                    age = time.time() - f.stat().st_mtime
                    if age > MIN_SESSION_AGE_SECONDS:
                        return True
    except OSError:
        pass
    return False


def has_active_locks(root: Path) -> bool:
    """Check if current session holds any locks (should release before exit)."""
    locks_dir = root / ".claude" / "locks" / "active-work"
    if not locks_dir.exists():
        return False
    return any(locks_dir.glob("*.lock"))


def main() -> int:
    root = find_project_root()
    if not root:
        return 0

    messages: list[str] = []

    # Check for unreleased locks
    if has_active_locks(root):
        messages.append("[mclaude] You have ACTIVE LOCKS that should be released before closing:")
        locks_dir = root / ".claude" / "locks" / "active-work"
        for lock in sorted(locks_dir.glob("*.lock")):
            messages.append(f"  - {lock.stem}")
        messages.append("  Run: mclaude lock release <slug> --summary '...'")
        messages.append("")

    # Check for handoff need
    if session_seems_long(root) and not has_recent_handoff(root):
        messages.append("[mclaude] Consider writing a handoff before closing this session:")
        messages.append("  mclaude handoff write --session <id> --goal '...'")
        messages.append("  Or tell Claude: 'prepare handoff'")

    if messages:
        print("\n".join(messages))

    return 0


if __name__ == "__main__":
    sys.exit(main())
