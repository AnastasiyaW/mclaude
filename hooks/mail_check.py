#!/usr/bin/env python3
"""
mclaude UserPromptSubmit hook - checks for new messages on every user prompt.

Runs before each user message is processed by Claude. If there are new
unread messages for the current identity, prints them to stdout so Claude
Code injects them into the agent's context.

Only shows messages that haven't been shown before (deduplication via
.watcher_state.json). Silent exit if no new messages (does not clutter
context with empty checks).

If MCLAUDE_HUB_URL is set, also syncs with hub before checking.

Usage in settings.json:
  {
    "hooks": {
      "UserPromptSubmit": [{
        "hook_command": "python .claude/hooks/mail_check.py",
        "timeout": 3000
      }]
    }
  }
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


def load_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"seen_files": [], "last_check": 0}


def save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    root = find_project_root()
    if not root:
        return 0

    identity = os.environ.get("MCLAUDE_IDENTITY", "")
    if not identity:
        return 0  # No identity = can't check inbox

    inbox_dir = root / ".claude" / "messages" / "inbox"
    if not inbox_dir.exists():
        return 0

    state_path = root / ".claude" / "messages" / ".watcher_state.json"
    state = load_state(state_path)
    seen = set(state.get("seen_files", []))

    # Optional: sync with hub first
    hub_url = os.environ.get("MCLAUDE_HUB_URL", "")
    if hub_url:
        try:
            # Import only if hub is configured (avoids import overhead otherwise)
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from mclaude.mail_sync import MailSync
            sync = MailSync(project_root=root)
            sync.auto_sync()
        except Exception:
            pass  # Sync failure should not block mail check

    # Scan for new messages
    new_messages: list[tuple[str, str, str, str, bool]] = []  # (from, type, subject, body_preview, urgent)

    for path in sorted(inbox_dir.glob("*.md")):
        if path.name in seen:
            continue
        if path.name.startswith("."):
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue

        if not text.startswith("---"):
            continue

        # Quick frontmatter parse
        meta: dict[str, str] = {}
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        for line in parts[1].strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()

        to = meta.get("to", "")
        status = meta.get("status", "")
        if to != identity and to != "*":
            continue
        if status != "unread":
            continue

        from_ = meta.get("from", "?")
        type_ = meta.get("type", "update")
        subject = meta.get("subject", "(no subject)")
        urgent = meta.get("urgent", "false").lower() == "true"

        # Body preview (first non-empty line after frontmatter, max 100 chars)
        body_text = parts[2].strip()
        body_lines = [l for l in body_text.splitlines() if l.strip() and not l.startswith("#")]
        preview = body_lines[0][:100] if body_lines else ""

        new_messages.append((from_, type_, subject, preview, urgent))
        seen.add(path.name)

    if not new_messages:
        return 0  # Silent - don't clutter context

    # Format output
    lines = [f"[mclaude mail] {len(new_messages)} new message(s) for {identity}:"]
    for from_, type_, subject, preview, urgent in new_messages:
        marker = "URGENT " if urgent else ""
        lines.append(f"  {marker}[{type_}] from {from_}: {subject}")
        if preview:
            lines.append(f"    > {preview}")
    lines.append("")
    lines.append("Reply: mclaude mail reply <message> --body '...'")
    lines.append("Details: mclaude mail check")

    # Save state
    state["seen_files"] = list(seen)
    state["last_check"] = time.time()
    save_state(state_path, state)

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
