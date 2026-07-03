---
name: forge-coder
description: >
  Procedural coding skill. Reads implementation.md, validates diffs against
  the codebase, applies changes in execution order, runs tests after each
  commit group, and produces a coder-report.md artifact.
---

# Forge Coder Skill

## Hard Constraints

1. Never apply a diff if `old_string` does not exist in the target file — stop and report
2. Never auto-fix a mismatched diff — report the mismatch exactly as found
3. Never skip the pre-flight validation phase
4. Never continue to the next commit group if tests fail in the current group
5. Always write the coder report, even on failure — partial progress must be documented
6. Never start/stop long-running services (backend, frontend) directly — rely on test commands that may auto-start them via Playwright webServer or similar
7. One git commit per commit group — not one commit per step
8. Never infer missing structure from prose — reject malformed implementation steps instead of guessing
9. Never accept standalone `# new_file` blocks, partial-file placeholders, or commit-group tests that depend on later groups

## Required Reads

Before any coding action, read these files in order:

1. `.claude/forge-project.yml` (fallback: `~/.claude/forge-project.yml`) — project config
2. `.dev/proposals/{slug}/implementation.md` — the spec to execute
3. `.dev/proposals/{slug}/final-plan.md` �� for context on intent (read, don't execute from this)
4. `~/.claude/skills/forge-coder/references/coder-report-format.md` — output template

## Phase 0: Config Loading

1. Read `.claude/forge-project.yml`
   (fallback: `~/.claude/forge-project.yml`)
2. Build the substitution map for all tokens:

| Token | Source | Fallback |
|-------|--------|----------|
| `{{project_name}}` | `project.name` | required |
| `{{test_command}}` | `testing.backend.command` | `pytest` |
| `{{activate_venv}}` | `testing.backend.activate_venv` | null |
| `{{e2e_command}}` | `testing.frontend.e2e_command` | null |
| `{{frontend_working_dir}}` | `services.frontend.working_dir` | null |
| `{{backend_working_dir}}` | `services.backend.working_dir` | `.` |
| `{{migration_system}}` | `database.migration_system` | `none` |
| `{{migration_dir}}` | `database.migration_dir` | null |
| `{{migration_command}}` | `database.migration_command` | null |
| `{{lint_command}}` | `linting.backend.command` | null |
| `{{lint_fix_command}}` | `linting.backend.fix_command` | null |
| `{{frontend_lint_command}}` | `linting.frontend.command` | null |
| `{{type_check_command}}` | `type_checking.backend.command` | null |
| `{{frontend_type_check_command}}` | `type_checking.frontend.command` | null |

3. If `forge-project.yml` is not found, report error and stop:
   ```
   FORGE-CODER ERROR: No forge-project.yml found. Create .claude/forge-project.yml
   with project config before running the coder skill.
   ```

## Phase 1: Input Validation

1. Read `.dev/proposals/{slug}/implementation.md` fully.
2. Locate the **Coverage Matrix** section.
3. Parse every row. If ANY plan item has `Status` = `GAP` or is missing coverage:
   ```
   FORGE-CODER REJECTED: Coverage matrix has gaps.
   Missing coverage for: P3, P7
   Re-run adversarial-implementation to produce complete coverage before coding.
   ```
   Stop. Do not proceed.

4. Build a step list from the **Implementation Steps** section. For each step, record:
   - Step number
   - Target file path
   - Whether it's a new file or modification
   - The `old_string` (for modifications)
   - The `new_string`
   - Reject the document if a step:
     - omits the file path
     - contains more than one target file
     - omits the `new_string`
     - uses a standalone `# new_file` block instead of the normal `old_string/new_string` pair
     - relies on prose like "rest of file unchanged" or "insert near"
   - Canonical new-file shape:
     - `**Action**: new_file`
     - `# old_string` body is exactly `# new file`
     - `# new_string` contains the full file content

5. **Pre-flight diff validation** — for every modification step (not new files):
   - Read the target file
   - Search for `old_string` in the file content
   - Record: FOUND (with line number), NOT FOUND, or NOT UNIQUE

6. Report pre-flight results:
   ```
   PRE-FLIGHT VALIDATION
   Step 1: src/foo/bar.py — FOUND (line 45)
   Step 2: src/foo/baz.py — FOUND (line 112)
   Step 3: tests/test_foo.py — NEW FILE (skip)
   Step 4: src/foo/qux.py — NOT UNIQUE
   Step 5: src/foo/qux.py — NOT FOUND

   Result: 2 validation failures. STOPPING.
   Step 4: old_string matched multiple locations in src/foo/qux.py
   Step 5: old_string not found in src/foo/qux.py
   The file may have changed since implementation.md was written.
   Re-run adversarial-implementation to produce updated diffs.
   ```

7. If ANY step has a mismatch, ambiguity, malformed diff structure, or non-runnable
   commit-group test dependency: stop and report. Write a partial coder-report with
   the validation failure. Do NOT attempt to apply any diffs.

8. If all steps validate: report success and proceed.
   ```
   PRE-FLIGHT VALIDATION: All N steps validated. Proceeding.
   ```

## Phase 2: Branch Check

1. Run `git status` to check working tree state.
2. Run `git branch --show-current` to note the current branch.
3. If working tree has uncommitted changes, warn but continue:
   ```
   WARNING: Working tree has uncommitted changes. Proceeding on branch: feat/my-feature
   ```
4. The coder does NOT create branches — that is the orchestrator's responsibility.
   The coder works on whatever branch it's given.

## Phase 3: Apply Changes

Read the **Commit Groups** section from implementation.md. If commit groups are defined,
follow them. If not, treat all steps as a single commit group.

For each commit group:

1. Log the group:
   ```
   COMMIT GROUP 1: "Add voice reassignment DB migration and ORM field"
   Steps: 1, 2, 3
   ```

2. For each step in the group, in order:

   **For modifications (old_string -> new_string):**
   - Use the Edit tool with the exact `old_string` and `new_string`
   - After the edit, read the file to confirm `new_string` is present
   - If the edit fails (old_string not unique or not found): stop immediately
     ```
     EDIT FAILED at Step 2: old_string not unique in src/foo/bar.py
     Found 2 occurrences. Need more context to disambiguate.
     STOPPING. Manual intervention required.
     ```

   **For new files:**
   - Require the canonical new-file shape from Phase 1
   - Use the Write tool with the full content from `new_string`
   - Confirm the file exists after writing

   **For database migrations:**
   - If the step creates a file in `{{migration_dir}}`, write it normally
   - Note in the report that a migration was created
   - Do NOT run `{{migration_command}}` automatically — tests should handle this,
     or the orchestrator runs it post-coding

3. After all steps in the group are applied, proceed to Phase 4 for this group.

## Phase 4: Test After Each Group

After completing a commit group:

1. **Run group-specific tests** if the implementation.md specifies them:
   ```
   Tests to run: pytest tests/unit/test_voice.py -v
   ```
   The command must be runnable after the group's steps are applied. If the command
   references a test file created in a later group, stop and report the implementation
   as invalid instead of guessing a substitute.

2. If no group-specific tests, determine which tests to run:
   - If any backend files (`.py`) were changed: run `{{test_command}}`
     from `{{backend_working_dir}}`
     (activate venv first if `{{activate_venv}}` is configured)
   - If any frontend files (`.ts`, `.tsx`, `.js`, `.jsx`) were changed:
     run `{{e2e_command}}` from `{{frontend_working_dir}}`
     (skip if `{{e2e_command}}` is null)

3. **If tests PASS:**
   - Stage the changed files for this group: `git add <files>`
   - Commit with the group's message (from implementation.md) or generate one:
     ```
     feat: <brief description of what this group does>

     Applied forge-coder steps N-M from implementation.md
     Proposal: {slug}
     ```
   - Report:
     ```
     COMMIT GROUP 1: PASS — committed as abc1234
     ```

4. **If tests FAIL:**
   - Report the failure with full test output:
     ```
     COMMIT GROUP 1: TESTS FAILED
     Command: pytest tests/unit/test_voice.py -v
     Exit code: 1
     Output:
     [full test output]

     STOPPING. Do not continue to next group.
     Files are modified but NOT committed.
     ```
   - Write the coder report with partial progress
   - Do NOT attempt to fix the failing tests
   - Do NOT continue to the next commit group

## Phase 5: Full Validation

After ALL commit groups pass and are committed:

1. **Full backend test suite:**
   ```bash
   {{activate_venv}} && {{test_command}}
   ```
   Record: total passed, failed, skipped, errors

2. **Linter** (if configured):
   ```bash
   {{lint_command}}
   ```
   Record: pass/fail, number of issues

3. **Type checker** (if configured):
   ```bash
   {{type_check_command}}
   ```
   Record: pass/fail, number of issues

4. **Frontend tests** (if frontend files were changed and e2e is configured):
   ```bash
   cd {{frontend_working_dir}} && {{e2e_command}}
   ```
   Record: pass/fail

5. **Frontend lint** (if configured):
   ```bash
   cd {{frontend_working_dir}} && {{frontend_lint_command}}
   ```

6. **Definition of Done** — walk the checklist from implementation.md.
   For each item, check it off or note the failure.

7. Full validation failures are **non-blocking** — the commits are already made.
   Report the failures in the coder report but do not roll back.
   The QA stage will catch regressions.

## Phase 6: Report

Write `.dev/proposals/{slug}/coder-report.md` following the format in
`references/coder-report-format.md`.

This file is the primary artifact. It MUST be written even on failure —
partial progress is valuable context for the orchestrator and QA.

Add the report path to the artifact list when reporting completion to forge.

---

## Invocation

This skill requires a **slug**. The slug identifies the pipeline directory
at `.dev/proposals/{slug}/` where `implementation.md` and `final-plan.md`
live, and where `coder-report.md` will be written.

The caller is responsible for providing the slug and for any coordination
signaling. This skill focuses solely on executing the implementation and
producing the report.

## Escalation: forge ask

When you hit a **blocking human decision** — an irreversible action (dropping
data, force-pushing, deleting resources), a genuinely ambiguous requirement the
plan does not settle, or a credential/billing surprise — STOP and escalate
instead of guessing:

```
forge ask --slug {slug} --stage {stage} --worker {worker} "<your question>"
```

`{slug}`, `{stage}`, and `{worker}` are surfaced in the stage prompt the
orchestrator dispatched to you — cite them verbatim. This keeps your pipeline
pending open and puts your question on the operator's board; their answer is
routed back to you. If those ids are unavailable, use
`forge ask --session-scope "<question>"`. Either way `forge ask` never blocks or
fails your run.

Do NOT ask for things you can resolve and note: a reasonable default, a naming
choice, a locally-decidable assumption. Log those in `coder-report.md` under an
"Assumptions" note and proceed. Ask-worthy = a human must decide before it is
safe to continue; proceed-and-note = you can continue and the report records it.

## Error Recovery

| Error | Action |
|-------|--------|
| `forge-project.yml` not found | Stop, report missing config |
| `implementation.md` not found | Stop, report missing input |
| Coverage matrix has gaps | Stop, report which items are uncovered |
| `old_string` not found in file | Stop at pre-flight, report all mismatches |
| Edit tool fails (not unique) | Stop at that step, report the ambiguity |
| Tests fail after commit group | Stop, report test output, do not continue |
| Full validation fails | Report failure but do not roll back — commits stand |
| Git commit fails | Report the error, files remain staged |

## What This Skill Does NOT Do

- Does not create branches (orchestrator's job)
- Does not push to remote (orchestrator's job)
- Does not start/stop services (tests handle this)
- Does not run database migrations (tests or orchestrator handle this)
- Does not fix failing tests (report and stop)
- Does not interpret the plan — it executes the diffs literally
- Does not spawn sub-agents — it is a single procedural executor
