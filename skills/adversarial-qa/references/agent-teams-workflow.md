# Adversarial QA Framework -- Agent Teams Workflow Design

## Overview

This document describes how the adversarial QA framework runs as a Claude Code Agent Teams workflow, fully automated from a single prompt. The user triggers the skill, and the team lead orchestrates the entire 4-round process.

## Plan Compatibility

**Agent Teams** requires the TeammateTool (TeamCreate, SendMessage, etc.) which may be gated behind Enterprise/Team plans.

- **If Agent Teams is available**: Use the primary workflow below
- **If Agent Teams is NOT available**: Use the subagent fallback (see `references/subagent-fallback.md`)

---

## Architecture

```
YOU (trigger the skill with a test scope)
  |
  v
TEAM LEAD (orchestrator -- your main Claude Code session)
  |
  |-- Detects input mode, identifies test scope
  |
  |-- Spawns --> QA-TESTER-A (teammate, background, UI Regression)
  |                 |
  |                 |-- Verifies environment (backend + frontend running)
  |                 |-- Runs existing Playwright specs
  |                 |-- Takes screenshots of all pages/stages
  |                 |-- Monitors console for errors
  |                 |-- Writes qa-report-A.md to disk
  |                 +-- Sends "done" message to lead
  |
  |-- Spawns --> QA-TESTER-B (teammate, background, IN PARALLEL, Functional Integration)
  |                 |
  |                 |-- Verifies environment
  |                 |-- Tests API endpoints with curl
  |                 |-- Writes + runs E2E Playwright flows
  |                 |-- Runs pytest for relevant tests
  |                 |-- Writes qa-report-B.md to disk
  |                 +-- Sends "done" message to lead
  |
  |  <-- Lead waits for both "done" messages -->
  |  <-- Quality check: real evidence, not speculation -->
  |  <-- Convergence check: if both find zero issues -> lightweight review -->
  |  <-- [Interactive mode: Checkpoint 1 -- user reviews findings] -->
  |
  |-- Spawns --> QA-SYNTHESIZER-C (teammate, background)
  |                 |
  |                 |-- Runs own spot-checks (Phase 0)
  |                 |-- Reads qa-report-A.md + qa-report-B.md
  |                 |-- Cross-verifies: re-runs failing tests
  |                 |-- Writes qa-synthesis.md (lead-only)
  |                 |-- Writes review-for-A.md (A's eyes only)
  |                 |-- Writes review-for-B.md (B's eyes only)
  |                 +-- Sends "done" message to lead
  |
  |  <-- Lead waits for C "done" -->
  |  <-- [Interactive mode: Checkpoint 2 -- user reviews synthesis] -->
  |
  |-- Messages --> QA-TESTER-A (still alive)
  |                 |
  |                 |-- "Read review-for-A.md and respond"
  |                 |-- CANNOT read: qa-synthesis.md, review-for-B.md, qa-report-B.md
  |                 |-- May re-run tests to defend findings
  |                 +-- Sends feedback to lead
  |
  |-- Messages --> QA-TESTER-B (still alive)
  |                 |
  |                 |-- "Read review-for-B.md and respond"
  |                 |-- CANNOT read: qa-synthesis.md, review-for-A.md, qa-report-A.md
  |                 |-- May re-run tests to defend findings
  |                 +-- Sends feedback to lead
  |
  |  <-- Lead waits for both feedback messages -->
  |
  |-- Messages --> QA-SYNTHESIZER-C (still alive)
  |                 |
  |                 |-- Lead forwards feedback from A and B
  |                 |-- C reconciles feedback with its synthesis
  |                 |-- Writes issues.md to disk
  |                 |-- Writes test-plan.md to disk
  |                 +-- Sends "done" message to lead
  |
  |  <-- Lead presents issues.md + test-plan.md to user -->
  |
  +-- Shutdown all teammates, cleanup team
```

---

