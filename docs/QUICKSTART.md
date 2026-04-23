# Quickstart: run mclaude on your own project in 10 minutes

This guide is for a Claude Code session (or a human) who wants to set up
the full mclaude stack from scratch. Follow top to bottom; each step
takes 1-3 minutes and builds on the previous one.

**Every command in this guide exists in the current CLI (run `mclaude
<command> --help` to see its flags).** If a command here fails, that is a
bug - open an issue.

---

## Step 0: prerequisites

- Python 3.9 or newer (`python --version`)
- A project directory where you want to install mclaude
- Optional: `git` for versioning the `.claude/` artifacts

No API keys, no cloud account, no external services needed for the core.

---

## Step 1: install the package

From a checkout (primary path - the library is still pre-PyPI):

```bash
git clone https://github.com/AnastasiyaW/mclaude.git
cd mclaude
pip install -e .
```

Verify:

```bash
mclaude --help
mclaude demo --no-pause   # ~30-second self-test, writes a temp directory
```

`mclaude demo` walks through all six layers with two simulated sessions
(`ani` and `vasya`) and prints a Mermaid diagram. If it completes with
`STEP 17/17` and no tracebacks, your install is healthy.

---

## Step 2: initialize your project

```bash
cd /path/to/your/project
mclaude identity register ani --owner "Your Name" --runtime claude-code
```

This creates `.claude/registry.json` with your first identity. The
`--runtime` flag tags this identity as a Claude Code session (other
valid values: `codex`, `cursor`, `opencode`, `hermes`, or any custom
string your team uses). In heterogeneous teams it lets you filter work
by agent family.

Verify:

```bash
mclaude identity list
mclaude identity whoami    # reads MCLAUDE_IDENTITY env var
```

For `whoami` to show something, `export MCLAUDE_IDENTITY=ani` first.

---

## Step 3: try a work lock

When a Claude session starts touching something sensitive, it should
claim a lock first so a parallel session does not do the same work.

```bash
mclaude lock claim \
  --slug fix-auth-middleware-race \
  --description "Fix race in session write path"
```

This writes `.claude/locks/fix-auth-middleware-race.json` with an
atomic `O_CREAT|O_EXCL`. A second session (or person) trying to claim
the same slug will see who holds it and fail fast.

Release when done:

```bash
mclaude lock release --slug fix-auth-middleware-race
```

List everything currently claimed:

```bash
mclaude lock list
```

---

## Step 4: write your first handoff

When a session finishes (or hits the context limit), it should write a
structured summary so the next session picks up cleanly.

Via CLI:

```bash
mclaude handoff write \
  --session ani-sess-1 \
  --goal "Fix drift validator" \
  --done "Updated Principle 09" "Pinned min-release-age=7" \
  --not-worked "Tried v1.14.2 pin - wrong version" \
  --next-step "Push update to public repo" \
  --refs vikunja:1247 linear:ENG-42
```

The `--refs` flag stores opaque `provider:id` tokens so external
scripts can cross-link the handoff to your task tracker. mclaude
itself never calls any tracker - see step 9.

Via Python:

```python
from pathlib import Path
from mclaude.handoffs import Handoff, HandoffStore

HandoffStore(project_root=Path(".")).write(Handoff(
    session_id="ani-sess-1",
    goal="Fix drift validator",
    done=["Updated Principle 09"],
    not_worked=["Tried v1.14.2 pin - wrong version"],
    next_step="Push update to public repo",
    refs=["vikunja:1247"],
))
```

List and read handoffs:

```bash
mclaude handoff list
mclaude handoff latest          # prints the newest one
mclaude handoff read <slug-fragment>
```

---

## Step 5: save something to memory

Memory drawers are long-lived notes - decisions, gotchas, reference
data. The address space is **wing / room / hall**, NOT a path. Every
drawer has these coordinates, so the CLI takes them as separate flags:

```bash
mclaude memory save \
  --wing infrastructure \
  --room gpu-servers \
  --hall references \
  --title "GPU training servers" \
  --content "Internal network. Key in ~/.ssh/team-key. Non-standard port. See infra docs."
```

