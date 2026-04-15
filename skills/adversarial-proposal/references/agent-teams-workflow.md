# Adversarial Proposal Framework — Agent Teams Workflow Design

## Overview

This document describes how the adversarial proposal framework runs as a Claude Code Agent Teams workflow, fully automated from a single prompt. The user triggers the skill, and the team lead orchestrates the entire 4-round process.

## Plan Compatibility

**Agent Teams** requires the TeammateTool (spawnTeam, SendMessage, etc.) which may be gated behind Enterprise/Team plans. Check your plan before proceeding.

- **If Agent Teams is available**: Use the primary workflow below
- **If Agent Teams is NOT available**: Use the subagent fallback (see `references/subagent-fallback.md`)

---

## Architecture

```
YOU (trigger the skill with a problem statement)
  │
  ▼
TEAM LEAD (orchestrator — your main Claude Code session)
  │
  ├── Detects problem type, selects strategy pair
  │
  ├── Spawns ──► PROPOSER-A (teammate, background, Strategy A)
  │                 │
  │                 ├── Reads source files
  │                 ├── Investigates using assigned strategy
  │                 ├── Writes proposal-A.md to disk
  │                 └── Sends "done" message to lead
  │
  ├── Spawns ──► PROPOSER-B (teammate, background, IN PARALLEL, Strategy B)
  │                 │
  │                 ├── Reads source files
  │                 ├── Investigates using assigned strategy
  │                 ├── Writes proposal-B.md to disk
  │                 └── Sends "done" message to lead
  │
  │  ◄── Lead waits for both "done" messages ──►
  │  ◄── Quality check: required sections, code refs, confidence annotations ──►
  │  ◄── Convergence check: if same finding + same approach → skip to lightweight review ──►
  │  ◄── [Interactive mode: Checkpoint 1 — user reviews proposals] ──►
  │
  ├── Spawns ──► SYNTHESIZER-C (teammate, background)
  │                 │
  │                 ├── Independently examines source files (Phase 0)
  │                 ├── Reads proposal-A.md + proposal-B.md
  │                 ├── Reviews both, states disagreements
  │                 ├── Writes proposal-C.md (lead-only)
  │                 ├── Writes review-for-A.md (A's eyes only, no mention of B)
  │                 ├── Writes review-for-B.md (B's eyes only, no mention of A)
  │                 └── Sends "done" message to lead
  │
  │  ◄── Lead waits for C "done" ──►
  │  ◄── [Interactive mode: Checkpoint 2 — user reviews synthesis] ──►
  │
  ├── Messages ──► PROPOSER-A (still alive)
  │                 │
  │                 ├── "Read review-for-A.md and give feedback"
  │                 ├── CANNOT read: proposal-C.md, review-for-B.md, proposal-B.md
  │                 ├── Reviews from its original context
  │                 └── Sends feedback to lead
  │
  ├── Messages ──► PROPOSER-B (still alive)
  │                 │
  │                 ├── "Read review-for-B.md and give feedback"
  │                 ├── CANNOT read: proposal-C.md, review-for-A.md, proposal-A.md
  │                 ├── Reviews from its original context
  │                 └── Sends feedback to lead
  │
  │  ◄── Lead waits for both feedback messages ──►
  │
  ├── Messages ──► SYNTHESIZER-C (still alive)
  │                 │
  │                 ├── Lead forwards feedback from A and B
  │                 ├── C reconciles feedback with its proposal
  │                 ├── Writes final-plan.md to disk
  │                 └── Sends "done" message to lead
  │
  │  ◄── Lead presents final-plan.md to user ──►
  │
  └── Shutdown all teammates, cleanup team
```

---

## Why This Works for Isolation

The critical requirement: **A and B must not see each other's work during Round 1, and must not be exposed to each other's ideas during Round 3.**

Agent Teams naturally provides this:

