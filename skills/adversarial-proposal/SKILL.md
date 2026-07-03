---
name: adversarial-proposal
description: >
  Automated adversarial proposal framework using Claude Code Agent Teams. Spawns 3 persistent
  teammates (Proposer A, Proposer B, Synthesizer C) to investigate a technical problem through
  4 rounds of isolated analysis, synthesis, critique, and reconciliation. A and B investigate
  the same problem independently with zero knowledge of each other; C reviews both and
  synthesizes; A and B critique C from their original contexts; C reconciles into a final plan.
  Trigger on: "adversarial proposal", "adversarial review", "multi-proposal", "debate approach",
  or explicit requests to investigate a bug/plan a feature/design an architecture with high
  confidence. For ambiguous phrases like "write a proposal" or "draft a plan", ASK whether the
  user wants the full adversarial flow or a single-pass hand-rolled document before proceeding.
  For small, bounded problems use `adversarial-lite` instead.
---

# Adversarial Proposal Framework

## Overview

Three persistent teammates investigate a technical problem through 4 rounds. The critical design principle is **information isolation** — A and B each have their own context window and never see each other's work. Only Synthesizer C sees both. A and B critique C from their original contexts (in Round 3 they see only their own isolated review file, never C's full synthesis or the other proposer's ideas). C reconciles into a final plan.

The full tool-call sequence lives in `references/agent-teams-workflow.md`. **Read that file before orchestrating.** This document is the high-level guide.

## When to Use

- Bug investigation where the root cause is non-obvious
- Feature planning where scope decisions carry real cost (schema, API shape, etc.)
- Architecture decisions with tradeoffs (simplicity vs scalability, etc.)
- Refactors that could go clean-break or incremental
- Any problem where a single-pass plan is likely to miss something

## When NOT to Use

- **Small, bounded problems** — one file, clear scope, low blast radius. Use `adversarial-lite` (2 rounds, no feedback loop).
- **Trivial bugs** — typos, one-line fixes, known recipes. Just fix them.
- **Problems where the user already has a plan** — don't re-deliberate a decision the user has made. Ask what they need help with specifically.

Before starting, if the problem is clearly bounded and low-risk, offer `adversarial-lite` first: "This looks bounded — want to use adversarial-lite (faster, lighter) or the full framework?"

## Prerequisites

Requires Claude Code Agent Teams:

```bash
# In ~/.claude/settings.json
{ "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
```

Takes effect on new Claude Code sessions. If Teams is not available (tool calls to `TeamCreate` / `SendMessage` error out), fall back to `references/subagent-fallback.md` — functionally equivalent but loses cross-round teammate persistence.

## Modes

| Mode | Flag | Behavior |
|---|---|---|
| **Default** | — | Fully automated. Lead orchestrates all rounds without pausing. |
| **Interactive** | `--interactive` | Pauses after Round 1 (user reviews proposals) and after Round 2 (user reviews synthesis) before continuing. |

---

## Architecture

```
ROUND 0  Setup: problem type → strategy pair → team → tasks
ROUND 1  proposer-a + proposer-b investigate in parallel (isolated)
           ─► proposal-A.md, proposal-B.md
         ▼  quality gate + convergence check + (interactive) checkpoint 1
ROUND 2  synthesizer-c reads both → produces 3 artifacts
           ─► proposal-C.md, review-for-A.md, review-for-B.md
         ▼  artifact gate + (interactive) checkpoint 2
ROUND 3  A and B resume from their Round 1 context, each reads ONLY its own
         review file → SendMessage feedback back to lead
ROUND 4  synthesizer-c resumes → reconciles both critiques
           ─► reconciliation-notes.md + final-plan.md
ROUND 5  (optional) external reviewer → review-external.md
ROUND 6  shutdown all teammates, TeamDelete, present to user
```

### Problem-type → strategy-pair table

| Problem type | Strategy A | Strategy B |
|---|---|---|
| bug_fix | Trace FORWARD from entry | Trace BACKWARD from symptom |
| feature | MINIMAL viable | ROBUST/extensible |
| architecture | Optimize for SIMPLICITY | Optimize for SCALABILITY |
| refactoring | INCREMENTAL migration | CLEAN-BREAK rewrite |

