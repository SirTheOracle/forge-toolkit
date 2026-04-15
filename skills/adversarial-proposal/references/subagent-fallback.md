# Subagent Fallback (When Agent Teams Not Available)

If the TeammateTool (spawnTeam, SendMessage) is not available on your plan, the adversarial proposal framework can still run using the Task tool with file-based coordination.

## Key Differences from Agent Teams

| Aspect | Agent Teams | Subagent Fallback |
|--------|------------|-------------------|
| Sessions persist | Yes — A, B, C stay alive | No — each round spawns fresh subagents |
| Round 3 context | A and B retain Round 1 analysis | A and B re-read proposal + investigation notes from disk |
| Communication | SendMessage (inbox) | Files on disk |
| Completion detection | Inbox messages | Poll for file existence |
| Isolation | Structural (separate context windows) | Even stronger (fully separate processes) |
| Investigation trail | Retained in context window | Written to investigation-notes files |

## Workflow

### Round 0: Setup

```
// Detect problem type and select strategy pair
// Based on problem statement, classify as: bug_fix | feature | architecture | refactoring
// This determines which strategy pair from agents/proposer.md to assign

// Note: If invoked with --interactive flag, the lead will pause for user
// review after Rounds 1 and 2 before proceeding

// Create output directory and save problem-statement.md
```

### Round 1: Parallel Investigation

```
// Determine strategy assignments based on problem type (same as agent-teams-workflow)

// Spawn A (background)
Task({
  subagent_type: "general-purpose",
  prompt: "{problem + source files + proposer role + proposal format
            + Strategy A assignment
            + save proposal as proposal-A.md
            + save investigation notes as investigation-notes-A.md
            + isolation rule
            + confidence annotations}",
  run_in_background: true
})

// Spawn B (background, same turn for parallel)
Task({
  subagent_type: "general-purpose",
  prompt: "{same structure, Strategy B assignment,
            save as proposal-B.md + investigation-notes-B.md
            + isolation rule}",
  run_in_background: true
})

// Poll for completion
// Watch for proposal-A.md and proposal-B.md to appear on disk
```

