# Proposal Format

Every proposal (A, B, and C) must follow this structure. This ensures proposals are comparable and reviewable.

## Required Sections

### 1. Problem Statement

Restate the problem in your own words. This validates understanding and catches misinterpretation early.

Adapt the bullets to the problem type:

| Problem Type | Required Bullets |
|-------------|-----------------|
| **Bug fix** | Observed vs expected behavior, severity, reproduction steps |
| **Feature** | What capability is needed, who needs it, why it matters |
| **Architecture** | What constraint or goal drives this, current limitations |
| **Refactoring** | Current state vs target state, what's wrong with the status quo |

If your problem doesn't fit neatly, combine bullets from the most relevant types.

### 2. Investigation Findings

Walk through your investigation step by step:

- What files/code did you examine?
- What did you find?
- What is the data flow relevant to this issue?
- Include specific code references (file, function, line range) where applicable

> **Variant guidance:** For bugs, trace the data flow from input to broken output. For features, map the integration points and existing patterns. For architecture, survey the current constraints and their effects. For refactoring, document the before-state with concrete examples.

### 3. Core Analysis

Clearly identify the core of the problem:

- **Core Finding**: One clear sentence stating what you've identified
- **Evidence**: What specific code/behavior supports this finding
- **Mechanism**: How this finding connects to the observed problem or need

> **Variant guidance:** For bugs, this is the root cause and its causal chain to the symptom. For features, this is the gap analysis — what's missing and where it fits. For architecture, this is the constraint or bottleneck driving the change. For refactoring, this is the structural problem and why it gets worse over time.

### 4. Solution Plan

Step-by-step implementation plan:

- Each step should be a discrete, reviewable change
- Include the specific files to modify
- Describe what changes in each file
- Note the order of operations if it matters

### 5. Risk Assessment

- What could go wrong with this approach?
- What other functionality might be affected?
- Are there edge cases to watch for?

### 6. Testing Strategy

- How do you verify the solution works?
- What regression tests are needed?
- What manual verification steps are required?

## Format Guidelines

- Use markdown headers and lists
- Include code snippets where they clarify the point
- Reference specific files with paths relative to project root
- Keep it actionable — every section should inform what to do next
- Target 300-600 lines for a thorough proposal (shorter for simple bugs, longer for complex features)

## Confidence Annotations

Annotate key claims throughout your proposal with confidence levels:

- **`[HIGH]`** — Verified against code, strong evidence, directly observed
- **`[MEDIUM]`** — Reasonable inference from evidence, some assumptions
- **`[LOW]`** — Hypothesis or educated guess, needs verification

Apply these to: root cause / core finding statements, risk predictions, claims about system behavior you haven't directly traced. You don't need to annotate every sentence — focus on claims where confidence matters for decision-making.
