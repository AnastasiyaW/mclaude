# Changelog

All notable changes to mclaude will be documented in this file. Newest first.

## 0.3.0 - 2026-04-11

### Added: Claude Code Hooks Integration

Four hook scripts that integrate mclaude with Claude Code's hook system, turning advisory coordination into automatic behavior.

**SessionStart hook** (`hooks/session_start.py`):
- Automatically shows latest handoff, unread messages, and active locks when a session starts
- Output injected into agent context by Claude Code harness
- Respects `MCLAUDE_IDENTITY` for message filtering
- Skips handoffs older than 48 hours

**PreToolUse lock check** (`hooks/pre_edit_lock_check.py`):
- Checks if files being edited are locked by another session
- Triggered on `Edit(*)` tool calls via Claude Code `if` filter
- Warns but does not block (advisory) - prints lock holder info
- Matches files by path normalization (handles relative/absolute paths)

**Stop hook** (`hooks/remind_handoff.py`):
- Reminds to write handoff at session end for long sessions
- Warns about unreleased locks that would become orphaned
- Suppressed if a recent handoff was already written (<30 min)
- Only triggers for sessions with significant activity (>10 min)

**Pre-commit guard** (`hooks/pre_commit_guard.py`):
- Git pre-commit hook that BLOCKS commits touching locked files
- Enforcement point: advisory locks become hard blocks at commit time
- Own locks (via `MCLAUDE_IDENTITY`) are allowed through
- Install: `mclaude hooks install-guard`

**Hook installer** (`hooks/install.py`):
- `mclaude hooks install --apply` - copies scripts + updates settings.json
- `mclaude hooks show` - prints config for manual setup
- `mclaude hooks install-guard` - installs git pre-commit hook
- Merges with existing settings, does not overwrite

**Rules template** (`rules/mclaude-coordination.md`):
- Ready-to-use `.claude/rules/` file for projects using mclaude
- Covers session start protocol, work claiming, handoff writing

### Added: Status Command

Single-command overview of all five mclaude layers:

```bash
$ mclaude status
[mclaude] status for /path/to/project
  Identity: ani

  Locks (2 active):
    [ACTIVE] fix-auth by abcd1234: Fixing auth middleware
    [STALE]  old-task by 9876fedc: Abandoned task
  Handoffs: 5 total, latest: 2026-04-10_14-32_abcd_test.md
  Messages: 3 total in 1 mailbox(es), 1 unread for ani
  Memory: 2 wing(s), 8 drawer(s)
  Identities: ani, vasya
```

### Added: MCP Server

Native MCP (Model Context Protocol) integration - Claude Code can call mclaude
tools directly via JSON-RPC instead of shelling out to the CLI.

16 tools exposed: lock claim/release/status/list/heartbeat/force-release,
handoff write/latest/list, memory save/search/core, message send/inbox,
identity whoami, status overview.

```json
{
  "mcpServers": {
    "mclaude": {
      "command": "python",
      "args": ["-m", "mclaude.mcp_server"]
    }
  }
}
```

Returns structured JSON instead of text that needs parsing. Zero dependencies
beyond the mclaude package itself.

### Added: Worktree Metadata Awareness

Lock claims now auto-detect git worktree and branch information:

- `worktree` field in lock metadata (auto-detected or `--worktree` override)
- `branch` field shows current git branch
- Both displayed in `lock status`, `lock list`, and MCP responses
- Enables parallel work: "this lock is in worktree `feature-auth`, I'm in `main`"

### Tests

- **38 new tests** (80 total, all passing):
  - 5 SessionStart hook tests (empty project, handoffs, locks, messages, identity-gated)
  - 5 PreToolUse lock check tests (no locks, locked file, own lock, empty stdin, bad json)
  - 3 Stop hook tests (no activity, active locks warning, recent handoff suppression)
  - 4 pre-commit guard tests (no locks, locked blocks, own lock allows, unlocked passes)
  - 5 status command tests (empty, locks, handoffs, identity, registry)
  - 16 MCP server tests (tool definitions, lock CRUD, handoff write/latest, memory save/search, messages, identity, status)

---

## 0.2.0 - 2026-04-09

### Added: Layer 5 - Messages (`mclaude.messages`)

Live inter-session messaging formalizes the "desktop dead drop" pattern: one Claude writes a question to a file, another reads it and answers, all via append-only markdown files. Different from handoffs - handoffs are end-of-session, messages are real-time Q&A during active work.

