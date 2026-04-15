---
name: adversarial-lite
description: >
  Lightweight adversarial proposal for low-risk, bounded tasks.
  2-round version of adversarial-proposal: Sonnet investigators + Opus synthesizer.
  No feedback loop. Use for small features, clear bugs, focused refactoring.
  For high-risk, ambiguous, or multi-service problems, use adversarial-proposal instead.
  Trigger on: "lite proposal", "quick proposal", "adversarial lite", or when the lead
  confirms a problem is low-risk and bounded. Do NOT trigger on generic "investigate"
  or "propose" requests — those should use the full adversarial-proposal.
---

# Adversarial Lite

Fast first-pass adversarial planning for low-risk, bounded tasks. Two Sonnet investigators examine the problem independently using different strategies, then one Opus synthesizer produces the final plan in a single pass.

**This is NOT the full adversarial-proposal.** It trades the feedback loop (Rounds 3-4) for speed and cost. Use `adversarial-proposal` when you need high-confidence analysis with multiple rounds of critique.

## When to Use

- Bug fixes with clear symptoms and bounded scope
- Small features (changes likely touch 1-5 files)
- Focused refactoring within a single module
- Quick architecture decisions for isolated components
- Problem can be clearly stated in 2-3 sentences

## When NOT to Use

Use `adversarial-proposal` instead for:

- **High-risk domains**: auth, billing/payment, data deletion, database migrations, security boundaries, tenant isolation, secrets handling, concurrency/locking
- **Multi-service changes** or cross-service coordination
- **Ambiguous requirements** that need exploration before planning
- **Hybrid problems** (bug + refactor, feature + architecture decision) with unclear primary classification
- Changes likely to touch **more than 5 files** or read **more than 7 source files**
- When the user explicitly requests **high-confidence analysis**

When in doubt, use the full version. The cost of a wrong lite plan exceeds the savings.

## Architecture

```
Round 0 (Lead):   Setup + complexity gate
Round 1 (Sonnet): A and B investigate in parallel
Round 2 (Opus):   C synthesizes → final-plan.md
```

Three agents, two rounds, one final deliverable. C **always runs** — convergence changes C's mode (verify vs synthesize), never skips it.

## Output

```
{project}/.dev/proposals/{slug}/
├── problem-statement.md   (Round 0)
├── proposal-A.md          (Round 1, Sonnet)
├── proposal-B.md          (Round 1, Sonnet)
└── final-plan.md          (Round 2, Opus)
```

---

## Round 0: Setup

### Step 1: Detect Problem Type

Classify the problem from the user's description:

| Type | Signal |
|------|--------|
| `bug_fix` | Something is broken, wrong behavior, error, regression |
| `feature` | New capability, missing functionality, enhancement |
| `architecture` | Structural change, performance, scaling concern |
| `refactoring` | Code quality, technical debt, reorganization |

### Step 2: Select Strategy Pair

| Problem Type | Strategy A | Strategy B |
|-------------|-----------|-----------|
| Bug fix | Trace FORWARD from entry point through the code path | Trace BACKWARD from the symptom to find where things went wrong |
| Feature | MINIMAL viable approach — smallest change that delivers | ROBUST/extensible approach — design for future needs and edge cases |
| Architecture | Optimize for SIMPLICITY — fewest moving parts | Optimize for SCALABILITY — handle growth, performance, flexibility |
| Refactoring | INCREMENTAL migration — stages, old and new coexist | CLEAN-BREAK rewrite — replace coherently in one change |

### Step 3: Complexity Gate

Run this multi-factor check. **Any single ESCALATE factor triggers recommendation to use the full version.**

**ESCALATE if ANY of:**
- Source files to read > 7 OR estimated total source lines > 2000
- Problem touches high-risk domains: auth, billing/payment, data deletion, database migrations, security boundaries, tenant isolation, secrets handling, concurrency/locking
- Problem spans multiple services or requires cross-service coordination
- Requirements are ambiguous — cannot state the problem in 2-3 clear sentences
- Problem is hybrid (e.g., "bug fix + refactor") with unclear primary classification
- User explicitly requests high-confidence analysis

**PROCEED if ALL of:**
- Problem is bounded and can be clearly stated
- Source files to read <= 7 AND changes likely touch <= 5 files
- No high-risk domain involvement
- Single service, single concern
- Problem type maps cleanly to one category

**On escalation:** Stop the lite workflow. Preserve `problem-statement.md` and source discovery so the user doesn't restart from scratch. Print:

```
This problem exceeds adversarial-lite scope:
- {reason(s) for escalation}

Recommend using adversarial-proposal for full 4-round analysis.
Problem statement preserved at: {output_dir}/problem-statement.md
```

### Step 4: Create Output Directory

```bash
output_dir="{project}/.dev/proposals/{slug}"
```

If directory exists with artifacts from a previous run, archive it:
```bash
mv "{output_dir}" "{output_dir}-$(date +%Y%m%d-%H%M%S)"
```

Create fresh directory:
```bash
mkdir -p "{output_dir}"
```

### Step 5: Save Problem Statement

Record the problem statement and git HEAD hash:

```markdown
# Problem Statement

{user's problem description, verbatim}

## Source Files
{list of source files to investigate}

## Metadata
- **Type:** {problem_type}
- **Strategy pair:** {Strategy A} vs {Strategy B}
- **Git HEAD:** {output of git rev-parse HEAD}
- **Created:** {timestamp}
```

### Step 6: Read Agent Role Files

Read the following files to embed their full content in agent prompts:
- `~/.claude/skills/adversarial-lite/agents/proposer-lite.md`
- `~/.claude/skills/adversarial-lite/agents/synthesizer-lite.md`
- `~/.claude/skills/adversarial-lite/references/proposal-format-lite.md`

