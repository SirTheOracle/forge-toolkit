# Review R4 - Callback Lifecycle Follow-Up

**Reviewed plan:** `handoffs/handoff-2026-06-29-infra-lock-plan.md`  
**Reviewed revision:** R3, updated 2026-06-29  
**Prior review:** `handoffs/handoff-2026-06-29-infra-lock-plan-review-r3.md`  
**Date:** 2026-06-29  
**Reviewer:** Codex  
**Status:** Architecture is stable, but callback lifecycle needs one more contract change before implementation

## Summary

R3 correctly drops the `attempt_ts` / `wait --after` design. Archive-based
callback consumption is the right direction because it avoids timestamp races
and works with filesystem state.

The remaining issue is where consumption happens. The current R3 text says
`cmd_wait` archives the non-terminal `BLOCKED` callback before returning it.
That is too early: if the orchestrator crashes, compacts, or pauses after
seeing `BLOCKED` but before sending the continuation, resume sees an open
pending entry and no canonical callback, then waits forever for a callback the
blocked worker will not send. Consumption should be tied to "continuation was
successfully sent," not to "wait read the callback."

## Direct Answers For The Callback Lifecycle Question

1. **Is archive-on-consume better than `wait --after`?** Yes. It removes the
   same-second timestamp race and does not require local orchestrator state.
2. **Should `cmd_wait` archive `BLOCKED` before returning?** No. That loses the
   durable "needs continuation" signal before the continuation is committed.
3. **Should `BLOCKED` keep the dispatch pending open?** Yes. That part of R3 is
   right. It preserves the in-flight stage for stall-check/status/resume.
4. **Should terminal callbacks keep current behavior?** Not exactly. Current
   behavior publishes the callback file before `log-response` closes the pending
   entry, which can race `wait` and next-stage dispatch. Terminal callbacks
   should become visible only after the terminal log close succeeds.
5. **Is `add-note` the right durable store for the blocked message?** It can be a
   best-effort convenience, but the callback file/archive and pending log entry
   should be the source of truth.

## Verified Code Facts

- Current `cmd_callback` writes the canonical callback file first
  (`bin/forge-bridge:1987-1999`) and only then calls `cmd_log_response`
  (`bin/forge-bridge:2001-2007`).
- Current `cmd_callback` suppresses `cmd_log_response` failure with `|| true`
  (`bin/forge-bridge:2006-2007`).
- Current `cmd_wait` returns immediately when the canonical callback file exists
  (`bin/forge-bridge:2092-2097`).
- Current `_wait_emit_callback` reads and emits the callback but does not consume
  it (`bin/forge-bridge:2129-2163`).
- Current `cmd_stall_check` emits `IDLE` when no pending dispatch exists
  (`bin/forge-bridge:2823-2831`), and emits `COMPLETED-PENDING-LOG-RESPONSE`
  for an idle worker with an open pending entry (`bin/forge-bridge:2870-2879`).
- Current `cmd_add_note` requires an existing context file and interpolates the
  note into Python source (`bin/forge-bridge:2512-2544`), so it should not be a
  critical path for arbitrary callback messages.

## Findings

### High - Auto-archiving inside `wait` creates a resume deadlock before continuation

R3 says that when `cmd_wait` consumes a non-terminal `BLOCKED` callback, it moves
the file out of the canonical callback path before returning
(`handoff-2026-06-29-infra-lock-plan.md:397-403`). The Shape A flow then runs
repair, sends a continuation, and waits again
(`handoff-2026-06-29-infra-lock-plan.md:376-381`).

This order has a failure window:

1. Worker writes `BLOCKED`.
2. `cmd_wait` reads it and archives it.
3. `cmd_wait` returns `STATUS: BLOCKED`.
4. The orchestrator crashes, compacts, pauses, or gets interrupted before repair
   and before `send --force continuation`.
5. Resume sees an open `response: null` pending entry, but the canonical
   callback path is empty.
6. Resume re-enters `wait`, but the worker is already blocked and will not write
   a fresh callback until it receives a continuation.

That is a durable deadlock created by consuming the signal before the
continuation is committed.

Recommended revision:

- Split "read callback" from "consume callback."
- `cmd_wait` should emit `STATUS: BLOCKED` but leave the canonical `BLOCKED`
  callback in place.
- After repair and after `send --force continuation` succeeds, the orchestrator
  should call a small consume/archive operation, for example:

  ```bash
  ~/bin/forge-bridge callback-consume --slug S --stage T --status BLOCKED
  ```

- Only after that consume succeeds should the orchestrator re-enter `wait` for
  the fresh terminal callback.
- If the orchestrator resumes and still sees the canonical `BLOCKED`, it should
  surface the same blocked state again instead of silently waiting.

This preserves resume safety at every point:

- before continuation: canonical `BLOCKED` remains durable;
- after continuation: stale `BLOCKED` is archived and re-wait blocks for the
  worker's next callback;
- after terminal callback: terminal path closes the pending and releases the
  lock.

