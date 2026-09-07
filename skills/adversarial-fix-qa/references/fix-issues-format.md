# Fix-Issues Format

This is the **authoritative** template for `fix-issues.md`, the final deliverable produced by the adversarial-fix-qa skill (Round 4 reconciliation). The SKILL.md includes a preview for orientation; if SKILL.md and this file diverge, this file wins.

The orchestrator parses fix-issues.md (and the parallel `fix-manifest.yaml`) to decide what to do next:

- **Verdict PASS** + `BLOCKING_ITEMS: 0` → advance to fix-verify (or ship if fix-verify is not configured)
- **Verdict FIX_FAILED** → escalate to user; possibly re-run adversarial-investigate (the diagnosis or plan was wrong)
- **Verdict REGRESSION** → re-dispatch adversarial-fix-plan in revise mode (the fix worked but introduced regressions)
- **Verdict BOTH** → escalate to user (worst-case outcome)
- **Verdict BLOCKED** → early-stop or stub path; route per `stop_reason` field in fix-manifest.yaml

## Template

```markdown
# Fix QA — {fix-slug}

**Generated**: {ISO 8601 timestamp}
**Reviewing fix**: {output_dir}/fix-coder-report.md commit {hash}
**Diagnosis**: {output_dir}/diagnosis.md
**Plan**: {output_dir}/fix-plan.md
**QA worker**: Claude Code Agent Teams (lead + 3 teammates)

## Verdict

{PASS | FIX_FAILED | REGRESSION | BOTH | BLOCKED}

{For prod PASS: append "Ready for verification post-deploy. Live prod confirmation is fix-verify's responsibility."}

{For BLOCKED: include stop_reason inline.}

The Verdict MUST match the severity counts and source agents per the Severity-to-verdict mapping in SKILL.md Step 7.5. The Output Quality Gate enforces this.

## Environment

{prod | dev}

For prod: A's confirmation is bounded by what can be tested locally; full prod confirmation is fix-verify's job after deploy.
For dev: A re-executes repro.md when present.

## Fix Confirmation Result (Agent A)

Exactly one of:

- **CONFIRMED**: fix resolves the diagnosed bug under all tested conditions. Evidence:
  - Repro re-run (dev): bug no longer triggers. `evidence/post-fix/A-1-repro-rerun.log`
  - Failing-path test reach analysis: confirmed test exercises the diagnosed cause site at {file:line}. `evidence/post-fix/A-3-test-reach-analysis.md`
  - {Variations and edge cases tested, with results}
  - Determinism: N runs, all consistent. `evidence/post-fix/A-5-determinism.log`

- **PARTIAL**: fix resolves the bug in some but not all conditions. Specify:
  - Conditions that pass: {list}
  - Conditions that fail: {list}
  - Why this is PARTIAL not FAILED: {rationale, e.g., "the failing variation is documented in repro.md as 'Variations Attempted §3' which the plan explicitly deprioritized"}

- **FAILED**: fix does NOT resolve the diagnosed bug. This drives the FIX_FAILED verdict.
  - Evidence of failure: `evidence/post-fix/...`
  - Diagnosed cause site: {file:line from diagnosis.md}
  - Observed behavior post-fix: {what still happens}

## Regression Findings (Agent B)

Exactly one of:

- **CLEAN**: no regressions identified. All dependents tested pass. Full test suite delta vs fix-coder-report.md baseline: {delta with sign}.

- **REGRESSIONS FOUND**: list of dependents that broke or edge cases that regressed:

  ### REG-{n}: {brief title}
  - **Where**: file/test/feature affected
  - **What**: how it manifests (error message, failed assertion, behavior change)
  - **Why It Matters**: severity rationale, blast radius
  - **Severity**: CRITICAL | BLOCKING | ADVISORY
  - **Evidence**: `evidence/post-fix/...`

## Critical Issues

Issues with severity CRITICAL. The fix doesn't work (any A finding) or a regression breaks a core feature (B).

For each issue:

```markdown
### CRIT-{n}: {brief title}

