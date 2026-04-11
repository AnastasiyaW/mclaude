"""
mclaude MCP server - expose all five layers as MCP tools.

Claude Code can call mclaude natively via MCP instead of shelling out
to the CLI. Returns structured JSON instead of text that needs parsing.

Transport: stdio (JSON-RPC over stdin/stdout).

Usage in .claude/settings.json or .mcp.json:
  {
    "mcpServers": {
      "mclaude": {
        "command": "python",
        "args": ["-m", "mclaude.mcp_server"]
      }
    }
  }

Protocol: MCP (Model Context Protocol) - JSON-RPC 2.0.
Spec: https://modelcontextprotocol.io/

Tools exposed:
  mclaude_lock_claim      - claim a work lock
  mclaude_lock_release    - release a lock with summary
  mclaude_lock_status     - check one lock
  mclaude_lock_list       - list all active locks
  mclaude_lock_heartbeat  - refresh heartbeat
  mclaude_lock_force_release - force-release a stale lock
  mclaude_handoff_write   - write a session handoff
  mclaude_handoff_latest  - read the latest handoff
  mclaude_handoff_list    - list handoffs
  mclaude_memory_save     - save a drawer to the memory graph
  mclaude_memory_search   - search the memory graph
  mclaude_memory_core     - read the always-loaded core memory
  mclaude_message_send    - send a message to another session
  mclaude_message_inbox   - check inbox for a recipient
  mclaude_mail_check      - check for NEW messages (with dedup state)
  mclaude_mail_reply      - reply to a message with auto-threading
  mclaude_mail_ask        - send a question and get thread_id
  mclaude_mail_digest     - summary of unread by sender/type
  mclaude_identity_whoami - get current identity
  mclaude_status          - one-command overview of all layers
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

from . import handoffs as _handoffs
from . import locks as _locks
from .mail import Mail as _Mail
from . import memory as _memory
from . import messages as _messages
from . import registry as _registry


# ---------------------------------------------------------------------------
# MCP protocol helpers (minimal, no dependencies)
# ---------------------------------------------------------------------------

def _read_message() -> dict | None:
    """Read one JSON-RPC message from stdin (Content-Length framing)."""
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None  # EOF
        line_str = line.decode("utf-8").rstrip("\r\n")
        if line_str == "":
            break  # end of headers
        if ":" in line_str:
            key, value = line_str.split(":", 1)
            headers[key.strip()] = value.strip()

    length = int(headers.get("Content-Length", "0"))
    if length == 0:
        return None
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


def _send_message(msg: dict) -> None:
    """Write one JSON-RPC message to stdout (Content-Length framing)."""
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
    sys.stdout.buffer.write(header + body)
    sys.stdout.buffer.flush()


def _result(id: int | str, result: dict) -> None:
    _send_message({"jsonrpc": "2.0", "id": id, "result": result})


def _error(id: int | str | None, code: int, message: str) -> None:
    _send_message({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "mclaude_lock_claim",
        "description": "Claim a work lock atomically. Returns success or info about who holds the lock.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Kebab-case identifier for the work (3-80 chars)"},
                "description": {"type": "string", "description": "Short description of what you're working on"},
                "files": {"type": "array", "items": {"type": "string"}, "description": "File paths this work will touch"},
                "session": {"type": "string", "description": "Your session ID (auto-generated if omitted)"},
                "worktree": {"type": "string", "description": "Git worktree path (auto-detected if omitted)"},
            },
            "required": ["slug", "description"],
        },
    },
    {
        "name": "mclaude_lock_release",
        "description": "Release a work lock with a summary of what was done.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Lock slug to release"},
                "summary": {"type": "string", "description": "Summary of completed work"},
                "session": {"type": "string", "description": "Your session ID (for validation)"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "mclaude_lock_status",
        "description": "Check status of a specific lock (FREE, ACTIVE, or STALE).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Lock slug to check"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "mclaude_lock_list",
        "description": "List all active work locks in this project.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mclaude_lock_heartbeat",
        "description": "Refresh the heartbeat for a lock you hold.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "session": {"type": "string"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "mclaude_lock_force_release",
        "description": "Force-release a stale or abandoned lock (creates audit trail).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "reason": {"type": "string", "description": "Why you are forcing the release"},
            },
            "required": ["slug", "reason"],
        },
    },
    {
        "name": "mclaude_handoff_write",
        "description": "Write a session handoff (structured context for the next session).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Your session ID"},
                "goal": {"type": "string", "description": "Goal of this session (1-2 sentences)"},
                "done": {"type": "array", "items": {"type": "string"}, "description": "What was accomplished"},
                "not_worked": {"type": "array", "items": {"type": "string"}, "description": "Failed approaches and why"},
                "working": {"type": "array", "items": {"type": "string"}, "description": "What is verified working"},
                "broken": {"type": "array", "items": {"type": "string"}, "description": "What is currently broken"},
                "blocked": {"type": "array", "items": {"type": "string"}, "description": "External blockers"},
                "next_step": {"type": "string", "description": "Single next action"},
                "slug": {"type": "string", "description": "Override auto-generated slug"},
            },
            "required": ["session", "goal"],
        },
    },
    {
        "name": "mclaude_handoff_latest",
        "description": "Read the most recent handoff file.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mclaude_handoff_list",
        "description": "List handoff files, optionally filtered by status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by ACTIVE, CLOSED, RESUMED, ABANDONED"},
            },
        },
    },
    {
        "name": "mclaude_memory_save",
        "description": "Save a knowledge drawer to the memory graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Wing (project or major topic)"},
                "room": {"type": "string", "description": "Room (sub-topic within wing)"},
                "hall": {"type": "string", "description": "Hall type: decisions|gotchas|references|discoveries|preferences|facts"},
                "title": {"type": "string", "description": "Title of the drawer"},
                "content": {"type": "string", "description": "Raw verbatim content (never summarize)"},
                "session": {"type": "string", "description": "Session ID of the writer"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["wing", "room", "title", "content"],
        },
    },
    {
        "name": "mclaude_memory_search",
        "description": "Search the memory graph by substring (grep baseline).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term"},
                "wing": {"type": "string", "description": "Limit search to one wing"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "mclaude_memory_core",
        "description": "Read the L0+L1 always-loaded core memory.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mclaude_message_send",
        "description": "Send a message to another Claude session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_": {"type": "string", "description": "Your identity name or session ID"},
                "to": {"type": "string", "description": "Recipient name, or '*' for broadcast"},
                "type": {"type": "string", "enum": ["question", "answer", "request", "update", "error", "broadcast", "ack"]},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "reply_to": {"type": "string", "description": "Filename of message being replied to"},
                "thread": {"type": "string", "description": "Thread ID"},
                "urgent": {"type": "boolean"},
            },
            "required": ["from_", "to"],
        },
    },
    {
        "name": "mclaude_message_inbox",
        "description": "Check inbox for unread messages addressed to a recipient.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "Your name or session ID"},
                "include_read": {"type": "boolean", "description": "Include already-read messages"},
            },
            "required": ["recipient"],
        },
    },
    {
        "name": "mclaude_identity_whoami",
        "description": "Get current identity from MCLAUDE_IDENTITY env var.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mclaude_status",
        "description": "One-command overview of all five mclaude layers (locks, handoffs, messages, memory, identities).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mclaude_mail_check",
        "description": "Check for NEW messages since last check (with dedup - won't show same message twice).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mclaude_mail_reply",
        "description": "Reply to a message with auto-threading. Finds the original message by filename fragment.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "original_filename": {"type": "string", "description": "Filename or fragment of the message to reply to"},
                "body": {"type": "string", "description": "Reply body"},
                "subject": {"type": "string", "description": "Override subject (default: Re: original subject)"},
            },
            "required": ["original_filename", "body"],
        },
    },
    {
        "name": "mclaude_mail_ask",
        "description": "Send a question to another Claude session. Returns thread_id for tracking replies.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient identity name"},
                "question": {"type": "string", "description": "The question (used as subject)"},
                "body": {"type": "string", "description": "Additional context"},
                "urgent": {"type": "boolean"},
            },
            "required": ["to", "question"],
        },
    },
    {
        "name": "mclaude_memory_find_similar",
        "description": "Find existing memory drawers with similar titles (entity resolution). Use BEFORE saving to avoid duplicates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Title to match against existing drawers"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "mclaude_memory_index",
        "description": "Get a markdown table of ALL knowledge in the memory graph. Use at session start for context.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mclaude_index",
        "description": "Generate code-map.md and llms.txt from the project's Python source. Returns stats.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project root (default: cwd)"},
                "format": {"type": "string", "enum": ["all", "code-map", "llms-txt"], "description": "Output format"},
            },
        },
    },
    {
        "name": "mclaude_mail_digest",
        "description": "Summary of unread messages: count by sender and type.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _handle_lock_claim(params: dict) -> dict:
    slug = params["slug"]
    try:
        _locks.validate_slug(slug)
    except SystemExit as e:
        return {"success": False, "error": str(e)}

    _locks.ensure_dirs()
    try:
        fd = os.open(str(_locks.lock_path(slug)), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        meta = _locks.read_metadata(slug) or {}
        stale = _locks.is_stale(slug)
        return {
            "success": False,
            "reason": "stale" if stale else "held",
            "holder_session": meta.get("session_id", "?"),
            "description": meta.get("description", "?"),
            "files": meta.get("files", []),
            "heartbeat_age": _locks.heartbeat_age(slug),
        }

    session_id = params.get("session") or uuid.uuid4().hex[:16]
    os.write(fd, session_id.encode("utf-8"))
    os.close(fd)

    worktree = params.get("worktree") or _locks.detect_worktree()
    branch = _locks.detect_git_branch()
    meta = {
        "slug": slug,
        "session_id": session_id,
        "claimed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "claimed_at_epoch": time.time(),
        "working_directory": str(_locks.project_root()),
        "description": params["description"],
        "files": params.get("files", []),
        "worktree": worktree,
        "branch": branch,
    }
    _locks.atomic_write(_locks.metadata_path(slug), json.dumps(meta, indent=2, ensure_ascii=False))
    _locks.heartbeat_path(slug).touch()

    return {"success": True, "session_id": session_id, "slug": slug}


def _handle_lock_release(params: dict) -> dict:
    slug = params["slug"]
    if not _locks.lock_path(slug).exists():
        return {"success": False, "error": "lock does not exist"}

    meta = _locks.read_metadata(slug) or {}
    session = params.get("session")
    if session and meta.get("session_id") != session:
        return {"success": False, "error": f"held by different session ({meta.get('session_id')})"}

    _locks.ensure_dirs()
    when = time.strftime("%Y-%m-%d_%H-%M")
    archive = _locks.completed_dir() / f"{slug}_{when}.md"
    summary = params.get("summary", "(no summary)")
    archive_body = [
        f"# Work Lock Release - {slug}", "",
        f"**Released at:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Session:** {meta.get('session_id', '?')}",
        f"**Description:** {meta.get('description', '?')}",
        f"**Summary:** {summary}",
    ]
    archive.write_text("\n".join(archive_body), encoding="utf-8")

    for p in (_locks.lock_path(slug), _locks.heartbeat_path(slug), _locks.metadata_path(slug)):
        try:
            p.unlink()
        except FileNotFoundError:
            pass

    return {"success": True, "archive": str(archive)}


def _handle_lock_status(params: dict) -> dict:
    slug = params["slug"]
    if not _locks.lock_path(slug).exists():
        return {"status": "FREE", "slug": slug}
    meta = _locks.read_metadata(slug) or {}
    stale = _locks.is_stale(slug)
    return {
        "status": "STALE" if stale else "ACTIVE",
        "slug": slug,
        "session_id": meta.get("session_id", "?"),
        "description": meta.get("description", "?"),
        "files": meta.get("files", []),
        "heartbeat_age": _locks.heartbeat_age(slug),
        "worktree": meta.get("worktree"),
        "branch": meta.get("branch"),
    }


def _handle_lock_list(params: dict) -> dict:
    _locks.ensure_dirs()
    locks = sorted(_locks.locks_dir().glob("*.lock"))
    result = []
    for lock in locks:
        slug = lock.stem
        meta = _locks.read_metadata(slug) or {}
        result.append({
            "slug": slug,
            "status": "STALE" if _locks.is_stale(slug) else "ACTIVE",
            "session_id": meta.get("session_id", "?"),
            "description": meta.get("description", "?"),
            "files": meta.get("files", []),
            "worktree": meta.get("worktree"),
            "branch": meta.get("branch"),
        })
    return {"locks": result, "count": len(result)}


def _handle_lock_heartbeat(params: dict) -> dict:
    slug = params["slug"]
    if not _locks.lock_path(slug).exists():
        return {"success": False, "error": "lock does not exist"}
    meta = _locks.read_metadata(slug) or {}
    session = params.get("session")
    if session and meta.get("session_id") != session:
        return {"success": False, "error": "held by different session"}
    _locks.heartbeat_path(slug).touch()
    return {"success": True}


def _handle_lock_force_release(params: dict) -> dict:
    slug = params["slug"]
    if not _locks.lock_path(slug).exists():
        return {"success": False, "error": "lock does not exist"}

    meta = _locks.read_metadata(slug) or {}
    _locks.ensure_dirs()
    when = time.strftime("%Y-%m-%d_%H-%M")
    archive = _locks.completed_dir() / f"{slug}_{when}_FORCE.md"
    archive_body = [
        f"# Work Lock FORCE RELEASE - {slug}", "",
        f"**Forced at:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Reason:** {params['reason']}",
        f"**Originally held by:** {meta.get('session_id', '?')}",
    ]
    archive.write_text("\n".join(archive_body), encoding="utf-8")

    for p in (_locks.lock_path(slug), _locks.heartbeat_path(slug), _locks.metadata_path(slug)):
        try:
            p.unlink()
        except FileNotFoundError:
            pass

    return {"success": True, "archive": str(archive)}


def _handle_handoff_write(params: dict) -> dict:
    store = _handoffs.HandoffStore()
    h = _handoffs.Handoff(
        session_id=params["session"],
        goal=params["goal"],
        done=params.get("done", []),
        not_worked=params.get("not_worked", []),
        working=params.get("working", []),
        broken=params.get("broken", []),
        blocked=params.get("blocked", []),
        next_step=params.get("next_step", ""),
        slug_override=params.get("slug"),
    )
    path = store.write(h)
    return {"success": True, "path": str(path), "filename": path.name}


def _handle_handoff_latest(params: dict) -> dict:
    store = _handoffs.HandoffStore()
    latest = store.latest()
    if not latest:
        return {"found": False}
    return {
        "found": True,
        "filename": latest.name,
        "content": latest.read_text(encoding="utf-8"),
    }


def _handle_handoff_list(params: dict) -> dict:
    store = _handoffs.HandoffStore()
    lines = store.get_index_lines(status_filter=params.get("status"))
    return {"lines": lines, "count": len(lines)}


def _handle_memory_save(params: dict) -> dict:
    graph = _memory.MemoryGraph()
    drawer = _memory.Drawer(
        title=params["title"],
        content=params["content"],
        hall=params.get("hall", "facts"),
        session_id=params.get("session", ""),
        tags=params.get("tags", []),
    )
    path = graph.save(params["wing"], params["room"], drawer)
    return {"success": True, "path": str(path)}


def _handle_memory_search(params: dict) -> dict:
    graph = _memory.MemoryGraph()
    results = graph.search(params["query"], wing=params.get("wing"))
    return {
        "results": [
            {"path": str(p.relative_to(graph.root)), "match": line}
            for p, line in results
        ],
        "count": len(results),
    }


def _handle_memory_core(params: dict) -> dict:
    graph = _memory.MemoryGraph()
    return {"content": graph.read_core()}


def _handle_message_send(params: dict) -> dict:
    store = _messages.MessageStore()
    msg = _messages.Message(
        from_=params["from_"],
        to=params["to"],
        type=params.get("type", "update"),
        subject=params.get("subject", ""),
        body=params.get("body", ""),
        reply_to=params.get("reply_to"),
        thread=params.get("thread"),
        urgent=params.get("urgent", False),
    )
    path = store.send(msg)
    return {"success": True, "path": str(path), "filename": path.name}


def _handle_message_inbox(params: dict) -> dict:
    store = _messages.MessageStore()
    msgs = store.inbox(
        recipient=params["recipient"],
        include_read=params.get("include_read", False),
    )
    return {
        "messages": [
            {
                "from": m.from_,
                "to": m.to,
                "type": m.type,
                "subject": m.subject,
                "body": m.body,
                "urgent": m.urgent,
                "status": m.status,
                "created": m.created,
            }
            for m in msgs
        ],
        "count": len(msgs),
    }


def _handle_identity_whoami(params: dict) -> dict:
    reg = _registry.Registry()
    me = reg.whoami()
    if not me:
        return {"found": False, "hint": "Set MCLAUDE_IDENTITY env var"}
    return {
        "found": True,
        "name": me.name,
        "id": me.id,
        "owner": me.owner,
        "roles": me.roles,
    }


def _handle_status(params: dict) -> dict:
    """Structured version of `mclaude status`."""
    root = Path.cwd()
    claude_dir = root / ".claude"
    result: dict = {"project_root": str(root)}

    # Identity
    result["identity"] = os.environ.get("MCLAUDE_IDENTITY", "")

    # Locks
    locks_dir = claude_dir / "locks" / "active-work"
    active_locks = []
    if locks_dir.exists():
        for lock in sorted(locks_dir.glob("*.lock")):
            slug = lock.stem
            meta = _locks.read_metadata(slug) or {}
            active_locks.append({
                "slug": slug,
                "status": "STALE" if _locks.is_stale(slug) else "ACTIVE",
                "session_id": meta.get("session_id", "?")[:8],
                "description": meta.get("description", "?"),
            })
    result["locks"] = active_locks

    # Handoffs
    handoffs_dir = claude_dir / "handoffs"
    if handoffs_dir.exists():
        files = [p for p in handoffs_dir.glob("*.md") if p.name != "INDEX.md"]
        files.sort(key=lambda p: p.name, reverse=True)
        result["handoffs"] = {
            "total": len(files),
            "latest": files[0].name if files else None,
        }
    else:
        result["handoffs"] = {"total": 0, "latest": None}

    # Messages
    identity = result["identity"]
    msg_dir = claude_dir / "messages"
    unread = 0
    total = 0
    if msg_dir.exists():
        for mb in (p for p in msg_dir.iterdir() if p.is_dir()):
            for f in mb.glob("*.md"):
                total += 1
                if identity:
                    try:
                        text = f.read_text(encoding="utf-8")
                        if "status: unread" in text and (f"to: {identity}" in text or "to: *" in text):
                            unread += 1
                    except OSError:
                        pass
    result["messages"] = {"total": total, "unread": unread}

    # Memory
    mem_dir = claude_dir / "memory-graph"
    if mem_dir.exists():
        wings_dir = mem_dir / "wings"
        wings = [p.name for p in wings_dir.iterdir() if p.is_dir()] if wings_dir.exists() else []
        drawers = len(list(mem_dir.rglob("*.md"))) - (1 if (mem_dir / "core.md").exists() else 0)
        result["memory"] = {"wings": len(wings), "drawers": drawers}
    else:
        result["memory"] = {"wings": 0, "drawers": 0}

    # Registry
    reg_path = claude_dir / "registry.json"
    if reg_path.exists():
        try:
            data = json.loads(reg_path.read_text(encoding="utf-8"))
            names = sorted(data.get("identities", {}).keys())
            result["identities"] = names
        except Exception:
            result["identities"] = []
    else:
        result["identities"] = []

    return result


def _handle_memory_find_similar(params: dict) -> dict:
    graph = _memory.MemoryGraph()
    similar = graph.find_similar(params["title"])
    return {
        "similar": similar[:10],
        "count": len(similar),
        "hint": "If a similar drawer exists, consider updating it via supersede() instead of creating a new one",
    }


def _handle_memory_index(params: dict) -> dict:
    graph = _memory.MemoryGraph()
    return {"index": graph.render_index()}


def _handle_index(params: dict) -> dict:
    from .indexer import CodeIndex
    root = Path(params["path"]) if params.get("path") else Path.cwd()
    idx = CodeIndex(root)
    idx.scan()
    fmt = params.get("format", "all")
    outputs = []
    if fmt in ("all", "code-map"):
        p = idx.write_code_map()
        outputs.append(str(p))
    if fmt in ("all", "llms-txt"):
        p = idx.write_llms_txt()
        outputs.append(str(p))
    stats = idx.stats()
    stats["outputs"] = outputs
    return stats


def _handle_mail_check(params: dict) -> dict:
    identity = os.environ.get("MCLAUDE_IDENTITY", "")
    if not identity:
        return {"messages": [], "count": 0, "error": "MCLAUDE_IDENTITY not set"}
    mail = _Mail(identity=identity)
    msgs = mail.check()
    return {
        "messages": [
            {
                "from": m.from_,
                "to": m.to,
                "type": m.type,
                "subject": m.subject,
                "body": m.body,
                "urgent": m.urgent,
            }
            for m in msgs
        ],
        "count": len(msgs),
    }


def _handle_mail_reply(params: dict) -> dict:
    identity = os.environ.get("MCLAUDE_IDENTITY", "")
    if not identity:
        return {"success": False, "error": "MCLAUDE_IDENTITY not set"}
    mail = _Mail(identity=identity)

    # Find the original message by filename fragment
    fragment = params["original_filename"]
    store = mail.store
    matches = []
    for path in store.list_mailbox(mail.mailbox):
        if fragment in path.name:
            matches.append(path)
    if not matches:
        return {"success": False, "error": f"No message matching '{fragment}'"}

    original = _messages.Message.parse(matches[0])
    path = mail.reply(original, params["body"], subject=params.get("subject"))
    return {"success": True, "path": str(path), "thread": original.thread or original.filename()}


def _handle_mail_ask(params: dict) -> dict:
    identity = os.environ.get("MCLAUDE_IDENTITY", "")
    if not identity:
        return {"success": False, "error": "MCLAUDE_IDENTITY not set"}
    mail = _Mail(identity=identity)
    thread_id = mail.ask(
        to=params["to"],
        question=params["question"],
        body=params.get("body", ""),
        urgent=params.get("urgent", False),
    )
    return {"success": True, "thread_id": thread_id}


def _handle_mail_digest(params: dict) -> dict:
    identity = os.environ.get("MCLAUDE_IDENTITY", "")
    if not identity:
        return {"total": 0, "error": "MCLAUDE_IDENTITY not set"}
    mail = _Mail(identity=identity)
    return mail.digest()


HANDLERS = {
    "mclaude_lock_claim": _handle_lock_claim,
    "mclaude_lock_release": _handle_lock_release,
    "mclaude_lock_status": _handle_lock_status,
    "mclaude_lock_list": _handle_lock_list,
    "mclaude_lock_heartbeat": _handle_lock_heartbeat,
    "mclaude_lock_force_release": _handle_lock_force_release,
    "mclaude_handoff_write": _handle_handoff_write,
    "mclaude_handoff_latest": _handle_handoff_latest,
    "mclaude_handoff_list": _handle_handoff_list,
    "mclaude_memory_save": _handle_memory_save,
    "mclaude_memory_search": _handle_memory_search,
    "mclaude_memory_core": _handle_memory_core,
    "mclaude_message_send": _handle_message_send,
    "mclaude_message_inbox": _handle_message_inbox,
    "mclaude_identity_whoami": _handle_identity_whoami,
    "mclaude_status": _handle_status,
    "mclaude_memory_find_similar": _handle_memory_find_similar,
    "mclaude_memory_index": _handle_memory_index,
    "mclaude_index": _handle_index,
    "mclaude_mail_check": _handle_mail_check,
    "mclaude_mail_reply": _handle_mail_reply,
    "mclaude_mail_ask": _handle_mail_ask,
    "mclaude_mail_digest": _handle_mail_digest,
}


# ---------------------------------------------------------------------------
# MCP server main loop
# ---------------------------------------------------------------------------

SERVER_INFO = {
    "name": "mclaude",
    "version": "0.3.0",
}

CAPABILITIES = {
    "tools": {},
}


def main() -> None:
    """Run the MCP server on stdio."""
    # Redirect any stray prints to stderr so they don't corrupt the protocol
    _real_stdout = sys.stdout
    sys.stdout = sys.stderr

    # Use the real stdout for protocol messages
    import io
    sys.stdout = _real_stdout

    while True:
        msg = _read_message()
        if msg is None:
            break  # EOF

        method = msg.get("method", "")
        id_ = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            _result(id_, {
                "protocolVersion": "2024-11-05",
                "serverInfo": SERVER_INFO,
                "capabilities": CAPABILITIES,
            })

        elif method == "notifications/initialized":
            pass  # No response needed for notifications

        elif method == "tools/list":
            _result(id_, {"tools": TOOLS})

        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            handler = HANDLERS.get(tool_name)
            if not handler:
                _result(id_, {
                    "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                    "isError": True,
                })
            else:
                try:
                    result = handler(tool_args)
                    _result(id_, {
                        "content": [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}],
                    })
                except Exception as e:
                    _result(id_, {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    })

        elif method == "ping":
            _result(id_, {})

        elif id_ is not None:
            _error(id_, -32601, f"Method not found: {method}")
        # else: notification we don't handle, ignore


if __name__ == "__main__":
    main()
