# Knowledge Base construction — compressed references with wiki-links

When you work with Claude on a real codebase for weeks, facts pile up. Module `X` has a non-obvious convention. Decision `Y` was made for reason `Z`. Library `L` has gotcha at version `V`. Without a structured place to keep these, the next session rediscovers them — burning tokens and human time.

mclaude Layer 3 (Memory Graph) provides the storage. This doc covers the **construction technique**: how to write KB entries that are **compressed**, **linked**, and **verifiable against real source** so future sessions don't hallucinate or waste time re-confirming.

---

## Principles

### 1. Full verbatim text, not LLM summaries

Research-backed: raw human/agent text beats LLM extraction on retrieval benchmarks (MemPalace: 96.6% vs 85%). Store what was actually written or decided, don't let the KB layer auto-summarize.

```markdown
# Bad — LLM-summarized
## Decision
We chose JWT for auth tokens.

# Good — verbatim with context
## 2026-04-09 — JWT over server sessions

**Decision**: 15-min access token + 30-day refresh token, both JWT signed with
RS256. Rotation via refresh endpoint.

**Reasoning**: stateless (works across our 4 backends without shared session
store); refresh rotation gives revocation semantics without a per-request DB
hit; aligns with OAuth 2.0 patterns we'll need for 3rd-party integrations.

**Alternatives rejected**:
- Server sessions — rejected because of `backends-sticky-sessions` gotcha (see
  [[wings/project-myapp/rooms/infra/gotchas/sticky-sessions]])
- Opaque tokens — rejected because revocation requires a DB round-trip per
  request, hurts p99
```

The good version preserves reasoning, alternatives, and links to related facts. A future session reading it sees *why* and *what else was considered*.

### 2. Wiki-links between drawers

mclaude memory supports `[[wing/room/path]]` syntax in drawer bodies. `mclaude memory backlinks <drawer>` traces incoming links. Use this aggressively:

- When a decision references another decision, link it
- When a gotcha is a consequence of a decision, link back
- When two rooms have related facts, cross-link

The result: a navigable graph, not a flat pile. The agent can ask "what else references this decision?" and get immediate answers without guessing.

### 3. Compressed for skim, verbose for dive

Drawer titles (first-line headers) should read like a search result. Bodies should hold the full verbose text. Claude sessions spend tokens only on the specific drawers they retrieve; titles alone stay cheap.

Good title: `2026-04-09 — JWT over server sessions (15m access + 30d refresh)`

Bad title: `Auth decision`

### 4. References to source code, not duplicates

If a fact lives in code, reference the file/line rather than paste it:

```markdown
**Current implementation**: [[services/auth/src/jwt.ts:signAccessToken()]]
uses `RS256` with the key at `config/keys/jwt-access.pem`.
```

Keeps the KB from drifting when code changes. The reference is the canonical
source; the drawer just frames the *decision* and links out.

### 5. Valid-from and supersede tracking

Frontmatter on every drawer:

```yaml
---
valid_from: 2026-04-09
valid_to: null              # null = still current
superseded_by: null         # set when replaced
---
```

When a decision is replaced, don't delete the old drawer. Create a new one, link back, and update the old one's `superseded_by`. This preserves reasoning trail for future "why did we change?" questions.

---

## File layout

```
.claude/memory-graph/
├── core.md                        <- L0+L1, always loaded (~170 tokens cap)
└── wings/
    ├── project-<name>/            <- Wing = project or major topic
    │   └── rooms/
    │       └── <subtopic>/        <- Room = sub-topic within a wing
    │           ├── decisions/     <- Hall = content type
    │           │   └── 2026-04-09_<slug>.md
    │           ├── gotchas/
    │           │   └── 2026-04-08_<slug>.md
    │           ├── references/
    │           │   └── <api-name>.md
    │           └── runbooks/
    │               └── <procedure>.md
    └── common/                    <- Shared across projects (libraries, frameworks)
```