- **Source agent**: A | B
- **Category** (A): symptom-only-fix | non-deterministic | partial-coverage | test-doesnt-exercise-path | other
- **Category** (B): regression-in-importer | regression-in-caller | regression-in-test | edge-case-regression | coverage-gap | other
- **Description**: 2–4 sentences
- **Evidence**: `evidence/post-fix/...`
- **Verdict implication**: drives FIX_FAILED (A) | REGRESSION (B) | BOTH (A and B)
```

Empty section uses exactly:

> No CRITICAL issues found.

## Blocking Issues

Issues with severity BLOCKING. Same per-issue structure with `BLOCK-{n}` prefix. Empty section uses:

> No BLOCKING issues found.

## Advisory Issues

Issues with severity ADVISORY. Same per-issue structure with `ADV-{n}` prefix. Empty section uses:

> No ADVISORY issues found.

## Re-emerged Advisories

For each ADVISORY in fix-review.md (if present), state whether QA observed it as still active:

| Advisory ID (from fix-review.md) | Summary | QA Disposition | Notes |
|---|---|---|---|
| ADV-1 | Consider extracting validate_token to separate module | still observed (now ADVISORY) | Cross-references ADV-3 below |
| ADV-2 | Add docstring to validate_token | no longer observed | fix-coder addressed this |
| ADV-3 | Logging on rejected tokens | out of QA scope | Not testable from QA mandate; flag for follow-up |

If fix-review.md is absent:

> No fix-review.md present. No prior advisories to re-check.

If fix-review.md is present but had no ADVISORY items:

> fix-review.md present; Advisory Issues section was empty. No prior advisories to re-check.

Empty body in any other case violates Hard Rule 8 (silent drop of upstream feedback) and fails the Output Quality Gate.

## Dependents Checked (Agent B audit)

Mandatory transparency table — populated from qa-B.md's Dependents Checked section.

| Dependent | Type | Result | Notes |
|---|---|---|---|
| src/auth/middleware.py | importer | pass | All importer tests pass |
| src/api/routes/login.py | caller | pass | Login flow tests pass |
| Full project test suite | full_suite | pass | +1 vs baseline (expected new test) |

Allowed `Type`: `importer`, `caller`, `adjacent`, `full_suite`.
Allowed `Result`: `pass`, `fail`, `not_applicable`.

If B identified no dependents (rare):

> B identified no dependents. Rationale: {from qa-B.md}. Full project test suite was still run; result: pass.

## Confidence + Blocking Items

The last two lines of `fix-issues.md` MUST be exactly:

```
CONFIDENCE: HIGH | MEDIUM | LOW
BLOCKING_ITEMS: N
```

Guidance:

- **HIGH** — both A and B reported HIGH; cross-verification in Round 2 confirmed the most critical findings; verdict is unambiguous given the mapping rules
- **MEDIUM** — at least one of A, B, or the synthesizer reported MEDIUM; one or more severity decisions required judgment between two levels; verdict is correct but a reasonable observer might choose differently on one finding
- **LOW** — significant constraints prevented thorough QA (e.g., prod-only environment); the verdict is a best-effort given those constraints; fix-verify is responsible for closing the residual gap

`BLOCKING_ITEMS` formula:

```
BLOCKING_ITEMS = count(CRITICAL issues) + count(BLOCKING issues)
```

ADVISORY issues do NOT count. Re-emerged advisories that QA reclassified to higher severities DO count (under their new classification, in Critical or Blocking sections).

## Severity-to-Verdict Mapping (for self-check)

This is the same table from SKILL.md Step 7.5; reproduced here for the reconciler's reference during Round 4. The Output Quality Gate enforces this mapping.

| Severity | Source | Verdict Impact |
|---|---|---|
| CRITICAL | A only | FIX_FAILED |
| CRITICAL | B only | REGRESSION |
| CRITICAL | A and B | BOTH |
| BLOCKING | A only | Stays PASS-with-qualification UNLESS A's finding indicates the fix does not address the full diagnosed cause — then escalates to FIX_FAILED |
| BLOCKING | B only | REGRESSION |
| BLOCKING | A and B | escalates to BOTH if A's BLOCKING is full-cause-coverage; otherwise REGRESSION |
| ADVISORY | any | no verdict change |

## Rules

1. Always write `fix-issues.md`, even on PASS verdict and even on early-stop (Hard Rule 5 in SKILL.md).
2. The Verdict must match the severity counts and source agents per the mapping above. The Output Quality Gate enforces this.
3. The Re-emerged Advisories section must contain a row for every ADVISORY in fix-review.md (Hard Rule 8). Silent drops fail the gate.
4. The Dependents Checked table is mandatory and populated from qa-B.md (or the no-dependents rationale).
5. The last two lines are `CONFIDENCE:` and `BLOCKING_ITEMS:` in that order — the orchestrator parses these.
6. The verdict in fix-issues.md must equal the `verdict` field in fix-manifest.yaml. The Output Quality Gate enforces this.
7. fix-issues.md is touched last so its mtime reflects QA completion.
