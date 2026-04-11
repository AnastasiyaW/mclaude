# Findings: Setting up 3 Claude Code sessions in Hyper-V VM

Date: 2026-04-10

## What we did
Set up ClaudeWorkspace (Hyper-V Win11 VM) for 3 Claude Code sessions working on a C++ project via mclaude coordination.

## Key findings

### PowerShell Direct sessions are isolated
Each `Invoke-Command -VMName` creates a **new session**. `net use` mounts, env vars, working dir - nothing persists between calls. Either do everything in one ScriptBlock or use persistent mechanisms (registry Run keys, scheduled tasks).

### Default Switch IP changes on every host reboot
Hyper-V Default Switch uses NAT with dynamic IP range. The gateway IP changes after host restart. SMB mounts break. Solution: automount.ps1 that discovers gateway dynamically via `Get-NetRoute`.

### SMB mounts don't work from PowerShell Direct
Even with correct IP, `net use` in PowerShell Direct often fails with "binding handle invalid" or "network name not found". Works fine from interactive RDP session. Copy-VMFile (Hyper-V integration services) is more reliable for file transfer.

### Copy-VMFile only does single files
No directory support. Workaround: tar on host -> Copy-VMFile -> tar -xf in VM. But tar.exe from bash conflicts with Windows paths (uses `/usr/bin/tar` which can't handle `C:\`). Must use `powershell.exe tar.exe` explicitly.

### mclaude lock CLI had argparse bug
`set_defaults(func=callable)` leaked into `_flatten_known_lock_args` causing "unrecognized arguments: --func <function>". Fix: skip `func` key and any callable values in the flattener. Pushed as mclaude@732bf35.

### Ghost Spectre execution policy
Scripts are disabled by default. Need `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` before Claude Code CLI (installed via npm) works.

### Claude Code via npm on Windows
`npm install -g @anthropic-ai/claude-code` puts claude.cmd in `%APPDATA%\npm\` which is NOT in PATH by default on Ghost Spectre. Must add manually.

## Patterns worth codifying

1. **VM setup checklist for multi-Claude** - Python, Claude Code CLI, mclaude, execution policy, PATH
2. **Automount script pattern** - dynamic gateway discovery for Default Switch NAT
3. **Copy-VMFile workflow** - tar -> copy -> extract pattern for directories
4. **mclaude identity registration** - one command per participant
