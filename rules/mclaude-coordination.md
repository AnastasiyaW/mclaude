# mclaude coordination rules

This project uses mclaude for multi-session coordination.

## At session start

1. Run `mclaude handoff latest` to check for recent handoffs
2. Run `mclaude lock list` to see active work claims
3. If `MCLAUDE_IDENTITY` is set, run `mclaude message inbox $MCLAUDE_IDENTITY` for unread messages

## Before starting work

1. Check if the task is already locked: `mclaude lock status <slug>`
2. If free, claim it: `mclaude lock claim --slug <slug> --description "..." --files <paths>`
3. Refresh heartbeat every 30 seconds while holding the lock

## Before editing files

If a file is listed in another session's lock metadata, do NOT edit it without confirmation.
Check: `mclaude lock list` and read the `files` field in each lock's metadata.

## At session end

1. Release any held locks: `mclaude lock release <slug> --summary "..."`
2. Write a handoff if the session lasted > 15 minutes:
   `mclaude handoff write --session <id> --goal "..." --done "..." --not-worked "..."`
3. The handoff MUST include the "what did NOT work" section - this is the most valuable part

## Saving knowledge

When a significant decision is made, save it to the memory graph:
`mclaude memory save --wing <project> --room <topic> --hall decisions --title "..." --content "..."`
