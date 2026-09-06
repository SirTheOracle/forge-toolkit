---
name: adversarial-fix-qa
description: >
  Automated adversarial QA framework for fix-pipelines using Claude Code Agent
  Teams. Spawns 3 persistent teammates (QA Confirmer A, Regression Hunter B,
  Synthesizer C) to verify a fix through 4 rounds of isolated testing,
  synthesis, critique, and reconciliation. A confirms the fix actually
  resolves the diagnosed bug under all conditions described in repro/diagnosis;
  B hunts for regressions in adjacent and downstream code affected by the fix;
  C synthesizes; A and B critique C from their original contexts; C
  reconciles into final fix-issues.md and fix-manifest.yaml. Trusts the
  upstream diagnosis, plan, and code — does NOT re-investigate or re-fix.
  Trigger as the QA stage of a fix-pipeline, after fix-coder has produced a
  committed fix.
---

# Adversarial Fix QA Framework

## Overview

Three persistent teammates verify a fix through 4 rounds. The two QA agents have **different mandates** — A confirms the fix works against the original failure; B hunts for regressions in adjacent code. They are not investigating the same thing, so they will never naturally "converge" — both produce findings independently, and the synthesizer consolidates rather than reconciles competing answers.

This skill differs from `adversarial-qa` in three ways:

1. **Strategy pair is dual-mandate, not problem-shaped.** A confirms the known-bad path is now good; B hunts for new bad paths in dependent code. They share evidence but answer different questions.
2. **Convergence path doesn't apply.** A and B answer different questions, so they cannot meaningfully agree or disagree on a single answer. Both findings are always consolidated.
3. **Four-outcome verdict.** PASS / FIX FAILED / REGRESSION / BOTH. The orchestrator's routing depends on which is signaled.

The full tool-call sequence lives in `references/agent-teams-workflow.md`. **Read that file before orchestrating.** This document is the high-level guide.

## When to Use

- After `fix-coder` has produced a committed fix with status SUCCESS or PARTIAL
- Before `adversarial-fix-verify` (which validates the QA findings independently)

**Expected runtime**: ~30–60 min — 4 rounds of Agent Teams, two of which include live test execution. Dev runs longer than prod due to repro re-execution; large dependent surfaces extend Round 1 B.

**Worker routing.** Claude Code lead only. Agent Teams is a Claude-Code-exclusive feature; this skill cannot run on Codex. If Teams is unavailable, fall back to `references/subagent-fallback.md`.

## When NOT to Use

- **fix-coder reported FAILED or STOPPED.** No committed fix to QA. Orchestrator should re-dispatch revise or escalate.
- **fix-coder reported STILL REPRODUCES.** The fix didn't fix; QA can't help. Orchestrator should re-investigate.
- **Build-pipeline QA.** Use `adversarial-qa`.

## Hard Rules

1. **Trust upstream artifacts.** This skill does not re-investigate, re-plan, re-code, or modify any source files. If A or B encounters evidence the diagnosis or plan was fundamentally wrong, they flag it as a CRITICAL issue and stop. The orchestrator decides whether to re-run upstream stages.
2. **Both agents must produce findings, even if "clean."** Empty issue lists are valid; silent skip is forbidden.
3. **Fix Confirmation requires repro re-run when feasible.** Agent A cannot rely solely on "the test passes." For dev, A must actually re-execute the repro from repro.md. For prod, A confirms the failing-path test exercises the failing path AND passes.
4. **Regression Hunting must be evidence-based.** Agent B must identify specific dependents from fix-diffs.md and exercise them. "I checked some adjacent stuff and it looks fine" is not acceptable. Each regression check must cite the file/dependent it tested.
5. **Always write fix-issues.md and fix-manifest.yaml.** Even on PASS verdict, even on early-stop paths (Step 0B), even when teammates fail. If a teammate fails to produce a required artifact after one retry, the lead writes stub fix-issues.md and fix-manifest.yaml with `verdict: BLOCKED`, a `stop_reason`, and the list of missing artifacts. Silent exit is forbidden.
6. **No code changes.** This skill validates; it does not modify source code, fix-plan.md, fix-diffs.md, or any artifact except fix-issues.md, fix-manifest.yaml, reconciliation-notes.md, qa-A.md, qa-B.md, qa-C.md, review-for-A.md, review-for-B.md, and content under `evidence/`.
7. **Prod QA is honest about its limits.** For `--env prod`, fix-qa cannot directly verify the deployed prod state. The verdict acknowledges "ready for verification post-deploy" rather than "fix works in prod." fix-verify handles the post-deploy check.
8. **No silent drops of re-emerged advisories.** If fix-review.md is present, the synthesizer must check whether any previously-flagged ADVISORY item is now an active QA finding. Re-emerged advisories must be cited in fix-issues.md (severity reassigned per QA evidence). Silently dropping a re-emerged item is forbidden.

