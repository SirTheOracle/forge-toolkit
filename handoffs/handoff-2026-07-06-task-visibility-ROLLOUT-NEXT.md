# Handoff: Task visibility — SHIPPED & LIVE; finish the rollout FOR THE OPERATOR

**Date:** 2026-07-06
**From:** forge-coder + rollout session (follows `handoff-2026-07-05-operator-task-visibility-CODER-NEXT.md`)
**Task for the NEXT session:** close the last two rollout items (codex first-turn, 4-task
acceptance run), help the operator work the 3 real hot items, then lift the freeze.
This handoff is operator-centric: the goal is the system WORKING FOR THEM, not more code.

## 1. Status: everything is coded, committed, and live

- forge-coder C1–C7 on main: `a7c7164` `6cfac06` `4fcf050` `f22bc8d` `68e85df` `e1b3141`
  `afdfb7e`; post-ship: `9dceb78` (SwiftBar hides acked rows), `08b401b` (return-path docs),
  `83e94b9` (handoffs). Tree clean. Suites at contract: forge-cc 108, forge-watch 158,
  forge-start 22, infra-lock 63. `install.sh --check-drift` clean. 3 spec-defect deviations
  ledgered in `.dev/proposals/operator-task-visibility/coder-report.md` §Deviations.
- Bins symlinked → LIVE. 4 edited skills synced to ~/.claude/skills + ~/.codex/skills.
- Delivery chain PROVEN: terminal-notifier installed (ring auto-picked it, delivered.log rc=0);
  selftest st-20260705T215013Z-29446 sent → operator SAW it → confirmed → banner self-cleared.
- watch.env: FORGE_WATCH_NOTIFY_REPLY=1, _DONE=1, _PANE_DONE=1 (all three rings armed).
- SwiftBar installed, plugin symlinked (`~/Library/Application Support/SwiftBar/forge-board.5s.sh`
  → repo file), menubar live.
- Codex: `forge register` re-run at all 4 roots (headless_factory, feedforge, goparent-ai,
  promptlol) → `.codex/hooks.json` (hash 48515cbc) + markers; operator ran `/hooks` at each root.
- Board debris cleaned: 14 stale unanswered test dispatches (Jul 3–5 smoke/probe pings)
  archived to `<root>/.dev/attention/archive/` (reversible; GC clears in 7d). Board is truthful.

## 2. Remaining rollout (2 items)

1. **Clear the 4 CODEX-EMISSION-OFF rows** — trust is proven OBSERVATIONALLY: run ONE turn in a
   codex pane at each root; the first codex-tagged emission clears that root's row. `/hooks`
   approval alone does not (by design — codex exposes no machine-readable trust state).
2. **4-task value-bar acceptance run** (formally closes the ship; folds item 1 in):
   - dispatch 2 tasks from the seat (`forge dispatch @forge-N "..."`),
   - type 1 task directly into a claude worker pane (pane 0 or 4),
   - type 1 task into a codex pane,
   - VERIFY: all four appear in `forge tasks`/menubar within ~1s of each turn-end; a ring fires
     per the armed knobs; then optionally unload launchd (`forge-watch uninstall`) to see the
     staleness line + SwiftBar ⚠, and reinstall.
   Then: **lift the freeze** — GoParent's `.dev/proposals/prep-strategic-profile-split/final-plan.md`
   has been waiting since 2026-07-05.

## 3. The operator's live queue (3 real items, commands on the board)

- forge-1/headless_factory **NEEDS-ASK** (feat-edit-cancel-scheduled-social-post/qa-fix):
  worker asks about QA findings F-B02/F-B04 →
  `forge dispatch @forge-1 "<answer>" --answers ask-20260705T232857Z-e7b038`
- forge-1/headless_factory **NEEDS-DECISION**: qa-retry finished, needs go/no-go → dispatch it.
- forge-3/goparent-ai **NEEDS-REPLY**: mediation sidebar counter-question →
  `forge dispatch @forge-3 "<your reply>"`

## 4. How the operator reads the system (teach-back, keep it this simple)

- **Menubar** `forge ✓` = nothing new; `forge N!` = N unseen items; trailing `⚠` = the watcher
  itself is stale (self-reporting failure). Dropdown = unseen rows only + gray "N seen hidden".
- **`forge board`** = the detail view: NEEDS YOU (with paste-ready commands) / SESSIONS /
  TASKS (per-turn, every pane, incl. typed + codex) / MAINTENANCE (collapsed).
- **`forge tasks [@session] [--json]`** = per-task narrative.
- Rows clear by RESOLVING (answer/reply/decide); `forge-watch ack <session>` = "seen, deferring"
  (drops menubar count; board keeps the row tagged [acked]).
- Latency: ~1s typical (event-driven triggers), 30s guaranteed floor (launchd).

## 5. Known UX debt (candidates, only if the operator asks)

- `ack` is session-scoped only — it swept the 3 real items along with debris on 2026-07-06;
  a per-row ack (`forge-watch ack <session> <task_id|condition>`) is the obvious next verb.
- TASK-STUCK on a dispatch that was absorbed into another turn can't tell "dropped" from
  "absorbed-and-handled" — archiving the dispatch file is the current manual remedy.
- A never-uses-codex root would show a permanent collapsed CODEX-EMISSION-OFF row (accepted).

## 6. Hard context (unchanged)

- Seat-operator interaction rules in force (no tmux/dispatch/notification/osascript without an
  explicit per-action go; a classifier deny on `forge register` with inferred targets was
  correct — name targets explicitly and get the operator's go).
- ALL project work FROZEN until the acceptance run passes and the operator lifts it.
- No-auto-commit protocol applies again (the forge-coder contract is closed); the operator
  authorized the 2026-07-06 commits explicitly.
- Memory `command-center-v2-status.md` is current through this handoff.
