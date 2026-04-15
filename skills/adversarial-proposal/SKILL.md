---
name: adversarial-proposal
description: >
  Automated adversarial proposal framework using Claude Code Agent Teams. Spawns 3 persistent teammates
  (Proposer A, Proposer B, Synthesizer C) to investigate bugs, plan features, or solve complex technical
  problems. A and B investigate the SAME problem independently with ZERO knowledge of each other — true
  isolation enforced by separate context windows. C reviews both and synthesizes. A and B then critique C.
  C reconciles into a final plan. The entire workflow runs automatically from a single prompt.
  Use this skill whenever the user wants to investigate a bug fix, plan a feature, create an implementation
  strategy, or says things like "propose a fix", "investigate this issue", "come up with a plan",
  "adversarial review", "multi-proposal", or "debate approach". Also trigger when the user wants thorough
  analysis before implementation — this prevents tunnel vision by forcing truly independent perspectives.
---

# Adversarial Proposal Framework

## Overview

This skill orchestrates an adversarial investigation workflow using Claude Code Agent Teams. Three persistent teammates investigate a problem through 4 rounds of independent analysis, synthesis, critique, and reconciliation — all automated from a single user prompt.

The critical design principle is **information isolation**: Proposer A and Proposer B each have their own context window and analyze the problem with zero knowledge of each other. Only after both finish does Synthesizer C see both proposals. Then A and B push back on C from their original perspectives — but each sees only their own isolated review file, never the full synthesis or the other's ideas. C reconciles everything into a final plan.

## When to Use

- Bug investigation and fix planning
- Feature implementation planning
- Architecture decisions
- Refactoring strategies
- Any technical problem where you want high confidence before implementing

## Prerequisites

This skill requires **Agent Teams** (TeammateTool, SendMessage). Enable with:

```bash
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

If Agent Teams is not available on your plan, see `references/subagent-fallback.md` for an alternative using the Task tool with file-based coordination.

## Modes

| Mode | Flag | Behavior |
|------|------|----------|
| **Default** | (none) | Fully automated. Lead orchestrates all rounds without pausing. |
| **Interactive** | `--interactive` | Pauses after Round 1 (user reviews proposals) and after Round 2 (user reviews synthesis). Useful for high-stakes decisions where the user wants to steer the process. |

## Architecture

```
ROUND 0: Setup
─────────────────────────────────────────────────
  Lead detects problem type → selects strategy pair
  ┌──────────────────────────────────────────────┐
  │ bug_fix:      A=Forward trace, B=Backward    │
  │ feature:      A=Minimal viable, B=Robust     │
  │ architecture: A=Simplicity, B=Scalability    │
  │ refactoring:  A=Incremental, B=Clean-break   │
  └──────────────────────────────────────────────┘

ROUND 1: Independent Investigation (parallel)
─────────────────────────────────────────────────
  Proposer A (Strategy A)       Proposer B (Strategy B)
  ┌──────────────────┐          ┌──────────────────┐
  │ Own context       │          │ Own context       │
  │                   │          │                   │
  │ Sees ONLY:        │          │ Sees ONLY:        │
  │ - Problem stmt    │          │ - Problem stmt    │
  │ - Source files    │          │ - Source files     │
  │ - Strategy A      │          │ - Strategy B      │
  │                   │          │                   │
  │ CANNOT see B ─────┼── ✗ ────┼── CANNOT see A    │
  │                   │          │                   │
  │ → proposal-A.md   │          │ → proposal-B.md   │
  └───────────────────┘          └───────────────────┘

  ◄── Quality check + Convergence check ──►
  ◄── [Interactive: Checkpoint 1] ──►

