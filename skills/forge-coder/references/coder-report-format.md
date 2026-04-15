# Coder Report Format

The coder report is the primary artifact produced by the forge-coder skill.
It MUST be written to `.dev/proposals/{slug}/coder-report.md` on every run,
including partial failures.

## Template

```markdown
# Coder Report: {slug}

**Generated**: {ISO 8601 timestamp}
**Implementation**: `.dev/proposals/{slug}/implementation.md`
**Branch**: {current git branch}
**Status**: COMPLETE | PARTIAL | FAILED | VALIDATION_FAILED

## Pre-Flight Validation

| Step | File | Type | Result | Line |
|------|------|------|--------|------|
| 1 | path/to/file.py | modify | FOUND | 45 |
| 2 | path/to/new.py | new | SKIP | — |
| 3 | path/to/other.py | modify | NOT FOUND | — |

**Result**: {N}/{M} steps validated | ALL VALIDATED

## Commit Groups

### Group 1: {commit message}
**Steps**: 1, 2, 3
**Status**: APPLIED | FAILED_AT_STEP_N | TESTS_FAILED

| Step | File | Action | Result |
|------|------|--------|--------|
| 1 | path/to/file.py | edit | Applied |
| 2 | path/to/new.py | create | Created |
| 3 | path/to/test.py | edit | Applied |

**Tests**:
- Command: `pytest tests/unit/test_foo.py -v`
- Result: PASS | FAIL
- Output: {summary or full output on failure}

**Commit**: {short SHA} | NOT COMMITTED (tests failed)

### Group 2: {commit message}
...

## Full Validation

| Check | Command | Result | Details |
|-------|---------|--------|---------|
| Backend tests | pytest | PASS | 142 passed, 3 skipped |
| Linter | ruff check src/ | PASS | 0 issues |
| Type checker | — | SKIPPED | Not configured |
| Frontend e2e | npx playwright test | PASS | 12 passed |
| Frontend lint | — | SKIPPED | Not configured |

## Definition of Done

- [x] All implementation steps applied
- [x] Backend tests pass
- [ ] Frontend e2e tests pass — {failure details}
- [x] Lint clean

## Summary

**Total steps**: {N}
**Steps applied**: {X}/{N}
**Commits created**: {C}
**Files changed**: {F}
**Files created**: {G}
**Migrations created**: {M} (in {migration_dir})

## Failures

{If any failures occurred, include full details here:
- Which step or test failed
- The exact error message
- The file and line number if applicable
- Suggested next action (re-run adversarial-implementation, manual fix, etc.)
}
```

## Status Values

| Status | Meaning |
|--------|---------|
| `COMPLETE` | All steps applied, all tests pass, full validation pass |
| `PARTIAL` | Some commit groups applied, stopped at a failure |
| `FAILED` | Pre-flight validation failed, no changes made |
| `VALIDATION_FAILED` | All steps applied but full validation found issues |

## Rules

1. Always include the pre-flight validation section, even if all steps pass
2. Always include every commit group, marking incomplete ones as such
3. On failure, include the full error output — don't truncate
4. If tests were not run (e.g., no test command configured), mark as SKIPPED
5. The report is the artifact — it must tell the full story of what happened
