---
name: adversarial-investigate
description: >
  Automated adversarial investigation framework using Claude Code Agent Teams.
  Spawns 3 persistent teammates (Investigator A, Investigator B, Synthesizer C)
  to diagnose the root cause of a known bug through 4 rounds of isolated
  analysis, synthesis, critique, and reconciliation. A traces FORWARD from the
  bug trigger; B traces BACKWARD from the failure point; both investigate the
  same evidence independently with zero knowledge of each other. C reviews both
  and synthesizes; A and B critique C from their original contexts; C
  reconciles into a final diagnosis. Allows "insufficient evidence" as a valid
  exit — does not force premature commitment to a cause. Trigger as the
  diagnosis stage of a fix-pipeline, after fix-reproducer (or directly off
  problem-statement.md if reproduce was skipped).
---

# Adversarial Investigation Framework

## Overview

Three persistent teammates investigate the root cause of a known bug through 4 rounds. The critical design principle is **information isolation** — A and B each have their own context window and never see each other's work. Only Synthesizer C sees both. A and B critique C from their original contexts (in Round 3 they see only their own isolated review file, never C's full synthesis or the other investigator's hypotheses). C reconciles into a final diagnosis.

This skill differs from `adversarial-proposal` in three important ways:

1. **Inputs are richer** — beyond a problem statement, investigators read repro.md (if present), the original build pipeline's artifacts (read-only context), and operate in an env-aware mode (prod is observational; dev is interactive).
2. **Output is a diagnosis, not a plan** — `diagnosis.md` commits to a root cause with evidence chain, or honestly admits insufficient evidence and specifies what data would resolve the ambiguity.
3. **A third valid exit exists** — INSUFFICIENT EVIDENCE is not a failure; it's a structurally legitimate output that signals the fix-pipeline to gather more data before proceeding.

The full tool-call sequence lives in `references/agent-teams-workflow.md`. **Read that file before orchestrating.** This document is the high-level guide.

## When to Use

- As the diagnosis stage of a fix-pipeline, after a bug has been reported (and ideally reproduced)
- When the cause of a known failure is non-obvious or could plausibly be in multiple places
- When prior fixes for similar-looking bugs failed to resolve the underlying cause
- When the bug surfaces inconsistently and you suspect compound conditions

## When NOT to Use

- **Trivial bugs** — typos, one-line errors with obvious cause. Just fix them.
- **Bugs where the cause is already known** — don't re-deliberate. Go straight to fix-plan.
- **Build-pipeline planning** — use `adversarial-proposal` for forward-looking work; investigation is for diagnosing known failures.
- **Bugs where reproduction itself is the problem** — get fix-reproducer to land first, then investigate.

## Prerequisites

Requires Claude Code Agent Teams:

```bash
# In ~/.claude/settings.json
{ "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
```

Takes effect on new Claude Code sessions. If Teams is not available, fall back to `references/subagent-fallback.md` — functionally equivalent but loses cross-round teammate persistence.

## Modes

| Mode | Flag | Behavior |
|------|------|----------|
| **Default** | — | Fully automated. Lead orchestrates all rounds without pausing. |
| **Interactive** | `--interactive` | Pauses after Round 1 (user reviews investigations) and after Round 2 (user reviews synthesis) before continuing. |

**Expected runtime**: ~20–30 min for a full run (longer than `adversarial-proposal` because investigators read more context — problem statement, repro.md, original-pipeline artifacts, plus source). Convergence-path runs are shorter (~10–15 min). INSUFFICIENT EVIDENCE outcomes typically resolve within Round 2 once C confirms no investigator hit ground truth.

## Inputs

Required:

- `--output-dir` — path to the fix directory. `problem-statement.md` lives here; `diagnosis.md` and supporting artifacts will be written here.
- `--env prod | dev` — which environment the bug exists in. Affects investigation tooling (prod = observational; dev = interactive).

Optional:

- `--original-slug` — slug of the original build pipeline whose feature has the bug. Enables read-only context access to that pipeline's `final-plan.md`, `implementation.md`, and `coder-report.md`.
- `--interactive` — pause at Round 1 and Round 2 checkpoints for user review.