ROUND 2: Synthesis
─────────────────────────────────────────────────
  Synthesizer C (teammate)
  ┌──────────────────────────────────────┐
  │ Phase 0: Examines source files first │
  │ Phase 1-2: Reads proposal-A + B      │
  │ Phase 3: Synthesizes proposal-C      │
  │ Phase 5: Writes isolated review files│
  │                                      │
  │ → proposal-C.md    (lead-only)       │
  │ → review-for-A.md  (A's eyes only)  │
  │ → review-for-B.md  (B's eyes only)  │
  └──────────────────────────────────────┘

  ◄── [Interactive: Checkpoint 2] ──►

ROUND 3: Feedback (parallel, ISOLATED)
─────────────────────────────────────────────────
  Proposer A (still alive)       Proposer B (still alive)
  ┌──────────────────┐           ┌──────────────────┐
  │ Reads ONLY:       │           │ Reads ONLY:       │
  │ - review-for-A.md │           │ - review-for-B.md │
  │ + own proposal    │           │ + own proposal     │
  │                   │           │                   │
  │ CANNOT see:       │           │ CANNOT see:       │
  │ - proposal-C.md   │           │ - proposal-C.md   │
  │ - review-for-B.md │           │ - review-for-A.md │
  │ - proposal-B.md   │           │ - proposal-A.md   │
  │                   │           │                   │
  │ → feedback to lead │           │ → feedback to lead │
  └──────────────────┘           └──────────────────┘

ROUND 4: Reconciliation
─────────────────────────────────────────────────
  Synthesizer C (still alive)
  ┌──────────────────────────────────────┐
  │ Receives feedback from A and B       │
  │ Reconciles into final plan           │
  │ → final-plan.md                      │
  └──────────────────────────────────────┘
```

### Why Isolation Works

Each teammate has its own context window. A and B share no conversation history. The only communication channel is SendMessage through the lead — and the lead never forwards A's work to B or vice versa.

| Round | Who | Can See | Cannot See |
|-------|-----|---------|------------|
| 1 | A | Problem + source files + Strategy A | B's proposal |
| 1 | B | Problem + source files + Strategy B | A's proposal |
| 2 | C | Problem + proposal-A + proposal-B + source files | — |
| 3 | A | review-for-A.md + its own proposal-A.md | proposal-C.md, review-for-B.md, proposal-B.md |
| 3 | B | review-for-B.md + its own proposal-B.md | proposal-C.md, review-for-A.md, proposal-A.md |
| 4 | C | A's feedback + B's feedback + its own prior synthesis | — |

### Why Teammates Stay Alive

All 3 teammates persist across rounds. This is essential because:
- A and B retain their full investigation context from Round 1 when reviewing C's feedback in Round 3, making their responses sharper and more grounded
- C retains its synthesis reasoning from Round 2 when reconciling feedback in Round 4

---

## How to Execute

When the user triggers this skill, you (the lead) orchestrate the entire workflow. Read `references/agent-teams-workflow.md` for the complete tool call sequence. Below is the high-level orchestration.

### Step 0: Gather Context and Detect Problem Type

1. Get the **problem statement** from the user
2. Identify the **relevant source files** — ask the user or search the codebase
3. **Detect problem type**: Classify as `bug_fix`, `feature`, `architecture`, or `refactoring`
4. **Select strategy pair** based on problem type (see table in Architecture diagram above)
5. Create output directory: `{project}/.dev/proposals/{issue-slug}/`
6. Save the problem statement as `problem-statement.md`
7. Read the agent role files to embed in teammate prompts:
   - `agents/proposer.md`
   - `agents/synthesizer.md`
   - `agents/critic.md`
   - `agents/reconciler.md`
   - `references/proposal-format.md`

### Step 1: Create Team and Tasks

```
Teammate({ operation: "spawnTeam", team_name: "adversarial-proposal" })
```

Create 5 tasks with dependencies:

| Task | Subject | Blocked By |
|------|---------|-----------|
| #1 | Proposal A: Independent investigation | — |
| #2 | Proposal B: Independent investigation | — |
| #3 | Proposal C: Synthesis + review files | #1, #2 |
| #4 | Feedback from A and B | #3 |
| #5 | Final plan: Reconciliation | #4 |

### Step 2: Round 1 — Spawn A and B (Parallel)

Spawn both proposers **in the same turn** so they run in parallel:

```
Task({
  team_name: "adversarial-proposal",
  name: "proposer-a",
  subagent_type: "general-purpose",
  prompt: "{problem statement + source files + proposer role + proposal format
            + Strategy A assignment + confidence annotations instruction
            + save as proposal-A.md + ISOLATION RULE + wait for instructions}",
  run_in_background: true
})

Task({
  team_name: "adversarial-proposal",
  name: "proposer-b",
  subagent_type: "general-purpose",
  prompt: "{same prompt but Strategy B assignment + save as proposal-B.md
            + ISOLATION RULE}",
  run_in_background: true
})
```

**CRITICAL — Isolation rule in both prompts:**
> "You are one of two independent investigators. You must NOT read any other proposal files in the output directory. Do NOT look at any files named proposal-B.md, proposal-C.md, review-for-A.md, review-for-B.md, or final-plan.md. You should not look at any other documents in this directory. Only read the source files listed above."

**CRITICAL — Both prompts must instruct the teammate to WAIT after completing their proposal**, not exit. They will be needed again in Round 3.

Monitor inbox for "done" messages from both A and B.

After both arrive: **quality check** (required sections, code refs, confidence annotations) then **convergence check**.

### Step 2.5: Convergence Check

If both proposals identify the **same core finding AND propose the same approach**, the full adversarial process adds limited value. In this case:
1. Skip Rounds 2-3
2. Spawn C for a lightweight review of the converged approach (check for blind spots, missed risks)
3. C writes `final-plan.md` directly

This saves significant token cost when investigators agree.

### Step 3: Round 2 — Spawn C (After A and B Complete)

When both "done" messages arrive and proposals diverge:

```
Task({
  team_name: "adversarial-proposal",
  name: "synthesizer-c",
  subagent_type: "general-purpose",
  prompt: "{instruction to examine source files FIRST (Phase 0)
            + then read proposal-A.md and proposal-B.md
            + synthesizer role
            + produce THREE files: proposal-C.md, review-for-A.md, review-for-B.md
            + isolation rules for review files (no cross-contamination)
            + wait for instructions}",
  run_in_background: true
})
```

Monitor inbox for C's "done" message.

### Step 4: Round 3 — Message A and B with Their Review Files

When C's "done" arrives, message both proposers (they're still alive):

```
Teammate({
  operation: "write",
  target_agent_id: "proposer-a",
  value: "Read review-for-A.md — a technical reviewer's feedback on your work.
          ISOLATION RULE: Read ONLY review-for-A.md. Do NOT read proposal-C.md,
          review-for-B.md, proposal-B.md, or any other files in the output
          directory. Give detailed feedback."
})

