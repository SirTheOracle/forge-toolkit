# Handoff: pane episode state — run the adversarial proposal

**Date:** 2026-07-07
**From:** seat session (AskUserQuestion visibility + episode-state framing)
**Task for the NEXT session:** run the **adversarial-proposal** skill against
`.dev/proposals/pane-episode-state/problem-statement.md` to produce a vetted
`final-plan.md` in the same directory. Plan only — do NOT implement. The operator
chose the adversarial route deliberately (fresh-usage session) because this change
redefines the board's "done" semantics and touches ring policy; do not downgrade to
adversarial-lite without operator say-so.

## 1. The task, precisely

- Input: `.dev/proposals/pane-episode-state/problem-statement.md`. Read it whole
  before spawning anything — its §Framing rule matters: the experience bar (§3) and
  design direction (§4, derive episode state from the existing wprompt/wstop stream)
  are operator-RATIFIED; the run owns the MECHANISM (boundary rules, settle window,
  precedence, ring policy, schema, rendering, tests, failure modes of timing-derived
  state), and §4's sketch is attackable.
- Output: `final-plan.md` answering all ten §6 open questions explicitly. Ring-policy
  changes (including the fate of the pending `FORGE_WATCH_NOTIFY_PANE_DONE` flip) must
  be surfaced as explicit operator decisions, never buried in a diff.
- After the run: present the plan to the operator for approval. Implementation is a
  SEPARATE, operator-gated step (fix-coder/forge-coder procedure, no-auto-commit
  protocol).

## 2. What this session shipped (context for the run, do NOT redo)

- `31c1dc2` — watcher hardening (notifier timeout, check watchdog, lock-skip trace,
  heartbeat, gc log cap) + PostToolUse perm-row resolution.
- `1e31de5` — AskUserQuestion visibility: perm records now carry
  `question_snippet`/`question_options`/`question_count`/`multi_select`; forge-watch
  `perm_detail()` renders question + options on NEEDS-PERMISSION rows; hot perm rows
  print a go-to-pane hint; command-center SKILL.md updated + mirrored to
  `~/.claude/skills/`. Plan: `.dev/proposals/askuserquestion-visibility/`
  (adversarial waived for that one — small, additive, fail-open; the episode-state
  change does NOT qualify for the same waiver).
- Suites green at `1e31de5`: forge-cc 123/0, forge-watch 178/0, forge-start 0 fail,
  infra-lock 63/0. Working tree clean.

## 3. How the problem was found (the story, short)

Operator ran a multi-round adversarial-fix-qa job in pane 0 of forge-3 (goparent-ai).
The board showed 25+ per-turn `done` rows for it while the job was mid-flight
("Round 4 complete…" 31s old) — because the watcher treats every wstop as terminal.
Operator's correction, verbatim in the problem statement §1: the start/done stream
per pane already exists on disk; USE it. The ratified UX: each pane answers
busy / needs-me / actually-finished; "done" only after quiet past a settle window
(or an explicit terminal marker); bell once at settle, never per turn; turn rows
demoted to drill-in history.

## 4. Open items from this session (not this run's job, don't lose them)

- **Live QA of `1e31de5`** (operator-gated): next real AskUserQuestion in any pane →
  `forge board` shows question + options + hint; answer in pane → row clears within a
  tick.
- PANE-DONE ring flip (`FORGE_WATCH_NOTIFY_PANE_DONE=1` in watch.env) still pending —
  deliberately held; the episode-state plan will likely subsume it (§6 Q4).
- Codex `/hooks` trust review per root still pending (CODEX-EMISSION-OFF shows in
  maintenance); affects episode coverage for codex panes (§6 Q7).
- Watcher-hardening handoff §3 record-level gaps (close-out residue, per-row ack,
  TASK-STUCK dropped-vs-absorbed) remain open, unowned.

## 5. Hard context (unchanged, in force)

- Seat-operator interaction rules: operator constraints verbatim; NO outward action
  (dispatch, tmux, notification, osascript) without an explicit per-action go; reads
  are fine. Code fixes/implementation via pipeline procedure, never ad-hoc seat edits;
  no-auto-commit; bins in `~/bin` are symlinks into this repo — edits are LIVE.
- forge-watch READ-ONLY; additive-only schemas; bell discipline; worker events never
  mark the session done; no pane scraping; deterministic (no model in the watcher).
- Memory `pane-episode-state-status.md` + `askuserquestion-visibility-status.md` are
  current through this handoff.
