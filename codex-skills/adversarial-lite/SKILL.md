---
name: adversarial-lite
description: >
  Codex-native lightweight adversarial proposal workflow for low-risk, bounded planning tasks.
  Uses isolated Codex subagents for two independent proposal passes plus a high-capacity synthesis
  pass that writes final-plan.md under .dev/proposals/{issue-slug}/. Use only when the user explicitly
  asks for "adversarial-lite", "lite proposal", "quick adversarial plan", "cheap/fast adversarial
  proposal", or explicitly wants a lower-cost bounded planning pass. Do not trigger for generic
  "investigate", "propose a fix", "come up with a plan", or high-confidence planning requests;
  use adversarial-proposal instead for ambiguous, high-risk, multi-service, or broad changes.
---

# Adversarial Lite

## Purpose

Use this skill to produce a fast, lower-cost `final-plan.md` for a small, low-risk bug, feature, refactor, or isolated architecture decision. The workflow preserves the key adversarial value: two isolated investigators analyze the same problem through different lenses, then a fresh synthesizer reads source first and produces the final plan.

This is intentionally weaker than `adversarial-proposal`: no A/B critique round, no C reconciliation round, and shorter proposals. When in doubt, escalate to `adversarial-proposal`.

## Codex Rules

- Treat an explicit `adversarial-lite` request as permission to use Codex subagents for isolated analysis.
- Do not use this skill for generic planning requests unless the user explicitly asks for lite/quick/cheap adversarial planning or the task is clearly low-risk and bounded.
- Do not simulate independent proposals in one shared context. If isolated subagents or isolated external sessions are unavailable, stop and say the skill cannot be followed faithfully.
- Prefer subagents returning proposal markdown to the lead; the lead writes artifacts locally. This avoids relying on forked-workspace file merges.
- Do not ask subagents to modify production code. They are read-only investigators for this skill.

## Native Model Policy

Prefer this mapping when model overrides are available:

| Pass | Preferred model | Rationale |
|------|-----------------|-----------|
| Proposer A | `gpt-5.4-mini` | Lower-cost structured investigation |
| Proposer B | `gpt-5.4-mini` | Same as A |
| Synthesizer C | `gpt-5.4` or the current high-capacity model | Source-first judgment and final decisions |

If model overrides are unavailable, omit them and continue with the current model. Tell the user that the adversarial structure still applies but the cost split may not.

## Scope Gate

Escalate to `adversarial-proposal` if any of these are true:

- More than 7 source files need to be read, more than 5 files are likely to change, or source volume is roughly over 2000 lines.
- The task touches auth, billing/payment, tenant isolation, secrets, security boundaries, data deletion, database migrations, concurrency/locking, or destructive operations.
- The change spans multiple services, repositories, or independently deployed systems.
- Requirements are ambiguous or cannot be stated in 2-3 concrete sentences.
- The problem is hybrid with unclear primary type, such as bug fix plus refactor or feature plus architecture decision.
- The user asks for high-confidence analysis or an implementation strategy with broad blast radius.

Proceed only when the task is bounded, low-risk, single-service, and maps cleanly to one problem type.

## Output

Write artifacts under:

```text
{project}/.dev/proposals/{slug}/
├── problem-statement.md
├── execution-method.md
├── proposal-A.md
├── proposal-B.md
└── final-plan.md
```

If the output directory already exists, archive it to `{slug}-{YYYYMMDD-HHMMSS}/` before writing a new run.

## Workflow

### Step 0: Setup

1. Clarify or infer a concise problem statement.
2. Discover relevant source files with `rg` / `rg --files`; read enough code locally to set the source list and run the scope gate.
3. Classify the task:

| Type | Strategy A | Strategy B |
|------|------------|------------|
| `bug_fix` | Trace forward from the entry point | Trace backward from the symptom |
| `feature` | Minimal viable change | Robust/extensible design |
| `architecture` | Optimize for simplicity | Optimize for scalability |
| `refactoring` | Incremental migration | Clean-break rewrite |

4. Create `.dev/proposals/{slug}/`.
5. Save `problem-statement.md` with the verbatim user request, source files, problem type, strategy pair, timestamp, git branch, and git HEAD.
6. Save `execution-method.md` describing:
   - Which subagents or isolated sessions will produce A, B, and C.
   - How A/B isolation is preserved.
   - Whether model overrides are available.
