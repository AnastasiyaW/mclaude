"""
Handoff Writer and Reader - per-session structured handoffs.

Each Claude Code session writes its own handoff file with a unique name,
so parallel sessions cannot overwrite each other's work.

Filename format:

    YYYY-MM-DD_HH-MM_<session-id-first-8>_<slug>.md

Where:
    - timestamp is local time (the agent's wall clock)
    - session-id-first-8 is the first 8 chars of the session UUID,
      or a random 8-char hex string if no session ID is available
    - slug is kebab-case, 2-5 words, derived from the handoff's goal

Example: 2026-04-09_14-32_373d1618_drift-validator-axios.md

Files live in `.claude/handoffs/` relative to the project root. An index
file `.claude/handoffs/INDEX.md` lists all handoffs in reverse chronological
order with status (ACTIVE, RESUMED, CLOSED, ABANDONED) and one-line summary.

The INDEX.md is append-only: new entries are appended, status updates are
new rows (not in-place edits). This avoids race conditions and gives a
full history - you can see when a handoff was claimed, resumed, abandoned.

Usage from Python:

    from mclaude.handoffs import Handoff, HandoffStore

    store = HandoffStore(project_root="/path/to/project")

    # Write a new handoff
    h = Handoff(
        session_id="373d1618abcd",
        goal="Fix drift validator + axios RAT defense",
        done=["Updated Principle 09", "Activated min-release-age=7"],
        not_worked=["Tried v1.14.2 pin - wrong version"],
        working=["validator runs clean on 8 files"],
        broken=[],
        blocked=[],
        decisions=[("native installer over npm", "eliminates transitive deps")],
        next_step="push Principle 09 update to public repo",
    )
    store.write(h)  # creates the file + appends to INDEX.md

    # Read and choose
    recent = store.list_active()
    for h in recent:
        print(h.summary_line())

    latest = store.latest()
    content = store.read(latest.filename)

CLI entry points are in mclaude/cli.py (mclaude handoff write / mclaude handoff list).
"""
from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Slug rules (same as locks)
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")

# Characters stripped when auto-generating a slug from a goal sentence
_SLUG_STRIP = re.compile(r"[^\w\s-]+", re.UNICODE)
_SLUG_WHITESPACE = re.compile(r"[\s_]+")

# Common "filler" words we drop when building a slug
_SLUG_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "of", "for", "to", "in", "on",
    "at", "by", "with", "from", "up", "down", "is", "are", "was", "were",
    "be", "been", "being", "do", "does", "did", "have", "has", "had",
    "this", "that", "these", "those", "it", "its",
})


def slugify(text: str, max_words: int = 5) -> str:
    """Convert arbitrary text into a kebab-case slug of up to max_words words.

    Used when a caller does not supply an explicit slug. Strips punctuation,
    drops stopwords, lower-cases, joins with hyphens. Returns at minimum
    one word; if the text is empty, returns 'untitled-work'.
    """
    if not text:
        return "untitled-work"
    cleaned = _SLUG_STRIP.sub(" ", text.lower())
    cleaned = _SLUG_WHITESPACE.sub(" ", cleaned).strip()
    words = [w for w in cleaned.split() if w and w not in _SLUG_STOPWORDS]
    if not words:
        # all words were stopwords - fall back to first few raw words
        words = cleaned.split()[:max_words] or ["untitled", "work"]
    words = words[:max_words]
    slug = "-".join(words)
    slug = slug[:80]  # hard cap on length
    return slug or "untitled-work"


# -- Data model --------------------------------------------------------------