- `--wing` is the broadest category (infrastructure, product, people, ...)
- `--room` is the specific topic inside the wing
- `--hall` is the type of content: `facts`, `decisions`, `gotchas`,
  `references`, `discoveries`, `preferences` (default: `facts`)

List what you have:

```bash
mclaude memory list
mclaude memory list --wing infrastructure
mclaude memory search "gpu server"    # substring grep across drawers
mclaude memory core                   # print always-loaded L0+L1 core
```

---

## Step 6: enable SessionStart awareness

`mclaude hooks install` generates the hook configuration Claude Code
needs. Run it once per project:

```bash
mclaude hooks show           # prints recommended settings.json fragment
mclaude hooks install --apply   # writes it into .claude/settings.json
```

After this, every new Claude Code session in the project prints
`mclaude status` at start - summary of locks, recent handoffs, live
sessions, unread messages.

To see the overview any time:

```bash
mclaude status
```

---

## Step 7: heartbeat for live visibility

For long-running sessions other sessions may want to see "she is still
working, not dead." From your Claude workflow, call every few minutes:

```python
from pathlib import Path
from mclaude.heartbeat import beat

beat(
    project_root=Path("."),
    identity="ani",
    session_id="ani-sess-1",
    activity="running tests on auth-race fix",
    lock_slugs=["fix-auth-middleware-race"],
)
```

Other sessions query it:

```python
from pathlib import Path
from mclaude.heartbeat import list_live, list_stale

for b in list_live(Path(".")):
    print(f"{b.identity}: {b.current_activity} ({b.runtime})")

# Sessions that missed their beat for 10+ min
for b, age in list_stale(Path(".")):
    print(f"{b.identity} stale for {age}s - lock {b.lock_slugs} may be reclaimable")
```

The heartbeat module is currently Python-only (no CLI subcommand yet).
Call `beat()` from a periodic tool call in your agent workflow, or
wrap it in a tiny cron script.

---

## Step 8: second person on the team

When someone else opens Claude on the same project:

```bash
mclaude identity register vasya --owner "Vasily" --runtime codex
```

Now locks, handoffs, messages, and heartbeats are all attributed. When
Vasya's Claude tries to claim a lock Ani already holds, it sees Ani's
description and decides whether to wait, help, or pick another task.

For cross-machine work, commit `.claude/` to git:

```bash
git add .claude/
git commit -m "Initial mclaude state"
```

`.claude/handoffs/INDEX.md` and `.claude/memory/` are meant to be
versioned. `.claude/locks/` contains short-lived state - some teams
gitignore it, others version it for auditing. Either works.

---

## Step 9: pair with claude-code-config (recommended)

