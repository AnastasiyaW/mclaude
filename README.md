# mclaude

**Multi-session collaboration for Claude Code and other AI coding agents.**

When you have two Claude chats open on the same project - or two teammates running Claude Code at the same time, or yourself switching between laptops - you eventually hit the same problem: *whose turn is it, what has already been done, and how do I find out without interrupting?*

mclaude is a small, file-based system that answers those questions. It does not replace your agent. It gives your agents a shared notebook, a shared lock box, a shared memory shelf, and a shared address book - all living as plain markdown and JSON inside your project.

Six file-based layers (zero dependencies) plus optional network, desktop, and audio extensions.

---

## The problem in one scene

> **Monday, 10:04 AM.** You ask Claude to fix a bug in `auth/middleware.py`.
>
> **Monday, 10:07 AM.** Your teammate, on their own laptop, asks their Claude to do the same thing. Neither Claude knows about the other.
>
> **Monday, 10:12 AM.** Both PRs open. Both delete the same function. Your merge conflicts, their test fails, and nobody understands why because the "decisions" live in two separate chat histories nobody has permission to read.

mclaude turns this into:

> **Monday, 10:04 AM.** Your Claude claims work on `fix-auth-middleware-race`. A lock file appears in `.claude/locks/`.
>
> **Monday, 10:07 AM.** Your teammate's Claude tries to claim the same slug, sees the lock, reads the `description`, and tells your teammate: *"ani is already working on this since 10:04, description says 'race condition in session write'. Want to wait, help, or pick another task?"*
>
> **Monday, 10:12 AM.** Your Claude finishes, releases the lock with a summary, writes a handoff. Teammate's Claude reads the handoff, continues the work on a different file, extends the fix with tests.

No merge conflicts. No lost context. No "wait, why did they do it this way?" - the decisions are in the handoff.

---

## The six layers

Each layer solves one specific problem. They are **orthogonal** - use one without the others, or all six together.

### Layer 1 - Work Locks (`mclaude lock`)

*The problem:* Two sessions can accidentally pick up the same task.

*The solution:* Atomic file creation (`O_CREAT | O_EXCL`). Whoever wins the race creates the lock file; everyone else sees it exists and backs off. The holder refreshes a heartbeat every 30 seconds. If the heartbeat goes silent for more than 3 minutes, the lock is considered stale and can be force-released by another session with an audit trail.

```bash
# Session A tries to claim work
$ mclaude lock claim --slug fix-auth-middleware-race \
    --description "Race condition when two requests write the same session key" \
    --files src/auth/middleware.py src/auth/session.py
[lock] claimed fix-auth-middleware-race
  session:  665a44027f424db7
  files:    src/auth/middleware.py, src/auth/session.py
  remember: refresh heartbeat every 30s

# Session B tries the same slug - rejected
$ mclaude lock claim --slug fix-auth-middleware-race --description "same bug I guess"
[lock] fix-auth-middleware-race already held
  by session: 665a44027f424db7
  since:      2026-04-09T14:04:18
  doing:      Race condition when two requests write the same session key
  files:      src/auth/middleware.py, src/auth/session.py
```

Files live in `.claude/locks/active-work/`. Completed work moves to `.claude/locks/completed/` with a summary and timestamp.

### Layer 2 - Handoffs (`mclaude handoff`)

*The problem:* Sessions close. Context dies. The next session has to rediscover what was decided, what failed, and where you left off.

*The solution:* Each session writes its own handoff file with a unique name:

    YYYY-MM-DD_HH-MM_<session-id-first-8>_<slug>.md

For example `2026-04-09_14-32_373d1618_drift-validator-axios.md`. Because every filename is unique, **no two sessions can ever overwrite each other's handoffs**. An append-only `INDEX.md` keeps a running log of all handoffs in chronological order with their status (`ACTIVE`, `RESUMED`, `CLOSED`, `ABANDONED`).

A handoff is structured, not free-form. It always has these sections:

- **Goal** - one or two sentences about what this session was for
- **Done** - concrete results with file paths
- **What did NOT work** - failed approaches with the reason why, so the next session does not rediscover the same dead ends
- **Current state** - what is working, what is broken, what is blocked
- **Key decisions** - choices made, and the reason behind each
- **Next step** - the single most concrete thing to do next

