"""
Project Work Lock - race-free work claim protocol for parallel Claude Code sessions.

Prevents two sessions from accidentally working on the same task at the same
time. Uses atomic file creation (O_CREAT | O_EXCL) so two claims can never
both succeed, even if they race.

Architecture:

    .claude/locks/active-work/<slug>.lock
        Atomic sentinel file. Its existence == the work is claimed.
        Created with os.open(O_CREAT | O_EXCL) - if the file already exists,
        the call fails and the second claimer knows to back off.

    .claude/locks/active-work/<slug>.heartbeat
        Unix timestamp (text), refreshed by the holder every 30 seconds.
        Any session can read it. If it has not been refreshed for more than
        STALE_AFTER_SECONDS, the lock is considered abandoned and can be
        force-released by another session (log tell, not silent).

    .claude/locks/active-work/<slug>.metadata.json
        Session ID, working directory, file paths, description, start time.
        Human-readable. Updated together with heartbeat. Other sessions read
        this to decide whether to wait, help, or warn the user.

    .claude/locks/completed/<slug>_YYYY-MM-DD_HH-MM.md
        Archived on release. Contains final summary and resolution.

Usage from the CLI (or from Claude):

    # Try to claim a piece of work
    python project_lock.py claim --slug fix-auth-bug-42 \
        --description "Fixing auth middleware race condition" \
        --files src/auth/middleware.py src/auth/session.py

    # Check if someone is already working on this
    python project_lock.py status fix-auth-bug-42

    # List everything that's currently claimed in this project
    python project_lock.py list

    # Refresh heartbeat (called periodically by the holder)
    python project_lock.py heartbeat fix-auth-bug-42 --session <session-id>

    # Release (either on successful completion or abandonment)
    python project_lock.py release fix-auth-bug-42 \
        --session <session-id> \
        --summary "Fixed by adding mutex around session write"

    # Force-release a stale lock (with audit trail)
    python project_lock.py force-release fix-auth-bug-42 --reason "heartbeat stale > 5 min"

Exit codes:

    0   success
    1   usage error
    10  lock held by another session (claim)
    11  lock held but stale - candidate for force-release (claim)
    12  lock does not exist (release, heartbeat)
    13  lock held by different session (release, heartbeat)

Design notes:

- **Atomic claim:** os.open(O_CREAT | O_EXCL) is the canonical race-free way
  to create a file. On POSIX and Windows NTFS, only one caller wins.
- **Heartbeat, not TTL:** a fixed TTL forces holders to set a length they
  might exceed. Heartbeat lets long-running work stay claimed as long as
  the holder is alive, and dies quickly when the holder goes silent.
- **Force-release requires explicit action:** no automatic cleanup on read.
  We do not want a session that was temporarily paused (Claude is thinking)
  to be preempted silently. Force-release leaves an audit record.
- **Project-local:** locks live under `.claude/locks/` in the working
  directory, not globally. Work claims are scoped to the project.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

# Stale threshold: if heartbeat is this old, the lock is considered abandoned
# and can be force-released. 3 minutes is conservative enough to survive a
# pause for thinking without letting dead sessions squat forever.
STALE_AFTER_SECONDS = 180

# Slug validation: kebab-case, letters/digits/hyphens, 3-80 chars.
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")


def project_root() -> Path:
    return Path.cwd()


def locks_dir() -> Path:
    return project_root() / ".claude" / "locks" / "active-work"


def completed_dir() -> Path:
    return project_root() / ".claude" / "locks" / "completed"


def ensure_dirs() -> None:
    locks_dir().mkdir(parents=True, exist_ok=True)
    completed_dir().mkdir(parents=True, exist_ok=True)


def lock_path(slug: str) -> Path:
    return locks_dir() / f"{slug}.lock"


def heartbeat_path(slug: str) -> Path:
    return locks_dir() / f"{slug}.heartbeat"


def metadata_path(slug: str) -> Path:
    return locks_dir() / f"{slug}.metadata.json"


def validate_slug(slug: str) -> None:
    if not SLUG_PATTERN.match(slug):
        raise SystemExit(
            f"Invalid slug {slug!r}: must be kebab-case, letters/digits/hyphens, 3-80 chars"
        )


def atomic_write(path: Path, content: str) -> None:
    """Write content atomically via tmp + rename. Safe against partial writes."""
    tmp = path.with_suffix(path.suffix + f".tmp.{uuid.uuid4().hex[:8]}")
    tmp.write_text(content, encoding="utf-8")
    # os.replace is atomic on both POSIX and Windows.
    os.replace(tmp, path)


def read_metadata(slug: str) -> dict | None:
    p = metadata_path(slug)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def heartbeat_age(slug: str) -> float | None:
    """Seconds since last heartbeat, or None if no heartbeat exists."""
    p = heartbeat_path(slug)
    if not p.exists():
        return None
    try:
        return time.time() - p.stat().st_mtime
    except OSError:
        return None


def is_stale(slug: str) -> bool:
    age = heartbeat_age(slug)
    return age is not None and age > STALE_AFTER_SECONDS


# -- Commands ---------------------------------------------------------------

def cmd_claim(args: argparse.Namespace) -> int:
    validate_slug(args.slug)
    ensure_dirs()

    # Atomic create - fails if file already exists
    try:
        fd = os.open(str(lock_path(args.slug)), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # Lock exists. Is it stale?
        if is_stale(args.slug):
            print(f"[lock] {args.slug} is held but STALE (heartbeat > {STALE_AFTER_SECONDS}s)")
            meta = read_metadata(args.slug) or {}
            print(f"  held by session: {meta.get('session_id', '?')}")
            print(f"  last heartbeat:  {heartbeat_age(args.slug):.0f}s ago")
            print(f"  to take over:    python project_lock.py force-release {args.slug} --reason 'stale'")
            return 11
        meta = read_metadata(args.slug) or {}
        print(f"[lock] {args.slug} already held")
        print(f"  by session: {meta.get('session_id', '?')}")
        print(f"  since:      {meta.get('claimed_at', '?')}")
        print(f"  doing:      {meta.get('description', '?')}")
        print(f"  files:      {', '.join(meta.get('files', []) or ['?'])}")
        return 10

    # We hold the lock. Write our session ID so other commands can validate.
    session_id = args.session or uuid.uuid4().hex[:16]
    os.write(fd, session_id.encode("utf-8"))
    os.close(fd)

    # Write metadata
    meta = {
        "slug": args.slug,
        "session_id": session_id,
        "claimed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        "claimed_at_epoch": time.time(),
        "working_directory": str(project_root()),
        "description": args.description,
        "files": args.files or [],
    }
    atomic_write(metadata_path(args.slug), json.dumps(meta, indent=2, ensure_ascii=False))

    # Initial heartbeat
    heartbeat_path(args.slug).touch()

    print(f"[lock] claimed {args.slug}")
    print(f"  session:  {session_id}")
    print(f"  files:    {', '.join(args.files or [])}")
    print(f"  remember: refresh heartbeat every {STALE_AFTER_SECONDS // 6}s")
    return 0


def cmd_heartbeat(args: argparse.Namespace) -> int:
    validate_slug(args.slug)
    if not lock_path(args.slug).exists():
        print(f"[lock] {args.slug} does not exist")
        return 12
    meta = read_metadata(args.slug) or {}
    if args.session and meta.get("session_id") != args.session:
        print(f"[lock] {args.slug} held by different session ({meta.get('session_id')})")
        return 13
    # Touch heartbeat file - mtime will be updated
    heartbeat_path(args.slug).touch()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    validate_slug(args.slug)
    if not lock_path(args.slug).exists():
        print(f"[lock] {args.slug}: FREE")
        return 0
    meta = read_metadata(args.slug) or {}
    age = heartbeat_age(args.slug)
    stale = is_stale(args.slug)
    status = "STALE" if stale else "ACTIVE"
    print(f"[lock] {args.slug}: {status}")
    print(f"  session:       {meta.get('session_id', '?')}")
    print(f"  claimed at:    {meta.get('claimed_at', '?')}")
    print(f"  description:   {meta.get('description', '?')}")
    print(f"  files:         {', '.join(meta.get('files', []) or [])}")
    print(f"  heartbeat age: {age:.0f}s" if age is not None else "  heartbeat:     none")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    ensure_dirs()
    locks = sorted(locks_dir().glob("*.lock"))
    if not locks:
        print("[lock] no active work claims")
        return 0
    print(f"[lock] {len(locks)} active claims:")
    for lock in locks:
        slug = lock.stem
        meta = read_metadata(slug) or {}
        age = heartbeat_age(slug)
        stale = is_stale(slug)
        tag = "STALE" if stale else "ACTIVE"
        age_str = f"{age:.0f}s" if age is not None else "?"
        print(f"  [{tag}] {slug}")
        print(f"         session: {meta.get('session_id', '?')}")
        print(f"         doing:   {meta.get('description', '?')}")
        print(f"         heartbeat: {age_str} ago")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    validate_slug(args.slug)
    if not lock_path(args.slug).exists():
        print(f"[lock] {args.slug} does not exist")
        return 12
    meta = read_metadata(args.slug) or {}
    if args.session and meta.get("session_id") != args.session:
        print(
            f"[lock] {args.slug} held by different session ({meta.get('session_id')}), "
            f"not yours ({args.session}). Use force-release if you really mean it."
        )
        return 13

    # Archive to completed/
    ensure_dirs()
    when = time.strftime("%Y-%m-%d_%H-%M")
    archive = completed_dir() / f"{args.slug}_{when}.md"
    archive_body = [
        f"# Work Lock Release - {args.slug}",
        "",
        f"**Released at:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Session:** {meta.get('session_id', '?')}",
        f"**Description:** {meta.get('description', '?')}",
        f"**Files:** {', '.join(meta.get('files', []) or [])}",
        f"**Claimed at:** {meta.get('claimed_at', '?')}",
        "",
        "## Summary",
        "",
        args.summary or "(no summary provided)",
    ]
    archive.write_text("\n".join(archive_body), encoding="utf-8")

    # Remove all three lock files
    for p in (lock_path(args.slug), heartbeat_path(args.slug), metadata_path(args.slug)):
        try:
            p.unlink()
        except FileNotFoundError:
            pass

    print(f"[lock] released {args.slug}")
    print(f"  archive: {archive}")
    return 0


def cmd_force_release(args: argparse.Namespace) -> int:
    validate_slug(args.slug)
    if not lock_path(args.slug).exists():
        print(f"[lock] {args.slug} does not exist")
        return 12
    meta = read_metadata(args.slug) or {}
    age = heartbeat_age(args.slug)
    age_str = f"{age:.0f}s" if age is not None else "unknown"

    ensure_dirs()
    when = time.strftime("%Y-%m-%d_%H-%M")
    archive = completed_dir() / f"{args.slug}_{when}_FORCE.md"
    archive_body = [
        f"# Work Lock FORCE RELEASE - {args.slug}",
        "",
        f"**Forced at:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Reason:** {args.reason}",
        f"**Originally held by:** {meta.get('session_id', '?')}",
        f"**Originally claimed at:** {meta.get('claimed_at', '?')}",
        f"**Heartbeat age at force:** {age_str}",
        f"**Description:** {meta.get('description', '?')}",
        f"**Files:** {', '.join(meta.get('files', []) or [])}",
        "",
        "## Note",
        "",
        "This lock was force-released. The original holder did NOT complete",
        "the work normally. Review the description and files to decide whether",
        "the in-progress changes need to be salvaged, continued, or reverted.",
    ]
    archive.write_text("\n".join(archive_body), encoding="utf-8")

    for p in (lock_path(args.slug), heartbeat_path(args.slug), metadata_path(args.slug)):
        try:
            p.unlink()
        except FileNotFoundError:
            pass

    print(f"[lock] FORCE-RELEASED {args.slug}")
    print(f"  reason:  {args.reason}")
    print(f"  archive: {archive}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("claim", help="Claim a unit of work atomically")
    c.add_argument("--slug", required=True, help="Kebab-case identifier for the work")
    c.add_argument("--description", required=True, help="Short description of the work")
    c.add_argument("--session", help="Your session ID (otherwise generated)")
    c.add_argument("--files", nargs="*", help="File paths the work will touch")
    c.set_defaults(func=cmd_claim)

    h = sub.add_parser("heartbeat", help="Refresh the heartbeat for a lock you hold")
    h.add_argument("slug", help="Work slug")
    h.add_argument("--session", help="Your session ID (for validation)")
    h.set_defaults(func=cmd_heartbeat)

    s = sub.add_parser("status", help="Show status of one specific lock")
    s.add_argument("slug")
    s.set_defaults(func=cmd_status)

    ls = sub.add_parser("list", help="List all active work claims in this project")
    ls.set_defaults(func=cmd_list)

    r = sub.add_parser("release", help="Release a lock you hold")
    r.add_argument("slug")
    r.add_argument("--session", help="Your session ID (for validation)")
    r.add_argument("--summary", help="Final summary of what was done")
    r.set_defaults(func=cmd_release)

    fr = sub.add_parser("force-release", help="Force-release a stale or abandoned lock")
    fr.add_argument("slug")
    fr.add_argument("--reason", required=True, help="Why you are forcing the release")
    fr.set_defaults(func=cmd_force_release)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
