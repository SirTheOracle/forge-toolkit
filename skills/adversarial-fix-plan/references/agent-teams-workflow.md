# Adversarial Fix Plan — Agent Teams Workflow

This is the canonical workflow. It uses Claude Code's Agent Teams feature (`TeamCreate`, `Agent(team_name, name)`, `SendMessage`, `TeamDelete`). Requires `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in settings.

If Teams is unavailable, fall back to `references/subagent-fallback.md` (file-based coordination).

This skill has **two modes**, auto-detected by the presence of `fix-review.md` in the output directory:

- **Full mode**: 4-round adversarial flow (Rounds 1–4) producing `fix-plan.md` v1
- **Revise mode**: single-agent revision producing `fix-plan.md` v2 from `fix-review.md`

---

## Confirmed Tool Surface

| Tool | Purpose | Key params |
|---|---|---|
| `TeamCreate` | Create team directory + task list | `team_name`, `description` |
| `Agent` | Spawn a registered teammate | `subagent_type`, `prompt`, `team_name`, `name`, `run_in_background: true` |
| `SendMessage` | Plain text or structured message to a teammate | `to`, `summary`, `message` |
| `SendMessage` (shutdown) | Graceful termination | `to`, `message: {type: "shutdown_request", reason}` |
| `TeamDelete` | Cleanup after shutdown | — |

Key facts:
- Teammate `agentId` is `<name>@<team-name>` (e.g. `planner-a@fixp-<slug>`).
- Teammates go **idle** after each turn. `SendMessage` to an idle teammate resumes from full transcript context.
- Messages from teammates arrive as new conversation turns. Do not poll.
- `TeamDelete` refuses while members are active — shut down first.

---

## Full Mode — Architecture

```
YOU (orchestrator) ── diagnosis.md ──► TEAM LEAD (this Claude session)
                                              │
                                              ├─ TeamCreate(fixp-<slug>)
                                              │
                                  ┌───────────┼───────────┐
                                  │           │           │
                           Round 1│     Round 2           │ Round 3      Round 4
                                  ▼           ▼           ▼              ▼
                         ┌──────────────┐  ┌──────────┐  ┌─────────┐  ┌──────────┐
                         │  planner-a   │  │synthesizer│  │  critic  │  │reconciler│
                         │  (Surgical)  │  │    -c     │  │ via A&B  │  │ via C    │
                         │              │  │(teammate) │  │(SendMsg  │  │(SendMsg  │
                         │  planner-b   │  │           │  │ resumes) │  │ resumes) │
                         │  (Robust)    │  │           │  │          │  │          │
                         └──────────────┘  └──────────┘  └─────────┘  └──────────┘
                                  │           │            │             │
                                  ▼           ▼            ▼             ▼
                              fixA.md    fixC.md       feedback-A   reconciliation-
                              fixB.md    review-for-A   /B (inbox)  notes.md +
                                         review-for-B               fix-plan.md
```

A and B are the **same teammates** across Round 1 and Round 3.

---

## Full Mode — Detailed Sequence

### Round 0: Setup

```python
# 1. Read inputs from output_dir
output_dir = ".dev/fixes/{original-slug}/{fix-slug}/"
# Required:
#   - diagnosis.md (orchestrator/upstream wrote this)
#   - problem-statement.md
# Optional:
#   - repro.md
# Optional context (read-only):
#   - .dev/proposals/{original-slug}/final-plan.md
#   - .dev/proposals/{original-slug}/implementation.md
#   - .dev/proposals/{original-slug}/coder-report.md

# 2. Apply Step 0 gates from SKILL.md:
#    - 0A: empty-args short-circuit (--output-dir, --env, diagnosis.md)
#    - 0B: read inputs
#    - 0C: parse convergence_status from diagnosis.md
#          → INSUFFICIENT EVIDENCE or missing/unparseable: write BLOCKED fix-plan.md and exit
#    - 0D: read original-pipeline context
#    - 0E: detect mode (revise mode if fix-review.md exists)
#    - 0F: confirm --env
#    - 0G: exploration budget (≤3-5 files)

# 3. Create team
TeamCreate(
    team_name=f"fixp-{slug[:18]}",  # 32-char limit; reserve room for "fixp-" prefix
    description=f"Adversarial fix-plan for {slug}"
)

# 4. Create the 5 tasks with dependencies
#    #1 Plan A (Surgical)    (no deps)
#    #2 Plan B (Robust)      (no deps)
#    #3 Synthesis C          (blocked by #1, #2)
#    #4 Round 3 critique     (blocked by #3)
#    #5 Reconciliation       (blocked by #4)
```