## Architecture

```
ROUND 0  Setup: read inputs → determine env path → strategy pair → team → tasks
ROUND 1  investigator-a + investigator-b investigate in parallel (isolated)
           A: traces FORWARD from the bug trigger
           B: traces BACKWARD from the failure point
           ─► investigation-A.md, investigation-B.md
         ▼  quality gate + convergence check + (interactive) checkpoint 1
ROUND 2  synthesizer-c reads both → produces 3 artifacts
           ─► investigation-C.md, review-for-A.md, review-for-B.md
         ▼  artifact gate + (interactive) checkpoint 2
ROUND 3  A and B resume from their Round 1 context, each reads ONLY its own
         review file → SendMessage feedback back to lead
ROUND 4  synthesizer-c resumes → reconciles both critiques
           ─► reconciliation-notes.md + diagnosis.md
ROUND 5  shutdown all teammates, TeamDelete, present to user
```

### Strategy pair

| Investigator | Strategy | Approach |
|---|---|---|
| **A** | Forward-from-trigger | Start at the action that triggers the bug (per repro.md or problem-statement.md) and walk forward through the code/data flow, noting assumptions made and verifying state at each transition until the failure manifests. Catches: "an early assumption turned out to be false later." |
| **B** | Backward-from-failure-point | Start at the visible failure (error, wrong output, exception) and walk backward through call stacks, data dependencies, and state transitions to find what produced it. Catches: "this state was wrong by the time we got here, where did it come from." |

Both end up needing to understand the same code, but they enter it from different ends, and they tend to notice different things.

### Why isolation works

Each teammate has its own context window. A and B share no conversation history. All communication goes through the lead. The lead NEVER forwards A's work to B or vice versa. Round 3 isolation is maintained via per-investigator review files (`review-for-A.md`, `review-for-B.md`) that each teammate reads exclusively.

### Why teammates stay alive

- A and B retain full Round 1 investigation context when critiquing C in Round 3. This makes their pushback grounded in *why* they chose their original hypothesis ranking, not just what the review file claims.
- C retains its synthesis reasoning from Round 2 when reconciling in Round 4.

Persistence is the main reason to use Teams over the fallback.

---

## How to Execute

You (the lead) orchestrate everything. The exact tool-call sequence is in `references/agent-teams-workflow.md`. Below is the orchestration checklist.

### Step 0 — Gather context and detect inputs

**0A. Empty-args short-circuit.** If invoked without `--output-dir`, without `--env`, or without a problem statement on disk, stop immediately. Respond:

> "This skill requires a problem statement at {output-dir}/problem-statement.md and the --env flag. Provide --output-dir and --env (one of `prod` | `dev`), and ensure problem-statement.md exists."

Do NOT attempt to investigate without these inputs.

**0B. Read inputs from `{output_dir}`:**

- `problem-statement.md` — required
- `repro.md` — if present, included in investigators' input. If absent, investigators must work from the problem statement alone.

**0C. Read original pipeline artifacts (if `--original-slug` provided):**

Read-only context from `.dev/proposals/{original-slug}/`:
- `final-plan.md` — what the feature was supposed to do
- `implementation.md` — what was actually built (exact diffs)
- `coder-report.md` — what was committed and what tests ran

These are **context, not suspects.** The investigators read them to understand what was built, not as a list of likely causes.

**0D. Confirm `--env`:**

`prod` or `dev`. The env affects investigator tooling guidance:
- **dev**: investigators may run code locally, add temporary logging, query dev DB, step through. Active investigation.
- **prod**: investigators are observational — read logs, query telemetry, read code. They may NOT run code in production. Passive investigation.

**Hybrid case (prod bug reproducible in dev).** If `--env prod` was passed but `repro.md` classifies the bug as REPRODUCED or PARTIALLY REPRODUCED in dev, dev-mode investigation IS permitted to test hypotheses about the prod bug. Investigators must explicitly record any dev-bridging in their evidence chain (e.g., "verified hypothesis H2 by reproducing in dev — dev/prod parity confirmed for {component}"). The diagnosis must still answer for prod.

