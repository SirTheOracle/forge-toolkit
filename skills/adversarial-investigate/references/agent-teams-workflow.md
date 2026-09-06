# Adversarial Investigation — Agent Teams Workflow

This is the canonical workflow. It uses Claude Code's Agent Teams feature (`TeamCreate`, `Agent(team_name, name)`, `SendMessage`, `TeamDelete`). Requires `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in settings.

**If Teams is unavailable**, fall back to `references/subagent-fallback.md` (file-based coordination). The fallback works but loses persistence — investigators lose their Round 1 context when Round 3 critiques C.

---

## Confirmed Tool Surface

| Tool | Purpose | Key params |
|---|---|---|
| `TeamCreate` | Create team directory + task list | `team_name`, `description` |
| `Agent` | Spawn a registered teammate | `subagent_type`, `prompt`, `team_name`, `name`, `run_in_background: true` |
| `SendMessage` | Plain text or structured message to a teammate (by name) | `to`, `summary`, `message` |
| `SendMessage` (shutdown) | Graceful termination | `to`, `message: {type: "shutdown_request", reason}` |
| `TeamDelete` | Cleanup after shutdown | — |

Key facts:
- Teammate `agentId` is `<name>@<team-name>` (e.g. `investigator-a@inv-<slug>`).
- Teammates go **idle** after each turn. Idle is not done — `SendMessage` to an idle teammate resumes it from its transcript with full prior context. This is what makes Round 3 critique grounded in Round 1 investigation.
- Messages from teammates arrive as **new conversation turns** (idle notifications). Do not poll an inbox.
- `TeamDelete` refuses while any member is active — shut teammates down first.

---

## Architecture

```
YOU (user) ── problem statement + repro.md ──► TEAM LEAD (this Claude session)
                                                      │
                                                      ├─ TeamCreate(inv-<slug>)
                                                      │
                                          ┌───────────┼───────────┐
                                          │           │           │
                                   Round 1│     Round 2           │ Round 3      Round 4
                                          ▼           ▼           ▼              ▼
                                 ┌──────────────┐  ┌──────────┐  ┌─────────┐  ┌──────────┐
                                 │ investigator-a│  │synthesizer│  │ critic   │  │reconciler│
                                 │ (forward)     │  │    -c     │  │ via A&B  │  │ via C    │
                                 │               │  │(teammate) │  │(SendMsg  │  │(SendMsg  │
                                 │ investigator-b│  │           │  │ resumes) │  │ resumes) │
                                 │ (backward)    │  │           │  │          │  │          │
                                 └──────────────┘  └──────────┘  └─────────┘  └──────────┘
                                          │           │            │             │
                                          ▼           ▼            ▼             ▼
                                investigation- investigation- feedback-A    reconciliation-
                                A.md / B.md    C.md           /B (inbox)    notes.md +
                                              review-for-A                  diagnosis.md
                                              review-for-B
```

A and B are the **same teammates** across Round 1 and Round 3 — that's the whole point.

---

## Detailed Sequence

### Round 0: Setup

```python
# 1. Read inputs from output_dir (set by orchestrator)
output_dir = ".dev/fixes/{original-slug}/{fix-slug}/"
# Required files already on disk:
#   - problem-statement.md (orchestrator wrote this)
# Optional files:
#   - repro.md (from fix-reproducer, if it ran)
# Optional context (read-only):
#   - .dev/proposals/{original-slug}/final-plan.md
#   - .dev/proposals/{original-slug}/implementation.md
#   - .dev/proposals/{original-slug}/coder-report.md

# 2. Apply Step 0 gates from SKILL.md:
#    - 0A: empty-args short-circuit
#    - 0B/0C: read inputs and original-pipeline context
#    - 0D: confirm --env (handle hybrid case)
#    - 0E: exploration budget (≤3-5 files)
#    - 0F: evidence floor — bail to user if no repro/no context/no telemetry

# 3. Create team
TeamCreate(
    team_name=f"inv-{slug[:20]}",
    description=f"Adversarial investigation for {slug}"
)

# 4. Create the 5 tasks with dependencies
#    #1 Investigation A     (no deps)
#    #2 Investigation B     (no deps)
#    #3 Synthesis C         (blocked by #1, #2)
#    #4 Round 3 critique    (blocked by #3)
#    #5 Reconciliation      (blocked by #4)
```

### Round 1: Spawn A and B in parallel (same turn)

```python
# Both Agent calls in ONE message to run in parallel.
Agent(
    subagent_type="general-purpose",
    description="Investigator A: Forward-from-trigger",
    team_name=f"inv-{slug[:20]}",
    name="investigator-a",
    run_in_background=True,
    prompt=build_investigator_prompt(
        strategy="A",  # Forward-from-trigger
        env=env,       # "prod" or "dev"
        problem_statement=...,
        repro=...,     # contents of repro.md if present, else None
        original_pipeline_context=...,  # if --original-slug
        source_files=[...],
        output_path=f"{output_dir}/investigation-A.md",
        investigator_role=open("agents/investigator.md").read(),
        investigation_format=open("references/investigation-format.md").read(),
    ),
)

Agent(
    subagent_type="general-purpose",
    description="Investigator B: Backward-from-failure-point",
    team_name=f"inv-{slug[:20]}",
    name="investigator-b",
    run_in_background=True,
    prompt=build_investigator_prompt(strategy="B", ...),  # Backward
)
```

