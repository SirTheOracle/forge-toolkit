---
name: adversarial-qa
description: >
  Adversarial QA framework for high-confidence verification of a running application after changes.
  Produces independent QA reports, a synthesis pass, a ranked `issues.md`, and a `test-plan.md`
  for missing coverage. Use this skill after `adversarial-proposal` or
  `adversarial-implementation`, after merging a feature branch, before deploy, when unit tests pass
  but UI or integration issues are still suspected, or when the user asks for adversarial QA,
  regression testing, a release check, or a high-confidence QA pass.
---

# Adversarial QA Framework

## Overview

This Codex copy tracks the generalized adversarial QA workflow from the authoritative Claude skill. Two isolated QA testers independently test the application from different angles, then a QA Synthesizer cross-verifies their findings, deduplicates issues, and produces a ranked issues report plus a test plan for missing coverage.

The critical design principle is **information isolation**: QA Tester A and QA Tester B each have their own context window and test the application with zero knowledge of each other's findings. Only after both finish does the QA Synthesizer see both reports. Then A and B review the synthesizer's assessment, and the synthesizer reconciles everything into a final QA report.

Unlike unit tests that validate isolated functions, this skill catches issues that only manifest in the running application: UI regressions, broken page loads, cross-stage interactions, API contract violations, and integration failures.

## Non-Negotiable: Real Workflow Gate

Treat fixture tests, mocked service tests, route-only unit tests, and direct helper assertions as supporting evidence only. They cannot prove the feature works.

Every QA pass must identify the real user workflow or live API/service path that the changed feature is supposed to support, then attempt to execute that path against the running application or a real backend service instance. A QA report may not conclude `SHIP IT`, `accept`, `CLEAR`, or "no confirmed product defects" unless at least one tester or the synthesizer has exercised that live path and recorded the exact evidence.

If the live path is blocked by missing seed data, auth, browser launch failure, external service credentials, or environment instability, classify QA as **BLOCKED** or **NEEDS REAL-WORKFLOW TESTING**. Do not downgrade the missing live-path check into a harmless coverage note. File it as a top issue and make it the first item in `issues.md`.

For backend-heavy features, the live path can be an authenticated API request or service command that uses real database rows and the same endpoint/background job the product uses. It must not be only a mocked pytest fixture that creates idealized rows. For UI features, the live path must include browser interaction or a documented reason why browser execution is impossible plus an equivalent authenticated API reproduction.

## When to Use

- After an adversarial-proposal or adversarial-implementation produces changes
- After merging a feature branch, before deploying
- As a standalone regression check when something feels off
- When unit tests pass but you suspect UI or integration issues
- Any time you want high-confidence QA before shipping

## Prerequisites

This skill follows the generalized multi-agent protocol from the authoritative Claude version.

- In Codex, prefer native subagents to preserve A/B/C isolation.
- When the upstream docs mention teammate APIs, map them to Codex delegation primitives such as `spawn_agent`, `send_input`, and `wait_agent`.
- If you need a file-coordinated fallback instead of long-lived subagents, use `references/subagent-fallback.md`.
- `references/agent-teams-workflow.md` is retained as the upstream round-by-round protocol reference.

### Runtime Prerequisites

The QA testers need a running application. Service commands come from the project config (`forge-project.yml`):

- **Backend**: `{{backend_command}}` in `{{backend_working_dir}}/` (or will be started by Playwright config)
- **Frontend**: `{{frontend_command}}` in `{{frontend_working_dir}}/` (or will be started by Playwright config)
- **Playwright installed**: `cd {{frontend_working_dir}} && {{playwright_install}}`

The Playwright config (`{{frontend_working_dir}}/{{playwright_config}}`) can auto-start servers (mode: `{{playwright_autostart}}`), so testers can also just run `{{e2e_command}}` directly.

### Restart-on-entry (cross-worktree infra lock — qa / qa-retry)

This QA stage runs under the cross-worktree **infra lock** (orchestrator-held;
see forge-orchestrator Hard Rule 23). All worktrees of this repo share one infra
stack (fixed-port services + a shared DB), so a dev server left running by a
*different* worktree's earlier stage would serve **that** worktree's code to your
tests. Before testing, **restart the services against THIS worktree** so you
exercise THIS worktree's code. This is config-driven and identity-checked — do
**not** blindly kill whatever holds a port:

