# Fix-Plan Final Format

This is the format for the **final** `fix-plan.md` produced by the reconciler in Round 4 (full mode) or by the reviser in revise mode. It is the deliverable consumed by downstream stages (`fix-plan-reviewer`, `fix-coding`).

The final format is tighter than the working format (`references/fix-plan-format.md`) — it omits exploratory content (the synthesizer's Source Plan Assessment, individual planner attribution) and focuses on what fix-coding needs to execute.

## Template

```markdown
# Fix Plan — {fix-slug}

**Generated**: {ISO 8601 timestamp}
**Diagnosis**: {output_dir}/diagnosis.md
**Original pipeline**: {original-slug or "n/a"}
**Version**: v1 | v2 | v3 (incremented in revise mode)

## Status

{ACTIVE | BLOCKED}

For BLOCKED, also include:

> **Blocking reason**: {one-sentence summary}
> **Required action**: {what the orchestrator should do — re-run investigate, escalate to user, etc.}

When status is BLOCKED, sections after Status describe the blocking reason and most subsequent sections may be N/A — but the file MUST still be written.

## Diagnosis Reference

{Restate the cause precisely, citing the specific evidence-chain location from diagnosis.md.}

For PARTIALLY CONVERGED diagnoses, also list:

> **Alternative causes from diagnosis.md**:
> - {H{n}: name} — addressed by Defense D{m} | not addressed — reason
> - ...

## Environment

{prod | dev}

If hybrid (prod bug verified in dev) was relevant during planning, note:
> "Plan verified against dev reproduction; production rollout follows the Rollback Plan section."

## Fix Approach

{The chosen position on the Surgical-Robust spectrum, with rationale grounded in:
 - Diagnosis confidence (HIGH = leans Surgical, MEDIUM = leans Robust)
 - Environment (prod = leans Robust, dev = leans Surgical)
 - Blast radius (high = leans Robust)

 If pure Surgical: explicit answer to "is the diagnosed cause likely to recur via a slightly different path? If so, why no defenses?"
 If Robust: explicit justification per defense.
 If somewhere on the spectrum: explicit position rationale.}

## Changes

File-level overview. For each file:

| File | Nature | Purpose | Description |
|------|--------|---------|-------------|
| `path/to/file.py` | modify | cause-fix | One-sentence description |
| `path/to/new_file.py` | add | defense | One-sentence description |
| `path/to/test_x.py` | add | test | One-sentence description |
| `migrations/2026_04_30_*.sql` | add | cause-fix | Migration adds column X with default Y |

This is NOT diff-level. The implementation stage produces diffs.

## Sequencing

Order of operations.

For multi-file changes with ordering constraints:

```
1. {first change} — {why this must come first}
2. {next change} — {dependency on step 1}
3. ...
```

If order does not matter, state explicitly:

> Order does not matter — all changes are additive and independent. fix-coding may apply them in any order.

The downstream `fix-coding` stage uses this section to construct commit groups.

## Defenses

For each defense (Robust/synthesis-with-defenses):

```markdown
### Defense D{n}: {short name}

- **Fragility guarded**: {what could go wrong without this defense}
- **Connection to cause**: {why this fragility is in scope given the diagnosed cause}
- **Justification**: {why worth the additional change cost}
```

OR, when there are no defenses:

> **Defenses**: None.
>
> **Rationale**: {explicit reason — diagnosis confidence HIGH, small blast radius, no adjacent fragility identified, etc.}

## Test Plan

```markdown
### Cause-is-fixed test
{Test name + location + what it asserts. If repro.md exists, reference how the reproduction maps to this test.}

### Defense tests (if any)
- D{n}: {test name + location + what it asserts}

### Regression tests
- {test name + location + what it covers}

### Existing tests that must still pass
- {test or test suite}
```

## Rollback Plan

Always present.

For **prod**:

```markdown
### Revert steps
1. {git operation or deployment command}
2. {next step}

### State reset
{DB rollback, cache flush, etc.}

### Monitoring post-deploy
- {metric / log signal that indicates the fix is working}
- {metric / log signal that indicates regression}

### Time-to-rollback estimate
~{N} minutes
```

For **dev**:

```markdown
### Revert steps
{file-level revert command(s)}

### State reset
{what dev-side state needs resetting}
```

## Risk Assessment

```markdown
- **Blast radius**: {which other features could be affected}
- **Compatibility**: {API/schema changes? backward-compat concerns?}
- **Performance**: {latency, throughput, resource usage}
- **Most likely failure mode of the fix itself**: {plausible way this fix could be wrong}
- **Deployment risk** (prod only): {staged rollout, traffic, dependencies}
```

## Confidence + Blocking Items

The last two lines of `fix-plan.md` MUST be exactly:

```
CONFIDENCE: HIGH | MEDIUM | LOW
BLOCKING_ITEMS: N
```

Guidance:

- **HIGH**: CONVERGED diagnosis, scope decision is clearly justified, all changes are concrete
- **MEDIUM**: PARTIALLY CONVERGED with reasonable plan; or scope involves a defensible judgment call
- **LOW**: status is BLOCKED, OR significant uncertainty remains

`BLOCKING_ITEMS`:

- **0**: plan is ready for fix-plan-reviewer / fix-coding
- **1+**: plan is BLOCKED (diagnosis disagreement, missing required data discovered during planning, etc.); downstream stages MUST NOT advance until resolved

## Downstream Contract

When `Status: BLOCKED` and `BLOCKING_ITEMS: 1+`, the orchestrator must:

1. NOT advance to fix-coding
2. Read the Blocking reason and Required action
3. Either:
   - Re-run adversarial-investigate with the data named in diagnosis.md's `required_data`
   - Escalate to the user

Silent advancement past a BLOCKED fix-plan is a contract violation.

## Rules

1. Always write `fix-plan.md`, even when BLOCKED. Silent failure is forbidden.
2. The Status, Diagnosis Reference, and Confidence + Blocking Items sections are mandatory. Other sections may be N/A when BLOCKED.
3. The Fix Approach section must justify the spectrum position; "we chose surgical" without why is insufficient.
4. Sequencing must answer the question — even if the answer is "order does not matter."
5. Rollback is mandatory; "N/A" is not allowed.
6. The last two lines are `CONFIDENCE:` and `BLOCKING_ITEMS:` — this is the contract the orchestrator parses.
7. No code changes. The plan describes what fix-coding will do; it does not do it.
