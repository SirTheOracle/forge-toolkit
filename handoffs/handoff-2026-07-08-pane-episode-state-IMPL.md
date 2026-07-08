# Handoff: pane episode state — IMPLEMENT the vetted plan

**Date:** 2026-07-08
**From:** seat session (adversarial-proposal run + 3 external-review revision passes)
**Task for the NEXT session:** implement
`.dev/proposals/pane-episode-state/final-plan.md` (revision R3) in `bin/forge-watch` +
`tests/forge-watch/run.sh`, via the coder procedure, no-auto-commit. The plan is FROZEN —
do not redesign, do not re-run the adversarial process, do not relitigate anything in it.

## 0. Gate before any edit

1. **Operator approval of D1–D3** (final-plan §4 "Explicit Operator Decisions") has NOT yet
   been given verbatim. Read them back to the operator and get an explicit yes/no on:
   - **D1** — retire the `PANE-DONE` condition into `EPISODE-SETTLED`.
   - **D2** — CANCEL the pending `FORGE_WATCH_NOTIFY_PANE_DONE` flip, with the migration
     rule (never silently de-arm; replace an armed line with
     `FORGE_WATCH_NOTIFY_EPISODE_DONE=1` in the same edit).
   - **D3** — ship `FORGE_WATCH_NOTIFY_EPISODE_DONE` **default OFF**; operator flips it in
     watch.env only after live QA. (D4, the SETTLE+270s cold-restart ring band, is an
     engineering default inside D3 — no separate ask.)
2. **Pre-flip gates from the plan** (cheap, run before coding):
   - `grep -rn 'PANE-DONE' bin tests ~/.claude/skills` excluding `-RESIDUE` → must hit only
     `bin/forge-watch` + its tests (re-verify no new consumer appeared).
   - `grep FORGE_WATCH_NOTIFY_PANE_DONE ~/.config/forge/watch.env` → expected empty
     (flip was pending, never armed). If a line exists, D2's migration sub-case applies.
3. Confirm baseline: repo clean at `1e31de5`, suites green
   (forge-cc 123/0, forge-watch 178/0, forge-start 0 fail, infra-lock 63/0).

## 1. What to implement (the short map — the plan is the authority)

All changes in `bin/forge-watch` + `tests/forge-watch/run.sh`; `bin/forge-cc-hook` and
`bin/forge` unchanged; SwiftBar explicitly OUT of v1 scope. Execution order (plan §3):

- **Step 0** — `add_finding` returns the appended dict (`:491-497`).
- **Step 1** — knobs: `FORGE_WATCH_EPISODE_SETTLE_S` (600), `FORGE_WATCH_NOTIFY_EPISODE_DONE`
  (False), derived `EPISODE_RING_FRESH_S = SETTLE + max(270, 3*STALE_TICK_S)`; ALLOWED_KEYS
  per D2.
- **Step 2** — stateless episode derivation inside `scan_attention` after `now_working`
  (`:965`): union of stop-panes ∪ prompt-panes; partition ALL stops into contiguous runs
  (gap ≥ SETTLE splits); **R2a** wprompt attach-vs-split rule (gap ≥ SETTLE → synthetic
  prompt-only current run, never resurrect the old episode); **R3a** module-level
  `EPISODES = []` beside `TASKS` (`:488-489`), local list extends it; sort/cap ONLY at board
  assembly.
- **Step 3** — replace the PANE-DONE loop (`:979-991`): EPISODE-ACTIVE (`policy='never'`) /
  EPISODE-SETTLED (ring-once via `once`, freshness-band-gated) / EPISODE-STUCK (hot,
  reuses `TASK_STUCK_S`); **conditions from the CURRENT run only**; **R2b** settled runs
  older than `TASK_WINDOW_S` → `continue` (residue covers them; STUCK/in-progress exempt);
  **msgs carry ONLY turn_count + snippet — no live ages** (QUIET/launchd protection);
  episode metadata attached to the returned finding dict. Keep PANE-DONE-RESIDUE untouched.
- **Step 4** — spine registration + suppression: `task_stuck_ids` set populated at the
  TASK-STUCK emission site (`:1036-1043`) — **never parse composite finding keys**; filter
  EPISODE-STUCK whose `(root, session, dispatch_id)` matches, before dedup (`:1145-1149`).
- **Step 5** — rendering: board-time `episode_rows = sorted(EPISODES, ...)[:200]` → additive
  `episodes[]` in cc-board/1; per-task `episode_id` tagging keyed `(root, session, pane)` +
  run interval (**R3b**, never task_id alone; uncapped partition is the lookup — **R2c**);
  `_row()` explicit metadata copy (**R1c**); pretty TASKS re-keyed `(session, pane)` with
  episode header + `(pane active earlier)`; SESSIONS = dim `· N pane(s) active` annotation,
  **glyph untouched**; `forge tasks --json` stays a bare array (**R1d**).
- **Step 6** — tests: 20 specs in plan §7 (incl. R2 tests 17/18, R3 tests 19/20) +
  regression rewrites (3 legacy PANE-DONE assertions red-then-rewritten; keep-verbatim list
  in §7); `wpromptf` gains optional 8th `dispatch_id` arg (**R1f**).

## 2. Verification bar

- Full suites green: forge-watch (rewritten), forge-cc, forge-start, infra-lock — no
  regressions outside the three intended PANE-DONE assertion rewrites.
- The QUIET byte-identical test (15) and ring-once test (6) are the two guarantees the
  operator cares most about — do not skip.
- Live QA (episode header on a real multi-turn job, settle flip, annotation) is
  operator-gated and SEPARATE — do not drive panes or ring anything yourself.

## 3. Hard context (unchanged, in force)

- Seat-operator rules: no outward actions without explicit per-action go; bins in `~/bin`
  are symlinks into this repo — **edits are LIVE the moment they're saved** (the watcher
  launchd tick runs every 30s; keep the tree consistent, land test rewrites in the same
  edit batch as Step 3).
- No auto-commit. Present the diff; the operator commits.
- forge-watch invariants: READ-ONLY (never writes attention files), additive-only schemas,
  deterministic/zero-model, worker events never mark the session lifecycle done, no pane
  scraping, hooks fail-open.
- Implementation via the coder procedure (forge-coder-style: apply plan mechanically, run
  tests per step, write a coder report into `.dev/proposals/pane-episode-state/`); deviations
  from the plan must be recorded as deviations, not silently improvised.

## 4. Paper trail (read before coding)

- `problem-statement.md` — ratified bar (§3/§4) + constraints (§5).
- `final-plan.md` — THE spec, at revision R3 (R1a–f, R2a–c, R3a–b markers inline).
- `reconciliation-notes.md` — Rounds 1–4 (adversarial) + Rounds 5–7 (codex reviews, every
  finding dispositioned). `review-codex.md`, `review-codex-r3.md` — the external reviews.
- Memory: `pane-episode-state-status.md` current through R3.

## 5. Adjacent open items (not this session's job)

- Live QA of `1e31de5` (AskUserQuestion visibility) still pending — unrelated surface, but
  it shares `forge board`; QA both together if the operator wants.
- Codex `/hooks` trust per root still pending (CODEX-EMISSION-OFF) — affects episode
  coverage for codex panes (plan Q7 already defines the degraded behavior).
- Watcher-hardening handoff §3 record-level gaps — still open, unowned.