1. **Expected service shape** (from the preamble / `forge-project.yml`):
   - backend: `{{backend_command}}` in `{{backend_working_dir}}/`, URL `{{backend_url}}`
   - frontend: `{{frontend_command}}` in `{{frontend_working_dir}}/`, URL `{{frontend_url}}`
   - the port is the `:PORT` in each URL.
2. **Inspect each configured port:** `lsof -nP -iTCP:<port> -sTCP:LISTEN`; for any
   listener get its command + cwd (`ps -o command= -p <pid>`, `lsof -p <pid> | grep -i cwd`).
3. **Decide per listener:**
   - **Matches the project's expected dev-server shape** (command matches
     `{{backend_command}}`/`{{frontend_command}}` AND cwd is a worktree of THIS
     repo) → it is a Forge-owned server for some worktree. **Stop it** (`kill <pid>`;
     `kill -9` only if it refuses) so you can start THIS worktree's server fresh.
     **Log** pid, command, cwd, port, and the owning worktree.
   - **Unknown process** (not the expected shape) → **STOP and ESCALATE — do NOT
     kill it.** Report `INFRA ESCALATION: configured port <port> held by an unknown
     process (pid=<pid> cmd=<command> cwd=<cwd>) not matching the expected
     dev-server shape; refusing to kill.` Emit `FORGE_BLOCKED` and wait for a human.
   - **Nothing listening** → proceed to start.
4. **Start THIS worktree's services** from `{{backend_working_dir}}`/`{{frontend_working_dir}}`,
   or rely on Playwright `webServer` autostart **only if** it starts a fresh server
   (NOT `reuseExistingServer: true`). Confirm the backend URL responds before testing.

Safe under the lock: exactly one infra stage runs globally at a time, so stopping
a prior holder's leftover server cannot race another live QA run. (`coding` and
`qa-fix` do NOT restart-on-entry — Hard Rule 23.)

## Modes

| Mode | Flag | Behavior |
|------|------|----------|
| **Default** | (none) | Fully automated. Lead orchestrates all rounds without pausing. |
| **Interactive** | `--interactive` | Pauses after Round 1 (user reviews QA findings) and after Round 2 (user reviews synthesis). |

## Input Modes

The skill adapts based on what artifacts are available:

| Input | Behavior |
|-------|----------|
| **Adversarial artifacts exist** | Reads `final-plan.md` + `implementation.md` (if present) to understand what changed and what exact diffs were planned, focuses testing on affected areas |
| **Git diff available** | Uses `git diff main...HEAD` to identify changed files and focus testing |
| **Standalone** | Runs full regression suite — all smoke pages from config, API health |
| **Scoped re-QA** | Receives a list of `still-open` / `regressed` item IDs from a verification cycle, tests ONLY those areas |

### Upstream Artifact Detection

In Step 0, check for these files in `.dev/proposals/{slug}/`:

1. **`final-plan.md`** (from adversarial-proposal) — high-level plan, affected areas, design decisions
2. **`implementation.md`** (from adversarial-implementation) — exact code diffs, file changes, test specs
3. **`proposal-C.md`** (from adversarial-proposal) — synthesized investigation findings

If `implementation.md` exists, it provides the most precise testing scope: the exact files modified, the specific diffs applied, and the test specifications written. Embed a summary of these in the tester prompts so they know exactly what changed and can target their testing.

## Architecture