### Round 1: Spawn A and B in parallel (same turn)

```python
# Both Agent calls in ONE message to run in parallel.
Agent(
    subagent_type="general-purpose",
    description="Planner A: Surgical fix",
    team_name=f"fixp-{slug[:18]}",
    name="planner-a",
    run_in_background=True,
    prompt=build_planner_prompt(
        strategy="A",  # Surgical
        env=env,
        diagnosis=...,
        problem_statement=...,
        repro=...,                     # if present
        original_pipeline_context=...,  # if --original-slug
        source_files=[...],
        output_path=f"{output_dir}/fixA.md",
        planner_role=open("agents/fix-planner.md").read(),
        plan_format=open("references/fix-plan-format.md").read(),
    ),
)

Agent(
    subagent_type="general-purpose",
    description="Planner B: Robust fix",
    team_name=f"fixp-{slug[:18]}",
    name="planner-b",
    run_in_background=True,
    prompt=build_planner_prompt(strategy="B", ...),  # Robust
)
```

**Planner prompt MUST include** (verbatim block):

```
ISOLATION RULE — strict.

Allowed reads:
- Files listed under "Source files" in this prompt
- diagnosis.md, problem-statement.md, repro.md (if present) in {output_dir}
- Files listed under "Original pipeline context" — READ-ONLY, treat as
  context for understanding the codebase, NOT as fix locations or suspects

Forbidden reads, in {output_dir}:
- fixA.md, fixB.md, fixC.md (except your own plan in Round 3)
- review-for-A.md, review-for-B.md (except your own review in Round 3)
- fix-plan.md, reconciliation-notes.md, fix-review.md, revision-notes.md

Forbidden writes:
- Any file under .dev/proposals/{original-slug}/ (read-only context)
- Any source code file (this skill produces a plan, not code)

DO NOT re-investigate or re-diagnose. Diagnosis is authoritative.
If you believe diagnosis.md is wrong, set Status: BLOCKED in your plan,
flag the contradiction in BLOCKING_ITEMS, and stop. The orchestrator
will decide whether to re-run investigate.

When you finish, SendMessage the team lead "team-lead" with a one-line
plain-text summary including your fix approach. Then go idle —
do NOT exit. You will be messaged again in Round 3.

Save your plan as: {output_path}
```

After both planners spawn, the lead goes idle — teammate messages arrive as conversation turns.

### Round 1 gates

When both "done" messages arrive:

**Artifact gate (run first)** — verify on disk:

```python
import os
assert os.path.exists(f"{output_dir}/fixA.md") and os.path.getsize(f"{output_dir}/fixA.md") > 0
assert os.path.exists(f"{output_dir}/fixB.md") and os.path.getsize(f"{output_dir}/fixB.md") > 0
```

If either is missing/empty: `SendMessage(to="planner-{a|b}", message="Your plan file is missing/empty. Write it before signaling done.")`. Wait for re-completion.

**Quality gate** — for each plan:
- Required sections (Strategy and Inputs, Diagnosis Reference, Fix Approach, Changes, Sequencing, Defenses, Test Plan, Rollback Plan, Risk Assessment, Confidence + Blocking Items)
- Defenses section is required even if "none + reason"
- Sequencing section is required (even if "order does not matter")
- Rollback plan is non-trivial (not "N/A")
- ≥1 confidence annotation

