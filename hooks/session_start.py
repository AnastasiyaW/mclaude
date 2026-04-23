#!/usr/bin/env python3
"""
mclaude SessionStart hook for Claude Code.

Runs at session start. Prints context that Claude Code injects into the
agent's system prompt:
  - Latest handoff summary (if any recent ones exist)
  - Unread messages for current identity
  - Active locks overview

Output goes to stdout - Claude Code captures it as hook context.

Usage in settings.json:
  {
    "hooks": {
      "SessionStart": [{
        "hook_command": "python hooks/session_start.py",
        "timeout": 5000
      }]
    }
  }
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def find_project_root() -> Path | None:
    """Walk up from cwd looking for .claude/ directory."""
    p = Path.cwd()
    for _ in range(10):
        if (p / ".claude").is_dir():
            return p
        parent = p.parent
        if parent == p:
            break
        p = parent
    return None


def section_handoffs(root: Path) -> list[str]:
    """Show latest handoff if it exists and is < 48 hours old."""
    handoffs_dir = root / ".claude" / "handoffs"
    if not handoffs_dir.exists():
        return []

    md_files = sorted(
        (p for p in handoffs_dir.glob("*.md") if p.name != "INDEX.md"),
        key=lambda p: p.name,
        reverse=True,
    )
    if not md_files:
        return []

    latest = md_files[0]
    # Check age - skip if older than 48 hours
    try:
        age_hours = (time.time() - latest.stat().st_mtime) / 3600
        if age_hours > 48:
            return []
    except OSError:
        return []

    lines = ["## Recent handoff"]
    lines.append(f"File: `{latest.name}`")
    lines.append("")

    # Read first ~40 lines (goal + done + not-worked sections)
    content = latest.read_text(encoding="utf-8").splitlines()
    for line in content[:40]:
        lines.append(line)

    if len(content) > 40:
        lines.append("... (truncated, run `mclaude handoff latest` for full content)")

    return lines


def section_messages(root: Path) -> list[str]:
    """Show unread messages for current identity."""
    identity = os.environ.get("MCLAUDE_IDENTITY", "")
    if not identity:
        return []

    inbox_dir = root / ".claude" / "messages" / "inbox"
    if not inbox_dir.exists():
        return []

    unread: list[tuple[str, str, str, bool]] = []  # (from, type, subject, urgent)

    for path in sorted(inbox_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue

        # Quick frontmatter parse - no yaml dependency
        if not text.startswith("---"):
            continue

        meta: dict[str, str] = {}
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        for line in parts[1].strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()

        # Check if addressed to us and unread
        to = meta.get("to", "")
        status = meta.get("status", "")
        if to != identity and to != "*":
            continue
        if status != "unread":
            continue

        from_ = meta.get("from", "?")
        type_ = meta.get("type", "?")
        subject = meta.get("subject", "(no subject)")
        urgent = meta.get("urgent", "false").lower() == "true"
        unread.append((from_, type_, subject, urgent))

    if not unread:
        return []

    lines = [f"## Unread messages for {identity}"]
    for from_, type_, subject, urgent in unread:
        marker = "URGENT " if urgent else ""
        lines.append(f"- {marker}[{type_}] from {from_}: {subject}")

    lines.append("")
    lines.append("Run `mclaude message inbox " + identity + "` for details.")
    return lines


def section_realtime_hint(root: Path) -> list[str]:
    """
    Suggest starting the real-time inbox Monitor if not already running.

    We cannot detect running Monitor tasks from here (hook runs in sibling
    process, no agent state access). So emit an idempotent hint ONLY if:
      - Identity is set (implies real collaboration setup)
      - inbox_monitor.sh script exists in the project or via mclaude install
      - Not already started flag file present

    Once the agent starts the Monitor, it writes a flag file so this
    suggestion does not re-appear in later sessions.
    """
    identity = os.environ.get("MCLAUDE_IDENTITY", "")
    if not identity:
        return []

    # Find the script relative to common install locations
    script_candidates = [
        root / "scripts" / "mclaude_inbox_monitor.sh",
        root / ".claude" / "scripts" / "mclaude_inbox_monitor.sh",
    ]
    script_path = next((p for p in script_candidates if p.exists()), None)
    if script_path is None:
        return []  # Script not installed — silent

    # Once-per-project suppression after agent confirms it started the monitor
    started_flag = root / ".claude" / "messages" / ".monitor_started"
    if started_flag.exists():
        return []

    lines = [
        "## Real-time inbox (Monitor tool recommended)",
        "",
        f"The Monitor polling script is available at `{script_path.relative_to(root)}`.",
        "Starting it keeps you notified of new teammate letters mid-conversation",
        "(without waiting for the next user prompt). Recommended for long-running",
        "sessions (>30 min) with active collaborators.",
        "",
        "Start once per session:",
        "",
        "```python",
        "Monitor(",
        f'    command="bash {script_path.relative_to(root).as_posix()}",',
        f'    description="mclaude inbox for {identity}",',
        "    persistent=True,",
        ")",
        "```",
        "",
        "After starting, create an empty flag file so this hint stops appearing:",
        "",
        f"    touch {started_flag.relative_to(root).as_posix()}",
        "",
    ]
    return lines


def section_locks(root: Path) -> list[str]:
    """Show active locks overview."""
    locks_dir = root / ".claude" / "locks" / "active-work"
    if not locks_dir.exists():
        return []

    lock_files = sorted(locks_dir.glob("*.lock"))
    if not lock_files:
        return []

    lines = ["## Active locks"]

    for lock in lock_files:
        slug = lock.stem
        meta_path = locks_dir / f"{slug}.metadata.json"
        hb_path = locks_dir / f"{slug}.heartbeat"

        description = "?"
        session = "?"
        stale = False

        if meta_path.exists():
            try:
                import json
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                description = meta.get("description", "?")
                session = meta.get("session_id", "?")[:8]
            except (OSError, Exception):
                pass

        if hb_path.exists():
            try:
                age = time.time() - hb_path.stat().st_mtime
                stale = age > 180
            except OSError:
                pass

        tag = "STALE" if stale else "ACTIVE"
        lines.append(f"- [{tag}] `{slug}` by {session}: {description}")

    return lines


def main() -> int:
    root = find_project_root()
    if not root:
        return 0  # Not in a project with .claude/ - silent exit

    output: list[str] = ["# mclaude context", ""]

    handoffs = section_handoffs(root)
    messages = section_messages(root)
    locks = section_locks(root)

    if not handoffs and not messages and not locks:
        return 0  # Nothing to report - don't clutter context

    if locks:
        output.extend(locks)
        output.append("")
    if messages:
        output.extend(messages)
        output.append("")
    if handoffs:
        output.extend(handoffs)
        output.append("")

    print("\n".join(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