```
ROUND 0: Setup
-----------------------------------------------------
  Lead detects input mode (artifacts / git diff / standalone)
  Lead identifies test scope and affected areas
  Lead selects strategy pair:
  +----------------------------------------------+
  | QA Tester A: UI Regression Tester             |
  |   - Page loads, component rendering           |
  |   - Console errors, visual regressions        |
  |   - Smoke tests for all pages from config      |
  |   - Screenshot evidence                       |
  |                                               |
  | QA Tester B: Functional Integration Tester    |
  |   - API endpoint contracts                    |
  |   - Cross-stage data flow                     |
  |   - Feature-specific E2E flows                |
  |   - New/changed functionality verification    |
  +----------------------------------------------+

ROUND 1: Independent Testing (parallel)
-----------------------------------------------------
  QA Tester A (Strategy A)        QA Tester B (Strategy B)
  +--------------------+          +--------------------+
  | Own context         |          | Own context         |
  |                     |          |                     |
  | Sees ONLY:          |          | Sees ONLY:          |
  | - Test scope        |          | - Test scope        |
  | - Source files      |          | - Source files       |
  | - Strategy A        |          | - Strategy B        |
  |                     |          |                     |
  | RUNS:               |          | RUNS:               |
  | - Playwright tests  |          | - API curl tests    |
  | - Screenshot checks |          | - Playwright E2E    |
  | - Console error     |          | - pytest integration |
  |   monitoring        |          |   tests             |
  |                     |          |                     |
  | CANNOT see B -------+-- X ----+-- CANNOT see A      |
  |                     |          |                     |
  | -> qa-report-A.md   |          | -> qa-report-B.md   |
  +---------------------+          +---------------------+

  <-- Quality check: real test evidence, not speculation -->
  <-- [Interactive: Checkpoint 1] -->

ROUND 2: Synthesis + Cross-Verification
-----------------------------------------------------
  QA Synthesizer C (teammate)
  +--------------------------------------+
  | Phase 0: Runs own spot-checks        |
  | Phase 1-2: Reads qa-report-A + B     |
  | Phase 3: Cross-verifies key findings |
  |   - Re-runs failing tests to confirm |
  |   - Checks if A's issues reproduce   |
  |   - Checks if B's issues reproduce   |
  | Phase 4: Synthesizes + ranks         |
  | Phase 5: Writes isolated review files|
  |                                      |
  | -> qa-synthesis.md   (lead-only)     |
  | -> review-for-A.md   (A's eyes only) |
  | -> review-for-B.md   (B's eyes only) |
  +--------------------------------------+

  <-- [Interactive: Checkpoint 2] -->

ROUND 3: Feedback (parallel, ISOLATED)
-----------------------------------------------------
  QA Tester A (still alive)       QA Tester B (still alive)
  +--------------------+           +--------------------+
  | Reads ONLY:         |           | Reads ONLY:         |
  | - review-for-A.md   |           | - review-for-B.md   |
  | + own qa-report     |           | + own qa-report     |
  |                     |           |                     |
  | CANNOT see:         |           | CANNOT see:         |
  | - qa-synthesis.md   |           | - qa-synthesis.md   |
  | - review-for-B.md   |           | - review-for-A.md   |
  | - qa-report-B.md    |           | - qa-report-A.md    |
  |                     |           |                     |
  | May re-run tests    |           | May re-run tests    |
  | to defend findings  |           | to defend findings  |
  |                     |           |                     |
  | -> feedback to lead  |           | -> feedback to lead  |
  +---------------------+           +---------------------+

ROUND 4: Reconciliation + Final Report
-----------------------------------------------------
  QA Synthesizer C (still alive)
  +--------------------------------------+
  | Receives feedback from A and B       |
  | Reconciles into final QA report      |
  | -> issues.md       (ranked issues)   |
  | -> test-plan.md    (new test cases)  |
  +--------------------------------------+
```

### Why Isolation Works for QA

Each tester has its own context window. A and B share no findings during Round 1. This prevents:
- **Confirmation bias**: If A finds an issue, B might unconsciously skip testing that area
- **Tunnel vision**: Both testers following the same testing path
- **False confidence**: One tester assuming the other covered something

| Round | Who | Can See | Cannot See |
|-------|-----|---------|------------|
| 1 | A | Test scope + source files + Strategy A | B's report |
| 1 | B | Test scope + source files + Strategy B | A's report |
| 2 | C | Test scope + qa-report-A + qa-report-B + source files | -- |
| 3 | A | review-for-A.md + its own qa-report-A.md | qa-synthesis.md, review-for-B.md, qa-report-B.md |
| 3 | B | review-for-B.md + its own qa-report-B.md | qa-synthesis.md, review-for-A.md, qa-report-A.md |
| 4 | C | A's feedback + B's feedback + its own prior synthesis | -- |

### Why Teammates Stay Alive

All 3 teammates persist across rounds:
- A and B retain their full testing context from Round 1 when reviewing C's assessment in Round 3
- They can re-run specific tests to defend their findings
- C retains its cross-verification notes from Round 2 when reconciling in Round 4

---

## How to Execute

When the user triggers this skill, you (the lead) orchestrate the entire workflow. Read `references/agent-teams-workflow.md` for the complete tool call sequence.

