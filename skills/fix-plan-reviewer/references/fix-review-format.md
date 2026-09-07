# Fix-Review Format

This is the **authoritative** template for `fix-review.md`, the deliverable produced by the fix-plan-reviewer skill. The SKILL.md may include a preview of this format for orientation; if the two diverge, this file wins.

The orchestrator parses fix-review.md to decide what to do next:

- **Verdict APPROVE** + `BLOCKING_ITEMS: 0` → advance to fix-coding
- **Verdict REVISE** + `BLOCKING_ITEMS ≥ 1` → re-dispatch adversarial-fix-plan in revise mode
- **Verdict REJECT** → escalate to user; possibly re-run adversarial-investigate

The structure below is mandatory. Skipping or reordering sections will fail the Phase 6 self-check in SKILL.md.

## Template

```markdown
# Fix Plan Review — {fix-slug}

**Generated**: {ISO 8601 timestamp}
**Reviewing**: {output_dir}/fix-plan.md
**Diagnosis**: {output_dir}/diagnosis.md
**Reviewer**: {Codex A | Codex B | Claude lead}

## Verdict

{APPROVE | REVISE | REJECT}

- **APPROVE**: no CRITICAL or BLOCKING issues (ADVISORY-only, or no issues at all). fix-plan can advance to fix-coding.
- **REVISE**: BLOCKING issues present (and no CRITICAL). fix-plan needs revision via revise mode of adversarial-fix-plan.
- **REJECT**: any CRITICAL issue present. fix-pipeline should escalate to user; possibly re-run adversarial-investigate.

The Verdict MUST match the severity counts. The Phase 6 self-check enforces this.

## Environment

{prod | dev}

For prod, severity weighting is heavier on rollback rigor (3E), regression risk (3D), and risk-assessment honesty (3G).

## Critical Issues

Issues with severity CRITICAL. The fix doesn't address the diagnosis, plan misinterprets the diagnosis, or the plan has a fatal flaw.

For each issue:

```markdown
### CRIT-{n}: {brief title}

