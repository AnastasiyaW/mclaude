# Multi-Session Security Audit with mclaude

How to run a parallel security audit using multiple Claude Code sessions coordinated through mclaude.

## Why multi-session

A single Claude Code session analyzing code for vulnerabilities has 14% true positive rate (Semgrep blog benchmark). Three identical runs on the same code give 3, 6, and 11 findings — non-deterministic.

Multi-session with different security perspectives gives +600% detection vs single-agent (MAVUL paper). Four parallel agents reach 82.7% recall (MultiVer). The key: diverse perspectives, not identical prompts.

mclaude provides the coordination layer: locks prevent overlap, messages enable real-time routing, handoffs preserve findings across sessions.

## Setup

### Prerequisites

```bash
pip install mclaude
mclaude init  # creates .claude/mclaude/ structure
```

### Register specialist identities

```bash
mclaude registry add injection-agent
mclaude registry add crypto-agent
mclaude registry add concurrency-agent
mclaude registry add logic-agent
mclaude registry add coordinator
```

## Workflow

### Step 1: Coordinator scopes the audit

Open a Claude Code session as coordinator:

```
I am the security audit coordinator. My identity: coordinator.
Target: [path to code or module]

Tasks:
1. Read the codebase, identify entry points and trust boundaries
2. Create lock on the audit scope
3. Send messages to specialist agents with their assignments
4. Collect and deduplicate findings
```

The coordinator:
- Reads the codebase structure
- Identifies which files/modules are relevant
- Creates a scope document in `.claude/mclaude/findings/audit-scope.md`
- Sends targeted messages to each specialist

### Step 2: Launch specialist sessions

Open 3-5 parallel Claude Code terminals. Each starts with:

```
I am [injection-agent/crypto-agent/concurrency-agent/logic-agent].
Read my messages from coordinator. Then audit the assigned code.
```

Each specialist has a focused mandate:

| Agent | Focus | What to trace |
|---|---|---|
| **injection-agent** | Input validation | SQL/NoSQL/cmd injection, XSS, path traversal, SSRF, template injection |
| **crypto-agent** | Cryptography & secrets | Weak algorithms, hardcoded secrets, improper random, timing attacks, key management |
| **concurrency-agent** | Race conditions & state | TOCTOU, deadlocks, shared mutable state, atomicity violations, double-spend |
| **logic-agent** | Business logic | State machine violations, numeric overflow, auth flow bypasses, IDOR, missing checks |

### Step 3: Specialists work and report

Each specialist:
1. Claims a lock on their focus area: `mclaude lock claim security-audit-injection`
2. Reads assigned files
3. For each finding, writes to `.claude/mclaude/findings/`:

```markdown
## Finding: [SHORT_TITLE]
**Agent:** injection-agent
**Severity:** CRITICAL | HIGH | MEDIUM | LOW
**File:** path/to/file.py:42
**CWE:** CWE-89 (SQL Injection)
**Evidence:**
```python
# vulnerable code snippet (max 5 lines)
```
**Exploit scenario:** [how an attacker would trigger this]
**Fix:** [concrete code change]
**Confidence:** HIGH | MEDIUM | LOW
```

4. Sends a message to coordinator: "Found N findings, see findings/ directory"
5. Writes handoff when done

### Step 4: Coordinator aggregates

The coordinator session:
1. Reads all findings from `.claude/mclaude/findings/`
2. Deduplicates (same file+line from multiple agents = HIGH CONFIDENCE)
3. **Preserves minority findings** — a finding from only one agent is NOT less valid
4. Runs adversarial verification on HIGH/CRITICAL findings
5. Produces final report

### Step 5: Adversarial pass (optional)

Launch one more session as `adversarial-agent`:

```
I am the adversarial verifier. For each HIGH/CRITICAL finding,
argue why it is NOT exploitable. If I cannot construct a valid
argument, the finding is confirmed.
```

This reduces false positives without reducing true positives.

## Findings structure

```
.claude/mclaude/findings/
  audit-scope.md          # coordinator: scope, entry points, trust boundaries
  injection-findings.md   # injection-agent results
  crypto-findings.md      # crypto-agent results
  concurrency-findings.md # concurrency-agent results
  logic-findings.md       # logic-agent results
  REPORT.md               # coordinator: deduplicated, triaged final report
```

## Integration with SAST

For maximum coverage, run SAST before the audit:

```bash
# Layer 1: SAST scan
semgrep --config auto --json . > sast-results.json

# Include SAST results in coordinator's scope message
```

Coordinator distributes SAST findings to relevant specialists for contextual validation (Layer 2 filtering). This eliminates ~91% of false positives (SAST-Genius benchmark).

## Tips

- **Memory graph for patterns**: if the codebase has recurring patterns (e.g., same auth middleware everywhere), store the pattern in mclaude memory so all agents know about it
- **Locks prevent duplicate work**: if injection-agent is auditing `auth/` module, concurrency-agent skips auth and focuses elsewhere
- **Handoffs for continuity**: if the audit spans multiple days, handoffs preserve what each agent found and what remains unchecked
- **Messages for coordination**: coordinator can redirect agents mid-audit ("injection-agent: also check the new API endpoint in routes/v2/")

## Expected results

Based on research benchmarks:

| Setup | Expected recall | Notes |
|---|---|---|
| Single session, single pass | ~14% | Semgrep blog benchmark |
| Single session, 3 runs | ~25-35% | Non-determinism helps |
| 4 specialized agents | ~60-80% | MultiVer: 82.7% with union voting |
| 4 agents + SAST + knowledge RAG | ~85-90% | Vul-RAG adds +16-24% |
| Full pipeline (all 6 layers) | ~90%+ | Theoretical upper bound |

## References

- MAVUL [2510.00317]: +600% vs single-agent vulnerability detection
- MultiVer [2602.17875]: 82.7% recall with 4 parallel agents
- VulAgent [2509.11523]: hypothesis-validation approach
- SAST-Genius [2509.15433]: 89.5% precision with Semgrep + LLM
- Vul-RAG [ACM TOSEM 2025]: +16-24% with knowledge-level RAG
- AgentAuditor [2602.09341]: minority findings recovery 65-82%
