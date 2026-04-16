---
name: proposal-reviewer
description: >
  Independent review skill for critiquing a `final-plan.md` produced by the adversarial proposal
  framework. Performs a fresh pass over the problem statement, relevant code, and final plan to
  find incorrect root-cause assumptions, missing steps, hidden regressions, rollout gaps, and weak
  tests before implementation begins. Use this skill after `adversarial-proposal`, when the user
  wants an independent plan review, or when pointed at a proposals directory or `final-plan.md`.
---

# Proposal Reviewer

## Overview

This skill is the fresh review pass that happens after the adversarial proposal workflow. Two independent investigators (A and B) produced proposals, a synthesizer (C) combined them, A and B critiqued C, and C reconciled everything into a `final-plan.md`. This skill treats that result as untrusted until it has been checked against the actual codebase.

Your job is to catch what that process missed. You are not anchored by any of the prior proposals. Read the problem statement, inspect the code directly, and evaluate the final plan on its own merits.

## Input

This skill takes either a proposals directory or a direct path to `final-plan.md`:

```text
/proposal-reviewer .dev/proposals/{slug}
/proposal-reviewer .dev/proposals/{slug}/final-plan.md
```

Arguments passed: `$ARGUMENTS`

## Path Resolution

Before reviewing anything:

1. Resolve the target proposals directory before reviewing anything.
2. If `$ARGUMENTS` is non-empty:
   - If it points to `final-plan.md`, use that file's parent directory.
   - Otherwise treat it as the proposals directory path.
3. If `$ARGUMENTS` is empty:
   - If the current working directory contains `final-plan.md`, use the current directory.
   - Otherwise search downward for `**/final-plan.md`.
   - If exactly one match exists, use that parent directory.
   - If multiple matches exist, stop and ask the user to rerun the command with the specific proposals directory or `final-plan.md` path.
   - If no match exists, stop and tell the user no proposal directory could be resolved.
4. Confirm the resolved directory up front before writing the review.

## When to Use

- After the adversarial proposal framework produces a `final-plan.md`
- When the user wants an independent check before implementing
- When pointed at a `.dev/proposals/{issue}/` directory

## What to Read

Read the files in this order:

1. `problem-statement.md` — understand the original problem first
2. The relevant source files mentioned in the problem statement — understand the actual code
3. `final-plan.md` — the plan you're reviewing
4. `proposal-A.md`, `proposal-B.md`, `proposal-C.md` — the deliberation trail (to understand what was considered and rejected)

Reading the source code is critical. You cannot review a technical plan without understanding the code it proposes to change.

## How to Review

### 1. Validate the Root Cause

The most important thing to get right. Ask yourself:

- Does the root cause identified in the final plan actually explain the observed symptom?
- Is there evidence in the code that confirms this root cause?
- Could there be a different root cause that explains the same symptom?
- Did all three proposals agree on the root cause? If not, was the disagreement resolved convincingly?
- Trace the data flow yourself — don't trust the plan's description of it. Verify it in the actual code.

### 2. Audit the Implementation Steps

For each step in the implementation plan:

- **Correctness**: Will this change actually do what the plan says it does?
- **Completeness**: Are there files or functions that need to change but aren't listed?
- **Ordering**: Do the steps need to happen in a specific order? Is that order correct?
- **Side effects**: What else does this change affect that isn't mentioned?
- **Rollback**: If this step fails, what happens to the steps already completed?

### 3. Find What's Missing

Things the adversarial process commonly misses:

- **Edge cases**: What happens with empty inputs, null values, missing data, concurrent access?
- **Error handling**: What happens when the fix encounters unexpected state?
- **Configuration**: Are there environment-specific behaviors (dev vs prod, different configs)?
- **Data migration**: Does existing data need to change, not just future data?
- **Dependencies**: Are there upstream or downstream systems affected?
- **Race conditions**: Can the timing of operations cause the fix to fail intermittently?
- **Backwards compatibility**: Does this break any existing behavior that callers depend on?
- **UI discoverability**: For wizard flows, multi-step forms, or repeated navigation patterns, verify that the primary action stays in the same expected navigation container on every step and is not buried below the fold inside page content.

### 4. Evaluate the Testing Strategy

- Does the testing strategy actually verify the fix works?
- Are the test cases specific enough to catch a regression?
- Are there test cases for the edge cases you identified?
- Is manual verification described clearly enough that someone could follow it?
- For wizard flows, do E2E tests assert the primary action inside the expected container rather than anywhere on the page?
- If a test uses a page-wide selector such as `page.getByRole("button", { name: "Save" })`, would it still pass if the button existed but was 2000px below the fold? If yes, flag a discoverability gap.
- Audit conditional navigation renders. Patterns like `{step < TOTAL && <Button>}` often omit the last-step submit/save action from the nav bar.
- Prefer a scoped pattern such as `const wizardNav = page.getByTestId("wizard-nav"); await expect(wizardNav.getByRole("button", { name: "Save" })).toBeVisible();`. If the repo contains a reference spec that demonstrates this pattern, treat it as the standard.

### 5. Check for Anchoring Bias

The adversarial process can still produce anchored results if A and B both made the same wrong assumption. Look for:

- Assumptions that all three proposals shared without questioning
- Alternative explanations that none of the proposals considered
- Parts of the codebase that none of the proposals examined

## Output Format

Save your review as `review-codex.md` in the same proposals directory.

Structure:

```markdown
# Independent Review of Final Plan

## Review Summary
[2-3 sentence verdict: Is this plan ready to implement, needs changes, or has fundamental issues?]

## Confidence Level
[High / Medium / Low — how confident are you in the final plan's correctness?]

## Root Cause Validation
[Is the root cause correct? Did you verify it in the code? Any alternative explanations?]

## Implementation Audit

### Step-by-Step Review
[For each implementation step: correct/incorrect/incomplete, with specifics]

### Missing Steps
[Steps that should be added]

### Ordering Issues
[Any steps that are in the wrong order]

## What Was Missed

### Edge Cases
[Edge cases not covered by the plan]

### Risk Factors
[Risks not identified in the plan's risk section]

### Assumptions to Verify
[Things the plan assumes that should be explicitly confirmed]

## Testing Gaps
[What the testing strategy doesn't cover]

## Anchoring Check
[Any shared assumptions across all proposals that should be questioned]

## Recommendations
[Specific, actionable changes to the final plan before implementing]
```

## Guidelines

- Be specific. "The plan has gaps" is useless. "Step 3 modifies `generate_prompt()` but doesn't account for shots where `character.dialogue` is an empty string vs null" is useful.
- Reference actual code — file paths, function names, line numbers when possible.
- Don't repeat what the plan already says. Focus on what it doesn't say.
- If the plan is solid, say so. Don't manufacture criticism for the sake of it.
- Prioritize your findings — lead with what would cause the fix to fail, not style preferences.
- If you find a fundamental issue with the root cause, flag it immediately and clearly. Everything else is secondary if the root cause is wrong.
- For UI reviews, distinguish "element exists in the DOM" from "primary action is discoverable to a real user without scrolling or hunting." Treat that as a correctness issue, not polish.
