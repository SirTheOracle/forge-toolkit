# Handoff: Operator task visibility — implementation.md COMPLETE, forge-coder next

**Date:** 2026-07-05
**From:** adversarial-implementation session (follows `handoff-2026-07-05-operator-task-visibility-IMPLEMENTATION-NEXT.md`)
**Task for the NEXT session:** run the `forge-coder` skill on
`.dev/proposals/operator-task-visibility/implementation.md` — mechanical execution
only, per the return-path pattern. Then live QA per its Definition of Done (all
live-fire steps operator-gated).

## 1. Status: everything before coding is DONE

1. **final-plan.md APPROVED** by operator 2026-07-05 (binding). Operator decisions:
   Q1 ambient surface = **SwiftBar** (phone push NO); ring-timing change GO;
   terminal-notifier install GO; **PANE-DONE ring ON via watch.env flip at rollout**
   (code default stays 0). Live-fire selftest/acceptance still need per-action gos.
2. **All 3 codex verification gates PASSED live** → `verify-results.md`:
   - G1: requirements.toml is enterprise/MDM-only → Step 7 ships via NON-managed
     `<root>/.codex/hooks.json` installed by `forge register` + one-time /hooks
     per-hash trust review (re-required if the hook block changes).
   - G2: payload fields byte-exact; **turn_id identical across a turn's
     UserPromptSubmit and Stop** → codex adapter correlates on turn_id.
   - G3: TMUX_PANE inherited into codex processes (pane precision available).
3. **Adversarial-implementation 4-round flow COMPLETE (zero agent deaths)** →
   final `implementation.md` (2303 ln, §1–§8 + §7b reconciliation + Attribution).
   Deliberation trail: impl-A/B/C.md, review-for-A/B.md, impl-feedback-A/B.md,
   impl-notes-A/B/C.md (proposal-phase reviews renamed proposal-review-for-*.md).

## 2. Round-4 reconciliation results (why the doc looks the way it does)

Verdicts: A APPROVE (1 MEDIUM + 3 LOW), B BLOCKING (1 item). Tally 7 ACCEPT /
1 PARTIAL / 1 REJECT:
- B-C1 BLOCKING accepted **in code**: `forge tasks @<session>` + `--json` now
  actually parsed (cmd_status → FORGE_WATCH_TASKS_SESSION/_JSON; render filters +
  json.dumps) + tview2 test. `forge board --json` unaffected (never sets those).
- A-MEDIUM accepted: 6a/6b (orchestrator Stop/PermissionRequest triggers) mapped
  into commit C5 + new 4.6-orch sentinel test (P28 "both roles" now provable).
- A-LOW accepted: trigger tests are sentinel-based (stub touches sentinel before
  sleeping — detach AND firing proven); dispatch trigger moved to the real branch
  (no trigger on --dry-run).
- PARTIAL: relayed task appears as two tasks[] rows (dispatch + wstop) — documented
  as intended narration, NO dedup in v1.
- REJECT: python-vs-bash detach style nit (both detach; idiomatic per language).

## 3. Coder contract (from implementation.md §6/§8)

- Commit groups C1–C7 in order; expected totals: **forge-cc 91→108** (reads 107
  after C1–C4; 108 once C5 lands — 4.6-orch ships with 6a/6b), **forge-watch
  121→158**, spawn 31 / forge-start 22 / infra-lock 63 unchanged.
- Baselines MUST be re-measured green before the first edit (return-path pattern).
- The existing "pane-0 Stop ignored" test is REWRITTEN to assert namespaced emission.
- New files: config/codex-cc-hooks.json, the SwiftBar plugin (swiftbar/
  forge-board.5s.sh), plus skills edits (command-center both copies; forge-orchestrator
  SKILL.md has a docs-refresh Source-sha256 hash guard at ~:1313 → regen required;
  codex-skills/forge-coder mirror too).
- Hard constraints re-assert throughout: worker Stop never touches canonical
  stop.<session>.json; per-TURN keying; forge-watch read-only; cc-*/1 additive;
  hooks fail-open; triggers detached (nohup/start_new_session); redaction on all new
  payloads. Everything tested via hermetic seams (FORGE_WATCH_SINK_CAPTURE etc.).

## 4. Operator-gated steps (each needs an explicit per-action go — do NOT run)

selftest (`forge-watch selftest` + `--confirm`), the 4-task acceptance run
(2 dispatched / 1 typed pane 0 / 1 codex), watch.env FORGE_WATCH_NOTIFY_PANE_DONE=1
flip, `brew install terminal-notifier`, SwiftBar install + plugin symlink, and the
per-root codex /hooks trust review.

## 5. Hard context (unchanged, still binding)

- ALL project work remains FROZEN until this ships with value (GoParent final-plan.md
  still waits).
- Seat-operator interaction rules in force (no tmux/dispatch/notification/osascript
  without explicit per-action go; stop covers diagnostics).
- Repo: main at 2415505; bins symlinked → working tree LIVE. Uncommitted:
  docs/forge-operator-guide.md + docs/forge-technical-reference.md (return-path docs,
  do not revert; operator commits), 5 untracked handoffs + this one. No-auto-commit
  protocol applies to everything EXCEPT forge-coder's own contracted commits C1–C7.
- Subagent watchdog discipline: heartbeat notes files + two-strike ping +
  lead-does-it-inline (this run needed none).