Teammate({
  operation: "write",
  target_agent_id: "proposer-b",
  value: "Read review-for-B.md — a technical reviewer's feedback on your work.
          ISOLATION RULE: Read ONLY review-for-B.md. Do NOT read proposal-C.md,
          review-for-A.md, proposal-A.md, or any other files in the output
          directory. Give detailed feedback."
})
```

Monitor inbox for feedback from both A and B.

### Step 5: Round 4 — Forward Feedback to C

When both feedback messages arrive, forward them to C:

```
Teammate({
  operation: "write",
  target_agent_id: "synthesizer-c",
  value: "Both investigators reviewed your feedback and responded. Reconcile
          their feedback and produce the final plan.

          Feedback from Proposer A:
          {A's feedback from inbox}

          Feedback from Proposer B:
          {B's feedback from inbox}

          {reconciler role embedded}

          Save the final plan as {output_dir}/final-plan.md"
})
```

Monitor inbox for C's "done" message.

### Step 6: Cleanup and Present

```
Teammate({ operation: "requestShutdown", target_agent_id: "proposer-a" })
Teammate({ operation: "requestShutdown", target_agent_id: "proposer-b" })
Teammate({ operation: "requestShutdown", target_agent_id: "synthesizer-c" })
// Wait for shutdown approvals
Teammate({ operation: "cleanup" })
```

Present `final-plan.md` to the user. Offer to:
- Show the full deliberation trail (all proposals)
- Explain specific decisions
- Proceed to implementation
- Re-run any round with different parameters

---

## Building the Teammate Prompts

Each teammate prompt must be **self-contained** — embed everything inline since teammates don't inherit the lead's conversation.

### What to Embed in Every Prompt

1. **The full problem statement** — verbatim from the user
2. **Source file paths** — explicit list of every file to examine
3. **The agent role** — full content from the relevant `agents/*.md` file
4. **The proposal format** — full content from `references/proposal-format.md` (for Rounds 1-2)
5. **The output path and filename** — exact save location
6. **The isolation rule** — for A and B in Rounds 1 and 3
7. **The strategy assignment** — for A and B in Round 1
8. **The "wait" instruction** — tell teammates to wait for further messages after completing their task

### Adapting to Problem Type

Tailor the investigation emphasis in the proposer prompts:

| Problem Type | Emphasis |
|-------------|---------|
| Bug fix | Trace data flow, find where output diverges from expected, identify root cause |
| Feature | Requirements analysis, integration points, risk assessment, API design |
| Architecture | Tradeoffs, scalability, maintenance burden, migration path |
| Refactoring | Before/after contracts, migration strategy, rollback plan |

---

## Error Handling

### Teammate Timeout / Exit Recovery

| Round | Failure | Recovery |
|-------|---------|----------|
| Round 1 | One proposer fails | Wait for the other. Skip adversarial process — C reviews single proposal for blind spots. |
| Round 1 | Both fail | Abort workflow. Report to user. |
| Round 2 | Synthesizer fails | Re-spawn C. If second attempt fails, present raw proposals. |
| Round 3 | One proposer fails | Proceed to Round 4 with available feedback. Note which is missing. |
| Round 3 | Both fail | Proceed to Round 4 with no feedback. C produces final-plan.md from synthesis alone. |
| Round 4 | Synthesizer fails | Re-spawn reconciler. If second attempt fails, present proposal-C.md. |

### Quality Gate Failures

If a proposal fails the structural quality check after Round 1, message the proposer with specific feedback and request revision before proceeding.

---

## Known Tradeoffs

**Reconciler bias toward own synthesis**: C naturally favors its own Proposal C when reconciling feedback from A and B. The reconciler role doc includes bias awareness guidance (steelman, perspective test, convergent signal weighting), but this is a soft mitigation — C will still tend to accept feedback that validates its approach more readily than feedback that challenges it. For high-stakes decisions, use `--interactive` mode to review the synthesis yourself before feedback rounds.

---

## Invoking the Skill

### Direct Prompt

In Claude Code:

```
Use the adversarial proposal framework to investigate this bug:

There is a bug with video prompt generation not including dialogue for
shots where characters are talking. The relevant files are in
src/services/prompt_generation/ and the system prompt is
ltx2_video_prompt_system_prompt.md.
```

### As a Claude Code Command

Save as `.claude/commands/adversarial-proposal.md`:

```
Read the adversarial proposal skill at .claude/skills/adversarial-proposal/SKILL.md.
Use the Agent Teams workflow described in references/agent-teams-workflow.md to
investigate the following problem.

Create a 3-teammate team: proposer-a and proposer-b investigate independently
(FULLY ISOLATED — no sharing), synthesizer-c reviews both and produces a
synthesis. Then A and B review C's feedback and respond. C reconciles into a
final plan. Save all outputs to .dev/proposals/.

Problem: $ARGUMENTS
```

Invoke with:
```
/adversarial-proposal There is a bug with video prompt generation...
```

For interactive mode:
```
/adversarial-proposal --interactive There is a bug with video prompt generation...
```

---

## Output Directory

```
{project}/.dev/proposals/{issue-slug}/
├── problem-statement.md      ← Round 0
├── proposal-A.md             ← Round 1: Proposer A (Strategy A)
├── proposal-B.md             ← Round 1: Proposer B (Strategy B)
├── proposal-C.md             ← Round 2: Synthesizer C (lead-only, not shown to A or B)
├── review-for-A.md           ← Round 2: C's review for A (no mention of B)
├── review-for-B.md           ← Round 2: C's review for B (no mention of A)
└── final-plan.md             ← Round 4: Synthesizer C (final deliverable)
```

---

## Reference Files

| File | When to Read | Purpose |
|------|-------------|---------|
| `references/agent-teams-workflow.md` | Before starting orchestration | Complete tool call sequence for all rounds |
| `references/proposal-format.md` | When building proposer prompts | Proposal template to embed in A/B/C prompts |
| `references/subagent-fallback.md` | If Agent Teams not available | Alternative workflow using Task tool + file coordination |
| `agents/proposer.md` | When building A and B prompts | Investigation role, mindset, and strategy table to embed |
| `agents/synthesizer.md` | When building C's Round 2 prompt | Synthesis role with 3-file output instructions to embed |
| `agents/critic.md` | When messaging A and B in Round 3 | Feedback guidance to include in message |
| `agents/reconciler.md` | When messaging C in Round 4 | Reconciliation + bias awareness guidance to include in message |

## Agent Roles Summary

| Agent | File | Used In | Purpose |
|-------|------|---------|---------|
| Proposer | `agents/proposer.md` | Round 1 (A and B) | Independent investigation with assigned strategy |
| Synthesizer | `agents/synthesizer.md` | Round 2 (C) | Independent review, synthesis, isolated review files |
| Critic | `agents/critic.md` | Round 3 (A and B) | Review isolated feedback, give grounded response |
| Reconciler | `agents/reconciler.md` | Round 4 (C) | Bias-aware reconciliation into final plan |
