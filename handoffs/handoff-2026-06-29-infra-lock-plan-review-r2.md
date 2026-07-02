# Review R2 - Cross-Worktree Infra Lock Plan

**Reviewed plan:** `handoffs/handoff-2026-06-29-infra-lock-plan.md`  
**Reviewed revision:** R1, updated 2026-06-29  
**Prior review:** `handoffs/handoff-2026-06-29-infra-lock-plan-review.md`  
**Date:** 2026-06-29  
**Reviewer:** Codex  
**Status:** Needs another revision before implementation

## Summary

R1 materially improves the plan. The lifecycle model is much better, the local
QA fallback is acknowledged, dead-session recovery is no longer overclaimed,
and the service-staleness limitation is correctly called out.

The plan is still not implementation-ready. The highest-risk issue is that the
new non-terminal hold loop does not work with today's callback-file semantics:
`forge-bridge wait` immediately re-reads the same callback file, so a
`BLOCKED -> repair -> send --force -> wait again` loop can return the stale
`BLOCKED` callback forever unless callback consumption or "wait for newer
callback" is added. There are also identity gaps around foreign hosts and tmux
server restarts, plus an unresolved contradiction between local QA fallback and
Hard Rule 22.

## Assumption And Evidence Check

### Verified

- **`#{session_id}` and `#{session_created}` are available.** On this host,
  `tmux list-sessions -F '#{session_name} #{session_id} #{session_created}'`
  printed live sessions with values such as `forge-1 $43 1782139047`. This
  supports storing a tmux session id, and it also shows a useful creation-time
  field for stronger identity.
- **The tmux server PID is available.** `tmux display-message -p -t forge-1
  '#{pid}'` returned a PID (`6282`). If the plan wants to distinguish tmux
  server restarts, this can be recorded too.
- **The event-whitelist correction is accurate.** `cmd_emit` still whitelists
  only `DISPATCH|WAIT|CALLBACK|DIGEST|STAGE|STALL|ERROR|COMPLETE|USAGE`
  (`bin/forge-bridge:2213-2265`). Internal `cmd_infra_lock` calls can call
  `_emit_event` directly without public `emit LOCK` support.
- **The service-staleness finding is real.** `adversarial-qa` checks whether
  servers are running (`skills/adversarial-qa/SKILL.md:198-206`), starts them
  in recovery with background commands (`skills/adversarial-qa/SKILL.md:519-525`),
  and cleanup only shuts down Agent Teams teammates, not backend/frontend
  services (`skills/adversarial-qa/SKILL.md:358-365`).
- **The local QA fallback exists in current orchestrator text.** QA Path B runs
  inline in pane 1 when codex-b is unavailable
  (`skills/forge-orchestrator/SKILL.md:636-644`), and qa-retry uses the same
  preference (`skills/forge-orchestrator/SKILL.md:735-736`).
- **The local QA fallback contradicts another current rule.** Hard Rule 22 says
  proposal is the only local stage and all other stage work is forbidden in
  pane 1 (`skills/forge-orchestrator/SKILL.md:1048-1067`). R1 relies on the
  local QA path but does not include resolving this contradiction.
- **The stale callback problem is in current bridge code.** `cmd_callback`
  writes `.dev/forge-tmp/callbacks/{slug}-{stage}.callback`
  (`bin/forge-bridge:1987-1999`). `cmd_wait` returns as soon as that file exists
  (`bin/forge-bridge:2092-2097`). `_wait_emit_callback` reads and prints the
  callback but does not remove or mark it consumed (`bin/forge-bridge:2129-2163`).

### Still Partially Verified Or Needs Care

- **Session id alone is not a durable process identity.** It distinguishes live
  sessions inside the current tmux server. It is not guaranteed to be globally
  unique across hosts, and it may be reused after a tmux server restart. I did
  not kill the tmux server to test restart reuse because that would disrupt
  active sessions.
- **The pgvector claim is still mixed.** R1 correctly narrows this to sampled
  configs and FeedForge. The problem section still says "one shared stack - a
  single Postgres + pgvector" globally (`handoff-2026-06-29-infra-lock-plan.md:53-61`).
  That should be softened to "Postgres, and pgvector in projects such as
  FeedForge" unless every target repo is checked.
