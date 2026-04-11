"""
Memory Graph - hierarchical raw verbatim knowledge storage.

Inspired by MemPalace (Milla Jovovich + Ben Sigman), which demonstrated that
**raw verbatim text in a vector store beats LLM extraction/summarization** on
LongMemEval benchmarks (96.6% R@5 with default embeddings, no LLM calls).

We take the structural insight (hierarchical knowledge graph) and the content
insight (never lose verbatim text) but drop the dependency on ChromaDB, SQLite,
or any external vector store. Instead we use plain markdown files in a nested
directory structure, searchable via ripgrep by default, with an optional vector
layer that can be added later if needed.

Hierarchy:

    .claude/memory-graph/
    └── wings/
        ├── project-myapp/          <- Wing = project or major topic
        │   ├── rooms/              <- Room = sub-topic within a wing
        │   │   ├── auth-system/
        │   │   │   ├── decisions/  <- Hall = type of content
        │   │   │   │   └── 2026-04-09_jwt-over-sessions.md
        │   │   │   ├── gotchas/
        │   │   │   │   └── 2026-04-08_jwt-expiry-race.md
        │   │   │   └── references/
        │   │   │       └── 2026-04-07_rfc7519-notes.md
        │   │   └── api-design/
        │   │       └── ...
        │   └── tunnels.md          <- cross-room links within a wing
        ├── project-other/
        └── common/                 <- shared across projects

Each file is a "Drawer" in MemPalace parlance - the lowest level, containing
one piece of raw verbatim knowledge with frontmatter metadata.

Drawer file format:

    ---
    title: Use JWT instead of server sessions
    created: 2026-04-09T14:32:00
    session: 373d1618
    hall: decisions
    tags: [auth, jwt, architecture]
    valid_from: 2026-04-09T14:32:00
    valid_to: null        # null = still valid
    superseded_by: null   # fill in if a newer decision replaces this one
    ---

    # Use JWT instead of server sessions

    Context: building an auth system for myapp. Need to decide how to
    represent authenticated user identity across requests.

    Decision: JWT with 15-minute access tokens + 30-day refresh tokens.

    Reasoning:
    - Stateless - no server-side session store needed
    - Works across multiple backend instances without sticky sessions
    - Refresh token rotation gives us revocation when needed
    - 15 min access token limits blast radius if a token leaks

    Alternatives considered:
    - Server sessions with Redis - rejected, wanted stateless
    - Opaque tokens + introspection - rejected, one extra hop per request

    References:
    - RFC 7519
    - Our existing userservice already issues JWTs for internal calls

Three design principles:

1. **Raw verbatim.** We store the actual text the agent wrote, never
   an LLM-extracted summary. MemPalace benchmarks show this wins over
   extraction on retrieval accuracy.

2. **Append-only.** Old entries are never deleted or edited. When a
   decision is superseded, a new file is added with `superseded_by`
   pointing back. This preserves history and lets later queries
   understand WHY a decision changed.

3. **Grep-first, embeddings-later.** The default search is ripgrep
   over the markdown files. This works in any environment, has zero
   dependencies, and surfaces exact matches. A vector layer (e.g.
   ChromaDB, MemPalace via MCP, or a local embedding server) can be
   added later to handle semantic queries without modifying the storage.

Layered loading (from MemPalace):

    L0 - identity and core facts, ~50 tokens, ALWAYS in context
    L1 - critical active project state, ~120 tokens, ALWAYS in context
    L2 - topic-relevant drawers, loaded on grep or vector match
    L3 - archive, loaded only when explicitly requested

L0 and L1 live in .claude/memory-graph/core.md - a single small file that the
agent reads at session start. L2 and L3 are searched on demand.
"""
from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# Slug rules (same as locks/handoffs)
_SLUG_STRIP = re.compile(r"[^\w\s-]+", re.UNICODE)
_SLUG_WHITESPACE = re.compile(r"[\s_]+")


