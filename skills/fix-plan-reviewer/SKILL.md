---
name: fix-plan-reviewer
description: >
  Single-agent adversarial reviewer for fix plans. Reads diagnosis.md and
  fix-plan.md, verifies the plan actually addresses the diagnosed cause,
  attacks the plan from multiple angles (cause coverage, defense adequacy,
  scope correctness, regression risk, rollback integrity, test plan integrity,
  risk honesty), and produces fix-review.md with severity-rated issues. Does
  NOT propose fixes for the issues — that is the revise mode of
  adversarial-fix-plan. Does NOT re-investigate the diagnosis. Trigger as the
  review stage of a fix-pipeline, after adversarial-fix-plan has produced
  fix-plan.md.
---

# Fix Plan Reviewer

## Overview

A single procedural reviewer that attacks fix-plan.md and produces fix-review.md. The reviewer is itself the adversarial pressure on the plan; this skill does not run Agent Teams or multiple rounds.

**Worker routing.** The orchestrator may run this skill locally (Claude lead) or dispatch to a Codex worker — same pattern as `proposal-reviewer` in the build pipeline. Codex is the recommended default because the fix-plan was produced by Claude (in `adversarial-fix-plan`); an external Codex review catches Claude-side blind spots. Local Claude execution is the fallback when Codex is unavailable.

The orchestrator uses fix-review.md's severity counts to decide:

- **CRITICAL > 0** → escalate to user (fix-plan has a fundamental problem; possibly the diagnosis is wrong)
- **BLOCKING > 0** → re-dispatch adversarial-fix-plan in revise mode
- **Only ADVISORY** → advance to fix-coding (or revise at user discretion)

## When to Use

- After `adversarial-fix-plan` produces `fix-plan.md` and before `fix-coding`
- The reviewer runs once per fix-plan version. After a revise pass produces fix-plan.md (v2), the reviewer is NOT automatically re-run unless the orchestrator decides another review pass is warranted.

**Expected runtime**: ~5–10 min single-agent. The skill is bounded by the size of fix-plan.md, the number of files it touches (each must be read per Hard Rule 3), and the depth of the diagnosis. Larger fix plans with many files or PARTIALLY CONVERGED diagnoses run longer.

## When NOT to Use

- **No fix-plan.md exists.** Hard refusal — see Hard Rules.
- **fix-plan.md has BLOCKED status** (INSUFFICIENT EVIDENCE diagnosis). The plan is already blocked; reviewing it adds no value. Orchestrator should escalate, not review.
- **Re-reviewing the same fix-plan.md without changes.** Wasted work.
- **Build-pipeline reviews.** Use `proposal-reviewer` for those.

## Hard Rules

1. **Attack the plan, do not propose fixes.** Issues describe *what is wrong* and *why*, not *how to fix*. Proposing fixes belongs to the revise mode of adversarial-fix-plan.
2. **Verify against the diagnosis, not just the plan.** The reviewer must read diagnosis.md and answer: "would the proposed changes actually make the diagnosed cause stop happening?" Trusting the plan's self-claim is forbidden.
3. **Read the actual code that would be touched.** When the plan says it will modify file X, the reviewer reads file X and confirms its structure matches the plan's assumptions. Trusting the plan's description of the code is forbidden.
4. **Always produce fix-review.md.** Empty issue sections are acceptable. Silent failure is forbidden.
5. **Severity ratings are conservative.** When uncertain between two levels, choose the higher (more severe). Under-rating ships bad fixes; over-rating adds a revision pass.
6. **Do not re-investigate.** This skill does not challenge the diagnosis itself. It checks whether the plan addresses what the diagnosis says. If the plan misinterprets the diagnosis, that is a CRITICAL issue. If the diagnosis itself seems wrong, that is OUT OF SCOPE — flag the concern in fix-review.md but do not re-diagnose.
7. **Single agent, no sub-agents.** This skill does not spawn teammates and does not run adversarial rounds.
8. **No code changes.** This skill produces a review document. It does not modify source code or fix-plan.md.