mclaude handles coordination (who does what, when). It deliberately does
NOT ship safety hooks or architectural principles - that job belongs to
its companion repository,
[claude-code-config](https://github.com/AnastasiyaW/claude-code-config).

Why pair them: parallel Claude sessions share a filesystem and a git
repo. One session can `rm -rf` or `git push --force` state another
session is actively using. mclaude's locks prevent double-work, but
they do not stop a confused agent from typing a destructive command.
claude-code-config's `PreToolUse` hooks do.

Install as a Claude Code plugin (simplest):

```bash
claude plugin install https://github.com/AnastasiyaW/claude-code-config
```

Or, if you prefer to keep copies in your own tree:

```bash
git clone https://github.com/AnastasiyaW/claude-code-config ~/claude-code-config
# Copy the hooks that matter most for multi-session work
mkdir -p ~/.claude/hooks
cp ~/claude-code-config/hooks/destructive-command-guard.py   ~/.claude/hooks/
cp ~/claude-code-config/hooks/git-destructive-guard.py       ~/.claude/hooks/
cp ~/claude-code-config/hooks/git-auto-backup.py             ~/.claude/hooks/
cp ~/claude-code-config/hooks/secret-leak-guard.py           ~/.claude/hooks/
cp ~/claude-code-config/hooks/session-drift-validator.py     ~/.claude/hooks/
```

Register them in `~/.claude/settings.json` under `hooks.PreToolUse` /
`hooks.SessionStart` (see `hooks/README.md` in claude-code-config for
the exact JSON snippet).

The mclaude layers gain specific pairings:

| mclaude layer | Pair with | Why |
|---|---|---|
| Locks (Layer 1) | `git-destructive-guard`, `git-auto-backup` | Locks stop you from picking the same task. Hooks stop you from destroying the shared repo. |
| Handoffs (Layer 2) | `secret-leak-guard`, `session-drift-validator` | Handoff contents should not leak secrets; drift validator catches broken paths the previous session left behind. |
| Memory (Layer 3) | `proof-verify` skill | KB-aware verification reads memory drawers as conformance source. |
| All layers | `destructive-command-guard` | Any session can type `rm -rf`. The hook blocks before damage. |

Verify the pair works:

```bash
# Try a destructive command - it should be blocked
echo "rm -rf /" | claude chat     # if the hook is installed, agent refuses
```

Full list of 23 architectural principles and 14 hooks is in
claude-code-config's README. Start with the five hooks above - they
cover 90% of multi-session failure modes we have seen in production.

---

## Step 10: pick your task-tracker integrations

mclaude does NOT call any task tracker (Vikunja, Linear, Jira, GitHub
Projects). It deliberately stays generic so you can plug any tracker in
with a small script.

**Pattern for integrating any tracker:**

1. When writing a handoff, include the tracker id in the `refs` field
   (e.g. `--refs linear:ENG-42 gh:123`). mclaude renders a `## Refs`
   section in the handoff markdown; it does nothing else with the token.

2. Write (or reuse) a ~150-line script in your own repo that scans
   `.claude/handoffs/` for tokens matching your tracker's pattern and
   posts cross-reference comments back.

3. Keep the script in your private or team repo, not in mclaude. Gives
   you full control of credentials and API quirks.

See [`examples/integrations/`](../examples/integrations/) for a
ready-to-copy template (`handoff_refs_to_tracker.py`) and an example
adaptation for Linear.

---

## Step 11: how much value can you get out of this?

mclaude scales with how many of its layers you actually use:

| You use | What you gain |
|---|---|
| Locks only | No more "I deleted the same function you just deleted" |
| Locks + handoffs | Sessions pick up each other's work cleanly |
| + memory | No more re-discovering the same gotcha every month |
| + heartbeat | Real-time visibility into who is alive and doing what |
| + messages (`mclaude mail`) | Specific cross-session requests ("can you review?") |
| + registry | Named actors in every log entry (human-readable history) |
| + indexer (`mclaude index`) | New sessions skip the 15-min "re-read the codebase" ritual |
| + hub | Cross-machine and cross-network sharing, optional desktop UI |

Minimum viable setup is steps 0-6 (install, identity, lock, handoff,
memory, hooks). That is ~10 minutes of setup and already prevents the
majority of parallel-session problems.

For a solo person doing short tasks, steps 1-4 alone are often enough.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `mclaude: command not found` | Not installed in active venv | `which python` - install in the right env |
| Lock claim succeeds but other session does not see it | Different `project_root` | Both sessions must run from the same directory |
| Memory drawer not found | Wrong wing/room/hall coordinates | Use `mclaude memory list` to discover |
| Handoff INDEX.md shows wrong time | INDEX is append-only, not edited in place | Status transitions append a new line; read the latest for each session |
| `beat()` complains about str vs Path | Old library version | `project_root` accepts `str | Path` since the current release |

---

## Learn more

- `docs/architecture.md` - how the pieces fit together under the hood
- `docs/security-audit-recipe.md` - threat model and review checklist
- `examples/` - real-world integration recipes (notifications, bridges)
- `examples/integrations/` - copyable scripts for Linear, Jira, Vikunja, etc.
- `mclaude demo` - always-runnable self-test that walks all six layers

If you hit a case that is not covered, open an issue.