### High - Terminal callback publication can race pending-log closure

R3 says terminal `DONE`/`ERROR` callbacks are left in place with current behavior
unchanged (`handoff-2026-06-29-infra-lock-plan.md:397-403`) and that terminal
callbacks close the pending (`handoff-2026-06-29-infra-lock-plan.md:404-415`).

Current behavior does not guarantee that ordering. `cmd_callback` writes the
canonical callback file first, then calls `cmd_log_response`. A parallel
`cmd_wait` can observe `STATUS: DONE` before `cmd_log_response` has closed the
pending entry. The orchestrator can then release the infra lock and try to
advance while the dispatch guard still sees `response: null`.

Recommended revision:

- For terminal `DONE`/`ERROR`, close the pending log entry before publishing the
  canonical callback file.
- Stop suppressing terminal `cmd_log_response` failure. If terminal log close
  fails, do not publish a successful terminal callback.
- Publish callback files atomically: write a complete temp file, then `mv` into
  the canonical path only after prerequisite side effects have succeeded.
- Add a test where `cmd_wait` polls aggressively while `cmd_callback DONE` is
  running; `wait` must not return `DONE` until the pending log entry is closed.

### Medium - `add-note` should be best-effort, not the non-terminal source of truth

R3 says `cmd_callback` should record a `BLOCKED` message via `add-note`
(`handoff-2026-06-29-infra-lock-plan.md:404-410`,
`handoff-2026-06-29-infra-lock-plan.md:500-505`). The idea is useful, but
`cmd_add_note` is not currently robust enough to be a critical lifecycle step:
it requires an existing context file and interpolates the note directly into
Python source.

Recommended revision:

- Treat `add-note` as best-effort observability only.
- Keep the full blocked message in the canonical callback while blocked, then
  in the archived callback after continuation is sent.
- If `add-note` is used from `cmd_callback`, write it through a YAML-safe path
  or pass the note as an argument/environment value to Python rather than
  interpolating shell text into Python source.
- Do not let `add-note` failure prevent a `BLOCKED` callback from being written.

### Medium - Archive naming and atomicity need to be specified

R3 proposes archiving to
`.../callbacks/archive/{slug}-{stage}.{n}.callback`
(`handoff-2026-06-29-infra-lock-plan.md:397-400`). That is directionally fine,
but the implementation contract should avoid racy or ambiguous archive names.

Recommended revision:

- Create the archive directory with `mkdir -p`.
- Use a unique filename such as `{slug}-{stage}.{timestamp}.{pid}.{random}.callback`
  or `{slug}-{stage}.{callback_id}.callback`.
- Preserve the original callback contents exactly.
- Move with atomic filesystem rename from the same filesystem.
- If no canonical `BLOCKED` callback exists when `callback-consume` runs, return
  a structured no-op/error that tells the orchestrator not to re-wait blindly.

### Medium - Verification should cover resume before continuation, not only after continuation

The R3 verification matrix includes resume after `send --force`
(`handoff-2026-06-29-infra-lock-plan.md:576-579`). The more important failure
window is before `send --force`, because that is where the current R3
archive-inside-wait design loses the only durable blocked signal.

Add verification cases:

- `BLOCKED -> wait returns -> orchestrator restarts before repair/continuation`:
  resume must see/surface the same blocked callback, not block forever.
- `BLOCKED -> repair -> send continuation succeeds -> consume/archive -> resume`:
  resume must wait for the worker's next callback and not re-read stale
  `BLOCKED`.
- `BLOCKED -> repair -> send continuation fails`: canonical `BLOCKED` must remain
  visible for retry/recovery.
- `DONE` publication race: `wait` must not return terminal before the pending log
  entry is closed.

## Recommended Callback Contract

Use this lifecycle instead of archive-inside-`wait`:

```text
cmd_callback BLOCKED:
  write complete BLOCKED callback atomically to canonical path
  do not log-response; keep pending open
  optionally add-note best-effort

cmd_wait:
  if canonical callback exists:
    emit its status/message
    do not archive it

orchestrator on BLOCKED:
  read message
  run repair while lock remains held
  send --force continuation
  if send succeeds:
    callback-consume --slug S --stage T --status BLOCKED
    wait again for fresh callback
  if send fails or orchestrator stops before send:
    canonical BLOCKED remains visible for resume/retry

cmd_callback DONE|ERROR:
  close pending log entry first
  publish terminal callback atomically only after close succeeds

orchestrator on DONE|ERROR:
  release lock after wait returns terminal
```

This keeps the R3 intent but makes the lifecycle genuinely durable across the
two important resume boundaries: before continuation and after continuation.

## Verdict

The plan is close. The lock architecture, owner identity, stage-specific
restart policy, installed skill mirroring, and Shape B fixes are now coherent.
The callback lifecycle should be revised once more before coding so consumption
is continuation-committed rather than wait-read, and terminal callback
publication is ordered after terminal log closure.
