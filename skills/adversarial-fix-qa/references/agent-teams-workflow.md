# Agent Teams Workflow — adversarial-fix-qa

Canonical tool-call sequence for orchestrating the 4-round QA flow. Read this before orchestrating; SKILL.md is the high-level guide.

## Round 0 — Setup

```python
TeamCreate(
  team_name="fixqa-{short-slug}",
  description="Adversarial fix QA: confirm fix + hunt regressions"
)

# Five tasks; the chain is reconciler-only-after-critiques-only-after-synthesizer-only-after-A-and-B
TaskCreate(subject="Round 1: Fix Confirmation (Agent A)", description="QA Confirmer A — verify fix resolves diagnosed bug")
TaskCreate(subject="Round 1: Regression Hunting (Agent B)", description="Regression Hunter B — verify nothing adjacent or downstream broke")
TaskCreate(subject="Round 2: Synthesize qa-C", description="Synthesizer C — consolidate qa-A and qa-B into qa-C with tentative verdict")
TaskCreate(subject="Round 3: Critique synthesis (A and B in parallel)", description="A and B critique C from their original Round 1 contexts")
TaskCreate(subject="Round 4: Reconcile to fix-issues + fix-manifest", description="Synthesizer C — produce final deliverables")

# Set dependencies via TaskUpdate addBlockedBy as appropriate
```

## Round 1 — Spawn A and B in parallel (single message)

Two `Agent` calls in the same tool-use block:

```python
Agent(
  team_name="fixqa-{short-slug}",
  name="qa-confirmer-a",
  subagent_type="general-purpose",
  prompt=<<<embed>>>
    # Role
    {contents of agents/fix-qa-confirmer.md}

    # Working artifact format (authoritative)
    {contents of references/qa-working-format.md}

    # Source files (read these — not optional)
    diagnosis.md: {contents}
    repro.md (if present): {contents}
    fix-plan.md: {contents}
    fix-coder-report.md: {contents}
    fix-diffs.md: {contents}
    problem-statement.md: {contents}

    # Original pipeline context (read-only, if --original-slug)
    {paths only — agent reads as needed}

    # Environment
    --env: {prod | dev}
    Implication: {dev = re-execute repro; prod = confirm failing-path test exercises}

    # Output
    Write your QA report to: {output_dir}/qa-A.md
    Write evidence to: {output_dir}/evidence/post-fix/A-*

    # Isolation rule
    {verbatim from SKILL.md "Isolation Rule" section}

    # Required actions (per agents/fix-qa-confirmer.md)
    [...]

    # When done
    SendMessage to "team-lead" a one-line summary by severity count.
    Then go idle — do NOT exit. You will be messaged in Round 3 to critique a synthesis.
  <<<end>>>,
)

Agent(
  team_name="fixqa-{short-slug}",
  name="regression-hunter-b",
  subagent_type="general-purpose",
  prompt=<<<embed>>>
    # Role
    {contents of agents/fix-qa-regression.md}

    # Working artifact format (authoritative)
    {contents of references/qa-working-format.md}

    # Source files (read these — not optional)
    {same set as A}

    # Original pipeline context (if provided)
    {paths only}

    # Environment
    --env: {prod | dev}
    Implication: {dev = live test execution; prod = local-test-suite-against-fix only}

    # Output
    Write your QA report to: {output_dir}/qa-B.md
    Write evidence to: {output_dir}/evidence/post-fix/B-*

    # Isolation rule
    {verbatim}

    # Required actions (per agents/fix-qa-regression.md)
    [Mandatory: cite every dependent checked in the Dependents Checked table]
    [...]

    # When done
    SendMessage to "team-lead" a one-line summary by severity count.
    Then go idle — do NOT exit.
  <<<end>>>,
)
```

The lead idles after the parallel spawn until both agents send their summary messages.

## Round 1 — Gates (run in this order)

### Artifact gate (run first)

```python
import os
qa_a = "{output_dir}/qa-A.md"
qa_b = "{output_dir}/qa-B.md"

if not os.path.exists(qa_a) or os.path.getsize(qa_a) == 0:
    SendMessage(to="qa-confirmer-a", message="Your QA report at qa-A.md is missing or empty. Write your full report to that path before signaling done.")
    # wait for resume
if not os.path.exists(qa_b) or os.path.getsize(qa_b) == 0:
    SendMessage(to="regression-hunter-b", message="Your QA report at qa-B.md is missing or empty. Write your full report to that path before signaling done.")
    # wait for resume
```