## Detailed Tool Call Sequence

### Round 0: Setup

```
// Lead creates the team
TeamCreate({
  team_name: "adversarial-qa",
  description: "Adversarial QA verification for: {test_scope_summary}"
})

// Detect input mode:
// 1. Check for .dev/proposals/{slug}/final-plan.md -> read it for context
// 2. Check for .dev/proposals/{slug}/implementation.md -> read for exact diffs and test specs
// 3. Check git diff main...HEAD --name-only -> identify changed files
// 4. If neither, scope = "full regression"
// 5. If scoped re-QA (from verification loop), scope = specific item IDs

// Create evidence directories
// mkdir -p {output_dir}/evidence/screenshots
// mkdir -p {output_dir}/evidence/api-responses
// mkdir -p {output_dir}/evidence/test-results

// Lead creates the task list for tracking
TaskCreate({
  subject: "QA Report A: UI Regression Testing",
  description: "Run UI regression tests and write qa-report-A.md",
  activeForm: "QA Tester A testing..."
})

TaskCreate({
  subject: "QA Report B: Functional Integration Testing",
  description: "Run integration tests and write qa-report-B.md",
  activeForm: "QA Tester B testing..."
})

TaskCreate({
  subject: "QA Synthesis: Cross-verification",
  description: "Cross-verify findings from A and B, write qa-synthesis.md + review files",
  activeForm: "Cross-verifying findings..."
})
// Task 3 blocked by 1 and 2
TaskUpdate({ taskId: "3", addBlockedBy: ["1", "2"] })

TaskCreate({
  subject: "Feedback from A and B on synthesis",
  description: "A reads review-for-A.md and B reads review-for-B.md, give feedback",
  activeForm: "Collecting QA feedback..."
})
TaskUpdate({ taskId: "4", addBlockedBy: ["3"] })

TaskCreate({
  subject: "Final QA Report: issues.md + test-plan.md",
  description: "C reconciles feedback into final deliverables",
  activeForm: "Producing final QA report..."
})
TaskUpdate({ taskId: "5", addBlockedBy: ["4"] })

// Lead saves test-scope.md
```

### Round 1: Independent Testing (A and B in parallel)