In Codex, treat the teammate-specific calls in that reference as conceptual. Preserve the same isolation and sequencing with Codex subagents, or fall back to the file-based workflow when persistent subagent messaging is not practical.

### Step 0: Load Project Config and Gather Context

1. **Read project config**: `.claude/forge-project.yml`
   - Extract all service, testing, auth, and QA config values
   - Build the placeholder substitution map (see "Config Substitution" below)
   - If the config file is missing, abort with: "No forge-project.yml found. Create one before running adversarial QA."
2. Get the **test scope** from the user (or auto-detect):
   - If adversarial artifacts exist in `.dev/proposals/{slug}/`, read `final-plan.md` AND `implementation.md` (if present) to understand changes
   - If `implementation.md` exists, extract the list of modified files, code diffs summary, and test specifications — this gives testers precise targeting
   - If on a feature branch, run `git diff main...HEAD --name-only` to identify changed files
   - If standalone, scope is "full regression"
   - If scoped re-QA (from verification loop), scope is limited to the `still-open` / `regressed` item IDs
3. Identify **affected areas** using the smoke pages and core workflows from config
4. Define the **real workflow gate** for this QA pass:
   - Name the exact user action, API endpoint, background job, or service command that proves the changed feature works in the product.
   - Identify the required real data row or setup, such as a candidate ID, draft ID, tenant, uploaded file, feed, or generated job.
   - Decide how to create or locate that data without relying only on mocked fixtures.
   - Add this gate to `test-scope.md` under `## Real Workflow Gate`.
   - Tell both testers that this gate is mandatory. At least one tester must execute it, or the final QA status must be blocked.
5. Check if both servers are running (backend on `{{backend_url}}`, frontend on `{{frontend_url}}`)
6. Create output directory and evidence subdirectories:
   ```
   {project}/.dev/qa/{issue-slug}/
   {project}/.dev/qa/{issue-slug}/evidence/screenshots/
   {project}/.dev/qa/{issue-slug}/evidence/api-responses/
   {project}/.dev/qa/{issue-slug}/evidence/test-results/
   ```
7. Save the test scope as `test-scope.md`
8. Read the agent role files to embed in teammate prompts:
   - `agents/qa-tester.md`
   - `agents/qa-synthesizer.md`
   - `agents/qa-critic.md`
   - `agents/qa-reconciler.md`
   - `references/qa-report-format.md`

### Step 1: Create Team and Tasks

```
TeamCreate({ team_name: "adversarial-qa" })
```

Create 5 tasks with dependencies:

| Task | Subject | Blocked By |
|------|---------|-----------|
| #1 | QA Report A: UI Regression Testing | -- |
| #2 | QA Report B: Functional Integration Testing | -- |
| #3 | QA Synthesis: Cross-verification + ranking | #1, #2 |
| #4 | Feedback from A and B | #3 |
| #5 | Final QA Report: issues.md + test-plan.md | #4 |

### Step 2: Round 1 -- Spawn A and B (Parallel)

Spawn both testers **in the same turn** so they run in parallel:

```
Agent({
  team_name: "adversarial-qa",
  name: "qa-tester-a",
  subagent_type: "general-purpose",
  prompt: "{test scope + source files + qa-tester role + qa-report format
            + Strategy A (UI Regression) assignment
            + available test commands + Playwright helpers reference
            + save as qa-report-A.md + ISOLATION RULE + wait for instructions}",
  run_in_background: true
})

Agent({
  team_name: "adversarial-qa",
  name: "qa-tester-b",
  subagent_type: "general-purpose",
  prompt: "{same prompt but Strategy B (Functional Integration) assignment
            + save as qa-report-B.md + ISOLATION RULE}",
  run_in_background: true
})
```

**CRITICAL -- Isolation rule in both prompts:**
> "You are one of two independent QA testers. You must NOT read any other QA report files in the output directory. Do NOT look at any files named qa-report-B.md, qa-synthesis.md, review-for-A.md, review-for-B.md, issues.md, or test-plan.md. Only read the source files and test scope listed above."

**CRITICAL -- Both prompts must instruct the teammate to WAIT after completing their report**, not exit. They will be needed again in Round 3.

Monitor inbox for "done" messages from both A and B.

After both arrive: **quality check** (real test evidence, actual pass/fail results, screenshots where applicable).

