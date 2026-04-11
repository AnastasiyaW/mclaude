"""
mclaude - multi-session collaboration layer for Claude Code and other AI agents.

Five layers, all file-based, no external dependencies:

1. **locks** - atomic work claims prevent two sessions from accidentally
   working on the same task. Heartbeat-based stale detection. Project-local.

2. **handoffs** - append-only per-session markdown files with unique slugs.
   No race conditions. Index file for quick navigation. Structured format
   with mandatory "what did NOT work" section.

3. **memory** - hierarchical raw verbatim knowledge graph (Wings/Rooms/Drawers).
   Inspired by MemPalace but without the ChromaDB dependency. Searchable via
   grep or optional vector layer.

4. **registry** - human-readable identity for Claude instances. Names, owners,
   roles, notify metadata for future notification layers.

5. **messages** - live inter-session messaging (question/answer/request/update).
   The "desktop dead drop" pattern formalized: one Claude writes a question,
   another reads it and answers, all via append-only markdown files in
   .claude/messages/. Compatible file format with the network hub layer,
   so local files and remote messages interoperate without translation.

All five layers are orthogonal - use one without the others, or all together.
"""

__version__ = "0.5.0"