```
// Spawn QA Tester A
Agent({
  team_name: "adversarial-qa",
  name: "qa-tester-a",
  subagent_type: "general-purpose",
  prompt: `
    You are an independent QA tester. Your job is to test the running
    application and report what you find with evidence.

    CRITICAL ISOLATION RULE: You are one of two independent QA testers.
    You must NOT read any other QA report files in the output directory.
    Do NOT look at any files named qa-report-B.md, qa-synthesis.md,
    review-for-A.md, review-for-B.md, issues.md, or test-plan.md.
    Only read the source files and test scope listed below.

    ## Test Scope
    {embedded test scope}

    ## Affected Files
    {list of changed/relevant files}

    ## Your Testing Strategy
    You have been assigned: **UI Regression Tester**
    Focus on: page loads, component rendering, console errors, visual
    regressions, screenshot evidence. Smoke test all pages from config.
    Run existing Playwright specs.

    ## Available Test Commands
    {embedded test commands from SKILL.md}

    ## Existing Playwright Infrastructure
    - Config: {{frontend_working_dir}}/{{playwright_config}} (auto-starts: {{playwright_autostart}})
    - E2E directory: {{frontend_working_dir}}/{{e2e_dir}}/
    - Auth: Handled automatically via storageState (credentials from config)

    ## Your Task
    1. Verify the environment is running (backend + frontend)
    2. Run existing Playwright test suite
    3. Take screenshots of dashboard and each pipeline stage
    4. Monitor for console errors
    5. Report all findings with evidence
    6. Save your report as: {output_dir}/qa-report-A.md

    ## QA Report Format
    {embedded qa-report-format.md}

    ## QA Tester Role
    {embedded qa-tester.md}

    ## When Done
    - Claim task #1 and mark it completed
    - Send your key findings summary to the team lead
    - Then WAIT for further instructions (do not exit)
  `,
  run_in_background: true
})

// Spawn QA Tester B -- IN THE SAME TURN for true parallel execution
Agent({
  team_name: "adversarial-qa",
  name: "qa-tester-b",
  subagent_type: "general-purpose",
  prompt: `
    You are an independent QA tester. Your job is to test the running
    application and report what you find with evidence.

    CRITICAL ISOLATION RULE: You are one of two independent QA testers.
    You must NOT read any other QA report files in the output directory.
    Do NOT look at any files named qa-report-A.md, qa-synthesis.md,
    review-for-A.md, review-for-B.md, issues.md, or test-plan.md.
    Only read the source files and test scope listed below.

    ## Test Scope
    {embedded test scope}

    ## Affected Files
    {list of changed/relevant files}

    ## Your Testing Strategy
    You have been assigned: **Functional Integration Tester**
    Focus on: API endpoint contracts, data flow between stages,
    feature-specific E2E flows, cross-stage interactions. Test with
    curl, write new Playwright E2E tests, run pytest.

    ## Available Test Commands
    {embedded test commands from SKILL.md}

    ## Existing Test Infrastructure
    - Playwright config: {{frontend_working_dir}}/{{playwright_config}}
    - E2E directory: {{frontend_working_dir}}/{{e2e_dir}}/
    - Backend tests: tests/unit/, tests/integration/, tests/e2e/
    - Test credentials: {{test_email}} / {{test_password}}

    ## Your Task
    1. Verify the environment is running (backend + frontend)
    2. Test API endpoints with curl (auth, relevant endpoints)
    3. Write and run E2E Playwright tests for key user flows
    4. Run relevant pytest tests
    5. Test cross-stage data flow
    6. Report all findings with evidence
    7. Save your report as: {output_dir}/qa-report-B.md

    ## QA Report Format
    {embedded qa-report-format.md}

    ## QA Tester Role
    {embedded qa-tester.md}

    ## When Done
    - Claim task #2 and mark it completed
    - Send your key findings summary to the team lead
    - Then WAIT for further instructions (do not exit)
  `,
  run_in_background: true
})

// Lead waits for both "done" messages (delivered automatically)
```

#### Quality Check (after both reports arrive)

Before proceeding to Round 2, the lead performs a quality check on both reports:

1. **Evidence present**: At least some screenshots, test outputs, or HTTP responses
2. **Tests actually ran**: Not just reading code and speculating
3. **Flaky protocol followed**: Failing tests run multiple times
4. **Coverage documented**: Clear table of what was/wasn't tested

If a report fails the quality check, message the tester with specific feedback and ask them to run actual tests.

#### Convergence Check

After both reports pass quality checks:

- **Both find zero issues?** Skip Rounds 2-3, spawn C for lightweight spot-check review, produce `issues.md` (clean bill of health) + `test-plan.md` directly.
- **Issues found?** Proceed to Round 2.

#### Interactive Checkpoint 1 (if `--interactive` mode)

Pause here and present both QA reports to the user for review.

### Round 2: Synthesis (C cross-verifies A + B)