Reject the Round 1 reports as incomplete if neither report executed the real workflow gate. Send one or both testers back with explicit instructions to run the gate before synthesis. Do not let passing unit tests, mocked pytest tests, or route-only frontend tests substitute for the gate.

For wizard flows or multi-step forms, verify every step exposes its primary action in the expected navigation container, including the final step. Do not treat "button exists somewhere on the page" as sufficient.

### Step 2.5: Convergence Check

If both testers find **zero issues**, all tests pass, and the real workflow gate was executed successfully, the synthesis adds limited value. In this case:
1. Skip Rounds 2-3
2. Spawn C for a lightweight review (spot-check a few areas neither explicitly tested)
3. C writes `issues.md` (clean bill of health or minor findings) + `test-plan.md` directly

If both testers find zero issues but the real workflow gate was not executed, do not use convergence. Treat the QA pass as blocked and require a gate run.

### Step 3: Round 2 -- Spawn C (After A and B Complete)

When both "done" messages arrive and findings exist:

```
Agent({
  team_name: "adversarial-qa",
  name: "qa-synthesizer-c",
  subagent_type: "general-purpose",
  prompt: "{instruction to run own spot-checks FIRST (Phase 0)
            + then read qa-report-A.md and qa-report-B.md
            + qa-synthesizer role
            + cross-verify: re-run key failing tests to confirm
            + verify whether the real workflow gate was actually executed
            + produce THREE files: qa-synthesis.md, review-for-A.md, review-for-B.md
            + isolation rules for review files
            + wait for instructions}",
  run_in_background: true
})
```

Monitor inbox for C's "done" message.

### Step 4: Round 3 -- Message A and B with Their Review Files

When C's "done" arrives, message both testers (they're still alive):

```
SendMessage({
  type: "message",
  recipient: "qa-tester-a",
  content: "Read review-for-A.md -- a QA reviewer's assessment of your findings.
            ISOLATION RULE: Read ONLY review-for-A.md. Do NOT read qa-synthesis.md,
            review-for-B.md, qa-report-B.md, or any other files in the output
            directory. You may re-run tests to defend your findings. Give detailed feedback.",
  summary: "Review feedback for QA Tester A"
})

SendMessage({
  type: "message",
  recipient: "qa-tester-b",
  content: "Read review-for-B.md -- a QA reviewer's assessment of your findings.
            ISOLATION RULE: Read ONLY review-for-B.md. Do NOT read qa-synthesis.md,
            review-for-A.md, qa-report-A.md, or any other files in the output
            directory. You may re-run tests to defend your findings. Give detailed feedback.",
  summary: "Review feedback for QA Tester B"
})
```

Monitor inbox for feedback from both A and B.

### Step 5: Round 4 -- Forward Feedback to C

When both feedback messages arrive, forward them to C:

```
SendMessage({
  type: "message",
  recipient: "qa-synthesizer-c",
  content: "Both QA testers reviewed your assessment and responded. Reconcile
            their feedback and produce the final QA report.

            Feedback from QA Tester A:
            {A's feedback from inbox}

            Feedback from QA Tester B:
            {B's feedback from inbox}

            {qa-reconciler role embedded}

            Save the final report as:
            - {output_dir}/issues.md (ranked issues)
            - {output_dir}/test-plan.md (new test cases to add)",
  summary: "Reconcile feedback into final QA report"
})
```

Monitor inbox for C's "done" message.

When E2E covers wizard navigation, scope action assertions to the expected container (for example a `data-testid="wizard-nav"` locator) instead of page-wide button selectors.

### Step 6: Cleanup and Present

```
SendMessage({ type: "shutdown_request", recipient: "qa-tester-a", content: "QA complete" })
SendMessage({ type: "shutdown_request", recipient: "qa-tester-b", content: "QA complete" })
SendMessage({ type: "shutdown_request", recipient: "qa-synthesizer-c", content: "QA complete" })
// Wait for shutdown approvals
TeamDelete()
```

Present `issues.md` and `test-plan.md` to the user. Offer to:
- Show the full QA trail (all reports)
- Explain specific findings
- Run additional targeted tests
- Proceed to fix identified issues

---

## Building the Teammate Prompts

Each teammate prompt must be **self-contained** -- embed everything inline since teammates don't inherit the lead's conversation.

### Config Substitution

