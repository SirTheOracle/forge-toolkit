---
name: adversarial-implementation
description: >
  Adversarial implementation framework that turns a vetted plan into a precise `implementation.md`
  with exact code diffs, test specifications, and a plan-item coverage matrix. This Codex copy
  preserves the generalized multi-agent protocol from the authoritative Claude version while keeping
  Codex-specific discoverability and execution patterns. Use this skill after `adversarial-proposal`
  produces a `final-plan.md`, or whenever the user wants to "create an implementation", "implement
  the plan", "write an implementation doc", "adversarial implementation", or prove that every plan
  item maps to a specific code change and test before coding begins.
---

# Adversarial Implementation Framework

## Overview

This skill takes a `final-plan.md` (typically from the adversarial-proposal skill) and produces a complete, vetted `implementation.md` with exact code diffs, test specifications, and a coverage matrix that proves every plan item is addressed.

The same adversarial process that works for planning applies here: two agents independently produce implementation documents from the same plan, a synthesizer merges the best of both, the original agents critique the synthesis, and the synthesizer reconciles into a final implementation document.

The critical output is the **coverage matrix** — a table mapping every plan item to a specific code diff and a specific test. Zero gaps allowed.

The final document must be mechanically executable by `$forge-coder`, not merely understandable to a human reviewer. Parser-safe structure, unique `old_string` values, runnable commit-group tests, and exact new-file encoding are mandatory.

This Codex copy keeps the generalized Claude workflow but expects Codex-native delegation. When the upstream docs mention teammate APIs, translate them to Codex subagents or use the file-based fallback.

## When to Use

- After `adversarial-proposal` produces a `final-plan.md`
- When you have any implementation plan and want vetted, exact diffs before coding
- When previous implementations have missed items from the plan
- When testing coverage is critical

## Prerequisites

This skill follows the generalized multi-agent protocol from the authoritative Claude version.

- In Codex, prefer native subagents to preserve isolation between A, B, and C.
- When the upstream docs mention teammate APIs, map them to Codex delegation primitives such as `spawn_agent`, `send_input`, and `wait_agent`.
- If persistent subagent messaging is not practical, use `references/subagent-fallback.md`.

## Architecture

```
INPUT: final-plan.md (from adversarial-proposal or any plan)
─────────────────────────────────────────────────

ROUND 0: Setup
─────────────────────────────────────────────────
  Lead reads final-plan.md → extracts source files + plan items
  Strategy pair: A=Surgical, B=Coverage

ROUND 1: Independent Implementation Planning (parallel)
─────────────────────────────────────────────────
  Agent A (Surgical)              Agent B (Coverage Guardian)
  ┌──────────────────┐            ┌──────────────────┐
  │ Own context       │            │ Own context       │
  │                   │            │                   │
  │ Sees ONLY:        │            │ Sees ONLY:        │
  │ - final-plan.md   │            │ - final-plan.md   │
  │ - Source files    │            │ - Source files     │
  │ - Test files      │            │ - Test files       │
  │                   │            │                   │
  │ Focus: minimal    │            │ Focus: complete    │
  │ diffs, correct    │            │ coverage, tests    │
  │ ordering          │            │ for everything     │
  │                   │            │                   │
  │ CANNOT see B ─────┼── ✗ ──────┼── CANNOT see A    │
  │                   │            │                   │
  │ → impl-A.md       │            │ → impl-B.md       │
  └───────────────────┘            └───────────────────┘

  ◄── Quality check + Convergence check ──►

ROUND 2: Synthesis
─────────────────────────────────────────────────
  Synthesizer C
  ┌──────────────────────────────────────┐
  │ Phase 0: Reads plan + source files   │
  │ Phase 1-2: Reads impl-A + impl-B    │
  │ Phase 3: Verifies diffs against src  │
  │ Phase 4: Merges into impl-C.md      │
  │ Phase 5: Writes isolated reviews     │
  │                                      │
  │ → impl-C.md       (lead-only)       │
  │ → review-for-A.md (A's eyes only)   │
  │ → review-for-B.md (B's eyes only)   │
  └──────────────────────────────────────┘

ROUND 3: Feedback (parallel, ISOLATED)
─────────────────────────────────────────────────
  Agent A                          Agent B
  ┌──────────────────┐             ┌──────────────────┐
  │ Reads ONLY:       │             │ Reads ONLY:       │
  │ - review-for-A.md │             │ - review-for-B.md │
  │ + own impl-A.md   │             │ + own impl-B.md   │
  │                   │             │                   │
  │ → feedback        │             │ → feedback        │
  └──────────────────┘             └──────────────────┘

ROUND 4: Reconciliation
─────────────────────────────────────────────────
  Synthesizer C
  ┌──────────────────────────────────────┐
  │ Receives feedback from A and B       │
  │ Verifies disputed diffs against src  │
  │ Reconciles into implementation.md    │
  │ → implementation.md                  │
  └──────────────────────────────────────┘

OUTPUT: implementation.md (in same directory as final-plan.md)
```