## Prerequisites

Requires Claude Code Agent Teams:

```bash
{ "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
```

Takes effect on new Claude Code sessions. If Teams is not available, fall back to `references/subagent-fallback.md`.

## Modes

| Mode | Flag | Behavior |
|---|---|---|
| **Default** | — | Fully automated. Lead orchestrates all rounds without pausing. |
| **Interactive** | `--interactive` | Pauses after Round 1 (user reviews qa-A/qa-B) and after Round 2 (user reviews qa-C). |

## Inputs

Required:

- `--output-dir` — path to the fix directory. fix-coder-report.md, fix-diffs.md, fix-plan.md, diagnosis.md, repro.md (if present) all live here. fix-issues.md and fix-manifest.yaml will be written here.
- `--env prod | dev` — affects QA strategy. Dev allows live execution; prod is test-against-local-fix only.

Optional:

- `--original-slug` — slug of the original build pipeline. Enables read-only context access to that pipeline's artifacts (helpful for understanding the wider feature surface for regression hunting).
- `--interactive` — pause at Round 1 and Round 2 checkpoints for user review.

## Architecture

```
ROUND 0  Setup: read inputs → check fix-coder-report status → strategy assignment
                → team → tasks
ROUND 1  qa-confirmer-a + regression-hunter-b run in parallel (isolated)
           A: Fix Confirmation — verify fix resolves the diagnosed bug
           B: Regression Hunting — verify nothing adjacent or downstream broke
           ─► qa-A.md, qa-B.md
         ▼  quality gate + (interactive) checkpoint 1
ROUND 2  synthesizer-c reads both → produces 3 artifacts
           ─► qa-C.md, review-for-A.md, review-for-B.md
         ▼  artifact gate + (interactive) checkpoint 2
ROUND 3  A and B resume from Round 1 context, each reads ONLY its own
         review file → SendMessage feedback back to lead
ROUND 4  synthesizer-c resumes → reconciles both critiques
           ─► reconciliation-notes.md + fix-issues.md + fix-manifest.yaml
ROUND 5  shutdown all teammates, TeamDelete, present to user
```

### Strategy pair (dual mandate)

| Agent | Strategy | Focus | Catches |
|---|---|---|---|
| **A** | Fix Confirmation | Verify the fix resolves the diagnosed bug under all conditions described in repro/diagnosis | "Fix worked in the happy case but not corner case from repro.md"; "Fix worked once but isn't deterministic"; "Fix addresses symptom but not full diagnosed cause"; "Failing-path test passes but doesn't actually exercise the failing path" |
| **B** | Regression Hunting | Verify nothing adjacent or downstream broke as a result of the fix | "Adjacent feature now fails"; "Edge case in the same module regressed"; "Consumer of the changed API now misbehaves"; "Test that previously passed now fails for an unrelated reason" |

These mandates are different, not opposed. Both agents will run tests, exercise code, and capture evidence — but A's terminating criterion is "I have established whether the fix works for the diagnosed cause," and B's is "I have established whether anything broke in code that depends on or is adjacent to the changes."

### Why isolation works

Each teammate has its own context window. A and B share no conversation history. All communication goes through the lead. Round 3 isolation is maintained via per-agent review files.

---

## How to Execute

You (the lead) orchestrate everything. The exact tool-call sequence is in `references/agent-teams-workflow.md`. Below is the orchestration checklist.

### Step 0 — Gather context, validate preconditions

**0A. Empty-args short-circuit.** If `--output-dir` is missing, `--env` is missing, or `fix-coder-report.md` does not exist, stop:

> "This skill requires --output-dir, --env (one of `prod` | `dev`), and an existing fix-coder-report.md in that directory. Provide --output-dir, --env, and ensure fix-coder has run."

