# Project Knowledge Base

A scaffolding tool for creating project-specific knowledge bases that multiple Claude Code sessions can share.

## The problem

Three Claude Code sessions working on the same project will independently invent different approaches to the same module - different error handling, different API styles, different naming conventions. Without a shared source of truth, the code diverges.

## The solution

A local MkDocs site where every module, decision, and gotcha is documented as a standalone article. Claude sessions read the KB before implementing anything.

```
Claude Code session
    |
    v
[1] Project KB (localhost:8200)  <-- project-specific modules, API, decisions
    |
    v (not found)
[2] Public KB (happyin.space)    <-- general technology references
```

## Quick start

```bash
# 1. Generate a KB for your project
python scaffold.py --name "my-project" --domains "backend,frontend,api,database"

# 2. Install dependencies
cd my-project-kb
pip install -r requirements.txt

# 3. Start the KB server
mkdocs serve -a 0.0.0.0:8200

# 4. Copy the Claude rule into your project
cp ../claude-rules/knowledge-base.md /path/to/project/.claude/rules/
```

Your Claude Code sessions can now read articles at `http://localhost:8200/`.

## scaffold.py options

```
--name      Project name (required)
--domains   Comma-separated domain names (required)
--port      MkDocs serve port (default: 8200)
--output    Output directory (default: <name>-kb)
```

## Article format

Each article follows a consistent structure:

```markdown
---
module: module-name
status: draft|active|deprecated
owner: session-identity
---

# Module Name

What this module does and why. 2-3 sentences.

## Public API
## Implementation Notes
## Code
## Usage Example
## Gotchas
## Dependencies
```

The article template is generated as `docs/_ARTICLE.md` - copy it for new articles.

## Two-layer lookup

The `claude-rules/knowledge-base.md` file instructs Claude to:

1. Check the Project KB first (localhost)
2. Fall back to the Public KB (happyin.space) for general references
3. Treat KB code as canonical - no deviation without documented reason
4. Create articles for new modules after implementing them
5. Always add gotchas when discovering edge cases

## How it works with mclaude

If you use [mclaude](https://github.com/AnastasiyaW/mclaude) for multi-session coordination:

- **Locks**: claim a KB article before editing it (prevents merge conflicts)
- **Messages**: ask another session to review your article
- **Handoffs**: mention which KB articles you created or updated

```bash
# Before editing an article
mclaude lock claim --slug "kb-engine-crypto" --description "Updating crypto module article" --files "docs/engine/security-crypto.md"

# After editing
mclaude lock release kb-engine-crypto --summary "Added HKDF gotcha about salt length"
```

## Directory structure

```
project-kb/
  scaffold.py                  # KB generator
  claude-rules/
    knowledge-base.md          # Rule for Claude Code (.claude/rules/)
  examples/
    retouch-app/               # Real-world example
```

## Example: retouch-app

The `examples/retouch-app/` directory contains a scaffolded KB for a C++ Photoshop plugin with 9 security layers. Domains:

- **engine** - daemon process: security, model loader, inference, scramble
- **converter** - output pipeline: inverse scramble, watermark
- **plugin** - Photoshop UXP Hybrid plugin
- **server** - license server (Go, Ed25519 JWT)
- **tools** - hmod_packer, key_extractor