## Required Reads

Before writing the review:

1. `{output_dir}/fix-plan.md` — the plan being reviewed
2. `{output_dir}/diagnosis.md` — the diagnosis the plan must address
3. `{output_dir}/problem-statement.md` — original problem context
4. `{output_dir}/repro.md` — if present, repro context
5. `~/.claude/skills/fix-plan-reviewer/references/fix-review-format.md` — output template (authoritative)

Optional:

- `.claude/forge-project.yml` (fallback: `~/.claude/forge-project.yml`) — project config. Useful when reviewing test plans for protected pages (auth config) or when env-specific service settings affect the rollback assessment. Skip if not present.

If `--original-slug` is provided, also read for context (read-only):

7. `.dev/proposals/{original-slug}/final-plan.md`
8. `.dev/proposals/{original-slug}/implementation.md`
9. `.dev/proposals/{original-slug}/coder-report.md`

The reviewer ALSO reads the actual source files the plan proposes to touch. This is mandatory — see Hard Rule 3.

## Inputs

Required:

- `--output-dir` — path to the fix directory. fix-plan.md lives here; fix-review.md will be written here.
- `--env prod | dev` — affects severity weighting. Prod issues weight higher (rollback rigor, deployment risk, regression blast radius).

Optional:

- `--original-slug` — slug of the original build pipeline. Enables read-only context access.

## Phase 0: Config + Inputs

1. Read `.claude/forge-project.yml` if present (optional — see Required Reads). Skip without error if absent.
2. Read `fix-plan.md` from `--output-dir`
3. **Empty-args short-circuit.** If `--output-dir` is missing, `--env` is missing, or `fix-plan.md` does not exist, stop:
   ```
   FIX-PLAN-REVIEWER ERROR: Missing required input. Provide --output-dir,
   --env (one of `prod` | `dev`), and ensure fix-plan.md exists.
   ```
4. **Status check.** If fix-plan.md has `Status: BLOCKED`, stop:
   ```
   FIX-PLAN-REVIEWER ERROR: fix-plan.md has BLOCKED status. Reviewing a
   blocked plan adds no value. The orchestrator should escalate.
   ```
5. Read `diagnosis.md`, `problem-statement.md`, `repro.md` (if present)
6. If `--original-slug` is provided, confirm `.dev/proposals/{original-slug}/` exists; read its artifacts

## Phase 1: Attack Surface Mapping

Parse fix-plan.md and extract:

- The cited root cause (from Diagnosis Reference)
- The fix approach (Surgical / Robust / synthesized position)
- Each file the plan proposes to modify, add, or remove
- Each defense the plan adds (with stated justification)
- Each test the plan calls for
- The rollback plan
- The risk assessment claims

This is the structured target list for the review attack.

## Phase 2: Read the Actual Code

For each file the plan proposes to touch:

- Read the file
- Confirm the file exists at the stated path
- Confirm the structure matches the plan's assumptions (function names, class layouts, dependencies)
- Note discrepancies between what the plan describes and what's actually there

This phase is mandatory and is the basis for many CRITICAL findings. A plan that misdescribes the code it touches will produce a fix that doesn't compile or doesn't behave as intended.

## Phase 3: Attack Angles

The reviewer runs each of these attacks against the plan. Each attack is a question the reviewer must explicitly answer in fix-review.md, even if the answer is "no issues found."

### 3A. Cause Coverage

**Question: Would the proposed changes actually make the diagnosed cause stop happening?**

- Map each diagnosed cause element to the plan changes that address it
- Identify any cause element with no corresponding change
- Identify any change that doesn't serve any cause element (potential scope creep — see 3C)

A miss here is typically CRITICAL. The fix doesn't fix.

### 3B. Defense Adequacy

**Question: For PARTIALLY CONVERGED diagnoses, did the plan address the alternative causes? For all diagnoses, are the defenses (if any) defending against the right things?**

