# Investigation Format

Every investigation file (`investigation-A.md`, `investigation-B.md`, and the body of `investigation-C.md`) must follow this structure. This ensures investigations are comparable and the synthesizer + reconciler can audit them against each other.

## Required Sections

### 1. Strategy and Inputs

State your assigned strategy and the inputs you read.

```markdown
## Strategy
{A — Forward-from-trigger | B — Backward-from-failure-point | C — Synthesis | escape-hatch: <reason>}

## Inputs Read
- problem-statement.md
- repro.md (if present)
- Original pipeline context (if --original-slug): final-plan.md, implementation.md, coder-report.md
- Source files examined: <list>
- Evidence consulted: <log paths, telemetry refs, screenshots>
```

### 2. Bug Restatement

Restate the bug in your own words. This validates understanding and catches misinterpretation early.

- **Trigger** (forward strategy): the specific action / input / condition that initiates the failure
- **Failure point** (backward strategy): the exact location and form of the visible failure (error message, response field, log line)
- **Symptoms observed**: the full set of visible effects (not just the most prominent)
- **Severity / blast radius**: who's affected and how badly

### 3. Investigation Findings

Walk through your investigation step by step, in the order you did it. Use the lens of your assigned strategy.

For **Strategy A (Forward-from-trigger)**:
- What happens at the trigger? What state is in play?
- For each transition (function call, await boundary, message send, DB write): what assumption is being made about state, and does it hold?
- Where does an assumption first fail? What did you find when you verified it?
- How does that failure propagate to the visible failure point?

For **Strategy B (Backward-from-failure-point)**:
- At the failure, what was the exact bad value / state / response?
- What produced that value? (Read the call stack, the function that wrote that field, the DB query that returned it, etc.)
- Walk back one transition at a time. At each step: what state was already wrong, and where did it come from?
- Where does the chain stop being wrong? That's where the cause sits.

For **Strategy C (Synthesizer)**:
- Phase 0 findings: what you found before reading A or B
- Phase 1 read of investigations: what each investigator got right and where they fell short
- Phase 2 verification: which hypotheses survived direct re-checking against code/evidence

Include specific code references (file path, function name, line range) and evidence references (log timestamp, response field, state value) throughout. Vague summaries are not investigations.

### 4. Hypotheses

A hypothesis is a candidate cause. You must list **at least three** — your top hypothesis plus at least two alternatives. Even if a leading candidate seems obvious, the alternatives are required (anti-anchoring).

For each hypothesis:

```markdown
#### H{N}: {short name}

**Description**: {what this hypothesis says is causing the bug}

**Evidence for**:
- {file:line or log ref + what it shows}
- {...}

**Evidence against / open questions**:
- {what would rule this out, or what's unresolved}

**Confidence**: [HIGH] | [MEDIUM] | [LOW]
**Verdict**: top candidate | alternative | rejected
**Reason**: {why this verdict}
```

### 5. Symptom-vs-Cause Analysis (REQUIRED)

You must explicitly answer this question:

> Could the visible error be a symptom of a deeper cause? What's upstream of where it manifests?

Possible answers:

- **Yes**: the visible error is a symptom of a deeper cause. Identify what's upstream and cite the evidence.
- **No**: the visible error is itself the cause. Explain why — what makes the visible failure point the actual origin and not a downstream artifact?

This section is mandatory. Skipping it is the failure mode it exists to prevent.

### 6. Committed Top Hypothesis (or Insufficient-Evidence Finding)

State plainly which hypothesis you commit to as the most-likely cause, OR explicitly state "insufficient evidence to commit" and list what additional data would resolve it.

For a committed hypothesis:

```markdown
## Top Hypothesis: H{N} — {name}

The cause is {precise statement}. Evidence chain: {brief recap pointing at the
key file:line + log refs that support it}.

Confidence: [HIGH] | [MEDIUM] | [LOW]
```

For insufficient evidence:

```markdown
## Top Hypothesis: INSUFFICIENT EVIDENCE

Current evidence does not support committing to a single cause. Candidate
hypotheses ranked by plausibility: H{N}, H{N}, H{N}.

What would resolve the ambiguity:
- {specific data, source, what it would distinguish}
- {...}
```

### 7. Confidence + Blocking Items

The final two lines of the file MUST be exactly:

```
CONFIDENCE: HIGH | MEDIUM | LOW
BLOCKING_ITEMS: N
```

Guidance:

- **HIGH** — evidence strongly supports the committed top hypothesis; alternatives are firmly ruled out.
- **MEDIUM** — committed hypothesis is best-supported but alternatives aren't fully ruled out, OR insufficient-evidence finding with clearly identified gaps.
- **LOW** — significant uncertainty; the synthesizer should expect to do meaningful re-investigation.

`BLOCKING_ITEMS` counts items that should block downstream stages — open hypotheses that materially affect fix planning, missing data without which the diagnosis cannot land.

## Format Guidelines

- Use markdown headers and lists.
- Include code excerpts when they clarify (short, focused).
- Reference specific files with paths relative to project root.
- Annotate confidence on key claims throughout (not just at the end).
- Target 400–800 lines for a thorough investigation. Shorter is fine for narrow bugs; longer is acceptable for compound or unclear bugs.

## Confidence Annotations

Annotate key claims throughout your investigation with confidence levels:

- **`[HIGH]`** — directly verified against code or evidence; you read it yourself
- **`[MEDIUM]`** — reasonable inference from evidence, with some assumptions
- **`[LOW]`** — hypothesis or educated guess; needs verification

Apply these to: hypothesis statements, claims about system behavior you didn't directly trace, predictions about what evidence would show. You don't need to annotate every sentence — focus on claims where confidence matters for the diagnosis.

## What This File Is Not

- Not a fix plan. Don't propose code changes; the diagnosis stage doesn't write fixes.
- Not a duplicate of `repro.md`. The reproduction is input; the investigation is the trace and the cause.
- Not a code summary. If you find yourself describing the architecture in general terms, you've drifted — refocus on the bug.
