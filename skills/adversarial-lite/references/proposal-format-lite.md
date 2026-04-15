# Proposal Format (Lite)

Every proposal (A and B) must follow this structure. This ensures proposals are comparable and the synthesizer can evaluate them effectively.

Target: **150-300 lines**. Be thorough but concise — prefer code references over inline snippets.

## Required Sections

### 1. Problem Statement (10-20 lines)

Restate the problem in your own words. This validates understanding and catches misinterpretation early.

Adapt the bullets to the problem type:

| Problem Type | Required Bullets |
|-------------|-----------------|
| **Bug fix** | Observed vs expected behavior, severity, reproduction steps |
| **Feature** | What capability is needed, who needs it, why it matters |
| **Architecture** | What constraint or goal drives this, current limitations |
| **Refactoring** | Current state vs target state, what's wrong with the status quo |

If your problem doesn't fit neatly, combine bullets from the most relevant types.

### 2. Investigation Findings (40-80 lines)

Walk through your investigation step by step:

- What files/code did you examine?
- What did you find?
- What is the data flow relevant to this issue?
- Include specific code references (file:function:line) for each finding

> **Variant guidance:** For bugs, trace the data flow from input to broken output. For features, map the integration points and existing patterns. For architecture, survey the current constraints and their effects. For refactoring, document the before-state with concrete examples.

### 3. Core Analysis (20-40 lines)

Clearly identify the core of the problem:

- **Core Finding**: One clear sentence stating what you've identified
- **Evidence**: What specific code/behavior supports this finding
- **Mechanism**: How this finding connects to the observed problem or need

> **Variant guidance:** For bugs, this is the root cause and its causal chain to the symptom. For features, this is the gap analysis — what's missing and where it fits. For architecture, this is the constraint or bottleneck driving the change. For refactoring, this is the structural problem and why it gets worse over time.

**Evidence Table** (required):

```markdown
| Claim | Source | Confidence |
|-------|--------|------------|
| {core finding or key claim} | {file:function():line} | [HIGH/MEDIUM/LOW] |
| {supporting claim} | {file:function():line} | [HIGH/MEDIUM/LOW] |
```

Minimum 3 rows. Every key claim in your analysis should have a row.

### 4. Solution Plan (40-80 lines)

Step-by-step implementation plan:

- Each step should be a discrete, reviewable change
- Include the specific files to modify
- Describe what changes in each file and why
- Note the order of operations if it matters
- Include specific function names and line ranges where changes apply

### 5. Alternatives Considered (10-20 lines)

This section is required. It demonstrates investigation depth and gives the synthesizer insight into your reasoning.

- **At least 1 rejected hypothesis**: What other root cause or gap did you consider? Why was it ruled out? Cite specific evidence.
- **At least 1 alternative approach**: What other solution did you consider? Why was your proposed approach better?
- **Falsification**: In one sentence — what evidence would prove your conclusion wrong?

### 6. Risk Assessment (15-30 lines)

- What could go wrong with this approach?
- What other functionality might be affected?
- Are there edge cases to watch for?

**Each risk must cite specific affected code or endpoints.** "Could break existing functionality" is not acceptable — name the file, function, or endpoint at risk.

Minimum 2 specific risks.

### 7. Testing Strategy (15-30 lines)

- How do you verify the solution works?
- What regression tests are needed?
- What manual verification steps are required?
- Name specific test files to create or modify.

## Confidence Annotations

Annotate key claims throughout your proposal with confidence levels:

- **`[HIGH]`** — Verified against code, strong evidence, directly observed
- **`[MEDIUM]`** — Reasonable inference from evidence, some assumptions
- **`[LOW]`** — Hypothesis or educated guess, needs verification

Apply these to: core finding statements, risk predictions, claims about system behavior you haven't directly traced. Focus on claims where confidence matters for decision-making.

**Calibration guidance:** Only mark `[HIGH]` if you can point to the exact line of code. If you're inferring from patterns or documentation, that's `[MEDIUM]`. If you're guessing based on naming conventions or general architecture knowledge, that's `[LOW]`.

## Format Guidelines

- Use markdown headers and lists
- Reference files as `file:function():line` (e.g., `backend/handlers/batch.py:process_items():L45-52`)
- Prefer references over inline code snippets to save space
- Keep it actionable — every section should inform what to do next