Write the early-stop pair (fix-issues.md + fix-manifest.yaml — see "Early-stop artifact pair" below).

**0B. Read fix-coder-report.md and check Status (defense in depth).** Parse the `Status:` field.

- **SUCCESS or PARTIAL** → proceed
- **FAILED with `Fix Validation: STILL REPRODUCES`** → STOP. Stop reason: "fix-coder-report.md indicates STILL REPRODUCES. The fix did not work. QA cannot proceed against a non-functional fix. Orchestrator should re-run adversarial-investigate or escalate."
- **FAILED (other) or STOPPED** → STOP. Stop reason: "fix-coder-report.md status is {FAILED|STOPPED}. No committed fix exists to QA. Orchestrator should re-dispatch revise mode or escalate."
- **Status missing, malformed, or any value not listed above** → STOP. Treat as worst-case. Stop reason: "fix-coder-report.md Status field is missing or unparseable. Cannot safely proceed; defense in depth requires a parseable upstream status."

For every STOP path in 0B, write the early-stop artifact pair and exit. Do not spawn teammates.

**Early-stop artifact pair.** Whenever Step 0 stops (0A or 0B), write BOTH artifacts:

`fix-issues.md`:
```markdown
# Fix QA — STOPPED ({fix-slug})

## Verdict
BLOCKED

## Stop Reason
{the reason from above}

## Confidence + Blocking Items
CONFIDENCE: HIGH
BLOCKING_ITEMS: 1
```

`fix-manifest.yaml`:
```yaml
slug: {fix-slug}
env: {prod | dev | unknown}
verdict: BLOCKED
prod_qualification: null
fix_confirmation:
  result: NOT_RUN
  method: not_applicable
counts: {critical: 0, blocking: 0, advisory: 0}
issues: []
dependents_checked: []
confidence: HIGH
blocking_items: 1
stop_reason: "{the reason from above}"
```

The orchestrator may parse the manifest unconditionally; both artifacts are required even on Step 0 stops.

**0C. Read inputs from `{output_dir}`:**