@dataclass
class Handoff:
    """A structured handoff record for a single session."""

    session_id: str
    goal: str
    done: list[str] = field(default_factory=list)
    not_worked: list[str] = field(default_factory=list)
    working: list[str] = field(default_factory=list)
    broken: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    decisions: list[tuple[str, str]] = field(default_factory=list)  # (what, why)
    next_step: str = ""
    background_tasks: list[str] = field(default_factory=list)
    working_directory: str | None = None
    slug_override: str | None = None  # if caller wants to name it manually
    timestamp: str | None = None  # ISO format, defaults to now
    status: str = "ACTIVE"  # ACTIVE | RESUMED | CLOSED | ABANDONED

    def session_short(self) -> str:
        """First 8 chars of session_id, or a random 8-char hex if ID is shorter."""
        if self.session_id and len(self.session_id) >= 8:
            return self.session_id[:8]
        return uuid.uuid4().hex[:8]

    def slug(self) -> str:
        if self.slug_override:
            return self.slug_override
        return slugify(self.goal)

    def filename(self) -> str:
        ts = self.timestamp or time.strftime("%Y-%m-%d_%H-%M")
        # Accept ISO format too - strip to YYYY-MM-DD_HH-MM
        iso_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})[T_ ](\d{2}):?(\d{2})", ts)
        if iso_match:
            ts = "{0}-{1}-{2}_{3}-{4}".format(*iso_match.groups())
        return f"{ts}_{self.session_short()}_{self.slug()}.md"

    def summary_line(self) -> str:
        """Short one-line summary for the INDEX.md."""
        ts = self.timestamp or time.strftime("%Y-%m-%d %H:%M")
        return f"{ts} | {self.session_short()} | {self.slug()} | {self.status}"

    def render_markdown(self) -> str:
        """Render the full handoff file body."""
        lines: list[str] = []
        lines.append(f"# Session Handoff - {self.timestamp or time.strftime('%Y-%m-%d %H:%M')}")
        lines.append("")
        lines.append(f"**Session ID:** {self.session_id}")
        lines.append(f"**Status:** {self.status}")
        if self.working_directory:
            lines.append(f"**Working directory:** {self.working_directory}")
        lines.append("")
        lines.append("## Goal")
        lines.append("")
        lines.append(self.goal or "(not specified)")
        lines.append("")
        lines.append("## Done")
        lines.append("")
        if self.done:
            for item in self.done:
                lines.append(f"- {item}")
        else:
            lines.append("(nothing recorded)")
        lines.append("")
        lines.append("## What did NOT work (and why)")
        lines.append("")
        if self.not_worked:
            for item in self.not_worked:
                lines.append(f"- {item}")
        else:
            lines.append("(no failed approaches recorded - consider whether this is honest)")
        lines.append("")
        lines.append("## Current state")
        lines.append("")
        lines.append("### Working")
        if self.working:
            for item in self.working:
                lines.append(f"- {item}")
        else:
            lines.append("- (nothing verified working)")
        lines.append("")
        lines.append("### Broken")
        if self.broken:
            for item in self.broken:
                lines.append(f"- {item}")
        else:
            lines.append("- (nothing broken)")
        lines.append("")
        lines.append("### Blocked")
        if self.blocked:
            for item in self.blocked:
                lines.append(f"- {item}")
        else:
            lines.append("- (no external blockers)")
        lines.append("")
        lines.append("## Key decisions")
        lines.append("")
        if self.decisions:
            for what, why in self.decisions:
                lines.append(f"- **{what}** because {why}")
        else:
            lines.append("(none)")
        lines.append("")
        if self.background_tasks:
            lines.append("## Background tasks")
            lines.append("")
            for task in self.background_tasks:
                lines.append(f"- {task}")
            lines.append("")
        lines.append("## Next step")
        lines.append("")
        lines.append(self.next_step or "(not specified)")
        lines.append("")
        return "\n".join(lines)


@dataclass
class IndexEntry:
    """A single row in INDEX.md."""

    timestamp: str
    session_short: str
    slug: str
    status: str
    summary: str = ""  # optional one-line summary
    filename: str = ""

    def render(self) -> str:
        return (
            f"- **{self.timestamp}** `{self.session_short}` `{self.slug}` "
            f"[{self.status}] {self.summary}".rstrip()
        )


# -- Store -------------------------------------------------------------------