7. Read the needed references:
   - `references/proposer-lite.md`
   - `references/synthesizer-lite.md`
   - `references/proposal-format-lite.md`
   - `references/workflow.md` when you need prompt templates.

### Step 1: Two Isolated Proposals

Spawn A and B in parallel when possible. Use separate subagents with `fork_context: false` so each receives only the prompt you provide.

Prompt each proposer with:

- Problem statement.
- Explicit source file list.
- Its assigned strategy.
- Full `references/proposer-lite.md`.
- Full `references/proposal-format-lite.md`.
- Isolation rule: do not read the other proposal, final plan, or proposal-directory artifacts.
- Output instruction: return the complete proposal markdown in the final response; do not edit code.

After both finish, the lead writes their returned markdown to `proposal-A.md` and `proposal-B.md`.

### Step 2: Quality Gate

Each proposal must pass:

- All required proposal sections exist.
- At least 3 concrete code references exist.
- Evidence table has at least 3 rows.
- Core finding has a confidence annotation, plus at least 2 other annotated claims.
- All listed source files are mentioned or explicitly ruled irrelevant.
- Alternatives Considered includes at least one rejected hypothesis and one rejected approach.
- Risks and tests cite specific files, functions, endpoints, or commands.

If one proposal fails, request one revision from the same isolated perspective. If it still fails, continue only if the remaining material is enough for C, and mark the quality gap in C's prompt. If both fail, abort and preserve `problem-statement.md`.

### Step 3: Convergence Check

Mark proposals `CONVERGED` only if all are true:

- Same root cause or core finding, including same function/file target and same mechanism.
- Same general solution approach, including same files and same intervention point.
- Shared evidence overlap: both cite at least one common code path supporting the same conclusion.
- Neither proposal relied on the strategy escape hatch to abandon its lens.

Otherwise mark `DIVERGED`.

C always runs. Convergence changes C's mode; it never skips the source-first synthesis pass.

### Step 4: Source-First Synthesis

Spawn a fresh C subagent whenever possible. C must not share A or B's context.

Prompt C with:

- Source file list and instruction to read source before reading proposals.
- Problem statement.
- Paths to `proposal-A.md` and `proposal-B.md`.
- Full `references/synthesizer-lite.md`.
- Mode: `CONVERGED` or `DIVERGED`.
- Output instruction: return complete `final-plan.md` markdown.

After C finishes, write its returned markdown to `final-plan.md`.

If C cannot be run in a fresh isolated context, stop and recommend `adversarial-proposal` unless the user explicitly accepts a reduced-confidence lead synthesis.

### Step 5: Final Gate

Before presenting the result, check `final-plan.md` contains:

- Header metadata with problem type, strategy pair, convergence state, and overall confidence.
- Source file inventory.
- Numbered implementation plan items with file/function targets.
- Specific test files or commands.
- Risk assessment tied to specific code or endpoints.
- Open assumptions and low-confidence items.

For anything beyond the smallest fixes, recommend `proposal-reviewer` or `adversarial-implementation` before coding.

## Error Handling

| Failure | Recovery |
|---------|----------|
| Scope gate escalates | Stop lite; preserve `problem-statement.md`; recommend `adversarial-proposal`. |
| No isolated subagent/session support | Stop; do not simulate independence. |
| One proposer fails | Retry once; if still failing, C reviews single proposal and notes reduced confidence. |
| Both proposers fail | Abort and preserve setup artifacts. |
| Quality gate fails after retry | Continue only if C has enough evidence; flag the gap in C prompt and final plan. |
| Isolation contamination | Discard contaminated proposal; retry once from a fresh subagent. |
| C fails | Retry once; if it fails again, present raw proposals and recommend full workflow. |
| C says both proposals are wrong | Accept as valid; final plan must explain why, mark low confidence, and recommend full workflow. |
| Source changes during run | Record git HEAD mismatch and ask C to note it in confidence assessment. |

## References

- `references/proposer-lite.md`: role prompt for A/B proposers.
- `references/synthesizer-lite.md`: role prompt for C.
- `references/proposal-format-lite.md`: required A/B proposal shape.
- `references/workflow.md`: Codex prompt templates and orchestration details.