- **Message types:** question, answer, request, update, error, broadcast, ack
- **Filename format:** `YYYY-MM-DD_HH-MM-SS_<from>_<to>_<type>_<slug>.md` (second-granularity because multiple messages can fly within a minute)
- **Multiple mailboxes** - `inbox` (default), or named like `review`, `infra-requests`
- **Broadcasts** via `to: "*"` (written to filesystem as `ALL` to survive Windows filename rules)
- **Threading** via `thread` field referencing the original message stem, `reply_to` for direct replies
- **Append-only semantics** - status transitions (`read`, `answered`, `archived`) are new ack messages, never edits to the original
- **Cross-platform safe** - `*` in filenames is sanitized to `ALL` (illegal on Windows)

New CLI commands:

```bash
mclaude message send --from ani --to vasya --type question \
    --subject "How to mock datetime" --body "Need to freeze time in tests"
mclaude message inbox ani
mclaude message thread <thread-id>
mclaude message mailboxes
mclaude message read <filename>
```

File format is designed to be compatible with the upcoming mclaude-hub network layer - a local file-based exchange and a WebSocket-based hub exchange will interoperate without format translation.

**11 new tests** (42 total, all passing):
- slug generation from subjects
- filename format and parse roundtrip
- validation (missing from_/to, bad type)
- render/parse roundtrip with full frontmatter
- inbox filtering by recipient (direct + broadcast)
- threading across multiple messages
- broadcast delivery to all recipients
- multiple mailboxes
- collision handling (_2, _3 suffixes)
- `mark_status` creates ack, never edits original

---

## 0.1.0 - 2026-04-09

Initial alpha release.

### Added

- **Layer 1: Work Locks** (`mclaude.locks`) - atomic work claims via `O_CREAT | O_EXCL`, heartbeat-based stale detection (3 min default), metadata.json with session ID and file paths, audit trail on force-release. Exit codes: 0 success, 10 held by another session, 11 held but stale, 12 does not exist, 13 wrong session.
- **Layer 2: Handoffs** (`mclaude.handoffs`) - per-session markdown files with unique names (`YYYY-MM-DD_HH-MM_<session8>_<slug>.md`), append-only INDEX.md, structured format with mandatory "what did NOT work" section, no-overwrite guarantee via unique naming + _2/_3 suffixes on collision, auto-slug from goal text, override with `slug_override`.
- **Layer 3: Memory Graph** (`mclaude.memory`) - hierarchical Wings/Rooms/Halls/Drawers structure stored as nested markdown files, raw verbatim content (inspired by MemPalace research showing extraction loses recall accuracy), frontmatter metadata with `valid_from`/`valid_to`/`superseded_by`, grep-first baseline search, append-only supersession that preserves history, L0+L1 always-loaded `core.md`.
- **Layer 4: Identity Registry** (`mclaude.registry`) - human-readable names for Claude instances, `MCLAUDE_IDENTITY` environment variable for `whoami`, atomic JSON writes with schema versioning, notification metadata (telegram, email, webhook) for future notification backends.
- **Unified CLI** (`mclaude` command) - `mclaude lock|handoff|memory|identity` subcommands dispatching to all four layers.
- **README** with the four-layer explanation, usage scenes, and design principles.
- **AGENTS.md** (Linux Foundation / Agentic AI Foundation format) telling agents how to use mclaude and which trigger phrases to recognize.
- **31 tests** covering all six lock scenarios (claim, double-claim, status, release, wrong-session, force-release, slug validation) plus handoff filename format, slug override, collision handling, listing, and find-by-fragment; memory supersession, search, filtering; registry validation, register/update, remove, touch, whoami, notify persistence.

### Design principles locked in for 0.x

1. File-based, zero external dependencies
2. Files are the source of truth
3. Append-only where possible, atomic otherwise
4. Graceful degradation - each layer works independently
5. Human-readable formats (Markdown, JSON, TOML)
6. Production-grade from day one

### Not yet implemented

- Notification layer (examples/notifications/ planned for 0.2)
- Vector search overlay for memory graph (optional, pluggable via MCP)
- Cross-machine sync beyond git (Syncthing recipe planned for docs)
- SessionStart hook scripts for Claude Code integration (hooks/ directory scaffolded)

### Credits

- **MemPalace** (Milla Jovovich, Ben Sigman) - hierarchical memory graph concept and the raw-verbatim-over-extraction insight
- **Paperclip** - heartbeat pattern and file-based coordination
- **DeerFlow 2.0** (ByteDance) - thinking about race conditions and the tradeoffs of isolation vs coordination
- **Claude Code** (Anthropic) - the harness mclaude is built to assist
- **Claw Code** (Sigrid Jin) - reminder that transparency in agent infrastructure is a feature
