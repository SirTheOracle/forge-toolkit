# Adversarial Proposal — Agent Teams Workflow

This is the canonical workflow. It uses Claude Code's Agent Teams feature (`TeamCreate`, `Agent(team_name, name)`, `SendMessage`, `TeamDelete`). Requires `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in settings.

**If Teams is unavailable**, fall back to `references/subagent-fallback.md` (file-based coordination). The fallback works but loses persistence — proposers lose their Round 1 investigation context when Round 3 critiques C.

---

## Confirmed Tool Surface

| Tool | Purpose | Key params |
|---|---|---|
| `TeamCreate` | Create team directory + task list | `team_name`, `description` |
| `Agent` | Spawn a registered teammate | `subagent_type`, `prompt`, `team_name`, `name`, `run_in_background: true` |
| `SendMessage` | Plain text or structured message to a teammate (by name) | `to`, `summary`, `message` |
| `SendMessage` (shutdown) | Graceful termination | `to`, `message: {type: "shutdown_request", reason}` |
| `TeamDelete` | Cleanup after shutdown | — |

Key facts from verification:
- Teammate `agentId` is `<name>@<team-name>` (e.g. `proposer-a@adv-<slug>`).
- Teammates go **idle** after each turn. Idle is not done — `SendMessage` to an idle teammate resumes it from its transcript with full prior context. This is what makes Round 3 critique grounded in Round 1 investigation.
- Messages from teammates arrive as **new conversation turns** (idle notifications). Do not poll an inbox.
- `TeamDelete` refuses while any member is active — shut teammates down first.
- The `Agent` tool's `team_name` + `name` params are what register a spawn as a teammate (vs. a detached subagent).

---

## Architecture

```
YOU (user) ── problem statement ──► TEAM LEAD (this Claude session)
                                          │
                                          ├─ TeamCreate(adv-<slug>)
                                          │
                              ┌───────────┼───────────┐
                              │           │           │
                       Round 1│     Round 2           │ Round 3      Round 4
                              ▼           ▼           ▼              ▼
                     ┌──────────────┐  ┌──────────┐  ┌─────────┐  ┌──────────┐
                     │ proposer-a   │  │synthesizer│  │ critic   │  │reconciler│
                     │ (teammate)   │  │    -c     │  │ via A&B  │  │ via C    │
                     │              │  │(teammate) │  │(SendMsg  │  │(SendMsg  │
                     │ proposer-b   │  │           │  │ resumes) │  │ resumes) │
                     │ (teammate)   │  │           │  │          │  │          │
                     └──────────────┘  └──────────┘  └─────────┘  └──────────┘
                              │           │            │             │
                              ▼           ▼            ▼             ▼
                      proposal-A.md  proposal-C.md  feedback-A   reconciliation-
                      proposal-B.md  review-for-A    /B (inbox)  notes.md +
                                     review-for-B                 final-plan.md
```

A and B are the **same teammates** across Round 1 and Round 3 — that's the whole point.

---

## Detailed Sequence

### Round 0: Setup

```python
# 1. Create output directory
output_dir = f"{project}/.dev/proposals/{slug}/"
# Write problem-statement.md to output_dir

# 2. Create team (team_name must be short — 32 char max, slug-like)
TeamCreate(
    team_name=f"adv-{slug[:20]}",
    description=f"Adversarial proposal for {slug}"
)
# → creates ~/.claude/teams/adv-<slug>/config.json
#   creates ~/.claude/tasks/adv-<slug>/

# 3. Create the 5 tasks with dependencies (use TaskCreate + TaskUpdate.addBlockedBy)
#    #1 Proposal A        (no deps)
#    #2 Proposal B        (no deps)
#    #3 Synthesis C       (blocked by #1, #2)
#    #4 Round 3 critique  (blocked by #3)
#    #5 Reconciliation    (blocked by #4)
```

### Round 1: Spawn A and B in parallel (same turn)

