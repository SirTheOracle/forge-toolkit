---
name: fix-reproducer
description: >
  Optional first stage of the fix-pipeline. Verifies a reported bug is reproducible
  and captures deterministic repro steps, environment state, and evidence. Single
  procedural executor — does not spawn sub-agents and does not diagnose. Branches
  by environment: dev path runs the bug locally; prod path gathers observational
  evidence from the deployed environment. Always writes repro.md, even on
  NOT REPRODUCED. Trigger when the user invokes a fix-pipeline with the reproduce
  step enabled.
---

# Fix Reproducer Skill

## Hard Constraints

1. **Reproduce, do not diagnose.** Capture what happens, not why. Observations
   are allowed; causal claims are out of scope and belong to investigate.
2. **Always write `repro.md`, even on NOT REPRODUCED.** A documented negative
   result is itself input for investigate.
3. **Never perform write operations against production without explicit user
   confirmation during runtime.** If a reproduction step would write production
   data, stop and ask before proceeding.
4. **Environment is a first-class input.** The `env` tag in `repro.md` must
   match what was actually used. Never fall back from prod to dev (or vice
   versa) without flagging it.
5. **May read original pipeline artifacts for context.** Read-only — never edit
   or write to anything under `.dev/proposals/{original-slug}/`.
6. **Initial Observations are strictly observational.** No causal claims, no
   "the bug is in module X." Note what you saw; let investigate decide what
   it means.
7. **Single executor.** This skill does not spawn sub-agents and does not run
   adversarial rounds.

## Required Reads

Before any reproduction action, read these files in order:

1. `.claude/forge-project.yml` (fallback: `~/.claude/forge-project.yml`) — project config
2. `{output_dir}/problem-statement.md` — the bug description as written by the orchestrator
3. `~/.claude/skills/fix-reproducer/references/repro-format.md` — output template

If `--original-slug` is provided, also read for context (read-only):

4. `.dev/proposals/{original-slug}/final-plan.md` — what the feature was supposed to do
5. `.dev/proposals/{original-slug}/implementation.md` — what was actually built
6. `.dev/proposals/{original-slug}/coder-report.md` — what was committed

## Inputs

Required:

- `--output-dir` — path to the fix directory. `problem-statement.md` lives here;
  `repro.md` and `evidence/` will be written here.
- `--env prod | dev` — which environment the bug is reported in.

Optional:

- `--original-slug` — slug of the original build pipeline whose feature has
  the bug. Enables read-only context access to that pipeline's artifacts.
- `--interactive` — pause for clarifying questions when the description is
  ambiguous, instead of proceeding autonomously.

## Modes

| Mode | Flag | Behavior |
|------|------|----------|
| **Default** | — | Autonomous. Reproduce based on the description as given. Document ambiguity in repro.md if encountered. |
| **Interactive** | `--interactive` | When description is ambiguous, pause and ask the user clarifying questions before attempting reproduction. |

## Phase 0: Config + Inputs

1. Read `.claude/forge-project.yml`
2. Read `problem-statement.md` from `--output-dir`
3. Confirm `--env` is set and is one of `prod` | `dev`
4. If `--original-slug` is provided, confirm `.dev/proposals/{original-slug}/`
   exists. If it doesn't, log a note in repro.md but continue.
5. Create `{output_dir}/evidence/` if it doesn't exist
6. If any required input is missing, stop and report:
   ```
   FIX-REPRODUCER ERROR: Missing required input — {field}
   ```

## Phase 1: Description Analysis

Parse `problem-statement.md` into structured form. Extract:

- **Action sequence** — what the user did (or what was happening) when the bug appeared
- **Expected behavior** — what should have happened
- **Observed behavior** — what actually happened
- **Error indicators** — error messages, stack traces, status codes mentioned
- **Environmental hints** — anything in the description that points at conditions (timing, state, data, user role, etc.)

