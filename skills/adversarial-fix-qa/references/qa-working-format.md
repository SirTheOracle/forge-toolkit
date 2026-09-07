# QA Working Format

This is the **authoritative** template for `qa-A.md` and `qa-B.md`, the Round 1 working artifacts produced by the QA Confirmer (A) and Regression Hunter (B) teammates of the adversarial-fix-qa skill. The SKILL.md does not include a preview of this format; this file is the single source of truth.

`qa-A.md` and `qa-B.md` are working artifacts (not deliverables). They feed Round 2 synthesis and Round 3 critique. The final deliverable is `fix-issues.md`, format defined separately.

## Why this artifact exists

A and B answer **different questions** with the same evidence base. The synthesizer must consolidate, not reconcile. To do that fairly, both reports must:

- State the strategy followed (so the synthesizer knows the agent's frame)
- Cite every action performed (no hand-waving — the synthesizer cross-verifies)
- Severity-rate each finding (so the synthesizer doesn't redo classification work)
- Surface evidence paths (so the synthesizer can sample the underlying logs/screenshots)

## Template

```markdown
# QA Report — {Agent A: Fix Confirmer | Agent B: Regression Hunter} — {fix-slug}

**Generated**: {ISO 8601 timestamp}
**Agent**: {qa-confirmer-a | regression-hunter-b}
**Environment**: {prod | dev}
**Source**: fix-coder-report.md commit {hash}

## Strategy Followed

{For A: "Fix Confirmation per agents/fix-qa-confirmer.md. {dev-mode: re-executed repro from repro.md} | {prod-mode: confirmed failing-path test exercises and passes}."}

{For B: "Regression Hunting per agents/fix-qa-regression.md. Identified dependents from fix-diffs.md, ran their tests, and probed for edge-case regressions in the modified code itself."}

State the frame in 2–4 sentences. The synthesizer relies on this to interpret findings.

## Actions Performed

A numbered list, each with a citation. Hand-waving is forbidden — every action references a specific file, test, or command.

For Agent A:

1. Re-executed repro.md steps 1–4. Outcome: {bug no longer triggers | bug still triggers | repro is non-deterministic, retried 3x}. Evidence: `evidence/post-fix/A-1-repro-rerun.log`.
2. Tested variation from repro.md "Variations Attempted" §2 (cookie-expired path). Outcome: {result}. Evidence: `evidence/post-fix/A-2-variation-cookie-expired.log`.
3. Verified the failing-path test (`tests/unit/test_token.py::test_invalid_token_rejected`) actually exercises the diagnosed cause site (`src/auth/token.py:142`). Inspected test imports and call sites. Confirmed reach. Evidence: `evidence/post-fix/A-3-test-reach-analysis.md`.
4. Tested edge case derived from diagnosis evidence chain: empty-string token. Outcome: {rejected | accepted}. Evidence: `evidence/post-fix/A-4-empty-token.log`.
5. Determinism check: re-ran the failing-path test 5 times. All passed. Evidence: `evidence/post-fix/A-5-determinism.log`.

For Agent B:

1. Read fix-diffs.md. Identified 3 modified files: src/auth/token.py, src/auth/middleware.py, tests/unit/test_token.py.
2. Identified dependents of src/auth/token.py via grep: src/auth/middleware.py (importer), src/api/routes/login.py (caller), src/api/routes/refresh.py (caller). Evidence: `evidence/post-fix/B-2-dependents-token.txt`.
3. Ran tests for each dependent: pytest tests/api/test_login.py, tests/api/test_refresh.py. All pass. Evidence: `evidence/post-fix/B-3-dependent-tests.log`.
4. Probed edge case in modified code: token with whitespace prefix. Test result: {pass | fail}. Evidence: `evidence/post-fix/B-4-whitespace-token.log`.
5. Ran full project test suite. Compared to fix-coder-report.md baseline. Delta: +1 new test (expected). No new failures. Evidence: `evidence/post-fix/B-5-full-suite.log`.

## Findings

Each finding has:

```markdown
### {AID|BID}-{n}: {brief title}

- **Severity**: CRITICAL | BLOCKING | ADVISORY
- **Category** (A): symptom-only-fix | non-deterministic | partial-coverage | test-doesnt-exercise-path | other
- **Category** (B): regression-in-importer | regression-in-caller | regression-in-test | edge-case-regression | coverage-gap | other
- **Evidence**: path under `evidence/`
- **Description**: 2–4 sentences on what was observed
- **Why severity**: justification — why CRITICAL not BLOCKING, or why BLOCKING not ADVISORY
```

Empty findings list is valid. Use exactly:

> No findings.

## Evidence

A flat directory listing of evidence paths produced during this report:

```
evidence/post-fix/A-1-repro-rerun.log
evidence/post-fix/A-2-variation-cookie-expired.log
evidence/post-fix/A-3-test-reach-analysis.md
...
```

The synthesizer reads selected evidence files during cross-verification (Round 2 Phase 2). Critical findings should have especially clear evidence.

## Dependents Checked (Agent B only)

This section is **mandatory for B** and **forbidden for A**. The synthesizer fails the quality gate if B's report omits this table.

| Dependent | Type | Result | Notes |
|---|---|---|---|
| src/auth/middleware.py | importer | pass | All importer tests pass |
| src/api/routes/login.py | caller | pass | Login flow tests pass |
| src/api/routes/refresh.py | caller | pass | Refresh flow tests pass |
| Full project test suite | full_suite | pass | +1 vs baseline (expected new test) |

Allowed `Type` values: `importer`, `caller`, `adjacent`, `full_suite`.
Allowed `Result` values: `pass`, `fail`, `not_applicable`.

If B identifies no dependents (rare — the fix touches an isolated leaf), the table reads:

> No dependents identified. Rationale: src/auth/token.py has no importers (verified via grep) and is not called externally (verified via grep). Full project test suite was still run for safety; result: pass.

## Result

Exactly one of:

For Agent A:

- **CONFIRMED**: fix resolves the diagnosed bug under all tested conditions
- **PARTIAL**: fix resolves the bug in some conditions but not all (specify which conditions failed)
- **FAILED**: fix does NOT resolve the diagnosed bug (drives FIX_FAILED verdict in synthesis)

For Agent B:

- **CLEAN**: no regressions identified; full test suite passes; all identified dependents pass
- **REGRESSIONS_FOUND**: at least one regression identified (severity-rated above; drives REGRESSION verdict in synthesis)

## Confidence + Blocking Items

The last two lines of the report MUST be exactly:

```
CONFIDENCE: HIGH | MEDIUM | LOW
BLOCKING_ITEMS: N
```

Guidance:

- **HIGH** — the actions performed comprehensively cover the agent's mandate; evidence is solid; result is clear-cut
- **MEDIUM** — the actions performed cover most of the mandate, but at least one judgment call was required (e.g., couldn't deterministically reproduce a flaky variation; chose to flag rather than ignore)
- **LOW** — significant constraints prevented thorough coverage (e.g., couldn't run live repro because environment is unavailable; relied on test-only verification when more was warranted)

`BLOCKING_ITEMS` formula:

```
BLOCKING_ITEMS = count(CRITICAL findings) + count(BLOCKING findings)
```

ADVISORY findings do NOT count toward `BLOCKING_ITEMS`.

## Rules

1. Always write the report, even when the result is CONFIRMED/CLEAN with no findings (Hard Rule 2 in SKILL.md).
2. Every action in "Actions Performed" must cite a file, test, or command — no hand-waving.
3. For B: the Dependents Checked table is mandatory. The synthesizer fails the gate without it.
4. For A: the failing-path test reach analysis is mandatory in dev mode (Action 3 above) — A may not trust "the test passes" without confirming the test actually exercises the failing logic.
5. Severity is conservative: when uncertain between two levels, choose the higher.
6. The last two lines are `CONFIDENCE:` and `BLOCKING_ITEMS:` in that order.
7. Result must be one of the allowed values for the agent (no synonyms).