**Investigation notes** (`investigation-notes-A.md`, `investigation-notes-B.md`): Since subagent sessions don't persist, each proposer must also write an investigation notes file containing:
- Full reasoning trail (what they examined and why)
- Rejected hypotheses (what they considered and ruled out, with reasoning)
- Key decision rationale (why they chose their approach over alternatives)
- Open questions (things they weren't sure about)

These notes are read back in Round 3 to compensate for the loss of Round 1 context.

#### Quality Check (after both proposals arrive)

Same as agent-teams-workflow: check required sections, code refs, confidence annotations. If a proposal fails, re-spawn the subagent with specific feedback on what's missing.

#### Convergence Check

Same as agent-teams-workflow: if same core finding AND same approach, skip to lightweight review.

#### Interactive Checkpoint 1 (if `--interactive` mode)

Same as agent-teams-workflow: pause for user review of both proposals.

### Round 2: Synthesis

```
// Wait until both files exist, then:
Task({
  subagent_type: "general-purpose",
  prompt: "
    IMPORTANT: Before reading the proposals, independently examine the
    source files listed below. Form your own understanding FIRST.

    Then read {output_dir}/proposal-A.md and {output_dir}/proposal-B.md.

    {synthesizer role}

    Produce THREE files:
    1. {output_dir}/proposal-C.md — Full synthesis (lead-only, not shown to A or B)
    2. {output_dir}/review-for-A.md — Feedback for A only. Write as if B
       does not exist. Zero references to B.
    3. {output_dir}/review-for-B.md — Feedback for B only. Write as if A
       does not exist. Zero references to A.

    Source files: {list of file paths}
    Problem statement: {embedded}
  "
})
```

#### Interactive Checkpoint 2 (if `--interactive` mode)

Same as agent-teams-workflow: pause for user review of proposal-C.md.

### Round 3: Feedback

```
// Spawn two feedback subagents in parallel
Task({
  subagent_type: "general-purpose",
  prompt: "Read {output_dir}/proposal-A.md (this was YOUR original proposal).
           Read {output_dir}/investigation-notes-A.md (these are YOUR investigation
           notes — use them to restore your original reasoning context).
           Now read {output_dir}/review-for-A.md (a technical reviewer's feedback
           on your work).

           ISOLATION RULE: Do NOT read proposal-B.md, proposal-C.md,
           review-for-B.md, investigation-notes-B.md, or any other files
           in the output directory.

           {critic role}
           Save your feedback as {output_dir}/feedback-A.md",
  run_in_background: true
})

Task({
  subagent_type: "general-purpose",
  prompt: "Read {output_dir}/proposal-B.md (this was YOUR original proposal).
           Read {output_dir}/investigation-notes-B.md (these are YOUR investigation
           notes — use them to restore your original reasoning context).
           Now read {output_dir}/review-for-B.md (a technical reviewer's feedback
           on your work).

           ISOLATION RULE: Do NOT read proposal-A.md, proposal-C.md,
           review-for-A.md, investigation-notes-A.md, or any other files
           in the output directory.

           {critic role}
           Save your feedback as {output_dir}/feedback-B.md",
  run_in_background: true
})
```

**Note:** These subagents don't have the original investigation context — they read their proposal + investigation notes from disk. The investigation notes compensate for the missing Round 1 context window, making feedback more grounded than re-reading the proposal alone.

### Round 4: Reconciliation

```
// If one feedback file is missing (subagent failed), proceed with available feedback
Task({
  subagent_type: "general-purpose",
  prompt: "Read {output_dir}/proposal-C.md (this was YOUR proposal).
           Read {output_dir}/feedback-A.md and {output_dir}/feedback-B.md.
           {reconciler role}
           Save as {output_dir}/final-plan.md"
})
```

## Polling for File Completion

Since subagents can't send messages, the lead polls for files:

```bash
# In a loop with sleep:
while [ ! -f "{output_dir}/proposal-A.md" ] || [ ! -f "{output_dir}/proposal-B.md" ]; do
  sleep 10
done
```

## Error Handling

Same recovery logic as agent-teams-workflow, adapted for subagents:

| Round | Failure | Recovery |
|-------|---------|----------|
| Round 1 | One subagent fails | Wait for the other. Skip adversarial process if only one arrives. |
| Round 1 | Both fail | Abort workflow. Report to user. |
| Round 2 | Synthesizer fails | Re-spawn with same prompt. If second attempt fails, present raw proposals. |
| Round 3 | One feedback subagent fails | Proceed to Round 4 with available feedback. |
| Round 3 | Both fail | Proceed to Round 4 with no feedback. |
| Round 4 | Reconciler fails | Re-spawn. If second attempt fails, present proposal-C.md as output. |

## Output Directory

```
{project}/.dev/proposals/{issue-slug}/
├── problem-statement.md
├── proposal-A.md
├── proposal-B.md
├── investigation-notes-A.md   ← Subagent fallback only: full reasoning trail
├── investigation-notes-B.md   ← Subagent fallback only: full reasoning trail
├── proposal-C.md              ← Lead-only (not shown to A or B)
├── review-for-A.md            ← Sent to A in Round 3 (no mention of B)
├── review-for-B.md            ← Sent to B in Round 3 (no mention of A)
├── feedback-A.md              ← Subagent fallback only: A's feedback on disk
├── feedback-B.md              ← Subagent fallback only: B's feedback on disk
└── final-plan.md
```

## Modes

| Mode | Behavior |
|------|----------|
| **Default** | Fully automated. Lead orchestrates all rounds without pausing. |
| **Interactive** (`--interactive`) | Pauses after Round 1 and Round 2 for user review. |

## When to Use This Fallback

- Agent Teams is not available on your plan
- You see the error: "The 'Agent Teams' feature (TeammateTool, SendMessage, spawnTeam) is not available on this plan"
- You want even stronger isolation (subagents are fully separate processes with zero communication)
