# Review - Cross-Worktree Infra Lock Plan

**Reviewed plan:** `handoffs/handoff-2026-06-29-infra-lock-plan.md`  
**Date:** 2026-06-29  
**Reviewer:** Codex  
**Status:** Needs revision before implementation

## Summary

The overall direction is sound: a single lock anchored at Git's common dir is
the right v1 shape for serializing fixed-port/live-DB stages across worktrees.
The plan correctly keeps the reasoning stages parallel and puts the mutex in
the orchestrator path before worker dispatch.

Do not implement the plan exactly as written yet. The main gaps are lifecycle
gaps: the plan releases on `STALLED`, `PROMPTING`, and `TIMEOUT`, does not
cover dispatch-failure leaks, and misses the local QA fallback path. Those gaps
can either break mutual exclusion or leave a live-session holder wedged until a
manual force release.

## Assumption Check

### Verified

- **Git common-dir anchor works on this host.** `git version 2.50.1
  (Apple Git-155)` supports `git rev-parse --path-format=absolute
  --git-common-dir`. In a throwaway repo plus linked worktree under `/tmp`, both
  the main worktree and linked worktree resolved to the same common dir:
  `/private/tmp/forge-lock-review-repo/.git`.
- **Python `fcntl.flock` is available.** `python3 -c 'import fcntl;
  print(hasattr(fcntl, "flock"))'` returned `True`. The host also has
  `/opt/homebrew/bin/flock`, but the plan's Python `fcntl.flock` approach does
  not depend on the CLI.
- **The no-false-stall premise is supported.** `cmd_dispatch` creates the
  pending log entry only after preflight checks and before `cmd_send`
  (`bin/forge-bridge:1814-1908`). `cmd_stall_check` emits `IDLE`, not
  `STALLED`, when there is no pending dispatch (`bin/forge-bridge:2823-2831`);
  `STALLED` requires pending plus elapsed unchanged snapshot
  (`bin/forge-bridge:2881-2896`).
- **QA and verify are definitely infra-touching.** `adversarial-qa` requires a
  running backend/frontend and may start them from project config
  (`skills/adversarial-qa/SKILL.md:29-37`, `:523-524`). `adversarial-verify`
  requires a running backend/frontend (`skills/adversarial-verify/SKILL.md:13-18`).
- **Coding can be infra-touching.** `forge-coder` runs tests after each commit
  group and full validation (`skills/forge-coder/SKILL.md:162-168`,
  `:204-224`), and its hard constraints explicitly mention tests that may
  auto-start services (`skills/forge-coder/SKILL.md:11-18`).
- **Fixed local ports are real in current Forge projects.** The promptlol config
  uses backend port `8001` and frontend port `5180`
  (`/Users/sirdrafton/sirtheoracle/automation/promptlol/.claude/forge-project.yml:22-33`).
  Other sampled configs also use fixed local backend/frontend ports.
- **The shared DB assumption is real for at least promptlol.** Promptlol's
  default local DB URL is `localhost:5432/shield_platform`
  (`/Users/sirdrafton/sirtheoracle/automation/promptlol/backend/src/app/core/config.py:17-20`).
- **The pgvector claim is verified for FeedForge, not promptlol.** FeedForge
  documents PostgreSQL 17 via `pgvector/pgvector:pg17` and a shared `pgdata`
  volume (`/Users/sirdrafton/sirtheoracle/automation/feedforge/docs/technical-reference.md:12`,
  `:28-39`). I did not find direct pgvector evidence for promptlol in the
  checked paths.

### Partially Verified

- **`qa-fix` is reasonable to lock but the plan overstates the evidence.**
  The `qa-fix` prompt requires resolving QA findings and reporting changed
  files, but it does not explicitly require running live tests
  (`/Users/sirdrafton/.config/forge/prompts/qa-fix.txt:5-24`). Locking it is
  still conservative and probably right because it is part of an infra-heavy
  QA loop, but the plan should say this is a conservative lock, not fully
  verified from the prompt.
- **The cited `forge-global-edit-protocol` memory was not present.**
  `/Users/sirdrafton/.codex/memories` only contains
  `headless_factory_db_context.md`. Earlier handoffs document the same
  backup -> edit toolkit copy -> mirror to `~/bin` -> no auto-commit protocol,
  so the procedure is still supported, but the plan should not depend on a
  missing memory by name.

## Findings

### High - Releasing on every `wait` outcome can break mutual exclusion

The plan says to release after every `wait` result, including
`STALLED`, `PROMPTING`, `DEAD`, and `TIMEOUT`
(`handoff-2026-06-29-infra-lock-plan.md:189-198`). Current `cmd_wait` returns
for `STALLED`, `PROMPTING`, and `DEAD` as soon as the stall classifier reports
them (`bin/forge-bridge:2100-2110`), and returns `TIMEOUT` when the wait
ceiling is hit (`bin/forge-bridge:2113-2121`).