class HandoffStore:
    """Reads and writes handoff files in a project's .claude/handoffs/ directory."""

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.handoffs_dir = self.project_root / ".claude" / "handoffs"
        self.index_path = self.handoffs_dir / "INDEX.md"
        self.archive_dir = self.handoffs_dir / "archive"

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".tmp.{uuid.uuid4().hex[:8]}")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)

    def write(self, handoff: Handoff) -> Path:
        """Write a new handoff and append to INDEX.md.

        If a file with the same name already exists (very unlikely due to
        timestamp + session_short uniqueness), a _2, _3, ... suffix is added.
        We NEVER overwrite an existing handoff file - that is the whole point.
        """
        self.handoffs_dir.mkdir(parents=True, exist_ok=True)
        base = handoff.filename()
        path = self.handoffs_dir / base
        counter = 2
        while path.exists():
            stem = base[:-3]  # strip .md
            path = self.handoffs_dir / f"{stem}_{counter}.md"
            counter += 1

        self._atomic_write(path, handoff.render_markdown())
        self._append_index(handoff, path.name)
        return path

    def _append_index(self, handoff: Handoff, filename: str) -> None:
        """Append one line to INDEX.md. Creates it with a header if missing.

        Append-only means: even status updates (RESUMED, CLOSED) are new rows,
        not edits to existing rows. This avoids concurrent-write races.
        """
        entry = IndexEntry(
            timestamp=handoff.timestamp or time.strftime("%Y-%m-%d %H:%M"),
            session_short=handoff.session_short(),
            slug=handoff.slug(),
            status=handoff.status,
            summary=(handoff.goal[:80] + "...") if len(handoff.goal) > 80 else handoff.goal,
            filename=filename,
        )
        line = entry.render()

        if not self.index_path.exists():
            header = [
                "# Handoff Index",
                "",
                "Chronological log of all session handoffs in this project.",
                "Newest at the bottom. Status transitions are appended as new rows,",
                "never edited in place - this avoids race conditions between parallel",
                "Claude Code sessions.",
                "",
                "Status values: ACTIVE | RESUMED | CLOSED | ABANDONED",
                "",
                "---",
                "",
            ]
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            self.index_path.write_text("\n".join(header), encoding="utf-8")

        with self.index_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def append_status(self, handoff_filename: str, new_status: str, note: str = "") -> None:
        """Record a status transition without editing existing rows."""
        # Parse timestamp + session + slug from filename
        m = re.match(r"^(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2})_([0-9a-fA-F]+)_(.+)\.md$", handoff_filename)
        if not m:
            raise ValueError(f"Unrecognized handoff filename: {handoff_filename}")
        date_part, time_part, session_short, slug = m.groups()
        ts = f"{date_part} {time_part.replace('-', ':')}"
        entry = IndexEntry(
            timestamp=time.strftime("%Y-%m-%d %H:%M"),
            session_short=session_short,
            slug=slug,
            status=new_status,
            summary=f"(status change, originally {ts}) {note}".strip(),
            filename=handoff_filename,
        )
        if not self.index_path.exists():
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            self.index_path.write_text("# Handoff Index\n\n", encoding="utf-8")
        with self.index_path.open("a", encoding="utf-8") as f:
            f.write(entry.render() + "\n")

    # -- Read API -----------------------------------------------------------

    def list_all(self) -> list[Path]:
        """Return all handoff files sorted newest-first by filename timestamp."""
        if not self.handoffs_dir.exists():
            return []
        files = [
            p for p in self.handoffs_dir.glob("*.md")
            if p.name != "INDEX.md"
        ]
        files.sort(key=lambda p: p.name, reverse=True)
        return files

    def latest(self) -> Path | None:
        files = self.list_all()
        return files[0] if files else None

    def find_by_slug(self, slug_fragment: str) -> list[Path]:
        """Find handoffs whose slug contains the fragment (case-insensitive)."""
        frag = slug_fragment.lower()
        return [p for p in self.list_all() if frag in p.name.lower()]

    def read(self, filename: str) -> str:
        path = self.handoffs_dir / filename
        return path.read_text(encoding="utf-8")

    def get_index_lines(self, status_filter: str | None = None) -> list[str]:
        """Return INDEX.md content lines, optionally filtered by status."""
        if not self.index_path.exists():
            return []
        lines = self.index_path.read_text(encoding="utf-8").splitlines()
        if not status_filter:
            return lines
        tag = f"[{status_filter.upper()}]"
        return [ln for ln in lines if tag in ln]