### Why Two Strategies

| Agent | Strategy | Focus | Catches |
|-------|----------|-------|---------|
| A (Surgical) | Minimal diffs, correct ordering | Unnecessary changes, over-engineering | —  |
| B (Coverage) | Complete tests, nothing dropped | Missed plan items, untested changes, broken existing tests | — |

The natural tension: A wants to do less, B wants to cover more. The synthesis finds the right balance — every plan item addressed, but no unnecessary bloat.

---

## How to Execute

When the user triggers this skill, you (the lead) orchestrate the entire workflow.

For Codex, interpret the teammate-specific call patterns in the upstream references as conceptual. Preserve the same round structure, isolation rules, and artifact flow with Codex subagents or the fallback workflow.

### Step 0: Gather Context

1. Get the **path to `final-plan.md`** from the user's command
2. **Read the final plan** — extract:
   - All source file paths referenced
   - All test file paths referenced (or derive them from source paths)
   - Every discrete plan item (these become the coverage matrix rows)
3. **Determine output directory** — same directory as `final-plan.md`
4. **Rename existing review files** if they exist from the proposal phase:
   - `review-for-A.md` → `proposal-review-for-A.md`
   - `review-for-B.md` → `proposal-review-for-B.md`
5. Read the agent role files to embed in prompts:
   - `agents/implementer.md`
   - `agents/guardian.md`
   - `agents/synthesizer.md`
   - `agents/critic.md`
   - `agents/reconciler.md`
   - `references/implementation-format.md`

### Step 1: Round 1 — Spawn A and B (Parallel)

Spawn both agents **in the same turn** for parallel execution.

**Agent A prompt must include:**
- Full content of `final-plan.md`
- List of source file paths to read and verify
- Implementer role (from `agents/implementer.md`)
- Implementation format (from `references/implementation-format.md`)
- Strategy assignment: **Surgical** — minimal diffs, correct ordering
- Output path: `{output_dir}/impl-A.md`
- Investigation notes path: `{output_dir}/impl-notes-A.md`
- Isolation rule: do NOT read impl-B.md or any other impl files

**Agent B prompt must include:**
- Same plan and source files
- Guardian role (from `agents/guardian.md`)
- Same implementation format
- Strategy assignment: **Coverage** — complete tests, nothing dropped
- Output path: `{output_dir}/impl-B.md`
- Investigation notes path: `{output_dir}/impl-notes-B.md`
- Isolation rule: do NOT read impl-A.md or any other impl files

**Both prompts must instruct the agent to WAIT after completing**, not exit.

Monitor inbox for "done" messages from both.

After both arrive: **quality check** then **convergence check**. Treat forge-coder executability as part of the quality check, not a later nice-to-have.

### Step 1.5: Quality Check