The skill is agnostic about specific tools. The runtime composes Railway-aware skills, Playwright, log scrapers, etc., based on what's available.

**0E. Exploration budget.** Before spawning A and B, the lead reads **at most 3–5 files** to orient. The investigators re-read source independently — that's the point. Over-exploration in the lead burns context that A/B will duplicate anyway.

**0F. Evidence floor (early bail-out).** Before spawning teammates, confirm at least one of these is present:

- `repro.md` exists and classifies as REPRODUCED or PARTIALLY REPRODUCED
- `--original-slug` is provided (gives access to feature artifacts as context)
- The problem statement contains concrete telemetry references (logs, error tracking IDs, stack traces, response bodies)

If none are present (only a vague problem statement), surface to the user before spawning teammates:

> "No reproduction, no original-pipeline context, and no telemetry references in the problem statement. Investigation will likely return INSUFFICIENT EVIDENCE. Recommend running fix-reproducer first or gathering data before proceeding. Continue anyway?"

Wait for explicit user confirmation. This avoids burning Teams budget on a structurally guaranteed INSUFFICIENT EVIDENCE outcome.

### Step 1 — Team setup

```
TeamCreate(team_name="inv-<short-slug>", description=<short>)
# Create 5 tasks (A, B, C, critique, reconcile) with dependency chain
```

Details in `references/agent-teams-workflow.md` §"Round 0".

### Step 2 — Round 1: Spawn A and B in parallel (same turn)

Two `Agent(team_name=..., name="investigator-a", ...)` calls in a single message. Each prompt embeds the **investigator role** (`agents/investigator.md`), the **investigation format** (`references/investigation-format.md`), the assigned strategy, the **isolation rule** (see below), the **env mode** (prod or dev), and explicit **"wait, don't exit"** instructions.

**Investigator A prompt must include:**
- Full content of `problem-statement.md`
- Full content of `repro.md` (if present)
- Original pipeline artifacts (if `--original-slug`) marked clearly as CONTEXT, NOT SUSPECTS
- Investigator role (from `agents/investigator.md`)
- Strategy assignment: **Forward-from-trigger**
- Env mode and what investigation is permitted
- Output path: `{output_dir}/investigation-A.md`
- Isolation rule: do NOT read investigation-B.md or any other investigation files
- Anti-anchoring requirement: must list ≥2 alternative hypotheses even if a leading candidate exists
- Symptom-confirmation challenge: must explicitly answer "could the visible error be a symptom of a deeper cause?"

**Investigator B prompt must include:**
- Same problem statement, repro, and original-pipeline context
- Same role file
- Strategy assignment: **Backward-from-failure-point**
- Same env mode, output path differing (`investigation-B.md`), same isolation rule, same anti-bias requirements

Then the lead idles — teammate "done" messages arrive as new conversation turns. No polling.

### Step 3 — Gates after Round 1

**Artifact gate (required, run first)**: verify on disk that `investigation-A.md` and `investigation-B.md` both exist and are non-empty. If either is missing or empty, `SendMessage(to="investigator-a|b", message="Your investigation file at {path} is missing or empty. Write your full investigation to that path before signaling done.")`. Do not proceed to the quality gate until both files exist on disk.

**Quality gate**: each investigation has required sections — at least 2 hypotheses (top + alternatives), evidence per hypothesis, explicit symptom-confirmation answer, ≥3 concrete code/log references. Failure → `SendMessage(to="investigator-a|b", message="revise: <specific>")`. Teammate resumes with full context.

**Convergence check**: did A and B independently land on the same top hypothesis with the same evidence? If yes, skip to §"Convergence path" in `agent-teams-workflow.md` — spawn C for lightweight review only, no Round 2/3.

**(Interactive) checkpoint 1**: pause for user review of investigation-A/B.

### Step 4 — Round 2: Synthesizer C

Spawn `synthesizer-c` teammate. Prompt embeds `agents/inv-synthesizer.md` and instructs Phase 0 (independent code/evidence exam) before reading investigations. Produce THREE files.

