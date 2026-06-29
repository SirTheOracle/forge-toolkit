# Handoff: Forge session-scoped state fix (start a fresh session here)

**Date:** 2026-06-01
**Status:** NOT STARTED — diagnosis confirmed, fix unimplemented. Use the
adversarial-proposal skill to design the fix (root cause is already known —
investigate the SOLUTION space, do not re-diagnose).

## How to start the new session

Open a fresh session in `/Users/sirdrafton/sirtheoracle/automation/forge-toolkit`
and say one of:

- **Stage-by-stage (recommended — the bridge is load-bearing):**
  "Use the adversarial-proposal skill to design a fix for the forge
  session-scoped-state bug described in
  `handoffs/handoff-2026-06-01-session-scoped-state.md`. Produce the design
  doc and stop — do not implement."
- **Full autonomous pipeline:** `forge-pipeline forge-session-scoped-state`
  (runs the whole adversarial flow through to implementation/verify).

Also read: memory at
`~/.claude/projects/-Users-sirdrafton-sirtheoracle-automation-forge-toolkit/memory/`
(`forge-global-edit-protocol`, `codex-soft-reset`) and the prior handoff
`handoffs/handoff-2026-05-28-per-pane-context-reduction-plan.md`.

## The bug — forge state is project-scoped, not session-scoped

Multiple forge tmux sessions can run in the SAME project. They all share one
set of `.dev/` state files, so a session reads/writes ANOTHER session's
"current pipeline." A window thinks the last pipeline from a different window
is the current one, and newly-started pipelines "don't do anything expected."

**Confirmed live (2026-06-01):** the feedforge project had THREE sessions
(`forge-3`, `forge-6`, `forge-7`) all rooted in the same dir, sharing ONE
`forge-context.yml` that pointed at an already-completed pipeline
(`youtube-playlist-curation-bypass`, `next_stage: complete`). Any orchestrator
reading that sees a finished pipeline as "current" and does nothing.

### Shared state that SHOULD be per-session

| File | Problem |
|---|---|
| `.dev/forge-context.yml` | Active-pipeline pointer — THE main symptom |
| `.dev/.forge-session` | Single file, last-`forge-start`-wins |
| `.dev/forge-log.yml` | Project-wide; used by the send-hook pending check (cross-session false positives) |

Per-pipeline logs `.dev/proposals/{slug}/forge-log.yml` are keyed by slug, so
they do NOT cross-contaminate (unless two sessions reuse a slug — user error).

### Code anchors (`bin/forge-bridge`, ~2932 lines)

- `:56`  `SUMMARY_LOG="$DEV_DIR/forge-log.yml"`
- `:57`  `CONTEXT_FILE="$DEV_DIR/forge-context.yml"`  ← static, NOT keyed by session
- `update_context()` ~331-405 — writes `CONTEXT_FILE`
- `cmd_context` ~986-1014 — reads `CONTEXT_FILE`
- `cmd_set_context` ~2100-2125
- `require_tmux_session` ~91-158 — resolves session via priority:
  1. `TMUX_SESSION` env  2. `tmux display-message` (only if `$TMUX` set)
  3. walk-up `.dev/.forge-session`  4. auto-pick `forge-*` (refuses if >1 candidate)
- `has_pending_log_for()` — matches `SUMMARY_LOG` by canonical pane name
- `forge-start:81` — writes the single `.dev/.forge-session` (last-write-wins)

## Design principle (user's hard requirement)

> "The current window IS the current forge, always. It must NEVER read or act
> on another window's / another session's pipeline state."

**Key enabler:** the session name is already knowable everywhere context is
touched — the orchestrator pins `TMUX_SESSION` (Hard Rule 0), and worker
callbacks run inside a pane so `$TMUX` resolves the session. The state files
simply never used it.

## Likely direction (validate / challenge in the proposal)

- `CONTEXT_FILE` → `forge-context.<session>.yml`, resolved per call (not a
  static global) — needs a best-effort session resolver usable by the no-tmux
  context commands too.
- Per-session `.forge-session` handling for concurrent sessions.
- Optional: session-scope the pending-log checks (`has_pending_log_for`).
- Back-compat: context is regenerable from logs (`set-context --slug`), so
  migration is low-stakes — but the proposal MUST spell out the transition for
  any in-flight pipeline at upgrade time.

## Scope fork the proposal must decide and justify

- **(A) Minimal** — session-scope the context pointer only (fixes the reported
  symptom; smallest, lowest-risk change to the state model).
- **(B) Full isolation** — also the pending-log checks + `.forge-session`
  (no cross-session false positives anywhere; touches the send-hook semantics,
  higher risk).

## Hard constraints

- **Global-edit protocol:** backup first; edit BOTH toolkit source AND installed
  mirrors —
  - `~/bin/forge-bridge`
  - `~/.claude/agents/forge-orchestrator.md` (full copy of toolkit SKILL body)
  - `~/.claude/skills/forge-orchestrator/SKILL.md` (own frontmatter; BODY must
    match toolkit)
  Show the git diff; **NO auto-commit**.
- **Do NOT reintroduce `reset-workers` / `/clear` automation.** It was rolled
  back precisely because `/clear` under a shared `.forge-session` is destructive
  across sessions. Session-scoping is the PREREQUISITE before any worker-reset
  feature can return. That work is parked on branch
  `feat/per-pane-context-reduction` (committed, NOT on `main`, NOT installed).
- Must stay correct for the single-session case (the common path).

## Deliverable

`final-plan.md` containing: the chosen scope (A or B) with rationale; the exact
per-session resolution rule; every read/write site that changes; the back-compat
/ in-flight-pipeline transition; and a test plan covering TWO concurrent sessions
in one project PLUS the single-session baseline.

## Current runtime state (post-rollback, for the new session's awareness)

- Installed bridge + orchestrator were restored to pre-change known-good
  (backups in `~/.cache/forge/edit-backups/2026-05-28-perpane/`). `reset-workers`
  and `gc` do NOT exist in the live bridge.
- Toolkit repo is on `main`; the per-pane-context work is preserved on
  `feat/per-pane-context-reduction`.
- feedforge is currently UNBLOCKED-able by consolidating to one session and/or
  `set-context --slug <wanted>` — not yet done at handoff time.
