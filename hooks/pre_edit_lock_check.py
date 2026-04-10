#!/usr/bin/env python3
"""
mclaude PreToolUse hook - checks if files being edited are locked by another session.

Runs before Edit/Write tool calls. Reads the tool input to extract the
file path, then checks if any active mclaude lock claims that file.

If the file IS locked by a different session, prints a warning and exits
with code 2 (Claude Code treats non-zero from PreToolUse hooks as a
signal to show the warning to the user, but does not block the edit).

To hard-block, change the exit code handling in your settings.json to
use the `decision` field.

Usage in settings.json:
  {
    "hooks": {
      "PreToolUse": [{
        "hook_command": "python hooks/pre_edit_lock_check.py",
        "if": "Edit(*)",
        "timeout": 3000
      }]
    }
  }

Input: receives JSON on stdin with tool_name and tool_input fields.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


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


def get_locked_files(root: Path) -> dict[str, dict]:
    """Return a map of {normalized_path: lock_metadata} for all active locks."""
    locks_dir = root / ".claude" / "locks" / "active-work"
    if not locks_dir.exists():
        return {}

    result: dict[str, dict] = {}

    for meta_path in locks_dir.glob("*.metadata.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        files = meta.get("files", [])
        slug = meta.get("slug", meta_path.stem.replace(".metadata", ""))
        session_id = meta.get("session_id", "?")
        description = meta.get("description", "?")

        for f in files:
            # Normalize the path for comparison
            try:
                normalized = str(Path(f).resolve())
            except (OSError, ValueError):
                normalized = f
            result[normalized] = {
                "slug": slug,
                "session_id": session_id,
                "description": description,
            }

    return result


def main() -> int:
    # Read tool input from stdin
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return 0  # Can't parse - don't block

    tool_input = data.get("tool_input", {})
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except json.JSONDecodeError:
            return 0

    file_path = tool_input.get("file_path") or tool_input.get("path", "")
    if not file_path:
        return 0

    root = find_project_root()
    if not root:
        return 0

    # Get my own session identity
    my_identity = os.environ.get("MCLAUDE_IDENTITY", "")

    locked_files = get_locked_files(root)
    if not locked_files:
        return 0

    # Normalize the target path
    try:
        target_normalized = str(Path(file_path).resolve())
    except (OSError, ValueError):
        target_normalized = file_path

    # Check exact match
    lock_info = locked_files.get(target_normalized)

    # Also check if target is a suffix of any locked path (relative paths)
    if not lock_info:
        target_name = Path(file_path).name
        for locked_path, info in locked_files.items():
            if locked_path.endswith(file_path) or Path(locked_path).name == target_name:
                lock_info = info
                break

    if not lock_info:
        return 0  # Not locked

    # Check if WE hold the lock (same identity = OK)
    lock_session = lock_info.get("session_id", "")
    if my_identity and lock_session.startswith(my_identity):
        return 0  # Our own lock

    # Another session holds the lock on this file
    slug = lock_info["slug"]
    session = lock_session[:8]
    desc = lock_info["description"]

    print(f"[mclaude] WARNING: {file_path} is locked by another session")
    print(f"  Lock: {slug}")
    print(f"  Session: {session}")
    print(f"  Description: {desc}")
    print(f"  Consider: `mclaude lock status {slug}` before editing")

    # Exit 0 = warning only, does not block
    # To block: output JSON {"decision": "block", "reason": "..."} and exit 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