Verify each implementation doc has:
1. Plan items inventory — all items from the plan listed
2. Implementation steps with exact `old_string → new_string` diffs
3. Every modification step uses an `old_string` that is unique in the current target file
4. Every new-file step uses `**Action**: new_file`, `# old_string` with the literal body `# new file`, and a `# new_string` block containing the full file content
5. No standalone `# new_file` blocks, prose placeholders, partial snippets for new files, or "rest of file unchanged" shortcuts
6. Coverage matrix with Status column
7. Test specifications (new and modified)
8. Every step includes a concrete verification command
9. Commit groups with tests that are runnable immediately after that group's steps are applied
10. Definition of done

If a doc fails, message the agent with specific feedback and request revision. Do not advance a document that is human-readable but not forge-coder-executable.

### Step 1.6: Convergence Check

If both implementations produce the same diffs and same tests for all plan items, skip Rounds 2-3. Spawn C for a lightweight review (verify diffs against source, check for missed items), then produce `implementation.md` directly.

### Step 2: Round 2 — Spawn C

```
Spawn synthesizer with:
- Instruction to read source files FIRST (Phase 0)
- Then read impl-A.md and impl-B.md
- Synthesizer role (from agents/synthesizer.md)
- Produce THREE files: impl-C.md, review-for-A.md, review-for-B.md
- Isolation rules for review files
- Wait for instructions
```

### Step 3: Round 3 — Message A and B

When C completes, message both agents with their review files. Same isolation rules as the proposal skill.

### Step 4: Round 4 — Forward Feedback to C

When both feedback messages arrive, forward to C with the reconciler role. C produces `implementation.md`.

### Step 4.5: Forge-Coder Compatibility Gate

Before presenting the final `implementation.md`, do a final mechanical review:

1. Every step has exactly one `File` and exactly one `old_string/new_string` diff pair
2. Every modification `old_string` is copied from the current source and is unique enough for an edit tool to apply without ambiguity
3. Every new file uses the canonical format:
   - `**Action**: new_file`
   - `# old_string` body is exactly `# new file`
   - `# new_string` contains the full file contents
4. No step relies on prose like "insert this near..." or "rest of file unchanged"
5. Every commit-group test command can run after the steps in that group, without depending on files created in later groups
6. Coverage matrix, test specifications, and definition of done are all still present after reconciliation

If any of these checks fail, send the document back to the reconciler with concrete fixes. Do not present it as final.

### Step 5: Cleanup and Present

Shutdown all agents. Present `implementation.md` to the user only after the compatibility gate passes. Offer to:
- Show the full deliberation trail
- Explain specific decisions
- Proceed to execute the implementation
- Re-run any round

---

## Building the Agent Prompts

Each prompt must be **self-contained** — embed everything inline.

### What to Embed in Every Prompt

1. **The full content of `final-plan.md`** — verbatim
2. **Source file paths** — explicit list of every file to read and verify
3. **Test file paths** — existing tests that may need updating
4. **The agent role** — full content from the relevant `agents/*.md` file
5. **The implementation format** — full content from `references/implementation-format.md`
6. **The output path and filename** — exact save location
7. **The isolation rule** — for A and B in Rounds 1 and 3
8. **The strategy assignment** — Surgical for A, Coverage for B
9. **The "wait" instruction** — tell agents to wait after completing

### Critical Instruction for All Agents

> "You MUST read every source file referenced in the plan BEFORE writing any diffs. Verify that line numbers, function signatures, and variable names in the plan match the actual code. If they don't, use the actual code — do not copy stale references from the plan."

---

## Error Handling

### Agent Timeout / Exit Recovery