Synthesizer C is required to:

- Phase 0: read source files and evidence directly, before reading A or B's work
- Phase 1: read investigation-A and investigation-B
- Phase 2: cross-verify the highest-evidence hypotheses against the actual code
- Phase 3: synthesize into investigation-C.md with C's own committed top hypothesis, ranked alternatives, and a tentative convergence-status assessment
- Phase 4: write isolated review files for A and B (no mention of the other investigator)

### Step 5 — Round 2 artifact gate (required)

Before Round 3, **verify on disk**:
- `investigation-C.md` exists
- `review-for-A.md` exists
- `review-for-B.md` exists

If any is missing, `SendMessage(to="synthesizer-c", message="<name the missing file>")`. Do not proceed with partial artifacts.

**(Interactive) checkpoint 2**: pause for user review of investigation-C.md.

### Step 6 — Round 3: Critique (pinned teammate descriptions)

Two `SendMessage` calls in one turn, using these exact `description` values:
- `"Critic A: Round 3 feedback on investigation-A"`
- `"Critic B: Round 3 feedback on investigation-B"`

Each message embeds `agents/inv-critic.md` and the per-investigator isolation rule (read ONLY own review file + own investigation). Teammates resume with full Round 1 context.

The critic role specifically asks each investigator: "Does C's synthesis fairly represent your evidence? Did C address the alternative hypotheses you ranked highly? If not, do you accept C's ranking, or do you have evidence that contradicts it? Does C's committed top hypothesis explain ALL the evidence, or only the symptom?"

### Step 7 — Round 4: Reconciliation

When both critiques arrive, `SendMessage(to="synthesizer-c", ...)` with both feedback bodies inline and `agents/inv-reconciler.md` embedded.

C produces TWO files:

- `diagnosis.md` — the deliverable
- `reconciliation-notes.md` — one line per critique: `A: <point> → ACCEPTED/PARTIAL/REJECTED — <reason>` (reduces reconciler-bias blind spot)

C is required to commit to a `convergence_status` in diagnosis.md:

- **CONVERGED** — single root cause identified with high confidence, evidence chain explains all observed symptoms
- **PARTIALLY CONVERGED** — top hypothesis is supported but meaningful uncertainty remains. diagnosis.md commits to the top hypothesis, flags alternatives, and includes a `required_data` section listing what would resolve the uncertainty.
- **INSUFFICIENT EVIDENCE** — cannot honestly commit to a cause given current evidence. diagnosis.md documents candidate hypotheses and includes a `required_data` section. The fix-pipeline orchestrator should escalate to the user or trigger data gathering before proceeding to fix-plan.

INSUFFICIENT EVIDENCE is a legitimate output, not a failure. The reconciler is explicitly instructed not to manufacture false convergence under pressure.

### Step 8 — Cleanup

```
SendMessage(to="investigator-a", message={"type": "shutdown_request", ...})  # all three in parallel
# Wait for shutdown acknowledgments (idle notifications)
TeamDelete()
```

Present `diagnosis.md` (+ `reconciliation-notes.md`) to the user.

---

## Isolation Rule (embed verbatim in investigator prompts)

```
ISOLATION RULE — strict.

Allowed reads:
- Files listed under "Source files" in this prompt
- problem-statement.md and repro.md in the current output directory
- Files listed under "Original pipeline context" (if provided) — READ-ONLY, treat as context not suspects

Forbidden reads, in the current output directory:
- investigation-A.md, investigation-B.md, investigation-C.md  (except your own investigation in Round 3)
- review-for-A.md, review-for-B.md                            (except your own review in Round 3)
- diagnosis.md, reconciliation-notes.md

Forbidden writes:
- Any file under .dev/proposals/{original-slug}/  (read-only context)

When you finish, SendMessage the team lead "team-lead" with a one-line
plain-text summary including your committed top hypothesis. Then go idle —
do NOT exit. You will be messaged again in Round 3 to critique a synthesis
of your work.
```

