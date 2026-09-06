# Diagnosis Format

The diagnosis is the deliverable of the adversarial-investigate skill. It MUST be written to `{output_dir}/diagnosis.md` after Round 4 reconciliation. It commits to a root cause, OR honestly admits insufficient evidence and specifies what data would resolve it.

This is **diagnosis**, not fix planning. The next stage of the fix-pipeline takes this file as input and produces a fix plan; this file does not propose code changes.

## Template

```markdown
# Diagnosis — {fix-slug}

**Generated**: {ISO 8601 timestamp}
**Original pipeline**: {original-slug or "n/a"}
**Inputs**: problem-statement.md, repro.md (if present)

## Convergence Status

{CONVERGED | PARTIALLY CONVERGED | INSUFFICIENT EVIDENCE}

## Environment

{prod | dev}

If hybrid investigation was used (prod bug verified in dev), note it here:
> "Verified in dev: {what was bridged} — dev/prod parity confirmed for {component}."

## Root Cause

For **CONVERGED**:
> The cause is {precise statement, not a topic — name the file, function, line, and the exact wrong behavior}.

For **PARTIALLY CONVERGED**:
> The top hypothesis is {precise statement}, supported by {evidence summary}. Meaningful uncertainty remains around {what's not pinned down}. See Candidate Causes and Required Data.

For **INSUFFICIENT EVIDENCE**:
> No single cause can be committed to with current evidence. See Candidate Causes for the candidates considered and Required Data for what would distinguish them.

## Evidence Chain

For each step in the causal chain from origin to visible failure: what was observed, where (file:line, log timestamp, response field), and how it connects to the next step. The chain MUST explain ALL observed symptoms, not just the most prominent one.

```
1. {origin observation} — {file:line} or {log ref} — {state value if relevant}
   ↓ {mechanism: what this causes}
2. {next state} — {evidence ref}
   ↓ {mechanism}
3. {visible failure} — {evidence ref}
```

If symptoms include multiple visible effects (e.g., wrong response AND error log AND stuck queue), each must trace back to the chain — or the chain is incomplete.

## Hypotheses Considered

Audit trail. Every hypothesis evaluated during investigation should appear here, including ones rejected.

| ID | Name | Evidence For | Evidence Against | Verdict | Reason |
|----|------|--------------|------------------|---------|--------|
| H1 | {name} | {brief refs} | {brief refs} | committed | {why this won} |
| H2 | {name} | {brief refs} | {brief refs} | rejected | {why ruled out} |
| H3 | {name} | {brief refs} | {brief refs} | deferred | {why kept open} |

## Candidate Causes

(Only present for **PARTIALLY CONVERGED** and **INSUFFICIENT EVIDENCE**.)

For each candidate that wasn't rejected, give a structured profile:

```markdown
### Candidate {id}: {name}

**Description**: {what this candidate says is causing the bug}

**Evidence supporting**:
- {ref + brief}

**Evidence against**:
- {ref + brief}

**Likelihood vs. other candidates**: {what makes it more or less likely than the others}

**What would confirm or rule out**: {specific data — referenced in Required Data below}
```

## Required Data

(Only present for **PARTIALLY CONVERGED** and **INSUFFICIENT EVIDENCE**.)

Structured list of data that would distinguish remaining candidates. The fix-pipeline orchestrator may surface this list to the user or trigger data gathering.

```yaml
required_data:
  - id: D1
    distinguishes: [hypothesis-2, hypothesis-3]
    description: "Production logs from auth service for the 5-minute window around the reported failure timestamp 2026-04-30T08:14:32Z"
    source: "Railway logs / production"
    blocking_severity: high
  - id: D2
    distinguishes: [hypothesis-1, hypothesis-2]
    description: "Whether refresh-token expiry was extended in last deployment"
    source: "Git history / config diff"
    blocking_severity: medium
```

`blocking_severity`:
- **high** — fix-pipeline must not advance to fix-plan without this data
- **medium** — fix-plan can proceed but should be revisited if this data later contradicts the chosen plan
- **low** — nice-to-have for completeness; does not gate downstream stages

Each entry must be specific and actionable. "More logs" is not Required Data; "production auth-service logs for the 5-minute window around 2026-04-30T08:14:32Z" is.

## Symptom vs Cause Analysis

Explicit answer (mandatory section):

> Was the visible error a symptom of a deeper cause?

- **Yes** — and identify what's upstream of where it manifests, with evidence.
- **No** — and explain why the visible error is itself the cause, not a downstream artifact.

This section exists specifically because reconcilers under pressure tend to commit to the loudest visible failure point even when the trace shows it's downstream.

## Reconciliation Trail

Reference, not duplication. Detailed accept/reject reasoning lives in `reconciliation-notes.md`. This section just summarizes:

> Round 3 critiques addressed: A raised {N} points, B raised {M} points. {X} accepted, {Y} partial, {Z} rejected. Convergent signals (both A and B): {N}. See `reconciliation-notes.md` for full audit trail.

## Confidence + Blocking Items

The final two lines MUST be exactly:

```
CONFIDENCE: HIGH | MEDIUM | LOW
BLOCKING_ITEMS: N
```

Guidance:

- **HIGH** — single committed cause, evidence chain explains all symptoms, alternatives firmly ruled out. Typically pairs with `CONVERGED`.
- **MEDIUM** — committed cause but with caveats, OR insufficient-evidence finding with clearly identified gaps. Typically pairs with `PARTIALLY CONVERGED`.
- **LOW** — investigation was significantly limited; downstream stages should treat this diagnosis as preliminary. Typically pairs with `INSUFFICIENT EVIDENCE`.

`BLOCKING_ITEMS`:
- For CONVERGED with no required_data: usually 0
- For PARTIALLY CONVERGED: count of `blocking_severity: high` entries in `required_data`
- For INSUFFICIENT EVIDENCE: defaults to N > 0 — the fix-pipeline cannot productively advance to fix-plan without resolving the ambiguity

## Rules

1. Always write `diagnosis.md`, even on INSUFFICIENT EVIDENCE. The honest negative is itself the deliverable.
2. The Root Cause section must commit precisely (or admit precisely) — vagueness is failure.
3. Every hypothesis evaluated must appear in the audit trail with a verdict.
4. The Evidence Chain must explain ALL observed symptoms.
5. The Symptom vs Cause Analysis is mandatory and must be answered explicitly.
6. The last two lines are `CONFIDENCE:` and `BLOCKING_ITEMS:` — this is the contract the orchestrator parses.
7. Do not propose code changes. The deliverable is the diagnosis; fix planning is a downstream stage.