def slugify(text: str, max_words: int = 5) -> str:
    if not text:
        return "untitled"
    cleaned = _SLUG_STRIP.sub(" ", text.lower())
    cleaned = _SLUG_WHITESPACE.sub(" ", cleaned).strip()
    words = cleaned.split()[:max_words]
    return "-".join(words) or "untitled"


# Standard hall names - the "types" of knowledge in a room
STANDARD_HALLS = ("decisions", "gotchas", "references", "discoveries", "preferences", "facts")


@dataclass
class Drawer:
    """One piece of raw verbatim knowledge."""

    title: str
    content: str
    hall: str = "facts"  # decisions | gotchas | references | discoveries | preferences | facts
    session_id: str = ""
    tags: list[str] = field(default_factory=list)
    created: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None  # None means still valid
    superseded_by: str | None = None  # filename of a newer drawer that replaces this one

    links: list[str] = field(default_factory=list)  # wiki-links: ["wing/room/hall/file"]

    def filename(self) -> str:
        when = time.strftime("%Y-%m-%d") if not self.created else self.created[:10]
        return f"{when}_{slugify(self.title)}.md"

    def render(self) -> str:
        lines: list[str] = ["---"]
        lines.append(f"title: {self.title}")
        lines.append(f"created: {self.created or time.strftime('%Y-%m-%dT%H:%M:%S')}")
        if self.session_id:
            lines.append(f"session: {self.session_id}")
        lines.append(f"hall: {self.hall}")
        if self.tags:
            lines.append(f"tags: [{', '.join(self.tags)}]")
        lines.append(f"valid_from: {self.valid_from or self.created or time.strftime('%Y-%m-%dT%H:%M:%S')}")
        lines.append(f"valid_to: {self.valid_to or 'null'}")
        lines.append(f"superseded_by: {self.superseded_by or 'null'}")
        if self.links:
            lines.append(f"links: [{', '.join(self.links)}]")
        lines.append("---")
        lines.append("")
        lines.append(f"# {self.title}")
        lines.append("")
        lines.append(self.content)
        if self.links:
            lines.append("")
            lines.append("## Related")
            lines.append("")
            for link in self.links:
                lines.append(f"- [[{link}]]")
        lines.append("")
        return "\n".join(lines)