If failed: `SendMessage(to="planner-a|b", message="revise: <specific>")`.

**Diagnosis-disagreement check (run before convergence)** — if either plan has `Status: BLOCKED` flagging diagnosis contradiction, do NOT proceed. Stop and report to the orchestrator. The orchestrator decides whether to re-run investigate.

**Convergence check** — A and B chose the same fix approach with the same files? If yes, skip to §"Convergence path" below.

**(Interactive) checkpoint 1** — if `--interactive`, pause for user review of fixA/fixB.

### Round 2: Synthesizer C

```python
Agent(
    subagent_type="general-purpose",
    description="Synthesizer C: cross-verify and synthesize",
    team_name=f"fixp-{slug[:18]}",
    name="synthesizer-c",
    run_in_background=True,
    prompt=build_synthesizer_prompt(
        env=env,
        diagnosis=...,
        problem_statement=...,
        repro=...,
        source_files=[...],
        plan_a_path=f"{output_dir}/fixA.md",
        plan_b_path=f"{output_dir}/fixB.md",
        output_dir=output_dir,
        synthesizer_role=open("agents/fixp-synthesizer.md").read(),
        plan_format=open("references/fix-plan-format.md").read(),
    ),
)
```

Synthesizer prompt MUST instruct Phase 0 (independent code/diagnosis exam BEFORE reading plans) and produce **three files**: `fixC.md`, `review-for-A.md`, `review-for-B.md`.

### Round 2 gate: Artifact completeness (required)

```python
assert os.path.exists(f"{output_dir}/fixC.md")
assert os.path.exists(f"{output_dir}/review-for-A.md")
assert os.path.exists(f"{output_dir}/review-for-B.md")
```

If missing: `SendMessage(to="synthesizer-c", message="<name missing file>")`. Do not proceed with partial artifacts.

**(Interactive) checkpoint 2** — if `--interactive`, pause for user review of `fixC.md`.

### Round 3: Critique (A and B resume, in parallel)

Use these exact `description` values for transcript scriptability:
- `"Critic A: Round 3 feedback on fixA"`
- `"Critic B: Round 3 feedback on fixB"`

```python
SendMessage(
    to="planner-a",
    summary="Round 3: critique synthesis of your plan",
    message=f"""A synthesizer reviewed your plan alongside another independent
plan you have not seen and produced a synthesis with isolated review files.

Read ONLY:
- {output_dir}/review-for-A.md (the reviewer's feedback on YOUR plan)
- {output_dir}/fixA.md (your own plan, for re-reference)

You must NOT read:
- {output_dir}/fixB.md, fixC.md
- {output_dir}/review-for-B.md
- {output_dir}/fix-plan.md, reconciliation-notes.md, fix-review.md, revision-notes.md

{open('agents/fixp-critic.md').read()}

Reply to team-lead with your detailed feedback (in the message body, not as a
file write). Then go idle."""
)

SendMessage(
    to="planner-b",
    summary="Round 3: critique synthesis of your plan",
    message="""<mirror for B>"""
)
```

### Round 4: Reconciliation

When both critiques arrive:

```python
SendMessage(
    to="synthesizer-c",
    summary="Round 4: reconcile critiques into final fix-plan",
    message=f"""Both original planners critiqued your synthesis from their
Round 1 contexts. Reconcile their feedback against the diagnosis and the
code, and produce:

1. {output_dir}/fix-plan.md — the final deliverable (follow references/fix-plan-final-format.md)
2. {output_dir}/reconciliation-notes.md — one line per critique point:
   "A: <point> → ACCEPTED/PARTIAL/REJECTED — <reason>"

Feedback from Planner A:
{a_feedback_body}

Feedback from Planner B:
{b_feedback_body}

{open('agents/fixp-reconciler.md').read()}"""
)
```

### Round 4 gate: Artifact completeness

```python
assert os.path.exists(f"{output_dir}/fix-plan.md")
assert os.path.exists(f"{output_dir}/reconciliation-notes.md")
```

