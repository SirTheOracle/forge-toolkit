---
name: fix-coder
description: >
  Mechanical single-executor that applies a vetted fix plan to the codebase.
  Reads fix-plan.md (the Changes, Defenses, and Test Plan sections), translates
  file-level changes into concrete diffs, applies them, runs the test plan,
  validates that the bug is no longer reproducible (dev) or that the test
  exercising the failing path passes (prod), and produces a single fix commit.
  Stays strictly in scope: does NOT modify the plan, does NOT re-investigate,
  does NOT touch files outside the plan. Always writes fix-coder-report.md,
  even on failure. Trigger as the implementation stage of a fix-pipeline,
  after fix-plan-reviewer has approved fix-plan.md.
---

# Fix Coder Skill

## When to Use

- After `fix-plan-reviewer` has produced `fix-review.md` with **Verdict: APPROVE** (no CRITICAL, no BLOCKING)
- The plan is being applied for the first time, or for a re-application after the orchestrator has explicitly cleared the previous attempt

**Expected runtime**: ~10–30 min, depending on diff count, test suite size, and dev-vs-prod validation rigor. Phase 4 (full test suite) and Phase 5 (dev repro) typically dominate.

**Worker routing.** In the default `contain` rollout, use a reviewed Claude host
lane because `fix-code` carries commit capability. A Codex worker is eligible only
after `forge codex-lane --stage fix-code --worker codex-a` returns `lane=codex`
and the broker's private-runtime live gate is proven. Never bypass `LANE_REQUIRED`.

## When NOT to Use

- **No fix-plan.md exists.** Hard refusal — see Hard Constraints.
- **fix-plan.md has BLOCKED status** (or status is missing/malformed — see defense in depth in Phase 0). Cannot apply a blocked plan.
- **fix-review.md exists and Verdict is REVISE or REJECT** (or missing/malformed). The plan needs revision or escalation first.
- **Build-pipeline coding tasks.** Use `forge-coder` for those.

## Hard Constraints

1. **Apply the plan, do not modify it.** If the plan turns out to be wrong during application, STOP and report. Do not silently "improve" the plan or re-plan on the fly. Re-planning is the orchestrator's job (revise mode of adversarial-fix-plan).
2. **Write fix-diffs.md before touching any code.** The translation from plan to concrete diffs is a separate, inspectable phase. No source files are modified before fix-diffs.md is written.
3. **Validate the fix actually fixes.** Before reporting SUCCESS, exercise the repro (dev) and confirm the bug no longer triggers, OR confirm a test that specifically exercises the failing path passes (prod or repro-unavailable). If neither can be done, status is at most PARTIAL.
4. **No scope expansion.** Files modified must be listed in fix-plan.md's Changes section. If application requires touching a file not in the plan, STOP and report a BLOCKING_ITEM. Do not silently expand scope.
5. **No unrelated changes.** No drive-by refactors, no formatting cleanups, no commented-out code removals unless the plan explicitly calls for them.
6. **Always write fix-coder-report.md.** Including on failure, including on STOP-and-report. Silent exit is forbidden.
7. **Single fix commit by default.** Unless fix-plan.md explicitly specifies multiple commit groups. The default is one logical commit.
8. **Rollback plan re-verification (prod only).** Before committing a prod fix, confirm the rollback plan in fix-plan.md is actually executable against the changes made. If it isn't, STOP and report.
9. **No re-investigation, no re-planning.** Same lane discipline as fix-plan-reviewer.
10. **Single executor.** This skill does not spawn sub-agents and does not run adversarial rounds.
11. **Acknowledge upstream feedback.** Read fix-review.md (when present) for ADVISORY items and surface each one in fix-coder-report.md's "Advisories Acknowledged" section with one-line "addressed | not addressed (rationale)". Silent drops are forbidden — even when the coder takes no action, the report must show the advisory was seen.

## Required Reads

Before any code action:

1. `.claude/forge-project.yml` (fallback: `~/.claude/forge-project.yml`) — project config
2. `{output_dir}/fix-plan.md` — the plan being applied (must have Status: ACTIVE)
3. `{output_dir}/diagnosis.md` — the diagnosis the plan addresses
4. `{output_dir}/fix-review.md` — if present, confirms the plan was reviewed
5. `{output_dir}/repro.md` — if present, used for dev-mode validation
6. `{output_dir}/problem-statement.md` — context
7. `~/.claude/skills/fix-coder/references/fix-diffs-format.md` — fix-diffs.md template
8. `~/.claude/skills/fix-coder/references/fix-coder-report-format.md` — final report template

