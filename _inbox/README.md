# Inbox - staging area for mclaude-hub

Drop files here during work. Periodically sort into the repo proper.

## Structure

```
_inbox/
  findings/    <- discoveries during multi-agent sessions (gotchas, patterns, what worked/failed)
  research/    <- raw research dumps, articles, comparisons
  code-snippets/ <- reusable code that should become part of the project
```

## Workflow

1. During work: `cp/move file _inbox/findings/` - don't stop to organize
2. Between sessions: review _inbox, move to proper location in repo
3. After moving: delete from _inbox

## Naming convention

`YYYY-MM-DD_short-description.md` - date helps sort by freshness.

Files without dates are fine too - just dump and go.

## What goes where after sorting

| From _inbox/ | Goes to |
|---|---|
| findings/ about mclaude coordination | project-kb/ or mclaude_hub/ code |
| findings/ about multi-agent patterns | docs/ or README updates |
| research/ about technologies | project-kb/examples/ or docs/ |
| code-snippets/ for scaffold | project-kb/scaffold.py or template/ |
| code-snippets/ for mclaude core | ../mclaude/ (separate repo) |