```python
# Both Agent calls go in ONE message to run in parallel.
Agent(
    subagent_type="general-purpose",
    description="Proposer A: Strategy A investigation",
    team_name=f"adv-{slug[:20]}",
    name="proposer-a",
    run_in_background=True,
    prompt=build_proposer_prompt(
        strategy="A",
        problem_statement=...,
        source_files=[...],
        dependency_paths=[...],   # e.g. prior slug's final-plan.md if declared
        output_path=f"{output_dir}/proposal-A.md",
        proposer_role=open("agents/proposer.md").read(),
        proposal_format=open("references/proposal-format.md").read(),
    ),
)

Agent(
    subagent_type="general-purpose",
    description="Proposer B: Strategy B investigation",
    team_name=f"adv-{slug[:20]}",
    name="proposer-b",
    run_in_background=True,
    prompt=build_proposer_prompt(strategy="B", ...),
)
```

**Proposer prompt MUST include** (verbatim block, adapted per strategy):

```
ISOLATION RULE — strict
- You may read: files listed in "Source files" below, the problem statement,
  and any path listed under "Dependency paths" (these are other slugs' final
  plans that this work depends on).
- You must NOT read any file inside {output_dir} except problem-statement.md.
  In particular: do NOT read proposal-B.md (or proposal-A.md), proposal-C.md,
  review-for-A.md, review-for-B.md, final-plan.md.
- When you finish, SendMessage the team lead "team-lead" with a one-line
  summary (plain text, not JSON). Then go idle. Do NOT exit — you will be
  messaged again in Round 3 to review a technical critique of your work.

Save proposal as: {output_path}
```

After both proposers spawn, **the lead goes idle** — teammate messages arrive as new conversation turns automatically. No polling, no file-watching.

### Round 1 gate: Quality + Convergence

When both "done" messages have arrived:

**Quality check** — for each proposal, verify on disk:
- Required sections (Problem, Investigation, Core Analysis, Solution, Risk, Testing)
- At least one `[HIGH]`/`[MEDIUM]`/`[LOW]` confidence tag
- At least 3 concrete file-path or line-range references

If a proposal fails: `SendMessage(to="proposer-a", message="<specific revision ask>")`. The teammate resumes with full Round 1 context — no re-spawn needed.

**Convergence check** — same core finding AND same approach?
- **Yes (converged)**: skip to §"Convergence path" below.
- **No**: proceed to Round 2.

### Round 2: Synthesizer C

```python
Agent(
    subagent_type="general-purpose",
    description="Synthesizer C: synthesis + isolated reviews",
    team_name=f"adv-{slug[:20]}",
    name="synthesizer-c",
    run_in_background=True,
    prompt=build_synthesizer_prompt(
        problem_statement=...,
        source_files=[...],
        proposal_a_path=f"{output_dir}/proposal-A.md",
        proposal_b_path=f"{output_dir}/proposal-B.md",
        output_dir=output_dir,
        synthesizer_role=open("agents/synthesizer.md").read(),
    ),
)
```

Synthesizer prompt MUST instruct Phase 0 (independent code exam before reading proposals) and produce **three files**: `proposal-C.md`, `review-for-A.md`, `review-for-B.md`.

### Round 2 gate: Artifact completeness (P0.3)

When C's "done" arrives, the lead MUST verify on disk:

```python
assert os.path.exists(f"{output_dir}/proposal-C.md")
assert os.path.exists(f"{output_dir}/review-for-A.md")
assert os.path.exists(f"{output_dir}/review-for-B.md")
```

If any is missing, `SendMessage(to="synthesizer-c", message="review-for-A.md is missing; please produce all three artifacts")`. Do NOT proceed to Round 3 with incomplete artifacts — past runs have silently skipped proposal-C.md and produced orphaned final-plan.md.

### Round 3: Critique (A and B resume, in parallel)

```python
# Both SendMessage calls in one turn to run in parallel.
SendMessage(
    to="proposer-a",
    summary="Round 3: review feedback on your proposal",
    message="""A technical reviewer has examined proposal-A.md. Read ONLY
{output_dir}/review-for-A.md and your own proposal-A.md. You must NOT read
proposal-B.md, proposal-C.md, review-for-B.md, or final-plan.md.

{critic_role_body}

Reply to team-lead with your detailed feedback. Then go idle."""
)

SendMessage(
    to="proposer-b",
    summary="Round 3: review feedback on your proposal",
    message="""<mirror for B>"""
)
```

