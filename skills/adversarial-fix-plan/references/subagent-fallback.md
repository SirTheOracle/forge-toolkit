# Subagent Fallback (When Agent Teams Not Available)

If Agent Teams (`TeamCreate`, `SendMessage`, `Agent(team_name, name)`) is not available on your plan, the adversarial-fix-plan framework can still run using the standard `Agent` tool with file-based coordination.

## Key Differences from Agent Teams

| Aspect | Agent Teams | Subagent Fallback |
|---|---|---|
| Sessions persist | Yes — A, B, C stay alive | No — each round spawns fresh subagents |
| Round 3 context | A and B retain Round 1 reasoning | A and B re-read plan + plan-notes from disk |
| Communication | SendMessage (inbox) | Files on disk |
| Completion detection | Inbox messages | Notification when background agent completes |
| Isolation | Structural (separate context windows) | Even stronger (fully separate processes) |
| Reasoning trail | Retained in context | Written to plan-notes files |

## Workflow

### Round 0: Setup

Same as agent-teams workflow — apply all Step 0 gates from `SKILL.md`:

- 0A: empty-args short-circuit (require `--output-dir`, `--env`, `diagnosis.md`)
- 0B: read inputs
- 0C: parse `convergence_status` from diagnosis.md → write BLOCKED fix-plan.md and exit if INSUFFICIENT EVIDENCE / missing
- 0D: read original-pipeline context
- 0E: detect mode (revise mode if `fix-review.md` exists)
- 0F: confirm `--env`
- 0G: exploration budget (≤3–5 files)

No `TeamCreate` in fallback mode.

### Round 1: Parallel Planning (Full Mode)

```
# Spawn A (Surgical, background)
Agent({
  description: "Planner A: Surgical fix",
  subagent_type: "general-purpose",
  run_in_background: true,
  prompt: """
    {diagnosis.md content}
    {problem-statement.md content}
    {repro.md content if present}
    {original-pipeline context if --original-slug, marked as CONTEXT NOT SUSPECTS}

    {planner role from agents/fix-planner.md}
    {plan format from references/fix-plan-format.md}

    Strategy: A — Surgical
    Env: {prod | dev}

    Save your plan as: {output_dir}/fixA.md
    ALSO save your reasoning trail as: {output_dir}/plan-notes-A.md
      (full reasoning, rejected approaches, scope decisions, open questions)

    {ISOLATION RULE — verbatim from SKILL.md, including the no-re-investigation rule}
  """
})

# Spawn B (Robust, background, same turn)
Agent({
  description: "Planner B: Robust fix",
  subagent_type: "general-purpose",
  run_in_background: true,
  prompt: """
    {same structure, Strategy B, save as fixB.md and plan-notes-B.md}
  """
})
```

**Plan notes** (`plan-notes-A.md`, `plan-notes-B.md`): Since subagent sessions don't persist, each planner must also write a notes file containing:

- Full reasoning trail (what they examined, in order, and why)
- Rejected approaches (what they considered and didn't take, with reasoning)
- Scope-decision rationale (why they chose their scope on the Surgical-Robust spectrum, beyond the strategy assignment)
- Defenses considered but not proposed (Robust only — what fragilities they noted but didn't add defenses for, and why)
- Open questions (things they weren't fully sure about)

These notes are read back in Round 3 to compensate for the loss of Round 1 context.

#### Round 1 gates

Same as agent-teams workflow:

1. **Artifact gate** — verify both `fixA.md` and `fixB.md` exist and are non-empty
2. **Quality gate** — required sections, sequencing present, rollback non-trivial, ≥1 confidence annotation
3. **Diagnosis-disagreement check** — if either plan is BLOCKED, stop and report
4. **Convergence check**
5. **(Interactive) checkpoint 1** if `--interactive`

### Round 2: Synthesis

```
Agent({
  description: "Synthesizer C: synthesis + isolated reviews",
  subagent_type: "general-purpose",
  run_in_background: true,
  prompt: """
    PHASE 0: Before reading the plans, independently examine the source
    files cited in diagnosis.md. Form your own view of the change-shape
    and blast radius FIRST.

    {diagnosis.md content}
    {problem-statement.md content}
    {repro.md content if present}
    {original-pipeline context if --original-slug}

    Source files: {list}

    PHASE 1+: Read these plans:
    - {output_dir}/fixA.md
    - {output_dir}/fixB.md

    {synthesizer role from agents/fixp-synthesizer.md}
    {plan format from references/fix-plan-format.md}

    Produce THREE files:
    1. {output_dir}/fixC.md — Full synthesis (lead-only; not shown to A or B).
       Includes Source Plan Assessment + Synthesis Decisions.
    2. {output_dir}/review-for-A.md — Feedback for A only. Write as if B
       does not exist. Zero references to B.
    3. {output_dir}/review-for-B.md — Feedback for B only. Write as if A
       does not exist. Zero references to A.
  """
})
```

#### Round 2 gate (artifact completeness)

Verify all three files exist on disk. If any is missing, re-spawn with a specific instruction (one retry).

#### Interactive checkpoint 2 (if `--interactive`)

Pause for user review of `fixC.md`.

### Round 3: Feedback

```
# Spawn two feedback subagents in parallel
Agent({
  description: "Critic A: Round 3 feedback on fixA",
  subagent_type: "general-purpose",
  run_in_background: true,
  prompt: """
    Read these files (in order):
    1. {output_dir}/fixA.md (this was YOUR original plan)
    2. {output_dir}/plan-notes-A.md (YOUR reasoning trail — restores context)
    3. {output_dir}/review-for-A.md (a synthesizer's feedback on your plan)

    ISOLATION RULE: Do NOT read fixB.md, fixC.md, review-for-B.md,
    plan-notes-B.md, fix-plan.md, reconciliation-notes.md, fix-review.md,
    revision-notes.md.

    {critic role from agents/fixp-critic.md}

    Save your feedback as: {output_dir}/feedback-A.md
  """
})

Agent({
  description: "Critic B: Round 3 feedback on fixB",
  subagent_type: "general-purpose",
  run_in_background: true,
  prompt: """{mirror for B, save as feedback-B.md}"""
})
```

These subagents read their plan + plan-notes from disk to restore context.

### Round 4: Reconciliation

```
# If one feedback file is missing (subagent failed), proceed with available feedback
Agent({
  description: "Reconciler: produce final fix-plan from critiques",
  subagent_type: "general-purpose",
  prompt: """
    Read these files:
    - {output_dir}/fixC.md (this was YOUR synthesis)
    - {output_dir}/diagnosis.md
    - {output_dir}/feedback-A.md
    - {output_dir}/feedback-B.md (if present)

    {reconciler role from agents/fixp-reconciler.md}
    {plan-final format from references/fix-plan-final-format.md}

    Produce TWO files:
    1. {output_dir}/fix-plan.md — final deliverable
    2. {output_dir}/reconciliation-notes.md — accept/partial/reject audit
  """
})
```

### Revise Mode (single subagent)

Auto-detected when `fix-review.md` exists. Skip Rounds 1-4.

```
# Validate preconditions per SKILL.md Step 9A first

Agent({
  description: "Reviser: incorporate fix-review.md into v2",
  subagent_type: "general-purpose",
  prompt: """
    Read these files:
    - {output_dir}/fix-plan.md (this is v1, the plan being revised)
    - {output_dir}/fix-review.md (the reviewer's feedback)
    - {output_dir}/diagnosis.md (for reference)
    - {output_dir}/problem-statement.md, repro.md (if present)
    - Original-pipeline context if --original-slug

    {reviser role from agents/fixp-reviser.md}
    {plan-final format from references/fix-plan-final-format.md}

    Produce TWO files:
    1. {output_dir}/fix-plan.md — v2 (overwrite v1)
    2. {output_dir}/revision-notes.md — accept/partial/reject per review point
  """
})
```

After done + quality gate passes: archive `fix-review.md` to `fix-review-{timestamp}.md` per SKILL.md Step 9D.

## Error Handling

Same recovery logic as agent-teams workflow, adapted for subagents:

| Round | Failure | Recovery |
|---|---|---|
| 0 | INSUFFICIENT EVIDENCE / missing convergence_status | Write BLOCKED fix-plan.md, exit. |
| 0 (revise) | fix-review.md fails preconditions | Surface to user; do not enter revise mode without confirmation |
| 1 | One subagent fails | Wait for the other. Skip adversarial process if only one arrives. |
| 1 | Both fail | Abort. Report to user. |
| 1 | Diagnosis disagreement BLOCKING_ITEM | Stop. Do not proceed to Round 2. |
| 2 | Synthesizer fails | Re-spawn. If second attempt fails, present plans as the output. |
| 3 | One feedback subagent fails | Proceed to Round 4 with available feedback. |
| 3 | Both fail | Proceed to Round 4 with no feedback. |
| 4 | Reconciler fails | Re-spawn. If second attempt fails, present fixC.md as output. |
| R (revise) | Quality gate fails twice | Surface to user. |

## Output Directory (Fallback)

```
{output_dir}/
├── problem-statement.md
├── repro.md                       ← if fix-reproducer ran
├── diagnosis.md                   ← from adversarial-investigate
├── fixA.md
├── fixB.md
├── plan-notes-A.md                ← Subagent fallback only
├── plan-notes-B.md                ← Subagent fallback only
├── fixC.md                        ← Lead-only (absent on convergence)
├── review-for-A.md                ← (absent on convergence)
├── review-for-B.md                ← (absent on convergence)
├── feedback-A.md                  ← Subagent fallback only
├── feedback-B.md                  ← Subagent fallback only
├── reconciliation-notes.md        ← (absent on convergence)
├── fix-plan.md                    ← FINAL DELIVERABLE
├── fix-review.md                  ← Input (revise mode trigger)
├── fix-review-{ts}.md             ← Archived consumed reviews
└── revision-notes.md              ← Revise mode only
```

## Modes

| Mode | Behavior |
|---|---|
| **Full (default)** | Run all 4 rounds. Auto when fix-review.md absent. |
| **Revise** | Single-agent. Auto when fix-review.md present. |
| **Interactive** (`--interactive`) | Pauses after Round 1 and Round 2 for user review (full mode only). |

## When to Use This Fallback

- Agent Teams not available on your plan
- You see error: "The 'Agent Teams' feature (TeammateTool, SendMessage, TeamCreate) is not available on this plan"
- You want stronger isolation (subagents are fully separate processes)

## Tradeoff Summary

The fallback works, but you lose:

- **Round 3 grounding from Round 1 context.** Critics in fallback mode reconstruct intent from the plan + plan-notes on disk. This is weaker than reading from a live transcript that contains the full reasoning. Mitigated by writing thorough plan-notes in Round 1.
- **Reconciler-side context.** The reconciler in fallback mode re-reads `fixC.md` from disk; in the Teams flow, it resumes from its Round 2 reasoning context. Less impactful but still real.

If Agent Teams is available, prefer it. Use the fallback only when forced.
