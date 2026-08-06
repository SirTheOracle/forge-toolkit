# Fix-Diffs Format

This is the **authoritative** template for `fix-diffs.md`, the Phase 1 translation artifact produced by the fix-coder skill. The SKILL.md may include narrative descriptions of this format; if SKILL.md and this file diverge, this file wins.

`fix-diffs.md` is the inspectable bridge between fix-plan.md (file-level intent) and the actual edits. It is written **before** any source files are touched (Hard Constraint 2). Reviewers and the orchestrator can read it to confirm the translation is faithful to the plan and does not silently expand scope.

## Why this artifact exists

The plan describes WHAT to change in prose; the diffs describe HOW the change looks in code. Separating these forces:

- Every assumption in the plan to be validated against the actual file before any edit
- Every diff to be linkable to a specific plan element (cause coverage, defense, or test)
- Scope expansion to be visible (any file touched here that isn't in the plan is a STOP, not a silent inclusion)

## Template

```markdown
# Fix Diffs — {fix-slug}

**Generated**: {ISO 8601 timestamp}
**Plan**: {output_dir}/fix-plan.md
**Diagnosis**: {output_dir}/diagnosis.md
**Plan Status**: ACTIVE
**Review Verdict**: APPROVE (or "no review present" if fix-review.md absent)

## Summary

{1–3 sentences: what is being changed, in which files, addressing which cited cause from diagnosis.md.}

## Files Touched

| Path | Action | In plan? | Plan element addressed |
|---|---|---|---|
| src/foo/bar.py | modify | Yes (Changes:bar.py) | Cause coverage: token validation gap |
| src/foo/baz.py | modify | Yes (Changes:baz.py) | Defense: defense-in-depth on caller |
| tests/unit/test_foo.py | add | Yes (Test Plan §2) | Test exercising failing path |

Every row's "In plan?" column MUST be Yes. If any row is No, fix-coder STOPs at Phase 1 with BLOCKING_ITEM (scope expansion).

## Per-File Diffs

### {path/to/file.py} — modify

**Plan element**: {quote or cite the specific section of fix-plan.md this implements}
**Pre-flight check**: file exists; structure matches plan assumption
- Function `validate_token` exists at line {N}: ✓
- Caller `auth_middleware.handle` at {file:line}: ✓
- No unexpected refactor since plan was written: ✓

**Region 1**: token validation tightening (lines {start}–{end})

```diff
@@ -{old_start},{old_count} +{new_start},{new_count} @@
 def validate_token(token: str) -> bool:
-    if token:
-        return True
+    if not token:
+        return False
+    if len(token) < MIN_TOKEN_LEN:
+        return False
+    return _verify_signature(token)
```

**Why**: addresses the diagnosis's evidence chain at {diagnosis.md:section}, where untyped truthy check accepted any non-empty string.

**Region 2** (if applicable): {repeat structure}

### {path/to/new.py} — add

**Plan element**: {citation}
**Pre-flight check**: file does not exist at this path: ✓

```python
{full file contents}
```

### {path/to/old.py} — remove

**Plan element**: {citation}
**Pre-flight check**: file exists at this path: ✓
**Confirmation**: this file is being removed entirely, not just emptied. Dependents (per regression risk analysis):
- {file}: imports updated in {other-diff}
- {file}: imports updated in {other-diff}

## Tests Added or Modified

| Test | Action | Exercises |
|---|---|---|
| tests/unit/test_foo.py::test_invalid_token_rejected | new | The diagnosed cause path: short tokens are now rejected |
| tests/unit/test_foo.py::test_signature_verified | modify | Defense path: signature must be valid even for long tokens |

For each NEW test: confirm it FAILS against the pre-fix code and PASSES against the post-fix code (the killer-question check from fix-plan-reviewer 3F). If a test passes both before and after, it does not exercise the failing path — flag as a Phase 1 STOP.

## Coverage Mapping

Every change in the plan must map to at least one diff above. Every diff above must map to at least one plan element. Bidirectional check.

| Plan Element | Addressed by |
|---|---|
| Cause coverage: token validation gap | bar.py Region 1, test_invalid_token_rejected |
| Defense: defense-in-depth on caller | baz.py Region 1 |
| Test Plan §2: new failing-path test | tests/unit/test_foo.py |
| Test Plan §3: regression test for signature path | test_signature_verified (modified) |

If any plan element has no addressed-by entry, STOP — the translation is incomplete.
If any diff has no plan-element entry, STOP — the diff is scope creep.

## Sequencing / Commit Groups

If fix-plan.md specifies multiple commit groups, mirror that grouping here:

### Group 1: {group title from plan}
- Diffs: bar.py Region 1, test_invalid_token_rejected
- Group-specific tests (per plan): pytest tests/unit/test_foo.py -v -k invalid_token

### Group 2: {group title from plan}
- Diffs: baz.py Region 1, test_signature_verified
- Group-specific tests (per plan): pytest tests/unit/test_foo.py -v -k signature

If the plan does not specify groups, this section reads:

> Single commit group. All diffs apply atomically.

## Open Questions (must be empty before Phase 2)

If the translator hit any ambiguity that required a guess, list it here AND STOP at Phase 1. fix-coder must not proceed past Phase 1 with open questions; the plan needs revision.

If everything was unambiguous, this section reads:

> No open questions. Plan-to-diff translation was unambiguous.

## Confidence + Blocking Items

The last two lines of `fix-diffs.md` MUST be exactly:

```
CONFIDENCE: HIGH | MEDIUM | LOW
BLOCKING_ITEMS: N
```

Guidance:

- **CONFIDENCE: HIGH** — every plan element mapped cleanly; every pre-flight check passed; no open questions; coverage mapping is bidirectional and complete
- **CONFIDENCE: MEDIUM** — translation succeeded but required at least one judgment call (e.g., choosing between two plausible diff regions for the same plan element); document the judgment under the relevant region's "Why"
- **CONFIDENCE: LOW** — translation is technically complete but the translator suspects the plan is fragile (e.g., relies on file structure that looks brittle); flag as Open Question and STOP at Phase 1

`BLOCKING_ITEMS` formula:

```
BLOCKING_ITEMS = count(Open Questions) + count(scope-expansion rows in Files Touched) + count(missing coverage mappings)
```

If `BLOCKING_ITEMS > 0`, fix-coder STOPs at Phase 1 and writes fix-coder-report.md with status STOPPED. No source files are modified.

## Rules

1. Always write `fix-diffs.md` before any source file is touched (Hard Constraint 2 in SKILL.md).
2. Every Files Touched row's "In plan?" column must be Yes — scope expansion fails Phase 1.
3. Every plan element must appear in the Coverage Mapping. Missing coverage fails Phase 1.
4. Every diff must map to a plan element. Orphan diffs fail Phase 1.
5. New tests must demonstrate the killer-question property: fail pre-fix, pass post-fix.
6. Open Questions section must be empty before Phase 2 begins. Any open question is a STOP.
7. The last two lines are `CONFIDENCE:` and `BLOCKING_ITEMS:` in that order — the orchestrator parses these.