### Why isolation works

Each teammate has its own context window. A and B share no conversation history. All communication goes through the lead. The lead NEVER forwards A's work to B or vice versa. Round 3 isolation is maintained via per-proposer review files (`review-for-A.md`, `review-for-B.md`) that each teammate reads exclusively.

Empirically (audited over 300+ teammate transcripts): isolation holds strongly. The only common "violation" is cross-slug reads of a declared dependency's `final-plan.md`, which the isolation rule now explicitly permits (see below).

### Why teammates stay alive

- A and B retain full Round 1 investigation context when critiquing C in Round 3. This makes their pushback grounded in *why* they chose their original approach, not just what the review file claims.
- C retains its synthesis reasoning from Round 2 when reconciling in Round 4.

Persistence is the main reason to use Teams over the fallback. With fresh subagents per round, Round 3 critiques have to reconstruct intent from artifacts alone.

---

## How to Execute

You (the lead) orchestrate everything. The exact tool-call sequence is in `references/agent-teams-workflow.md`. Below is the orchestration checklist.

### Step 0 — Gather context and detect problem type

**0A. Empty-args short-circuit.** If invoked with no problem statement (e.g. `/adversarial-proposal` with empty `$ARGUMENTS`), stop immediately. Respond once with:

> "This skill needs a problem statement. Provide the problem in a single message with: (1) what's wrong or what you want to build, (2) the relevant source files or subdirectories, (3) any existing context (related PRs, prior decisions). If you want a lighter flow, use `adversarial-lite`."

Do NOT start drifting through the codebase looking for something to investigate. If the user doesn't provide a payload in the next turn, end the session.

**0B. Disambiguate skill vs. single-plan.** If the user said "write a proposal" or "draft a plan" (phrases that could mean either the adversarial flow or a single-file hand-rolled doc), ASK once:

> "Do you want the full adversarial flow (2 isolated investigators, synthesis, critique, reconciliation — ~15 min) or a single-pass plan document (~2 min)?"

Default to adversarial when in doubt on non-trivial problems.

**0C. Exploration budget.** Before spawning A and B, the lead reads **at most 3–5 files** to orient. The proposers re-read the source independently — that's the point. Over-exploration in the lead burns context that A/B will duplicate anyway. If you need more context, ask the user for specific files rather than searching broadly.

**0D. Detect problem type**: classify as `bug_fix`, `feature`, `architecture`, or `refactoring`. This selects the strategy pair.

**0E. Detect dependencies.** If the problem depends on a prior workstream (e.g. "3B-payload-assembly" depends on "3A-assignment-source-fetch"), collect the dependency's `final-plan.md` path and include it in the proposer prompts under **Dependency paths** (see isolation rule below).

**0F. Create output directory** `{project}/.dev/proposals/{slug}/` and write `problem-statement.md`.

### Step 1 — Team setup

```
TeamCreate(team_name="adv-<short-slug>", description=<short>)
# Create 5 tasks (A, B, C, critique, reconcile) with dependency chain
```

Details in `references/agent-teams-workflow.md` §"Round 0".

### Step 2 — Round 1: Spawn A and B in parallel (same turn)

Two `Agent(team_name=..., name="proposer-a", ...)` calls in a single message. Each prompt embeds the **proposer role** (`agents/proposer.md`), the **proposal format** (`references/proposal-format.md`), the assigned strategy, the **isolation rule** (see below), and explicit **"wait, don't exit"** instructions.

Then the lead idles — teammate "done" messages arrive as new conversation turns. No polling.

### Step 3 — Gates after Round 1

**Quality**: each proposal has required sections, ≥1 confidence annotation, ≥3 concrete code references. Failure → `SendMessage(to="proposer-a|b", message="revise: <specific>")`. Teammate resumes with full context.

**Convergence**: same core finding AND same approach? If yes, skip to §"Convergence path" in `agent-teams-workflow.md` — spawn C for lightweight review only, no Round 2/3.

**(Interactive) checkpoint 1**: pause for user review of proposal-A/B.

### Step 4 — Round 2: Synthesizer C

Spawn `synthesizer-c` teammate. Prompt embeds `agents/synthesizer.md` and instructs Phase 0 (independent code exam) before reading proposals. Produce THREE files.

