#!/usr/bin/env python3
"""
mclaude_diagram — generate a Mermaid sequence diagram from a .claude/ state.

Takes the coordination artifacts (identities, locks, handoffs, messages) and
renders them as a sequence diagram that GitHub, Habr, and Medium all render
natively. Chronological order is mtime; actors are identities + any "session:"
frontmatter that appeared in handoffs or lock metadata.

Usage:
    python scripts/mclaude_diagram.py                    # current dir
    python scripts/mclaude_diagram.py /tmp/mclaude-demo  # a playground dir
    python scripts/mclaude_diagram.py --out diagram.md   # write file instead of stdout

Output: a Markdown fence block with a mermaid sequence diagram inside. Drop it
straight into any GitHub / Habr post and it renders as SVG.

Why Mermaid instead of graphviz / plantuml: zero install, native rendering on
the platforms we publish to.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Set


@dataclass
class Event:
    ts: float
    actor: str
    verb: str
    target: str
    detail: str = ""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _frontmatter(text: str) -> dict:
    """Return a dict for simple YAML frontmatter (no nested structures needed here)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    body = text[3:end]
    out: dict = {}
    current_key = None
    for line in body.splitlines():
        # Continuation list (- item)
        m = re.match(r"^\s+-\s+(.+)$", line)
        if m and current_key:
            out.setdefault(current_key, [])
            if isinstance(out[current_key], list):
                out[current_key].append(m.group(1).strip())
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            current_key = key
            if val == "":
                out[key] = []  # likely a list
            else:
                out[key] = val
    return out


_SESSION_PATTERNS = [
    re.compile(r"^\*\*Session ID:\*\*\s*(\S+)", re.MULTILINE),
    re.compile(r"^\*\*Session:\*\*\s*(\S+)", re.MULTILINE),
    re.compile(r"^session:\s*(\S+)", re.MULTILINE),  # frontmatter fallback
]


def _extract_session(text: str) -> str:
    for pat in _SESSION_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1)
    return "?"


