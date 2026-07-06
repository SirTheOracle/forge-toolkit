# Handoff: Operator task visibility — exploration done, write problem statement next

**Date:** 2026-07-05
**From:** seat session (return-path QA + first real-workflow shakedown)
**Task for NEXT session:** write `.dev/proposals/operator-task-visibility/problem-statement.md`
in the forge-toolkit from this handoff's material, get operator review, then run the
adversarial-proposal flow on it. **Operator verdict: all three explored directions are
viable — the problem statement must carry all of them, not pre-pick.**

## 0. HARD CONTEXT — read before doing anything

- **ALL project work is frozen** by the operator until this visibility problem is fixed and
  "has some type of value." Do not touch GoParent or any other initiative.
  (goparent-ai has a finished, unreviewed `final-plan.md` under
  `.dev/proposals/prep-strategic-profile-split/` — it waits.)
- **Do not message, dispatch to, or touch any tmux session or pane.** The operator works
  the panes themselves right now. No `forge dispatch`, no tmux commands, no osascript
  notifications, nothing outward — without an explicit per-action go from the operator.
- **Carry operator constraints verbatim** into anything you write or send; if you add an
  interpretation, flag it as yours. This session paraphrased a routing constraint
  ("using the claude-opus panel and not locally") into a wrong dispatch and the operator
  could not see the deviation. That failure is part of why trust broke.
- After a stop order, stop **everything** — including "harmless" diagnostics. A rejected
  notification test right after a stop order made things worse.

## 1. The problem, in the operator's words (verbatim — use these in the doc)

- "I don't see how this really adds value if I have to go look myself at every single task
  to see where it is. That's a real hole right now in this whole new layer."
- "The FORGE framework depends on the orchestrator being able to coordinate work across
  all of the phases. To only account for one pane makes no sense at all when all of them
  are coordinating."
- "Unless there are instructions for panel one to always know that there is a possible
  overseer … there needs to be an overseer skill that can more manage this, possibly.
  Or if not, this needs to be turned into a real app-like service. App as in script that's
  running in some form of CLI that's always monitoring and not having to do this
  half-batch process."
- "There will be no working on any projects until this is working and has some type of value."

## 2. Evidence — 2026-07-05 shakedown timeline (all verifiable on disk)

1. 14:04Z seat dispatched an investigation task to forge-3 (`cc-20260705T140429Z-58015b`).
   It completed at 14:10Z — **silently**. Operator had to poll the seat twice ("check
   forge-3") to learn it finished. SESSION-DONE is board-only by default.
2. `FORGE_WATCH_NOTIFY_DONE=1` + `FORGE_WATCH_NOTIFY_REPLY=1` were added to
   `~/.config/forge/watch.env` (both live). Stale done-rows acked to avoid a backlog burst.
3. 14:17Z seat dispatched the adversarial run with a **mis-relayed** routing constraint;
   operator intervened manually and ran the task **in pane 0** of forge-3 themselves.
4. The full 4-round adversarial run executed in pane 0, 14:34–14:59Z, producing
   proposal-A/B/C, reviews, `final-plan.md` — **zero signals of any kind**. Cause:
   `bin/forge-cc-hook` role gate (only `pane_index == '1'` emits; workers exit silently —
   `bin/forge-cc-hook:44-47`). Codex panes (2/3) have no hooks at all.
5. Pane 1's own unrelated turn ended 15:01Z; the new done-bell **did** deliver twice
   (15:02Z + 15:16Z re-ring; `~/.cache/forge-watch/state.json` shows count=2) — **operator
   saw neither**. Delivery is osascript attributed to Script Editor: unverifiable,
   silently no-ops without notification permission / under Focus. A live delivery test was
   proposed and REFUSED (stop order was in effect); delivery remains UNVERIFIED on this Mac.
6. Operator verdict: not a practical workflow; too many places a response is left hanging.

## 3. Exploration result — three directions, decomposed as LAYERS (all viable per operator)

**Layer 1 — Emission (signals must exist for all coordinating panes).**
Today: worker panes emit nothing (protects session lifecycle, but discards the signal
entirely). Additive concept: every pane emits role/pane-tagged events; consumers decide
meaning; session lifecycle stays pane-1-derived. Codex ad-hoc work is the hard residue
(pipeline codex work already signals via bridge callbacks).
**Direction A (operator's "overseer-aware pane 1") lives here:** a standing contract in
the orchestrator skill AND worker-pane skills — "you are overseen; register every task you
receive (dispatched or typed), milestone at boundaries, signal at end." Only behavioral
best-effort — the piece a daemon cannot provide (milestones), but never the foundation.

**Layer 2 — State (task-shaped ledger, always-on).**
Machine tracks sessions/turns; operator thinks in tasks. `dispatch_id` already has a
derivable lifecycle (dispatched → accepted → answered via prompt/stop events +
`response_dispatch_ids`), but there is no `forge tasks` view, and pane-typed work has no
identity. **Direction C (operator's "real app-like service"):** persistent watcher
(FSEvents/fswatch on `.dev/attention` + `.dev/forge-tmp` across roots), durable task
ledger, event-driven — replaces the 30s launchd batch scan ("half-batch process").
Deterministic, zero model usage, its own death detectable.

**Layer 3 — Delivery + judgment.**
(a) Replace the osascript/Script Editor channel with something verifiable (real notifier /
menubar / phone push / dashboard — operator preference NOT yet asked; open question).
(b) **Direction B (overseer skill):** an agent that triages ("this done needs eyes vs not"),
chases hanging responses, phrases meaningful messages ("final-plan.md ready at <path>",
not "session done"). MUST be event-triggered by the deterministic layer, never a polling
LLM loop — this toolkit's own history (multiple silent agent deaths) argues against
model-as-monitor.

**Seat's landing (operator has NOT ratified — present as input, not decision):**
C is the spine, A is the contract, B is an optional brain on top; delivery replacement is
day-one regardless.

## 4. Constraints carried from prior phases (inviolable unless the adversarial run overturns)

- A worker pane's Stop must never mark the session done (Phase A hard rule) — fix by
  namespacing, not by re-gating.
- forge-watch stays READ-ONLY; `cc-*/1` schemas additive-only; bell discipline (rows are
  cheap, rings are deliberate); no pane scraping — files are the transport.
- Single subscription; usage-sensitivity is a real operator constraint (it drove the
  "claude-opus panel, not locally" instruction).

## 5. Open questions for the problem statement

1. Operator's preferred delivery surface(s): Mac notification / menubar / phone / dashboard?
2. Task identity for pane-typed work: self-registration via overseer-aware skills, an
   explicit `forge task` verb, or out-of-ledger by declaration?
3. What is the operator's minimum "value bar" — the checkable definition of "I never have
   to go look"? (Propose: every task reaches a terminal signal the operator provably
   receives, or the failure to do so is itself surfaced.)
4. Scope of codex-pane coverage for ad-hoc (non-pipeline) work.
5. Does the overseer (B) ship in v1 at all, or after C+A prove out?

## 6. Session-state footnotes

- Return path (reply / --wait / NEEDS-REPLY) shipped and live QA'd 2026-07-04 — 4 commits
  `aeb1b9c`..`2415505`; suites 91/121/31/22/63 green; coder-report in
  `.dev/proposals/response-return-path/`. This handoff's problem is the NEXT hole, found
  by the first real-workflow shakedown on top of that work.
- `watch.env` now has NOTIFY_REPLY=1 and NOTIFY_DONE=1 (operator-chosen).
- Untracked in this repo: `handoffs/handoff-2026-07-04-*` (3 files) + this file.
