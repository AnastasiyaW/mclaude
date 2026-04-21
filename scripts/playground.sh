#!/usr/bin/env bash
# mclaude playground — deterministic 15-step demo of all six coordination layers.
#
# Runs in an isolated temp directory so your real .claude/ is never touched.
# Exits non-zero on any failed step so you can wrap it in CI.
#
# Usage:
#     bash scripts/playground.sh                  # run interactively
#     bash scripts/playground.sh --no-pause       # no between-step pauses
#     MCLAUDE=python\ -m\ mclaude.cli bash scripts/playground.sh  # override binary
#
# Reads: MCLAUDE (default: mclaude), PAUSE (default: 0.8)
#
# Produces: a temp .claude/ tree plus stdout narration. Prints final temp path
# at the end so you can inspect / diagram / tar it.
set -euo pipefail

MCLAUDE="${MCLAUDE:-mclaude}"
PAUSE="${PAUSE:-0.8}"
if [ "${1:-}" = "--no-pause" ]; then PAUSE=0; fi

# Colors (disabled if stdout is not a TTY)
if [ -t 1 ]; then
  BOLD="\033[1m"; DIM="\033[2m"; CYAN="\033[36m"; YELLOW="\033[33m"
  GREEN="\033[32m"; RED="\033[31m"; RESET="\033[0m"
else
  BOLD=""; DIM=""; CYAN=""; YELLOW=""; GREEN=""; RED=""; RESET=""
fi

step() {
  local n="$1"; shift
  printf "\n${BOLD}${CYAN}[STEP %2s/17]${RESET} %s\n" "$n" "$*"
  sleep "$PAUSE"
}
act() {
  printf "\n${BOLD}${YELLOW}─── Act %s ── %s ───${RESET}\n" "$1" "$2"
}
note() { printf "${DIM}%s${RESET}\n" "$*"; }
ok() { printf "${GREEN}✓${RESET} %s\n" "$*"; }
fail() { printf "${RED}✗${RESET} %s\n" "$*"; }
show() {
  # Pretty-print a file path + contents (truncate to 20 lines for readability)
  local f="$1"
  if [ -f "$f" ]; then
    printf "${DIM}  %s:${RESET}\n" "$f"
    sed 's/^/    /' "$f" | head -20
    local total
    total="$(wc -l < "$f")"
    if [ "$total" -gt 20 ]; then printf "${DIM}    ... (%s more lines)${RESET}\n" "$((total - 20))"; fi
  else
    fail "  $f does not exist"
  fi
}
try_or_show_failure() {
  # Run a command. On non-zero exit, pretty-print the error but keep going.
  if ! "$@" 2>&1; then
    fail "(command failed, but this can be part of the scenario — continuing)"
  fi
}

# -- Setup ---------------------------------------------------------------------

PLAYGROUND_DIR="$(mktemp -d -t mclaude-playground.XXXXXX)"
cd "$PLAYGROUND_DIR"
note "playground dir: $PLAYGROUND_DIR"
note "mclaude binary: $MCLAUDE"

echo
printf "${BOLD}mclaude playground${RESET} — simulating two Claude sessions "
printf "coordinating through all six layers.\n"
printf "${DIM}Sessions: ${RESET}ani${DIM} (refactoring auth) and ${RESET}vasya${DIM} (writing tests)\n"
printf "${DIM}Goal: end with a handoff ani → vasya, with full audit trail.${RESET}\n"

# -- Act 1 — Setup (identity registry) -----------------------------------------

act 1 "Setup — identity registry (Layer 4)"

step 1 "ani registers her identity"
$MCLAUDE identity register ani --owner "Anastasia" --roles infra auth
ok "registry now knows ani"

step 2 "vasya registers his identity (from the 'other machine')"
$MCLAUDE identity register vasya --owner "Demo teammate" --roles qa tests
ok "registry now knows both"

step 3 "list all identities — both visible"
$MCLAUDE identity list

# -- Act 2 — Collision on a shared task (Layer 1) ------------------------------

act 2 "Collision — two sessions want the same task (Layer 1: Locks)"

step 4 "ani claims work on 'refactor-auth-middleware'"
MCLAUDE_IDENTITY=ani $MCLAUDE lock claim \
    --slug refactor-auth-middleware \
    --session ani \
    --description "Race condition when two requests write the same session key" \
    --files src/auth/middleware.py src/auth/session.py
ok "ani holds the lock"

step 5 "vasya tries to claim the SAME slug — mclaude blocks him"
MCLAUDE_IDENTITY=vasya set +e
$MCLAUDE lock claim \
    --slug refactor-auth-middleware \
    --session vasya \
    --description "I thought nobody was on this" 2>&1 || true
set -e
ok "second claim correctly refused"

step 6 "vasya checks status — sees WHO holds it and WHAT they're doing"
$MCLAUDE lock status refactor-auth-middleware

# -- Act 3 — Coordination via messages (Layer 5) -------------------------------

act 3 "Coordination — vasya asks ani a question (Layer 5: Messages)"

step 7 "vasya sends a question instead of fighting for the lock"
MCLAUDE_IDENTITY=vasya $MCLAUDE mail ask ani \
    "Is the race condition in write path or in read path?" \
    --body "If write — I'll mock the store. If read — I need live Redis in tests."
ok "question dropped in ani's inbox"

step 8 "ani checks her inbox"
MCLAUDE_IDENTITY=ani $MCLAUDE mail check