**Investigator prompt MUST include** (verbatim block):

```
ISOLATION RULE — strict.

Allowed reads:
- Files listed under "Source files" in this prompt
- problem-statement.md and repro.md (if present) in {output_dir}
- Files listed under "Original pipeline context" — READ-ONLY, treat as
  context not suspects

Forbidden reads, in {output_dir}:
- investigation-A.md, investigation-B.md, investigation-C.md (except your
  own investigation in Round 3)
- review-for-A.md, review-for-B.md (except your own review in Round 3)
- diagnosis.md, reconciliation-notes.md

Forbidden writes:
- Any file under .dev/proposals/{original-slug}/ (read-only context)

When you finish, SendMessage the team lead "team-lead" with a one-line
plain-text summary including your committed top hypothesis (or
"insufficient evidence" if that is the honest finding). Then go idle —
do NOT exit. You will be messaged again in Round 3 to critique a
synthesis of your work.

Save your investigation as: {output_path}
```

After both investigators spawn, **the lead goes idle** — teammate messages arrive as new conversation turns automatically.

### Round 1 gate: Artifact + Quality + Convergence

When both "done" messages have arrived:

**Artifact gate (run first)** — verify on disk:

```python
assert os.path.exists(f"{output_dir}/investigation-A.md")
assert os.path.exists(f"{output_dir}/investigation-B.md")
# AND non-empty (size > some threshold, e.g. 200 bytes)
```

If either is missing or empty, `SendMessage(to="investigator-{a|b}", message="Your investigation file at {path} is missing or empty. Write your full investigation to that path before signaling done.")`. Do not proceed to the quality gate until both files exist.

**Quality gate** — for each investigation, verify:

- Required sections (Strategy and Inputs, Bug Restatement, Investigation Findings, Hypotheses (≥3 — top + 2 alternatives), Symptom-vs-Cause Analysis, Top Hypothesis or Insufficient Evidence, Confidence + Blocking Items)
- ≥3 concrete file:line or log references
- At least one `[HIGH]`/`[MEDIUM]`/`[LOW]` confidence tag
- The Symptom-vs-Cause Analysis section is non-empty

If a gate fails: `SendMessage(to="investigator-a", message="<specific revision ask>")`. Teammate resumes with full context.

**Convergence check** — did A and B independently land on the same top hypothesis with the same evidence?

- **Yes (converged)**: skip to §"Convergence path" below.
- **No**: proceed to Round 2.

**(Interactive) checkpoint 1**: if `--interactive`, pause for user review of investigation-A/B.

### Round 2: Synthesizer C

```python
Agent(
    subagent_type="general-purpose",
    description="Synthesizer C: synthesis + isolated reviews",
    team_name=f"inv-{slug[:20]}",
    name="synthesizer-c",
    run_in_background=True,
    prompt=build_synthesizer_prompt(
        problem_statement=...,
        repro=...,
        original_pipeline_context=...,
        source_files=[...],
        investigation_a_path=f"{output_dir}/investigation-A.md",
        investigation_b_path=f"{output_dir}/investigation-B.md",
        output_dir=output_dir,
        synthesizer_role=open("agents/inv-synthesizer.md").read(),
        investigation_format=open("references/investigation-format.md").read(),
    ),
)
```

The synthesizer prompt MUST instruct Phase 0 (independent code/evidence exam before reading investigations) and produce **three files**: `investigation-C.md`, `review-for-A.md`, `review-for-B.md`.

### Round 2 gate: Artifact completeness (required)

When C's "done" arrives, the lead MUST verify on disk:

```python
assert os.path.exists(f"{output_dir}/investigation-C.md")
assert os.path.exists(f"{output_dir}/review-for-A.md")
assert os.path.exists(f"{output_dir}/review-for-B.md")
```

If any is missing, `SendMessage(to="synthesizer-c", message="<name the missing file>")`. Do not proceed to Round 3 with incomplete artifacts.

**(Interactive) checkpoint 2**: if `--interactive`, pause for user review of investigation-C.md.

### Round 3: Critique (A and B resume, in parallel)

Two `SendMessage` calls in one turn. Use these exact `description` values:

- `"Critic A: Round 3 feedback on investigation-A"`
- `"Critic B: Round 3 feedback on investigation-B"`

```python
SendMessage(
    to="investigator-a",
    summary="Round 3: review feedback on your investigation",
    message=f"""A synthesizer reviewed your investigation alongside another
independent investigation you have not seen and produced a synthesis with
isolated review files.

Read ONLY:
- {output_dir}/review-for-A.md (the reviewer's feedback on YOUR work)
- {output_dir}/investigation-A.md (your own investigation, for re-reference)

You must NOT read:
- {output_dir}/investigation-B.md, investigation-C.md
- {output_dir}/review-for-B.md
- {output_dir}/diagnosis.md, reconciliation-notes.md

{critic_role_body}

Reply to team-lead with your detailed feedback. Then go idle."""
)

SendMessage(
    to="investigator-b",
    summary="Round 3: review feedback on your investigation",
    message="""<mirror for B>"""
)
```

