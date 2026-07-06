# Handoff: Response return path — implementation.md vetted, run forge-coder

**Date:** 2026-07-04
**From:** adversarial-implementation session (follows `handoff-2026-07-04-response-return-path-IMPLEMENTATION-NEXT.md`)
**Task for THIS session:** run the `forge-coder` skill on
`.dev/proposals/response-return-path/implementation.md` — apply the diffs in commit-group
order, run the test plan, produce `coder-report.md`. The doc is self-contained; the coder
needs no other deliberation file.

## 1. State at handoff

- Base: `main` — the two precondition commits landed and verified green:
  `c26f1e4` (registry adopts only cc-registry/1 files) and `dac58ab` (human-first board).
  All diffs in implementation.md were verified against this base. If main has moved,
  re-verify anchors before applying.
- **LV-1/LV-3 pre-implementation gates PASSED** (`.dev/proposals/response-return-path/lv-results.md`):
  LV-1 confirmed the standard UPS-at-dequeue ordering live (3 probes on forge-1) →
  Step 3b ships exactly as written (file-existence `--wait` predicate). LV-3 pre-code
  confirmed absorption degrades to a clean timeout. **LV-2 and behavioral LV-3 re-run
  POST-code** — they are in the doc's Definition of Done.
- The 4-round adversarial flow completed clean: both critics APPROVED with zero blocking
  defects; their one convergent finding (two untested paths) was folded in as R10's
  `--timeout 1` assertion and the new R15 composition test.

## 2. Execution facts the coder must honor

- **Bins are symlinks** (`~/bin/forge*` → repo): every applied diff is LIVE immediately.
  Apply → test → commit per group; do not leave the tree half-applied overnight.
- **Re-measure baselines FIRST**: expected totals are deltas over live-measured baselines
  (forge-cc 57, forge-watch 98 at handoff). Expected after: **forge-cc 91, forge-watch 121**;
  spawn 31 / forge-start 22 / infra-lock 63 untouched.
- **4 commit groups** as specified in implementation.md §6 (hook; forge verbs; forge-watch
  + knobs; skill both copies). Both skill copies must stay byte-identical (md5 check in DoD);
  `./install.sh --check-drift` must be zero at the end.
- The seven §0 R1 items and the nine hard constraints are audited in the doc (§8/§9) —
  the coder applies, it does not re-litigate.

## 3. After the code lands (QA stage)

- LV-2 (coalescing shape) + LV-3 (behavioral: `--wait` timeout on an absorbed did, `forge
  reply` returns the combined answer) live on forge-1..4.
- Manual live pass from the plan: `--wait` Q&A loop; fire-and-forget → NEEDS-REPLY hot row,
  no bell; `FORGE_WATCH_NOTIFY_REPLY=1` → exactly one bell (operator signaled they'll
  likely leave it on after shakedown, but it ships default-off).
- Remaining backlog unchanged: two-worktree infra-lock pipeline run; new-project spawn
  shakedown.

## 4. Process notes

- FOUR silent agent deaths this run (implementer-A ×2, synthesizer ×1 — all while composing
  large writes; one false alarm each for A3/C2 during long reads). The ladder that worked
  every time: write-early skeleton → notes-file heartbeat per file/section → 7-8 min stall
  watchdog → one ping + 3-min verdict monitor → respawn RESUMING from the notes file
  (never restart). Enforce ≤100-line incremental writes on any heavy agent from the start.
- Deliberation trail (only if a diff-level question re-opens a settled debate — don't
  re-litigate): impl-A/B/C.md, review-for-A/B.md, impl-feedback-A/B.md,
  reconciliation-notes.md, review-codex.md, all under `.dev/proposals/response-return-path/`.
