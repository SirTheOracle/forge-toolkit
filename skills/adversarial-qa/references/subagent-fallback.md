# Subagent Fallback (When Agent Teams Not Available)

If the TeammateTool (TeamCreate, SendMessage) is not available on your plan, the adversarial QA framework can still run using the Agent tool with file-based coordination.

## Key Differences from Agent Teams

| Aspect | Agent Teams | Subagent Fallback |
|--------|------------|-------------------|
| Sessions persist | Yes -- A, B, C stay alive | No -- each round spawns fresh agents |
| Round 3 context | A and B retain Round 1 testing context | A and B re-read their report + notes from disk |
| Communication | SendMessage (inbox) | Files on disk |
| Completion detection | Inbox messages | Poll for file existence |
| Isolation | Structural (separate context windows) | Even stronger (fully separate processes) |
| Re-running tests | Can re-run in Round 3 with full context | Re-runs in Round 3 without original context |

## Workflow

### Round 0: Setup

```
// Detect input mode and identify test scope
// Create output directory and save test-scope.md
// No team creation needed
```

### Round 1: Parallel Testing

```
// Spawn QA Tester A (background)
Agent({
  subagent_type: "general-purpose",
  prompt: "{test scope + source files + qa-tester role + qa-report format
            + Strategy A (UI Regression) assignment
            + available test commands
            + save qa-report as qa-report-A.md
            + save testing notes as testing-notes-A.md
            + isolation rule}",
  run_in_background: true
})

// Spawn QA Tester B (background, same turn for parallel)
Agent({
  subagent_type: "general-purpose",
  prompt: "{same structure, Strategy B (Functional Integration),
            save as qa-report-B.md + testing-notes-B.md
            + isolation rule}",
  run_in_background: true
})

// Poll for completion: watch for qa-report-A.md and qa-report-B.md
```

**Testing notes** (`testing-notes-A.md`, `testing-notes-B.md`): Since subagent sessions don't persist, each tester must also write a testing notes file containing:
- Full reasoning trail (what they tested and why)
- Environment observations (anything unusual noticed)
- Test commands that were run and their exact output
- Hypotheses about findings
- Open questions

These notes are read back in Round 3 to compensate for the loss of Round 1 context.

#### Quality Check

Same as agent-teams-workflow: check for real evidence, actual test runs, flaky protocol.

#### Convergence Check

Same: if both find zero issues, skip to lightweight review.

### Round 2: Synthesis

```
Agent({
  subagent_type: "general-purpose",
  prompt: "
    IMPORTANT: Before reading the reports, run your own spot-checks first.
    Take a screenshot of the main application page (first entry from {{smoke_pages}}),
    hit 2-3 API endpoints, run {{e2e_command}}.

    Then read {output_dir}/qa-report-A.md and {output_dir}/qa-report-B.md.
    Cross-verify: re-run failing tests reported by either tester.

    {qa-synthesizer role}

    Produce THREE files:
    1. {output_dir}/qa-synthesis.md -- Full synthesis (lead-only)
    2. {output_dir}/review-for-A.md -- Feedback for A only.
       Write as if B does not exist. Zero references to B.
    3. {output_dir}/review-for-B.md -- Feedback for B only.
       Write as if A does not exist. Zero references to A.

    Available test commands: {embedded}
    Test scope: {embedded}
  "
})
```

### Round 3: Feedback

```
// Spawn two feedback agents in parallel
Agent({
  subagent_type: "general-purpose",
  prompt: "Read {output_dir}/qa-report-A.md (this was YOUR QA report).
           Read {output_dir}/testing-notes-A.md (these are YOUR testing notes).
           Now read {output_dir}/review-for-A.md (a QA reviewer's assessment).

           ISOLATION RULE: Do NOT read qa-report-B.md, qa-synthesis.md,
           review-for-B.md, testing-notes-B.md, issues.md, test-plan.md,
           or any other files in the output directory.

           {qa-critic role}

           For findings the reviewer couldn't reproduce:
           - Re-run the test and provide fresh evidence
           - Use the same commands from your testing notes

           Available test commands: {embedded}

           Save your feedback as {output_dir}/feedback-A.md",
  run_in_background: true
})

Agent({
  subagent_type: "general-purpose",
  prompt: "Read {output_dir}/qa-report-B.md (this was YOUR QA report).
           Read {output_dir}/testing-notes-B.md (these are YOUR testing notes).
           Now read {output_dir}/review-for-B.md (a QA reviewer's assessment).

           ISOLATION RULE: Do NOT read qa-report-A.md, qa-synthesis.md,
           review-for-A.md, testing-notes-A.md, issues.md, test-plan.md,
           or any other files in the output directory.

           {qa-critic role}

           For findings the reviewer couldn't reproduce:
           - Re-run the test and provide fresh evidence

           Available test commands: {embedded}

           Save your feedback as {output_dir}/feedback-B.md",
  run_in_background: true
})
```

### Round 4: Reconciliation

```
Agent({
  subagent_type: "general-purpose",
  prompt: "Read {output_dir}/qa-synthesis.md (this was YOUR synthesis).
           Read {output_dir}/feedback-A.md and {output_dir}/feedback-B.md.

           {qa-reconciler role}

           Save as:
           - {output_dir}/issues.md (ranked issues report)
           - {output_dir}/test-plan.md (test cases to add)"
})
```

## Polling for File Completion

Since subagents can't send messages, the lead polls for files:

```bash
# In a loop with sleep:
while [ ! -f "{output_dir}/qa-report-A.md" ] || [ ! -f "{output_dir}/qa-report-B.md" ]; do
  sleep 10
done
```

## Error Handling

Same recovery logic as agent-teams-workflow, adapted for subagents:

| Round | Failure | Recovery |
|-------|---------|----------|
| Round 1 | One agent fails | Wait for the other. C reviews single report. |
| Round 1 | Both fail | Abort. Report to user. |
| Round 2 | Synthesizer fails | Re-spawn. If fails again, present raw reports. |
| Round 3 | One feedback agent fails | Proceed to Round 4 with available feedback. |
| Round 3 | Both fail | Proceed to Round 4 with no feedback. |
| Round 4 | Reconciler fails | Re-spawn. If fails again, present qa-synthesis.md. |

## Output Directory

```
{project}/.dev/qa/{issue-slug}/
|-- test-scope.md
|-- qa-report-A.md
|-- qa-report-B.md
|-- testing-notes-A.md         <- Subagent fallback only
|-- testing-notes-B.md         <- Subagent fallback only
|-- qa-synthesis.md
|-- review-for-A.md
|-- review-for-B.md
|-- feedback-A.md              <- Subagent fallback only
|-- feedback-B.md              <- Subagent fallback only
|-- issues.md                  <- Final deliverable
+-- test-plan.md               <- Final deliverable
```

## When to Use This Fallback

- Agent Teams is not available on your plan
- You see errors related to TeamCreate or SendMessage
- You want even stronger isolation (agents are fully separate processes)