1. **Each teammate has its own context window** — A's context contains only the problem statement and source files. B's context contains only the problem statement and source files. They share no conversation history.
2. **Communication is only via SendMessage** — A and B have no way to see each other's work unless someone sends it to them. The lead controls all message routing.
3. **File isolation by convention** — A writes `proposal-A.md`, B writes `proposal-B.md`. Neither is instructed to read the other's file. Their prompts explicitly say not to look at other proposals.
4. **Round 3 isolation via review files** — In Round 3, A reads only `review-for-A.md` (which contains zero references to B), and B reads only `review-for-B.md` (which contains zero references to A). Neither reads `proposal-C.md` which contains the full comparative analysis.
5. **The lead enforces the protocol** — The lead never sends A's findings to B or vice versa. It routes only the appropriate review file to each proposer.

### Potential Leak Points (and mitigations)

| Leak Risk | Mitigation |
|-----------|-----------|
| A reads proposal-B.md from disk | Prompt explicitly says "Do NOT read any other proposal files" |
| A reads proposal-C.md in Round 3 | Prompt says to read ONLY review-for-A.md; proposal-C.md contains B's ideas |
| A reads review-for-B.md | Prompt explicitly prohibits reading any file not addressed to them |
| A and B message each other | They have no reason to — their prompts don't mention each other |
| Lead accidentally forwards A's work to B | Lead prompt explicitly prohibits this during Round 1 |
| Shared project context (CLAUDE.md etc.) | Fine — both should see the same codebase context |

---

## Detailed Tool Call Sequence

### Round 0: Setup

```
// Lead creates the team
Teammate({
  operation: "spawnTeam",
  team_name: "adversarial-proposal",
  description: "Adversarial proposal investigation for: {problem_summary}"
})

// Detect problem type and select strategy pair
// Based on problem statement, classify as: bug_fix | feature | architecture | refactoring
// This determines which strategy pair from agents/proposer.md to assign

// Note: If invoked with --interactive flag, the lead will pause for user
// review after Rounds 1 and 2 before proceeding

// Lead creates the task list for tracking
TaskCreate({
  subject: "Proposal A: Independent investigation",
  description: "Investigate the problem independently and write proposal-A.md",
  activeForm: "Proposer A investigating..."
})

TaskCreate({
  subject: "Proposal B: Independent investigation",
  description: "Investigate the problem independently and write proposal-B.md",
  activeForm: "Proposer B investigating..."
})

TaskCreate({
  subject: "Proposal C: Synthesis",
  description: "Review proposals A and B, synthesize proposal-C.md + review files",
  activeForm: "Synthesizing proposals..."
})
// Task 3 blocked by 1 and 2
TaskUpdate({ taskId: "3", addBlockedBy: ["1", "2"] })

TaskCreate({
  subject: "Feedback from A and B on C",
  description: "A reads review-for-A.md and B reads review-for-B.md, give feedback",
  activeForm: "Collecting feedback..."
})
TaskUpdate({ taskId: "4", addBlockedBy: ["3"] })

TaskCreate({
  subject: "Final plan: Reconciliation",
  description: "C reconciles feedback into final-plan.md",
  activeForm: "Reconciling final plan..."
})
TaskUpdate({ taskId: "5", addBlockedBy: ["4"] })

// Lead saves problem-statement.md
// (writes the file to {project}/.dev/proposals/{slug}/problem-statement.md)
```

### Round 1: Independent Investigation (A and B in parallel)