```
Agent({
  team_name: "adversarial-qa",
  name: "qa-synthesizer-c",
  subagent_type: "general-purpose",
  prompt: `
    You are a QA synthesizer. Two independent testers have tested the
    same application and produced QA reports. Your job is to cross-verify
    their findings and produce a unified assessment.

    IMPORTANT: Before reading the reports, run your own spot-checks first.
    This prevents anchoring bias.

    ## Test Scope
    {embedded test scope}

    ## Source Files
    {list of relevant files}

    ## Available Test Commands
    {embedded test commands}

    ## Your Task
    1. Run your own spot-checks FIRST (before reading reports)
       - Take a screenshot of the main application page (first smoke page)
       - Hit 2-3 API endpoints
       - Run {{e2e_command}} for baseline
    2. Read {output_dir}/qa-report-A.md
    3. Read {output_dir}/qa-report-B.md
    4. Cross-verify: re-run failing tests reported by either tester
    5. Merge per-tester manifests into {output_dir}/manifest.yaml
       (renumber F-A01→F-001, F-B01→F-003, etc.)
    6. Produce FOUR files:

       a) {output_dir}/qa-synthesis.md -- Full synthesis with cross-verification.
          This is for the lead only and will NOT be shown to A or B.

       b) {output_dir}/review-for-A.md -- Feedback for QA Tester A.
          CRITICAL: Write as if QA Tester B does not exist. Do NOT
          mention B or reference B's findings.

       c) {output_dir}/review-for-B.md -- Feedback for QA Tester B.
          CRITICAL: Write as if QA Tester A does not exist.

    ## QA Synthesizer Role
    {embedded qa-synthesizer.md}

    ## When Done
    - Claim task #3 and mark it completed
    - Send summary to the team lead
    - Then WAIT for further instructions (do not exit)
  `,
  run_in_background: true
})
```

#### Interactive Checkpoint 2 (if `--interactive` mode)

Pause here and present `qa-synthesis.md` to the user for review.

### Round 3: Feedback (Lead messages A and B)

```
// Message QA Tester A (still alive from Round 1)
SendMessage({
  type: "message",
  recipient: "qa-tester-a",
  content: `
    A QA reviewer has assessed your testing report and cross-verified
    your findings.

    Read {output_dir}/review-for-A.md -- it contains their assessment
    of your work, which findings they confirmed, and which they couldn't
    reproduce.

    ISOLATION RULE: Read ONLY review-for-A.md. Do NOT read qa-synthesis.md,
    review-for-B.md, qa-report-B.md, or any other files in the output
    directory. Only review-for-A.md and your own qa-report-A.md.

    For findings the reviewer couldn't reproduce:
    - Re-run the test and provide fresh evidence
    - If you still can't reproduce it either, say so honestly

    For gaps the reviewer identified:
    - Run those tests now and report the results

    {embedded qa-critic.md}

    Send your complete feedback to the team lead when done.
  `,
  summary: "Review feedback for QA Tester A"
})

// Message QA Tester B (still alive from Round 1)
SendMessage({
  type: "message",
  recipient: "qa-tester-b",
  content: `
    A QA reviewer has assessed your testing report and cross-verified
    your findings.

    Read {output_dir}/review-for-B.md -- it contains their assessment
    of your work, which findings they confirmed, and which they couldn't
    reproduce.

    ISOLATION RULE: Read ONLY review-for-B.md. Do NOT read qa-synthesis.md,
    review-for-A.md, qa-report-A.md, or any other files in the output
    directory. Only review-for-B.md and your own qa-report-B.md.

    For findings the reviewer couldn't reproduce:
    - Re-run the test and provide fresh evidence
    - If you still can't reproduce it either, say so honestly

    For gaps the reviewer identified:
    - Run those tests now and report the results

    {embedded qa-critic.md}

    Send your complete feedback to the team lead when done.
  `,
  summary: "Review feedback for QA Tester B"
})

// Lead waits for feedback from both A and B
// Lead marks task #4 complete when both respond
```

### Round 4: Reconciliation (Lead forwards feedback to C)