Halls we recommend:
- `decisions/` — choices made with reasoning
- `gotchas/` — "expected X but got Y" surprises with explanation
- `references/` — quick lookup for APIs, configs, paths
- `runbooks/` — step-by-step for reproducible procedures

---

## Avoiding "todo confirmation loops"

A common failure mode: agent writes a TODO, next session re-reads it, asks user "is this still relevant?", human confirms, agent starts work, later rediscovers the reason it was deferred. Everyone's time wasted.

Fix: when deferring work, write a **full reasoning drawer** in `decisions/`, not just a TODO comment:

```markdown
# 2026-04-22 — Defer: dispatcher workersMax scaling

**Decision**: Do NOT increase RunPod endpoint workersMax past 2 until we see
production burst data.

**Reasoning**: state machine correctly transitions to runpod_scaling, but
workersMax=2 caps actual spawning. Before bumping, need:
1. Observed burst pattern (hourly queue peaks)
2. GPU supply check on target DC (5090 in EU-RO-1 throttled today)
3. Cost ceiling agreement from stakeholder

**Trigger to revisit**: any production hour with >20 jobs queued OR any
stakeholder ping about slow bulk jobs.

**Who decided**: agent + user in session 2026-04-22 (chronicle entry).
```

Now future sessions reading the drawer know:
1. *Why* it's deferred (prereqs listed)
2. *When* to revisit (concrete triggers)
3. *Who* to re-confirm with (stakeholder ping → user)

No "is this still relevant?" loop — the drawer itself answers.

---

## Verifying against real source

Before trusting a drawer, the agent should verify it's not stale:

```bash
# Drawer references a specific file
mclaude memory get <drawer>
# Body mentions: services/auth/src/jwt.ts:signAccessToken()

# Check the file exists and the function is still there
grep -n "signAccessToken" services/auth/src/jwt.ts
```

If the grep fails: the drawer is stale. Either:
1. Update the drawer with new reference
2. Mark drawer `valid_to: <today>` + `superseded_by: <new drawer>`
3. If the fact still matters but reference moved, write new drawer and link

**Rule**: never let the agent act on a drawer without verifying its references still hold. This prevents "the memory says X exists" hallucinations.

---

## Integrating with handoffs

At session end, write handoff + save relevant facts to memory:

```bash
# Session summary
mclaude handoff write --session $SID --goal "..." \
  --done "Implemented rate limiter for /api/upload" \
  --not-worked "Sliding window was too heavy, used leaky bucket" \
  --next-step "Add per-user quotas"

# Decision worth preserving
mclaude memory save --wing project-myapp --room api --hall decisions \
  --title "Rate limiting: leaky bucket over sliding window" \
  --content "..."
```

Rule of thumb: if a fact will matter in 30 days, save to memory. If it only matters for the next session, handoff is enough.

---

## Retrieval at session start

Recommended boot sequence for long-running projects:

1. Read latest handoff (via `session_start.py` hook or `mclaude handoff latest`)
2. Scan memory wings for the current project (`mclaude memory list --wing project-myapp`)
3. Read any drawer whose title matches the current task
4. Follow wiki-links to related drawers

Avoid reading all drawers — that's what kills context. Retrieve by relevance, expand via links only when needed.

---

## Public KB vs Project KB

Some projects run a dedicated KB server (e.g. MkDocs site at `localhost:8200` or a public site). mclaude memory is complementary, not competing:

- **Project KB site**: long-lived, human-curated, rendered HTML docs, searchable
- **mclaude memory**: session-discoverable, agent-written, plain markdown with frontmatter, backlinks

Use both. Project KB for stable API reference, mclaude memory for living decisions and gotchas.

---

## Summary

- Store verbatim, not summaries
- Link aggressively (wiki-style)
- Title for skim, body for dive
- Reference source code; don't duplicate it
- Track validity + supersede chain
- Defer = full reasoning drawer, not a TODO
- Verify references before trusting a drawer

Applied consistently, this turns a growing pile of session scraps into a navigable graph where the next agent doesn't have to re-ask anything.