If the description is too vague to extract a usable action sequence:

- **Default mode:** proceed with what you have, note the ambiguity in repro.md's
  Variations Attempted section
- **Interactive mode:** stop and ask the user the specific question(s) you
  need answered before proceeding

Do not invent steps the description doesn't support.

## Phase 2: Reproduction

The reproduction work branches by `--env`. The contract is the same in both paths:

- Try to trigger or observe the bug
- Capture concrete evidence
- Note any side effects of the attempt

The skill is agnostic about which tools are used. Pick the appropriate tool at
runtime based on what the project provides — Playwright for UI flows, curl for
HTTP, direct invocation for CLI tools, log scraping for observational evidence,
etc. The phase below describes the *contract* of each path, not the tooling.

### Phase 2A: Dev Path (`--env dev`)

Local reproduction — the app runs on localhost; you can interact with it directly.

1. Confirm prerequisites are running (or start them per forge-project.yml). If
   you cannot bring services up, classify outcome as NOT REPRODUCED and report
   the infrastructure failure as evidence.
2. Execute the action sequence from Phase 1 against the local app.
3. Capture at each step:
   - What was attempted
   - What was observed (output, response, UI state, error)
   - Evidence: screenshot path, log excerpt, response body, stack trace
4. If the literal steps don't reproduce, you may try **bounded variations** —
   small adjustments that test the same hypothesis (different input value,
   different order, different timing). Each variation is recorded.
5. The dev path is allowed to be interactive in spirit: try, observe, adjust,
   retry. Stop when you have a deterministic repro or have exhausted reasonable
   variations.

### Phase 2B: Prod Path (`--env prod`)

Observational — the app is deployed; you do not run it locally. Evidence comes
from production telemetry, deployed endpoints, and any user-provided artifacts.

1. **Static evidence first.** Before attempting any live reproduction, gather
   what already exists: production logs around the reported timeframe, error
   tracking entries (Sentry/etc. if configured), any artifacts the user
   provided (HAR files, screenshots, IDs).
2. **Live observation second.** If static evidence isn't sufficient to confirm
   the bug, attempt to observe it against the deployed environment. This is
   typically read-only HTTP requests or UI navigation that doesn't write data.
3. **Side-effect guardrail.** If a reproduction step would write production
   data (form submissions, mutations, anything non-idempotent), STOP and
   surface the intent to the user before proceeding. Do not silently perform
   the action.
4. Capture at each step:
   - What was attempted (or what static source was queried)
   - What was observed
   - Evidence: log excerpts, response bodies, screenshots, telemetry references
5. The prod path may produce one of three sub-outcomes worth distinguishing in
   the report:
   - **Reproduced live** — bug observed by direct reproduction against prod
   - **Confirmed via telemetry** — bug not directly reproduced, but production
     evidence (logs, error tracking) confirms it occurred
   - **Statistical evidence only** — telemetry shows N occurrences but no
     deterministic trigger was identified

## Phase 3: Outcome Classification

Classify the reproduction attempt as exactly one of:

- **REPRODUCED** — bug confirmed with concrete, deterministic evidence. Steps
  reliably trigger the failure.
- **PARTIALLY REPRODUCED** — bug-like behavior observed but does not exactly
  match the reported description. Could be a related bug, an environment
  difference, or an incomplete description.
- **NOT REPRODUCED** — could not trigger or observe the bug with what was
  attempted. Document everything tried.

For prod, "Confirmed via telemetry" and "Statistical evidence only" both fall
under REPRODUCED — the bug exists, even if the trigger isn't deterministic.
Note the sub-outcome in the report.

## Phase 4: Write repro.md

Write `{output_dir}/repro.md` following the structure in
`references/repro-format.md`. Required sections:

- **Environment** — `prod` or `dev`, plus relevant version/config info
- **Problem Statement** — the structured form from Phase 1
- **Reproduction Status** — REPRODUCED / PARTIALLY / NOT REPRODUCED, with
  prod sub-outcome if applicable
