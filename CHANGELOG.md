# Changelog

All notable changes to mclaude will be documented in this file. Newest first.

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
