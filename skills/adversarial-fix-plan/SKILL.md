---
name: adversarial-fix-plan
description: >
  Automated adversarial fix-planning framework using Claude Code Agent Teams.
  Spawns 3 persistent teammates (Planner A, Planner B, Synthesizer C) to
  produce a vetted fix plan for a diagnosed bug through 4 rounds of isolated
  proposal, synthesis, critique, and reconciliation. A proposes a SURGICAL
  fix (minimum change to stop the diagnosed cause); B proposes a ROBUST fix
  (cause fixed + defenses against recurrence and adjacent fragility); C
  synthesizes; A and B critique C from their original contexts; C reconciles
  into a final fix-plan.md. Trusts the upstream diagnosis — does NOT
  re-investigate. Supports a revise mode for incorporating fix-review feedback.
  Trigger as the planning stage of a fix-pipeline, after adversarial-investigate
  has produced diagnosis.md.
---

# Adversarial Fix Plan Framework

## Overview

Three persistent teammates produce a vetted patch plan for a diagnosed bug through 4 rounds. The critical design principle is **information isolation** — A and B each have their own context window and never see each other's work. Only Synthesizer C sees both. A and B critique C from their original contexts (in Round 3 they see only their own isolated review file).

This skill differs from `adversarial-investigate` in three important ways:

1. **Inputs are narrow** — the upstream diagnosis is trusted as authoritative. The deliberation is about *how* to fix the known cause, not *what* the cause is.
2. **Strategy pair is about scope** — Surgical vs. Robust. They argue about how broadly the fix should reach.
3. **Two operating modes** — full mode (first invocation; runs all 4 rounds) and revise mode (second invocation; incorporates fix-review feedback into a v2 fix-plan).

The full tool-call sequence lives in `references/agent-teams-workflow.md`. **Read that file before orchestrating.** This document is the high-level guide.

## When to Use

- As the planning stage of a fix-pipeline, after `adversarial-investigate` has produced a CONVERGED or PARTIALLY CONVERGED diagnosis
- As the revise stage when `fix-plan-reviewer` has produced fix-review.md and the orchestrator has determined the feedback warrants incorporation

## When NOT to Use

- **Diagnosis is INSUFFICIENT EVIDENCE.** Hard refusal — see Hard Rules. The fix-pipeline must escalate or gather data first.
- **Trivial fixes.** One-line typo fixes don't need adversarial deliberation. Just fix them.
- **The diagnosis appears wrong.** Hard Rule — this skill does NOT re-investigate. If A or B believes the diagnosis is wrong, they flag it as a BLOCKING_ITEM and the skill stops. The orchestrator decides whether to re-run investigate.
- **Build-pipeline planning.** Use `adversarial-proposal` for forward-looking work.

## Hard Rules

1. **Trust the diagnosis.** This skill does not re-investigate, re-diagnose, or challenge the upstream root cause. If A or B encounters evidence that contradicts diagnosis.md during fix-planning, they note it as a BLOCKING_ITEM in their plan and stop. The skill does NOT silently re-do investigate's job.
2. **Refuse INSUFFICIENT EVIDENCE diagnoses.** If `diagnosis.md` has `convergence_status: INSUFFICIENT EVIDENCE`, this skill stops in Phase 0 and reports. The orchestrator should not have invoked it; this is defense in depth.
3. **PARTIALLY CONVERGED diagnoses force alternative-aware planning.** When the diagnosis is partial, the Robust planner MUST explicitly consider "what if an alternative cause is actually right" and propose defenses. The synthesis must explicitly note which alternatives the fix does or does not cover.
4. **Always produce fix-plan.md, even when blocked.** A skill that stops mid-flight still writes fix-plan.md with the blocking reason and a non-zero BLOCKING_ITEMS count. Silent failure is forbidden.
5. **Single output deliverable.** fix-plan.md is the only artifact downstream stages (fix-review, fix-coding) consume. Working artifacts (planA, planB, planC, reviews) are intermediate.
6. **No code changes.** This skill produces a plan. It does not modify source code. fix-coding does that.

## Prerequisites

Requires Claude Code Agent Teams:

```bash
# In ~/.claude/settings.json
{ "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
```

Takes effect on new Claude Code sessions. If Teams is not available, fall back to `references/subagent-fallback.md`.

## Modes

