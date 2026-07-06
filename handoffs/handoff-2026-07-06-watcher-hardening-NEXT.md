# Handoff: forge-watch hardening — latent warts exposed by the 115s-stale incident

**Date:** 2026-07-06
**From:** board-fidelity session (followed `handoff-2026-07-06-task-visibility-ROLLOUT-NEXT.md`)
**Task for the NEXT session:** a small hardening pass on forge-watch's run/heartbeat/notify
plumbing. None of these are urgent — the system self-healed — but each one makes a real
failure harder to see or slower to recover from. **Do this only after the freeze is lifted**
(rollout close-out still lives in the task-visibility handoff: codex first-turn per root,
4-task acceptance run).

## 0. What already got fixed this session (do NOT redo)

- `54f928c` — buried-question detection (`trailing_question()` scans the last 8 non-empty
  lines; endswith('?') alone missed a worker that asked then appended a footer) +
  sentence-aligned `snip_tail` + display ellipsis. Both forge-cc-hook emission sites.
- `1c540a4` — human board redesign (grouped TASKS, ages, stage-dispatch summarization,
  section bands, word-boundary clip). JSON contracts untouched.
- forge-1's stale hot rows were record-level, resolved via `--answers` (ask archived) and an
  orchestrator housekeeping dispatch (`next_stage: complete`). See §3.

## 1. The incident (evidence, all verified live 2026-07-06)

Between ~10:44:38 and ~10:46:40 the SwiftBar menubar showed `forge ✓ ⚠` with
"watcher stale (115s)". Reconstruction:

- ONE `forge-watch check` run started 10:44:38 and took ~110s (its notification deliveries
  logged to `~/.cache/forge-watch/delivered.log` at 17:46:28Z, rc=0).
- While it held `state.lock`, every 30s launchd tick hit `flock(LOCK_EX|LOCK_NB)` →
  **exited 0 silently** (forge-watch:249-254). `launchctl list` showed exit 0, `launchd.err`
  empty — zero trace of the stall.
- The slow run then stamped `state['last_tick'] = NOW` where **NOW is process-start time**
  (forge-watch:127, written at :1139) — i.e. it wrote a heartbeat that was already ~110s old.
- Next tick ran normally; board healthy since. Root cause of the 110s hang itself was never
  pinned (terminal-notifier is the prime suspect; scans measure ~0.3s).

SwiftBar behaved exactly as designed — self-reported staleness, self-cleared. The warts are
in how the watcher gets INTO and reports that state.

## 2. The hardening items

1. **Heartbeat stamped with run START time.** `state['last_tick'] = NOW.isoformat()`
   (forge-watch:1139) uses the module-level NOW (:127). A slow run's heartbeat is stale at
   birth, inflating the reported staleness and double-counting the hang. Fix: stamp
   completion time (`datetime.datetime.now(UTC)`) at the save. One line; check whether any
   test pins last_tick == event NOW.
2. **Lock contention is invisible and unbounded.** A hung run holds `state.lock` forever;
   ticks skip quietly (exit 0, no log). Fix candidates, cheapest first: (a) SIGALRM
   watchdog on check mode (~60-90s) so a hung run dies and the next tick recovers —
   this alone converts a 2-minute stall into one missed tick; (b) log a one-line
   "tick skipped, lock held Ns" to stderr so launchd.err finally has evidence.
3. **terminal-notifier has no timeout.** `fw_notify` (forge-watch:~85-87) shells out bare;
   a wedged Notification Center stalls the whole check while holding the lock. Fix: wrap
   the notifier call with a timeout (macOS has no coreutils `timeout` by default — use
   `perl -e 'alarm N; exec @ARGV'` or a background+wait pattern); on timeout treat as
   nonzero rc → existing DELIVERY-UNVERIFIED path already handles it.
4. **launchd.out grows unbounded — 37MB on 2026-07-06.** Every 30s tick appends the full
   findings dump (`StandardOutPath ~/.cache/forge-watch/launchd.err|out`, plist
   `com.forge.watch`). delivered.log + state.json already carry the durable signal. Fix
   candidates: make check mode print nothing (or a one-line delta) when findings are
   unchanged; and/or have `forge gc` truncate launchd.out past a size cap. Truncate the
   current 37MB file at install/first run of the fix.

Suggested order: 3 → 2a → 1 → 4 (the notifier timeout removes the likely hang source;
the watchdog bounds whatever else can hang; then the reporting fixes).
Suites at contract: forge-watch 158, forge-cc 108, forge-start 22, infra-lock 63.
Protocol: no-auto-commit; bins are symlinks so edits are live immediately — mind the
running launchd agent when testing check-mode changes (`forge-watch uninstall` first, or
test via hermetic env like the suite does).

## 3. Related record-level gaps (same family, lower priority)

- **Close-out leaves NEEDS-DECISION residue.** The forge-1 close-out appended "FEATURE
  CLOSED" to context NOTES but left `next_stage: qa` → the board flagged a phantom decision
  for 17h. Candidate: orchestrator-skill close-out contract MUST set `next_stage: complete`
  (doc/skill change, maybe a bridge close verb). This will recur on every conversational
  close-out until fixed.
- **`ack` is session-scoped only** (swept 3 real items with 14 debris rows on 2026-07-06);
  per-row ack (`forge-watch ack <session> <task_id|condition>`) is the obvious next verb.
- **TASK-STUCK can't tell dropped from absorbed-and-handled**; manual remedy is archiving
  the dispatch file. Carried from the rollout handoff.

## 4. Hard context (unchanged)

- Seat-operator interaction rules in force (no dispatch/tmux/notification/osascript without
  an explicit per-action go; reads are fine).
- Freeze status lives with the operator; rollout close-out checklist is in
  `handoff-2026-07-06-task-visibility-ROLLOUT-NEXT.md` §2.
- Memory `command-center-v2-status.md` is current through this handoff.