```
SendMessage({
  type: "message",
  recipient: "qa-synthesizer-c",
  content: `
    Both QA testers reviewed your assessment and responded.
    Reconcile their feedback and produce the final QA deliverables.

    ## Feedback from QA Tester A:
    {paste A's feedback}

    ## Feedback from QA Tester B:
    {paste B's feedback}

    ## QA Reconciler Role
    {embedded qa-reconciler.md}

    ## Your Task
    1. Review each piece of feedback honestly
    2. Incorporate valid defenses (re-promote issues that were defended with evidence)
    3. Remove issues that testers conceded were false positives
    4. Include any new findings from the feedback round
    5. Produce THREE final files:
       - {output_dir}/manifest.yaml -- Updated merged evidence manifest
       - {output_dir}/issues.md -- Ranked issues report
       - {output_dir}/test-plan.md -- Test cases to add for missing coverage

    The issues.md should be grouped by severity and each issue should have
    a verification status (CONFIRMED / CANNOT_REPRODUCE / FLAKY / ENVIRONMENT_SPECIFIC).

    The test-plan.md should categorize tests as:
    - Run existing (already have tests)
    - Extend existing (add cases to existing files)
    - Create new (new test files needed)
    Include skeleton code for new tests.
  `,
  summary: "Reconcile feedback into final QA report"
})

// Lead waits for C's "done" message
// Lead marks task #5 complete
```

### Round 5: Cleanup

```
// Shutdown all teammates
SendMessage({ type: "shutdown_request", recipient: "qa-tester-a", content: "QA complete" })
SendMessage({ type: "shutdown_request", recipient: "qa-tester-b", content: "QA complete" })
SendMessage({ type: "shutdown_request", recipient: "qa-synthesizer-c", content: "QA complete" })

// Wait for shutdown approvals...

// Cleanup team
TeamDelete()

// Present issues.md + test-plan.md to user
```

---

## Task Dependency Chain

```
#1 [QA Report A]  --+
                     +--> #3 [Synthesis C] --> #4 [Feedback] --> #5 [Final Report]
#2 [QA Report B]  --+
```

---

## Error Handling

### Teammate Timeout / Exit Recovery

| Round | Failure | Recovery |
|-------|---------|----------|
| Round 1 | One tester exits/times out | Wait for the other. C reviews single report for gaps. |
| Round 1 | Both fail | Abort. Report to user. |
| Round 2 | Synthesizer exits/times out | Re-spawn C. If second attempt fails, present raw reports. |
| Round 3 | One tester fails to respond | Proceed to Round 4 with available feedback. |
| Round 3 | Both fail | C produces final report from synthesis alone. |
| Round 4 | Synthesizer fails | Re-spawn reconciler. If fails, present qa-synthesis.md. |

### Test Infrastructure Failures

| Failure | Recovery |
|---------|----------|
| Backend not running | Tester starts it in background |
| Frontend not running | Tester starts it in background |
| Playwright not installed | Run `npx playwright install chromium` |
| Auth setup missing | Run auth setup first |
| Database unreachable | Report as critical environment issue |

---

## Output Directory

```
{project}/.dev/qa/{issue-slug}/
|-- test-scope.md
|-- qa-report-A.md
|-- qa-report-B.md
|-- qa-synthesis.md           <- Lead-only (not shown to A or B)
|-- review-for-A.md           <- Sent to A in Round 3 (no mention of B)
|-- review-for-B.md           <- Sent to B in Round 3 (no mention of A)
|-- manifest.yaml             <- Merged evidence manifest (machine-readable)
|-- issues.md                 <- Final deliverable
|-- test-plan.md              <- Final deliverable
|-- verification-report.yaml  <- From adversarial-verify (Step 7)
+-- evidence/
    |-- screenshots/          <- All QA screenshots (named by ID)
    |-- api-responses/        <- Saved API response bodies
    |-- test-results/         <- Test command output
    |-- verify-cycle-1/       <- Verification re-runs + pixel diffs
    |   |-- screenshots/
    |   +-- diffs/
    +-- verify-cycle-2/       <- If second cycle runs
        |-- screenshots/
        +-- diffs/
```

---

## Modes

| Mode | Behavior |
|------|----------|
| **Default** | Fully automated. Lead orchestrates all rounds without pausing. |
| **Interactive** (`--interactive`) | Pauses after Round 1 and Round 2 for user review. |
