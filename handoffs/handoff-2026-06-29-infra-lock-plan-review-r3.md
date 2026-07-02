# Review R3 - Cross-Worktree Infra Lock Plan

**Reviewed plan:** `handoffs/handoff-2026-06-29-infra-lock-plan.md`  
**Reviewed revision:** R2, updated 2026-06-29  
**Prior review:** `handoffs/handoff-2026-06-29-infra-lock-plan-review-r2.md`  
**Date:** 2026-06-29  
**Reviewer:** Codex  
**Status:** Needs one more revision before implementation

## Summary

R2 resolves the biggest policy decisions from the prior review: terminality-based
release, host plus `session_created` ownership, local QA fallback as an explicit
Rule 22 exception, and restart-on-entry as the service-staleness answer. The
overall architecture is still the right v1 direction.

The plan is not implementation-ready yet. The remaining blockers are mostly
precision gaps introduced by the new R2 fixes:

- release ownership does not match the full owner identity R2 now requires;
- the proposed `attempt_ts`/`wait --after` mechanism is not actually safe with
  the bridge's current second-resolution timestamp and is not durable across
  resume unless a marker is persisted;
- `BLOCKED` callbacks currently auto-close the pending log entry, which breaks
  the non-terminal "hold and continue" model;
- restart-on-entry edits must reach the installed skill paths workers actually
  read, not just repo-local skill files;
- restart-on-entry is underspecified for `coding` and `qa-fix`, and may conflict
  with the current forge-coder hard constraint.

## Assumption And Evidence Check

### Verified

- **Git common-dir support is present.** This host has `git version 2.50.1
  (Apple Git-155)`, and `git rev-parse --path-format=absolute --git-common-dir`
  resolves the toolkit common dir as an absolute path. The prior review also
  verified this across a throwaway linked worktree.
- **The tmux identity fields R2 depends on are available.**
  `tmux list-sessions -F '#{session_name} #{session_id} #{session_created}'`
  printed live sessions such as `forge-1 $43 1782139047`, and
  `tmux display-message -p '#{pid}'` returned `6282`.
- **The callback stale-read issue is real.** Current `cmd_wait` returns as soon
  as `.dev/forge-tmp/callbacks/{slug}-{stage}.callback` exists
  (`bin/forge-bridge:2092-2097`), and `_wait_emit_callback` reads it without
  deleting, renaming, or marking it consumed (`bin/forge-bridge:2129-2163`).
- **The current timestamp is not monotonic or high resolution.** `timestamp()`
  is `date -u +"%Y-%m-%dT%H:%M:%SZ"` (`bin/forge-bridge:256-258`). That is
  second-resolution wall-clock time.
- **`cmd_callback` currently closes the log for all statuses, including
  `BLOCKED`.** It writes the callback file with `timestamp: $ts`
  (`bin/forge-bridge:1987-1999`) and then calls `cmd_log_response` for
  `FORGE_${status}` unconditionally (`bin/forge-bridge:2001-2007`).
- **The local QA fallback and Rule 22 contradiction are real in current text.**
  QA Path B runs adversarial QA inline in pane 1 when codex-b is unavailable
  (`skills/forge-orchestrator/SKILL.md:636-644`), while current Hard Rule 22 says
  proposal is the only local stage and all other stage work is forbidden in pane
  1 (`skills/forge-orchestrator/SKILL.md:1048-1067`). R2 correctly plans to amend
  this.
- **The local-stage log-closure requirement is real.** Current Hard Rule 5 says
  every local `to: claude` stage must be logged and closed, and dispatch refuses
  the next stage while a pending entry remains
  (`skills/forge-orchestrator/SKILL.md:904-912`).
- **Runtime prompts point to installed skill paths.** `qa.txt` tells workers to
  follow `~/.codex/skills/adversarial-qa/SKILL.md`
  (`/Users/sirdrafton/.config/forge/prompts/qa.txt:7`), `qa-retry.txt` does the
  same, `verify.txt` points at `~/.claude/skills/adversarial-verify/SKILL.md`
  (`/Users/sirdrafton/.config/forge/prompts/verify.txt:7-8`), and `coding.txt`
  points at `~/.claude/skills/forge-coder/SKILL.md`
  (`/Users/sirdrafton/.config/forge/prompts/coding.txt:9`).
- **The installed QA skill is not byte-identical to the repo QA skill.**
  `shasum skills/adversarial-qa/SKILL.md
  /Users/sirdrafton/.codex/skills/adversarial-qa/SKILL.md` produced different
  hashes. The installed verify and forge-coder files currently match their repo
  copies, but runtime still reads the installed paths.
