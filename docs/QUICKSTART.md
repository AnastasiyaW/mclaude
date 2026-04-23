# Quickstart: run mclaude on your own project in 10 minutes

This guide is for a Claude Code session (or a human) who wants to set up
the full mclaude stack from scratch. Follow top to bottom; each step
takes 1-3 minutes and builds on the previous one.

At the end you will have:

- Parallel Claude sessions that cannot overwrite each other's work
- Session-to-session handoffs so no context is lost when you close a chat
- A navigable memory graph the agent can read when it opens a new session
- Named identities so you can see "ani is on auth, vasya is on frontend"
- Live heartbeats so other sessions can tell who is active right now
- Optional: voice in/out, tray notifications, cross-machine sync via the hub

No database. No server required (the hub is optional). Everything is
plain markdown and JSON inside `.claude/`.

---

## Step 0: prerequisites

- Python 3.9 or newer (`python --version`)
- A project directory where you want to install mclaude
- Optional: `git` for versioning the `.claude/` artifacts

No API keys, no cloud account, no external services needed for the core.

---

## Step 1: install the package

```bash
pip install mclaude
```

Or from a checkout (if you want to modify the source):

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

If `mclaude demo` walks through 17 steps and prints a Mermaid diagram,
the package is healthy.

---

## Step 2: initialize your project

```bash
cd /path/to/your/project
mclaude identity register ani --owner "Your Name" --runtime claude-code
```

This creates `.claude/registry.json` with your first identity. The
`--runtime` flag tags this identity as a Claude Code session (other
valid values: `codex`, `cursor`, `opencode`, `hermes`, or any custom
string your team uses).

Verify:

```bash
mclaude identity list
mclaude identity whoami
```

---

## Step 3: try a work lock

When a Claude session starts touching something sensitive, it should
claim a lock first so a parallel session does not do the same work.

```bash
mclaude lock claim fix-auth-middleware-race \
  --description "Fix race in session write path" \
  --identity ani
```

This writes `.claude/locks/fix-auth-middleware-race.json`. A second
session (or person) trying to claim the same slug will see who holds it
and why.

Release when done:

```bash
mclaude lock release fix-auth-middleware-race
```

---

## Step 4: write your first handoff

When a session finishes (or hits the context limit), it should write a
structured summary so the next session picks up cleanly.

Easiest: ask your Claude to do it. The trigger phrase is wired into the
default rule in `.claude/rules/session-handoff.md`. In the Claude Code
chat just type:

    prepare handoff

Claude writes `.claude/handoffs/<timestamp>_<session>_<slug>.md` and
appends to `.claude/handoffs/INDEX.md`. Close the chat. When you open a
new one in the same project, it reads the handoff automatically (if the
SessionStart hook is enabled - see step 6).

Manually from code:

```python
from mclaude.handoffs import Handoff, HandoffStore

HandoffStore(project_root=".").write(Handoff(
    session_id="ani-sess-42",
    goal="Fix drift validator",
    done=["Updated Principle 09", "Pinned min-release-age=7"],
    not_worked=["Tried v1.14.2 pin - wrong version"],
    next_step="Push update to public repo",
))
```

---

## Step 5: save something to memory

Memory drawers are long-lived notes about your project - decisions,
gotchas, reference data. Any session can read them; any session can add
to them.

```bash
mclaude memory save infrastructure/gpu-servers \
  "GPU training servers run on internal network.
   Identity file at ~/.ssh/team-key. Port non-standard.
   See infra docs for connection details."
```

Creates `.claude/memory/infrastructure/gpu-servers.md`. Use
`[[other-drawer]]` wiki-links inside the body to connect drawers; they
become a navigable graph over time.

Find similar existing drawers before writing a new one:

```bash
mclaude memory find-similar "gpu server connection"
```

---

## Step 6: enable SessionStart awareness

Claude Code can run a hook at session start. Add to your project's
`.claude/settings.json` (or `~/.claude/settings.json` for global):

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "mclaude status --brief"
      }]
    }],
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "mclaude handoff remind --stale-after 900"
      }]
    }]
  }
}
```

Now every new session sees "2 active locks, 1 handoff ready to resume,
ani active 3 min ago" without you doing anything. And every session
close reminds you to write a handoff if the session was substantial.

---

## Step 7: heartbeat for live visibility (optional)

For long-running sessions you may want other sessions to see "she's
still working, not dead." Add to your Claude workflow a call every few
minutes:

```python
from mclaude.heartbeat import beat
beat(project_root=".", identity="ani", session_id="ani-sess-42",
     activity="running tests on auth-race fix",
     lock_slugs=["fix-auth-middleware-race"])
```

Other sessions query it:

```python
from mclaude.heartbeat import list_live
for b in list_live("."):
    print(f"{b.identity}: {b.current_activity} ({b.runtime})")
```

Sessions whose last beat is older than `stale_after` (default 10 min)
drop off the live list automatically.

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

## Step 9: pick your integrations

mclaude does NOT call any task tracker (Vikunja, Linear, Jira, GitHub
Projects). It deliberately stays generic so you can plug any tracker in
with a small script.

**Pattern for integrating any tracker:**

1. When writing a handoff, include the tracker id in the `refs` field
   (e.g. `refs=["linear:ENG-42"]`). mclaude renders a `## Refs`
   section; it does nothing else with the token.

2. Write (or reuse) a ~200-line script in your own repo that scans
   `.claude/handoffs/` for tokens matching your tracker's pattern and
   posts cross-reference comments back.

3. Keep the script in your private or team repo, not in mclaude. Gives
   you full control of credentials and API quirks.

See `examples/integrations/` in this repo for reference scripts (Linear,
GitHub, Vikunja) that you can copy and adapt.

---

## Step 10: how much value can you get out of this?

mclaude scales with how many of its layers you actually use. Rough map:

| You use | What you gain |
|---|---|
| Locks only | No more "I deleted the same function you just deleted" |
| Locks + handoffs | Sessions pick up each other's work cleanly |
| + memory | No more re-discovering the same gotcha every month |
| + heartbeat | Real-time visibility into who is alive and doing what |
| + messages | Specific cross-session requests ("can you handle review?") |
| + registry | Named actors in every log entry (human-readable history) |
| + indexer | New sessions skip the 15-min "let me re-read the codebase" |
| + hub | Cross-machine and cross-network sharing, optional desktop UI |

Minimum viable setup is steps 0-6 (install, identity, lock, handoff,
memory, hooks). That is ~10 minutes of setup and already prevents the
majority of parallel-session problems.

The remaining layers pay off proportionally to how many sessions you
run and how long your projects last. For a solo person doing short
tasks, steps 1-4 alone are often enough.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `mclaude: command not found` | Not installed in active venv | `which python` - install in the right env |
| Lock claim succeeds but other session does not see it | Different `project_root` | Both sessions must run from the same directory |
| Handoffs pile up without cleanup | Old handoffs never archived | Run `mclaude handoff archive --older-than 14d` (or schedule it) |
| Memory graph has no `[[links]]` | Drawers were written without the wiki-link syntax | Edit the drawers to add links, or use `mclaude memory suggest-links` |
| Heartbeats show sessions that are dead | `stop()` was never called | Stale sessions drop off after `stale_after` seconds automatically; set this to match your expected beat interval |

---

## Learn more

- `docs/architecture.md` - how the pieces fit together under the hood
- `docs/security-audit-recipe.md` - threat model and review checklist
- `examples/` - real-world integration recipes (notifications, bridges)
- `mclaude demo` - always-runnable self-test that walks all six layers

If you hit a case that is not covered, open an issue - the library
tries to match the shape of problems it has seen in production.