If `--original-slug` is provided, also read for context (read-only):

9. `.dev/proposals/{original-slug}/final-plan.md`
10. `.dev/proposals/{original-slug}/implementation.md`
11. `.dev/proposals/{original-slug}/coder-report.md`

## Inputs

Required:

- `--output-dir` — path to the fix directory. fix-plan.md lives here; fix-diffs.md and fix-coder-report.md will be written here.
- `--env prod | dev` — affects validation strategy and rollback rigor.

Optional:

- `--original-slug` — slug of the original build pipeline. Enables read-only context access.

## Phase 0: Config + Inputs

1. Read `.claude/forge-project.yml`
2. Read fix-plan.md from `--output-dir`
3. **Empty-args short-circuit.** If `--output-dir` is missing, `--env` is missing, or fix-plan.md does not exist, stop:
   ```
   FIX-CODER ERROR: Missing required input. Provide --output-dir,
   --env (one of `prod` | `dev`), and ensure fix-plan.md exists.
   ```
4. **Plan status check (defense in depth).** Parse the `Status:` field from fix-plan.md.
   - If `Status: BLOCKED`, stop.
   - If the field is missing, malformed, or has any value other than `ACTIVE`, treat it as BLOCKED and stop. Conservative interpretation prevents acting on an unparseable plan.
   ```
   FIX-CODER ERROR: fix-plan.md has BLOCKED (or unparseable) Status.
   Cannot apply a non-ACTIVE plan.
   ```
5. **Review status check (defense in depth).** If fix-review.md exists, parse its `Verdict:` field.
   - If `Verdict: APPROVE`, proceed.
   - If `Verdict: REVISE` or `Verdict: REJECT`, stop. The plan should have gone through revise mode (REVISE) or been escalated (REJECT) before coding.
   - If the field is missing, malformed, or any value other than the three above, treat as REJECT and stop. The orchestrator's routing should have prevented this; defense in depth.
   ```
   FIX-CODER ERROR: fix-review.md has Verdict {REJECT|REVISE|unparseable}.
   Plan must be revised or escalated before coding can proceed.
   ```
6. Read diagnosis.md, repro.md (if present), problem-statement.md
7. If `--original-slug` is provided, confirm `.dev/proposals/{original-slug}/` exists
8. Confirm `--env` is `prod` or `dev`

## Phase 1: Translate Plan to Diffs

For each file listed in fix-plan.md's Changes section:

1. Read the file's current content
2. Confirm the file exists at the stated path (for modify/remove) or doesn't exist (for add)
3. Confirm the file's structure matches what the plan assumes — function names, class names, imports referenced in the plan should actually exist
4. Produce the concrete change:
   - **Modify**: unified diff or before/after blocks for each region changed
   - **Add**: full content of the new file
   - **Remove**: confirmation of what's being removed
5. Link each change to the fix-plan.md element it implements (cause coverage or defense)

Write all of this to `{output_dir}/fix-diffs.md`. Format per `references/fix-diffs-format.md`.

**Stop conditions in Phase 1:**

- File doesn't exist where the plan says → STOP, write fix-coder-report.md with BLOCKING_ITEM
- File structure doesn't match plan's assumptions → STOP, BLOCKING_ITEM
- A change cannot be expressed without modifying a file not in the plan → STOP, BLOCKING_ITEM
- Translation requires guessing about plan intent → STOP, BLOCKING_ITEM (the plan is too vague; revise needed)

No source files are modified during Phase 1. fix-diffs.md is the only artifact written.

## Phase 2: Pre-flight Checks

Before applying any diff:

1. **Working directory clean check.** `git status` should show no unrelated changes. If there are, the plan must explicitly account for them or fix-coder stops.
2. **Branch check.** Confirm the working branch is appropriate per project conventions (typically a feature/fix branch, not main).
3. **Baseline lint/typecheck.** Run the project's lint and typecheck commands per forge-project.yml. Capture baseline results — used to detect new violations introduced by the fix vs. pre-existing.
4. **Baseline test run.** Run the test command per forge-project.yml. Capture baseline pass/fail. Used to detect regressions.

