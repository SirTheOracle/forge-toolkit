# Repro Report Format

The reproduction report is the primary artifact produced by the fix-reproducer
skill. It MUST be written to `{output_dir}/repro.md` on every run — including
NOT REPRODUCED, ambiguous descriptions, and infrastructure failures. A
documented negative result is itself input for investigate.

## Template

```markdown
# Repro Report: {fix-slug}

**Generated**: {ISO 8601 timestamp}
**Environment**: prod | dev
**Original pipeline**: {original-slug or "n/a"}
**Problem statement**: `{output_dir}/problem-statement.md`

## Environment

- **Target**: prod | dev
- **Version / commit**: {git SHA, deployed release tag, or "unknown"}
- **Service(s) involved**: {e.g. backend@8000, frontend@5173, runpod-wan2}
- **Config notes**: {feature flags, env vars, account/tenant, anything that
  affected outcome}

For prod, also note:
- **Region / cluster**: {if relevant}
- **Time window observed**: {ISO 8601 range — when the evidence was gathered}

## Problem Statement (Structured)

Parsed from `problem-statement.md` in Phase 1.

- **Action sequence**: {numbered steps the user took or the system was
  performing when the bug appeared}
- **Expected behavior**: {what should have happened}
- **Observed behavior**: {what actually happened}
- **Error indicators**: {error messages, stack traces, status codes — quote
  verbatim where possible}
- **Environmental hints**: {timing, state, data shape, user role, etc.}

## Reproduction Status

**Outcome**: REPRODUCED | PARTIALLY REPRODUCED | NOT REPRODUCED

For prod, also specify the sub-outcome (REPRODUCED only):
- **Sub-outcome**: live | telemetry | statistical
  - `live` — bug observed by direct reproduction against prod
  - `telemetry` — bug not directly reproduced, but production evidence
    (logs, error tracking) confirms it occurred
  - `statistical` — telemetry shows N occurrences but no deterministic
    trigger was identified (include occurrence count + window)

For PARTIALLY REPRODUCED, summarize how the observed behavior differs from
the reported description.

For NOT REPRODUCED, summarize what was attempted and why reproduction failed
(infra issue, ambiguous description, behavior not triggered, etc.).

## Steps to Reproduce

Numbered, deterministic, with expected vs. actual per step. If NOT
REPRODUCED, leave this empty or write "Not applicable — see Variations
Attempted."

1. **{Action}**
   - Expected: {what should happen}
   - Actual: {what happened}
   - Evidence: `evidence/{path}` (screenshot, log excerpt, response body)
2. ...

For prod telemetry / statistical sub-outcomes, "steps" may instead be the
queries / dashboards / log filters that surfaced the evidence.

## Evidence

All evidence files live under `{output_dir}/evidence/`. Reference each
artifact with its relative path and a one-line description.

| Path | Type | Description |
|------|------|-------------|
| `evidence/screenshots/01-form-error.png` | screenshot | UI state when submit failed |
| `evidence/logs/backend-2026-04-30T10:15.log` | log | Backend logs around the failure |
| `evidence/captures/api-response.json` | response | Raw response body from `/api/foo` |
| `evidence/telemetry/sentry-issue-1234.txt` | telemetry | Sentry issue reference + summary |

Subdirectory conventions:
- `evidence/screenshots/` — PNG/JPG screenshots
- `evidence/logs/` — log excerpts (timestamped filenames preferred)
- `evidence/captures/` — network/HAR captures, response bodies
- `evidence/telemetry/` — references to production telemetry (prod only)

## Environment State

Anything that affected outcome and might matter for investigate:

- **Versions**: {service versions, library versions, browser, OS}
- **Config**: {feature flags, env vars, runtime settings}
- **Data state**: {DB row counts, queue depth, fixtures used, account/tenant}
- **Auth state**: {logged-in user, role, permissions}
- **Network / external**: {third-party API status, region}

## Variations Attempted

Bounded variations tried during Phase 2. Especially relevant for PARTIALLY or
NOT REPRODUCED — show your work.

| # | Variation | Outcome |
|---|-----------|---------|
| 1 | {what you changed from the literal steps} | {what happened} |
| 2 | {…} | {…} |

If the description was ambiguous, note the ambiguity and which interpretation
each variation tested.

## Initial Observations

**Strictly observational.** No causal claims, no "the bug is in module X."
Note what you saw; let investigate decide what it means.

- {Observation 1 — e.g. "Failure only occurred when payload included the
  `X-Custom` header; without it the request returned 200."}
- {Observation 2 — e.g. "Backend log shows the failure path runs an extra
  DB query not present in the success path."}
- ...

## Confidence + Blocking Items

The final two lines of the report MUST be exactly this format:

```
CONFIDENCE: HIGH | MEDIUM | LOW
BLOCKING_ITEMS: N
```

Guidance:
- **HIGH** — bug is reproduced or confirmed via concrete evidence;
  investigate has everything it needs to start.
- **MEDIUM** — partial reproduction, or reproduced but with gaps in
  environment state / evidence.
- **LOW** — could not reproduce; description is ambiguous; or evidence is
  thin enough that investigate may need to gather more before proceeding.

`BLOCKING_ITEMS` counts items that should block downstream stages:
- NOT REPRODUCED defaults to `BLOCKING_ITEMS > 0` because investigate
  cannot proceed productively without something to investigate.
- REPRODUCED with full evidence and clear steps → `BLOCKING_ITEMS: 0`.
- PARTIALLY REPRODUCED → count the unresolved gaps (e.g. "1" for an
  ambiguous environment, "2" for ambiguous environment + missing
  reproduction of one of two reported symptoms).

## Rules

1. Always write `repro.md`, even on failure. Partial progress is valuable
   input for investigate or for the user to decide next steps.
2. Initial Observations are strictly observational — no causal claims.
3. The `Environment` line must match what was actually used. Never silently
   fall back from prod to dev (or vice versa).
4. Evidence paths must be relative to `{output_dir}/` and the files must
   exist on disk before the report is finalized.
5. The last two lines of the file are `CONFIDENCE:` and `BLOCKING_ITEMS:`
   in that order — this is the contract the orchestrator parses.
