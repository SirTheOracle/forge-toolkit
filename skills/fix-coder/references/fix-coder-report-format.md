# Fix-Coder-Report Format

This is the **authoritative** template for `fix-coder-report.md`, the final deliverable produced by the fix-coder skill. The SKILL.md may include a preview of this format for orientation; if SKILL.md and this file diverge, this file wins.

The orchestrator parses fix-coder-report.md to decide what to do next:

- **Status SUCCESS** + `BLOCKING_ITEMS: 0` → advance to fix-qa
- **Status PARTIAL** → advance to fix-qa (acceptable partial validation; fix-verify will close the loop post-deploy)
- **Status FAILED** with **STILL REPRODUCES** → escalate; possibly re-run adversarial-investigate (the fix did not fix)
- **Status FAILED** with other → re-dispatch adversarial-fix-plan in revise mode
- **Status STOPPED** → re-dispatch adversarial-fix-plan in revise mode (the plan does not match the code)

The structure below is mandatory. The Phase 9 self-check in SKILL.md enforces it.

## Template

```markdown
# Fix Coder Report — {fix-slug}

**Generated**: {ISO 8601 timestamp}
**Plan**: {output_dir}/fix-plan.md
**Diagnosis**: {output_dir}/diagnosis.md
**Diffs**: {output_dir}/fix-diffs.md
**Worker**: {Claude lead | Codex worker}
**Branch**: {current git branch}

## Status

{SUCCESS | PARTIAL | FAILED | STOPPED}

- **SUCCESS**: all phases completed cleanly, fix is committed, validation confirmed it works
- **PARTIAL**: fix is committed, but validation was limited (typically prod path with no live verification)
- **FAILED**: a phase failed and the fix could not be safely completed
- **STOPPED**: a Hard Constraint was triggered (scope expansion, plan-vs-code mismatch, malformed upstream artifact, etc.); no commit made

The Status MUST match phase outcomes. The Phase 9 self-check in SKILL.md enforces this:

| Status | Required signals |
|---|---|
| SUCCESS | Fix Validation = VALIDATED via repro/test, Commit has real hash, BLOCKING_ITEMS = 0 |
| PARTIAL | Commit has real hash, Fix Validation ≠ STILL REPRODUCES |
| FAILED | Commit = "NOT COMMITTED", BLOCKING_ITEMS > 0 |
| STOPPED | Commit = "NOT COMMITTED", BLOCKING_ITEMS > 0 |

## Environment

{prod | dev}

For prod: validation is bounded by what can be observed locally; full validation completes in fix-verify after deploy.
For dev: validation includes re-running the repro from repro.md and confirming the bug no longer triggers.

## Diagnosis Reference

{Direct citation of root cause from diagnosis.md, including the convergence_status field
(CONVERGED | PARTIALLY CONVERGED | INSUFFICIENT EVIDENCE).
If diagnosis.md has INSUFFICIENT EVIDENCE, fix-coder should not have run — flag as a STOPPED gate violation.}

Example:
> diagnosis.md states convergence_status: CONVERGED. Root cause: token validation
> in src/auth/token.py:142 accepts any non-empty string due to truthy check
> instead of explicit length+signature verification.

## Plan Applied

{Reference to fix-plan.md with brief summary of what was applied. Cite the plan's chosen
posture (Surgical / Robust / synthesized).}

Example:
> fix-plan.md (Status: ACTIVE, posture: Surgical-with-one-defense). Tightens
> token validation at the diagnosed cause site and adds defense-in-depth in
> the caller. Test plan adds one new failing-path test and one regression test.

## Files Modified

List from fix-diffs.md, confirming what was actually changed in code:

| Path | Action | Lines Changed | Plan Element |
|---|---|---|---|
| src/auth/token.py | modify | 142–158 | Cause coverage |
| src/auth/middleware.py | modify | 87–94 | Defense |
| tests/unit/test_token.py | add | new file | Test Plan §2 |

If the actual modifications differ from fix-diffs.md (they should not — this would mean a Hard Constraint violation), document the discrepancy here as an Issue.

## Test Plan Results

For each test specified in fix-plan.md's Test Plan section:

| Test | Plan citation | Result | Notes |
|---|---|---|---|
| test_invalid_token_rejected | Test Plan §2 | PASS (new) | Confirmed FAIL pre-fix, PASS post-fix |
| test_signature_verified | Test Plan §3 | PASS (modified) | Regression coverage for signature path |
| {regression test from plan} | Test Plan §4 | PASS | No regression |

Plus full-suite baseline-vs-post comparison:

| Suite | Baseline | Post-fix | Delta |
|---|---|---|---|
| Backend pytest | 142 passed, 3 skipped | 143 passed, 3 skipped | +1 (new test) |
| Frontend e2e (if run) | 12 passed | 12 passed | 0 |
| Lint | 0 issues | 0 issues | 0 |
| Typecheck | clean | clean | 0 |

Any new failures must be in files explicitly modified by this fix. New failures elsewhere are CRITICAL stop conditions.

## Fix Validation

Exactly one of:

- **VALIDATED via repro (dev)**: bug no longer triggers when repro.md steps are executed. Evidence at `{output_dir}/evidence/post-fix/`. Steps run: {brief}. Outcome: {brief}.
- **VALIDATED via test (any env)**: {test name} exercises the failing path identified in diagnosis.md (cite file:line) and passes. Confirmed pre-fix FAIL: {yes | no — explain}.
- **NOT VALIDATED**: validation could not be performed. Reason: {repro non-deterministic | prod-only path | environmental dependency unavailable}. Compensation: {what was checked instead}.
- **STILL REPRODUCES**: bug still triggers after fix. The fix did NOT fix. This is the highest-severity outcome — the orchestrator should escalate or re-run adversarial-investigate.

STILL REPRODUCES is mutually exclusive with SUCCESS. The Phase 9 self-check enforces this.

## Rollback Plan Re-verification

For prod (`--env prod`):

| Rollback Step (from fix-plan.md) | Executable Against Actual Changes? | Notes |
|---|---|---|
| Revert commit {hash} | Yes — single commit, no follow-on | — |
| Re-run migration {name} down | N/A — no schema change in this fix | — |
| Restore config {path} | N/A — no config change | — |

If any step is "No", STOP at Phase 6 and report a STOPPED status. The plan needs to be revised so its rollback section matches what was built.

For dev (`--env dev`):

> Rollback equivalent to `git revert {hash}` or `git reset` — no formal re-verification required.

## Commit

For SUCCESS / PARTIAL:

```
Hash: abc1234
Message: fix(auth): tighten token validation at diagnosed cause site
Files: src/auth/token.py, src/auth/middleware.py, tests/unit/test_token.py
```

For FAILED / STOPPED:

```
NOT COMMITTED
Reason: {Phase 4 test failure | Phase 5 STILL REPRODUCES | Phase 6 rollback mismatch | Phase 1 scope expansion | Phase 0 upstream artifact malformed | ...}
Working directory state: {clean | dirty with applied diffs preserved | partial revert per Phase 3}
```

## Advisories Acknowledged

For each ADVISORY in fix-review.md (if present):

| Advisory ID | Summary | Disposition | Rationale |
|---|---|---|---|
| ADV-1 | Consider extracting validate_token to a separate module | not addressed | Out of fix scope; flagged for follow-up cleanup task |
| ADV-2 | Add docstring to validate_token | addressed | Added in src/auth/token.py:142 |
| ADV-3 | Logging on rejected tokens | not addressed | Plan didn't include observability changes; flagged for follow-up |

If fix-review.md is absent, this section reads:

> No fix-review.md present. No advisories to acknowledge.

If fix-review.md is present but has no ADVISORY items, this section reads:

> fix-review.md present; Advisory Issues section was empty. No advisories to acknowledge.

Empty body in any other case is a Hard Constraint 11 violation (silent drop) and fails the Phase 9 self-check.

## Issues Encountered

Anything notable that didn't trigger STOP but is worth flagging:

- Lint warnings introduced (cite file:line and the rule)
- Tests that almost failed (flakiness indicators)
- Timing observations (e.g., one test took 30s where suite avg is 2s)
- Assumptions made during translation (anything that became an Open Question candidate but resolved)
- Pre-existing tech debt observed but not touched (per Hard Constraint 5)

Empty section is acceptable.

## Confidence + Blocking Items

The last two lines of `fix-coder-report.md` MUST be exactly:

```
CONFIDENCE: HIGH | MEDIUM | LOW
BLOCKING_ITEMS: N
```

Guidance:

- **CONFIDENCE: HIGH** — all phases passed cleanly, validation was conclusive (VALIDATED via repro for dev or VALIDATED via test for prod with confirmed pre-fix failure), no judgment calls during translation, no surprises during application
- **CONFIDENCE: MEDIUM** — fix completed but at least one of: repro was somewhat non-deterministic, baseline diff required interpretation, validation was VALIDATED via test (not repro) on dev path, or one Issues Encountered entry materially affects trust in the result
- **CONFIDENCE: LOW** — fix is technically committed but validation is NOT VALIDATED, OR multiple judgment calls were required during translation, OR Issues Encountered contains material concerns; orchestrator should treat this as borderline PARTIAL

For FAILED / STOPPED, CONFIDENCE refers to confidence in the failure attribution, not in the (non-existent) fix:

- **CONFIDENCE: HIGH** for FAILED/STOPPED — the failure cause is clearly identified and the orchestrator routing recommendation is unambiguous
- **CONFIDENCE: MEDIUM** for FAILED/STOPPED — the failure cause is identified but the right next step (revise vs re-investigate) is ambiguous
- **CONFIDENCE: LOW** for FAILED/STOPPED — multiple possible causes; orchestrator should escalate

`BLOCKING_ITEMS` formula:

| Status | BLOCKING_ITEMS |
|---|---|
| SUCCESS | 0 |
| PARTIAL | 0 (acceptable partial) or N where N = items worth flagging for fix-verify |
| FAILED | ≥ 1 (always; the failure itself is at least one blocking item) |
| STOPPED | ≥ 1 (always; the Hard Constraint trigger is at least one blocking item) |

## Status Decision Matrix

For self-check during Phase 9:

| Phase outcome | Status |
|---|---|
| All 8 phases pass, validation = VALIDATED | SUCCESS |
| All phases pass, validation = NOT VALIDATED (prod path with no repro) | PARTIAL |
| Phase 0 — config / artifact / Verdict gate trips | STOPPED |
| Phase 1 — file mismatch, scope expansion, ambiguity | STOPPED |
| Phase 2 — dirty working dir, broken baseline | STOPPED |
| Phase 3 — diff fails to apply, lint regression outside scope | FAILED (diff fail) or STOPPED (scope) |
| Phase 4 — test plan failure | FAILED |
| Phase 5 — STILL REPRODUCES | FAILED (highest severity — diagnosis or plan was wrong) |
| Phase 6 — prod rollback mismatch | STOPPED |
| Phase 7 — commit fails | FAILED |

## Rules

1. Always write `fix-coder-report.md`, even on failure or STOP. Silent failure is forbidden (Hard Constraint 6 in SKILL.md).
2. The Status must match phase outcomes per the Status Decision Matrix above.
3. The Advisories Acknowledged section must contain a row for every ADVISORY in fix-review.md. Silent drops fail the Phase 9 self-check (Hard Constraint 11).
4. The last two lines are `CONFIDENCE:` and `BLOCKING_ITEMS:` in that order — the orchestrator parses these.
5. fix-coder-report.md is touched last so its mtime reflects coder completion (lets downstream stages' mtime checks work correctly).
6. SUCCESS requires Fix Validation = VALIDATED. STILL REPRODUCES is mutually exclusive with SUCCESS.
7. FAILED and STOPPED both have `Commit: NOT COMMITTED`. Working directory state is documented per the relevant phase's failure mode.
