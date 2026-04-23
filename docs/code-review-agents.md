# Code review agents with self-verification (proof-loop pattern)

When you ask an agent to review code, the natural failure mode is **fabrication**: the agent reports "I checked X, Y, Z all look fine" without actually checking. Models hallucinate confirmation because it's the path of least resistance.

This doc describes how to structure code review agents so they **cannot** report success without leaving an artifact that proves they did the work. Applied correctly, the pattern eliminates the "looked fine" class of false positives.

---

## The problem in one scene

> Agent claims to have reviewed 3 files for SQL injection. Reports: "All three use parameterized queries — safe."
>
> Human checks file 3 an hour later: raw string interpolation on line 47. Bug ships.

The agent didn't lie intentionally. It lied *plausibly*, because:
1. No evidence was required for the claim
2. Reading a file carefully costs tokens; asserting "looks fine" is free
3. The reviewer had no independent verification step

Proof-loop fixes all three.

---

## Core pattern

Split the work into two agent roles that cannot see each other's private context:

1. **Generator** — does the review, writes findings to a file
2. **Verifier** — in a separate session, reads only the file (not the conversation) and independently re-checks claims

If the verifier can't reproduce a finding, the finding gets marked `NEEDS-RECHECK`. Only findings both agents independently confirm pass as real.

### Why this works

The generator knows it will be verified. It can't fabricate because the verifier will grep for the cited line/function and find the bug (or fail to reproduce the absence).

The verifier has no incentive to rubber-stamp — it's a fresh context, it sees only the claim, it either confirms or doesn't. Calibration bias is lower because the verifier wasn't in the generator's "I want this to pass" loop.

### Cost