```bash
# Write a handoff at the end of your session
$ mclaude handoff write \
    --session 373d1618 \
    --goal "Fix drift validator false positives on placeholder paths" \
    --done "Added SKIP_PATTERNS for template placeholders" \
           "Tested against real CLAUDE.md - 0 drift" \
    --not-worked "Initial regex was too broad - matched bare filenames as paths" \
    --working "validator runs clean on 8 files" \
    --next-step "Push to public repo with Principle 11 update"
[handoff] written .claude/handoffs/2026-04-09_14-32_373d1618_fix-drift-validator-false-positives.md

# Next session, different day
$ mclaude handoff latest
# Session Handoff - 2026-04-09 14:32
# ... full content of the latest handoff ...
```

#### Rollup handoffs (for long-running projects)

Once a project accumulates 20+ handoffs, reading the full pile at session start becomes the actual startup cost. A **rollup handoff** summarizes a span of prior handoffs and carries a pointer to where the summary ends. Old handoffs are kept for forensic lookup but get a backlink to the rollup that subsumed them. New sessions read: the rollup + only handoffs dated after its boundary.

The pattern is borrowed from [PavelMuntyan/MF0-1984](https://github.com/PavelMuntyan/MF0-1984), whose `thread_summaries.covered_until_message_id` column solves the same problem inside a SQLite schema. mclaude adapts it to markdown frontmatter + git.

**File naming convention:**

    .claude/handoffs/YYYY-MM-DD_HH-MM_rollup_<slug>.md

The `rollup` segment is what makes it discoverable.

**Frontmatter on the rollup file:**

```yaml
---
type: rollup
session: rollup-march-w1-3
covers:
  - 2026-03-01_12-00_session-01_kickoff
  - 2026-03-02_14-30_session-02_auth-refactor
  - ...
  - 2026-03-15_18-00_session-12_ci-pipeline
through: 2026-03-15 18:00
author: ani
---
```

**Backlink on each subsumed handoff:**

The body of the old handoff is not rewritten. Only one frontmatter field is appended:

```yaml
rolled_up_into: 2026-03-16_09-00_rollup_march-weeks-1-3
```

**Session start protocol (currently manual):**

1. `mclaude handoff index` (or read `INDEX.md`)
2. Find the most recent line marked `ROLLUP`.
3. Read that rollup.
4. Read handoffs whose timestamp is **later** than the rollup's `through:` field.
5. Skip any handoff with `rolled_up_into:` set — the rollup already covers it.

**Status:** the frontmatter convention and INDEX.md marker are shipped. A `mclaude handoff rollup --through ...` CLI subcommand is planned, not yet written. Until then, roll up manually: copy the pattern above, list the covered handoffs in the `covers:` field, and append `rolled_up_into:` to each covered handoff's frontmatter.

**When not to use rollups:** projects with fewer than ~10 handoffs, short-lived work where reading every handoff is still cheap, or audit-trail scenarios where truncation would be bad.

For the cross-referenced principle write-up (chronicle + handoff + rollup interaction), see [claude-code-config alternatives/session-handoff.md §F](https://github.com/AnastasiyaW/claude-code-config/blob/main/alternatives/session-handoff.md) and [principle 16](https://github.com/AnastasiyaW/claude-code-config/blob/main/principles/16-project-chronicles.md).

#### Agent attribution in handoffs

When a session delegates work to sub-agents (via `Task()`, subagent skills, or a teammate session in Agent Teams), the handoff used to show only the parent session's identity. The rest was implicit.

Borrowed from MF0-1984's split between `requested_provider_id` (who was asked) and `responding_provider_id` (who actually answered), mclaude adds two optional frontmatter fields to handoffs:

```yaml
invoked_by: ani                        # session that claimed the work (required)
worked_by:                              # all agents that did real work (optional)
  - ani
  - explorer-sub                        # subagent name if a Task() was spawned
  - ani                                 # parent again if it resumed after subagent
```

`worked_by:` is an ordered list — it reads left-to-right like a call stack. If work bounced between the parent session and a subagent several times, each switch is one entry.

**Why it matters:** future sessions reading the handoff can see at a glance whether work was solo or delegated. For the [Principle 10 (Agent Security)](https://github.com/AnastasiyaW/claude-code-config/blob/main/principles/10-agent-security.md) inter-agent trust boundary, this is also the audit trail of which agent touched which decision.

**Status:** optional field — handoffs without `worked_by:` continue to work as before.

### Layer 3 - Memory Graph (`mclaude memory`)

*The problem:* Facts, decisions, and gotchas pile up over weeks. You remember you decided something about JWT vs sessions, but you cannot find where. Grepping 50 chat histories is painful.

*The solution:* A hierarchical knowledge graph stored as nested markdown files. Inspired by [MemPalace](https://github.com/milla-jovovich/mempalace), whose research demonstrated that **raw verbatim text beats LLM-extracted summaries** on retrieval benchmarks (96.6% R@5 vs 85% for extract-based systems). We borrow the hierarchy and the "store the full text, do not summarize" rule; we drop the ChromaDB dependency.

The hierarchy:

```
.claude/memory-graph/
├── core.md                        <- L0 + L1 - always loaded, ~170 tokens
└── wings/
    ├── project-myapp/             <- Wing = a project or major topic
    │   └── rooms/
    │       └── auth-system/       <- Room = sub-topic within a wing
    │           ├── decisions/     <- Hall = type of content
    │           │   └── 2026-04-09_jwt-over-sessions.md
    │           ├── gotchas/
    │           │   └── 2026-04-08_jwt-expiry-race.md
    │           └── references/
    └── common/                    <- shared across projects
```

Each "drawer" file contains the full verbatim text the agent wrote, with frontmatter metadata (`valid_from`, `valid_to`, `superseded_by`). Old decisions are never deleted - when a newer decision replaces them, the old file is marked superseded but kept for history.

```bash
# Save a decision
$ mclaude memory save \
    --wing project-myapp --room auth-system --hall decisions \
    --title "Use JWT instead of server sessions" \
    --content "Decision: JWT with 15-min access + 30-day refresh. Reasoning: stateless, works across multiple backends, refresh rotation gives us revocation..."

# Find it later
$ mclaude memory search "JWT"
wings/project-myapp/rooms/auth-system/decisions/2026-04-09_use-jwt-instead-of-server-sessions.md: title: Use JWT instead of server sessions

# List everything in a room
$ mclaude memory list --wing project-myapp --room auth-system
wings/project-myapp/rooms/auth-system/decisions/2026-04-09_use-jwt-instead-of-server-sessions.md
wings/project-myapp/rooms/auth-system/gotchas/2026-04-08_jwt-expiry-race.md
```

Default search is ripgrep over the files. Zero dependencies. If you later want semantic search, wire up a vector layer that reads the same files - nothing in mclaude has to change.

### Layer 4 - Identity Registry (`mclaude identity`)

*The problem:* Every Claude session is a bare UUID. You cannot say "Claude ani is doing infra, Claude vasya is doing frontend" - they have no names. You cannot route notifications to specific people. You cannot tell at a glance who is doing what.

*The solution:* A small registry that maps human-friendly names to Claude instances.

```bash
# Register your identity (done once per machine / account)
$ mclaude identity register ani \
    --owner "Anastasia" \
    --roles infra ml product \
    --notify telegram:123456789

# Your teammate does the same on their machine
$ mclaude identity register vasya \
    --owner "Vasily" \
    --roles frontend design \
    --notify email:vasya@example.com

# Any session can see who is who
$ mclaude identity list
ani                  id=c0d3-ani-1ddb15f5    owner=Anastasia           last_seen=2026-04-09T14:32:00
vasya                id=c0d3-vasya-a7f3e812  owner=Vasily              last_seen=2026-04-09T12:15:00

# A Claude session picks up its own identity from the environment
$ MCLAUDE_IDENTITY=ani claude
$ mclaude identity whoami
ani id=c0d3-ani-1ddb15f5 owner=Anastasia roles=infra,ml,product
```

The registry is **not for authentication**. It is a naming directory. Trust between instances comes from whatever transport you use to share the project directory (git, ssh, shared drive). mclaude just lets agents refer to each other by name instead of UUID.

Once identities exist, you can build a notification layer on top - for example a small service that watches the `.claude/` directory, detects events like "ani claimed work on auth-rewrite", and sends a Telegram message to Vasily. mclaude does not ship that service; `examples/notifications/` shows how to wire one up.

### Layer 5 - Messages (`mclaude message`)

*The problem:* Handoffs are for end-of-session. But sometimes one Claude needs help from another Claude *right now*, while working - ask a question, request a review, report an error, get an answer back. Before mclaude, users did this manually by writing to a shared file on the desktop and asking both Claudes to read it.

*The solution:* Live inter-session messaging, formalized. Each message is a standalone markdown file with structured frontmatter:

    YYYY-MM-DD_HH-MM-SS_<from>_<to>_<type>_<slug>.md

For example `2026-04-09_14-32-17_ani_vasya_question_how-to-mock-datetime.md`. Different from handoffs - messages are short, addressed, often expecting a reply. Seven types cover the usual needs: `question`, `answer`, `request`, `update`, `error`, `broadcast`, `ack`.

```bash
# Session A asks session B a question
$ mclaude message send \
    --from ani --to vasya --type question \
    --subject "How to mock datetime in pytest" \
    --body "I want to freeze time for a test. What's the cleanest way?"
[message] sent .claude/messages/inbox/2026-04-09_14-32-17_ani_vasya_question_how-to-mock-datetime-in-pytest.md

# Session B checks its inbox
$ mclaude message inbox vasya
  [question ] from ani          | How to mock datetime in pytest

# Session B answers
$ mclaude message send \
    --from vasya --to ani --type answer \
    --subject "Re: How to mock datetime" \
    --reply-to 2026-04-09_14-32-17_ani_vasya_question_how-to-mock-datetime-in-pytest.md \
    --body "Use pytest-freezer. See example below..."

# Anyone can view the full thread
$ mclaude message thread 2026-04-09_14-32-17_ani_vasya_question_how-to-mock-datetime-in-pytest
```

**Broadcasts** reach everyone:

```bash
$ mclaude message send --from system --to "*" --type broadcast \
    --subject "Rebasing main in 5 min" --urgent
```

All sessions scanning their inbox (any recipient) will see the message because `*` is treated as broadcast. On the filesystem, `*` is sanitized to `ALL` so Windows does not reject the filename.

**Multiple mailboxes** let you separate traffic:

```bash
# Route all code review requests to a dedicated mailbox
$ mclaude message send --mailbox review --from ani --to reviewers \
    --type request --subject "PR #42" --body "Ready for review"
```

**Append-only semantics** - you never edit a message after it is sent. Status transitions (read, answered, archived) are new "ack" messages referencing the original. This gives you a complete audit trail: who asked what, when, who answered, when, and nothing can be retroactively rewritten.

The message file format is deliberately simple markdown + YAML frontmatter. This matters because the upcoming `mclaude-hub` network layer uses the **same format** for WebSocket-delivered messages. A local exchange and a network exchange interoperate without translation - you can dump hub messages into local files and they work as local messages, or scan local files and push them to the hub.

### Layer 6 - Code Indexer (`mclaude index`)

*The problem:* A new session that joins a project (or joins a teammate's session) has no map of the codebase. It re-discovers architecture one file at a time, burning tokens and context on orientation.

*The solution:* AST-based scanner that produces two artifacts:

- `code-map.md` - full module/class/function architecture, human-readable
- `llms.txt` - machine-readable index optimized for agent consumption

```bash
$ mclaude index
[index] scanned 47 Python modules, 289 classes, 1,143 functions
  code-map.md: 8.2 KB
  llms.txt:    4.1 KB

# Or via MCP (native Claude Code integration)
# -> mclaude_index tool
```

Code map and memory knowledge index complement each other: code-map describes *structure* (what exists), memory drawers describe *decisions and gotchas* (why it exists that way). Together they replace the 15-minute "let me re-read the codebase" ritual that new sessions do by default.

**Memory knowledge index** (`mclaude memory find-similar`, `mclaude_memory_index`) deduplicates drawers before you create overlapping ones. Word-overlap matching against existing drawers catches "I already wrote about this in another wing" before you fragment the knowledge.

**Wiki-links** (`[[path]]` syntax in drawer bodies) turn the memory graph into a navigable web. `find_backlinks()` traces incoming references. Related section auto-renders from forward links. No database needed.

---

## Quick status

One command to see everything:

```bash
$ mclaude status
[mclaude] status for /home/user/project
  Identity: ani

  Locks (1 active):
    [ACTIVE] fix-auth by 665a4402: Race condition in session write
  Handoffs: 3 total, latest: 2026-04-10_14-32_373d1618_fix-auth-bug.md
  Messages: 2 total in 1 mailbox(es), 1 unread for ani
  Memory: 1 wing(s), 4 drawer(s)
  Identities: ani, vasya
```

---

## Claude Code hooks

mclaude ships four hook scripts that turn advisory coordination into automatic behavior:

| Hook | Event | What it does |
|------|-------|-------------|
| `session_start.py` | SessionStart | Shows latest handoff, unread messages, active locks |
| `pre_edit_lock_check.py` | PreToolUse (Edit) | Warns if the file is locked by another session |
| `remind_handoff.py` | Stop | Reminds to write handoff and release locks |
| `pre_commit_guard.py` | git pre-commit | **Blocks** commits that touch locked files |

Install all Claude Code hooks:

```bash
$ mclaude hooks install --apply
[mclaude] Installing hooks...
  Copied: .claude/hooks/session_start.py
  Copied: .claude/hooks/pre_edit_lock_check.py
  Copied: .claude/hooks/remind_handoff.py
  Settings written to .claude/settings.json
[mclaude] Hooks installed. Restart Claude Code to activate.
```

Install the git pre-commit guard separately:

```bash
$ mclaude hooks install-guard
[hooks] pre-commit guard installed at .git/hooks/pre-commit
```

The pre-commit guard is the enforcement layer. Locks are advisory by default - any session *can* edit a locked file. But with the guard, `git commit` refuses to accept changes to files claimed by another session. This is the same pattern used by MCP Agent Mail: runtime warnings + commit-time enforcement.

---

## Active mail

mclaude mail turns passive message checking into automatic delivery. Instead of manually running `mclaude message inbox`, Claude sees new messages **on every user prompt** via the `UserPromptSubmit` hook.

```bash
# Session A asks session B a question
$ mclaude mail ask vasya "What's the API schema for auth?"
[mail] question sent to vasya
  thread: 2026-04-11_14-32-17_ani_vasya_question_api-schema-auth

# Session B - on the NEXT user prompt, the hook auto-shows:
# [mclaude mail] 1 new message(s) for vasya:
#   [question] from ani: What's the API schema for auth?
#     > What's the API schema for auth?

# Session B replies
$ mclaude mail reply 2026-04-11_14-32-17_ani --body "JWT with 15-min expiry, refresh token 30d"

# Session A - on the next prompt, sees the answer automatically
```

The `mail.wait_for_reply()` API also supports blocking wait:

```python
from mclaude.mail import Mail
mail = Mail(identity="ani")
thread = mail.ask("vasya", "How to deploy?")
answer = mail.wait_for_reply(thread, timeout=120)  # blocks up to 2 min
```

**Hub sync**: if `MCLAUDE_HUB_URL` and `MCLAUDE_HUB_TOKEN` are set, `mclaude mail sync` pushes local messages to hub and pulls hub messages locally. The hook does this automatically before each check.

---

## MCP server (native Claude Code integration)

mclaude ships an MCP server that lets Claude Code call tools directly - no `Bash("mclaude lock list")` parsing needed:

```json
// Add to .mcp.json or .claude/settings.json
{
  "mcpServers": {
    "mclaude": {
      "command": "python",
      "args": ["-m", "mclaude.mcp_server"]
    }
  }
}
```

Then Claude Code can call `mclaude_lock_claim`, `mclaude_handoff_latest`, `mclaude_memory_search`, etc. and get structured JSON back. 16+ tools covering all six layers (including `mclaude_index`, `mclaude_memory_find_similar`, `mclaude_memory_index`).

---

## How you actually use this

### Single-session setup

You do not need mclaude if you only ever run one Claude chat. But you probably already lose context between sessions, so `handoff` alone is worth it:

1. `pip install mclaude` (or clone this repo and run from source)
2. In your project, add the rule files: `cp -r rules .claude/rules/`
3. At the end of a long session, tell Claude: *"prepare handoff"*. It writes `.claude/handoffs/...md`.
4. At the start of the next session, tell Claude: *"check for handoff"* or wire up the SessionStart hook in `hooks/`.

### Two chats on the same machine

You get race protection:

1. Set up as above.
2. When you open a second chat, tell it: *"use mclaude to claim work on X"*.
3. If the first chat is still active on the same slug, the second chat sees the lock and asks you what you want to do.

### Multi-person teams

You get full collaboration:

1. Put your project under git.
2. Add `.claude/` to the repo (it is designed to be committed - all files are plain text and merge cleanly).
3. Each person registers their identity once with a unique name.
4. Everyone pulls before claiming work, pushes after releasing.
5. Handoffs become team memory. The knowledge graph becomes team documentation.
6. Optional: wire up a notification bot to push events.

### With MemPalace or another vector store

mclaude memory is grep-first on purpose. If you want semantic search, MemPalace, ChromaDB, or a local embedding server can sit **on top of** the memory graph files without any change to mclaude:

1. Point MemPalace (or your tool of choice) at `.claude/memory-graph/`.
2. Let it index the markdown files.
3. Claude queries via both - grep for exact matches, vector search for semantic ones.

The files are the source of truth. Vector indexes are derived and can be rebuilt any time.

---

## Design principles

These are non-negotiable. If a contribution breaks one of these, it is rejected.

1. **File-based, zero external dependencies.** Everything is markdown, JSON, or TOML on disk. No databases, no MCP servers required, no network calls. This guarantees mclaude works in any environment that has Python 3.9+ and a filesystem - including CI runners, airgapped machines, and emergency SSH sessions.

2. **Files are the source of truth.** No derived state lives only in memory. You can delete the Python package and still understand everything in `.claude/` by reading the files with a text editor.

3. **Append-only where possible, atomic otherwise.** Handoffs and memory drawers are append-only - no two sessions can overwrite each other's writes. Locks are atomic via `O_CREAT | O_EXCL` - the OS guarantees only one session wins a race.

4. **Graceful degradation.** Each of the four layers works independently. If you only use locks, handoffs are unaffected. If the memory module has a bug, handoffs keep working. If mclaude disappears entirely, the `.claude/` directory is still a readable archive of your project's history.

5. **Human-readable formats.** Markdown for narrative, JSON for structured metadata, TOML for config. No pickle, no binary blobs, no opaque schemas. If you want to read or edit something by hand, you can.

6. **Production-grade from day one.** We do not ship MVPs that require rework. Every file format is versioned. Every write is atomic. Every race is prevented, not worked around. Every failure mode is named, tested, and documented.

---

## Architecture at a glance

```
your-project/
└── .claude/
    ├── locks/
    │   ├── active-work/
    │   │   ├── fix-auth-bug.lock
    │   │   ├── fix-auth-bug.heartbeat
    │   │   └── fix-auth-bug.metadata.json
    │   └── completed/
    │       └── fix-auth-bug_2026-04-09_15-30.md
    │
    ├── handoffs/
    │   ├── 2026-04-09_14-32_373d1618_fix-auth-bug.md
    │   ├── 2026-04-09_16-47_b858f500_dashboard-refactor.md
    │   └── INDEX.md                              <- append-only
    │
    ├── memory-graph/
    │   ├── core.md                               <- L0 + L1, always loaded
    │   └── wings/
    │       ├── project-myapp/
    │       │   └── rooms/
    │       │       └── auth-system/
    │       │           ├── decisions/
    │       │           └── gotchas/
    │       └── common/
    │
    └── registry.json                             <- Layer 4: who is who
```

Everything is text. Everything is atomic. Everything is grep-friendly.

---

## How mclaude fits the 2026 orchestration landscape

Multi-Claude orchestration went from niche to crowded in one quarter. Since February 2026, at least a dozen open-source projects have shipped variations on "run several Claude sessions together." Anthropic itself published experimental **Agent Teams** in Claude Code. This section sets expectations on where mclaude fits — what it does that the others do not, and what it deliberately does not try to do.

### The landscape (April 2026)

| Project | Differentiator | Best for |
|---|---|---|
| **[Claude Agent Teams](https://code.claude.com/docs/en/agent-teams)** | First-party, native to Claude Code. Team lead + teammates with separate context windows, shared task list. Experimental, disabled by default. | In-Claude coordination when you want Anthropic to own the protocol. |
| **[affaan-m/claude-swarm](https://github.com/affaan-m/claude-swarm)** | Dependency-graph task decomposition. Parallel spawn for independent subtasks. Rich terminal UI. Built with Claude Agent SDK. | Breaking one large task into many parallel agents. |
| **[nwiizo/ccswarm](https://github.com/nwiizo/ccswarm)** | Git worktree isolation per agent. Specialized roles. | Collaborative development where agents need filesystem-level isolation. |
| **[dsifry/metaswarm](https://github.com/dsifry/metaswarm)** | Multi-tool runtime (Claude Code + Gemini CLI + Codex CLI). 18 agents / 13 skills / 15 commands. Self-improving. TDD-enforced. | Projects that want multiple model vendors in the same workflow. |
| **[barkain/claude-code-workflow-orchestration](https://github.com/barkain/claude-code-workflow-orchestration)** | Claude Code plugin layered over Agent Teams when available. Native plan mode integration. | Drop-in orchestration on top of Anthropic's own primitives. |
| **[am-will/swarms](https://github.com/am-will/swarms)** | Explicit task-dependency declarations. Orchestrator derives parallelism from the graph. | Task queues with clear upstream/downstream edges. |
| **[ruvnet/ruflo](https://github.com/ruvnet/ruflo)** | Distributed swarm intelligence. RAG integration. Enterprise-oriented. | Production deployments with scale concerns. |
| **[desplega-ai/agent-swarm](https://github.com/desplega-ai/agent-swarm)** | Docker container per agent. Lead delegates to workers. | Strong isolation where a rogue agent cannot reach beyond its box. |
| **[swarmclawai/swarmclaw](https://github.com/swarmclawai/swarmclaw)** | Self-hosted runtime, 23+ LLM providers. | Provider-agnostic orchestration (Claude + GPT + Gemini + Ollama). |
| **Multica** (closed OSS) | Open-source analog to Claude Managed Agents. Task lifecycle + concurrency + multi-model. | Teams that outgrew managed agents on claude.ai. |
| **mclaude (this project)** | Knowledge infrastructure: handoffs / memory graph / identity / project KB / findings inbox. File-based, zero-dependency core. | Multi-session continuity when sessions outlive any one conversation. |

### Where mclaude's edge is

Orchestration — "spawn N agents, give them tasks, collect outputs" — is now a solved problem. Anthropic's Agent Teams does it natively, and each project in the table does it with a different flavor.

**What is not solved is knowledge management across sessions.** When session 3 inherits work from session 1 that session 2 already continued, whose memory wins? What does the incoming agent read first? How do you prevent the same mistake in session 4 that session 2 already figured out? These are questions about **persistent structured context**, not about coordination.

mclaude's differentiator is the answer to those questions:

- **Handoffs with "NOT worked" sections** — dead ends do not get rediscovered.
- **Memory graph with `superseded_by` versioning** — old decisions are findable but not misleading.
- **Project KB as an MkDocs scaffold** — living documentation served locally that the agent reads at start, not a static wiki.
- **Findings inbox** (`_inbox/findings/`) — discrete capture zone for insights that do not yet belong in any specific memory file. Currently a manual workflow (markdown files only); a CLI surface is planned, not shipped.
- **Identity registry** — Claude sessions get human names, so handoffs read as "ani left notes for vasya" not "UUID-a went offline."

All five of these knowledge primitives live as files in the repo. No daemon. No database. No cloud account. The six mclaude layers (locks + handoffs + memory + registry + messages + indexer) continue to provide coordination primitives — the orchestrators listed above can be layered on top of them, not in place of them.

### When to pick what

- **Start-up overhead is your concern** → Agent Teams (native) or barkain plugin (thin wrapper).
- **Task decomposition is your concern** → claude-swarm (dependency graph) or am-will/swarms (explicit declarations).
- **Isolation is your concern** → ccswarm (worktrees) or desplega-ai agent-swarm (containers).
- **Multi-vendor LLMs are your concern** → metaswarm or swarmclaw.
- **Cross-session knowledge continuity is your concern** → mclaude (by design). Pair it with one of the orchestrators if you also need coordination primitives mclaude does not ship.

### Adoption ideas we are considering

Reading the other projects has generated work items:

- **Git worktree per session** (borrowed from ccswarm): we already name locks with worktree paths; the next step is to make the lock claim actually create a worktree, so isolation becomes real instead of naming.
- **Quality gate phase** (borrowed from claude-swarm + metaswarm): today handoffs are advisory; a formal gate step between "work claimed" and "work released" would make review mandatory, not optional.
- **Dependency graph on top of handoffs**: the handoff INDEX.md could carry `depends_on:` pointers, turning multi-session work into an explicit DAG.
- **Plugin mode that rides on Agent Teams** when that API ships stably: let Anthropic own coordination, let mclaude own knowledge.

None of these are in 0.6.0. They are openly tracked — contributions welcome.

---

## What this is not

- **Not only an MCP server.** mclaude ships an MCP server as one of its integration surfaces (see "MCP server" section above, 20+ tools), but the core is a CLI + Python library. Everything works without any server process running — MCP is additive, not required.
- **Not an authentication system.** The registry names instances; it does not verify them. Trust comes from the transport.
- **Not a replacement for Claude Code.** It is a thin layer on top that solves coordination problems Claude Code itself does not attempt.
- **Not a MemPalace fork.** We borrow ideas from their research (hierarchical graph, raw verbatim beats extraction) but implement them as plain files without their dependencies.
- **Not a silver bullet.** If two humans disagree about the right design, two Claudes reading the same handoffs will still disagree. mclaude makes the disagreement *visible* - it does not decide who is right.

---

## Status

- **Version:** 0.6.0
- **Stability:** alpha - the file formats are stable (we commit to not breaking them), but CLI flags and Python API may evolve in 0.x
- **Tested on:** Python 3.9+, Windows 10/11, macOS, Linux
- **Dependencies:** standard library only (argparse, dataclasses, pathlib, json, re, os, time, uuid)
- **Tests:** 190+ tests, all passing (core + hub + bridge + audio + indexer)

---

## Installation

```bash
# Core only (zero dependencies, file-based coordination)
pip install mclaude

# With hub server (FastAPI + SQLite, for multi-machine teams)
pip install mclaude[hub]

# With desktop client (PyQt6 tray icon, notifications)
pip install mclaude[client]

# With voice I/O (STT via faster-whisper, TTS via pyttsx3)
pip install mclaude[audio-full]

# Everything
pip install mclaude[hub,client,audio-full]

# Development
git clone https://github.com/AnastasiyaW/mclaude
cd mclaude
pip install -e ".[hub,dev]"
```

After installation, `mclaude` is available as a command in your shell. Run `mclaude status` to see all layers at a glance.

---

## Optional extensions

The core six layers work with zero dependencies. These extensions add network, desktop, and audio capabilities:

### Hub server (`mclaude.hub`)

A central relay so sessions on different machines can share state. FastAPI + SQLite + WebSocket broadcast.

```bash
pip install mclaude[hub]
uvicorn mclaude.hub.server:create_app --factory --host 0.0.0.0 --port 8080
```

If the hub is offline, everything degrades to local file mode. Nothing hard-fails.

### Claude bridge (`mclaude.bridge`)

Connects a Claude Code session to the hub. Falls back to file-based mclaude when network is down.

```python
from mclaude.bridge import BridgeClient, BridgeConfig
bridge = BridgeClient(BridgeConfig(
    hub_url="https://your-hub.example.com",
    token="your-bearer-token",
    identity="ani",
))
```

### Desktop client (`mclaude.client`)

System tray icon with native notifications, voice input (STT), and text-to-speech (TTS).

```bash
pip install "mclaude[client,audio-full]"
python -m mclaude.client
```

### Project Knowledge Base (`project-kb/`)

MkDocs scaffold for per-project knowledge bases. Multiple Claude sessions read the KB before implementing anything.

```bash
python project-kb/scaffold.py --name "my-project" --domains "backend,frontend,api"
```

See [docs/architecture.md](docs/architecture.md) for the full system architecture.

---

## Relation to architectural principles

mclaude is the **production implementation** of several patterns documented abstractly in the [claude-code-config](https://github.com/AnastasiyaW/claude-code-config) principles repo:

| claude-code-config principle | mclaude layer | What it means for you |
|---|---|---|
| [18 - Multi-Session Coordination](https://github.com/AnastasiyaW/claude-code-config/blob/main/principles/18-multi-session-coordination.md) | Layer 1 (Locks), Layer 2 (Handoffs) | mutex for exclusive resources + append-only handoffs for shared state |
| [19 - Inter-Agent Communication](https://github.com/AnastasiyaW/claude-code-config/blob/main/principles/19-inter-agent-communication.md) | Layer 5 (Messages), Active mail | email-semantics inter-agent messaging with threading, delivery receipts, sent folder |
| [07 - Codified Context](https://github.com/AnastasiyaW/claude-code-config/blob/main/principles/07-codified-context.md) | Layer 3 (Memory Graph), Layer 6 (Code Indexer) | runtime-config treatment of memory + on-demand architectural context |
| [04 - Deterministic Orchestration](https://github.com/AnastasiyaW/claude-code-config/blob/main/principles/04-deterministic-orchestration.md) | All hooks and CLI commands | state lives in files; shell scripts for mechanical steps |

If you want the theory and trade-offs, read the principles. If you want `pip install mclaude` that works today, you are already in the right repo.

---

## License

MIT. Use it, fork it, rewrite it, put it in your proprietary stack. If you find a bug or have an idea, open an issue - but do not feel obligated to upstream every change.

---

## Credits and inspiration

- **MemPalace** (Milla Jovovich, Ben Sigman) - hierarchical memory graph and the raw-verbatim-over-extraction principle
- **Paperclip** - heartbeat pattern and file-based coordination ideas
- **DeerFlow 2.0** (ByteDance) - thinking about the race condition problem they solve with sandbox isolation and the tradeoffs involved
- **Claude Code** (Anthropic) - the harness this layer is built to assist
- **Claw Code** (Sigrid Jin) - reminder that transparency in agent infrastructure is a feature, not a compromise
