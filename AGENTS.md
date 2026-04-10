# AGENTS.md

This is mclaude - a multi-session collaboration layer for AI coding agents. It provides five file-based primitives (locks, handoffs, memory graph, identity registry, messages) plus Claude Code hooks integration that let parallel sessions coordinate without losing data.

## Purpose for you, the agent

When a user has multiple Claude sessions open, or works in a team, or switches machines, use mclaude to:

- Claim work atomically before editing files (`mclaude lock claim`)
- Refresh your heartbeat every 30 seconds while the lock is held
- Release the lock with a summary when done
- Write a handoff at the end of long sessions (`mclaude handoff write`)
- Read prior handoffs at the start of a new session (`mclaude handoff latest`)
- Save durable decisions and gotchas to the memory graph (`mclaude memory save`)
- Register the user's identity the first time they run you

## Trigger phrases the user will say

When the user sends any of these, act immediately without asking for clarification:

**Claim work:**
- "claim <slug>" or "claim work on <description>"
- "lock <slug>"
- "are we already working on this?"

**Write handoff:**
- "prepare handoff"
- "save context for new chat"
- "write handoff"
- "handoff this session"
- "closing session, write handoff"

**Read handoff:**
- "what did we do last session?"
- "continue from handoff"
- "is there a handoff?"

**Memory:**
- "save this decision"
- "remember that..."
- "what did we decide about X?"

**Identity:**
- "whoami"
- "register me as <name>"
- "list team members"

## Commands

```
mclaude status                        # one-command overview of all layers

mclaude lock claim --slug <slug> --description "..." [--files a.py b.py]
mclaude lock release <slug> [--summary "..."]
mclaude lock status <slug>
mclaude lock list
mclaude lock heartbeat <slug>

mclaude handoff write --session <id> --goal "..." [--done ... --not-worked ...]
mclaude handoff latest
mclaude handoff list [--status ACTIVE|CLOSED|...]
mclaude handoff read <filename-or-slug>

mclaude memory save --wing <w> --room <r> --hall <h> --title "..." --content "..."
mclaude memory search <query>
mclaude memory list [--wing <w>]
mclaude memory core

mclaude identity register <name> --owner "..."
mclaude identity list
mclaude identity whoami

mclaude message send --from <name> --to <name> --type question --subject "..."
mclaude message inbox <name>
mclaude message thread <thread-id>

mclaude hooks install --apply          # install Claude Code hooks
mclaude hooks install-guard            # install git pre-commit guard
mclaude hooks show                     # print hook config for manual setup
```

## Rules

1. **Never overwrite another session's handoff.** Filenames are unique by timestamp + session ID + slug, so this is structurally impossible - but if you see a file named like yours, back off and report.

2. **Always write a handoff before a long session ends** if the user gave you more than 15 minutes of work. Include what did NOT work - this section is the most valuable part.

3. **Never silently force-release a lock.** If you find a stale lock, tell the user and ask. Force-release leaves a permanent audit record in `.claude/locks/completed/`.

4. **Heartbeat or release.** If you claim a lock, either refresh its heartbeat at least every 2 minutes or release it. Orphan locks get force-released by the next session and create noise.

5. **Memory is append-only.** When a decision is superseded, save a new drawer and use `graph.supersede(old, new)` - do not delete the old file.

6. **Raw verbatim.** When saving to memory, store the actual text that captures the decision, not a three-word summary. MemPalace research shows extraction loses 10+ percentage points of recall accuracy.

## Files you will read often

- `.claude/handoffs/INDEX.md` - chronological log of all handoffs
- `.claude/memory-graph/core.md` - L0 + L1 always-loaded facts
- `.claude/registry.json` - who is who
- `.claude/locks/active-work/*.metadata.json` - what is currently claimed

## Files you should not touch directly

- Any `.lock` file - they are managed by `mclaude lock` atomic commands
- Any `.heartbeat` file - only refreshed by `mclaude lock heartbeat`
- Handoff files written by other sessions - read only, never edit

## Context engineering notes

This file is under 120 lines - fits in a single cached prompt prefix. It uses the AGENTS.md standard (Linux Foundation / Agentic AI Foundation) so it works with any agent that reads AGENTS.md, not only Claude Code. Keep it stable; do not add dynamic content like timestamps.