Those are not all safe release points:

- `PROMPTING` means a tool approval prompt is visible; approving it can resume
  an infra-touching command. Releasing before approval lets another worktree
  enter the shared stack.
- `STALLED` is a classifier verdict, not proof that the process stopped. It may
  still be inside a long-running command with no output.
- `TIMEOUT` says the orchestrator stopped waiting, not that the worker stopped.
- `BLOCKED` also deserves care: the worker is paused, but the orchestrator may
  run follow-up repair commands that touch the same stack before sending the
  worker a continuation.

Recommended revision:

- Define lock release around stage terminality, not around one `wait` call.
- Release automatically on `DONE` and `ERROR` callbacks.
- Keep the lock on `PROMPTING`, `STALLED`, `TIMEOUT`, and usually `BLOCKED`
  until the stage resumes to terminal completion or the orchestrator explicitly
  aborts/kills/parks it.
- For `DEAD`, release only after acknowledging that child processes may still
  be holding ports; at minimum surface that risk before release.
- Update Hard Rule 23 to say the lock may intentionally remain held while an
  infra stage is prompting, stalled, timed out, or being unblocked.

### High - Dispatch failure after acquire can leak the lock

The integration pseudocode acquires, dispatches, waits, then releases
(`handoff-2026-06-29-infra-lock-plan.md:189-193`). It does not specify what
happens if `dispatch` fails before `wait` is ever called.

That can happen in real code: the dispatch guard can reject open pendings
(`bin/forge-bridge:1814-1859`), tmux session validation can fail
(`bin/forge-bridge:1862-1865`), and `cmd_send` can fail after the prompt file is
written (`bin/forge-bridge:1900-1904`). If any of those happen after the lock
is acquired, the holder remains until another worktree times out or the user
force-releases it.

Recommended revision:

- Add an explicit "dispatch failed after acquire" branch that immediately
  releases the lock and surfaces the dispatch error.
- Add a test where acquire succeeds and dispatch fails due an existing pending
  log entry; expected result is lock released.
- Consider an orchestration helper or shell wrapper only if it can preserve the
  non-terminal-status behavior above. A blanket `trap release EXIT` is unsafe
  if it releases while a worker is still running.

### High - Local QA and QA retry fallback are not covered

The plan's wrapper is expressed as `acquire -> dispatch -> wait -> release`
(`handoff-2026-06-29-infra-lock-plan.md:189-193`). But the orchestrator has a
local QA fallback: if codex-b is unavailable, QA runs inline in pane 1 via
Agent Teams (`skills/forge-orchestrator/SKILL.md:636-644`). QA retry uses the
same worker preference and local fallback (`skills/forge-orchestrator/SKILL.md:735-736`).

That local path is one of the most infra-heavy paths: `adversarial-qa` requires
running backend/frontend services and may start them (`skills/adversarial-qa/SKILL.md:29-37`,
`:523-524`). If only dispatched stages are wrapped, concurrent worktrees can
still collide whenever QA or QA retry falls back to local execution.

Recommended revision:

- Add a second integration shape for local infra stages:
  `infra-lock acquire -> run local adversarial-qa -> digest -> release`.
- Apply it to local `qa` and local `qa-retry`.
- Document how local callback/log-response and digest handling interact with
  the lock, since there is no `wait` outcome to key off.

### High - Same-session different-slug auto-steal is unsafe

The acquire branch says that if the holder session equals the current session
but the slug differs, the new caller should steal the lock as "stale, recycled
name" (`handoff-2026-06-29-infra-lock-plan.md:135-143`).

That is unsafe. A live Forge session can still have a real infra stage for slug
A while a mistaken or resumed orchestrator tries to start slug B in the same
session. Auto-stealing would allow slug B into the shared stack while slug A's
worker may still be running.

Recommended revision:

- Do not treat same-session/different-slug as automatically stale.
- Store a tmux session id or creation marker in the holder, not only the session
  name. A recreated `forge-N` can then be distinguished from the same live
  session.
- Only auto-steal when the recorded session name is dead, or when the recorded
  session id no longer matches a live session with that name.
- For same live session plus different slug, surface a conflict and require
  explicit operator action.

### Medium - Same-slug reentrancy ignores stage and can hide stale holders

The plan treats any same `(session, slug)` holder as `ALREADY_HELD`
(`handoff-2026-06-29-infra-lock-plan.md:138-140`), and `release` only checks
`(session, slug)` (`handoff-2026-06-29-infra-lock-plan.md:119-123`).