Round 3 adds: "Read ONLY review-for-A.md (or review-for-B.md for B) and your own investigation-A.md. Do NOT read investigation-C.md, the other investigator's review, or the other investigator's investigation."

---

## diagnosis.md Structure

```markdown
# Diagnosis — {slug}

## Convergence Status
{CONVERGED | PARTIALLY CONVERGED | INSUFFICIENT EVIDENCE}

## Environment
{prod | dev}

## Root Cause
{For CONVERGED: the committed cause, stated precisely.
 For PARTIALLY CONVERGED: the top hypothesis, with caveats.
 For INSUFFICIENT EVIDENCE: "No single cause can be committed to with current evidence. See Candidate Causes."}

## Evidence Chain
{For each step in the causal chain: what was observed, where (file:line, log reference, etc.),
 and how it connects to the next step. The chain should explain ALL observed symptoms,
 not just the most prominent one.}

## Hypotheses Considered
{Audit trail. Each hypothesis: name, evidence for, evidence against, verdict (committed/rejected/deferred), reason.}

## Candidate Causes
{Only present for PARTIALLY CONVERGED and INSUFFICIENT EVIDENCE.
 Each candidate: id, description, evidence supporting, evidence against,
 what makes it more or less likely than the others.}

## Required Data
{Only present for PARTIALLY CONVERGED and INSUFFICIENT EVIDENCE.
 Structured list of additional data that would distinguish remaining hypotheses:}

```yaml
required_data:
  - id: D1
    distinguishes: [hypothesis-2, hypothesis-3]
    description: "Production logs from auth service for the 5-minute window around the reported failure timestamp"
    source: "Railway logs / production"
    blocking_severity: high
  - id: D2
    distinguishes: [hypothesis-1, hypothesis-2]
    description: "Whether refresh-token expiry was extended in last deployment"
    source: "Git history / config diff"
    blocking_severity: medium
```

`blocking_severity` is one of `high | medium | low`:
- `high` — fix-pipeline must not advance to fix-plan without this data
- `medium` — fix-plan can proceed but should be revisited if this data later contradicts the chosen plan
- `low` — nice-to-have for completeness; does not gate downstream stages

## Symptom vs Cause Analysis
{Explicit answer to: was the visible error a symptom of a deeper cause? If yes, what's upstream of where it manifests? If no, why is the visible error itself the cause?}

## Confidence + Blocking Items
CONFIDENCE: HIGH | MEDIUM | LOW
BLOCKING_ITEMS: N
```

---

## Bias Mitigations

**Anchoring bias (investigator latches onto first plausible hypothesis).**
Investigator role file requires ≥2 alternative hypotheses, even when a leading candidate exists. Synthesizer role requires evaluating all hypotheses, not just top one.

**Symptom-confirmation bias (investigator chases the visible error).**
Investigator role file requires explicit answer to: "could the visible error be a symptom of a deeper cause? What's upstream of where it manifests?"

**Recency bias from original pipeline artifacts.**
Original pipeline artifacts are framed as CONTEXT, NOT SUSPECTS in investigator prompts. The investigator role file is explicit: read them to understand what was built, not as a list of likely causes.

**Reconciler bias toward own synthesis.**
Same mechanism as adversarial-proposal:
- `agents/inv-reconciler.md` includes steelman / perspective-test guidance
- `reconciliation-notes.md` (required) forces explicit accept/partial/reject for every critique point
- `--interactive` mode lets user review investigation-C.md before Round 3 critiques

**Forced-convergence bias (reconciler manufactures a cause to look decisive).**
Reconciler role explicitly authorizes INSUFFICIENT EVIDENCE as a legitimate output. The reconciler is instructed not to commit to a cause if the evidence doesn't support it.

---

## Known Tradeoffs

**Lead context pressure.** Orchestration across many rounds can fill the lead's window. Stay disciplined: the lead doesn't re-read source files (the teammates do). The lead reads problem-statement.md, repro.md, the three synthesis artifacts, and the final diagnosis. Everything else is teammate-scoped.