def _handoff_events(claude: Path) -> List[Event]:
    out: List[Event] = []
    hd = claude / "handoffs"
    if not hd.is_dir():
        return out
    for md in sorted(hd.glob("*.md"), key=lambda p: p.stat().st_mtime):
        if md.name == "INDEX.md":
            continue
        name = md.name
        text = _read_text(md)
        # Rollup
        if "_rollup_" in name:
            fm = _frontmatter(text)
            author = fm.get("author", "?")
            covers = fm.get("covers", [])
            covers_str = f"rolls up {len(covers)} handoff(s)" if isinstance(covers, list) else "rolled up"
            out.append(
                Event(
                    ts=md.stat().st_mtime,
                    actor=author,
                    verb="ROLLUP",
                    target="handoffs",
                    detail=covers_str,
                )
            )
            continue
        # Regular handoff — prefer the identity stored INSIDE the file
        actor = _extract_session(text)
        # Fallback to filename hash if content did not carry the session
        if actor == "?":
            m = re.match(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_([^_]+)_(.+)\.md$", name)
            if m:
                actor = m.group(1)
        # Slug from filename (always reliable)
        m = re.match(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_[^_]+_(.+)\.md$", name)
        slug = m.group(1).replace("-", " ") if m else name
        out.append(
            Event(
                ts=md.stat().st_mtime,
                actor=actor,
                verb="handoff",
                target="*any-next*",
                detail=slug,
            )
        )
    return out


def _lock_events(claude: Path) -> List[Event]:
    out: List[Event] = []
    active = claude / "locks" / "active-work"
    if active.is_dir():
        for metadata_file in sorted(active.glob("*.metadata.json"), key=lambda p: p.stat().st_mtime):
            try:
                data = json.loads(metadata_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            actor = data.get("session") or data.get("identity") or "?"
            slug = metadata_file.name.rsplit(".metadata.json", 1)[0]
            out.append(
                Event(
                    ts=metadata_file.stat().st_mtime,
                    actor=actor,
                    verb="CLAIM",
                    target="locks",
                    detail=slug,
                )
            )
    completed = claude / "locks" / "completed"
    if completed.is_dir():
        for md in sorted(completed.glob("*.md"), key=lambda p: p.stat().st_mtime):
            # Filename: <slug>_YYYY-MM-DD_HH-MM.md
            text = _read_text(md)
            actor = _extract_session(text)
            slug = re.sub(r"_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\.md$", "", md.name)
            # Synthesize a CLAIM event from the archived "Claimed at:" timestamp
            # so the diagram shows the full lifecycle, not just the release.
            import datetime as _dt
            claimed_match = re.search(
                r"\*\*Claimed at:\*\*\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", text
            )
            if claimed_match:
                try:
                    claimed_dt = _dt.datetime.strptime(
                        claimed_match.group(1), "%Y-%m-%dT%H:%M:%S"
                    )
                    out.append(
                        Event(
                            ts=claimed_dt.timestamp(),
                            actor=actor,
                            verb="CLAIM",
                            target="locks",
                            detail=slug,
                        )
                    )
                except ValueError:
                    pass
            out.append(
                Event(
                    ts=md.stat().st_mtime,
                    actor=actor,
                    verb="RELEASE",
                    target="locks",
                    detail=slug,
                )
            )
    return out


def _message_events(claude: Path) -> List[Event]:
    out: List[Event] = []
    for root in [claude / "messages", claude / "mail"]:
        if not root.is_dir():
            continue
        for md in sorted(root.rglob("*.md"), key=lambda p: p.stat().st_mtime):
            fm = _frontmatter(_read_text(md))
            sender = fm.get("from", "?")
            recipient = fm.get("to", "?")
            kind = fm.get("type", "message")
            subject = fm.get("subject") or md.stem
            out.append(
                Event(
                    ts=md.stat().st_mtime,
                    actor=sender,
                    verb=kind,
                    target=recipient,
                    detail=subject[:60],
                )
            )
    return out


def _memory_events(claude: Path) -> List[Event]:
    out: List[Event] = []
    graph = claude / "memory-graph"
    if not graph.is_dir():
        return out
    for md in sorted(graph.rglob("*.md"), key=lambda p: p.stat().st_mtime):
        if md.name == "core.md":
            continue
        fm = _frontmatter(_read_text(md))
        session = fm.get("session", "?")
        title = fm.get("title") or md.stem
        # Wing / room from path
        parts = md.parts
        try:
            wings_idx = parts.index("wings")
            wing = parts[wings_idx + 1]
            room = parts[wings_idx + 3] if wings_idx + 3 < len(parts) else "?"
        except (ValueError, IndexError):
            wing, room = "?", "?"
        out.append(
            Event(
                ts=md.stat().st_mtime,
                actor=session,
                verb="memory",
                target=f"{wing}/{room}",
                detail=title[:60],
            )
        )
    return out


def _identities(claude: Path) -> Set[str]:
    reg = claude / "registry.json"
    if not reg.exists():
        return set()
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    names: Set[str] = set()
    if not isinstance(data, dict):
        return names
    # Schema v1: identities is a list of {name, owner, ...}. Earlier writers
    # used a dict keyed by name. Support both.
    for key in ("identities", "entries", "instances"):
        val = data.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict) and item.get("name"):
                    names.add(item["name"])
        elif isinstance(val, dict):
            names.update(val.keys())
    # Last-chance fallback: {name: {...}} at the top level
    if not names:
        for k, v in data.items():
            if isinstance(v, dict) and ("owner" in v or "roles" in v):
                names.add(k)
    return names


@dataclass
class Diagram:
    actors: List[str] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)


def collect(project: Path) -> Diagram:
    claude = project / ".claude"
    if not claude.is_dir():
        raise SystemExit(f"no .claude/ under {project}")
    all_events: List[Event] = []
    all_events += _lock_events(claude)
    all_events += _message_events(claude)
    all_events += _memory_events(claude)
    all_events += _handoff_events(claude)
    all_events.sort(key=lambda e: e.ts)
    actor_names = _identities(claude)
    # Add any actor mentioned in events that wasn't in the registry (anonymous sessions)
    for e in all_events:
        if e.actor and e.actor not in ("?", "rollup"):
            actor_names.add(e.actor)
        if e.target and e.target not in ("?", "*any-next*", "locks", "handoffs") and not e.target.startswith(("project-", "wings/")):
            # target is a recipient name for messages
            if e.verb in ("question", "answer", "request", "update", "error", "broadcast", "ack"):
                actor_names.add(e.target)
    # Stable order: identity registry order, then alphabetic
    actors = sorted(actor_names)
    return Diagram(actors=actors, events=all_events)


def _filter_actors(actors: List[str]) -> List[str]:
    """Drop hash-like pseudo-actors (8 hex chars) that slipped in from filenames."""
    return [a for a in actors if not re.fullmatch(r"[0-9a-f]{8}", a)]


def render_mermaid(d: Diagram) -> str:
    actors = _filter_actors(d.actors)
    if not actors:
        actors = ["unknown"]
    lines: List[str] = ["```mermaid", "sequenceDiagram", "    autonumber"]
    for name in actors:
        lines.append(f"    participant {name}")
    lines.append(f"    participant files as .claude/ files")
    for e in d.events:
        actor = e.actor if e.actor and e.actor != "?" else "unknown"
        # Use ASCII dashes to stay safe across every terminal codepage
        detail = (e.detail or "").replace("|", "\\|").replace("`", "")
        if e.verb == "CLAIM":
            lines.append(f"    {actor}->>files: CLAIM lock ({detail})")
        elif e.verb == "RELEASE":
            lines.append(f"    {actor}->>files: RELEASE lock ({detail})")
        elif e.verb == "handoff":
            lines.append(f"    {actor}->>files: write handoff ({detail})")
        elif e.verb == "ROLLUP":
            lines.append(f"    Note over files: ROLLUP -- {detail}")
        elif e.verb == "memory":
            lines.append(f"    {actor}->>files: save memory in {e.target} ({detail})")
        elif e.verb in ("question", "request"):
            lines.append(f"    {actor}->>{e.target}: {e.verb} -- {detail}")
        elif e.verb == "answer":
            lines.append(f"    {actor}-->>{e.target}: answer -- {detail}")
        elif e.verb in ("update", "broadcast", "error", "ack"):
            lines.append(f"    {actor}->>{e.target}: {e.verb} -- {detail}")
        else:
            lines.append(f"    {actor}->>files: {e.verb} ({detail})")
    lines.append("```")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate Mermaid sequence diagram from .claude/ state")
    ap.add_argument("project_dir", nargs="?", default=".")
    ap.add_argument("--out", help="Write to file (default: stdout)")
    args = ap.parse_args()

    d = collect(Path(args.project_dir).resolve())
    diagram = render_mermaid(d)

    if args.out:
        Path(args.out).write_text(diagram + "\n", encoding="utf-8")
        print(f"wrote {args.out} ({len(d.events)} events, {len(d.actors)} actors)", file=sys.stderr)
    else:
        print(diagram)
    return 0


if __name__ == "__main__":
    sys.exit(main())
