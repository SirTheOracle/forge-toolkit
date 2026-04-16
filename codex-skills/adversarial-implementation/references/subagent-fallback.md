# Subagent Fallback (When Agent Teams Not Available)

If persistent teammate messaging is not available, the adversarial implementation framework runs using Codex subagents with file-based coordination. The `Task({...})` blocks below are conceptual; in Codex, map them to `spawn_agent`, `wait_agent`, and follow-up messages as needed.

## Key Differences from Agent Teams

| Aspect | Agent Teams | Subagent Fallback |
|--------|------------|-------------------|
| Sessions persist | Yes — A, B, C stay alive | No — each round spawns fresh subagents |
| Round 3 context | A and B retain Round 1 analysis | A and B re-read impl + investigation notes from disk |
| Communication | SendMessage (inbox) | Files on disk |
| Completion detection | Inbox messages | Poll for file existence |
| Isolation | Structural (separate context windows) | Even stronger (fully separate processes) |

## Workflow

### Round 0: Setup

```
// Validate that final-plan.md exists at the given path
// Identify the output directory (same directory as final-plan.md)
// Read the final plan to extract source file references
// Read agent role files to embed in prompts
```

### Round 1: Parallel Implementation Planning

```
// Spawn A (background) — Surgical Implementer
Task({
  subagent_type: "general-purpose",
  prompt: "{final plan content + source files + implementer role + impl format
            + Strategy: Surgical assignment
            + save implementation as impl-A.md
            + save investigation notes as impl-notes-A.md
            + isolation rule}",
  run_in_background: true
})

// Spawn B (background, same turn for parallel) — Coverage Guardian
Task({
  subagent_type: "general-purpose",
  prompt: "{final plan content + source files + guardian role + impl format
            + Strategy: Coverage assignment
            + save implementation as impl-B.md
            + save investigation notes as impl-notes-B.md
            + isolation rule}",
  run_in_background: true
})

// Poll for completion — watch for impl-A.md and impl-B.md on disk
```

**Investigation notes** (`impl-notes-A.md`, `impl-notes-B.md`): Since subagent sessions don't persist, each agent must also write investigation notes containing:
- Discrepancies found between plan and actual code (stale line numbers, changed signatures)
- Decisions made where the plan was ambiguous
- Why they chose their specific diff approach
- What they verified and what they assumed

#### Quality Check (after both arrive)

Verify each implementation doc has:
1. Plan items inventory (all plan items listed)
2. Implementation steps with exact diffs
3. Coverage matrix with no gaps
4. Test specifications
5. Commit groups
6. Definition of done

If a doc fails, re-spawn with specific feedback.

#### Convergence Check

If both implementations produce essentially the same diffs and tests, skip Rounds 2-3. Spawn C for a lightweight review (check for missed items, verify diffs against source), then produce `implementation.md` directly.

### Round 2: Synthesis

```
Task({
  subagent_type: "general-purpose",
  prompt: "
    IMPORTANT: Before reading the implementation docs, independently examine
    the final plan and source files. Form your own understanding FIRST.

    Then read {output_dir}/impl-A.md and {output_dir}/impl-B.md.

    {synthesizer role}

    Produce THREE files:
    1. {output_dir}/impl-C.md — Full synthesis (lead-only)
    2. {output_dir}/review-for-A.md — Feedback for A only. Zero references to B.
    3. {output_dir}/review-for-B.md — Feedback for B only. Zero references to A.

    Final plan: {output_dir}/final-plan.md
    Source files: {list of file paths}
  "
})
```

### Round 3: Feedback

```
// Spawn two feedback subagents in parallel
Task({
  subagent_type: "general-purpose",
  prompt: "Read {output_dir}/impl-A.md (this was YOUR implementation doc).
           Read {output_dir}/impl-notes-A.md (YOUR investigation notes).
           Now read {output_dir}/review-for-A.md (reviewer feedback on your work).

           ISOLATION RULE: Do NOT read impl-B.md, impl-C.md,
           review-for-B.md, impl-notes-B.md, or any other files.

           {critic role}
           Save feedback as {output_dir}/impl-feedback-A.md",
  run_in_background: true
})

Task({
  subagent_type: "general-purpose",
  prompt: "Read {output_dir}/impl-B.md (this was YOUR implementation doc).
           Read {output_dir}/impl-notes-B.md (YOUR investigation notes).
           Now read {output_dir}/review-for-B.md (reviewer feedback on your work).

           ISOLATION RULE: Do NOT read impl-A.md, impl-C.md,
           review-for-A.md, impl-notes-A.md, or any other files.

           {critic role}
           Save feedback as {output_dir}/impl-feedback-B.md",
  run_in_background: true
})
```

### Round 4: Reconciliation

```
Task({
  subagent_type: "general-purpose",
  prompt: "Read {output_dir}/impl-C.md (this was YOUR synthesis).
           Read {output_dir}/impl-feedback-A.md and {output_dir}/impl-feedback-B.md.

           Also re-read the source files to verify any disputed diffs.

           {reconciler role}

           Save as {output_dir}/implementation.md

           Source files: {list of file paths}"
})
```

## Polling for File Completion

```bash
# In a loop with sleep:
while [ ! -f "{output_dir}/impl-A.md" ] || [ ! -f "{output_dir}/impl-B.md" ]; do
  sleep 10
done
```

## Error Handling

| Round | Failure | Recovery |
|-------|---------|----------|
| Round 1 | One subagent fails | Wait for the other. Skip adversarial — C reviews single doc for gaps. |
| Round 1 | Both fail | Abort workflow. Report to user. |
| Round 2 | Synthesizer fails | Re-spawn with same prompt. If second attempt fails, present both raw docs. |
| Round 3 | One feedback subagent fails | Proceed to Round 4 with available feedback. |
| Round 3 | Both fail | Proceed to Round 4 with no feedback. |
| Round 4 | Reconciler fails | Re-spawn. If second attempt fails, present impl-C.md as output. |

## Output Directory

Files are saved in the **same directory** as the `final-plan.md` input:

```
{project}/.dev/proposals/{issue-slug}/
├── final-plan.md                ← Input (from adversarial-proposal)
├── impl-A.md                   ← Round 1: Surgical Implementer
├── impl-B.md                   ← Round 1: Coverage Guardian
├── impl-notes-A.md             ← Round 1: A's investigation notes
├── impl-notes-B.md             ← Round 1: B's investigation notes
├── impl-C.md                   ← Round 2: Synthesis (lead-only)
├── review-for-A.md             ← Round 2: C's review for A (overwrites proposal review)
├── review-for-B.md             ← Round 2: C's review for B (overwrites proposal review)
├── impl-feedback-A.md          ← Round 3: A's feedback
├── impl-feedback-B.md          ← Round 3: B's feedback
└── implementation.md            ← Round 4: Final deliverable
```

**Note:** The `review-for-A.md` and `review-for-B.md` files from the proposal phase will be overwritten. This is intentional — the implementation phase review files serve a different purpose. If you want to preserve the proposal-phase reviews, the lead should rename them before starting (e.g., `proposal-review-for-A.md`).