- **Service staleness remains real.** QA checks whether servers are already
  running (`skills/adversarial-qa/SKILL.md:193-207`) and starts missing services
  with background commands (`skills/adversarial-qa/SKILL.md:519-525`); cleanup
  shuts down Agent Teams teammates, not backend/frontend services
  (`skills/adversarial-qa/SKILL.md:358-365`). Verify requires a running
  application (`skills/adversarial-verify/SKILL.md:13-18`) and says to start
  backend/frontend if missing (`skills/adversarial-verify/SKILL.md:208-214`).
- **Forge-coder currently forbids direct service lifecycle management.** Its
  hard constraints say never start/stop long-running backend/frontend services
  directly (`skills/forge-coder/SKILL.md:11-18`).
- **Sampled infra assumptions remain accurate as narrowed.** Promptlol has fixed
  local ports `8001` and `5180` plus a default local Postgres URL
  (`/Users/sirdrafton/sirtheoracle/automation/promptlol/.claude/forge-project.yml:22-33`,
  `/Users/sirdrafton/sirtheoracle/automation/promptlol/backend/src/app/core/config.py:18-19`).
  FeedForge uses `pgvector/pgvector:pg17` and a shared `pgdata` volume
  (`/Users/sirdrafton/sirtheoracle/automation/feedforge/docker-compose.yml:3-11`).
- **`qa-fix` locking is still conservative, not directly proven by the prompt.**
  The `qa-fix` prompt asks for fixing QA findings and writing a report, but does
  not mandate live tests (`/Users/sirdrafton/.config/forge/prompts/qa-fix.txt:5-29`).

### Still Needs Verification Or A Tighter Contract

- **Restart-on-entry process selection.** R2 says to "kill whatever occupies the
  fixed ports." The implementation needs a safe, project-config-driven process
  selection rule and verification that it does not kill unrelated local services.
- **Stage-specific restart policy.** The plan says "each infra stage" restarts
  services, but `coding`, `qa-fix`, `qa`, `qa-retry`, and `verify` do not have
  identical service contracts.
- **Installed skill deployment.** The plan's edit list must say how repo skill
  changes are mirrored to the installed runtime paths, or explicitly change the
  prompts to read repo-local skills.
- **Resume behavior for `wait --after`.** If the orchestrator compacts or
  restarts after sending a continuation, a local variable `ts := now` is not
  enough to avoid re-reading the old `BLOCKED` callback.

## Findings

### High - Release ownership still omits part of the R2 owner key

R2 defines holder identity as `(host, session_name, session_id, session_created,
slug)` (`handoff-2026-06-29-infra-lock-plan.md:145-149`). Acquire uses both
`session_id` and `session_created` for same-session detection
(`handoff-2026-06-29-infra-lock-plan.md:235-247`).

But `release --slug S --stage T` removes the holder iff `(host, session_id,
slug)` is mine (`handoff-2026-06-29-infra-lock-plan.md:203-209`). That is a
contract mismatch. A stale holder from an earlier tmux server can have the same
host, same slug, and a reused `session_id` but a different `session_created`.
Release would then be allowed even though acquire would not consider that holder
the same owner.

Recommended revision:

- Make release match the same owner key used by acquire: at minimum
  `(host, session_id, session_created, slug)`, and preferably include
  `session_name` too for diagnostics and consistency.
- Keep `stage` out of the release match if stage-update behavior requires that,
  but state that explicitly as the only omitted field.
- Add a verification case: sidecar has same host, same slug, same session id,
  different `session_created`; ordinary release must no-op, while acquire should
  classify stale and steal under the guard.

### High - `attempt_ts` is not safe if it uses the current bridge timestamp

R2 says `cmd_callback` should stamp each callback with a monotonic `attempt_ts`
and that `cmd_wait --after <ts>` should ignore callbacks with `attempt_ts <= ts`
(`handoff-2026-06-29-infra-lock-plan.md:360-371`). It also says the bridge
"already writes a timestamp."

The current timestamp is second-resolution wall-clock time
(`bin/forge-bridge:256-258`). If the orchestrator records `ts := now`, sends
`send --force`, and the worker immediately writes the next callback in the same
second, the new callback can have `attempt_ts <= ts` and be ignored. That creates
the exact wedge the R2 change is meant to prevent.

There is also a resume gap. `ts := now` is local orchestrator state. If pane 1
compacts, crashes, or resumes after the continuation was sent, the next
orchestrator pass may not know the correct `--after` value and can re-read the
old `BLOCKED` callback.

Recommended revision:

- Do not base callback generation on the existing `timestamp()` value.
- Use a strictly unique callback generation token, such as an integer
  `attempt_id`, UUID, or nanosecond epoch plus collision handling. Compare a
  typed value, not a human timestamp string.
- Persist the "ignore callbacks up to generation X" marker somewhere durable
  before sending the continuation. Reasonable places: callback archive metadata,
  a stage-control file under `.dev/forge-tmp/`, or a pipeline-log note tied to
  the continuation.