class MemoryGraph:
    """A hierarchical knowledge graph stored as nested markdown files."""

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.root = self.project_root / ".claude" / "memory-graph"
        self.wings_dir = self.root / "wings"
        self.core_path = self.root / "core.md"

    def ensure(self) -> None:
        self.wings_dir.mkdir(parents=True, exist_ok=True)
        if not self.core_path.exists():
            self.core_path.write_text(
                "# Core Memory (L0 + L1)\n\n"
                "This file is always loaded at session start. Keep it under ~170 tokens.\n\n"
                "## L0 - Identity (~50 tokens)\n\n"
                "(describe who the user is, what role, key preferences)\n\n"
                "## L1 - Active Project Critical Facts (~120 tokens)\n\n"
                "(the 3-5 facts the agent must always know about the current project)\n",
                encoding="utf-8",
            )

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".tmp.{uuid.uuid4().hex[:8]}")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)

    def wing_path(self, wing: str) -> Path:
        return self.wings_dir / slugify(wing)

    def room_path(self, wing: str, room: str) -> Path:
        return self.wing_path(wing) / "rooms" / slugify(room)

    def hall_path(self, wing: str, room: str, hall: str) -> Path:
        return self.room_path(wing, room) / slugify(hall)

    def save(self, wing: str, room: str, drawer: Drawer) -> Path:
        """Save a drawer to the given wing/room. Appends a suffix if name collides."""
        self.ensure()
        hall_dir = self.hall_path(wing, room, drawer.hall)
        hall_dir.mkdir(parents=True, exist_ok=True)
        path = hall_dir / drawer.filename()
        counter = 2
        while path.exists():
            stem = path.stem
            path = hall_dir / f"{stem}_{counter}.md"
            counter += 1
        self._atomic_write(path, drawer.render())
        return path

    def supersede(self, old_path: Path, new_drawer: Drawer) -> tuple[Path, Path]:
        """Mark old_path as superseded by a new drawer and return both paths.

        The old file is NOT deleted. We append supersession metadata to it
        (via a new `valid_to` and `superseded_by` in the frontmatter).
        The new drawer is saved normally.
        """
        if not old_path.exists():
            raise FileNotFoundError(old_path)
        # Save the new drawer first - if this fails, the old file is untouched
        # We need to figure out the wing/room from the old path
        new_path = old_path.parent / new_drawer.filename()
        counter = 2
        while new_path.exists():
            stem = new_path.stem
            new_path = old_path.parent / f"{stem}_{counter}.md"
            counter += 1
        self._atomic_write(new_path, new_drawer.render())

        # Now rewrite the old file's frontmatter to mark it superseded
        old_content = old_path.read_text(encoding="utf-8")
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        old_content = re.sub(r"^valid_to: .*$", f"valid_to: {now}", old_content, count=1, flags=re.MULTILINE)
        old_content = re.sub(
            r"^superseded_by: .*$",
            f"superseded_by: {new_path.name}",
            old_content,
            count=1,
            flags=re.MULTILINE,
        )
        self._atomic_write(old_path, old_content)

        return old_path, new_path

    def list_wings(self) -> list[str]:
        if not self.wings_dir.exists():
            return []
        return sorted(p.name for p in self.wings_dir.iterdir() if p.is_dir())

    def list_rooms(self, wing: str) -> list[str]:
        rooms_dir = self.wing_path(wing) / "rooms"
        if not rooms_dir.exists():
            return []
        return sorted(p.name for p in rooms_dir.iterdir() if p.is_dir())

    def list_drawers(
        self,
        wing: str | None = None,
        room: str | None = None,
        hall: str | None = None,
        include_superseded: bool = False,
    ) -> list[Path]:
        """Walk the memory graph and collect drawer files by filter."""
        if not self.wings_dir.exists():
            return []
        start = self.wings_dir
        if wing:
            start = self.wing_path(wing)
            if not start.exists():
                return []
        results: list[Path] = []
        for path in start.rglob("*.md"):
            if not path.is_file():
                continue
            # Filter by room if requested
            if room and slugify(room) not in path.parts:
                continue
            if hall and slugify(hall) not in path.parts:
                continue
            if not include_superseded:
                # Cheap check: skip files with superseded_by: non-null
                try:
                    head = path.read_text(encoding="utf-8", errors="ignore")[:500]
                    if re.search(r"^superseded_by: (?!null$)", head, re.MULTILINE):
                        continue
                except OSError:
                    continue
            results.append(path)
        return sorted(results)

    def search(self, query: str, wing: str | None = None) -> list[tuple[Path, str]]:
        """Simple substring search across drawer bodies. Returns (path, matching_line) tuples.

        This is the grep-first baseline. For semantic search, wire up an
        external layer that reads the same files.
        """
        results: list[tuple[Path, str]] = []
        q = query.lower()
        for path in self.list_drawers(wing=wing):
            try:
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if q in line.lower():
                        results.append((path, line.strip()))
                        break  # one match per drawer is enough for the index
            except OSError:
                continue
        return results

    def read_core(self) -> str:
        """Return the L0+L1 always-loaded core memory as a string."""
        self.ensure()
        return self.core_path.read_text(encoding="utf-8")

    # -- Knowledge Index (entity resolution) --------------------------------

    def build_index(self) -> list[dict]:
        """Scan all drawers and build a knowledge index.

        Returns a list of dicts, one per drawer:
            {title, wing, room, hall, tags, path, superseded}

        Use this before saving a new drawer to check if a similar entry
        already exists (entity resolution without vector search).
        The index is cheap to build - just frontmatter parsing, no LLM.
        """
        index: list[dict] = []
        if not self.wings_dir.exists():
            return index

        for wing_dir in sorted(self.wings_dir.iterdir()):
            if not wing_dir.is_dir():
                continue
            wing_name = wing_dir.name
            rooms_dir = wing_dir / "rooms"
            if not rooms_dir.exists():
                continue
            for room_dir in sorted(rooms_dir.iterdir()):
                if not room_dir.is_dir():
                    continue
                room_name = room_dir.name
                for md_file in room_dir.rglob("*.md"):
                    if not md_file.is_file():
                        continue
                    try:
                        head = md_file.read_text(encoding="utf-8", errors="ignore")[:800]
                    except OSError:
                        continue
                    # Parse frontmatter
                    meta = self._parse_frontmatter(head)
                    # Determine hall from path
                    hall = md_file.parent.name if md_file.parent.name in STANDARD_HALLS else ""

                    entry = {
                        "title": meta.get("title", md_file.stem),
                        "wing": wing_name,
                        "room": room_name,
                        "hall": hall,
                        "tags": [t.strip() for t in meta.get("tags", "").strip("[]").split(",") if t.strip()],
                        "path": str(md_file.relative_to(self.root)),
                        "superseded": meta.get("superseded_by", "null") != "null",
                        "created": meta.get("created", ""),
                    }
                    index.append(entry)

        return index

    def find_similar(self, title: str, threshold: float = 0.5) -> list[dict]:
        """Find drawers with titles similar to the given one.

        Uses word overlap ratio (Jaccard-like) - no external deps.
        Returns matching index entries sorted by similarity (highest first).

        Args:
            title: the title to match against
            threshold: minimum word overlap ratio (0.0-1.0)
        """
        title_words = set(slugify(title).split("-"))
        if not title_words:
            return []

        index = self.build_index()
        scored: list[tuple[float, dict]] = []

        for entry in index:
            if entry["superseded"]:
                continue
            entry_words = set(slugify(entry["title"]).split("-"))
            if not entry_words:
                continue
            # Jaccard similarity
            intersection = title_words & entry_words
            union = title_words | entry_words
            score = len(intersection) / len(union) if union else 0
            if score >= threshold:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored]

    def render_index(self) -> str:
        """Render the knowledge index as a markdown table.

        Designed to be injected into an agent's context so it knows what
        knowledge already exists before creating new entries.
        """
        index = self.build_index()
        if not index:
            return "(empty memory graph)"

        lines = [
            "| Title | Wing | Room | Hall | Tags |",
            "|---|---|---|---|---|",
        ]
        for entry in index:
            if entry["superseded"]:
                continue
            tags = ", ".join(entry["tags"][:3])
            lines.append(
                f"| {entry['title'][:50]} | {entry['wing']} | {entry['room']} "
                f"| {entry['hall']} | {tags} |"
            )
        return "\n".join(lines)

    def find_backlinks(self, drawer_path: str) -> list[dict]:
        """Find all drawers that link to the given path via [[wiki-links]].

        Args:
            drawer_path: relative path within memory-graph/ (e.g. "wings/myapp/rooms/auth/decisions/jwt.md")

        Returns list of {title, path, link_text} for each drawer that references this one.
        """
        # Normalize: accept full path or just filename
        target_name = Path(drawer_path).stem
        pattern = re.compile(r"\[\[([^\]]*" + re.escape(target_name) + r"[^\]]*)\]\]")

        results: list[dict] = []
        for drawer in self.list_drawers(include_superseded=False):
            try:
                content = drawer.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in pattern.finditer(content):
                meta = self._parse_frontmatter(content[:500])
                results.append({
                    "title": meta.get("title", drawer.stem),
                    "path": str(drawer.relative_to(self.root)),
                    "link_text": match.group(1),
                })
                break  # one backlink per file is enough
        return results

    @staticmethod
    def _parse_frontmatter(text: str) -> dict:
        """Quick frontmatter parser - no yaml dependency."""
        meta: dict[str, str] = {}
        if not text.startswith("---"):
            return meta
        parts = text.split("---", 2)
        if len(parts) < 3:
            return meta
        for line in parts[1].strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        return meta