- **`qa-fix` locking remains conservative.** R1 correctly says the prompt does
  not itself mandate live tests (`handoff-2026-06-29-infra-lock-plan.md:84-88`).
  That is an acceptable conservative lock, but tests and docs should keep that
  wording.

## Findings

### High - The non-terminal `BLOCKED` loop cannot work with current callback files

R1 says to hold the lock on `BLOCKED`, run repair, send a continuation, and
re-enter `wait` (`handoff-2026-06-29-infra-lock-plan.md:272-299`). That is the
right lock policy, but current bridge mechanics do not support it as written.

`cmd_wait` returns immediately whenever the callback file exists
(`bin/forge-bridge:2092-2097`). `_wait_emit_callback` reads the callback but
does not delete it, rename it, or record a consumed timestamp
(`bin/forge-bridge:2129-2163`). Therefore after a `BLOCKED` callback, a second
`wait --slug S --stage T` can immediately return the old `BLOCKED` callback
again before the worker has any chance to write a new callback.

This matters more after R1 because the lock can remain held while the
orchestrator loops. A stale callback loop can leave the infra lock held
indefinitely, block other worktrees, and make the recovery path look like it is
working when it is just reading old state.

Recommended revision:

- Add an explicit callback-consumption or callback-generation mechanism to the
  plan. Options:
  - `wait --since-callback-ts <ts>` or `wait --ignore-callback-ts <ts>`
  - after reading a non-terminal callback, move it to a consumed/archive path
    before sending the continuation
  - include a monotonic callback attempt id and require `wait` to wait for a
    newer id
- Update the scope line. This probably is a small change to `wait`/callback
  behavior, so the current "No change to existing dispatch/callback/stall-check
  logic" claim is no longer true.
- Add a verification case: stage returns `BLOCKED`, orchestrator sends
  `send --force`, then `wait` must block until a new callback rather than
  re-emitting the stale one.

### High - Holder ownership comparisons must include host, not just session id

R1 says foreign-host holders are live-not-me and should not be stolen
(`handoff-2026-06-29-infra-lock-plan.md:205-207`). But the branch logic first
checks only `h.session_id == my session_id`
(`handoff-2026-06-29-infra-lock-plan.md:179-188`), and release removes the
holder iff `(session_id, slug)` is mine
(`handoff-2026-06-29-infra-lock-plan.md:155-160`).

Tmux session ids are not globally unique. A foreign host can have `$43` too.
With the current pseudocode, a local session with the same session id and slug
could be treated as the same owner and release a foreign-host holder, directly
contradicting the foreign-host safety rule.

Recommended revision:

- Make the owner key at least `(host, session_id, slug)`, and preferably
  `(host, session_name, session_id, session_created, slug)`.
- In acquire, check `h.host == my_host and h.session_id == my_session_id`
  before any same-session branch.
- In release, require `host` as well as session identity and slug.
- Add a test where `infra.holder` has `host=foreign` and `session_id` matching
  a local live session id; acquire must wait/escalate and release must no-op.

### High - Session id alone does not handle tmux server restart reuse

R1 says keying on `#{session_id}` makes a recreated `forge-N` safe because a
new `forge-N` has a new id (`handoff-2026-06-29-infra-lock-plan.md:196-200`).
That is true for a new session within the same tmux server. It is not a durable
guarantee across tmux server restarts. Tmux session ids are server-local; after
the tmux server exits and restarts, ids can plausibly start again from low
values.

This creates a stale-holder failure mode: old holder says session id `$0`; tmux
server restarts; a new unrelated session gets `$0`; the lock sees the stale
holder as live and can return `ALREADY_HELD` or `CONFLICT` instead of stealing.

Recommended revision:

- Store and compare `session_created` in addition to `session_id`; it is
  available in current tmux output.
- Consider also storing the tmux server PID (`#{pid}`) for diagnostic output.
- Rename the liveness predicate from `tmux_session_id_live(id)` to something
  like `tmux_owner_live(host, session_name, session_id, session_created)` so the
  required match is explicit.