- Add tests for same-second callback creation and resume-after-continuation.

### High - `BLOCKED` still auto-closes the pending log entry

R2 correctly changes the lock policy to hold through `BLOCKED`, repair, send a
continuation, and wait for the next callback
(`handoff-2026-06-29-infra-lock-plan.md:348-357`). But current `cmd_callback`
auto-calls `cmd_log_response` for every status, including `BLOCKED`
(`bin/forge-bridge:2001-2007`).

That means the pending dispatch entry is closed at the moment the non-terminal
`BLOCKED` callback arrives. After the orchestrator sends the continuation with
`send --force`, there is no pending `response: null` log entry for the continued
work. Current stall-check explicitly treats "no pending dispatch" as `IDLE`, not
`STALLED` (`bin/forge-bridge:2823-2831`). The status file and resume context can
therefore look idle even though an infra worker is still expected to continue
under a held lock.

This is a lifecycle mismatch, not just a display issue. The R2 plan depends on
non-terminal callbacks keeping the stage in a non-terminal state until a later
`DONE` or `ERROR` callback.

Recommended revision:

- Define how non-terminal callbacks interact with the pipeline log.
- Preferred: do not close the pending entry on `BLOCKED`; record the blocked
  message separately, keep the pending open, and close only on terminal
  `DONE`/`ERROR`.
- Alternative: close the first pending as `FORGE_BLOCKED`, immediately create a
  new pending continuation entry before `send --force`, and make `wait --after`
  and stall-check target that continuation.
- Update resume/status docs so a held infra lock plus non-terminal callback
  cannot appear as an idle pipeline.
- Add a verification case that after `BLOCKED -> continuation`, the log/status
  still exposes an in-flight stage and stall-check can still classify it.

### High - Restart-on-entry edits may not reach the worker runtime

R2 says D1 restart-on-entry lives in
`skills/adversarial-qa/SKILL.md`, `skills/adversarial-verify/SKILL.md`, and
possibly `skills/forge-coder/SKILL.md`
(`handoff-2026-06-29-infra-lock-plan.md:458-467`). It also says no worker prompt
changes are needed.

The runtime prompts do not tell workers to read those repo-local files. QA and
qa-retry read `~/.codex/skills/adversarial-qa/SKILL.md`; verify reads
`~/.claude/skills/adversarial-verify/SKILL.md`; coding reads
`~/.claude/skills/forge-coder/SKILL.md`. The installed QA skill is currently not
byte-identical to the repo QA skill.

If implementation edits only repo `skills/`, dispatched QA workers may continue
using the old installed QA instructions and skip restart-on-entry entirely.

Recommended revision:

- Add an explicit deployment step for skill edits:
  - edit repo skill;
  - mirror/sync to the installed path(s) workers actually read;
  - verify hashes or a targeted grep in both locations.
- Include `qa-retry.txt` in the prompt-path verification, since it also reads
  the installed QA skill.
- If the project intentionally wants repo-local skills to be authoritative,
  then update the worker prompts to point there. Otherwise keep the current
  prompts and mirror installed skill files as part of the edit protocol.
- Add an acceptance check that fails if restart-on-entry text appears only in
  repo files and not in the installed runtime files.

### High - Restart-on-entry conflicts with the current `forge-coder` contract

The plan locks `coding` and says D1 means each infra stage restarts services
against its own worktree before testing
(`handoff-2026-06-29-infra-lock-plan.md:75-79`,
`handoff-2026-06-29-infra-lock-plan.md:572-579`). The file list says this may
include `skills/forge-coder/SKILL.md`
(`handoff-2026-06-29-infra-lock-plan.md:458-463`).

Current forge-coder has a hard constraint: "Never start/stop long-running
services (backend, frontend) directly" (`skills/forge-coder/SKILL.md:11-18`).
That is a direct conflict if restart-on-entry is applied to `coding`.

`qa-fix` has a related ambiguity: it is locked conservatively as part of the QA
loop, but its prompt does not require live tests. If "each infra stage restarts"
is literal, `qa-fix` would also start killing/restarting services without a
clear need.

Recommended revision:

- Decide the stage-specific D1 policy:
  - QA, qa-retry, verify: restart-on-entry is required because the skills
    explicitly exercise a running app.
  - Coding: either keep the current forge-coder constraint and accept that tests
    may rely on their own webServer/autostart behavior, or amend the constraint
    explicitly and specify how coder restarts services safely.
  - qa-fix: either no restart by default, or restart only if the worker is about
    to run live/e2e checks.
- Update the guarantee wording. If coding is excluded from restart-on-entry, do
  not say "each infra stage tests its own worktree's code" without qualification.
- Add verification cases for the chosen coding and qa-fix policy.

### High - Shape B abort/failure path can still wedge the pipeline

R2 adds Shape B local logging:

```
acquire -> log -> inline QA -> log-response -> digest -> release
```

(`handoff-2026-06-29-infra-lock-plan.md:394-406`). That fixes the happy path,
but the failure path is still underspecified. If inline adversarial QA errors,
is interrupted, or aborts before `log-response`, then two things can remain:

- the infra lock, unless force-released; and
- the local pending log entry, which will make the next dispatch fail because
  the dispatch guard refuses open pendings.

The plan says "abort -> force-release" in the pseudocode, but force-releasing
the lock does not close the local log entry.

Recommended revision:

- Add an explicit Shape B failure branch:
  - on inline success: close local log with `FORGE_DONE`, release, then run or
    consume digest;
  - on inline failure/abort: close local log with `FORGE_ERROR` or
    `AGENT_FAILED`, release or force-release only after confirming no service
    process remains unsafe, then surface the failure.
- Make this branch part of verification: simulate inline QA failure after
  `log` and assert both the lock and pending log are cleaned up.

### Medium - Shape B holds the lock through digest work unnecessarily

For dispatched Shape A, the plan releases on terminal `DONE` or `ERROR` before
advancing. The digest prompt is rendered by `wait`, but the digest agent itself
is orchestrator-side follow-up work.

Shape B releases only after `forge-bridge digest` and "consume digest"
(`handoff-2026-06-29-infra-lock-plan.md:394-406`). Digest consumption should not
need the shared backend/frontend/DB lock. Holding the infra mutex while a digest
agent reads artifacts serializes non-infra work and can make lock waits look
longer than the actual infra stage.

Recommended revision:

- Release Shape B immediately after the local run has written artifacts and the
  local log entry has been closed.
- Run the digest after release, matching the effective Shape A lifecycle.
- If there is a reason digest must run under the lock, document the infra it
  touches and add verification for that claim.

### Medium - "Kill whatever occupies the fixed ports" is too broad

R2 resolves D1 by saying each infra stage should kill whatever occupies the
fixed ports, then start the current worktree's services
(`handoff-2026-06-29-infra-lock-plan.md:572-579`). That is directionally right
for the wrong-worktree-server problem, but unsafe as an implementation contract.

On a developer machine, a fixed project port might be occupied by a process that
Forge did not start, or by a service from a different repo that happens to use
the same port. The plan needs a safe process-selection rule before this can be
implemented.

Recommended revision:

- Use project config to identify expected backend/frontend ports and working
  directories.
- Prefer killing only processes whose cwd/command line matches the expected
  project service command or known dev-server shape.
- If the port is occupied by an unknown process, surface a structured
  escalation instead of silently killing it.
- Log exactly what process was stopped: pid, command, cwd if available, port,
  and owning worktree if inferable.
- Add tests for "port occupied by unknown process" and "port occupied by prior
  Forge worktree server."

### Medium - The verification matrix needs to cover the new R3 failure modes

R2's verification section is strong for the original lock behavior, but it does
not yet cover several claims added in R2.

Add cases for:

- release with same host/session_id/slug but different `session_created` must
  no-op;
- same-second callback after `send --force` must not be ignored by `--after`;
- resume after `BLOCKED -> continuation` must not re-read the stale callback;
- `BLOCKED` must leave a durable non-terminal stage state, either by keeping
  the pending entry open or by creating a continuation pending;
- Shape B inline failure after `log` must close the local log and release or
  force-release the lock;
- restart-on-entry text must be present in the installed runtime skill paths;
- coder and qa-fix must follow the explicit stage-specific restart policy;
- unknown process on a configured port must not be killed silently.

## Minor Notes

- Section 6 still says dead-session recovery is based on a holder whose tmux
  "session id" is no longer live (`handoff-2026-06-29-infra-lock-plan.md:276-279`).
  Since R2 now requires `session_created`, this wording should say "session
  identity" or "session id plus creation time."
- The header says "Plan is implementation-ready pending final go-ahead"
  (`handoff-2026-06-29-infra-lock-plan.md:5-9`). That should be downgraded until
  the blockers above are resolved.
- The R2 file list says repo and `~/bin` bridge copies are currently
  byte-identical (`handoff-2026-06-29-infra-lock-plan.md:433-436`). I did not
  re-verify `~/bin/forge-bridge` in this pass; keep that as an implementation
  preflight check.

## Recommended Acceptance Gate Before Coding

Before implementation starts, revise the plan so it has explicit contracts for:

1. full owner-key matching on release;
2. callback generation and durable `wait --after` semantics;
3. non-terminal `BLOCKED` log lifecycle;
4. installed skill mirroring for restart-on-entry;
5. stage-specific restart-on-entry behavior for `coding` and `qa-fix`;
6. Shape B failure cleanup;
7. safe port-process selection and escalation.

After those are in the plan, the remaining implementation should be bounded and
testable.
