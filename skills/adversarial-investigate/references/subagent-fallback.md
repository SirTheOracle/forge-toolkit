# Subagent Fallback (When Agent Teams Not Available)

If the Agent Teams feature (`TeamCreate`, `SendMessage`, `Agent(team_name, name)`) is not available on your plan, the adversarial-investigate framework can still run using the standard `Agent` tool with file-based coordination.

## Key Differences from Agent Teams

| Aspect | Agent Teams | Subagent Fallback |
|---|---|---|
| Sessions persist | Yes — A, B, C stay alive | No — each round spawns fresh subagents |
| Round 3 context | A and B retain Round 1 reasoning | A and B re-read investigation + investigation-notes from disk |
| Communication | SendMessage (inbox) | Files on disk |
| Completion detection | Inbox messages | Notification when background agent completes |
| Isolation | Structural (separate context windows) | Even stronger (fully separate processes) |
| Reasoning trail | Retained in context | Written to investigation-notes files |

## Workflow

### Round 0: Setup

Same as agent-teams workflow — apply all Step 0 gates from `SKILL.md`:

- 0A: empty-args short-circuit (require `--output-dir`, `--env`, problem-statement.md)
- 0B/0C: read inputs and original-pipeline context (read-only)
- 0D: confirm `--env` (handle hybrid case)
- 0E: exploration budget (≤3–5 files)
- 0F: evidence floor — bail to user if no repro/no context/no telemetry

No `TeamCreate` in fallback mode.

### Round 1: Parallel Investigation

```
# Spawn A (background)
Agent({
  description: "Investigator A: Forward-from-trigger",
  subagent_type: "general-purpose",
  run_in_background: true,
  prompt: """
    {problem-statement.md content}
    {repro.md content if present}
    {original-pipeline context if --original-slug, marked as CONTEXT NOT SUSPECTS}

    {investigator role from agents/investigator.md}
    {investigation format from references/investigation-format.md}

    Strategy: A — Forward-from-trigger
    Env: {prod | dev}
    {hybrid-case clause if applicable}

    Save your investigation as: {output_dir}/investigation-A.md
    ALSO save your reasoning trail as: {output_dir}/investigation-notes-A.md
      (full reasoning, rejected hypotheses, assumptions tested, open questions)

    {ISOLATION RULE — verbatim from SKILL.md, including Forbidden writes}
  """
})

# Spawn B (background, same turn for parallel)
Agent({
  description: "Investigator B: Backward-from-failure-point",
  subagent_type: "general-purpose",
  run_in_background: true,
  prompt: """
    {same structure, Strategy B, save as investigation-B.md and investigation-notes-B.md}
  """
})
```

**Investigation notes** (`investigation-notes-A.md`, `investigation-notes-B.md`): Since subagent sessions don't persist, each investigator must also write a notes file containing:

- Full reasoning trail (what they examined, in order, and why)
- Rejected hypotheses (what they considered and ruled out, with reasoning)
- Key decision rationale (why they chose their committed hypothesis over alternatives)
- Open questions (what they weren't sure about, and what would resolve it)

These notes are read back in Round 3 to compensate for the loss of Round 1 context.

#### Round 1 gates

Same as agent-teams workflow:

1. **Artifact gate** — verify both `investigation-A.md` and `investigation-B.md` exist on disk and are non-empty. If either is missing/empty, re-spawn the failing subagent with a specific instruction.
2. **Quality gate** — required sections, ≥3 concrete file:line/log references, ≥1 confidence annotation, non-empty Symptom-vs-Cause Analysis. If a gate fails, re-spawn with specific feedback.
3. **Convergence check** — same core finding AND same evidence? If yes, skip to lightweight review.
4. **(Interactive) checkpoint 1** if `--interactive` is set.

### Round 2: Synthesis

```
Agent({
  description: "Synthesizer C: synthesis + isolated reviews",
  subagent_type: "general-purpose",
  run_in_background: true,
  prompt: """
    PHASE 0: Before reading the investigations, independently examine the
    source files and evidence below. Form your own preliminary view of the
    bug shape.

    {problem-statement.md content}
    {repro.md content if present}
    {original-pipeline context if --original-slug}

    Source files: {list of file paths}

    PHASE 1+: Read these investigations and produce three files:
    - {output_dir}/investigation-A.md
    - {output_dir}/investigation-B.md

    {synthesizer role from agents/inv-synthesizer.md}
    {investigation format from references/investigation-format.md}

    Produce THREE files:
    1. {output_dir}/investigation-C.md — Full synthesis (lead-only; not shown
       to A or B). Includes Source Investigation Assessment + Synthesis
       Decisions + tentative convergence status.
    2. {output_dir}/review-for-A.md — Feedback for A only. Write as if B
       does not exist. Zero references to B.
    3. {output_dir}/review-for-B.md — Feedback for B only. Write as if A
       does not exist. Zero references to A.
  """
})
```

#### Round 2 gate (artifact completeness)

Verify all three files exist on disk: `investigation-C.md`, `review-for-A.md`, `review-for-B.md`. If any is missing, re-spawn synthesizer with a specific instruction (one retry).

#### Interactive checkpoint 2 (if `--interactive` mode)

Pause for user review of `investigation-C.md`.

### Round 3: Feedback

```
# Spawn two feedback subagents in parallel
Agent({
  description: "Critic A: Round 3 feedback on investigation-A",
  subagent_type: "general-purpose",
  run_in_background: true,
  prompt: """
    Read these files (in order):
    1. {output_dir}/investigation-A.md (this was YOUR original investigation)
    2. {output_dir}/investigation-notes-A.md (YOUR reasoning trail — use it
       to restore your original context)
    3. {output_dir}/review-for-A.md (a synthesizer's feedback on your work)

    ISOLATION RULE: Do NOT read investigation-B.md, investigation-C.md,
    review-for-B.md, investigation-notes-B.md, diagnosis.md, or
    reconciliation-notes.md.

    {critic role from agents/inv-critic.md}

    Save your feedback as: {output_dir}/feedback-A.md
  """
})

Agent({
  description: "Critic B: Round 3 feedback on investigation-B",
  subagent_type: "general-purpose",
  run_in_background: true,
  prompt: """{mirror for B, save as feedback-B.md}"""
})
```

**Note:** these subagents don't have the original investigation context — they read their investigation + investigation-notes from disk. The notes compensate for the missing Round 1 context, making feedback more grounded than re-reading the investigation alone.

### Round 4: Reconciliation

```
# If one feedback file is missing (subagent failed), proceed with available feedback
Agent({
  description: "Reconciler: produce final diagnosis from critiques",
  subagent_type: "general-purpose",
  prompt: """
    Read these files:
    - {output_dir}/investigation-C.md (this was YOUR synthesis)
    - {output_dir}/feedback-A.md
    - {output_dir}/feedback-B.md (if present)

    {reconciler role from agents/inv-reconciler.md}
    {diagnosis format from references/diagnosis-format.md}

    Produce TWO files:
    1. {output_dir}/diagnosis.md — final diagnosis
    2. {output_dir}/reconciliation-notes.md — accept/partial/reject audit trail

    You are explicitly authorized to commit to INSUFFICIENT EVIDENCE as the
    convergence status. Do not manufacture confidence to look decisive.
  """
})
```

## Error Handling

Same recovery logic as agent-teams workflow, adapted for subagents:

| Round | Failure | Recovery |
|---|---|---|
| 0 | Evidence floor not met | Surface to user; do not spawn |
| 1 | One subagent fails | Wait for the other. Skip adversarial process if only one arrives, note it in the synthesis prompt. |
| 1 | Both fail | Abort. Report to user. |
| 2 | Synthesizer fails | Re-spawn with same prompt. If second attempt fails, present investigations as the output. |
| 3 | One feedback subagent fails | Proceed to Round 4 with available feedback. |
| 3 | Both fail | Proceed to Round 4 with no feedback (reconciler reconciles against its own synthesis). |
| 4 | Reconciler fails | Re-spawn. If second attempt fails, present investigation-C.md as the output, noting reconciliation incomplete. |

## Output Directory (Fallback)

```
{output_dir}/
├── problem-statement.md
├── repro.md                       ← if fix-reproducer ran
├── investigation-A.md
├── investigation-B.md
├── investigation-notes-A.md       ← Subagent fallback only: full reasoning trail
├── investigation-notes-B.md       ← Subagent fallback only: full reasoning trail
├── investigation-C.md             ← Lead-only (absent on convergence)
├── review-for-A.md                ← A's eyes only (absent on convergence)
├── review-for-B.md                ← B's eyes only (absent on convergence)
├── feedback-A.md                  ← Subagent fallback only: A's Round 3 feedback on disk
├── feedback-B.md                  ← Subagent fallback only: B's Round 3 feedback on disk
├── reconciliation-notes.md        ← (absent on convergence)
└── diagnosis.md                   ← FINAL DELIVERABLE
```

## Modes

| Mode | Behavior |
|---|---|
| **Default** | Fully automated. Lead orchestrates all rounds without pausing. |
| **Interactive** (`--interactive`) | Pauses after Round 1 and Round 2 for user review. |

## When to Use This Fallback

- Agent Teams is not available on your plan
- You see an error like: "The 'Agent Teams' feature (TeammateTool, SendMessage, TeamCreate) is not available on this plan"
- You want even stronger isolation (subagents are fully separate processes with zero communication)

## Tradeoff Summary

The fallback works, but you lose:

- **Round 3 grounding from Round 1 context.** Critics in fallback mode reconstruct intent from the investigation + investigation-notes on disk. This is weaker than reading from a live transcript that contains the full reasoning. Mitigated by writing thorough investigation-notes in Round 1.
- **Reconciler-side context.** The reconciler in fallback mode re-reads `investigation-C.md` from disk; in the Teams flow, it resumes from its Round 2 reasoning context. Less impactful than the Round 3 loss, but still real.

If Agent Teams is available, prefer it. Use the fallback only when forced.