~$1-5 per review round depending on scope. Multi-round catches bugs that single-pass review misses (we've observed 5-10 real findings per round 2 that round 1 missed). The cost is worth it for security-sensitive or production-path code.

---

## Artifact requirements

Generator's findings file must contain, for each claim:

```markdown
# Review: <target>

## Finding 1 — <short title>

**Severity**: Critical | High | Medium | Low
**Confidence**: High | Medium | Low
**Location**: `<file>:<line-range>`
**Claim**: <what the generator asserts>
**Evidence**: <exact quote from file that proves the claim>
**Reproduction**:
    grep -n "<exact string>" <file>
**Impact if claim wrong**: <what breaks if this assessment is incorrect>
```

The `Reproduction` line is non-negotiable. It's a concrete command the verifier runs. If the command doesn't match what's in the file, the finding is stale or fabricated.

---

## Protocol

### Round 1 — Generator

Spawn agent (via Task() tool, sub-session, or teammate's Claude):

```
You are a code review agent.

Target: <file paths or directories>
Focus: <security | performance | correctness | style | specific concern>

For every finding, write a structured entry per the schema in
docs/code-review-agents.md. Include Reproduction commands for each claim.

Do NOT summarize — emit full entries. Do NOT say "this file looks good"
without a Finding entry describing what you verified.

Output to: <path>/review-round1.md
```

### Round 2 — Verifier

Fresh session, no knowledge of round 1's reasoning:

```
You are a code review verifier.

Read: <path>/review-round1.md
For each Finding, independently reproduce the claim by:
1. Running the Reproduction command exactly as given
2. Checking the Evidence matches the current file contents
3. Re-assessing whether the Severity and Confidence ratings are defensible

Emit a Verification for each Finding:
    VERIFIED | NEEDS-RECHECK | REJECTED | STALE

With justification. If Reproduction command fails or returns different
content than cited, mark STALE with explanation.

Do NOT add new findings. Your job is to audit round 1, not extend it.

Output to: <path>/review-round2.md
```

### Round 3 — Fixer (optional)

Only if round 2 confirms real bugs. Generator should not fix; fresh session fixes, then round 4 verifies the fix.

```
You are a code review fixer.

Read: <path>/review-round1.md + <path>/review-round2.md
Apply minimal changes to resolve VERIFIED findings only.
For each fix, write a Fix entry referencing the original Finding.
Do NOT refactor beyond the finding scope.

Output to: <path>/fixes.md + actual code changes
```

---

## Anti-patterns

### "Looks fine" without evidence
Generator emits: "Reviewed `auth.ts` — clean." No findings, no reproduction.

**Reject.** Empty output = agent didn't do the work. Re-spawn with explicit: "Emit at least one Finding entry per file reviewed, even if finding is 'no issues in section X, verified by grepping for pattern Y'."

### Generator doing its own verification
"I reviewed X and also verified X" in one session is not proof. Same context, same biases, self-confirmation bias wins.

**Reject.** Verification must happen in separate session without generator's reasoning trail.

### Round 2 adding new findings
Verifier says: "Round 1 missed Finding 4..."

**Reject.** Scope is audit of round 1, not extension. If round 2 finds new things, it's doing generator's job and loses the independence property. If new findings genuinely matter, run round 1 again after round 2 closes.

### Vague severity / confidence labels
"Severity: unclear, Confidence: moderate"

**Reject.** Labels must be on the 4-point scales (Critical/High/Medium/Low). "Medium confidence" is a decision, not a dodge. If you cannot pick a label, the claim isn't firm enough to review.

---

## Integration with mclaude

The review workflow maps naturally onto mclaude:

- **Locks**: generator claims `review-<target>` lock while writing round 1
- **Handoff**: when rounds complete, write handoff summarizing verified findings
- **Memory**: save each VERIFIED finding to `wings/<project>/rooms/<area>/gotchas/` as a drawer
- **Messages**: if another session needs to apply fixes, send message with `type: fix-request` containing the verified findings file path

### Parallelization

For large reviews, split across sub-agents and parallel sessions:

```
Round 1: 5 agents, each reviewing 1 concern (security, perf, types,
          concurrency, error handling) — emit 5 separate review-round1-<concern>.md

Round 2: 1 verifier per round 1 file, fresh session each — emit
          review-round2-<concern>.md

Round 3: 1 fixer merging all verified findings across concerns
```

This catches issues that serial single-agent review misses (agent fatigue, concern bias).

---

## Example prompt library

Put these in your project's `prompts/` folder and reference from `mclaude` or your agent runner:

- `prompts/review-security.md` — OWASP top 10 checklist style
- `prompts/review-concurrency.md` — race, deadlock, atomic ops
- `prompts/review-error-handling.md` — try/catch correctness, error propagation
- `prompts/review-performance.md` — N+1 queries, allocation hotspots
- `prompts/review-api-stability.md` — public API breaking changes

Each prompt includes the Finding/Reproduction/Evidence schema so the generator's output is verifiable regardless of domain.

---

## Known limits

- **Pure reasoning bugs**: some bugs (e.g. algorithm incorrectness) can't be reproduced by grep. For those, require the generator to write a failing test case as the Reproduction artifact instead.
- **Cross-file invariants**: verifier sees one file at a time via its reproduction command. For multi-file invariants, verifier must follow all cited paths. If reproduction command mentions multiple files, verifier runs against each.
- **Subjective findings** ("this naming is confusing"): don't pass this through proof-loop. Use it only for falsifiable claims.
- **Agent cost**: $5-15 per full review of a 20-file PR (3 rounds × multiple concerns). Cheaper than a prod incident; expensive for trivial diffs. Gate by change size.

---

## When to use proof-loop review

**Use**:
- Security-sensitive code (auth, crypto, input handling)
- Production-path dispatchers / routers / state machines
- PRs that touch infrastructure boundaries
- Any diff >500 LOC
- When single-pass review missed something real in the past

**Don't use**:
- Trivial diffs (typo fixes, whitespace)
- Prototypes you'll throw away
- UI tweaks with no state implications
- When the target is too small to split into rounds meaningfully

---

## Summary

- Split work: generator writes, separate verifier audits
- Every claim has structured evidence + reproducible command
- Verifier is fresh session, cannot see generator reasoning
- VERIFIED | NEEDS-RECHECK | REJECTED | STALE labels per finding
- Fixer is third role; fix scope limited to verified findings
- Integrate with locks / handoff / memory for multi-session teams
- Gate by PR size to control cost

Applied consistently, fabrication drops to near-zero because every claim has to survive a second independent pass over the actual code.
