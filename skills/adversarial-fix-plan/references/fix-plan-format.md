# Fix-Plan Working Format

Every working fix-plan file (`fixA.md`, `fixB.md`, `fixC.md`) follows this structure. The synthesis (`fixC.md`) adds a Source Plan Assessment preamble per `agents/fixp-synthesizer.md`; the rest of the structure is identical.

The final `fix-plan.md` produced in Round 4 (or revise mode) follows a slightly tighter format documented in `references/fix-plan-final-format.md`. Working artifacts may be more verbose and exploratory; the final is intended for downstream consumption.

## Required Sections

### 1. Strategy and Inputs

```markdown
## Strategy
{A — Surgical | B — Robust | C — Synthesis}

## Inputs Read
- diagnosis.md (cited locations: <list>)
- problem-statement.md
- repro.md (if present)
- Original pipeline context (if --original-slug): final-plan.md, implementation.md, coder-report.md
- Source files examined: <list>
```

### 2. Diagnosis Reference

Restate the diagnosed cause from `diagnosis.md` in your own words. Cite the specific evidence-chain locations.

For **PARTIALLY CONVERGED** diagnoses, also list the alternative causes from diagnosis.md's Candidate Causes — even if your strategy doesn't address them all, naming them surfaces what your plan does and doesn't cover.

### 3. Fix Approach

Your overall approach, with rationale for the scope you chose.

- **Surgical (A)**: explicit minimalism rationale. Why no defenses (or, if you propose any, why these are essential rather than optional). Answer the required question: "Is the diagnosed cause likely to recur via a slightly different path? If so, why are no defenses warranted?"
- **Robust (B)**: explicit defense rationale. What fragilities exist around this code, what defenses each guards against, why each is worth the additional change. For PARTIALLY CONVERGED diagnoses: explicitly address each alternative cause from diagnosis.md.
- **Synthesis (C)**: chosen position on the Surgical-Robust spectrum, justified by diagnosis confidence + environment + blast radius.

### 4. Changes

File-level overview. For each file changed:

```markdown
#### {file path}

- **Nature**: modify | add | remove
- **Description**: one or two sentences on what changes
- **Purpose**: cause-fix | defense
- **Confidence**: [HIGH] | [MEDIUM] | [LOW]
```

This is NOT diff-level. The implementation stage produces diffs. This is the plan.

### 5. Sequencing

Order of operations across the changes above. Required when there are multi-file changes with ordering constraints.

Examples of constraints:
- Migration must land before code that reads/writes the new column
- Feature flag must be added (default off) before gated reads
- New service endpoint must deploy before client code that calls it
- Tests must pass before next group can apply

If order does not matter, say so explicitly:

> Order does not matter — all changes are additive and independent.

The downstream `fix-coding` stage uses this section to construct commit groups.

### 6. Defenses

For **Robust (B)** and any **Synthesis (C)** that includes defenses:

```markdown
#### Defense D{n}: {short name}

- **Fragility guarded**: {what could go wrong without this defense}
- **Connection to cause**: {why this fragility is in scope given the diagnosed cause}
- **Justification**: {why this defense is worth the additional change vs. its risk/complexity cost}
- **Rejected alternatives**: {what you considered and didn't take, with reason}
```

For **Surgical (A)** or **Synthesis (C)** that includes no defenses:

```markdown
**Defenses**: None.

**Rationale**: {explicit reason — diagnosis confidence is HIGH, blast radius is small, no adjacent fragility identified, etc. Not just "didn't see any" — affirmative reason.}
```

For **PARTIALLY CONVERGED** diagnoses, the Defenses section MUST address each alternative cause from diagnosis.md's Candidate Causes:

```markdown
**Coverage of alternative causes**:
- **{Alternative H{n}}**: {addressed by D{m} | not addressed — reason}
- ...
```

### 7. Test Plan

What tests prove the fix works AND prove relevant regressions don't happen.

```markdown
- **Cause-is-fixed test**: {test that proves the diagnosed cause no longer reproduces. If repro.md is present, reference how its reproduction will become an automated test (or why it can't).}
- **Defense tests** (if any defenses): {one or more per defense}
- **Regression tests**: {tests for adjacent functionality the changes touch}
- **Existing tests that must still pass**: {especially if changes touch shared infrastructure}
```

### 8. Rollback Plan

Always present. Never "N/A".

For **prod**:

```markdown
- **Revert steps**: {exact git operations or deployment commands}
- **State reset**: {DB rollback, cache flush, anything else}
- **Monitoring post-deploy**: {what metric / log signal indicates the fix is working or has regressed}
- **Time-to-rollback estimate**: {minutes — for incident response planning}
```

For **dev**:

```markdown
- **Revert steps**: {file-level revert commands}
- **State reset**: {what dev-side state needs resetting}
```

### 9. Risk Assessment

```markdown
- **Blast radius**: {which other features could be affected by these changes}
- **Compatibility**: {API/schema changes? backward-compat concerns?}
- **Performance**: {could this fix slow something down? add latency?}
- **Most likely failure mode of the fix itself**: {what's the most plausible way this fix could be wrong}
- **Deployment risk** (prod only): {staged rollout considerations, traffic considerations, dependency on other deploys}
```

### 10. Confidence + Blocking Items

The final two lines of the file MUST be exactly:

```
CONFIDENCE: HIGH | MEDIUM | LOW
BLOCKING_ITEMS: N
```

Guidance:

- **HIGH**: diagnosis is CONVERGED, your scope decision is clearly justified, all proposed changes are concrete and verified against the code
- **MEDIUM**: diagnosis is PARTIALLY CONVERGED but your plan is well-grounded; OR scope decision involves a judgment call you're confident in but acknowledge could go either way
- **LOW**: significant uncertainty (e.g., flagged a BLOCKING_ITEM, or scope is unclear)

`BLOCKING_ITEMS` counts items that should block the synthesis or downstream stages:

- **Diagnosis disagreement**: 1 (or more) — the diagnosis seems wrong; orchestrator must decide whether to re-run investigate. The plan's `Status` should be set to BLOCKED in this case.
- **Missing required data not flagged in diagnosis**: 1+ — you discovered during planning that something else is needed.
- **Otherwise**: 0 — the plan is ready to advance.

## Format Guidelines

- Use markdown headers and lists.
- Cite specific files with paths relative to project root, plus line numbers where helpful.
- Annotate confidence on key claims throughout (not just at the end).
- Target 300–600 lines for a thorough plan. Shorter is fine for narrow fixes; longer for multi-file changes with sequencing.

## What This Format Is Not

- **Not a diff.** No code blocks showing exact text replacements; that's fix-coding's job.
- **Not a re-investigation.** If your diagnosis-fidelity check turns up a contradiction, set `Status: BLOCKED` and stop — do not pivot to investigating yourself.
- **Not a postmortem.** No "what we should have done differently"; this is forward-looking.