- `fix-coder-report.md` — required (status + commit + validation claims)
- `fix-diffs.md` — required (the concrete changes for B's dependent analysis)
- `fix-plan.md` — required (intent and test plan)
- `diagnosis.md` — required (what the cause was supposed to be)
- `problem-statement.md` — required (context)
- `repro.md` — if present (A's primary input for fix confirmation)
- `fix-review.md` — if present (context for what was previously flagged; required for Hard Rule 8 re-emerged-advisory check)
- `.claude/forge-project.yml` (fallback: `~/.claude/forge-project.yml`) — if present, used by B for test commands. Optional; agents may also infer commands from project context.

**0D. Read original pipeline artifacts (if `--original-slug`):**

Read-only context from `.dev/proposals/{original-slug}/`. These help B understand the wider feature surface — what other code paths exist that depend on or interact with the changed code.

**0E. Confirm `--env`.** Affects A's confirmation strategy:

- **dev**: A re-executes repro.md steps against the local app
- **prod**: A confirms the failing-path test exercises and passes (cannot hit prod)

And affects B's regression strategy:

- **dev**: B can run live tests, exercise edge cases via Playwright/CLI/etc.
- **prod**: B is limited to running the local test suite against the fixed code

**0F. Exploration budget.** Lead reads at most 3-5 files to orient. The QA agents re-read what they need independently.

### Step 1 — Team setup

```
TeamCreate(team_name="fixqa-<short-slug>", description=<short>)
# Create 5 tasks (A, B, C, critique, reconcile) with dependency chain
```

Details in `references/agent-teams-workflow.md` §"Round 0".

### Step 2 — Round 1: Spawn A and B in parallel (same turn)

Two `Agent(team_name=..., name="qa-confirmer-a", ...)` and `Agent(name="regression-hunter-b", ...)` calls in a single message. Each prompt embeds:

**Agent A (Fix Confirmer) prompt:**
- `agents/fix-qa-confirmer.md` (the role)
- `references/qa-working-format.md` (the working artifact format — authoritative)
- diagnosis.md, repro.md (if present), fix-plan.md, fix-coder-report.md, fix-diffs.md
- Env mode and what confirmation strategy is permitted
- Output path: `{output_dir}/qa-A.md`
- Isolation rule (see below)
- Required actions:
  - Re-execute repro.md steps (dev) or confirm failing-path test exercises and passes (prod)
  - Verify fix addresses the FULL diagnosed cause, not just the visible symptom
  - Test variations from repro.md's "Variations Attempted" — do they trigger now?
  - Test edge cases derived from diagnosis.md's evidence chain
  - Verify fix is deterministic — multiple runs produce consistent results

**Agent B (Regression Hunter) prompt:**
- `agents/fix-qa-regression.md` (the role)
- Same artifacts
- Output path: `{output_dir}/qa-B.md`
- Isolation rule
- Required actions:
  - Read fix-diffs.md and identify every file modified
  - For each modified file, identify dependents (importers, callers, consumers) — must be explicit, not hand-waved
  - Run existing tests for each dependent
  - Identify edge cases in the modified code that the fix might have inadvertently changed
  - Run the project's full test suite and compare against fix-coder-report's baseline
  - Cite every dependent checked, with the result

Then the lead idles.

### Step 3 — Gates after Round 1

**Artifact gate (required, run first)**: verify on disk that `qa-A.md` and `qa-B.md` both exist and are non-empty. If either is missing or empty, `SendMessage(to="qa-confirmer-a|regression-hunter-b", message="Your QA report at {path} is missing or empty. Write your full report to that path before signaling done.")`. Do not proceed to the quality gate until both files exist on disk.

**Quality gate**: each QA report has required sections per `references/qa-working-format.md` — strategy followed, actions performed (with citations), findings, evidence. Failure → `SendMessage(to="qa-confirmer-a|regression-hunter-b", message="revise: <specific>")`. One retry; if it still fails, surface to user.

**No convergence check.** A and B answer different questions; they don't converge. Both findings always proceed to synthesis.

**Critical-finding short-circuit (operational detail)**: parse each report's findings. If qa-A.md has any finding with severity CRITICAL, OR qa-B.md has any finding with severity CRITICAL, record this fact for the Step 4 synthesizer prompt. The synthesizer prompt MUST begin with a leading line:

```
CRITICAL_FROM_AGENT: A | B | both — <one-line summary of each CRITICAL finding>
```

The synthesizer must surface this verbatim in qa-C.md and may not downgrade severity without explicit, evidence-cited justification in qa-C.md's "Severity Decisions" section. If no agent flagged CRITICAL, the leading line reads:

```
CRITICAL_FROM_AGENT: none
```

**Optional fast path (both clean)**: if A.result = CONFIRMED with no findings AND B has no findings AND CRITICAL_FROM_AGENT is `none`, the synthesizer may skip Phase 2 cross-verification in Round 2 and proceed directly to consolidation. This is an optimization only — severity assignment, verdict, and Round 4 reconciliation still proceed normally.

**(Interactive) checkpoint 1**: pause for user review of qa-A/qa-B.

### Step 4 — Round 2: Synthesizer C

Spawn `synthesizer-c` teammate. Prompt embeds `agents/fix-qa-synthesizer.md` and instructs:

- Phase 0: read fix-coder-report.md, fix-diffs.md, fix-plan.md, diagnosis.md directly
- Phase 1: read qa-A.md and qa-B.md
- Phase 2: cross-verify the most critical findings from each — re-run the test or check the dependent if feasible
- Phase 3: synthesize into qa-C.md with a tentative verdict (PASS / FIX FAILED / REGRESSION / BOTH) and severity-classified findings
- Phase 4: write isolated review files for A and B

The synthesizer's job is consolidation more than reconciliation. A and B aren't disagreeing — they're answering different questions. The synthesizer determines:
- Which findings are real (cross-verify when possible)
- Severity assignment per finding
- The overall verdict given the findings

### Step 5 — Round 2 artifact gate (required)

Before Round 3, **verify on disk**:
- `qa-C.md` exists and is non-empty
- `review-for-A.md` exists and is non-empty
- `review-for-B.md` exists and is non-empty

If any is missing or empty, `SendMessage(to="synthesizer-c", message="<name the missing/empty files and the required content per qa-working-format / fix-issues-format>")`. **One retry; if it still fails, surface to user with the list of missing artifacts and stop.** Do not proceed with partial artifacts.

**(Interactive) checkpoint 2**: pause for user review of qa-C.md.

### Step 6 — Round 3: Critique (pinned teammate descriptions)

Two `SendMessage` calls in one turn:
- `"Confirmer A: Round 3 feedback on qa-A"`
- `"Hunter B: Round 3 feedback on qa-B"`

Each message embeds `agents/fix-qa-critic.md` and the per-agent isolation rule. Teammates resume with full Round 1 context.

The critic role asks each agent: "Did C's synthesis fairly represent your findings? Did C downgrade severity inappropriately on any of your findings? Is C's verdict consistent with the evidence you gathered?"

### Step 7 — Round 4: Reconciliation

When both critiques arrive, `SendMessage(to="synthesizer-c", ...)` with both feedback bodies inline and `agents/fix-qa-reconciler.md` embedded.

C produces THREE files:

- `fix-issues.md` — the deliverable (consolidated findings + verdict)
- `fix-manifest.yaml` — structured data for orchestrator consumption
- `reconciliation-notes.md` — one line per critique: `A: <point> → ACCEPTED/PARTIAL/REJECTED — <reason>`

### Step 7.5 — Output Quality Gate (required, before cleanup)

Before signaling completion, verify the three written artifacts on disk:

**File-existence checks:**
1. `{output_dir}/fix-issues.md` exists and is non-empty
2. `{output_dir}/fix-manifest.yaml` exists and is non-empty
3. `{output_dir}/reconciliation-notes.md` exists and is non-empty

**Structure checks on fix-issues.md** (sections in order, per `references/fix-issues-format.md`):
- Verdict
- Environment
- Fix Confirmation Result (Agent A)
- Regression Findings (Agent B)
- Critical Issues
- Blocking Issues
- Advisory Issues
- Re-emerged Advisories (header must exist; empty body permitted ONLY if fix-review.md absent or had no ADVISORY items — Hard Rule 8)
- Dependents Checked (Agent B audit)
- Confidence + Blocking Items (last two lines exactly)

**Last-two-lines check:** the last two lines of fix-issues.md are exactly `CONFIDENCE: {HIGH|MEDIUM|LOW}` and `BLOCKING_ITEMS: {N}`.

**Manifest schema check:** `fix-manifest.yaml` validates against `references/fix-manifest-schema.yaml` — every required field present with an allowed value.

**Verdict-vs-counts consistency** (defense in depth):

| Verdict | Required signals |
|---|---|
| PASS | `counts.critical = 0` AND `counts.blocking = 0`; `fix_confirmation.result` = CONFIRMED (or PARTIAL with explicit dev-vs-prod note); B reports CLEAN |
| FIX_FAILED | `fix_confirmation.result` = FAILED → at least one CRITICAL from A |
| REGRESSION | `fix_confirmation.result` = CONFIRMED AND B has ≥1 BLOCKING-or-CRITICAL regression |
| BOTH | `fix_confirmation.result` = FAILED AND B has ≥1 BLOCKING-or-CRITICAL regression |
| BLOCKED | early-stop or stub path only — never produced from a completed Round 4 |

**Verdict-match check:** the Verdict in fix-issues.md must equal the `verdict` field in fix-manifest.yaml.

**Severity-to-verdict mapping** (refined — applies during reconciliation and is enforced here):

| Severity | Source | Verdict Impact |
|---|---|---|
| CRITICAL | A only | FIX_FAILED |
| CRITICAL | B only | REGRESSION |
| CRITICAL | A and B | BOTH |
| BLOCKING | A only | Stays PASS-with-qualification UNLESS A's finding indicates the fix does not address the full diagnosed cause — then escalates to FIX_FAILED |
| BLOCKING | B only | REGRESSION |
| BLOCKING | A and B | escalates to BOTH if A's BLOCKING is full-cause-coverage; otherwise REGRESSION |
| ADVISORY | any | no verdict change |

**Defense in depth on the verdict field:** if the verdict in fix-manifest.yaml is missing, malformed, or any value other than PASS / FIX_FAILED / REGRESSION / BOTH / BLOCKED, treat as BOTH (worst case) and surface to user.

**On gate failure:** `SendMessage(to="synthesizer-c", message="<specific gap, with section name and what's missing>")`. **One retry; if it still fails, write the stub artifact pair (per Hard Rule 5) and surface to user.**

Once all checks pass, proceed to Step 8.

### Step 8 — Cleanup

```
SendMessage(to="qa-confirmer-a", message={"type": "shutdown_request", ...})  # all three in parallel
TeamDelete()
```

Present `fix-issues.md` (+ `fix-manifest.yaml` + `reconciliation-notes.md`) to the user.

---

## Isolation Rule (embed verbatim in QA prompts)

```
ISOLATION RULE — strict.

Allowed reads:
- Files listed under "Source files" in this prompt
- diagnosis.md, problem-statement.md, repro.md, fix-plan.md, fix-coder-report.md,
  fix-diffs.md in the current output directory
- Files listed under "Original pipeline context" (if provided) — READ-ONLY

Forbidden reads, in the current output directory:
- qa-A.md, qa-B.md, qa-C.md  (except your own report in Round 3)
- review-for-A.md, review-for-B.md  (except your own review in Round 3)
- fix-issues.md, fix-manifest.yaml, reconciliation-notes.md

Forbidden writes:
- Any source code file
- Any artifact other than your own qa-A.md or qa-B.md (and evidence/ subdirectory)
- Any file under .dev/proposals/{original-slug}/  (read-only context)

DO NOT re-investigate, re-plan, or attempt to fix issues you find. The diagnosis,
plan, and code are authoritative. Your job is to verify and report findings.
If you find the fix is fundamentally broken, flag it as a CRITICAL issue.

When you finish, SendMessage the team lead "team-lead" with a one-line plain-text
summary of your finding count by severity. Then go idle — do NOT exit.
```

---

## Verdict Structure

```yaml
verdict: PASS | FIX_FAILED | REGRESSION | BOTH | BLOCKED
```

- **PASS** — fix confirmed (or ready for post-deploy verification on prod), no regressions found, no CRITICAL or BLOCKING issues
- **FIX_FAILED** — A established the fix does NOT resolve the diagnosed bug. Severity = CRITICAL. Orchestrator should escalate or re-investigate.
- **REGRESSION** — A established the fix works, but B found regressions. Severity at least BLOCKING. Orchestrator should re-dispatch revise mode.
- **BOTH** — A established the fix doesn't work AND B found regressions. The worst outcome. Orchestrator should escalate to user.
- **BLOCKED** — early-stop path only (Step 0A/0B trips, or Round 4 produced no parseable artifacts after one retry). Never produced from a completed Round 4. Orchestrator should treat as "QA could not run" and route per `stop_reason`.

For `--env prod`, PASS verdict carries an explicit qualification: "ready for verification post-deploy" — fix-verify is responsible for the actual prod confirmation.

## Severity Levels

The full severity-to-verdict mapping (refined per attack source) lives in Step 7.5 "Severity-to-verdict mapping." High-level definitions:

| Severity | Meaning |
|---|---|
| **CRITICAL** | Fix doesn't work (any A finding), or regression breaks a core feature |
| **BLOCKING** | Fix has gaps in coverage (A) or regression breaks a non-core feature (B) |
| **ADVISORY** | Minor issues worth flagging but not blocking |

When uncertain between two severities, choose the higher.

---

## fix-issues.md Structure

The block below is a preview for orientation. The authoritative format is `references/fix-issues-format.md`; if the two diverge, the reference file wins.

```markdown
# Fix QA — {slug}

## Verdict
{PASS | FIX_FAILED | REGRESSION | BOTH}
{For prod PASS: "Ready for verification post-deploy."}

## Environment
{prod | dev}

## Fix Confirmation Result (Agent A)
{One of:
 - CONFIRMED: fix resolves the diagnosed bug under all tested conditions
 - PARTIAL: fix resolves the bug in some conditions but not all (specify)
 - FAILED: fix does NOT resolve the diagnosed bug (this drives FIX_FAILED verdict)}

Evidence:
- {test or repro execution → result}
- {variation tested → result}
- {edge case from diagnosis evidence → result}

## Regression Findings (Agent B)
{One of:
 - CLEAN: no regressions identified
 - REGRESSIONS FOUND: list of dependents that broke (severity-rated)}

For each regression:
- **Where**: file/test/feature affected
- **What**: how it manifests
- **Why It Matters**: severity rationale
- **Evidence**: test name, error output, evidence/ path

## Critical Issues
{Any finding with CRITICAL severity. Empty section if none.}

## Blocking Issues
{Any finding with BLOCKING severity. Empty section if none.}

## Advisory Issues
{Any finding with ADVISORY severity. Empty section if none.}

## Re-emerged Advisories
{For each ADVISORY in fix-review.md (if present): one-line summary, then disposition:
 "still observed in QA (now severity X)" | "no longer observed" | "out of QA scope (cite reason)".
 Empty body permitted ONLY if fix-review.md absent or had no ADVISORY items. Hard Rule 8.}

## Dependents Checked (Agent B audit)
{Required: explicit list of dependents identified from fix-diffs.md and the
 result of checking each. Forces evidence-based regression hunting.}

| Dependent | Type | Result |
|---|---|---|
| <file/test/feature> | <importer | caller | adjacent | full-suite> | <pass | fail | not-applicable> |

## Confidence + Blocking Items
CONFIDENCE: HIGH | MEDIUM | LOW
BLOCKING_ITEMS: N

(BLOCKING_ITEMS = count of CRITICAL + BLOCKING. ADVISORY does not count toward blocking.)
```

## fix-manifest.yaml Structure

The block below is a preview for orientation. The authoritative schema is `references/fix-manifest-schema.yaml`; if the two diverge, the schema file wins.

```yaml
slug: {fix-slug}
original_slug: {original-slug}
env: prod | dev
verdict: PASS | FIX_FAILED | REGRESSION | BOTH
prod_qualification: ready_for_post_deploy_verification | null
fix_confirmation:
  result: CONFIRMED | PARTIAL | FAILED
  method: repro_re_executed | failing_path_test | both
counts:
  critical: N
  blocking: N
  advisory: N
issues:
  - id: I1
    agent: A | B | C
    severity: CRITICAL | BLOCKING | ADVISORY
    category: fix_confirmation | regression | coverage_gap
    summary: "<one line>"
    evidence_path: "evidence/<path>"
dependents_checked:
  - name: "<dependent>"
    type: importer | caller | adjacent | full_suite
    result: pass | fail | not_applicable
confidence: HIGH | MEDIUM | LOW
blocking_items: N
```

The manifest is the structured signal the orchestrator reads to decide routing.

---

## Bias Mitigations

**Symptom-only confirmation (A confirms the visible symptom is fixed but misses that the diagnosed cause is broader).**
A's role file requires explicit answer to: "does the fix address the FULL diagnosed cause, or only the most visible symptom?" Forces the broader question.

**Test-passes-equals-fix-works (A trusts the test runs without verifying it actually exercises the failing path).**
A's role file requires explicit verification that the failing-path test actually exercises the failing logic. "I read the test and confirmed it reaches line X where the bug originally occurred."

**Hand-waved regression hunting (B says "I checked some adjacent stuff" without specifics).**
B's role file requires citing every dependent checked, what type of dependent it is, and the result. The Dependents Checked table in fix-issues.md is mandatory.

**Test-suite-passes-equals-no-regressions (B trusts the full test suite without considering coverage gaps).**
B's role file requires identifying dependents NOT covered by existing tests, and noting them as coverage gaps even when the existing tests pass.

**Synthesizer downgrade (C softens severity to fit a cleaner narrative).**
Same mechanism as adversarial-fix-plan: reconciliation-notes.md forces explicit accept/partial/reject for every critique point. The critic role asks each agent specifically about severity changes.

**Prod overconfidence.**
For `--env prod`, PASS verdicts carry the "ready for verification post-deploy" qualification. The skill is explicit that prod fix-qa cannot fully verify a deployed fix.

---

## Known Tradeoffs

**A and B don't naturally converge.** This is by design — they answer different questions. The synthesizer's job is consolidation, not reconciliation. The convergence-path optimization from adversarial-proposal/investigate doesn't apply.

**Prod confirmation is weaker than dev confirmation.** A's strongest verification (re-executing the repro) isn't possible for prod-only bugs. The skill is honest about this; fix-verify is responsible for closing the gap post-deploy.

**Regression hunting is bounded by what dependents can be identified.** B looks at imports, callers, and project structure — but if a dependency relationship isn't expressed in code (e.g. runtime configuration, external service contract), B may miss it. The Dependents Checked table makes the boundary visible.

**Lead context pressure.** Same discipline as previous Agent Teams skills — lead reads the synthesis artifacts; teammates re-read source files independently.

---

## Invoking the Skill

### Direct prompt

```
/adversarial-fix-qa \
  --output-dir .dev/fixes/jwt-refresh-tokens/auth-token-refresh-race/ \
  --env prod \
  --original-slug jwt-refresh-tokens
```

For an interactive run:

```
/adversarial-fix-qa \
  --output-dir .dev/fixes/.../... \
  --env dev \
  --original-slug ... \
  --interactive
```

### As a Claude Code command

`~/.claude/commands/adversarial-fix-qa.md`:

```
Read .claude/skills/adversarial-fix-qa/SKILL.md and
.claude/skills/adversarial-fix-qa/references/agent-teams-workflow.md,
then orchestrate the workflow.

Arguments: $ARGUMENTS
```

---

## Output Directory

```
{output_dir}/
├── problem-statement.md         ← Input
├── repro.md                     ← Input (if present)
├── diagnosis.md                 ← Input
├── fix-plan.md                  ← Input
├── fix-review.md                ← Input (if present)
├── fix-diffs.md                 ← Input
├── fix-coder-report.md          ← Input
├── qa-A.md                      ← Round 1: Fix Confirmation
├── qa-B.md                      ← Round 1: Regression Hunting
├── qa-C.md                      ← Round 2: Synthesis
├── review-for-A.md              ← Round 2: A's eyes only
├── review-for-B.md              ← Round 2: B's eyes only
├── reconciliation-notes.md      ← Round 4: Audit trail
├── fix-issues.md                ← Round 4: FINAL DELIVERABLE
├── fix-manifest.yaml            ← Round 4: Structured signal for orchestrator
└── evidence/
    └── post-fix/                ← QA evidence (test runs, repro re-executions, regression findings)
```

---

## What This Skill Does NOT Do

- Does not modify source code, fix-plan.md, fix-diffs.md, or any upstream artifact
- Does not re-investigate, re-plan, or attempt to fix issues found
- Does not naturally converge A and B (they answer different questions by design)
- Does not skip the Dependents Checked audit (regression hunting must cite specific dependents)
- Does not use convergence-path optimization (not applicable)
- Does not over-claim prod verification (PASS for prod is qualified)

## Reference Files

| File | When to read | Purpose |
|---|---|---|
| `references/agent-teams-workflow.md` | Before orchestrating | Exact tool-call sequence for all rounds |
| `references/qa-working-format.md` | When building qa-confirmer/regression-hunter prompts | Working artifact template (authoritative for qa-A.md and qa-B.md) |
| `references/fix-issues-format.md` | When building reconciler prompt | Final deliverable template (authoritative) |
| `references/fix-manifest-schema.yaml` | When building reconciler prompt | Manifest schema (authoritative) |
| `references/subagent-fallback.md` | Only if Teams is unavailable | Task-based fallback |
| `agents/fix-qa-confirmer.md` | Building A's prompt | Fix confirmation role + bias mitigations |
| `agents/fix-qa-regression.md` | Building B's prompt | Regression hunting role + dependents-citation requirement |
| `agents/fix-qa-synthesizer.md` | Building C's Round 2 prompt | Synthesis role with 3-file output spec |
| `agents/fix-qa-critic.md` | Building Round 3 messages | Critique guidance (defend severity, defend findings) |
| `agents/fix-qa-reconciler.md` | Building Round 4 message | Bias-aware reconciliation + final deliverable spec |

## Agent Roles Summary

| Agent | File | Used in | Purpose |
|---|---|---|---|
| Fix Confirmer | `agents/fix-qa-confirmer.md` | Round 1 (A) | Verify fix resolves diagnosed bug under all conditions |
| Regression Hunter | `agents/fix-qa-regression.md` | Round 1 (B) | Verify nothing adjacent or downstream broke; cite all dependents checked |
| Synthesizer | `agents/fix-qa-synthesizer.md` | Round 2 (C) | Consolidate findings, assign severity, tentative verdict |
| Critic | `agents/fix-qa-critic.md` | Round 3 (A, B) | Defend severity/findings against synthesis |
| Reconciler | `agents/fix-qa-reconciler.md` | Round 4 (C) | Bias-aware reconciliation + final fix-issues.md + fix-manifest.yaml |