Before embedding agent role files and reference docs into sub-agent prompts, the orchestrator MUST substitute all `{{placeholder_tokens}}` with values from `forge-project.yml`. Sub-agents don't inherit the orchestrator's context, so they cannot read the config themselves.

Substitution map (built in Step 0):

| Token | Config source |
|-------|---------------|
| `{{project_name}}` | `project.name` |
| `{{backend_url}}` | `http://localhost:{services.backend.port}` |
| `{{frontend_url}}` | `http://localhost:{services.frontend.port}` |
| `{{backend_command}}` | `services.backend.command` |
| `{{frontend_command}}` | `services.frontend.command` |
| `{{frontend_working_dir}}` | `services.frontend.working_dir` |
| `{{backend_working_dir}}` | `services.backend.working_dir` |
| `{{activate_venv}}` | `services.backend.activate_venv` |
| `{{test_command}}` | `testing.backend.command` |
| `{{e2e_command}}` | `testing.frontend.e2e_command` |
| `{{playwright_install}}` | `testing.frontend.playwright_install` |
| `{{playwright_config}}` | `testing.frontend.playwright_config` |
| `{{playwright_autostart}}` | `testing.frontend.playwright_autostart` |
| `{{e2e_dir}}` | `testing.frontend.e2e_dir` |
| `{{screenshot_full_page}}` | `testing.screenshot.full_page` |
| `{{screenshot_viewport}}` | `testing.screenshot.viewport` |
| `{{test_email}}` | `auth.test_email` |
| `{{test_password}}` | `auth.test_password` |
| `{{login_endpoint}}` | `auth.login_endpoint` |
| `{{smoke_pages}}` | `qa.smoke_pages` (render as markdown list) |
| `{{core_workflows}}` | `qa.core_workflows` (render as markdown list) |

For `{{smoke_pages}}`, render each entry as a markdown bullet with name, path, and auth requirement. For `{{core_workflows}}`, render each entry as a markdown bullet with name and description.

### What to Embed in Every Prompt

1. **The test scope** -- what to test and why (from test-scope.md)
   - Include the `## Real Workflow Gate` verbatim and state that it is mandatory.
2. **Affected source files** -- explicit list of files changed or relevant
3. **The agent role** -- full content from the relevant `agents/*.md` file
4. **The QA report format** -- full content from `references/qa-report-format.md`
5. **The output path and filename** -- exact save location
6. **The isolation rule** -- for A and B in Rounds 1 and 3
7. **The strategy assignment** -- for A and B in Round 1
8. **The "wait" instruction** -- tell teammates to wait for further messages after completing
9. **Available test commands** -- what tools they can use to test:

```
## Available Test Commands

### Playwright (UI tests)
cd {{frontend_working_dir}}

# Run all existing Playwright tests
{{e2e_command}}

# Run a specific test file
{{e2e_command}} {{e2e_dir}}/some-test.spec.ts

# Take a screenshot of any page
{{screenshot_full_page}}

# Run with headed browser for debugging
{{e2e_command}} --headed

### Backend (API tests)
cd {{backend_working_dir}}
{{activate_venv}}

# Run all pytest tests
{{test_command}}

# Run specific test file
{{test_command}} tests/unit/test_file.py -v

# Run e2e tests
{{test_command}} tests/e2e/ -v

### API Smoke Tests (curl)
# Health check
curl -s {{backend_url}}/docs | head -20

# Test authenticated endpoints (get token first)
curl -s -X POST {{backend_url}}{{login_endpoint}} \
  -H "Content-Type: application/json" \
  -d '{"email":"{{test_email}}","password":"{{test_password}}"}' | jq .token

### Screenshots (always use Playwright CLI, never MCP)
{{screenshot_full_page}}
```

### Strategy Details

| Strategy | Focus | Test Types |
|----------|-------|-----------|
| **A: UI Regression** | Visual integrity, page loads, component rendering, console errors | Playwright screenshots, existing spec files, smoke test all pages from config |
| **B: Functional Integration** | Data flow, API contracts, feature behavior, cross-component interactions | API curl tests, new Playwright E2E flows, pytest integration tests |

Both strategies must account for the real workflow gate. Strategy A may satisfy it through browser interaction. Strategy B may satisfy it through authenticated API calls, a real background job, or a service command that uses live database rows. If A cannot run browser automation, B's live API/service evidence becomes mandatory.