- Check Defense section against the diagnosis's alternative causes (if any)
- Check each defense against its stated justification — is the justification grounded in real fragility, or cargo-culted?
- Check whether obvious adjacent fragilities (related code paths that could fail similarly) are addressed or explicitly ignored with rationale

Misses here are typically BLOCKING.

### 3C. Scope Correctness

**Question: Is the fix appropriately scoped — neither too narrow nor too broad?**

Two directions:

- **Too narrow**: are there obvious adjacent fragilities the fix should address?
- **Too broad**: are there changes that don't serve the diagnosis (unrelated refactors, scope creep)?

Too narrow is typically BLOCKING. Too broad is typically ADVISORY unless the over-broad change introduces real risk, in which case BLOCKING.

### 3D. Regression Risk

**Question: What could break that the test plan doesn't cover?**

- For each file touched, identify dependents (other files that import or call into it)
- Identify dependents whose tests would NOT exercise the changes
- Identify behaviors of the changed code that the change might inadvertently alter

Misses here are typically BLOCKING for prod, ADVISORY for dev.

### 3E. Rollback Integrity

**Question: Will the rollback plan actually work?**

Especially important for prod. The reviewer asks:

- Are there schema or data changes that can't be cleanly reverted?
- Are there cascading effects (one revert requires another)?
- Does "just revert the commit" actually restore working state, or does it leave intermediate state?
- For prod: are deployment considerations realistic? Monitoring named?

Misses here are typically BLOCKING for prod, ADVISORY for dev.

### 3F. Test Plan Integrity

**Question: Do the proposed tests actually prove the fix works AND the regressions don't happen?**

The killer question: **would the proposed tests have caught the original bug?**

- A test that passes before and after the fix because it doesn't exercise the failing path is worthless
- Verify the test plan exercises the actual cause, not adjacent code
- Verify regression tests actually exercise the dependents identified in 3D

**When repro.md is absent**, the killer question is harder to answer because the concrete shape of the original bug is less explicit. Fall back to: "do the proposed tests exercise the diagnosed cause path identified in `diagnosis.md`'s evidence chain?" This is weaker than the repro-driven check but still actionable — a test that doesn't traverse the cited file:line locations from the evidence chain almost certainly wouldn't catch the bug.

Misses here are typically BLOCKING.

### 3G. Risk Assessment Honesty

**Question: Did the planner downplay risks or miss any?**

- Cross-reference the plan's stated risks against the reviewer's attack findings — are findings from 3A-3F that should have appeared in the plan's Risk Assessment actually there?
- Check for honesty about blast radius
- Check for honesty about deployment risk (prod only)

Misses here are typically ADVISORY unless they materially change the risk calculus, in which case BLOCKING.

### 3H. Plan-vs-Diagnosis Interpretation

**Question: Does the plan correctly interpret what the diagnosis says?**

- Does the cited root cause in fix-plan match what diagnosis.md actually committed to?
- For PARTIALLY CONVERGED: does the plan acknowledge the alternatives the diagnosis flagged?

Misses here are typically CRITICAL — the plan is solving a different problem than the diagnosis identified.

### 3I. Diagnosis-Doubt Flag (out of scope)