- **Target Section**: {which section of fix-plan.md the issue applies to — e.g. "Diagnosis Reference", "Changes:auth/token.py:142", "Rollback Plan"}
- **Finding**: {what is wrong, stated concretely}
- **Why It Matters**: {why this is a problem, with reference to diagnosis evidence chain or actual code read in Phase 2}
- **Required Resolution**: {what the plan needs to address, NOT how to fix it — proposing fixes is revise mode's job}
- **Attack Angle**: {3A / 3H / etc.}
```

Empty section if no CRITICAL issues. Use exactly:

> No CRITICAL issues found.

## Blocking Issues

Issues with severity BLOCKING. The fix has problems that must be addressed before fix-coding, but the plan is salvageable via revise mode.

Same per-issue structure as CRITICAL (use `BLOCK-{n}` prefix). Empty section if none.

## Advisory Issues

Issues with severity ADVISORY. The fix is fundamentally sound but has improvable aspects.

Same per-issue structure (use `ADV-{n}` prefix). Empty section if none.

## Diagnosis Concerns

Out-of-scope concerns about the diagnosis itself (Attack Angle 3I). Severity is always ADVISORY at this layer; the orchestrator decides whether to re-run adversarial-investigate.

This section is **always answered**, even when no concerns are observed. Empty answers must be explicit:

> No diagnosis concerns observed during review. The diagnosis appears internally consistent and the cited evidence-chain locations match what is in the code.

When concerns ARE observed, structure each like:

```markdown
### DIAG-{n}: {brief title}

- **Observation**: {what you noticed during plan review that suggests the diagnosis is wrong}
- **Evidence**: {file:line or log reference}
- **Why this is out-of-scope here**: {one line — this skill does not re-investigate; the orchestrator decides whether to re-run adversarial-investigate}
```

Note: concrete plan-vs-diagnosis discrepancies (e.g., the plan cites a different cause than diagnosis.md does) belong in Critical Issues under Attack Angle 3H, not here. This section is for reviewer-side suspicion that the upstream diagnosis itself missed the mark.

## Strengths

What the plan got right. Brief — the goal is to help the reviser preserve good parts during revise mode, not to praise everything.

```markdown
- {one-line specific call-out, e.g. "Sequencing section correctly orders the migration before dependent code reads"}
- {...}
```

Empty section is acceptable if the plan has no specific strengths worth preserving (rare — usually at least the diagnosis-fidelity or the identified change locations are right).

## Attack Angle Summary

Mandatory transparency table. Every angle from 3A through 3H gets a row, even when no issues were found. Missing rows fail the Phase 6 self-check.

| Angle | Result |
|---|---|
| 3A Cause Coverage | {N issues found / clean} |
| 3B Defense Adequacy | {N issues found / clean / N/A for CONVERGED diagnoses with no defenses} |
| 3C Scope Correctness | {N issues found / clean} |
| 3D Regression Risk | {N issues found / clean} |
| 3E Rollback Integrity | {N issues found / clean} |
| 3F Test Plan Integrity | {N issues found / clean} |
| 3G Risk Assessment Honesty | {N issues found / clean} |
| 3H Plan-vs-Diagnosis Interpretation | {N issues found / clean} |

3I (Diagnosis Concerns) is NOT in this table — it's an out-of-band signal handled by its own section above. The Diagnosis Concerns section's presence (even when empty) is the equivalent confirmation that 3I was checked.

## Confidence + Blocking Items

The last two lines of `fix-review.md` MUST be exactly:

```
CONFIDENCE: HIGH | MEDIUM | LOW
BLOCKING_ITEMS: N
```

Guidance:

- **CONFIDENCE: HIGH** — every attack angle was checked thoroughly, the actual code was read for every file the plan touches, severity assignments are clear-cut
- **CONFIDENCE: MEDIUM** — most angles were checked thoroughly; one or more required judgment calls between severity levels
- **CONFIDENCE: LOW** — could not read all referenced source files (record the gap as a CRITICAL-or-BLOCKING issue), or the plan is structurally unusual enough that the standard angles didn't cleanly apply

`BLOCKING_ITEMS` formula:

```
BLOCKING_ITEMS = count(CRITICAL issues) + count(BLOCKING issues)
```

ADVISORY issues do NOT count toward `BLOCKING_ITEMS`. Diagnosis Concerns (3I) do NOT count toward `BLOCKING_ITEMS` (they are out-of-band ADVISORY signals).

## Severity Quick Reference (for self-check + orchestrator)

| Severity | Triggers (typical) | Verdict | Counts toward BLOCKING_ITEMS? |
|---|---|---|---|
| **CRITICAL** | Cause coverage miss (3A), plan misinterprets diagnosis (3H), fatal flaw | REJECT | Yes |
| **BLOCKING** | Test plan doesn't exercise failing path (3F), prod rollback won't work (3E + prod), missing defense for stated alternative (3B), scope-too-narrow with named adjacent fragility (3C), regression risk for prod (3D + prod) | REVISE | Yes |
| **ADVISORY** | Scope-too-broad without risk (3C), risk-assessment downplay without material consequence (3G), regression risk for dev (3D + dev), rollback looseness for dev (3E + dev) | APPROVE | No |
| **DIAG-ADVISORY** (3I only) | Reviewer-side suspicion the diagnosis itself missed the mark | (does not affect Verdict) | No |

When uncertain between CRITICAL and BLOCKING, choose CRITICAL. Under-rating ships bad fixes; over-rating adds a user touchpoint. The asymmetry is intentional.

When uncertain between BLOCKING and ADVISORY, choose BLOCKING. Same reasoning.

## Rules

1. Always write `fix-review.md`, even when no issues are found. Silent failure is forbidden (Hard Rule 4 in SKILL.md).
2. Every attack angle 3A–3H must have a row in the Attack Angle Summary table.
3. The Diagnosis Concerns section must be present (even if empty) — 3I is always answered.
4. Issues describe *what is wrong* and *why*, not *how to fix*. Proposing fixes belongs to revise mode.
5. The last two lines are `CONFIDENCE:` and `BLOCKING_ITEMS:` in that order — the orchestrator parses these.
6. The Verdict must match severity counts (Phase 6 self-check enforces this).
7. The reviewer touches `fix-review.md` last so its mtime reflects review completion (revise-mode mtime check depends on this).