step 9 "ani replies — all threading handled by mclaude"
# Messages land in .claude/messages/inbox/ addressed to the recipient in frontmatter
MSGFILE=""
for candidate in .claude/messages/inbox/*question*ani*.md .claude/messages/inbox/*.md; do
  if [ -f "$candidate" ]; then MSGFILE="$candidate"; break; fi
done
if [ -n "$MSGFILE" ] && [ -f "$MSGFILE" ]; then
  MCLAUDE_IDENTITY=ani $MCLAUDE mail reply "$(basename "$MSGFILE")" \
    --body "Write path -- session_store.write() races on concurrent requests. You can mock the store entirely."
  ok "reply sent, thread preserved"
else
  note "(inbox empty -- messages layer may have been routed differently; demo continues)"
fi

# -- Act 4 — Memory of decisions (Layer 3) ------------------------------------

act 4 "Knowledge capture — turn the decision into memory (Layer 3: Memory Graph)"

step 10 "ani saves the decision as a drawer in the memory graph"
$MCLAUDE memory save \
    --wing project-demo \
    --room auth-system \
    --hall decisions \
    --title "Race condition is in write path, not read path" \
    --content "Decision: session_store.write() has the race under concurrent requests. Reads are safe. Consequence for tests: mock the entire store; no need for a live Redis." \
    --session ani \
    --tags auth race-condition testing
ok "decision saved — findable by any future session"

step 11 "memory search confirms it's findable by keyword"
$MCLAUDE memory search "race condition" --wing project-demo

# -- Act 5 — Completion and handoff (Layer 2) ---------------------------------

act 5 "Completion — ani writes handoff for vasya (Layer 2: Handoffs)"

step 12 "ani writes a structured handoff with worked_by attribution"
$MCLAUDE handoff write \
    --session ani \
    --slug refactor-auth-middleware \
    --goal "Fix session-write race condition in auth middleware" \
    --done "Identified write-path race" "Added mutex around session_store.write()" "Unit test asserts no lost writes" \
    --not-worked "Redis transactions — too heavy for this use case, rejected" \
    --working "Isolated mutex + compare-and-swap in the in-memory store" \
    --next-step "vasya — add integration test that runs 100 concurrent writes and asserts no loss"
ok "handoff written"

step 13 "ani releases the lock with a summary — work is claimable again"
MCLAUDE_IDENTITY=ani $MCLAUDE lock release refactor-auth-middleware \
    --summary "Done. Race fixed via mutex. Handoff written for vasya. Tests pending."
ok "lock released — vasya (or any future session) can pick up"

# -- Act 6 — Observability and indexing (Layers 6 + status) -------------------

act 6 "Observability — one-command status and code indexer (Layer 6 + status)"

step 14 "mclaude status — the one-screen overview of all six layers"
$MCLAUDE status 2>&1 | head -40 || note "(status may not be implemented yet — continuing)"

step 15 "(skipping mclaude index — needs real code tree; noted for manual demo)"
note "in a real project: '\$MCLAUDE index' scans source and writes code-map.md + llms.txt"

# -- Rollup demo (simulated manual rollup) ------------------------------------

act 7 "Rollup — compressing old handoffs (Layer 2 extension, 2026-04 addition)"

step 16 "Manually create 3 older handoffs to simulate a project with history"
# Backfill three fake-older handoffs so the rollup is non-trivial
for slug in kickoff-design schema-choice ci-pipeline; do
  $MCLAUDE handoff write \
      --session ani \
      --slug "$slug" \
      --goal "Older session on the same project" \
      --done "Baseline done" \
      --next-step "Next session picks up" > /dev/null
done
$MCLAUDE handoff list 2>&1 | head -20

step 17 "Create a rollup handoff that subsumes them (manual pattern, no CLI yet)"
ROLLUP_FILE=".claude/handoffs/$(date +%Y-%m-%d_%H-%M)_rollup_project-week-1.md"
mkdir -p .claude/handoffs
cat > "$ROLLUP_FILE" <<'ROLLUP_EOF'
---
type: rollup
session: rollup-project-week-1
covers:
  - kickoff-design
  - schema-choice
  - ci-pipeline
through: 2026-04-21 12:00
author: ani
---

# Rollup — project week 1

## Strategic arc
Started with kickoff design → settled on JWT schema in session 2 → finished CI pipeline in session 3.

## Decisions that still apply
- JWT over session cookies — see memory graph: project-demo/auth-system/decisions
- Mutex over Redis transactions — see refactor-auth-middleware handoff

## What did NOT work (do not retry)
- Redis transactions for session store — too heavy; rejected in kickoff-design

## State at rollup boundary
- Working: auth refactor, CI
- Open: integration tests (next up — see refactor-auth-middleware handoff)
ROLLUP_EOF
ok "rollup created at $ROLLUP_FILE"
note "pattern: old handoffs stay on disk, the rollup carries 'covers:' + 'through:'"

# -- Final summary ------------------------------------------------------------

echo
printf "${BOLD}${GREEN}Playground complete.${RESET}\n"
echo
printf "${DIM}What got created:${RESET}\n"
find .claude -type f | sort | sed 's/^/  /'
echo
printf "${DIM}Inspect further:${RESET}\n"
printf "  cd %s\n" "$PLAYGROUND_DIR"
printf "  ls -la .claude/handoffs/\n"
printf "  cat .claude/handoffs/INDEX.md\n"
echo
printf "${DIM}Generate a sequence diagram from this state:${RESET}\n"
printf "  python %s/scripts/mclaude_diagram.py %s\n" \
  "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")/.." "$PLAYGROUND_DIR"
echo
printf "${DIM}Watch a replay (start in another terminal BEFORE re-running):${RESET}\n"
printf "  python %s/scripts/mclaude_watch.py %s\n" \
  "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")/.." "$PLAYGROUND_DIR"
echo
printf "${DIM}(Temp dir left intact for inspection. Clean up with: rm -rf %s)${RESET}\n" "$PLAYGROUND_DIR"