Do not proceed until both files exist on disk and are non-empty.

### Quality gate

Read both reports. Each must have the required sections from `qa-working-format.md`:

- Strategy Followed
- Actions Performed (with file/test citations)
- Findings (severity-rated)
- Evidence (paths to logs, screenshots, test output under `evidence/`)
- Confidence + Blocking Items (last two lines)

For B specifically: the Dependents Checked table must be present and non-empty (or contain "no dependents identified — rationale: ...").

If a report is structurally incomplete:

```python
SendMessage(to="qa-confirmer-a", message="revise: <specific gap>")  # or to b
```

One retry. If still incomplete, surface to user with the gap list and stop.

### Critical-finding short-circuit

Parse findings from both reports.

```python
critical_a = any(f.severity == "CRITICAL" for f in qa_a.findings)
critical_b = any(f.severity == "CRITICAL" for f in qa_b.findings)

if critical_a and critical_b:
    critical_line = f"CRITICAL_FROM_AGENT: both — A: {a_summary}; B: {b_summary}"
elif critical_a:
    critical_line = f"CRITICAL_FROM_AGENT: A — {a_summary}"
elif critical_b:
    critical_line = f"CRITICAL_FROM_AGENT: B — {b_summary}"
else:
    critical_line = "CRITICAL_FROM_AGENT: none"
```

`critical_line` becomes the leading line of the synthesizer's Round 2 prompt.

### Optional fast path (both clean)

If A.result == CONFIRMED with zero findings AND B has zero findings AND `critical_line == "CRITICAL_FROM_AGENT: none"`, the synthesizer prompt may include:

```
FAST_PATH_ELIGIBLE: true — both agents reported clean. You MAY skip Phase 2 cross-verification and proceed directly to Phase 3 consolidation. Severity assignment, verdict, and Round 4 reconciliation still proceed normally.
```

Otherwise:

```
FAST_PATH_ELIGIBLE: false
```

### (Interactive) checkpoint 1

If `--interactive`, pause for user review of qa-A and qa-B. Wait for `continue`.

## Round 2 — Synthesizer C

```python
Agent(
  team_name="fixqa-{short-slug}",
  name="synthesizer-c",
  subagent_type="general-purpose",
  prompt=<<<embed>>>
    # Leading line (Round 1 short-circuit signal)
    {critical_line}
    {fast_path_line}

    # Role
    {contents of agents/fix-qa-synthesizer.md}

    # Output formats (authoritative)
    Final qa-C.md follows qa-working-format conventions for working artifacts.
    Round 4 will produce fix-issues.md and fix-manifest.yaml; for now, qa-C.md
    is the synthesis with a tentative verdict.

    # Source files
    {paths to fix-coder-report.md, fix-diffs.md, fix-plan.md, diagnosis.md, repro.md, problem-statement.md}

    # Round 1 reports
    qa-A.md: {contents}
    qa-B.md: {contents}

    # Required outputs
    {output_dir}/qa-C.md
    {output_dir}/review-for-A.md  (isolated; A may not read this until Round 3)
    {output_dir}/review-for-B.md  (isolated; B may not read this until Round 3)

    # Phase order
    Phase 0: read source files DIRECTLY (don't trust A/B summaries)
    Phase 1: read qa-A.md and qa-B.md
    Phase 2: cross-verify the most critical findings (skip if FAST_PATH_ELIGIBLE)
    Phase 3: synthesize qa-C.md with tentative verdict and severity assignments
    Phase 4: write isolated review-for-A and review-for-B (no mention of the other agent)

    # When done
    SendMessage to "team-lead" with a one-line tentative verdict.
    Then go idle.
  <<<end>>>
)
```

## Round 2 — Artifact gate (required)

```python
required = ["qa-C.md", "review-for-A.md", "review-for-B.md"]
missing = [f for f in required if not os.path.exists(f"{output_dir}/{f}") or os.path.getsize(...) == 0]

if missing:
    SendMessage(to="synthesizer-c", message=f"Missing or empty: {missing}. Write each per qa-working-format / fix-issues-format conventions before signaling done.")
    # one retry
    # if still missing: surface to user, stop
```

If `--interactive`, checkpoint 2: pause for user review of qa-C.md.

## Round 3 — Critique (A and B in parallel)

Two `SendMessage` calls in one tool-use block:

```python
SendMessage(
  to="qa-confirmer-a",
  description="Confirmer A: Round 3 feedback on qa-A",
  message=<<<embed>>>
    # Round 3 critique role
    {contents of agents/fix-qa-critic.md}

    # Round 3 isolation rule
    Read ONLY:
    - {output_dir}/review-for-A.md  (the synthesizer's review of YOUR qa-A.md)
    - {output_dir}/qa-A.md  (your own Round 1 report, for reference)

    Do NOT read:
    - qa-B.md, qa-C.md, review-for-B.md, fix-issues.md, reconciliation-notes.md

    # Critique focus
    Did C's synthesis fairly represent your findings?
    Did C downgrade severity inappropriately on any of your findings?
    Is C's verdict consistent with the evidence YOU gathered?

    # Output
    SendMessage your feedback back to "team-lead" with structured points:
    - Point: <statement>
      Severity: matches | downgraded | upgraded
      Justification needed: yes | no
  <<<end>>>
)

SendMessage(
  to="regression-hunter-b",
  description="Hunter B: Round 3 feedback on qa-B",
  message=<<<embed>>>
    {same structure with B-specific paths}
  <<<end>>>
)
```

Wait for both critiques.

## Round 4 — Reconciliation

```python
SendMessage(
  to="synthesizer-c",
  message=<<<embed>>>
    # Reconciler role
    {contents of agents/fix-qa-reconciler.md}

    # Authoritative formats
    fix-issues.md per references/fix-issues-format.md
    fix-manifest.yaml per references/fix-manifest-schema.yaml

    # Inputs
    Critique from A: {a_critique_body}
    Critique from B: {b_critique_body}
    Your prior qa-C.md: {output_dir}/qa-C.md
    fix-review.md (if present): {output_dir}/fix-review.md

    # Required outputs (all three)
    {output_dir}/fix-issues.md
    {output_dir}/fix-manifest.yaml
    {output_dir}/reconciliation-notes.md

    # Hard rule (Hard Rule 8)
    For each ADVISORY in fix-review.md (if present), state in fix-issues.md's
    "Re-emerged Advisories" section whether QA observed it as still active.
    Silent drop is forbidden.

    # Hard rule (verdict-vs-counts consistency)
    The verdict MUST match the severity counts and the source agents.
    See SKILL.md Step 7.5 "Severity-to-verdict mapping" — enforce it.

    # When done
    SendMessage to "team-lead" with the final verdict and BLOCKING_ITEMS count.
  <<<end>>>
)
```

## Round 4 — Output Quality Gate (required, before Step 8)

Per SKILL.md Step 7.5. Verify all three artifacts on disk, structure, last-two-lines, schema, verdict-vs-counts consistency, verdict-match between fix-issues and fix-manifest.

On gate failure:

```python
SendMessage(to="synthesizer-c", message="<specific gap>")
# one retry
# if still failing: write stub artifact pair (Hard Rule 5), surface to user
```

## Round 5 — Cleanup

```python
# Three shutdowns in parallel
SendMessage(to="qa-confirmer-a", message={"type": "shutdown_request", "reason": "QA complete"})
SendMessage(to="regression-hunter-b", message={"type": "shutdown_request", "reason": "QA complete"})
SendMessage(to="synthesizer-c", message={"type": "shutdown_request", "reason": "QA complete"})

TeamDelete(team_name="fixqa-{short-slug}")
```

Present `fix-issues.md`, `fix-manifest.yaml`, `reconciliation-notes.md` to the user.

## Error matrix

| Symptom | Action |
|---|---|
| Round 1 agent doesn't message back within reasonable time | Check team status; if alive, wait. If dead, mark task FAILED, write stub artifact pair, escalate. |
| qa-A or qa-B missing on disk | SendMessage to author. One retry. If still missing, write stub pair, escalate. |
| Synthesizer skips Phase 2 cross-verification when FAST_PATH_ELIGIBLE was false | Reject in quality gate; SendMessage with "FAST_PATH_ELIGIBLE was false; Phase 2 is required." |
| Round 4 verdict missing or unparseable | Treat as BOTH (worst case, defense in depth). SendMessage one retry. If still bad, escalate. |
| Verdict-vs-counts inconsistency in fix-manifest.yaml | SendMessage with the specific inconsistency. One retry. Escalate. |
| TeamCreate fails ("Teams disabled") | Fall back to `references/subagent-fallback.md`. |

## Convergence path note

A and B answer different questions (fix-confirmation vs regression-hunting). They do NOT naturally converge. The closest analog is the fast path (both clean) above; otherwise, both findings always proceed through Round 2-4 normally. Do not skip Round 3 critiques even when both reports are clean — severity decisions and edge cases still benefit from the critique pass.