### Round 4: Reconciliation

When both critiques arrive:

```python
SendMessage(
    to="synthesizer-c",
    summary="Round 4: reconcile critiques into final diagnosis",
    message=f"""Both original investigators critiqued your synthesis from
their Round 1 contexts. Reconcile their feedback against the evidence and
produce:

1. {output_dir}/diagnosis.md — the final diagnosis (follow references/diagnosis-format.md)
2. {output_dir}/reconciliation-notes.md — one line per critique point:
   "A: <point> → ACCEPTED/PARTIAL/REJECTED — <reason>"

Feedback from Investigator A:
{a_feedback_body}

Feedback from Investigator B:
{b_feedback_body}

{reconciler_role_body}

You are explicitly authorized to commit to INSUFFICIENT EVIDENCE as the
convergence status. Do not manufacture confidence to look decisive."""
)
```

### Round 5: Cleanup

```python
# Shutdown all teammates in parallel.
SendMessage(to="investigator-a", message={"type": "shutdown_request", "reason": "complete"})
SendMessage(to="investigator-b", message={"type": "shutdown_request", "reason": "complete"})
SendMessage(to="synthesizer-c", message={"type": "shutdown_request", "reason": "complete"})

# Wait for shutdown acknowledgments (they arrive as conversation turns), then:
TeamDelete()
```

If `TeamDelete` returns `Cannot cleanup team with N active member(s)`, a shutdown hasn't completed yet — wait for the next idle notification and retry.

---

## Convergence path (A and B agreed)

If A and B independently landed on the same top hypothesis with the same evidence, the bias-mitigation rounds (synthesis + critique + reconciliation) provide diminishing value — both investigators already agree. Skip to a lightweight review.

```python
# No Round 2/3/4. Spawn C as a lightweight reviewer.
Agent(
    team_name=..., name="synthesizer-c",
    prompt=build_convergence_review_prompt(
        investigation_a_path=...,
        investigation_b_path=...,
        output_path=f"{output_dir}/diagnosis.md",
        diagnosis_format=open("references/diagnosis-format.md").read(),
    ),
    run_in_background=True,
)
# Convergence diagnosis still requires:
#  - Symptom vs Cause Analysis section (do not skip — convergence on the
#    visible failure could still be symptom-confirmation bias from both)
#  - Confidence + Blocking Items
# No review-for-A.md, no review-for-B.md, no investigation-C.md, no
# reconciliation-notes.md.
# Artifacts: problem-statement.md, repro.md (if present), investigation-A.md,
# investigation-B.md, diagnosis.md.
```

---

## Error handling

| Round | Failure | Recovery |
|---|---|---|
| 0 | Evidence floor not met (Step 0F) | Surface to user; do not spawn teammates |
| 1 | One investigator silent | Wait 10 min. If still silent, `SendMessage(to=name, message="status?")`. If still silent, proceed with one investigation and note it in the synthesis prompt. |
| 1 | Both fail | Abort. Report to user. |
| 1 | Investigation file missing/empty after done message | Re-message teammate with explicit path; one retry |
| 2 | C produces partial artifacts | Resume via SendMessage, name the missing file. One retry. |
| 2 | C sends error | Shut down C, spawn `synthesizer-c-2` with same prompt. |
| 3 | One critic silent | Proceed to Round 4 noting which critique is missing. |
| 3 | Both silent | Proceed with no critique — C reconciles against its own synthesis. |
| 4 | C fails to produce diagnosis.md | Present investigation-C.md as the final artifact, noting reconciliation incomplete. |

---

## Task dependency chain

```
#1 Investigator A ─┐
                   ├─► #3 Synthesis C ──► #4 Round 3 critique ──► #5 Reconciliation
#2 Investigator B ─┘
```

Tasks auto-unblock as upstream tasks complete. Teammates can claim tasks via `TaskUpdate(owner=<name>)` — or the lead assigns explicitly.

---

## Output directory

```
{output_dir}/
├── problem-statement.md         ← Input (from orchestrator)
├── repro.md                     ← Input (from fix-reproducer, if it ran)
├── investigation-A.md           ← Round 1: Forward-from-trigger
├── investigation-B.md           ← Round 1: Backward-from-failure-point
├── investigation-C.md           ← Round 2: Synthesis (lead-only; absent on convergence)
├── review-for-A.md              ← Round 2: A's eyes only (absent on convergence)
├── review-for-B.md              ← Round 2: B's eyes only (absent on convergence)
├── reconciliation-notes.md      ← Round 4: ACCEPT/PARTIAL/REJECT audit (absent on convergence)
└── diagnosis.md                 ← Round 4 (or convergence): FINAL DELIVERABLE
```

On the convergence path, `investigation-C.md`, `review-for-A.md`, `review-for-B.md`, and `reconciliation-notes.md` are **absent by design** — their absence is the structural signal that the run was convergent.
