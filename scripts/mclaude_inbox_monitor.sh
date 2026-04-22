#!/usr/bin/env bash
# mclaude_inbox_monitor.sh — real-time inbox polling for Claude Code's Monitor tool.
#
# Problem: mail_check.py UserPromptSubmit hook delivers inbox messages only
# when the user types the next prompt. If the user is away (coffee, meeting)
# and a teammate's Claude sends a letter — the running agent does not see
# it until the next human interaction.
#
# Solution: run this script via Claude Code's Monitor tool. Each new message
# matching the identity's inbox filter produces one stdout line, which the
# tool delivers to the agent as a notification mid-conversation.
#
# Usage inside Claude Code:
#
#   Monitor(
#     command="bash scripts/mclaude_inbox_monitor.sh",
#     description="mclaude inbox for $IDENTITY",
#     persistent=True
#   )
#
# Environment:
#   MCLAUDE_IDENTITY  — required. Messages with to: $IDENTITY or to: * are
#                       announced. Others are skipped silently.
#   MCLAUDE_INBOX_DIR — optional. Override default .claude/messages/inbox.
#   MCLAUDE_POLL_SEC  — optional. Polling interval seconds, default 30.
#                       Stay >=30s for file-based polling; lower is wasteful.
#
# Exit: runs until killed. Claude Code's Monitor tool lifecycle manages it.
#
# Coverage: emits on every NEW file in the inbox directory matching the filter.
# Does not emit on message status changes (unread→read), deletions, or edits.
# First invocation marks all existing files as "seen" so you do not get a
# flood of historical messages.

set -u

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

IDENTITY="${MCLAUDE_IDENTITY:-}"
INBOX_DIR="${MCLAUDE_INBOX_DIR:-.claude/messages/inbox}"
POLL_SEC="${MCLAUDE_POLL_SEC:-30}"
SEEN_FILE=".claude/messages/.monitor_seen.txt"

if [ -z "$IDENTITY" ]; then
  echo "FATAL: MCLAUDE_IDENTITY not set. Export it before starting monitor." >&2
  exit 2
fi

mkdir -p "$INBOX_DIR" "$(dirname "$SEEN_FILE")"

# Initial seed: mark all existing messages as seen so we only announce new ones.
if [ ! -f "$SEEN_FILE" ]; then
  for f in "$INBOX_DIR"/*.md; do
    [ -f "$f" ] || continue
    basename "$f" >> "$SEEN_FILE"
  done
fi

echo "monitor-started identity=$IDENTITY inbox=$INBOX_DIR poll=${POLL_SEC}s seen=$(wc -l < "$SEEN_FILE" 2>/dev/null || echo 0)"

while true; do
  sleep "$POLL_SEC"

  for msg in "$INBOX_DIR"/*.md; do
    [ -f "$msg" ] || continue
    name="$(basename "$msg")"

    # Skip if already announced
    if grep -Fxq "$name" "$SEEN_FILE" 2>/dev/null; then
      continue
    fi

    # Parse frontmatter (lines between first and second '---')
    in_fm=0
    fm_done=0
    meta_from=""
    meta_to=""
    meta_subject=""
    meta_type=""
    meta_urgent=""
    while IFS= read -r line; do
      if [ "$line" = "---" ]; then
        if [ "$in_fm" -eq 0 ]; then
          in_fm=1
          continue
        else
          fm_done=1
          break
        fi
      fi
      if [ "$in_fm" -eq 1 ]; then
        case "$line" in
          from:*)    meta_from="${line#from:}" ;;
          to:*)      meta_to="${line#to:}" ;;
          subject:*) meta_subject="${line#subject:}" ;;
          type:*)    meta_type="${line#type:}" ;;
          urgent:*)  meta_urgent="${line#urgent:}" ;;
        esac
      fi
    done < "$msg"

    # Strip leading whitespace + matching surrounding quotes (YAML scalar forms)
    strip() {
      local v="$1"
      v="${v# }"            # leading space
      v="${v#\"}"; v="${v%\"}"   # double quotes
      v="${v#\'}"; v="${v%\'}"   # single quotes
      printf '%s' "$v"
    }
    meta_from="$(strip "$meta_from")"
    meta_to="$(strip "$meta_to")"
    meta_subject="$(strip "$meta_subject")"
    meta_type="$(strip "$meta_type")"
    meta_urgent="$(strip "$meta_urgent")"

    # Filter: only messages addressed to our identity or wildcard
    if [ "$meta_to" != "$IDENTITY" ] && [ "$meta_to" != "*" ]; then
      echo "$name" >> "$SEEN_FILE"
      continue
    fi

    # Emit notification (one line = one Monitor event)
    marker=""
    if [ "$meta_urgent" = "true" ]; then
      marker="URGENT "
    fi
    echo "${marker}new-mail from=${meta_from:-?} type=${meta_type:-update} subject=\"${meta_subject:-(no subject)}\" file=$name"

    echo "$name" >> "$SEEN_FILE"
  done
done