### Step 5 — Round 2 artifact gate (required)

Before Round 3, **verify on disk**:
- `proposal-C.md` exists
- `review-for-A.md` exists
- `review-for-B.md` exists

If any is missing, `SendMessage(to="synthesizer-c", message="<name the missing file>")`. Do not proceed with partial artifacts — past runs have silently skipped `proposal-C.md` and produced orphaned final plans.

**(Interactive) checkpoint 2**: pause for user review of proposal-C.md.

### Step 6 — Round 3: Critique (pinned teammate descriptions)

Two `SendMessage` calls in one turn, using these exact `description` values for the underlying Agent tasks:
- `"Critic A: Round 3 feedback on proposal-A"`
- `"Critic B: Round 3 feedback on proposal-B"`

(Consistent naming makes transcripts scriptable and breach detection straightforward.)

Each message embeds `agents/critic.md` and the per-proposer isolation rule (read ONLY own review file + own proposal). Teammates resume with full Round 1 context.

### Step 7 — Round 4: Reconciliation

When both critiques arrive, `SendMessage(to="synthesizer-c", ...)` with both feedback bodies inline and `agents/reconciler.md` embedded. C produces TWO files:

- `final-plan.md` — the deliverable
- `reconciliation-notes.md` — one line per critique: `A: <point> → ACCEPTED/PARTIAL/REJECTED — <reason>` (reduces reconciler-bias blind spot)

### Step 8 — Round 5 (optional): External review

For high-stakes or cross-service problems, dispatch a fresh un-teamed `Agent` (general-purpose) to review `final-plan.md` against the source and write `review-external.md`. Useful because in-team critics share strategy-pair biases by design; an outside pair of eyes catches what the adversarial frame missed. Skip for routine work.

### Step 9 — Cleanup

```
SendMessage(to="proposer-a", message={"type": "shutdown_request", ...})  # all three in parallel
# Wait for shutdown acknowledgments (idle notifications)
TeamDelete()
```

Present `final-plan.md` (+ `reconciliation-notes.md` + optional `review-external.md`) to the user.

---

## Isolation Rule (embed verbatim in proposer prompts)

```
ISOLATION RULE — strict.

Allowed reads:
- Files listed under "Source files" in this prompt
- problem-statement.md in the current output directory
- Files listed under "Dependency paths" in this prompt (prior workstreams'
  final plans that this work depends on)

Forbidden reads, in the current output directory:
- proposal-A.md, proposal-B.md, proposal-C.md  (except your own proposal in Round 3)
- review-for-A.md, review-for-B.md              (except your own review in Round 3)
- final-plan.md, reconciliation-notes.md, review-external.md

When you finish, SendMessage the team lead "team-lead" with a one-line
plain-text summary. Then go idle — do NOT exit. You will be messaged again
in Round 3 to critique a technical review of your work.
```

Round 3 adds: "Read ONLY review-for-A.md (or review-for-B.md for B) and your own proposal-A.md. Do NOT read proposal-C.md, the other proposer's review, or the other proposer's proposal."

---

## Building the Teammate Prompts

Each teammate prompt must be **self-contained** — embed everything inline since teammates don't inherit the lead's conversation.

### What to Embed in Every Prompt

1. **The full problem statement** — verbatim from the user
2. **Source file paths** — explicit list of every file to examine (plus Dependency paths per Step 0E)
3. **The agent role** — full content from the relevant `agents/*.md` file
4. **The proposal format** — full content from `references/proposal-format.md` (for Rounds 1-2)
5. **The output path and filename** — exact save location
6. **The isolation rule** — for A and B in Rounds 1 and 3 (verbatim block above)
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
| Round 1 | One proposer fails | Wait for the other. Skip adversarial process — C reviews the single proposal for blind spots. |
| Round 1 | Both fail | Abort workflow. Report to user. |
| Round 2 | Synthesizer fails | Re-spawn C. If second attempt fails, present raw proposals. |
| Round 3 | One proposer fails | Proceed to Round 4 with available feedback. Note which is missing. |
| Round 3 | Both fail | Proceed to Round 4 with no feedback. C produces final-plan.md from synthesis alone. |
| Round 4 | Synthesizer fails | Re-spawn reconciler. If second attempt fails, present proposal-C.md. |

