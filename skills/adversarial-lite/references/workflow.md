# Workflow Reference

This document defines the exact tool call sequences for the adversarial-lite skill. Two execution paths are documented: the **primary workflow** (Agent tool) and the **fallback workflow** (subagent with file-based coordination).

---

## Primary Workflow: Agent Tool

This is the default execution path. Uses the `Agent` tool with `model` parameter for mixed-model routing and `run_in_background` for parallel execution.

### Round 0: Setup

The lead does this directly — no agents spawned.

```
1. Detect problem type from user's description
2. Select strategy pair from the strategy table
3. Run complexity gate (multi-factor check — see SKILL.md)
   - If ESCALATE: stop, preserve problem-statement.md, recommend full version
   - If PROCEED: continue
4. Determine output directory: {project}/.dev/proposals/{slug}/
   - If directory exists with artifacts from a previous run:
     archive as {slug}-{timestamp}/ before proceeding
5. Record git HEAD hash: git rev-parse HEAD
6. Write problem-statement.md to output directory (include HEAD hash)
7. Read all agent role files to embed in prompts:
   - agents/proposer-lite.md
   - agents/synthesizer-lite.md
   - references/proposal-format-lite.md
```

### Round 1: Parallel Investigation

Spawn both proposers **in the same message** for true parallel execution.

```
Agent({
  description: "Proposer A: {problem_type} investigation",
  model: "sonnet",
  prompt: `
    You are Proposer A investigating a {problem_type}.

    ## Problem Statement
    {verbatim problem statement from user}

    ## Source Files to Read
    {explicit list of source file paths}

    ## Your Role
    {embed full content of agents/proposer-lite.md}

    ## Your Strategy
    You are assigned **Strategy A: {strategy_a_description}**
    Apply this lens throughout your investigation.

    ## Proposal Format
    {embed full content of references/proposal-format-lite.md}

    ## Output
    Save your proposal as: {output_dir}/proposal-A.md
    Write to proposal-A.md.tmp first, then rename to proposal-A.md when complete.

    ## ISOLATION RULE
    You are one of two independent investigators. You must NOT read any other
    proposal files in the output directory. Do NOT look at any files named
    proposal-B.md, final-plan.md, or any .tmp files other than your own.
    Only read the source files listed above and your own output.
  `,
  run_in_background: true
})

Agent({
  description: "Proposer B: {problem_type} investigation",
  model: "sonnet",
  prompt: `
    You are Proposer B investigating a {problem_type}.

    ## Problem Statement
    {verbatim problem statement from user}

    ## Source Files to Read
    {explicit list of source file paths}

    ## Your Role
    {embed full content of agents/proposer-lite.md}

    ## Your Strategy
    You are assigned **Strategy B: {strategy_b_description}**
    Apply this lens throughout your investigation.

    ## Proposal Format
    {embed full content of references/proposal-format-lite.md}

    ## Output
    Save your proposal as: {output_dir}/proposal-B.md
    Write to proposal-B.md.tmp first, then rename to proposal-B.md when complete.

    ## ISOLATION RULE
    You are one of two independent investigators. You must NOT read any other
    proposal files in the output directory. Do NOT look at any files named
    proposal-A.md, final-plan.md, or any .tmp files other than your own.
    Only read the source files listed above and your own output.
  `,
  run_in_background: true
})
```

**Wait for both agents to complete** (background notification).

**Timeout:** If either agent has not completed after 5 minutes, treat as failure and apply error recovery.

### Quality Gate

After both proposals arrive, the lead reads each and checks:

**Structural checks (pass/fail):**
- All 7 required sections present (Problem Statement, Investigation Findings, Core Analysis, Solution Plan, Alternatives Considered, Risk Assessment, Testing Strategy)
- At least 3 specific code references (file:function or file:line)
- Confidence annotations on core finding and at least 2 other claims
- Evidence table present with at least 3 rows
- Proposal between 100-350 lines (some flex on the 150-300 target)

**Semantic checks (lead judgment):**
- Evidence table: claims backed by specific file:line references, not vague assertions?
- Source coverage: are all listed source files referenced in the investigation?
- Alternatives Considered: at least 1 substantive rejected hypothesis with evidence-based reasoning?
- Risk specificity: risks cite specific affected code/endpoints, not generic statements?

**On failure:** Re-run the failed proposer with specific feedback identifying what's missing:
```
Agent({
  description: "Proposer {A|B}: revision",
  model: "sonnet",
  prompt: `
    Your previous proposal failed quality review. Issues:
    {list specific failures}

    Re-read the source files and revise your proposal.
    {original prompt content}
  `,
  run_in_background: true
})
```

Allow **one retry**. If retry also fails, proceed to Round 2 with available material and flag the quality gap to C.

### Convergence Check

After quality gate passes, the lead evaluates convergence. **All three conditions must be met:**

1. **Same root cause / core finding:** Both proposals cite the same function(s) or file location(s) as the root cause AND explain the same mechanism
2. **Same general approach:** Both propose changes to the same files with the same strategy (not just "add validation" — must agree on where and how)
3. **Evidence overlap:** Both proposals reference at least one common code path or function with specific file:line citations supporting the same conclusion

**NOT convergence (false positive guards):**
- Same root cause label but different mechanisms
- Same approach but significantly different confidence levels (A says `[HIGH]`, B says `[LOW]`)
- Both proposers invoked the strategy escape hatch and investigated "naturally" — convergence from abandoning strategies is not meaningful
- Same diagnosis but different implementation targets (different files or functions)

### Round 2: Synthesis

**Always runs.** Convergence changes the mode, not whether C is spawned.

