# Stall Detection

This reference describes the classifier inside `forge-bridge stall-check` and
`forge-bridge wait`. The orchestrator does not normally call `stall-check`
directly — `wait` invokes it internally. This file is debugging-only.

`~/bin/forge-bridge stall-check --project-root "$PROJECT_ROOT" <pane>` returns
one of seven states based on (a) whether a pending dispatch targets the
pane, (b) whether the pane's normalized content changed since last check,
and (c) per-pane regex matches against `~/.config/forge/idle-prompts.yml`.

Watched external worker panes in Phase 2:
- `claude-opus` / pane 0 — incorporate, impl-review
- `codex-a` / pane 2 — review and eligible implementation/review work
- `codex-b` / pane 3 — implementation and QA work
- `claude-sonnet` / pane 4 — coding, qa-fix, verify local fallback

| state                          | trigger                                                              | bridge action / orchestrator response |
|--------------------------------|----------------------------------------------------------------------|---------------------------------------|
| ACTIVE                         | content changed, or Claude active_work_marker appears in last 30 lines | `wait` keeps polling                |
| IDLE                           | no pending log targeting this pane                                   | `wait` keeps polling                  |
| COMPLETED-PENDING-LOG-RESPONSE | pending log + idle-prompt regex match on normalized tail and no active marker | `wait` confirms via callback file; if no callback yet, keeps polling |
| STALLED                        | pending log + no idle match + elapsed > threshold + no PROMPTING     | `wait` returns STATUS=STALLED; orchestrator treats as AGENT_FAILED |
| PROMPTING                      | approval-prompt regex match (regardless of elapsed)                  | `wait` returns STATUS=PROMPTING immediately; orchestrator surfaces to user |
| DEAD                           | tmux pane gone                                                       | `wait` returns STATUS=DEAD; orchestrator halts |
| UNKNOWN                        | first call after cache wipe / unable to characterize regex           | `wait` keeps polling                  |

Claude panes require a two-anchor classifier: `idle_prompt_anchor` in the
last 5 normalized non-blank lines AND no `active_work_marker` in the last
30 normalized non-blank lines. If `active_work_marker` is missing for
`claude-opus` or `claude-sonnet`, stall-check returns
`UNKNOWN reason=active_work_marker_unavailable` rather than guessing.

State is computed from per-pane snapshots in
`~/.cache/forge/<session>-pane<idx>.snapshot`, wiped on `forge-start` for
session-name reuse hygiene. No daemon process runs. `wait` polls on its own
interval (default 15 s, override via `--interval` or `FORGE_WAIT_INTERVAL_S`).

Threshold: `FORGE_STALL_THRESHOLD_S` (default 600 seconds = 10 min). For
legitimately-long stages, the orchestrator can pass a per-stage timeout to
`wait` via `--timeout`. Suggested per-stage values:
  - proposal:  1800   (30 min — Agent Teams take a while)
  - review:     600   (10 min)
  - incorporate, impl-review: 1200 (20 min — Opus reasoning)
  - coding:    1500   (25 min)
  - qa, qa-fix: 1200  (20 min)
  - verify:     900   (15 min)

Self-detection-of-dead-detector (canonical §2.4): `forge-bridge context`
prepends a `=== Stall Check Status ===` block when any pane has a pending
dispatch but `stall-check` hasn't run within 2× threshold. This surfaces on
every session-start path (Hard Rule 16), making coverage gaps observable
without a daemon.

The runtime regex tables at `~/.config/forge/idle-prompts.yml` are
installed from committed verification fixtures via
`~/bin/forge-stall-install-regex` (per-project; the fixtures live under each
project's `.dev/proposals/forge-sonnet-pane/verification/phase1b-step0/` and
`.dev/proposals/forge-sonnet-pane/verification/phase2-step0/`). Running the
install script after a Codex or Claude Code CLI update (or any time the
runtime regex matches stop working) is the self-service repair path.

Unattended stalls (orchestrator silent + user away) go undetected until
the next orchestrator wake. This is an accepted limitation of the no-daemon
design (canonical §3.2 reactive + §8 anti-momentum-trap carve-out).
