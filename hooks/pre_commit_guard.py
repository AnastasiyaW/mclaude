#!/usr/bin/env python3
"""
mclaude pre-commit guard - blocks commits that touch locked files.

Git pre-commit hook that checks staged files against active mclaude locks.
If any staged file is listed in another session's lock metadata, the commit
is blocked with an explanation.

Install:
    mclaude hooks install-guard       # copies to .git/hooks/pre-commit
    # or manually:
    cp hooks/pre_commit_guard.py .git/hooks/pre-commit
    chmod +x .git/hooks/pre-commit

How it works:
    1. Reads `git diff --cached --name-only` to get staged files
    2. Scans .claude/locks/active-work/*.metadata.json for file claims
    3. If any staged file matches a lock held by a DIFFERENT session, exit 1
    4. Own locks (matched by MCLAUDE_IDENTITY) are allowed

Exit codes:
    0 - no conflicts, commit proceeds
    1 - conflicts found, commit blocked
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def get_staged_files() -> list[str]:
    """Get list of files staged for commit."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        return [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def get_project_root() -> Path | None:
    """Get git repo root."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def get_active_locks(root: Path) -> dict[str, dict]:
    """Return {normalized_file: lock_info} for all active lock claims."""
    locks_dir = root / ".claude" / "locks" / "active-work"
    if not locks_dir.exists():
        return {}

    result: dict[str, dict] = {}
    for meta_path in locks_dir.glob("*.metadata.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        slug = meta.get("slug", "?")
        session_id = meta.get("session_id", "?")
        description = meta.get("description", "?")
        files = meta.get("files", [])

        for f in files:
            # Store both the raw path and normalized version
            result[f] = {
                "slug": slug,
                "session_id": session_id,
                "description": description,
            }
            # Also try normalized
            try:
                normalized = str((root / f).resolve())
                result[normalized] = result[f]
            except (OSError, ValueError):
                pass

    return result


def main() -> int:
    root = get_project_root()
    if not root:
        return 0  # Not in a git repo, don't block

    staged = get_staged_files()
    if not staged:
        return 0

    locked_files = get_active_locks(root)
    if not locked_files:
        return 0

    my_identity = os.environ.get("MCLAUDE_IDENTITY", "")
    conflicts: list[tuple[str, dict]] = []

    for staged_file in staged:
        lock_info = locked_files.get(staged_file)

        # Also check by resolved path
        if not lock_info:
            try:
                resolved = str((root / staged_file).resolve())
                lock_info = locked_files.get(resolved)
            except (OSError, ValueError):
                pass

        if not lock_info:
            continue

        # Skip our own locks
        lock_session = lock_info.get("session_id", "")
        if my_identity and lock_session.startswith(my_identity):
            continue

        conflicts.append((staged_file, lock_info))

    if not conflicts:
        return 0

    # Block the commit
    print("[mclaude] COMMIT BLOCKED - staged files are locked by another session:")
    print()
    for file_path, info in conflicts:
        print(f"  {file_path}")
        print(f"    Lock: {info['slug']}")
        print(f"    Session: {info['session_id'][:8]}")
        print(f"    Description: {info['description']}")
        print()
    print("Options:")
    print("  1. Wait for the lock holder to finish and release")
    print("  2. Contact them: mclaude lock status <slug>")
    print("  3. Force if stale: mclaude lock force-release <slug> --reason '...'")
    print("  4. Skip guard: git commit --no-verify (use with caution)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