**Strategy pair can mismatch.** If the bug doesn't fit a clear forward-trace/backward-trace shape (for example, a timing-dependent issue with no clear "trigger" or "failure point"), the strategy pair may converge prematurely or both miss the cause. The investigator role includes an escape hatch: "if the assigned strategy doesn't fit, note why and investigate naturally — but explain your reasoning."

**Prod investigation is harder.** Without the ability to run code or add logging, prod investigators rely entirely on existing telemetry. If telemetry is sparse, INSUFFICIENT EVIDENCE will be the honest outcome. The skill does not penalize this — it surfaces it.

---

## Invoking the Skill

### Direct prompt

```
/adversarial-investigate \
  --output-dir .dev/fixes/jwt-refresh-tokens/auth-token-refresh-race/ \
  --env prod \
  --original-slug jwt-refresh-tokens
```

For an interactive run:

```
/adversarial-investigate \
  --output-dir .dev/fixes/.../... \
  --env dev \
  --original-slug ... \
  --interactive
```

### As a Claude Code command

`~/.claude/commands/adversarial-investigate.md`:

```
Read .claude/skills/adversarial-investigate/SKILL.md and
.claude/skills/adversarial-investigate/references/agent-teams-workflow.md,
then orchestrate the workflow.

Arguments: $ARGUMENTS
```

If `$ARGUMENTS` is empty or missing required flags, follow Step 0A (empty-args short-circuit).

---

## Output Directory

```
{output_dir}/
├── problem-statement.md         ← Input (from orchestrator)
├── repro.md                     ← Input (from fix-reproducer, if it ran)
├── investigation-A.md           ← Round 1: Forward-from-trigger
├── investigation-B.md           ← Round 1: Backward-from-failure-point
├── investigation-C.md           ← Round 2: Synthesis (lead-only; absent on convergence)
├── review-for-A.md              ← Round 2: A's eyes only (absent on convergence)
├── review-for-B.md              ← Round 2: B's eyes only (absent on convergence)
├── reconciliation-notes.md      ← Round 4: ACCEPT/PARTIAL/REJECT audit trail (absent on convergence)
└── diagnosis.md                 ← Round 4: FINAL DELIVERABLE
```

The presence or absence of `investigation-C.md` + reviews is the structural signal of which path ran:
- **Full run**: all files present
- **Convergence run**: investigation-A/B + diagnosis.md only

---

## Reference Files

| File | When to read | Purpose |
|---|---|---|
| `references/agent-teams-workflow.md` | Before orchestrating | Exact tool-call sequence for all rounds |
| `references/investigation-format.md` | When building investigator/synthesizer prompts | Investigation template to embed |
| `references/diagnosis-format.md` | When building reconciler prompts | Final diagnosis template (matches diagnosis.md structure above) |
| `references/subagent-fallback.md` | Only if Teams is unavailable | Task-based fallback with file coordination |
| `agents/investigator.md` | Building A/B prompts | Investigation role + strategy pair + bias mitigations |
| `agents/inv-synthesizer.md` | Building C's Round 2 prompt | Synthesis role with 3-file output spec + tentative convergence assessment |
| `agents/inv-critic.md` | Building Round 3 messages | Critique guidance (defend, accept, or counter the synthesis) |
| `agents/inv-reconciler.md` | Building Round 4 message | Bias-aware reconciliation + diagnosis.md spec + convergence status authorization |

## Agent Roles Summary

| Agent | File | Used in | Purpose |
|---|---|---|---|
| Investigator | `agents/investigator.md` | Round 1 (A, B) | Independent investigation with assigned strategy + anti-bias requirements |
| Synthesizer | `agents/inv-synthesizer.md` | Round 2 (C) | Independent exam, synthesis, isolated review files, tentative convergence status |
| Critic | `agents/inv-critic.md` | Round 3 (A, B) | Review own isolated critique, defend or concede on grounds of evidence |
| Reconciler | `agents/inv-reconciler.md` | Round 4 (C) | Bias-aware reconciliation + final diagnosis + convergence status (including INSUFFICIENT EVIDENCE) |