```
// Determine strategy assignments based on problem type:
//   bug_fix:      A = "Trace FORWARD from entry point"
//                 B = "Trace BACKWARD from symptom"
//   feature:      A = "MINIMAL viable approach"
//                 B = "ROBUST/extensible approach"
//   architecture: A = "Optimize for SIMPLICITY"
//                 B = "Optimize for SCALABILITY"
//   refactoring:  A = "INCREMENTAL migration"
//                 B = "CLEAN-BREAK rewrite"

// Spawn Proposer A
Task({
  team_name: "adversarial-proposal",
  name: "proposer-a",
  subagent_type: "general-purpose",
  prompt: `
    You are an independent investigator. Your job is to analyze a technical
    problem, understand its core, and write a proposal.

    CRITICAL ISOLATION RULE: You are one of two independent investigators.
    You must NOT read any other proposal files in the output directory.
    Do NOT look at any files named proposal-B.md, proposal-C.md,
    review-for-A.md, review-for-B.md, or final-plan.md. Only read the
    source files listed below and the problem statement.

    ## Problem Statement
    {embedded problem statement}

    ## Source Files to Examine
    {list of file paths}

    ## Your Investigation Strategy
    You have been assigned: **{strategy_A_description}**
    {brief explanation of what this strategy means — from proposer.md table}

    ## Your Task
    1. Read and analyze the source files listed above
    2. Investigate the problem using your assigned strategy
    3. Identify the core issue with specific evidence
    4. Propose a solution with step-by-step implementation plan
    5. Annotate key claims with [HIGH], [MEDIUM], or [LOW] confidence
    6. Save your proposal as: {output_dir}/proposal-A.md

    ## Proposal Format
    {embedded proposal format from references/proposal-format.md}

    ## When Done
    - Claim task #1 and mark it completed
    - Send your key findings summary to team-lead via:
      Teammate({ operation: "write", target_agent_id: "team-lead",
        value: "Proposal A complete. Saved to proposal-A.md." })
    - Then WAIT for further instructions (do not exit)
  `,
  run_in_background: true
})

// Spawn Proposer B — IN THE SAME TURN for true parallel execution
Task({
  team_name: "adversarial-proposal",
  name: "proposer-b",
  subagent_type: "general-purpose",
  prompt: `
    You are an independent investigator. Your job is to analyze a technical
    problem, understand its core, and write a proposal.

    CRITICAL ISOLATION RULE: You are one of two independent investigators.
    You must NOT read any other proposal files in the output directory.
    Do NOT look at any files named proposal-A.md, proposal-C.md,
    review-for-A.md, review-for-B.md, or final-plan.md. Only read the
    source files listed below and the problem statement.

    ## Problem Statement
    {embedded problem statement}

    ## Source Files to Examine
    {list of file paths}

    ## Your Investigation Strategy
    You have been assigned: **{strategy_B_description}**
    {brief explanation of what this strategy means — from proposer.md table}

    ## Your Task
    1. Read and analyze the source files listed above
    2. Investigate the problem using your assigned strategy
    3. Identify the core issue with specific evidence
    4. Propose a solution with step-by-step implementation plan
    5. Annotate key claims with [HIGH], [MEDIUM], or [LOW] confidence
    6. Save your proposal as: {output_dir}/proposal-B.md

    ## Proposal Format
    {embedded proposal format from references/proposal-format.md}

    ## When Done
    - Claim task #2 and mark it completed
    - Send your key findings summary to team-lead via:
      Teammate({ operation: "write", target_agent_id: "team-lead",
        value: "Proposal B complete. Saved to proposal-B.md." })
    - Then WAIT for further instructions (do not exit)
  `,
  run_in_background: true
})

// Lead monitors inbox for both "done" messages
// Checks: cat ~/.claude/teams/adversarial-proposal/inboxes/team-lead.json
```

#### Quality Check (after both proposals arrive)

Before proceeding to Round 2, the lead performs a structural quality check on both proposals:

1. **Required sections present**: Problem Statement, Investigation Findings, Core Analysis, Solution Plan, Risk Assessment, Testing Strategy
2. **Specific code references**: At least some file paths, function names, or line ranges (not just abstract reasoning)
3. **Confidence annotations**: At least one `[HIGH]`, `[MEDIUM]`, or `[LOW]` tag in the proposal

If a proposal fails the quality check, message the proposer with specific feedback on what's missing and ask them to revise before proceeding.

#### Convergence Check