**Question (always answered, even if empty): In the course of reviewing the plan, did I see any signal that the diagnosis itself is wrong (not just the plan's interpretation of it)?**

This angle is checked on every run, parallel to 3A–3H. The reviewer must explicitly answer in fix-review.md's "Diagnosis Concerns" section — even when the answer is "no concerns observed." Passive noticing is not enough; an empty section means the reviewer affirmatively saw no signal, not that the reviewer forgot to look.

If concerns ARE observed, the reviewer flags them in the "Diagnosis Concerns" section with severity ADVISORY (because the reviewer is not a re-investigator). The orchestrator decides whether to re-run investigate.

This is NOT a CRITICAL issue against the plan — the plan can only be as good as the diagnosis. It's an out-of-band signal to the orchestrator. Concrete plan-vs-diagnosis discrepancies still surface as CRITICAL via 3H; 3I is for reviewer-side suspicion that the upstream diagnosis itself missed the mark.

## Phase 4: Severity Assignment

Each finding from Phase 3 gets a severity:

| Severity | Meaning | Orchestrator Action |
|---|---|---|
| **CRITICAL** | Fix doesn't address the diagnosis, plan misinterprets diagnosis, or plan has fatal flaw | Escalate to user; possibly re-run investigate |
| **BLOCKING** | Fix has problems that must be addressed before fix-coding | Re-dispatch adversarial-fix-plan in revise mode |
| **ADVISORY** | Fix is fundamentally sound but has improvable aspects | Advance to fix-coding (or revise at user discretion) |

Severity guidance:

- Cause coverage misses (3A): CRITICAL if any cause element is unaddressed; BLOCKING if addressed but inadequately
- Plan-vs-diagnosis misinterpretation (3H): CRITICAL
- Test plan that doesn't exercise the failing path (3F): BLOCKING
- Rollback that won't work for prod (3E with --env prod): BLOCKING
- Defense missing for stated PARTIALLY CONVERGED alternative (3B): BLOCKING
- Scope too narrow with named adjacent fragility (3C): BLOCKING
- Scope too broad without risk (3C): ADVISORY
- Risk assessment downplay without material consequence (3G): ADVISORY

When uncertain between two severities, choose the higher.

## Phase 5: Write fix-review.md

Write `{output_dir}/fix-review.md` following the structure in `references/fix-review-format.md` (authoritative). The template below is a preview for orientation; if it diverges from the reference file, the reference file wins.

```markdown
# Fix Plan Review — {slug}

## Verdict
{APPROVE | REVISE | REJECT}
- APPROVE: no CRITICAL or BLOCKING issues (ADVISORY-only, or no issues at all). fix-plan can advance to fix-coding.
- REVISE: BLOCKING issues present (and no CRITICAL). fix-plan needs revision via revise mode.
- REJECT: any CRITICAL issue present. fix-pipeline should escalate to user.

## Environment
{prod | dev}

## Critical Issues
{Issues with severity CRITICAL. Empty section if none.}

For each:
- **Issue**: brief title
- **Target Section**: which section of fix-plan.md the issue applies to
- **Finding**: what is wrong
- **Why It Matters**: why this is a problem, with reference to diagnosis or code
- **Required Resolution**: what the plan needs to address (NOT how to fix it)

## Blocking Issues
{Issues with severity BLOCKING. Same per-issue structure. Empty section if none.}

## Advisory Issues
{Issues with severity ADVISORY. Same per-issue structure. Empty section if none.}

## Diagnosis Concerns
{Out-of-scope concerns about the diagnosis itself. Empty if none. Severity always ADVISORY at this layer.}

## Strengths
{What the plan got right. This helps the reviser preserve good parts during revise mode.
 Brief — no need to praise everything; just call out what specifically should be preserved.}

## Attack Angle Summary
{One line per attack angle (3A-3H), confirming it was checked and the result.
 Required for transparency — the reviewer must show its work.}

| Angle | Result |
|---|---|
| 3A Cause Coverage | <issues found / clean> |
| 3B Defense Adequacy | <issues found / clean / N/A for CONVERGED diagnoses> |
| 3C Scope Correctness | <issues found / clean> |
| 3D Regression Risk | <issues found / clean> |
| 3E Rollback Integrity | <issues found / clean> |
| 3F Test Plan Integrity | <issues found / clean> |
| 3G Risk Assessment Honesty | <issues found / clean> |
| 3H Plan-vs-Diagnosis Interpretation | <issues found / clean> |

## Confidence + Blocking Items
CONFIDENCE: HIGH | MEDIUM | LOW
BLOCKING_ITEMS: N

(BLOCKING_ITEMS = count of CRITICAL + BLOCKING issues. ADVISORY does not count toward blocking.)
```

The Attack Angle Summary table is mandatory — it forces the reviewer to confirm each angle was checked, even when no issues were found. This prevents silent skipping.

## Phase 6: Output Quality Gate (Self-Check Before FORGE_DONE)

Before signaling completion, verify the written `fix-review.md` on disk:

1. The file exists at `{output_dir}/fix-review.md` and is non-empty
2. All required sections are present, in this order:
   - Verdict (with one of APPROVE / REVISE / REJECT)
   - Environment
   - Critical Issues
   - Blocking Issues
   - Advisory Issues
   - Diagnosis Concerns (may be empty, but the section header must exist with the explicit answer to 3I)
   - Strengths
   - Attack Angle Summary
   - Confidence + Blocking Items (last two lines exactly)
3. The Attack Angle Summary table contains a row for each of 3A, 3B, 3C, 3D, 3E, 3F, 3G, 3H — no row may be missing or marked "skipped"
4. The Verdict matches the severity counts:
   - APPROVE iff no CRITICAL and no BLOCKING
   - REVISE iff no CRITICAL but at least one BLOCKING
   - REJECT iff at least one CRITICAL
5. The last two lines are exactly `CONFIDENCE: {HIGH|MEDIUM|LOW}` and `BLOCKING_ITEMS: {N}`, where N = count of CRITICAL + BLOCKING
6. The reviewer touches `fix-review.md` last so its mtime reflects review completion (lets the downstream revise-mode mtime check work correctly)

If any check fails, fix the file and re-verify. The Attack Angle Summary table specifically is a load-bearing transparency mechanism — a missing row means the reviewer skipped an attack angle silently, which violates Hard Rule 4 in spirit even if the file exists.

Once the self-check passes, signal FORGE_DONE.

## Error Recovery

| Error | Action |
|---|---|
| `forge-project.yml` not found | Skip without error (optional input) |
| `--output-dir` missing | Stop, report missing input |
| `--env` missing | Stop, report missing input |
| `fix-plan.md` not found | Stop, report missing input |
| `diagnosis.md` not found | Stop, report missing diagnosis |
| `fix-plan.md` has BLOCKED status | Stop, report — reviewing blocked plan adds no value |
| Plan references files that don't exist | Record as CRITICAL issue under 3A or 3H |
| Plan structure doesn't match real code | Record as CRITICAL or BLOCKING under 3H |
| Cannot read source files (permissions, missing) | Record as note in fix-review.md, downgrade affected attack angles to "could not verify" |

## Output Directory

```
{output_dir}/
├── problem-statement.md     ← Input
├── repro.md                 ← Input (if present)
├── diagnosis.md             ← Input
├── fix-plan.md              ← Input (the plan being reviewed)
└── fix-review.md            ← Output (this skill writes this)
```

## What This Skill Does NOT Do

- Does not propose fixes for issues found (that's revise mode of adversarial-fix-plan)
- Does not re-investigate or challenge the diagnosis (only flags interpretation issues)
- Does not modify fix-plan.md or any source code
- Does not spawn sub-agents or run adversarial rounds
- Does not have an interactive mode
- Does not skip attack angles silently — every angle gets explicit confirmation in the Attack Angle Summary

## Reference Files

| File | When to read | Purpose |
|---|---|---|
| `references/fix-review-format.md` | When writing fix-review.md | Template for the output artifact |

## Invocation

```
fix-plan-reviewer \
  --output-dir .dev/fixes/{original-slug}/{fix-slug}/ \
  --env prod \
  --original-slug {original-slug}
```

The orchestrator is responsible for:

- Invoking this skill after adversarial-fix-plan produces fix-plan.md
- Reading the resulting fix-review.md
- Routing based on severity counts:
  - CRITICAL > 0 → escalate to user
  - BLOCKING > 0 → re-dispatch adversarial-fix-plan (revise mode auto-detects fix-review.md)
  - Only ADVISORY → advance to fix-coding (or revise at user discretion)

This skill is responsible for:

- Reading inputs
- Performing all 8 attack angles + diagnosis-doubt flag
- Writing fix-review.md with severity-rated findings and the Attack Angle Summary
- Reporting completion via standard FORGE_DONE callback (handled by orchestrator dispatch protocol)