A teammate that produces no output while "running" is indistinguishable from one that is working — verify bytes on disk before assuming progress, ping once via SendMessage (a ping can wake a wedged teammate), and if respawning a replacement, give it different output paths so a late-waking original cannot clobber them.

### Quality Gate Failures

If a proposal fails the structural quality check after Round 1, message the proposer with specific feedback and request revision before proceeding.

---

## Known Tradeoffs

**Reconciler bias toward own synthesis.** C naturally favors its own proposal-C when reconciling. Mitigations:
- `agents/reconciler.md` includes steelman / perspective-test / convergent-signal guidance (soft).
- `reconciliation-notes.md` (required in Step 7) forces C to state explicitly which critique points were accepted vs rejected. Makes bias visible.
- Optional Step 8 external review catches cases where C rubber-stamped itself.
- `--interactive` mode lets the user review proposal-C.md before Round 3 critiques, providing a human check.

**Lead context pressure.** Orchestration across many rounds can fill the lead's window. Stay disciplined: the lead doesn't re-read source files (the teammates do). The lead reads problem-statement.md, the three synthesis artifacts, and the final plan. Everything else is teammate-scoped.

**Strategy pair can mismatch.** If the problem doesn't fit a neat bug/feature/arch/refactor bucket, the strategy pair may converge prematurely. The proposer role includes an escape hatch ("if the assigned strategy doesn't fit, note why and investigate naturally").

---

## Invoking the Skill

### Direct prompt
```
Use the adversarial proposal framework to investigate this bug:

<problem description, including relevant files>
```

### As a Claude Code command

`~/.claude/commands/adversarial-proposal.md`:
```
Read .claude/skills/adversarial-proposal/SKILL.md and
.claude/skills/adversarial-proposal/references/agent-teams-workflow.md,
then orchestrate the workflow.

Problem: $ARGUMENTS
```

Invocation:
```
/adversarial-proposal There is a bug with video prompt generation not including dialogue...
/adversarial-proposal --interactive There is a bug...
```

If `$ARGUMENTS` is empty, follow Step 0A (empty-args short-circuit).

---

## Output Directory

```
{project}/.dev/proposals/{slug}/
├── problem-statement.md
├── proposal-A.md
├── proposal-B.md
├── proposal-C.md              ← lead-only (absent on convergence path)
├── review-for-A.md            ← A's eyes only; zero references to B (absent on convergence path)
├── review-for-B.md            ← B's eyes only; zero references to A (absent on convergence path)
├── reconciliation-notes.md    ← accepted/rejected critiques with reasons (absent on convergence path)
├── final-plan.md              ← the deliverable
└── review-external.md         ← optional Step 8
```

The presence or absence of `proposal-C.md` + reviews is the structural signal of which path ran:
- **Full run**: all files present
- **Convergence run**: proposal-A/B + problem-statement + final-plan only

---

## Reference Files

| File | When to read | Purpose |
|---|---|---|
| `references/agent-teams-workflow.md` | Before orchestrating | Exact tool-call sequence for all rounds |
| `references/proposal-format.md` | When building proposer/synthesizer prompts | Proposal template to embed |
| `references/subagent-fallback.md` | Only if Teams is unavailable | Task-based fallback with file coordination |
| `agents/proposer.md` | Building A/B prompts | Investigation role + strategy table |
| `agents/synthesizer.md` | Building C's Round 2 prompt | Synthesis role with 3-file output spec |
| `agents/critic.md` | Building Round 3 messages | Critique guidance |
| `agents/reconciler.md` | Building Round 4 message | Bias-aware reconciliation + reconciliation-notes.md spec |

## Agent Roles Summary

| Agent | File | Used in | Purpose |
|---|---|---|---|
| Proposer | `agents/proposer.md` | Round 1 (A, B) | Independent investigation with assigned strategy |
| Synthesizer | `agents/synthesizer.md` | Round 2 (C) | Independent exam, synthesis, isolated review files |
| Critic | `agents/critic.md` | Round 3 (A, B) | Review own isolated critique, give grounded response |
| Reconciler | `agents/reconciler.md` | Round 4 (C) | Bias-aware reconciliation + reconciliation-notes.md |