Read the last two lines of `fix-plan.md` to extract `CONFIDENCE` and `BLOCKING_ITEMS`. If `Status: BLOCKED` or `BLOCKING_ITEMS > 0`, surface to orchestrator before cleanup.

### Round 5: Cleanup (full mode)

```python
SendMessage(to="planner-a", message={"type": "shutdown_request", "reason": "complete"})
SendMessage(to="planner-b", message={"type": "shutdown_request", "reason": "complete"})
SendMessage(to="synthesizer-c", message={"type": "shutdown_request", "reason": "complete"})

# Wait for shutdown acknowledgments, then:
TeamDelete()
```

Present `fix-plan.md` (+ `reconciliation-notes.md`) to the user.

---

## Convergence path (full mode, A and B agreed)

When the convergence check at end of Round 1 detects A and B chose the same approach with the same files:

```python
# No Round 2/3/4. Spawn C as a lightweight verifier that produces fix-plan.md directly.
Agent(
    team_name=f"fixp-{slug[:18]}",
    name="synthesizer-c",
    run_in_background=True,
    prompt=build_convergence_review_prompt(
        plan_a_path=f"{output_dir}/fixA.md",
        plan_b_path=f"{output_dir}/fixB.md",
        diagnosis=...,
        env=env,
        output_path=f"{output_dir}/fix-plan.md",
        plan_final_format=open("references/fix-plan-final-format.md").read(),
    ),
)
```

The convergence-review prompt instructs C to:

1. Independently verify the convergent plan against the actual code (anti-anchoring)
2. Confirm the plan addresses the diagnosed cause
3. Confirm sequencing, rollback, and test plan are sound for the env
4. Either ratify the convergent plan into `fix-plan.md` directly, OR flag a gap (in which case the run upgrades to a full Round 2/3/4 — do NOT silently skip the gap)

Artifacts on convergence path:
- `problem-statement.md`, `diagnosis.md`, `repro.md` (if present)
- `fixA.md`, `fixB.md`
- `fix-plan.md`
- (no `fixC.md`, no `review-for-*.md`, no `reconciliation-notes.md`)

The absence of those files is the structural signal that the run was convergent.

---

## Revise Mode

Revise mode is auto-detected by `fix-review.md` existing in the output directory at Round 0. It skips Rounds 1–4 entirely.

### Round R-0: Validate revise-mode preconditions

Per Step 9A of SKILL.md:

- `fix-review.md` exists and is non-empty (size > 200 bytes)
- `fix-review.md` contains structured review points (headers/bullets, not just freeform prose). If structure can't be detected, surface to user and wait for confirmation.
- `fix-plan.md` exists (the v1 being revised). If not, surface to user.
- `fix-review.md` mtime is newer than `fix-plan.md` mtime. If not, surface to user.

If any precondition fails and user does not confirm, do not enter revise mode.

### Round R: Spawn the reviser

```python
TeamCreate(
    team_name=f"fixp-rev-{slug[:14]}",
    description="Revise fix-plan from review"
)
# Single task: revise

Agent(
    subagent_type="general-purpose",
    description="Reviser: incorporate fix-review.md into fix-plan.md v2",
    team_name=f"fixp-rev-{slug[:14]}",
    name="synthesizer-c",
    run_in_background=True,
    prompt=build_reviser_prompt(
        env=env,
        diagnosis=...,
        existing_fix_plan_path=f"{output_dir}/fix-plan.md",
        fix_review_path=f"{output_dir}/fix-review.md",
        output_path_v2=f"{output_dir}/fix-plan.md",  # overwrites v1
        revision_notes_path=f"{output_dir}/revision-notes.md",
        reviser_role=open("agents/fixp-reviser.md").read(),
        plan_final_format=open("references/fix-plan-final-format.md").read(),
    ),
)
```

### Round R gate: Quality + completeness

When the reviser signals done:

