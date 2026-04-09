"""
mclaude - multi-session collaboration layer for Claude Code and other AI agents.

Three layers, all file-based, no external dependencies:

1. **locks** - atomic work claims prevent two sessions from accidentally
   working on the same task. Heartbeat-based stale detection. Project-local.

2. **handoffs** - append-only per-session markdown files with unique slugs.
   No race conditions. Index file for quick navigation. Structured format
   with mandatory "what did NOT work" section.

3. **memory** - hierarchical raw verbatim knowledge graph (Wings/Rooms/Drawers).
   Inspired by MemPalace but without the ChromaDB dependency. Searchable via
   grep or optional vector layer.

All three layers are orthogonal - you can use one without the others.
"""

__version__ = "0.1.0"
