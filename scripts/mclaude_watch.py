#!/usr/bin/env python3
"""
mclaude watcher — prints .claude/ file changes as human-readable events.

Use it alongside playground.sh in another terminal to see coordination happening
live. No external dependencies — stdlib only. Poll-based (500ms) on purpose:
the point is to be runnable on any machine with any Python >= 3.9, not to use
inotify which is Linux-only.

Usage:
    python scripts/mclaude_watch.py               # watch ./.claude/
    python scripts/mclaude_watch.py /tmp/demo     # watch a specific playground dir
    python scripts/mclaude_watch.py --no-color    # plain output for piping
    python scripts/mclaude_watch.py --once        # one snapshot, exit

What it shows:
    [HH:MM:SS] LAYER  ACTOR  EVENT  (details)

Example:
    [14:32:17] locks   ani    claimed refactor-auth-middleware
    [14:32:19] mail    vasya  asked ani "Is the race in write path?"
    [14:32:28] memory  ani    saved decision: "Race is in write path"
    [14:32:31] handoff ani    wrote refactor-auth-middleware (next: vasya adds tests)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, Tuple

POLL_INTERVAL_SEC = 0.5


def colored(text: str, color: str, enabled: bool = True) -> str:
    if not enabled:
        return text
    codes = {
        "red": "\033[31m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "blue": "\033[34m",
        "magenta": "\033[35m",
        "cyan": "\033[36m",
        "grey": "\033[90m",
        "bold": "\033[1m",
        "dim": "\033[2m",
        "reset": "\033[0m",
    }
    return f"{codes.get(color, '')}{text}{codes['reset']}"


LAYER_COLORS = {
    "locks": "cyan",
    "handoff": "yellow",
    "memory": "magenta",
    "registry": "blue",
    "mail": "green",
    "messages": "green",
    "index": "grey",
}


def classify(path: Path) -> Tuple[str, str]:
    """Return (layer, action_hint) for a path under .claude/."""
    parts = path.parts
    try:
        idx = parts.index(".claude")
    except ValueError:
        return ("unknown", "touched")
    suffix = parts[idx + 1 :]
    if not suffix:
        return ("unknown", "touched")
    first = suffix[0]
    if first == "locks":
        if len(suffix) >= 3 and suffix[1] == "active-work":
            if path.name.endswith(".lock"):
                return ("locks", "claimed")
            if path.name.endswith(".heartbeat"):
                return ("locks", "heartbeat")
            if path.name.endswith(".metadata.json"):
                return ("locks", "metadata")
        if len(suffix) >= 2 and suffix[1] == "completed":
            return ("locks", "released")
        return ("locks", "touched")
    if first == "handoffs":
        if path.name == "INDEX.md":
            return ("handoff", "indexed")
        if "rollup" in path.name:
            return ("handoff", "rolled-up")
        return ("handoff", "wrote")
    if first == "memory-graph":
        if path.name == "core.md":
            return ("memory", "core-updated")
        if "superseded" in path.name:
            return ("memory", "superseded")
        return ("memory", "saved")
    if first == "registry.json":
        return ("registry", "updated")
    if first == "messages" or first == "mail":
        return ("mail", "message")
    if first in ("code-map.md", "llms.txt"):
        return ("index", "re-indexed")
    return ("unknown", "touched")


def extract_actor_and_subject(path: Path) -> Tuple[str, str]:
    """Try to pull 'who did this' and 'what is it about' from file name / content."""
    name = path.name
    actor = "?"
    subject = ""
    # Handoff format: YYYY-MM-DD_HH-MM_<session-id-first-8>_<slug>.md
    m = re.match(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_([^_]+)_(.+)\.md$", name)
    if m:
        actor = m.group(1)
        subject = m.group(2).replace("-", " ")
        return actor, subject
    # Rollup format: YYYY-MM-DD_HH-MM_rollup_<slug>.md
    m = re.match(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_rollup_(.+)\.md$", name)
    if m:
        return ("rollup", m.group(1).replace("-", " "))
    # Lock files: <slug>.lock / .heartbeat / .metadata.json
    if name.endswith((".lock", ".heartbeat", ".metadata.json")):
        slug = name.rsplit(".", 2)[0] if name.endswith(".metadata.json") else name.rsplit(".", 1)[0]
        # Read metadata to find session
        metadata = path.parent / f"{slug}.metadata.json"
        if metadata.exists():
            try:
                data = json.loads(metadata.read_text(encoding="utf-8"))
                actor = data.get("session") or data.get("identity") or "?"
                subject = slug.replace("-", " ")
                return actor, subject
            except (json.JSONDecodeError, OSError):
                pass
        return ("?", slug.replace("-", " "))
    # Message files — try to read frontmatter
    if path.suffix == ".md" and ("messages" in path.parts or "mail" in path.parts):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^from:\s*(\S+)", text, re.MULTILINE)
            if m:
                actor = m.group(1)
            m = re.search(r"^subject:\s*(.+)", text, re.MULTILINE)
            if m:
                subject = m.group(1).strip()[:60]
            return actor, subject
        except OSError:
            pass
    # Memory drawer: the path tells us wing/room/hall, the filename the title
    if "memory-graph" in path.parts:
        try:
            wing_idx = path.parts.index("wings")
            parts = path.parts[wing_idx + 1 :]
            if len(parts) >= 4:
                wing, _rooms, room, _halls = parts[:4]  # wings/<w>/rooms/<r>/halls/...
                subject = f"{wing}/{room}: {name.rsplit('.', 1)[0]}"
        except (ValueError, IndexError):
            subject = name.rsplit(".", 1)[0]
        return ("?", subject)
    return ("?", name)


def event_line(ts: float, layer: str, action: str, actor: str, subject: str, color_on: bool) -> str:
    ts_str = time.strftime("%H:%M:%S", time.localtime(ts))
    color = LAYER_COLORS.get(layer, "grey")
    return (
        f"[{ts_str}] "
        + colored(f"{layer:8s}", color, color_on)
        + " "
        + colored(f"{actor:8s}", "bold", color_on)
        + f" {action:12s} "
        + colored(subject, "dim", color_on)
    )


def snapshot(root: Path) -> Dict[Path, float]:
    """Return a map of every file under root to its mtime."""
    out: Dict[Path, float] = {}
    if not root.exists():
        return out
    for p in root.rglob("*"):
        if p.is_file():
            try:
                out[p] = p.stat().st_mtime
            except OSError:
                pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Watch .claude/ for coordination events")
    ap.add_argument("project_dir", nargs="?", default=".", help="Project root containing .claude/")
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    ap.add_argument("--once", action="store_true", help="Print a snapshot and exit")
    args = ap.parse_args()

    color_on = (not args.no_color) and sys.stdout.isatty()
    project = Path(args.project_dir).resolve()
    claude_dir = project / ".claude"

    print(colored(f"watching {claude_dir}", "dim", color_on))
    print(colored("(Ctrl+C to stop)", "dim", color_on))

    known = snapshot(claude_dir)
    if args.once:
        for path, ts in sorted(known.items(), key=lambda kv: kv[1]):
            layer, action = classify(path)
            actor, subject = extract_actor_and_subject(path)
            print(event_line(ts, layer, action, actor, subject, color_on))
        return 0

    try:
        while True:
            time.sleep(POLL_INTERVAL_SEC)
            current = snapshot(claude_dir)
            # New or changed files
            for path, mtime in current.items():
                if path not in known or known[path] != mtime:
                    layer, action = classify(path)
                    actor, subject = extract_actor_and_subject(path)
                    # Suppress heartbeat noise unless actor changed
                    if layer == "locks" and action == "heartbeat" and path in known:
                        continue
                    print(event_line(mtime, layer, action, actor, subject, color_on), flush=True)
            # Deleted files (lock release writes .completed and removes .lock)
            for path in known.keys() - current.keys():
                layer, _action = classify(path)
                actor, subject = extract_actor_and_subject(path)
                if layer == "locks" and path.name.endswith(".lock"):
                    print(
                        event_line(time.time(), "locks", "released", actor, subject, color_on),
                        flush=True,
                    )
            known = current
    except KeyboardInterrupt:
        print(colored("\nwatcher stopped.", "dim", color_on))
        return 0


if __name__ == "__main__":
    sys.exit(main())