After both proposals pass quality checks, the lead compares them:

- **Same core finding AND same approach?** If both proposals identify the same root cause and propose essentially the same solution, the full adversarial process adds little value. Skip Rounds 2-3 and proceed to a lightweight review: spawn C to review the single approach for missed risks, then produce `final-plan.md` directly.
- **Different findings OR different approaches?** Proceed to Round 2 as normal.

#### Interactive Checkpoint 1 (if `--interactive` mode)

If the skill was invoked with the `--interactive` flag, pause here and present both proposals to the user for review before proceeding to Round 2. The user can:
- Approve and continue
- Provide guidance to steer the synthesis
- Terminate early if one proposal is clearly sufficient

### Round 2: Synthesis (C reviews A + B)

Triggered when lead receives "done" from both A and B (and quality/convergence checks pass).

```
// Spawn Synthesizer C
Task({
  team_name: "adversarial-proposal",
  name: "synthesizer-c",
  subagent_type: "general-purpose",
  prompt: `
    You are a technical reviewer and synthesizer. Two independent investigators
    have analyzed the same problem and produced competing proposals. Your job
    is to review both, identify strengths and weaknesses, and produce a
    superior synthesized proposal.

    IMPORTANT: Before reading the proposals, independently examine the source
    files listed below. Form your own understanding of the problem BEFORE
    reading A or B. This prevents anchoring bias.

    ## Problem Statement
    {embedded problem statement}

    ## Source Files (examine these FIRST, before reading proposals)
    {list of file paths}

    ## Your Task
    1. Read and examine the source files above FIRST
    2. Read {output_dir}/proposal-A.md
    3. Read {output_dir}/proposal-B.md
    4. Analyze each proposal against your own understanding
    5. Produce THREE files:

       a) {output_dir}/proposal-C.md — Your full synthesis with analysis
          of both proposals, attribution, and your recommended approach.
          This file is for the lead only and will NOT be shown to A or B.

       b) {output_dir}/review-for-A.md — Your feedback for Proposer A.
          CRITICAL: Write this as if Proposal B does not exist. Do NOT
          mention B, reference B's ideas, or compare A to B. Present any
          insights from B as your own findings from examining the source.

       c) {output_dir}/review-for-B.md — Your feedback for Proposer B.
          CRITICAL: Write this as if Proposal A does not exist. Same rule.

    ## Synthesizer Role
    {embedded synthesizer role from agents/synthesizer.md}

    ## When Done
    - Claim task #3 and mark it completed
    - Send message to team-lead:
      Teammate({ operation: "write", target_agent_id: "team-lead",
        value: "Synthesis complete. Saved proposal-C.md, review-for-A.md,
               review-for-B.md." })
    - Then WAIT for further instructions (do not exit)
  `,
  run_in_background: true
})
```

#### Interactive Checkpoint 2 (if `--interactive` mode)

If `--interactive`, pause here and present `proposal-C.md` to the user for review before proceeding to Round 3. The user can review the synthesis and provide guidance.

### Round 3: Feedback (Lead messages A and B to review their review files)

Triggered when lead receives "done" from C.

```
// Message Proposer A (still alive from Round 1)
Teammate({
  operation: "write",
  target_agent_id: "proposer-a",
  value: `
    A technical reviewer has examined your proposal and written feedback.

    Read {output_dir}/review-for-A.md — it contains a direct review of
    your work with critiques and an alternative approach on specific points.

    ISOLATION RULE: Read ONLY review-for-A.md. Do NOT read proposal-C.md,
    review-for-B.md, proposal-B.md, or any other files in the output
    directory. Only review-for-A.md and your own proposal-A.md.

    Do a full analysis:
    - Where the reviewer is right and your proposal was wrong or incomplete
    - Where the reviewer is wrong and your proposal was better
    - Specific technical concerns with the reviewer's alternative approach
    - What you'd change based on this feedback

    Send your complete feedback to team-lead when done.
  `
})

// Message Proposer B (still alive from Round 1)
Teammate({
  operation: "write",
  target_agent_id: "proposer-b",
  value: `
    A technical reviewer has examined your proposal and written feedback.

    Read {output_dir}/review-for-B.md — it contains a direct review of
    your work with critiques and an alternative approach on specific points.

    ISOLATION RULE: Read ONLY review-for-B.md. Do NOT read proposal-C.md,
    review-for-A.md, proposal-A.md, or any other files in the output
    directory. Only review-for-B.md and your own proposal-B.md.

    Do a full analysis:
    - Where the reviewer is right and your proposal was wrong or incomplete
    - Where the reviewer is wrong and your proposal was better
    - Specific technical concerns with the reviewer's alternative approach
    - What you'd change based on this feedback

    Send your complete feedback to team-lead when done.
  `
})

// Lead waits for feedback from both A and B
// Lead marks task #4 complete when both respond
```

### Round 4: Reconciliation (Lead forwards feedback to C)

Triggered when lead receives feedback from both A and B.

**Note:** If one proposer fails to respond (timeout, exit), proceed with only the available feedback. One-sided feedback is better than no feedback. Mark which feedback is missing in the message to C.

```
// Forward feedback to Synthesizer C
Teammate({
  operation: "write",
  target_agent_id: "synthesizer-c",
  value: `
    Both original investigators have reviewed your feedback and responded.
    Please reconcile their feedback with your proposal and produce the
    final plan.

    ## Feedback from Proposer A:
    {paste A's feedback from inbox}

    ## Feedback from Proposer B:
    {paste B's feedback from inbox}

    ## Reconciler Role
    {embedded reconciler role from agents/reconciler.md}

    ## Your Task
    1. Review each piece of feedback honestly
    2. Incorporate what's valid
    3. Reject what's not applicable (with reasoning)
    4. Produce the final implementation plan
    5. Save as: {output_dir}/final-plan.md

    The final plan should include:
    - Problem statement (refined)
    - Core finding (final determination with confidence level)
    - Implementation plan (step-by-step)
    - Feedback reconciliation (what was accepted/rejected and why)
    - Risks and mitigations
    - Testing strategy
  `
})

// Lead waits for C's "done" message
// Lead marks task #5 complete
```

### Round 5: Cleanup

```
// Shutdown all teammates
Teammate({ operation: "requestShutdown", target_agent_id: "proposer-a" })
Teammate({ operation: "requestShutdown", target_agent_id: "proposer-b" })
Teammate({ operation: "requestShutdown", target_agent_id: "synthesizer-c" })

// Wait for shutdown approvals...

// Cleanup team
Teammate({ operation: "cleanup" })

// Present final-plan.md to user
```

---

## Task Dependency Chain

```
#1 [Proposal A]  ──┐
                    ├──► #3 [Synthesis C] ──► #4 [Feedback] ──► #5 [Final Plan]
#2 [Proposal B]  ──┘
```

Tasks auto-unblock as dependencies complete. This drives the workflow forward automatically.

---

## Error Handling

### Teammate Timeout / Exit Recovery

| Round | Failure | Recovery |
|-------|---------|----------|
| Round 1 | One proposer exits/times out | Wait for the other. If only one proposal arrives, skip adversarial process — spawn C to review the single proposal for blind spots, then produce final-plan.md |
| Round 1 | Both proposers fail | Abort workflow. Report failure to user. |
| Round 2 | Synthesizer exits/times out | Re-spawn C with same prompt. If second attempt fails, present both raw proposals to user. |
| Round 3 | One proposer fails to respond | Proceed to Round 4 with only available feedback. Note which is missing. |
| Round 3 | Both proposers fail | Proceed to Round 4 with no feedback. C produces final-plan.md based on its synthesis alone. |
| Round 4 | Synthesizer fails | Re-spawn reconciler subagent with C's proposal + feedback files. If second attempt fails, present proposal-C.md as the final output. |

### Quality Gate Failures

If a proposal fails the structural quality check after Round 1:
1. Message the proposer with specific feedback on what's missing
2. Wait for a revised proposal
3. If the revision still fails, proceed with the other proposal only

---

## Modes

| Mode | Behavior |
|------|----------|
| **Default** | Fully automated. Lead orchestrates all rounds without pausing. |
| **Interactive** (`--interactive`) | Pauses after Round 1 (user reviews proposals) and after Round 2 (user reviews synthesis) before continuing. Useful for high-stakes decisions where the user wants to steer the process. |

---

## How to Invoke

### Single Command

In Claude Code, the user says something like:

```
Use the adversarial proposal framework to investigate this bug:
{problem statement}

The relevant files are: {file paths}
```

The skill reads the SKILL.md, sees it should use Agent Teams, and the lead orchestrates the entire workflow.

### As a Claude Code Command

Save as `.claude/commands/adversarial-proposal.md`:

```
Use the adversarial proposal skill at .claude/skills/adversarial-proposal/SKILL.md
to investigate the following problem using the Agent Teams workflow.

Create a 3-teammate adversarial proposal team:
- proposer-a and proposer-b investigate independently (ISOLATED, no sharing)
- synthesizer-c reviews both and produces a synthesis
- A and B then review C's feedback and respond
- C reconciles feedback into a final plan

Problem: $ARGUMENTS

Save all outputs to .dev/proposals/
```

Then invoke with:
```
/adversarial-proposal There is a bug with video prompt generation not including dialogue...
```

For interactive mode:
```
/adversarial-proposal --interactive There is a bug with video prompt generation...
```

---

## Subagent Fallback (If Agent Teams Not Available)

If TeammateTool is not available on your plan, the workflow can still run
using the Task tool with subagents + file-based coordination:

```
Round 1: Two parallel subagents (Task with run_in_background: true)
  - Each writes its proposal to disk
  - Lead polls for file existence to know when done

Round 2: One subagent reads both proposals, writes proposal-C.md +
  review-for-A.md + review-for-B.md

Round 3: Two parallel subagents:
  - A reads proposal-A.md + review-for-A.md, writes feedback to disk
  - B reads proposal-B.md + review-for-B.md, writes feedback to disk

Round 4: One subagent reads C + both feedback files, writes final-plan.md
```

Key differences from Agent Teams version:
- Sessions are NOT persistent — Round 3 subagents don't retain Round 1 context
  (they re-read their proposal from disk instead)
- No inter-agent messaging — everything goes through files on disk
- Lead polls for file completion instead of receiving inbox messages
- Less elegant but functionally equivalent for the isolation requirement

The isolation is actually STRONGER in the subagent version because each
subagent literally has no way to access another's context.

See `references/subagent-fallback.md` for the full tool call sequence.

---

## Output Directory

```
{project}/.dev/proposals/{issue-slug}/
├── problem-statement.md
├── proposal-A.md
├── proposal-B.md
├── proposal-C.md             ← Lead-only (not shown to A or B)
├── review-for-A.md           ← Sent to A in Round 3 (no mention of B)
├── review-for-B.md           ← Sent to B in Round 3 (no mention of A)
└── final-plan.md
```

---

## Token Cost Estimate

Each teammate is a full Claude context window. For a typical investigation:

| Session | Est. Context | Notes |
|---------|-------------|-------|
| Lead | Light | Mostly orchestration, minimal analysis |
| Proposer A | Heavy | Reads all source files, full investigation |
| Proposer B | Heavy | Reads all source files, full investigation |
| Synthesizer C | Heavy | Reads A + B + source files, full analysis |
| **Total** | ~4x single session | Worth it for complex problems where wrong approach = wasted time |

Round 3 feedback adds context to A and B (they read their review file into their existing windows). Round 4 adds feedback to C's window. These are incremental, not new sessions.
