# Knowledge Base Integration

## Two-layer knowledge lookup

When you need information about a module, pattern, or decision:

### Layer 1: Project KB (localhost:8200) - check FIRST

Project-specific modules, API references, architecture decisions, gotchas.

```bash
# Read an article directly
curl -s http://localhost:8200/<domain>/<article>/

# Check what's available
curl -s http://localhost:8200/llms.txt
```

If the Project KB has an article for the module you are implementing - follow it. The code in KB articles is canonical.

### Layer 2: Public KB (happyin.space) - check SECOND

General technology references: languages, frameworks, libraries, patterns.

```bash
curl -s https://happyin.space/llms.txt
```

Use when Project KB does not cover the topic (e.g. general C++ patterns, ONNX Runtime API, CMake syntax).

## Rules

- **Before implementing a module**: read its KB article if one exists
- **KB code is canonical**: do not deviate without documenting the reason
- **After making a decision**: update or create the relevant KB article
- **Gotchas are mandatory**: every bug, edge case, or surprise goes into the Gotchas section
- **No article = create one**: if you built something and there is no KB article, write a draft before ending the session