**Token mapping note.** The substitution tokens used here (`{{test_command}}`, `{{lint_command}}`, `{{type_check_command}}`, `{{activate_venv}}`, `{{backend_working_dir}}`, etc.) are identical to forge-coder Phase 0. See `~/.claude/skills/forge-coder/SKILL.md` for the full token table; this skill resolves them the same way.

If any pre-flight check fails in a way that prevents safe application (dirty working dir, broken baseline tests on prod files), STOP and report.

## Phase 3: Apply Diffs

For each diff in fix-diffs.md, in the order the plan specifies (or topological order if dependencies exist):

1. Apply the diff to the file (Edit tool for modifies; Write tool for new files)
2. Save
3. Run targeted tests for the file's module if such a runner is available per forge-project.yml
4. Confirm no syntax errors / typecheck failures introduced
5. Move to next diff

If a diff fails to apply cleanly:

- Preserve the partial application for reviewed recovery, or apply the exact
  inverse patch from fix-diffs.md when that inverse is unambiguous. Never mutate
  Git metadata to roll back from a worker lane.
- STOP, write fix-coder-report.md with status FAILED and the failure details
- Do NOT continue to subsequent diffs (avoids partially-applied fix that's worse than no fix)

**Multi-commit-group handling.** If fix-plan.md's Sequencing or Commit Groups section specifies multiple groups:

- Apply diffs one group at a time, in order.
- After each group's diffs are applied, run that group's group-specific tests (if the plan calls them out) before moving to the next group.
- If group-specific tests fail, STOP — do not apply later groups. Status FAILED, report which group, leave applied diffs in working dir for inspection. Do not commit.
- If no group-specific tests are listed, defer to Phase 4's full test pass.

After all diffs (single group or all groups) are applied:

- Run full lint/typecheck across the project
- Compare against Phase 2 baseline — any new violations must be in files explicitly modified by this fix; new violations elsewhere are CRITICAL stop conditions

## Phase 4: Run Test Plan

Execute the tests specified in fix-plan.md's Test Plan section:

1. **New tests.** If the plan calls for new tests, those should now exist (either added by this skill if specified by the plan, or already added if the plan expected them to be pre-existing — clarify in fix-diffs.md). Run them; all must pass.
2. **Regression tests.** Run the regression tests called out in the plan. All must pass.
3. **Adjacent dependents.** Run tests for files identified in fix-plan.md's regression risk analysis. All must pass.
4. **Full test suite.** Run the project's full test suite per forge-project.yml. Compare to Phase 2 baseline. Any new failures are stop conditions.

If any required test fails, STOP and report status FAILED. The fix is not ready.

## Phase 5: Validate Fix

This is the load-bearing fix-specific validation.

### Dev path (`--env dev`)

If repro.md exists with deterministic steps:

1. Execute the repro steps against the local app
2. Confirm the bug no longer triggers
3. Capture evidence (output, screenshots if UI, logs) to `{output_dir}/evidence/post-fix/`

If repro.md is absent or non-deterministic:

1. Identify the test in the test plan that most directly exercises the failing path
2. Confirm that test passes
3. Note in fix-coder-report.md that repro-based validation was unavailable

### Prod path (`--env prod`)

Cannot run against prod. Validation is limited to:

1. Confirm the test in the test plan that exercises the failing path passes
2. Confirm the path is actually exercised (test isn't a no-op or doesn't bypass the failing logic)
3. Note in fix-coder-report.md that full prod validation requires fix-verify after deploy

### Validation outcome

- **VALIDATED**: bug confirmed no longer reproduces (dev) or test exercises and passes (prod) → status SUCCESS eligible
- **NOT VALIDATED**: validation could not be performed → status PARTIAL at best
- **STILL REPRODUCES**: dev validation triggered the bug → STOP, status FAILED. The fix did not fix.

STILL REPRODUCES is the most important outcome to handle correctly. It means the diagnosis or the plan was wrong, and continuing would ship a broken fix. The orchestrator decides whether to re-run investigate or escalate.

## Phase 6: Rollback Plan Re-verification (Prod Only)

For `--env prod`:

1. Read fix-plan.md's Rollback Plan section
2. Cross-reference the actual files modified (from fix-diffs.md) against the rollback steps
3. Confirm each rollback step is executable:
   - File reverts: confirm the files exist and a clean revert is possible (no follow-on commits between fix and now)
   - Schema reverts: confirm the migration is reversible
   - Config reverts: confirm the config can be cleanly restored
4. If rollback plan does not match the actual changes, STOP. The plan needs to be revised to match what was built.

For `--env dev`: rollback re-verification is informal — note in the report that "rollback equivalent to git revert" applies, no formal check needed.

## Phase 7: Commit

Default: a single fix commit.

Commit message format:

```
fix({original-slug or area}): {one-line summary of the fix}

Diagnosis: {diagnosis convergence_status} — {root cause one-liner}
Fix Plan: {fix-plan summary}
Validates: {repro confirmed cleared | test exercising failing path passes}

Refs: .dev/fixes/{original-slug}/{fix-slug}/
```

The Co-Authored-By line and any `🤖 Generated with` trailer are added per harness/agent convention — do not hardcode them in this skill (model and worker identity vary between Claude Code local and Codex worker runs).

If fix-plan.md explicitly specifies multiple commit groups (rare), follow that grouping. Otherwise: one commit.

The commit must include:

- All file changes from fix-diffs.md
- Any new tests added per the plan
- No unrelated changes

Commit through the delivery-bound broker only:

1. Write the exact commit message above to `.dev/forge-tmp/commit-message.txt`.
2. Capture the current physical worktree HEAD as `expected_head`.
3. Run `forge-git-request commit --root "$PROJECT_ROOT" --expected-head
   "$expected_head" --message-file .dev/forge-tmp/commit-message.txt -- <path>...`,
   passing every changed file as a separate literal path.
4. Direct `git add`, `git commit`, `git update-ref`, and mutation fallback are
   forbidden. Treat `INDEX_NOT_CLEAN`, `INDEX_UNMERGED`, `HEAD_MOVED`,
   `BRANCH_CHANGED`, `CAPABILITY_DENIED`, and `BROKER_TIMEOUT` as typed blocked
   outcomes; record them and do not broaden or retry authority.
5. Record the returned operation ID and commit hash in fix-coder-report.md.

## Phase 8: Write fix-coder-report.md

Always written. Format per `references/fix-coder-report-format.md` (authoritative). The block below is a preview for orientation; if it diverges from the reference file, the reference file wins.

```markdown
# Fix Coder Report — {slug}

## Status
{SUCCESS | PARTIAL | FAILED | STOPPED}

- SUCCESS: all phases completed, fix validated, committed
- PARTIAL: fix applied and committed, but validation was limited (prod path with no live verification)
- FAILED: a phase failed and the fix could not be safely completed; working directory state per Status Guidance below
- STOPPED: a Hard Constraint was triggered (scope expansion required, plan-vs-code mismatch, etc.); no commit made

## Environment
{prod | dev}

## Diagnosis Reference
{Citation of root cause from diagnosis.md}

## Plan Applied
{Reference to fix-plan.md, with brief summary of what was applied}

## Files Modified
{List from fix-diffs.md, confirming what was actually changed}

## Test Plan Results
{Per test specified in fix-plan.md: passed | failed | not-run-with-reason}
{Plus full-suite baseline-vs-post comparison}

## Fix Validation
{One of:
 - VALIDATED via repro (dev): bug no longer triggers, evidence at {path}
 - VALIDATED via test (any env): {test name} exercises failing path and passes
 - NOT VALIDATED: validation unavailable; reason
 - STILL REPRODUCES: bug still triggers after fix — fix did NOT work}

## Rollback Plan Re-verification
{For prod: confirmation each rollback step is executable, OR list of mismatches.
 For dev: "Standard git revert applies."}

## Commit
{Commit hash, commit message summary, files in commit. "NOT COMMITTED" if status is FAILED or STOPPED.}

## Advisories Acknowledged
{For each ADVISORY in fix-review.md (if present): one-line summary, then "addressed | not addressed (rationale)".
 Empty section is permitted ONLY if fix-review.md has no ADVISORY items.
 Silent drops are forbidden — every ADVISORY in fix-review.md must appear here.}

## Issues Encountered
{Anything notable that didn't trigger STOP but is worth flagging:
 - lint warnings introduced
 - tests that almost failed
 - timing/flakiness observed
 - assumptions made during translation}

## Confidence + Blocking Items
CONFIDENCE: HIGH | MEDIUM | LOW
BLOCKING_ITEMS: N
```

## Phase 9: Output Quality Gate (Self-Check Before FORGE_DONE)

Before signaling completion, verify the written `fix-coder-report.md` on disk:

1. The file exists at `{output_dir}/fix-coder-report.md` and is non-empty
2. All required sections are present, in this order:
   - Status (with one of SUCCESS / PARTIAL / FAILED / STOPPED)
   - Environment
   - Diagnosis Reference
   - Plan Applied
   - Files Modified
   - Test Plan Results
   - Fix Validation
   - Rollback Plan Re-verification
   - Commit
   - Advisories Acknowledged (header must exist; empty body is permitted ONLY if fix-review.md had no ADVISORY items)
   - Issues Encountered
   - Confidence + Blocking Items (last two lines exactly)
3. The Status value matches phase outcomes (defense-in-depth):
   - SUCCESS → Fix Validation must be VALIDATED via repro or test; Commit must have a real hash; BLOCKING_ITEMS must be 0
   - PARTIAL → Commit must have a real hash; Fix Validation must be VALIDATED or NOT VALIDATED (not STILL REPRODUCES)
   - FAILED → Commit must read "NOT COMMITTED"; BLOCKING_ITEMS must be > 0
   - STOPPED → Commit must read "NOT COMMITTED"; BLOCKING_ITEMS must be > 0
4. If fix-review.md exists with ADVISORY items, every ADVISORY must appear in Advisories Acknowledged (count match). Silent drops fail the gate.
5. The last two lines are exactly `CONFIDENCE: {HIGH|MEDIUM|LOW}` and `BLOCKING_ITEMS: {N}`
6. fix-coder-report.md is touched last so its mtime reflects coder completion (lets downstream stages' mtime checks work correctly)

If any check fails, fix the file and re-verify. Do NOT signal FORGE_DONE with a malformed report — the orchestrator's routing depends on these fields.

Once the self-check passes, signal FORGE_DONE.

## Status Guidance

- **SUCCESS**: All phases completed cleanly, fix is committed, validation confirmed it works.
  - Validation = VALIDATED via repro (dev) or VALIDATED via test (prod with passing failing-path test)
  - All test plan items pass
  - No new lint/typecheck violations
  - Rollback plan re-verified (prod) or trivial (dev)
  - BLOCKING_ITEMS = 0
  - Working directory: clean (changes are in the commit)

- **PARTIAL**: Fix is committed, but full validation wasn't possible.
  - Most common: prod fix where dev cannot reproduce the prod bug, so validation is limited to "test exercises failing path"
  - Acceptable status — fix-verify will handle full prod validation post-deploy
  - BLOCKING_ITEMS may be 0 (acceptable partial) or > 0 (issues worth flagging for fix-verify)
  - Working directory: clean (changes are in the commit)

- **FAILED**: A phase failed and the fix could not be safely completed.
  - Fix is NOT committed
  - BLOCKING_ITEMS > 0
  - Per-phase working-directory state:
    - **Phase 3 mid-application failure**: preserve the partial diff for reviewed recovery, or apply its exact inverse patch when unambiguous; earlier-group diffs remain in the working dir; later-group diffs are unapplied. Report lists which groups applied and which did not.
    - **Phase 4 test failure**: all diffs applied, no commit, working dir dirty. Left for inspection — orchestrator decides revert vs re-dispatch.
    - **Phase 5 STILL REPRODUCES**: all diffs applied, no commit, working dir dirty. Left for inspection — STILL REPRODUCES is the highest-severity signal that the diagnosis or plan is wrong. Orchestrator decides whether to re-investigate, re-plan, or escalate.
  - Orchestrator should escalate (STILL REPRODUCES) or re-dispatch revise mode (test failures, lint regressions outside scope).

- **STOPPED**: Hard Constraint triggered (scope expansion, plan mismatch, structural assumption violation).
  - Fix is NOT committed
  - Per-phase working-directory state:
    - **Phase 0/1 STOP** (config, plan-status, review-verdict, plan-vs-code mismatch): no diffs applied, working dir matches Phase 2 baseline (i.e., whatever it was on entry).
    - **Phase 6 prod rollback mismatch**: all diffs applied, no commit, working dir dirty. Plan must be revised to match the actual changes; the revise pass will likely re-author the rollback section.
  - BLOCKING_ITEMS > 0
  - Orchestrator should re-dispatch revise mode or escalate

## Error Recovery

| Error | Action |
|---|---|
| `forge-project.yml` not found | STOPPED, report missing config |
| `--output-dir` missing | STOPPED, report missing input |
| fix-plan.md not found | STOPPED, report missing input |
| fix-plan.md has BLOCKED status | STOPPED, report — cannot apply blocked plan |
| fix-review.md has REJECT verdict | STOPPED, report — plan must be revised |
| File in plan doesn't exist | STOPPED at Phase 1, BLOCKING_ITEM |
| File structure doesn't match plan | STOPPED at Phase 1, BLOCKING_ITEM |
| Application requires file not in plan | STOPPED at Phase 1 or 3, BLOCKING_ITEM (scope expansion) |
| Diff fails to apply cleanly | FAILED at Phase 3, single diff reverted, no further diffs applied |
| Test plan failure | FAILED at Phase 4, BLOCKING_ITEM |
| Repro still triggers bug after fix | FAILED at Phase 5, STILL REPRODUCES, BLOCKING_ITEM (highest severity — diagnosis or plan was wrong) |
| Rollback plan doesn't match changes (prod) | STOPPED at Phase 6, BLOCKING_ITEM (plan needs revision) |
| Lint/typecheck regressions outside modified files | STOPPED at Phase 3 end, BLOCKING_ITEM (unexpected side effect) |

## Output Directory

```
{output_dir}/
├── problem-statement.md         ← Input
├── repro.md                     ← Input (if present)
├── diagnosis.md                 ← Input
├── fix-plan.md                  ← Input (the plan being applied)
├── fix-review.md                ← Input (if present, must have Verdict: APPROVE)
├── fix-diffs.md                 ← Output: Phase 1 translation artifact
├── fix-coder-report.md          ← Output: Final report (always written)
└── evidence/
    └── post-fix/                ← Dev validation evidence (post-fix screenshots, logs)
```

## What This Skill Does NOT Do

- Does not modify, revise, or improve fix-plan.md
- Does not re-investigate or challenge the diagnosis
- Does not propose alternative approaches when application reveals plan issues
- Does not touch files outside the plan's Changes section
- Does not perform unrelated cleanups, refactors, or formatting
- Does not split fixes across multiple commits unless the plan specifies it
- Does not skip validation phase (Phase 5) on success
- Does not spawn sub-agents
- Does not run adversarial rounds
- Does not have an interactive mode

## Reference Files

| File | When to read | Purpose |
|---|---|---|
| `references/fix-diffs-format.md` | When writing fix-diffs.md | Template for the translation artifact |
| `references/fix-coder-report-format.md` | When writing fix-coder-report.md | Template for the final report |

## Invocation

```
fix-coder \
  --output-dir .dev/fixes/{original-slug}/{fix-slug}/ \
  --env prod \
  --original-slug {original-slug}
```

The orchestrator is responsible for:

- Invoking this skill after fix-plan-reviewer's APPROVE verdict
- Reading fix-coder-report.md to determine next action:
  - SUCCESS or PARTIAL → advance to fix-qa
  - FAILED with STILL REPRODUCES → escalate or re-run investigate (the fix didn't work)
  - FAILED with other → re-dispatch revise mode
  - STOPPED → re-dispatch revise mode (plan was wrong about the code)

This skill is responsible for:

- Reading inputs and validating preconditions
- Translating fix-plan.md to fix-diffs.md
- Applying diffs cleanly with per-file validation
- Running the full test plan
- Validating the fix actually fixes (dev) or that the failing-path test passes (prod)
- Re-verifying rollback plan integrity (prod)
- Producing a single fix commit
- Writing fix-coder-report.md
- Reporting completion via standard FORGE_DONE callback (handled by orchestrator dispatch protocol)
