"""Generic template: cross-link mclaude handoffs to any external task tracker.

Copy this file into your own team repo and plug in a `poster` function
that calls your tracker's API. mclaude intentionally does not ship a
built-in integration - see examples/integrations/README.md for why.

Usage pattern
-------------
    from handoff_refs_to_tracker import scan_once

    def post_to_my_tracker(task_id: str, body: str) -> None:
        # Your HTTP call here. Credentials from env vars.
        ...

    scan_once(project_root=".", provider="mytracker",
              poster=post_to_my_tracker)

The template:

- Scans `.claude/handoffs/*.md` for tokens matching `{provider}:ID`
  (both in a structured `## Refs` section and free-form in the body)
- For each (handoff_file, task_id) pair not already seen, calls `poster`
- Records what was posted in `.claude/handoffs/.link-state.json` so
  re-running does not double-post
- Never modifies handoff files, never modifies tracker state beyond
  calling your `poster`

This is a single-file, no-dependencies template. Copy it, do not import
it - the point is for your team to own the integration code.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable


STATE_FILENAME = ".link-state.json"


def _pattern_for(provider: str) -> re.Pattern[str]:
    """Build a regex that matches `<provider>:ID`, `<provider> #ID`,
    `<provider> ID` forms. ID is captured as group 1.

    Intentionally strict - words between provider and id (e.g.
    `vikunja task 1247`) are NOT matched. If you want flexible forms,
    widen the pattern in your own copy.
    """
    p = re.escape(provider)
    return re.compile(rf"{p}[:\s#]+([A-Za-z0-9_-]+)", re.IGNORECASE)


def _handoffs_dir(project_root: Path) -> Path:
    return project_root / ".claude" / "handoffs"


def _load_state(project_root: Path) -> dict[str, list[str]]:
    p = _handoffs_dir(project_root) / STATE_FILENAME
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(project_root: Path, state: dict[str, list[str]]) -> None:
    p = _handoffs_dir(project_root) / STATE_FILENAME
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(p))


def extract_ids(text: str, provider: str) -> set[str]:
    """Return every tracker id the handoff references."""
    pat = _pattern_for(provider)
    return {m.group(1) for m in pat.finditer(text)}


def build_default_comment(handoff_path: Path, handoff_md: str) -> str:
    """Starter comment text. Override with your own in the caller."""
    goal = ""
    for line in handoff_md.splitlines():
        if line.startswith("# "):
            goal = line[2:].strip()
            break
    goal = goal or "(handoff)"
    return (
        f"Handoff written: `{handoff_path.name}` - {goal[:200]}\n\n"
        "(posted by an mclaude integration script; the handoff file is "
        "the authoritative record)"
    )


def scan_once(
    project_root: Path | str,
    provider: str,
    poster: Callable[[str, str], None],
    *,
    build_comment: Callable[[Path, str], str] = build_default_comment,
    dry_run: bool = False,
) -> int:
    """One scan cycle. Returns number of (handoff, task_id) comments posted.

    Parameters
    ----------
    project_root : directory containing `.claude/`
    provider : the provider token prefix your refs use (e.g. "linear")
    poster : callable(task_id: str, body: str) that posts a comment.
             Your function owns auth, retries, rate-limiting.
    build_comment : build the comment body from (handoff_path, handoff_md).
                    Defaults to a simple "Handoff written: ..." message.
    dry_run : if True, print what would be posted and do not call poster.
    """
    root = Path(project_root)
    hdir = _handoffs_dir(root)
    if not hdir.is_dir():
        return 0

    state = _load_state(root)
    posted = 0

    for fp in sorted(hdir.glob("*.md")):
        if fp.name.startswith("INDEX") or fp.name.startswith("."):
            continue
        try:
            md = fp.read_text(encoding="utf-8")
        except OSError:
            continue

        ids = extract_ids(md, provider)
        if not ids:
            continue

        already = set(state.get(fp.name, []))
        to_post = ids - already
        if not to_post:
            continue

        body = build_comment(fp, md)
        done_ids = list(already)
        for tid in sorted(to_post):
            if dry_run:
                print(f"  DRY-RUN would post to {provider}:{tid}")
                done_ids.append(tid)
                posted += 1
                continue
            try:
                poster(tid, body)
                done_ids.append(tid)
                posted += 1
            except Exception as e:
                print(f"  failed {fp.name} -> {provider}:{tid}: {e}")

        if done_ids and not dry_run:
            state[fp.name] = sorted(set(done_ids))

    if posted and not dry_run:
        _save_state(root, state)

    return posted