---

## Round 1: Independent Investigation

Spawn both proposers **in the same message** for true parallel execution. See `references/workflow.md` for the exact prompt templates.

```
Agent({
  description: "Proposer A: {problem_type} investigation",
  model: "sonnet",
  prompt: "{problem + sources + proposer-lite role + format-lite + Strategy A + isolation rule}",
  run_in_background: true
})

Agent({
  description: "Proposer B: {problem_type} investigation",
  model: "sonnet",
  prompt: "{problem + sources + proposer-lite role + format-lite + Strategy B + isolation rule}",
  run_in_background: true
})
```

**Model:** `model: "sonnet"` — investigation is the high-volume, lower-judgment work. If the runtime rejects the model parameter, agents inherit the parent model. Warn the user that cost savings may not apply.

**Wait** for both agents to complete (background notification). **Timeout:** 5 minutes per proposer.

### Quality Gate

After both proposals arrive, read each and run checks.

**Structural checks (pass/fail):**
- All 7 required sections present (Problem Statement, Investigation Findings, Core Analysis, Solution Plan, Alternatives Considered, Risk Assessment, Testing Strategy)
- At least 3 specific code references (file:function or file:line)
- Confidence annotations on core finding and at least 2 other claims
- Evidence table present with at least 3 rows
- Proposal between 100-350 lines

**Semantic checks (lead judgment):**
- Evidence table: claims backed by specific file:line references, not vague assertions?
- Source coverage: are all listed source files referenced in the investigation?
- Alternatives Considered: at least 1 substantive rejected hypothesis with evidence-based reasoning?
- Risk specificity: risks cite specific affected code/endpoints?

**On failure:** Re-run the failed proposer with feedback identifying what's missing. Allow **one retry**. If retry also fails, proceed to Round 2 with available material and flag the quality gap to C.

### Convergence Check

After quality gate passes, evaluate convergence. **All three conditions must be met:**

1. **Same root cause / core finding:** Both proposals cite the same function(s) or file location(s) as the root cause AND explain the same mechanism
2. **Same general approach:** Both propose changes to the same files with the same strategy (not just "add validation" — must agree on where and how)
3. **Evidence overlap:** Both proposals reference at least one common code path or function with specific file:line citations supporting the same conclusion

**NOT convergence (false positive guards):**
- Same root cause label but different mechanisms ("both say validation, but A validates at request level, B validates at DB level")
- Same approach but different confidence levels on core finding (A says `[HIGH]`, B says `[LOW]`)
- Both proposers invoked the strategy escape hatch — convergence from abandoning strategies is not meaningful
- Same diagnosis but different implementation targets (different files or functions)

Record the result: CONVERGED or DIVERGED.

---

## Round 2: Synthesis

**C always runs.** Convergence changes C's mode, not whether C is spawned.

See `references/workflow.md` for the exact prompt templates for each mode.

**If CONVERGED:**
```
Agent({
  description: "Verify and consolidate converged proposals",
  model: "opus",
  prompt: "{source files to read FIRST + problem + proposals + synthesizer-lite role, Mode: CONVERGED}"
})
```

**If DIVERGED:**
```
Agent({
  description: "Synthesize divergent proposals into final plan",
  model: "opus",
  prompt: "{source files to read FIRST + problem + proposals + synthesizer-lite role, Mode: DIVERGED}"
})
```

**Model:** `model: "opus"` — synthesis is the high-judgment work that justifies the cost.

**Timeout:** 8 minutes.

---

## Completion

After `final-plan.md` is written:

1. **Clean up:** Remove any `.tmp` files in the output directory
2. **Present:** Read and present `final-plan.md` to the user
3. **Recommend next step:** For anything beyond the smallest fixes, suggest:
   - `adversarial-verify` for validation
   - `adversarial-implementation` for producing exact code diffs
   - Or direct implementation if the plan is straightforward

---

## Error Handling

| Failure | Recovery |
|---------|----------|
| One proposer fails | Retry once. If still fails, C reviews single proposal with blind-spot check. Note reduced confidence. |
| Both proposers fail | Abort. Report to user. Preserve problem-statement.md. |
| Proposal fails quality gate after retry | Proceed to Round 2 with available proposals. Flag gap to C. |
| Isolation violation (A references B) | Discard contaminated proposal. Re-run once. If repeats, single-proposal path. |
| C fails | Re-spawn once. If second failure, present raw proposals. Recommend full version. |
| C produces partial file | .tmp exists but .md does not. Remove .tmp, re-spawn C. |
| C concludes both proposals wrong | Valid outcome. C writes final-plan.md with own approach, LOW confidence, recommends full version. |
| Complexity gate escalates | Stop lite. Preserve problem-statement.md. Print escalation reason. |
| Output directory collision | Archive existing as {slug}-{timestamp}/ before starting. |
| Model parameter rejected | Fall back to default model. Warn user about cost. Proceed — structural benefits still apply. |
| Timeout (5 min proposer, 8 min C) | Treat as failure for that round. Apply recovery. |

---

## Model Configuration

| Agent | Model | Rationale |
|-------|-------|-----------|
| Proposer A | `model: "sonnet"` | Investigation is structured, directive — Sonnet follows the format well at lower cost |
| Proposer B | `model: "sonnet"` | Same as A |
| Synthesizer C | `model: "opus"` | Synthesis requires judgment, bias awareness, resolving disagreements — worth the Opus cost |

If the `model` parameter is not available in the runtime, all agents inherit the parent model. The skill still provides value through independent investigation and anti-anchoring. Print a warning about cost.