- **Steps to Reproduce** — numbered, deterministic, with expected vs actual
  per step. Empty/abbreviated if NOT REPRODUCED.
- **Evidence** — paths under `evidence/` for screenshots, logs, captures,
  telemetry references
- **Environment State** — versions, config, data state, anything that
  affected outcome
- **Variations Attempted** — bounded variations tried during Phase 2,
  especially relevant for PARTIALLY or NOT REPRODUCED
- **Initial Observations** — strictly observational notes that may inform
  investigate. No causal claims.
- **Confidence + Blocking Items** — final lines, in this format:
  ```
  CONFIDENCE: HIGH | MEDIUM | LOW
  BLOCKING_ITEMS: N
  ```
  NOT REPRODUCED defaults to BLOCKING_ITEMS > 0 because investigate cannot
  proceed productively without something to investigate.

The report MUST be written even on failure (NOT REPRODUCED, infrastructure
errors, ambiguous description). Partial progress is valuable input for
investigate or for the user to decide next steps.

## Error Recovery

| Error | Action |
|-------|--------|
| `forge-project.yml` not found | Stop, report missing config |
| `problem-statement.md` not found in --output-dir | Stop, report missing input |
| `--env` not set or invalid | Stop, report invalid input |
| Description too vague (default mode) | Document ambiguity in repro.md, classify as NOT REPRODUCED if it blocks reproduction |
| Description too vague (interactive mode) | Pause, ask user clarifying questions |
| Local services won't start (dev path) | Classify NOT REPRODUCED, capture infra failure as evidence |
| Prod write operation needed | Stop, surface intent to user, do not proceed without explicit confirmation |
| Reproduction succeeds but description doesn't match | Classify PARTIALLY REPRODUCED, document the discrepancy |

## Output Directory

```
{output_dir}/
├── problem-statement.md     ← Input (written by orchestrator)
├── repro.md                 ← Output (this skill writes this)
└── evidence/
    ├── screenshots/
    ├── logs/
    ├── captures/            ← network/HAR captures, response bodies
    └── telemetry/           ← references to production telemetry (prod only)
```

## What This Skill Does NOT Do

- Does not generate the fix slug or create the output directory (orchestrator's job)
- Does not write `problem-statement.md` (orchestrator's job)
- Does not pick reproduction tools — runtime composes Railway/Playwright/curl/etc.
- Does not diagnose — observations only, no causal claims
- Does not edit or write to original pipeline artifacts
- Does not spawn sub-agents
- Does not run adversarial rounds
- Does not retry past reasonable variations — if the bug doesn't reproduce,
  classify NOT REPRODUCED and report

## Reference Files

| File | When to read | Purpose |
|------|--------------|---------|
| `references/repro-format.md` | When writing the repro.md | Template for the output artifact |

## Invocation

This skill is invoked by the fix-pipeline orchestrator with required arguments:

```
fix-reproducer \
  --output-dir .dev/fixes/{original-slug}/{fix-slug}/ \
  --env prod \
  --original-slug jwt-refresh-tokens
```

Or for an interactive run with a fuzzy description:

```
fix-reproducer \
  --output-dir .dev/fixes/{original-slug}/{fix-slug}/ \
  --env dev \
  --interactive
```

The orchestrator is responsible for:

- Generating the fix-slug
- Creating the output directory
- Writing `problem-statement.md` to the output directory
- Invoking this skill with the correct arguments
- Logging the dispatch via forge-bridge

This skill is responsible for:

- Reading inputs
- Performing reproduction appropriate to the environment
- Writing `repro.md` and evidence files
- Reporting the outcome (REPRODUCED / PARTIALLY / NOT REPRODUCED) back via
  the standard FORGE_DONE callback (handled by the orchestrator's dispatch
  protocol, not by this skill directly)