That is resume-safe, but if a prior infra stage leaked its holder and the same
slug advances to another infra stage, the acquire can silently succeed while
the sidecar still reports the old stage. Mutual exclusion still holds, but
status and recovery become misleading.

Recommended revision:

- Make reentrancy stage-aware:
  - same session, slug, and stage -> `ALREADY_HELD`
  - same session and slug but different stage -> update the holder's `stage`
    and `acquired_at`, and emit a distinct `LOCK action=stage_update`
- Alternatively include a lock token returned by acquire and require release to
  match that token, but that is a larger change.

### Medium - "Crash-safe by construction" is overclaimed

The plan says a dead holder is stolen by the next waiter and "never leaked into
a wedge" (`handoff-2026-06-29-infra-lock-plan.md:159-180`). That is true for a
dead tmux session on the same host. It is not true for a live session whose
orchestrator stopped after acquiring the lock, a live session abandoned at
`PROMPTING` or `STALLED`, or a foreign-host holder.

Recommended revision:

- Reword this as "dead-session recovery" rather than full crash safety.
- Add recovery docs for live-session abandoned holders:
  - inspect `infra-lock status`
  - inspect the holder session/worktree if available
  - resume the same slug/stage if appropriate
  - force-release only after confirming the stage is stopped or intentionally
    aborting it

### Medium - Status should report stale/dead holders, not just held/free

The plan's `status` command prints only `HELD ...` or `FREE`
(`handoff-2026-06-29-infra-lock-plan.md:125-127`). Since stale cleanup only
happens on acquire, `/forge status` can show a dead holder as simply held.

Recommended revision:

- Keep `status` read-only, but let it evaluate local liveness:
  - `FREE`
  - `HELD live by ...`
  - `STALE dead-session holder by ...`
  - `HELD foreign-host by ...`
- Surface the exact force-release command only in the stale/timeout guidance,
  with a warning that force release can create collisions if the holder is
  actually still running.

### Medium - The proposed test harness will not prove live-holder blocking

The verification section proposes simulating two worktrees with only
`TMUX_SESSION=forge-A` and `TMUX_SESSION=forge-B`
(`handoff-2026-06-29-infra-lock-plan.md:230-239`). But the acquire logic uses
`tmux has-session` for liveness. If the test does not create real tmux
sessions or mock `tmux_has_session`, holder A will look dead and B will steal
instead of block.

Recommended revision:

- For tests that expect a live holder, create temporary tmux sessions
  `forge-lock-test-A` and `forge-lock-test-B`, or inject a fake tmux liveness
  function.
- Add test cases for:
  - dispatch failure after acquire releases the lock
  - local QA fallback acquires/releases the lock
  - `PROMPTING`/`STALLED`/`TIMEOUT` keep the lock held
  - same live session with different slug does not auto-steal
  - same slug different stage updates or rejects stale stage state
  - corrupt `infra.holder` produces a safe error/escalation, not a traceback

### Low - Event whitelist details should be clarified

The plan says to add `LOCK` to the emit whitelist in three spots
(`handoff-2026-06-29-infra-lock-plan.md:212-214`). Direct calls from
`cmd_infra_lock` can call `_emit_event` without going through `cmd_emit`, so
the whitelist matters only if the orchestrator or tests will call
`forge-bridge emit LOCK ...`.

Recommended revision:

- Clarify whether `LOCK` events are emitted only internally by
  `cmd_infra_lock`, or whether `forge-bridge emit LOCK` is a supported public
  event.
- If public, update both the `cmd_emit` whitelist and help/docs.

## Recommended Plan Changes

1. Revise the lock lifecycle policy before implementation:
   - acquire before any infra stage work
   - release on terminal completion/error
   - keep held on prompting/stalled/timeout/unblocked-in-progress states
   - release immediately on dispatch failure before worker start
2. Add explicit local fallback coverage for `qa` and `qa-retry`.
3. Replace same-session/different-slug auto-steal with session-id-aware stale
   detection or explicit conflict escalation.
4. Make same-slug reentrancy stage-aware, or update the sidecar when the stage
   changes.
5. Downgrade "crash-safe by construction" to the narrower claim actually
   supported by `tmux has-session`: dead-session recovery.
6. Strengthen the test plan with real tmux sessions or a liveness mock, plus
   non-terminal wait outcome tests.
7. Update the assumption language:
   - fixed local ports and shared DB are verified in sampled project configs
   - pgvector is verified for FeedForge, not all projects
   - `qa-fix` locking is conservative rather than directly proven from its
     prompt
   - the named memory file is unavailable, though the same edit protocol exists
     in prior handoffs

## Bottom Line

Implementing a git-common-dir infra mutex is the right v1 direction. The plan
needs a tighter lifecycle model before coding; otherwise it can either release
while an infra worker is still active or leak a holder on pre-wait failures.