**If CONVERGED:**
```
Agent({
  description: "Verify and consolidate converged proposals",
  model: "opus",
  prompt: `
    You are the Synthesizer. Mode: CONVERGED.

    Both investigators independently reached the same conclusion. Your job is
    to VERIFY their shared finding against the source code and check for blind
    spots they may share.

    ## Source Files to Read FIRST
    {explicit list of source file paths}

    **READ THESE FILES BEFORE READING THE PROPOSALS.**
    Form your own understanding of the problem space first. Take notes.
    This is non-negotiable — it prevents anchoring bias and catches shared
    blind spots between the investigators.

    ## Problem Statement
    {verbatim problem statement}

    ## Proposals (read AFTER examining source files)
    Read: {output_dir}/proposal-A.md
    Read: {output_dir}/proposal-B.md

    ## Your Role
    {embed full content of agents/synthesizer-lite.md}

    Use Mode: CONVERGED — verify and consolidate.

    ## Output
    Save as: {output_dir}/final-plan.md
    Write to final-plan.md.tmp first, then rename to final-plan.md when complete.
  `
})
```

**If DIVERGED:**
```
Agent({
  description: "Synthesize divergent proposals into final plan",
  model: "opus",
  prompt: `
    You are the Synthesizer. Mode: DIVERGED.

    Two investigators analyzed this problem using different strategies and
    reached different conclusions. Your job is to evaluate both, synthesize
    the strongest elements, and produce the definitive implementation plan.

    ## Source Files to Read FIRST
    {explicit list of source file paths}

    **READ THESE FILES BEFORE READING THE PROPOSALS.**
    Form your own understanding of the problem space first. Take notes.
    This is non-negotiable — it prevents anchoring bias.

    ## Problem Statement
    {verbatim problem statement}

    ## Proposals (read AFTER examining source files)
    Read: {output_dir}/proposal-A.md
    Read: {output_dir}/proposal-B.md

    ## Your Role
    {embed full content of agents/synthesizer-lite.md}

    Use Mode: DIVERGED — full synthesis.

    ## Output
    Save as: {output_dir}/final-plan.md
    Write to final-plan.md.tmp first, then rename to final-plan.md when complete.
  `
})
```

**Timeout:** If C has not completed after 8 minutes, treat as failure.

### Cleanup

After `final-plan.md` is written (or on terminal failure):

1. Remove any `.tmp` files in the output directory
2. Read and present `final-plan.md` to the user
3. For anything beyond the smallest fixes, recommend `adversarial-verify` or `adversarial-implementation` as a follow-up

---

## Fallback Workflow: Subagent / File-Based

Use this if the `Agent` tool's `model` parameter is not available or if background agents are not supported. Same round structure, different completion detection.

### Differences from Primary

| Aspect | Primary (Agent tool) | Fallback (subagent) |
|--------|---------------------|---------------------|
| Completion detection | Background notification | File-existence polling |
| Model routing | `model: "sonnet"` / `model: "opus"` | Inherits parent model (warn user) |
| Parallel execution | `run_in_background: true` | `run_in_background: true` + polling |
| Atomic writes | Same (.tmp → rename) | Same (.tmp → rename) — critical for polling |

### File-Based Completion Polling

```bash
# Poll for Round 1 completion
# Only check for .md files (not .tmp) — atomic rename means .md exists only when complete
until [ -f "{output_dir}/proposal-A.md" ] && [ -f "{output_dir}/proposal-B.md" ]; do
  sleep 10
done
```

```bash
# Poll for Round 2 completion
until [ -f "{output_dir}/final-plan.md" ]; do
  sleep 10
done
```

**Important:** Polling checks for the renamed `.md` file, never the `.tmp` file. This is why atomic writes are critical — a `.md` file only exists when the proposal is fully written.

### Fallback Model Warning

If the `model` parameter is not accepted by the runtime, all agents inherit the parent model. The skill still provides value through its structural benefits (independent investigation, anti-anchoring, strategy pairs), but cost savings from model routing will not apply.

Print a warning to the user:
```
Note: model routing unavailable — all agents using default model.
Cost savings from Sonnet/Opus split will not apply.
The adversarial structure still provides independent-investigation value.
```

---

## Error Recovery

| Round | Failure | Recovery |
|-------|---------|----------|
| Round 1 | One proposer fails | Retry once. If still fails, proceed to Round 2 — C reviews single proposal with instruction to check for blind spots. Note reduced confidence in final-plan.md. |
| Round 1 | Both proposers fail | Abort workflow. Report to user. Preserve problem-statement.md. |
| Round 1 | One proposal fails quality gate after retry | Proceed to Round 2 with available proposals. Flag quality gap to C in the prompt. |
| Round 1 | Isolation violation (A references B's work) | Discard contaminated proposal. Re-run that proposer once. If violation repeats, proceed with single-proposal path. |
| Round 2 | C fails | Re-spawn once with same prompt. If second attempt fails, present raw proposals to user with recommendation to use full adversarial-proposal. |
| Round 2 | C produces partial file | .tmp file exists but .md does not. Re-spawn C. Remove the .tmp file first. |
| Round 2 | C concludes both proposals are wrong | C produces final-plan.md with its own approach, flags LOW confidence, recommends full adversarial-proposal. This is valid — present it to user. |
| Any | Timeout | After max wait (5 min proposers, 8 min synthesizer), treat as failure. Apply recovery for that round. |
| Any | Output directory collision | Archive existing directory as {slug}-{timestamp}/ before starting. |
| Any | Source files changed during run | C's Phase 0 examination naturally picks up current state. If git HEAD at Round 2 differs from problem-statement.md's recorded hash, C notes this in final-plan.md. |