### Adapting to Input Mode

| Input Mode | Tester A Emphasis | Tester B Emphasis |
|-----------|-------------------|-------------------|
| **Adversarial artifacts** | Regression test areas mentioned in final-plan.md | Test the specific changes proposed |
| **Git diff** | Smoke test all pages, focus screenshots on changed components | Test changed API endpoints and service logic |
| **Standalone** | Full smoke page regression (all pages from config) | Full API contract + core workflow E2E |

---

## Test Strategy Decision

A key output of this skill is deciding whether to use existing test infrastructure or create new tests:

### Use Existing Tests When
- The change is in an area already covered by existing E2E specs in `{{frontend_working_dir}}/{{e2e_dir}}/`
- Existing Playwright helpers cover the flow
- The `tests/` directory has relevant pytest tests

### Create New Tests When
- A new feature was added with no existing test coverage
- The change affects a flow not covered by current specs
- A gap is identified where regression could recur

The **test-plan.md** output explicitly recommends which approach for each area, referencing:
- Existing test files that should be run
- New test files that should be created (with skeleton code)
- Whether tests should be Playwright (UI) or pytest (backend)

---

## Error Handling

### Teammate Timeout / Exit Recovery

| Round | Failure | Recovery |
|-------|---------|----------|
| Round 1 | One tester fails | Wait for the other. C reviews single report for gaps. |
| Round 1 | Both fail | Abort workflow. Report to user. |
| Round 2 | Synthesizer fails | Re-spawn C. If second attempt fails, present raw reports. |
| Round 3 | One tester fails | Proceed to Round 4 with available feedback. |
| Round 3 | Both fail | C produces final report from synthesis alone. |
| Round 4 | Synthesizer fails | Re-spawn reconciler. If fails again, present qa-synthesis.md. |

### Test Infrastructure Failures

| Failure | Recovery |
|---------|----------|
| Backend not running | Tester starts it: `cd {{backend_working_dir}} && {{activate_venv}} && {{backend_command}} &` |
| Frontend not running | Tester starts it: `cd {{frontend_working_dir}} && {{frontend_command}} &` |
| Playwright not installed | Tester runs: `cd {{frontend_working_dir}} && {{playwright_install}}` |
| Database connection fails | Report as a critical issue in the QA report |

---

## Known Tradeoffs

**Synthesizer bias toward dismissing findings**: C might dismiss legitimate issues as "environment-specific" or "flaky" rather than real bugs. The qa-reconciler role includes guidance to steelman findings and re-run tests before dismissing.

**Test execution time**: Running Playwright tests takes real time. Each tester may take 3-5 minutes for UI tests. The parallel execution of A and B mitigates this.

**Non-deterministic tests**: Some tests may be flaky. The QA report format requires testers to run failing tests multiple times and note consistency.

---

## Invoking the Skill

### Direct Prompt

```
Use the adversarial QA framework to verify the changes in the current branch.
The relevant proposal is at .dev/proposals/premium-lip-sync-regen/final-plan.md
```

### As a Reusable Command Wrapper

Invoke with:
```
/adversarial-qa
```

With specific scope:
```
/adversarial-qa Verify the BGM feature changes. Focus on Stage 9 assembly and the dashboard.
```

For interactive mode:
```
/adversarial-qa --interactive Full regression check after the billing feature merge.
```

---

## Output Directory

```
{project}/.dev/qa/{issue-slug}/
|-- test-scope.md             <- Round 0: what we're testing and why
|-- qa-report-A.md            <- Round 1: QA Tester A (UI Regression)
|-- qa-report-B.md            <- Round 1: QA Tester B (Functional Integration)
|-- qa-synthesis.md           <- Round 2: Synthesizer C (lead-only)
|-- review-for-A.md           <- Round 2: C's review for A (no mention of B)
|-- review-for-B.md           <- Round 2: C's review for B (no mention of A)
|-- manifest.yaml             <- Round 4: Merged evidence manifest (machine-readable)
|-- issues.md                 <- Round 4: Final ranked issues (deliverable)
|-- test-plan.md              <- Round 4: Test cases to add (deliverable)
|-- verification-report.yaml  <- Round 5: Verification results (from adversarial-verify)
+-- evidence/
    |-- screenshots/          <- All screenshots from QA testers
    |-- api-responses/        <- Saved API response bodies
    |-- test-results/         <- Test command stdout/stderr
    |-- verify-cycle-1/       <- Re-taken screenshots + pixel diffs (from verify)
    |   |-- screenshots/
    |   +-- diffs/
    +-- verify-cycle-2/       <- If second verification cycle runs
        |-- screenshots/
        +-- diffs/
```

