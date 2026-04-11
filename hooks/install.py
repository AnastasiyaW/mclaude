#!/usr/bin/env python3
"""
Install mclaude hooks into Claude Code settings.

Copies hook scripts to the project and adds hook entries to the
user's Claude Code settings.json (or prints the config to add manually).

Usage:
    python -m mclaude.hooks.install          # print config to add manually
    python -m mclaude.hooks.install --apply   # write to project .claude/settings.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# The hooks we install
HOOKS_CONFIG = {
    "hooks": {
        "SessionStart": [
            {
                "hook_command": "python .claude/hooks/session_start.py",
                "timeout": 5000,
            }
        ],
        "PreToolUse": [
            {
                "hook_command": "python .claude/hooks/pre_edit_lock_check.py",
                "if": "Edit(*)",
                "timeout": 3000,
            }
        ],
        "UserPromptSubmit": [
            {
                "hook_command": "python .claude/hooks/mail_check.py",
                "timeout": 3000,
            }
        ],
        "Stop": [
            {
                "hook_command": "python .claude/hooks/remind_handoff.py",
                "timeout": 3000,
            }
        ],
    }
}

HOOK_FILES = [
    "session_start.py",
    "pre_edit_lock_check.py",
    "mail_check.py",
    "remind_handoff.py",
]


def find_hook_source_dir() -> Path:
    """Find the hooks/ directory relative to this script."""
    return Path(__file__).parent


def copy_hooks(project_root: Path) -> list[Path]:
    """Copy hook scripts to project's .claude/hooks/."""
    target_dir = project_root / ".claude" / "hooks"
    target_dir.mkdir(parents=True, exist_ok=True)

    source_dir = find_hook_source_dir()
    copied: list[Path] = []

    for name in HOOK_FILES:
        src = source_dir / name
        dst = target_dir / name
        if not src.exists():
            print(f"  WARNING: source {src} not found, skipping", file=sys.stderr)
            continue
        shutil.copy2(src, dst)
        copied.append(dst)
        print(f"  Copied: {dst}")

    return copied


def print_config() -> None:
    """Print the settings.json snippet for manual installation."""
    print("Add the following to your .claude/settings.json (or global settings):")
    print()
    print(json.dumps(HOOKS_CONFIG, indent=2))
    print()
    print("Hook scripts should be placed in .claude/hooks/ relative to your project root.")
    print("Run with --apply to install automatically.")


def apply_config(project_root: Path) -> None:
    """Copy hooks and merge config into .claude/settings.json."""
    print("[mclaude] Installing hooks...")
    print()

    # Copy hook scripts
    copy_hooks(project_root)
    print()

    # Read or create settings.json
    settings_path = project_root / ".claude" / "settings.json"
    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"  WARNING: could not read {settings_path}, creating new", file=sys.stderr)

    # Merge hooks - don't overwrite existing hooks, append
    existing_hooks = settings.get("hooks", {})
    for event, hook_list in HOOKS_CONFIG["hooks"].items():
        if event not in existing_hooks:
            existing_hooks[event] = []
        # Check if mclaude hook already installed (by command substring)
        for new_hook in hook_list:
            already = any(
                "mclaude" in h.get("hook_command", "") or
                new_hook["hook_command"] in h.get("hook_command", "")
                for h in existing_hooks[event]
            )
            if not already:
                existing_hooks[event].append(new_hook)
                print(f"  Added {event} hook: {new_hook['hook_command']}")
            else:
                print(f"  {event} hook already installed, skipping")

    settings["hooks"] = existing_hooks

    # Write back
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print()
    print(f"[mclaude] Settings written to {settings_path}")
    print("[mclaude] Hooks installed. Restart Claude Code to activate.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Install mclaude hooks into Claude Code")
    parser.add_argument("--apply", action="store_true", help="Actually install (copy files + update settings)")
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="Project root (default: cwd)")
    args = parser.parse_args()

    if args.apply:
        apply_config(args.project)
    else:
        print_config()

    return 0


if __name__ == "__main__":
    sys.exit(main())