| Round | Failure | Recovery |
|-------|---------|----------|
| Round 1 | One agent fails | Wait for the other. C reviews single doc for gaps. |
| Round 1 | Both fail | Abort workflow. Report to user. |
| Round 2 | Synthesizer fails | Re-spawn C. If second attempt fails, present both raw docs. |
| Round 3 | One agent fails | Proceed to Round 4 with available feedback. |
| Round 3 | Both fail | Proceed to Round 4 with no feedback. C reconciles from synthesis alone. |
| Round 4 | Synthesizer fails | Re-spawn reconciler. If second attempt fails, present impl-C.md. |

### Quality Gate Failures

If an implementation doc fails the quality check after Round 1, message the agent with specific feedback and request revision before proceeding.

---

## Known Tradeoffs

**Reconciler bias toward own synthesis**: Same as the proposal skill — C naturally favors its own impl-C.md when reconciling. The reconciler role includes bias awareness guidance, but for critical implementations, review the synthesis yourself before feedback rounds.

**Diff staleness**: Between the time agents read source files and the time `implementation.md` is produced, the source code could change (if someone is actively coding). The implementation doc captures a point-in-time snapshot. If the code changes, diffs may need re-verification.

---

## Invoking the Skill

### Direct Prompt

```
/adversarial-implementation .dev/proposals/caption-audio-sync-drift/final-plan.md
```

### As a Reusable Command Wrapper

Save as the equivalent command wrapper for your environment:

```
Read the installed adversarial implementation skill and use the workflow it describes.
Use the workflow described to produce an implementation document from the given plan.

Create three isolated workers: implementer-a (Surgical) and implementer-b (Coverage Guardian) work
independently (FULLY ISOLATED), synthesizer-c reviews both and produces a synthesis. Then A
and B critique C. C reconciles into a final implementation.md.

Plan path: $ARGUMENTS
```

---

## Output Directory

Files are saved in the **same directory** as the input `final-plan.md`:

```
{project}/.dev/proposals/{issue-slug}/
├── final-plan.md                ← Input
├── impl-A.md                   ← Round 1: Surgical Implementer
├── impl-B.md                   ← Round 1: Coverage Guardian
├── impl-notes-A.md             ← Round 1: A's investigation notes (subagent fallback)
├── impl-notes-B.md             ← Round 1: B's investigation notes (subagent fallback)
├── impl-C.md                   ← Round 2: Synthesis (lead-only)
├── review-for-A.md             ← Round 2: C's review for A
├── review-for-B.md             ← Round 2: C's review for B
├── impl-feedback-A.md          ← Round 3: A's feedback (subagent fallback)
├── impl-feedback-B.md          ← Round 3: B's feedback (subagent fallback)
└── implementation.md            ← Round 4: FINAL DELIVERABLE
```

---

## Reference Files

| File | When to Read | Purpose |
|------|-------------|---------|
| `references/implementation-format.md` | When building agent prompts | Implementation doc template to embed |
| `references/subagent-fallback.md` | If persistent teammate messaging is not available | Alternative workflow using Codex subagents or file coordination |
| `agents/implementer.md` | When building A's prompt | Surgical implementer role |
| `agents/guardian.md` | When building B's prompt | Coverage guardian role |
| `agents/synthesizer.md` | When building C's Round 2 prompt | Synthesis role |
| `agents/critic.md` | When messaging A and B in Round 3 | Feedback guidance |
| `agents/reconciler.md` | When messaging C in Round 4 | Reconciliation + bias awareness |

## Agent Roles Summary

| Agent | File | Used In | Purpose |
|-------|------|---------|---------|
| Implementer | `agents/implementer.md` | Round 1 (A) | Surgical: minimal exact diffs |
| Guardian | `agents/guardian.md` | Round 1 (B) | Coverage: tests for everything, nothing dropped |
| Synthesizer | `agents/synthesizer.md` | Round 2 (C) | Merge best of both, verify against source |
| Critic | `agents/critic.md` | Round 3 (A and B) | Review synthesis, flag issues |
| Reconciler | `agents/reconciler.md` | Round 4 (C) | Reconcile feedback, produce final implementation.md |