---

## Step 7: Verification Loop (Automated)

After Step 6 (cleanup), the lead runs the verification loop. This is automatic -- no user intervention unless escalation is needed.

### Cycle 1: Verify

```
1. Invoke adversarial-verify against {output_dir}/manifest.yaml
2. adversarial-verify re-runs all commands, re-takes all screenshots,
   runs pixel-diff comparisons against baselines
3. Produces verification-report.yaml with verdict: CLEAR or ISSUES_REMAIN
```

**If verdict = CLEAR**: Done. Present clean bill of health to user. Ship it.

**If verdict = ISSUES_REMAIN**: Proceed to scoped re-QA.

### Cycle 1.5: Scoped Re-QA

```
1. Read verification-report.yaml for still-open findings and regressed checks
2. Re-run adversarial-qa in SCOPED mode:
   - Only test the specific items that failed verification
   - Input mode = "scoped re-QA" with the list of item IDs
   - Same 4-round adversarial process, but narrower scope
3. Produces updated manifest.yaml
```

### Cycle 2: Verify Again

```
1. Invoke adversarial-verify again (cycle 2)
2. Same process: re-run commands, re-take screenshots, pixel-diff
3. Produces updated verification-report.yaml
```

**If verdict = CLEAR**: Done. Ship it.

**If verdict = ISSUES_REMAIN**: **Escalate to user.** Present:
- The full evidence trail across both cycles
- Which items remain unresolved
- The verification-report.yaml with all evidence paths
- Recommendation: manual investigation needed

### Maximum Cycles

The verification loop runs at most **2 cycles**. If issues persist after 2 rounds of QA + verification, human intervention is required. This prevents infinite loops on genuinely hard-to-fix or environment-specific issues.

```
QA Round 1-4 → manifest.yaml
  → Verify (cycle 1) → CLEAR? → done
  → ISSUES_REMAIN → Scoped Re-QA → updated manifest.yaml
    → Verify (cycle 2) → CLEAR? → done
    → ISSUES_REMAIN → escalate to user
```

---

## Reference Files

| File | When to Read | Purpose |
|------|-------------|---------|
| `references/agent-teams-workflow.md` | Before starting orchestration | Complete tool call sequence for all rounds |
| `references/qa-report-format.md` | When building tester prompts | QA report template with manifest requirements |
| `references/subagent-fallback.md` | If persistent teammate messaging is not available | Alternative workflow using Codex subagents or file coordination |
| `agents/qa-tester.md` | When building A and B prompts | QA testing role, evidence collection protocol, manifest format |
| `agents/qa-synthesizer.md` | When building C's Round 2 prompt | Cross-verification, synthesis, manifest merge |
| `agents/qa-critic.md` | When messaging A and B in Round 3 | Feedback guidance for defending findings |
| `agents/qa-reconciler.md` | When messaging C in Round 4 | Reconciliation + final report + manifest update |

### Cross-Skill References

| File | Skill | Purpose |
|------|-------|---------|
| `adversarial-verify/SKILL.md` | adversarial-verify | Verification skill invoked in Step 7 |
| `adversarial-verify/references/manifest-schema.md` | adversarial-verify | Canonical manifest.yaml schema (shared by both skills) |
| `adversarial-verify/references/verification-report-format.md` | adversarial-verify | Verification report output format |

## Agent Roles Summary

| Agent | File | Used In | Purpose |
|-------|------|---------|---------|
| QA Tester | `agents/qa-tester.md` | Round 1 (A and B) | Independent testing with assigned strategy + structured evidence |
| QA Synthesizer | `agents/qa-synthesizer.md` | Round 2 (C) | Cross-verification, synthesis, manifest merge, isolated review files |
| QA Critic | `agents/qa-critic.md` | Round 3 (A and B) | Review assessment, defend or accept findings |
| QA Reconciler | `agents/qa-reconciler.md` | Round 4 (C) | Bias-aware reconciliation into final report + manifest update |