### Round 4: Reconciliation

When both critiques arrive:

```python
SendMessage(
    to="synthesizer-c",
    summary="Round 4: reconcile critiques into final plan",
    message=f"""Both original investigators critiqued your synthesis. Reconcile
their feedback and produce:

1. {output_dir}/final-plan.md — the final implementation plan
2. {output_dir}/reconciliation-notes.md — one line per critique point:
   "A: <point> → ACCEPTED/PARTIAL/REJECTED — <reason>"

Feedback from Proposer A:
{a_feedback_body}

Feedback from Proposer B:
{b_feedback_body}

{reconciler_role_body}"""
)
```

### Round 5 (optional): External review

If the problem is high-stakes or crosses multiple services, the lead may dispatch one more review pass via an un-teamed `Agent` (general-purpose) to produce `review-external.md`. This reviewer reads `final-plan.md` + source and reports on blind spots the adversarial rounds may have missed. Present both artifacts to the user.

### Round 6: Cleanup

```python
# Shutdown all teammates in parallel.
SendMessage(to="proposer-a", message={"type": "shutdown_request", "reason": "complete"})
SendMessage(to="proposer-b", message={"type": "shutdown_request", "reason": "complete"})
SendMessage(to="synthesizer-c", message={"type": "shutdown_request", "reason": "complete"})

# Wait for shutdown acknowledgments (they arrive as conversation turns), then:
TeamDelete()
```

If `TeamDelete` returns `Cannot cleanup team with N active member(s)`, a shutdown hasn't completed yet — wait for the next idle notification and retry.

---

## Convergence path (A and B agreed)

```python
# No Round 2 synthesis. Spawn C as a lightweight reviewer.
Agent(
    team_name=..., name="synthesizer-c",
    prompt=build_convergence_review_prompt(
        proposal_a_path=...,
        proposal_b_path=...,
        output_path=f"{output_dir}/final-plan.md",
    ),
    run_in_background=True,
)
# No review-for-A.md, no review-for-B.md, no proposal-C.md.
# Artifacts: problem-statement.md, proposal-A.md, proposal-B.md, final-plan.md.
```

---

## Error handling

| Round | Failure | Recovery |
|---|---|---|
| 1 | One proposer teammate sends error or never sends done | Wait 10 min. If still silent, `SendMessage(to=name, message="status?")`. If still silent, proceed with one proposal. |
| 1 | Both fail | Abort. Report to user. |
| 2 | C produces partial artifacts | Resume via SendMessage, name the missing file. One retry. |
| 2 | C sends error | Shut down C, spawn `synthesizer-c-2` with same prompt. |
| 3 | One critic silent | Proceed to Round 4 noting which critique is missing. |
| 3 | Both silent | Proceed with no critique — C reconciles against its own synthesis. |
| 4 | C fails to produce final-plan.md | Present proposal-C.md as the final artifact. |

---

## Task dependency chain

```
#1 Proposer A ─┐
               ├─► #3 Synthesis C ──► #4 Round 3 critique ──► #5 Reconciliation
#2 Proposer B ─┘
```

Tasks auto-unblock as upstream tasks complete. Teammates can claim tasks via `TaskUpdate(owner=<name>)` — or the lead assigns explicitly. Either works.

---

## Output directory

```
{project}/.dev/proposals/{slug}/
├── problem-statement.md
├── proposal-A.md
├── proposal-B.md
├── proposal-C.md             ← lead-only, not shown to A or B
├── review-for-A.md           ← A's eyes only — zero references to B
├── review-for-B.md           ← B's eyes only — zero references to A
├── reconciliation-notes.md   ← accepted/rejected critiques with reasons
├── final-plan.md             ← the deliverable
└── review-external.md        ← optional Round 5 pass
```

On the convergence path, `proposal-C.md`, `review-for-A.md`, `review-for-B.md`, and `reconciliation-notes.md` are **absent by design** — their absence is the signal that the run was convergent.