```python
# 1. Both files exist and are non-empty
assert os.path.exists(f"{output_dir}/fix-plan.md")
assert os.path.exists(f"{output_dir}/revision-notes.md")
assert os.path.getsize(f"{output_dir}/fix-plan.md") > 0
assert os.path.getsize(f"{output_dir}/revision-notes.md") > 0

# 2. fix-plan.md (v2) has all required sections per fix-plan-final-format.md
# 3. revision-notes.md contains an entry for every substantive review point
#    in fix-review.md (no silent drops)
```

If any check fails: `SendMessage(to="synthesizer-c", message="<specific gap>")`. One retry; if still failing, surface to user.

### Round R-archive: Archive consumed fix-review.md

After the quality gate passes:

```python
import shutil, datetime
ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
shutil.move(
    f"{output_dir}/fix-review.md",
    f"{output_dir}/fix-review-{ts}.md"
)
```

This guards against re-entering revise mode on the next invocation when v2 hasn't been re-reviewed.

### Round R-cleanup

Same as full mode but with single teammate.

---

## Error handling

| Round | Failure | Recovery |
|---|---|---|
| 0 | INSUFFICIENT EVIDENCE / missing convergence_status | Write BLOCKED fix-plan.md, exit. Do not spawn. |
| 0 (revise) | fix-review.md fails preconditions | Surface to user; do not enter revise mode without confirmation |
| 1 | One planner silent | Wait 10 min. If still silent, `SendMessage(to=name, message="status?")`. If still silent, proceed with one plan, note in synthesis prompt. |
| 1 | Both fail | Abort. Report to user. |
| 1 | Plan file missing/empty after done | SendMessage with explicit path; one retry. Then abort. |
| 1 | Diagnosis disagreement BLOCKING_ITEM in either plan | Stop. Surface to orchestrator. Do not proceed to Round 2. |
| 2 | C produces partial artifacts | SendMessage naming the missing file; one retry. |
| 2 | C errors | Shut down C, spawn `synthesizer-c-2` with same prompt. |
| 3 | One critic silent | Proceed to Round 4 noting which critique is missing. |
| 3 | Both silent | Proceed with no critique — C reconciles against own synthesis. |
| 4 | C fails to produce fix-plan.md | Present fixC.md to user as partial result with "no final plan produced" note. |
| R (revise) | Quality gate fails twice | Surface to user with what's incomplete. |

---

## Task dependency chain (full mode)

```
#1 Planner A (Surgical) ─┐
                         ├─► #3 Synthesis C ──► #4 Round 3 critique ──► #5 Reconciliation
#2 Planner B (Robust)   ─┘
```

---

## Output directory

```
{output_dir}/
├── problem-statement.md         ← Input
├── repro.md                     ← Input (if fix-reproducer ran)
├── diagnosis.md                 ← Input (from adversarial-investigate)
├── fixA.md                      ← Round 1: Surgical (full mode only)
├── fixB.md                      ← Round 1: Robust (full mode only)
├── fixC.md                      ← Round 2: Synthesis (full mode only; absent on convergence)
├── review-for-A.md              ← Round 2 (full mode; absent on convergence)
├── review-for-B.md              ← Round 2 (full mode; absent on convergence)
├── reconciliation-notes.md      ← Round 4 (full mode; absent on convergence)
├── fix-plan.md                  ← Round 4 (full) OR Round R (revise): FINAL DELIVERABLE
├── fix-review.md                ← Input from fix-plan-reviewer; presence triggers revise mode (archived after consumption)
├── fix-review-{ts}.md           ← Archived consumed reviews
└── revision-notes.md            ← Revise mode only
```

Path inference:
- **Full run, fully adversarial**: fixA + fixB + fixC + reviews + reconciliation-notes + fix-plan
- **Convergence run**: fixA + fixB + fix-plan only
- **Revise run**: fix-plan (overwritten) + revision-notes + fix-review-{ts}.md
- **Blocked run**: fix-plan only (with `Status: BLOCKED`)