- Add a test with a sidecar whose session id exists but whose
  `session_created` differs; expected result is stale/dead-session, not held
  live.

### High - The plan relies on local QA fallback but does not resolve Hard Rule 22

Shape B is added for local `qa` and `qa-retry`
(`handoff-2026-06-29-infra-lock-plan.md:307-321`). That is necessary if the
local fallback remains. But the orchestrator's Hard Rule 22 currently says
proposal is the only local stage and all other stage work is forbidden in pane 1
(`skills/forge-orchestrator/SKILL.md:1048-1067`).

If the implementation only adds Hard Rule 23, the orchestrator will contain two
conflicting hard rules: one forbids local QA, the other requires locking around
local QA. This is not just documentation polish; it affects whether Shape B is
a valid execution path.

Recommended revision:

- Choose one policy:
  - remove local QA/qa-retry fallback and require codex-b/worker dispatch, or
  - keep local QA fallback and amend Hard Rule 22 to list it as a named
    exception alongside proposal.
- If keeping the local fallback, update the worker selection and Stage Details
  sections so pane-1 local QA is explicitly allowed only under the fallback
  condition and only under the infra lock.
- Add an acceptance grep/check for contradictions such as "ONLY stage that
  executes locally" and "qa local fallback".

### High - Service staleness is acknowledged but left optional, so v1 still may test the wrong worktree

R1 correctly identifies that services are not torn down and a left-running
server from worktree A can serve worktree B (`handoff-2026-06-29-infra-lock-plan.md:414-444`).
But the mitigation is described as optional and "tracked as a follow-up"
(`handoff-2026-06-29-infra-lock-plan.md:438-444`).

That means the v1 lock can satisfy "no concurrent infra stages" but still fail
the practical purpose of cross-worktree infra serialization: B's QA/verify may
run against A's backend/frontend code. It also weakens the repeated claim that
port collisions become impossible by construction (`handoff-2026-06-29-infra-lock-plan.md:96-98`):
the port may remain occupied by the prior holder's server, so the next holder
either reuses wrong code or must kill/restart it.

Recommended revision:

- Either make restart-on-entry part of this plan, or explicitly downgrade v1's
  guarantee to "serializes infra-stage execution but does not guarantee tests
  run against the current worktree's server code."
- If restart-on-entry is included, update files touched and scope. It likely
  affects `skills/adversarial-qa/SKILL.md`, `skills/adversarial-verify/SKILL.md`,
  possibly `skills/forge-coder/SKILL.md`, and/or orchestrator guidance.
- Add verification: start server from worktree A, acquire lock from worktree B,
  run B infra entry, and prove requests hit B's worktree code or that the plan
  explicitly accepts they may not.

### Medium - Shape B does not include required local-stage log closure