| Mode | Detection | Behavior |
|---|---|---|
| **Full** | `fix-review.md` does NOT exist in `--output-dir` | Run all 4 rounds. Produce fix-plan.md (v1). |
| **Revise** | `fix-review.md` exists in `--output-dir` | Skip Rounds 1-3. Synthesizer C reads the existing fix-plan.md + fix-review.md and produces fix-plan.md (v2) plus revision-notes.md. |
| **Interactive** | `--interactive` flag | Within full mode: pauses after Round 1 (user reviews fixA/fixB) and after Round 2 (user reviews fixC). Compatible with full mode only. |

The skill auto-detects full vs. revise based on the presence of `fix-review.md`. The orchestrator does not pass a mode flag.

**Expected runtime**: ~15–25 min for full mode (planning is narrower than investigation — A and B don't need to trace causality, they design fixes against a known cause). Convergence-path runs are shorter (~8–12 min). Revise mode is ~5–10 min (single agent, narrow scope: incorporate review points into existing plan).

## Inputs

Required:

- `--output-dir` — path to the fix directory. `diagnosis.md` lives here; `fix-plan.md` will be written here.
- `--env prod | dev` — which environment the bug exists in. Affects fix-strategy considerations (deployment risk for prod, iteration speed for dev).

Optional:

- `--original-slug` — slug of the original build pipeline whose feature has the bug. Enables read-only context access to that pipeline's `final-plan.md`, `implementation.md`, and `coder-report.md`.
- `--interactive` — pause at Round 1 and Round 2 checkpoints for user review. Full mode only.

## Architecture

```
ROUND 0  Setup: read inputs → check diagnosis convergence_status → detect mode
                → strategy pair → team → tasks
ROUND 1  planner-a + planner-b plan in parallel (isolated)
           A: SURGICAL — minimum change to stop the diagnosed cause
           B: ROBUST — cause fixed + defenses against recurrence
           ─► fixA.md, fixB.md
         ▼  quality gate + convergence check + (interactive) checkpoint 1
ROUND 2  synthesizer-c reads both → produces 3 artifacts
           ─► fixC.md, review-for-A.md, review-for-B.md
         ▼  artifact gate + (interactive) checkpoint 2
ROUND 3  A and B resume from Round 1 context, each reads ONLY its own
         review file → SendMessage feedback back to lead
ROUND 4  synthesizer-c resumes → reconciles both critiques
           ─► reconciliation-notes.md + fix-plan.md
ROUND 5  shutdown all teammates, TeamDelete, present to user

REVISE MODE (when fix-review.md is present on entry):
ROUND 0  Setup: read inputs (including existing fix-plan.md and fix-review.md)
ROUND R  synthesizer-c reads existing fix-plan.md and fix-review.md, produces
           ─► revision-notes.md (accept/partial/reject per review point)
           ─► fix-plan.md (v2 — overwrites v1)
ROUND 5  shutdown, TeamDelete, present to user
```

### Strategy pair

| Planner | Strategy | Focus | Catches |
|---|---|---|---|
| **A** | Surgical | Minimum change to stop the diagnosed cause | Over-engineering, scope creep, accidental refactors, fix-induced regressions from unnecessary changes |
| **B** | Robust | Cause fixed + defenses against recurrence and adjacent fragility | Insufficient defense, missed related bugs, "cause-fixed-but-symptom-still-possible" outcomes |

The synthesis (Round 2 and Round 4) lands somewhere on the spectrum between them, justified by the diagnosis's confidence level, the deployment environment, and the blast radius of the affected code.

### Why isolation works

Same as adversarial-investigate. Each teammate has its own context window. A and B share no conversation history. All communication goes through the lead. Round 3 isolation is maintained via per-planner review files.

---

## How to Execute

You (the lead) orchestrate everything. The exact tool-call sequence is in `references/agent-teams-workflow.md`. Below is the orchestration checklist.

### Step 0 — Gather context, validate diagnosis, detect mode

**0A. Empty-args short-circuit.** If invoked without `--output-dir`, without `--env`, or `diagnosis.md` does not exist at `{output_dir}/diagnosis.md`, stop immediately:

> "This skill requires --output-dir, --env (one of `prod` | `dev`), and an existing diagnosis.md in that directory. Provide --output-dir and --env, and ensure adversarial-investigate has run."

**0B. Read inputs from `{output_dir}`:**

- `diagnosis.md` — required
- `problem-statement.md` — required (context)
- `repro.md` — if present (context)
- `fix-review.md` — if present, switches to REVISE mode
- `fix-plan.md` — if present (only relevant in revise mode; this is the v1 being revised)

**0C. Check diagnosis convergence_status.**

Parse the convergence status from diagnosis.md by locating the `## Convergence Status` header and reading the value on the line(s) immediately below (per `references/diagnosis-format.md` from the adversarial-investigate skill). Expected values: `CONVERGED`, `PARTIALLY CONVERGED`, `INSUFFICIENT EVIDENCE`.

- **CONVERGED** → proceed normally
- **PARTIALLY CONVERGED** → proceed, but the Robust planner role is required to consider alternative causes (see role file)
- **INSUFFICIENT EVIDENCE** → STOP (handled below)
- **Missing or unparseable** → treat as defense-in-depth equivalent to INSUFFICIENT EVIDENCE: STOP and report. A diagnosis without a parseable convergence status cannot be safely planned against.

For **INSUFFICIENT EVIDENCE** or missing/unparseable status, write fix-plan.md with:

```
# Fix Plan — BLOCKED

## Status
BLOCKED — diagnosis convergence_status is {INSUFFICIENT EVIDENCE | missing/unparseable}.
Fix planning cannot proceed without a committed (or partially committed) root cause.

## Required Action
Re-run adversarial-investigate with additional data per the required_data
section of diagnosis.md, or escalate to the user for manual diagnosis.

CONFIDENCE: N/A
BLOCKING_ITEMS: 1
```

Then exit. Do not spawn teammates.

**Downstream contract:** when a downstream stage (orchestrator, fix-coding) sees `Status: BLOCKED` with `BLOCKING_ITEMS: 1`, it must NOT advance to fix-coding. The expected actions are: re-run adversarial-investigate with the data named in diagnosis.md's `required_data` section, OR escalate to the user. Silent advancement past a BLOCKED fix-plan is a contract violation.

**0D. Read original pipeline artifacts (if `--original-slug` provided):**

Read-only context from `.dev/proposals/{original-slug}/`:
- `final-plan.md` — what the feature was supposed to do
- `implementation.md` — what was actually built
- `coder-report.md` — what was committed

Used to understand the surrounding code and avoid breaking adjacent functionality. NOT used to second-guess the diagnosis.

**0E. Detect mode.**

- If `fix-review.md` exists → REVISE mode. Skip to Step 9.
- Otherwise → FULL mode. Proceed to Step 1.

**0F. Confirm `--env`.** `prod` or `dev`. Affects:
- Risk assessment weight (prod fixes have higher blast radius)
- Rollback plan rigor (prod requires explicit rollback steps; dev can be looser)
- Deployment considerations (prod requires staged rollout consideration; dev does not)

**0G. Exploration budget.** Before spawning A and B, the lead reads **at most 3–5 files** to orient. The planners re-read source independently — that's the point. Over-exploration in the lead burns context that A/B will duplicate anyway.

### Step 1 — Team setup

```
TeamCreate(team_name="fixp-<short-slug>", description=<short>)
# Create 5 tasks (A, B, C, critique, reconcile) with dependency chain
```

Details in `references/agent-teams-workflow.md` §"Round 0".

### Step 2 — Round 1: Spawn A and B in parallel (same turn)

Two `Agent(team_name=..., name="planner-a", ...)` calls in a single message. Each prompt embeds:

- `agents/fix-planner.md` (the role)
- `references/fix-plan-format.md` (the working artifact format)
- The assigned strategy (Surgical for A, Robust for B)
- Full content of `diagnosis.md`, `problem-statement.md`, `repro.md` (if present)
- Original pipeline artifacts (if `--original-slug`) marked clearly as CONTEXT
- The env mode and what it implies for risk/rollback rigor
- Output path: `{output_dir}/fixA.md` (or `fixB.md`)
- Isolation rule (see below)
- Anti-second-guessing requirement: do NOT re-investigate. If diagnosis seems wrong, flag as BLOCKING_ITEM and stop.
- For PARTIALLY CONVERGED diagnoses (B only): explicit instruction to consider alternative causes from diagnosis.md and propose defenses

Then the lead idles.

### Step 3 — Gates after Round 1

**Artifact gate (required, run first)**: verify on disk that `fixA.md` and `fixB.md` both exist and are non-empty. If either is missing or empty, `SendMessage(to="planner-a|b", message="Your plan file at {path} is missing or empty. Write your full plan to that path before signaling done.")`. Do not proceed to the quality gate until both files exist on disk.

**Quality gate**: each plan has required sections — diagnosis reference, fix approach, file-level changes, sequencing (when multi-file changes have ordering constraints), defenses (or "none" with rationale), test plan, rollback plan, risk assessment. Failure → `SendMessage(to="planner-a|b", message="revise: <specific>")`.

**Diagnosis-disagreement check**: if either A or B flagged the diagnosis as wrong (BLOCKING_ITEM), do NOT proceed. Stop and report to the orchestrator. The orchestrator decides whether to re-run investigate.

**Convergence check**: did A and B independently land on the same fix approach with the same files touched? If yes, skip to §"Convergence path" — spawn C for lightweight review only, no Round 2/3.

**(Interactive) checkpoint 1**: pause for user review of fixA/fixB.

### Step 4 — Round 2: Synthesizer C

Spawn `synthesizer-c` teammate. Prompt embeds `agents/fixp-synthesizer.md` and instructs:

- Phase 0: read source files and diagnosis directly, before reading A or B's plans
- Phase 1: read fixA.md and fixB.md
- Phase 2: cross-verify the proposed fixes against the actual code (do they touch the right files? would they actually fix the diagnosed cause?)
- Phase 3: synthesize into fixC.md with C's chosen position on the Surgical-Robust spectrum, justified
- Phase 4: write isolated review files for A and B (no mention of the other planner)

### Step 5 — Round 2 artifact gate (required)

Before Round 3, **verify on disk**:
- `fixC.md` exists
- `review-for-A.md` exists
- `review-for-B.md` exists

If any is missing, `SendMessage(to="synthesizer-c", message="<name the missing file>")`. Do not proceed with partial artifacts.

**(Interactive) checkpoint 2**: pause for user review of fixC.md.

### Step 6 — Round 3: Critique (pinned teammate descriptions)

Two `SendMessage` calls in one turn, using these exact `description` values:
- `"Critic A: Round 3 feedback on fixA"`
- `"Critic B: Round 3 feedback on fixB"`

Each message embeds `agents/fixp-critic.md` and the per-planner isolation rule (read ONLY own review file + own plan). Teammates resume with full Round 1 context.

The critic role specifically asks each planner: "Does C's synthesis fairly represent your fix approach? Did C reject your defenses (or your minimalism) without sufficient justification? Does C's chosen position adequately address the diagnosed cause AND its environment-appropriate blast radius?"

### Step 7 — Round 4: Reconciliation

When both critiques arrive, `SendMessage(to="synthesizer-c", ...)` with both feedback bodies inline and `agents/fixp-reconciler.md` embedded.

C produces TWO files:

- `fix-plan.md` — the deliverable
- `reconciliation-notes.md` — one line per critique: `A: <point> → ACCEPTED/PARTIAL/REJECTED — <reason>`

### Step 8 — Cleanup (full mode)

```
SendMessage(to="planner-a", message={"type": "shutdown_request", ...})  # all three in parallel
TeamDelete()
```

Present `fix-plan.md` (+ `reconciliation-notes.md`) to the user.

### Step 9 — Revise mode (when fix-review.md is present)

REVISE mode skips Rounds 1-3 entirely. The team is single-agent (just synthesizer-c) because the deliberation is narrow: incorporate review feedback into an existing plan.

**9A. Validate revise-mode preconditions** before spawning anything:

- `fix-review.md` exists and is non-empty (size > 200 bytes — guards against accidentally-touched empty files)
- `fix-review.md` contains structured review points (look for headers/bullets, not just freeform prose). If structure cannot be detected, surface to user: "fix-review.md exists but does not appear to contain structured review points. Continue anyway, or have the reviewer re-issue?" Wait for confirmation.
- `fix-plan.md` exists (this is v1 — the plan being revised). If it does not, this is not a revise scenario; either fail with a clear message or fall through to full mode (orchestrator decision; default = fail).
- `fix-review.md` mtime is newer than `fix-plan.md` mtime. If not, the review predates the current plan and may already have been incorporated — surface to user: "fix-review.md is older than fix-plan.md. The review may already be incorporated. Continue anyway?" Wait for confirmation.

If any precondition fails and the user does not confirm, do not enter revise mode.

**9B. Spawn the reviser:**

```
TeamCreate(team_name="fixp-rev-<short-slug>", description="Revise fix-plan from review")
# Create 1 task (revise) — no dependencies
```

Spawn `synthesizer-c` with prompt embedding `agents/fixp-reviser.md`. The reviser role:

- Phase 0: read the existing `fix-plan.md` (v1), `fix-review.md`, `diagnosis.md`
- Phase 1: classify each review point — Accept (incorporate), Partial (incorporate with caveat), Reject (do not incorporate, justify)
- Phase 2: write `revision-notes.md` with the classification
- Phase 3: produce revised `fix-plan.md` (v2) — overwrites v1
- Hard rule: rejecting a review point requires explicit justification in revision-notes.md
- Hard rule: every review point must appear in revision-notes.md with a verdict — silent drops are forbidden

Output:
- `fix-plan.md` — overwritten with v2
- `revision-notes.md` — audit trail of which review points were accepted/rejected

**9C. Revise-mode quality gate.** After the reviser signals done, verify on disk before cleanup:

- `fix-plan.md` exists, is non-empty, and contains all required sections per `fix-plan.md Structure` (Status, Diagnosis Reference, Environment, Fix Approach, Changes, Sequencing, Defenses, Test Plan, Rollback Plan, Risk Assessment, Confidence + Blocking Items)
- `revision-notes.md` exists and is non-empty
- `revision-notes.md` contains an entry for every substantive review point in `fix-review.md` (no silent drops)

If any check fails: `SendMessage(to="synthesizer-c", message="<specific gap>")`. One retry; if it still fails, surface to user with what's incomplete.

**9D. Archive consumed fix-review.md.** After the quality gate passes, rename the consumed review:

```
mv {output_dir}/fix-review.md {output_dir}/fix-review-{timestamp}.md
```

This guards against re-entering revise mode on the next invocation when the v2 plan has not yet been re-reviewed. The orchestrator's next fix-plan-reviewer run produces a fresh `fix-review.md` if v2 needs revising; the timestamped archive preserves the audit trail.

### Step 10 — Cleanup (revise mode)

Same as Step 8 but with single teammate.

---

## Isolation Rule (embed verbatim in planner prompts)

```
ISOLATION RULE — strict.

Allowed reads:
- Files listed under "Source files" in this prompt
- diagnosis.md, problem-statement.md, repro.md in the current output directory
- Files listed under "Original pipeline context" (if provided) — READ-ONLY, treat as
  context for understanding the codebase, NOT as a list of suspects or fix locations

Forbidden reads, in the current output directory:
- fixA.md, fixB.md, fixC.md  (except your own plan in Round 3)
- review-for-A.md, review-for-B.md  (except your own review in Round 3)
- fix-plan.md, reconciliation-notes.md, fix-review.md, revision-notes.md

Forbidden writes:
- Any file under .dev/proposals/{original-slug}/  (read-only context)
- Any source code file (this skill produces a plan, not code)

DO NOT re-investigate or re-diagnose. The diagnosis is authoritative.
If you believe the diagnosis is wrong, write your plan with status "BLOCKED",
flag the contradiction in BLOCKING_ITEMS, and stop. The orchestrator will decide
whether to re-run investigate.

When you finish, SendMessage the team lead "team-lead" with a one-line plain-text
summary including your fix approach. Then go idle — do NOT exit. You will be
messaged again in Round 3 to critique a synthesis of your work.
```

Round 3 adds: "Read ONLY review-for-A.md (or review-for-B.md for B) and your own fixA.md (or fixB.md). Do NOT read fixC.md, the other planner's review, or the other planner's plan."

---

## fix-plan.md Structure

```markdown
# Fix Plan — {slug}

## Status
{ACTIVE | BLOCKED}

## Diagnosis Reference
{Citation of the root cause from diagnosis.md being addressed.
 For PARTIALLY CONVERGED: explicit list of alternatives this plan considers (or doesn't).}

## Environment
{prod | dev}

## Fix Approach
{The chosen position on the Surgical-Robust spectrum, with rationale.
 If pure Surgical: why no defenses are warranted given the diagnosis confidence and blast radius.
 If pure Robust: why the broader scope is justified.
 Most likely: a synthesis that's surgical on the cause and robust on a small set of related fragilities.}

## Changes
{File-level overview. For each file:
 - Path
 - Nature of change (modify / add / remove)
 - Brief description (one or two sentences)
 - Whether the change is for the cause or a defense

This is NOT diff-level. The implementation stage produces diffs. This is the plan.}

## Sequencing
{Order of operations across the changes above. Required when there are multi-file
 changes with ordering constraints; "single-file change" or "order does not matter"
 is also a valid answer when true. Examples of constraints:
 - Migration must land before code that reads/writes the new column
 - Feature flag must be added (default off) before gated reads
 - New service endpoint must deploy before client code that calls it
 The downstream fix-coding stage uses this section to construct commit groups.}

## Defenses
{What's being added beyond the minimum cause-fix.
 If "none": explicit rationale rooted in the diagnosis confidence and blast radius.
 Each defense: what fragility it guards against, why it's worth the additional change.}

## Test Plan
{What tests prove the fix works AND prove relevant regressions don't happen.
 - Tests that confirm the diagnosed cause is no longer reproducible (referencing repro.md if present)
 - Tests that confirm the defenses (if any) work
 - Regression tests for adjacent functionality the changes touch
 - Existing tests that must still pass}

## Rollback Plan
{How to back out the fix if it causes problems in prod.
 - For prod: explicit revert steps, deployment considerations, what to monitor post-deploy
 - For dev: lighter — what files to revert, what state to reset
 - Always present, never "N/A"}

## Risk Assessment
{What could go wrong with this fix:
 - Blast radius (which other features could be affected)
 - Compatibility (API/schema changes? backward-compat concerns?)
 - Performance (could this fix slow something down?)
 - The most likely failure mode of the fix itself
 - For prod: deployment risk, traffic considerations}

## Confidence + Blocking Items
CONFIDENCE: HIGH | MEDIUM | LOW
BLOCKING_ITEMS: N
```

When status is BLOCKED, sections after Status describe the blocking reason and the fix-plan.md is shorter — most sections may be N/A. The skill MUST still write the file.

---

## Bias Mitigations

**Diagnosis-second-guessing.**
Hard Rule. The role files explicitly forbid re-investigation. If a planner believes the diagnosis is wrong, the only valid action is to flag it as a BLOCKING_ITEM and stop.

**Scope creep (Robust planner over-engineers).**
Robust planner's role file requires explicit justification per defense — what fragility it guards against and why the additional change is worth it. The synthesizer's role file is required to challenge defenses that lack clear justification.

**Under-engineering (Surgical planner ignores adjacent fragility).**
Surgical planner's role file requires explicit answer to "is the diagnosed cause likely to recur via a slightly different path? If so, why are no defenses warranted?" Forces the question to be answered, not skipped.

**Reconciler bias toward own synthesis.**
Same mechanism as adversarial-investigate:
- `agents/fixp-reconciler.md` includes steelman / perspective-test guidance
- `reconciliation-notes.md` (required) forces explicit accept/partial/reject for every critique point
- `--interactive` mode lets user review fixC.md before Round 3 critiques

**PARTIALLY CONVERGED complacency.**
When the diagnosis has known alternatives, the easy thing is to ignore them and plan for the top hypothesis only. The Robust role file is required to address each alternative explicitly: either propose a defense, or justify why the alternative is unlikely enough to ignore.

---

## Known Tradeoffs

**Lead context pressure.** Same as adversarial-investigate — discipline matters. Lead reads diagnosis.md, problem-statement.md, the three synthesis artifacts, and the final fix-plan.md. Source files are teammate-scoped.

**The Surgical-Robust pair can underperform on architectural bugs.** If the diagnosed cause is a design-level issue (not a localized bug), both Surgical and Robust may converge on inadequate fixes. The synthesizer should detect this and flag in fixC.md — but the skill cannot architect a redesign. Architectural diagnoses should escalate to a build-pipeline (`adversarial-proposal`) for the redesign rather than a fix-pipeline.

**Revise mode is single-agent.** Loses adversarial pressure on the revision pass. This is intentional — fix-review's feedback is itself adversarial, and revise mode is incorporating that feedback rather than re-deliberating. If a revision needs deeper deliberation, the orchestrator should re-run full mode.

---

## Invoking the Skill

### Direct prompt (full mode)

```
/adversarial-fix-plan \
  --output-dir .dev/fixes/jwt-refresh-tokens/auth-token-refresh-race/ \
  --env prod \
  --original-slug jwt-refresh-tokens
```

### Direct prompt (revise mode — auto-detected by presence of fix-review.md)

```
/adversarial-fix-plan \
  --output-dir .dev/fixes/jwt-refresh-tokens/auth-token-refresh-race/ \
  --env prod \
  --original-slug jwt-refresh-tokens
```

(Same invocation. The skill detects fix-review.md in the output directory and switches modes.)

### As a Claude Code command

`~/.claude/commands/adversarial-fix-plan.md`:

```
Read .claude/skills/adversarial-fix-plan/SKILL.md and
.claude/skills/adversarial-fix-plan/references/agent-teams-workflow.md,
then orchestrate the workflow.

Arguments: $ARGUMENTS
```

---

## Output Directory

```
{output_dir}/
├── problem-statement.md         ← Input (from orchestrator)
├── repro.md                     ← Input (from fix-reproducer, if it ran)
├── diagnosis.md                 ← Input (from adversarial-investigate)
├── fixA.md                      ← Round 1: Surgical (full mode only)
├── fixB.md                      ← Round 1: Robust (full mode only)
├── fixC.md                      ← Round 2: Synthesis (lead-only, full mode only)
├── review-for-A.md              ← Round 2: A's eyes only (full mode only)
├── review-for-B.md              ← Round 2: B's eyes only (full mode only)
├── reconciliation-notes.md      ← Round 4: ACCEPT/PARTIAL/REJECT audit (full mode only)
├── fix-plan.md                  ← Round 4 (full) OR Round R (revise): FINAL DELIVERABLE
├── fix-review.md                ← Input from fix-plan-reviewer; its presence triggers revise mode (archived to fix-review-{ts}.md after consumption)
├── fix-review-{ts}.md           ← (Post-revise) Archived review files preserved for audit
└── revision-notes.md            ← (Revise mode only) Audit trail of accept/reject per review point
```

The presence or absence of intermediate files signals which path ran:
- **Full run**: fixA, fixB, fixC, reviews, reconciliation-notes, fix-plan.md
- **Convergence run** (A and B agreed): fixA, fixB, fix-plan.md only
- **Revise run**: fix-plan.md (overwritten v2), revision-notes.md
- **Blocked run** (INSUFFICIENT EVIDENCE diagnosis): fix-plan.md only with BLOCKED status

---

## Reference Files

| File | When to read | Purpose |
|---|---|---|
| `references/agent-teams-workflow.md` | Before orchestrating | Exact tool-call sequence for all rounds (full and revise) |
| `references/fix-plan-format.md` | When building planner/synthesizer prompts | Working artifact template for fixA/fixB/fixC |
| `references/fix-plan-final-format.md` | When building reconciler/reviser prompts | Final fix-plan.md template (matches structure above) |
| `references/subagent-fallback.md` | Only if Teams is unavailable | Task-based fallback with file coordination |
| `agents/fix-planner.md` | Building A/B prompts | Planner role + strategy assignment + diagnosis-trust requirement + bias mitigations |
| `agents/fixp-synthesizer.md` | Building C's Round 2 prompt | Synthesis role with 3-file output spec |
| `agents/fixp-critic.md` | Building Round 3 messages | Critique guidance |
| `agents/fixp-reconciler.md` | Building Round 4 message | Bias-aware reconciliation + fix-plan.md spec |
| `agents/fixp-reviser.md` | Building revise-mode prompt | Single-agent revise role + revision-notes.md spec |

## Agent Roles Summary

| Agent | File | Used in | Purpose |
|---|---|---|---|
| Planner | `agents/fix-planner.md` | Round 1 (A, B) | Independent fix planning with assigned strategy + diagnosis-trust requirement |
| Synthesizer | `agents/fixp-synthesizer.md` | Round 2 (C) | Independent verification, synthesis, isolated review files |
| Critic | `agents/fixp-critic.md` | Round 3 (A, B) | Review own isolated critique, defend or concede |
| Reconciler | `agents/fixp-reconciler.md` | Round 4 (C) | Bias-aware reconciliation + final fix-plan.md |
| Reviser | `agents/fixp-reviser.md` | Round R (revise mode) | Single-agent incorporation of fix-review feedback into v2 |
