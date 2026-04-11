"""Claude bridge - connects a local Claude Code session to the hub.

The bridge is the minimal glue between Claude (or anything Claude-like) and
the hub server. It does three things:

1. **Outbound:** forwards local events (lock claims, handoffs written,
   messages sent) to the hub via HTTP
2. **Inbound:** receives events from the hub via WebSocket and delivers them
   to Claude as synthetic "user input" lines or queued messages
3. **Fallback:** if the hub is offline, writes events to local `.claude/`
   files using the mclaude library - the exact same format, so nothing is lost
"""
from mclaude.bridge.client import BridgeClient, BridgeConfig

__all__ = ["BridgeClient", "BridgeConfig"]