R1's local fallback shape says `acquire -> run adversarial-qa inline -> digest
-> release` and says `log-response` handling is unchanged
(`handoff-2026-06-29-infra-lock-plan.md:312-321`). Current Hard Rule 5 says
local work gets logged and closed too; every local `to: claude` stage must log
its response before the next dispatch (`skills/forge-orchestrator/SKILL.md:904-912`).

The current local QA fallback instructions are already thin here, but adding
the lock is an opportunity to make the local path precise. Without an explicit
local `log`/`log-response` sequence, context/status may be wrong and the next
dispatch can be blocked by an open pending entry or proceed without a proper
stage record.

Recommended revision:

- Expand Shape B to include the local stage log lifecycle:
  - create/log the local QA stage entry before the inline run
  - run inline QA while holding the lock
  - close with `log-response --to claude --stage qa|qa-retry`
  - then digest and release, or release after digest depending on whether the
    digest is considered part of the infra stage
- Add a test for local fallback context/log state, not just lock state.

### Medium - `DEAD` handling is underspecified and may block recovery

R1 says hold on `DEAD`, then "re-dispatch from scratch (re-acquire is
reentrant) or abort" (`handoff-2026-06-29-infra-lock-plan.md:298-299`). But
`DEAD` from stall-check means a pane is gone; `dispatch` still depends on a
valid tmux session and worker pane. In many cases the next required step is a
session/pane repair, not immediate re-dispatch.

Holding the lock through pane repair can be correct, but the plan should say
what happens if repairing the pane requires killing/restarting the tmux
session. If the session dies, another waiter can steal; if the session remains
but pane count is wrong, dispatch may fail and the holder can sit live but
unusable.

Recommended revision:

- Split `DEAD` into:
  - pane dead but session repairable: hold lock, repair pane/session, then
    re-dispatch
  - session will be killed/restarted: expect dead-session steal or explicit
    force release as part of abort
  - repair abandoned: force-release only after confirming no child service/test
    process is still running
- Add a test where `wait` returns `DEAD`, dispatch fails due pane/session
  validation, and the recovery path does not leak a live holder indefinitely.

### Medium - The timeout escalation lacks holder metadata needed for recovery

R1's acquire timeout prints `held_by`, `held_session`, and waited seconds
(`handoff-2026-06-29-infra-lock-plan.md:146-153`). With the new identity model,
operators need more data to decide whether force-release is safe.

Recommended revision:

- Include `host`, `session`, `session_id`, `session_created`, `slug`, `stage`,
  `acquired_at`, and liveness verdict in the timeout block.
- Include the worktree/project root if it can be captured safely. The current
  sidecar does not store it, but it would make "inspect that worktree/session"
  much easier.

### Medium - Test seam via `FORGE_TMUX_LIVENESS_CMD` should not become unsafe production eval

R1 proposes an injection seam such as `FORGE_TMUX_LIVENESS_CMD`
(`handoff-2026-06-29-infra-lock-plan.md:358-367`). That is useful for tests,
but the plan does not specify how it avoids arbitrary shell eval in production.

Recommended revision:

- Prefer real tmux sessions in the scratch harness.
- If an injection seam is kept, make it test-only and tightly scoped: for
  example, accept a path to an executable helper plus fixed argv rather than
  evaling an arbitrary env var string.
- Add a negative test that weird session ids or helper output cannot inject
  shell syntax into the lock script.

### Low - Some assertion wording still overstates universality

R1 narrows many assumptions, but a few broad phrases remain:

- "one shared stack - a single Postgres + pgvector" in the Problem section
  (`handoff-2026-06-29-infra-lock-plan.md:53-61`) is not verified for all
  sampled repos.
- "port collisions impossible by construction" (`handoff-2026-06-29-infra-lock-plan.md:96-98`)
  is only true for simultaneous start attempts, not for stale already-running
  services occupying fixed ports.
- "No change to existing dispatch/callback/stall-check logic"
  (`handoff-2026-06-29-infra-lock-plan.md:8-12`) conflicts with the likely need
  for callback generation/consumption to make non-terminal loops reliable.

Recommended revision:

- Soften the problem wording to "shared local services such as fixed-port
  backend/frontend and a shared Postgres; some projects use pgvector."
- Replace "port collisions impossible" with "concurrent port-bind races
  impossible."
- Update scope once the callback-loop design is chosen.

## Testing Gaps To Add

- `BLOCKED` callback consumption/new-callback wait: second `wait` must not read
  the stale callback.
- Foreign-host holder with colliding `session_id`: acquire waits/escalates and
  release no-ops.
- Tmux server restart or simulated session id reuse: matching id with different
  `session_created` is stale, not live.
- Hard Rule 22/23 consistency check for local QA fallback.
- Worktree A server left running, worktree B infra stage starts: prove B gets
  B's server, or document that v1 does not guarantee it.
- Local fallback logging/context check.
- `DEAD` pane/session repair path.
- Injection seam safety if `FORGE_TMUX_LIVENESS_CMD` is implemented.

## Bottom Line

R1 is a strong improvement but still has implementation blockers. The plan
should be revised once more around callback-file lifecycle, owner identity
including host and creation time, local QA rule consistency, and the service
staleness guarantee. After those are tightened, the mutex approach should be
ready for implementation.
