# Forge Operator Guide

Task-oriented guide for running the forge multi-agent system. Living
document — content inside `docs-refresh` marker blocks is regenerated
from the source files declared in `.claude/docs-refresh.yml`. Manual
prose outside the markers is preserved.

## Getting Started

<!-- docs-refresh:start section=getting-started -->
### One-time setup per project

A forge project needs:

1. **A `.claude/forge-project.yml`** at the project root declaring
   `project.name`, `forge.expected_root`, `forge.base_ref`, and the
   service/test/qa sections that workers read for their environment
   preamble. (See the project-config section of `technical-reference.md`
   for the schema.)
2. **`forge-start`** to create the 5-pane tmux session and write
   `.dev/.forge-session` with the session name.

### Starting a pipeline

From pane 1 of your forge session, type:

```
/forge pipeline <slug>
```

What happens:

1. `/forge` confirms `.dev/.forge-session` exists in the cwd; it refuses
   otherwise.
2. It parses the argument line into a canonical intent:
   `forge-pipeline <slug>`, `forge-fix-pipeline <slug>`, resume mode,
   local status/pause, or an ad-hoc request.
3. For pipeline/fix/ad-hoc orchestration, it loads
   `~/.claude/skills/forge-orchestrator/SKILL.md` into pane 1. You now
   **are** the in-pane orchestrator; there is no hidden background agent
   and no forwarding layer.
4. The orchestrator seeds itself with the original request, project root,
   tmux session, and `.dev/forge-tmp/orchestrator-events.log`.
5. It runs Hard Rule 0 first (pin session, `export TMUX_SESSION`), then
   `preflight` + `health` (Hard Rule 18), then enters the requested mode.

### Other `/forge` forms

| You type | What happens |
|---|---|
| `/forge pipeline <slug>` | Load the in-pane orchestrator and enter Pipeline Mode |
| `/forge start pipeline <slug>` | Same as `/forge pipeline <slug>` |
| `/forge fix-pipeline <slug> [--reproduce]` | Load the orchestrator and enter Fix Pipeline Mode |
| `/forge resume <slug>` | Load the in-pane orchestrator with the resume preamble (cd, preflight, context, inspect pending callback, resume `wait` or dispatch next) |
| `/forge status` | Local: runs `~/bin/forge-bridge status` and prints verbatim |
| `/forge pause` | In-pane pause: stop dispatching, leave callbacks intact, print bridge status, explain `/forge resume <slug>` |
| anything else | Treated as an ad-hoc request; handled by the in-pane orchestrator unless the wording is an exact pipeline trigger |

### Subsequent messages

Once `/forge` has loaded the orchestrator, the user speaks to pane 1
directly. There is no active-agent forwarding grammar. Two literal
exceptions are handled specially:

- `/forge status` or `/forge-status` prints `~/bin/forge-bridge status`
  verbatim, then continues.
- `/forge pause` pauses per the local pause behavior above.

If a user-typed line begins with `FORGE_DONE:`, `FORGE_BLOCKED:`, or
`FORGE_ERROR:`, the orchestrator drops it as synthetic worker-callback
noise.

### Escape hatch

`/forge-orchestrator` loads the orchestrator body directly into the
current session for manual driving without the `/forge` argument grammar.
The behavioral rules below apply equally to both modes.
<!-- docs-refresh:end section=getting-started -->

## Running a Pipeline

<!-- docs-refresh:start section=running-a-pipeline -->
### Pipeline mode trigger

Only this literal phrasing enters Pipeline Mode:

```
forge-pipeline {slug-or-feature-description}
```

The `/forge pipeline <slug>` slash command forwards this verbatim. Other
phrasings ("run the pipeline for X", "do the full thing for X") are
treated as ambiguous and the orchestrator will ask before doing
anything.

### What runs autonomously

The eight stages execute back-to-back without asking between stages:

```
proposal → review → incorporate → implementation → impl-review → coding → qa → verify
```

Between stages the orchestrator:

1. Spawns the digest agent (background `Agent` with a one-line "follow
   this file" prompt against `.dev/forge-tmp/digest-{stage}-{slug}.txt`)
2. Waits for the digest's `CONFIDENCE` + `BLOCKING_ITEMS` summary
3. **Advances** if `CONFIDENCE: HIGH` and `BLOCKING_ITEMS: 0` —
   emits a one-line status (`✓ review complete — advancing to
   incorporate`) and immediately begins the next stage
4. Otherwise applies the **Change-of-Course Heuristic**: read the disk
   artifact, classify as risk-flagging (advance with `add-note`) or
   real defect (escalate to user)

### Infra-lock discipline

The five infra-touching stages (`coding`, `qa`, `qa-fix`, `qa-retry`,
`verify`) are wrapped in `forge-bridge infra-lock` so parallel worktrees
do not collide on fixed ports or the shared database. Reasoning stages
(`proposal`, `review`, `incorporate`, `implementation`, `impl-review`)
never lock and can run in parallel across worktrees.

For dispatched infra stages, the orchestrator acquires before dispatch
and releases only after terminal `DONE` or `ERROR`. It intentionally holds
the lock through `PROMPTING`, `STALLED`, `TIMEOUT`, `DEAD`, and `BLOCKED`.

### Status messages between stages

One line each. No recaps, no "here's what we did". The user is waiting
for completion or escalation, not following along (Hard Rule 8).

### Halt conditions (orchestrator stops and surfaces)

| Condition | Source |
|---|---|
| `FORGE_BLOCKED` the orchestrator can't resolve in one fix attempt | Worker callback |
| `AGENT_FAILED` after one retry | Background agent failure |
| Digest `BLOCKING_ITEMS > 0` pointing at a real defect | Change-of-Course |
| Missing prerequisite (no session, worker dead, etc.) | Preflight / Hard Rule 9 |
| Verify returns `ISSUES_REMAIN` | Final stage |
| Preflight HALT code | `BRANCH_MERGED_WITH_DRIFT`, `WRONG_DIRECTORY`, `DETACHED_HEAD`, `BRANCH_UNCLEAR` |
| Non-OK pane from `forge-bridge health` | `DEAD`, `WRONG_PROCESS`, `UNKNOWN` |
| Explicit user interrupt | `forge-stop`, `forge-pause`, `forge-skip <stage>` |

### Completion

After `verify` returns clean:

> ✓ verify complete — pipeline complete for {slug}. Ready for PR —
> let me know when to open it.

Then STOP. The orchestrator never opens the PR autonomously — that's an
explicit user instruction outside pipeline mode.
<!-- docs-refresh:end section=running-a-pipeline -->

## Interrupting and Resuming

<!-- docs-refresh:start section=interrupting-and-resuming -->
Four explicit user interrupts halt or alter Pipeline Mode. Type any of
them as a plain message to the orchestrator (or via `/forge pause` for
the pause variant).

| Verb | Effect |
|---|---|
| `forge-stop` | Halt immediately at the current step. Any in-flight Codex worker is left as-is; background agents currently running are also left to finish, but their outputs are not consumed once stopped |
| `forge-pause` | Finish the current stage's digest, then halt before the next dispatch. Pipeline state is preserved; resume with `forge-resume` |
| `forge-skip {stage}` | Skip a named stage and advance to the next one. Valid for `qa` (with a warning about unverified code shipping). **Refused for `verify`** — verify is load-bearing for the completion guarantee |
| `forge-resume` | Re-enter Pipeline Mode at the next stage if a `forge-pause` is active for the slug. No-op if no paused pipeline exists |

### Resume from a fresh shell

If you closed the chat or the orchestrator's session ended:

```
/forge resume <slug>
```

The orchestrator's resume preamble runs:

1. `cd` to the project root
2. `~/bin/forge-bridge preflight` (validates session, panes, working
   tree)
3. `~/bin/forge-bridge context` (renders current pipeline context)
4. Inspect the session-scoped `.dev/forge-context.<session>.yml` for the
   active stage / worker / wait state
5. If a stage is pending callback, resume `forge-bridge wait` for that
   stage. Otherwise, dispatch the next stage per the transition table

`.dev/forge-status.<session>.md` is **human-facing display**, not the
primary machine recovery source. Use `forge-bridge context` (Hard Rule 16)
to load the canonical state.

If `context` reports a legacy shared `.dev/forge-context.yml`, use the
suggested `set-context --slug <slug>` only when that pipeline is yours.
The bridge deliberately does not auto-adopt a legacy context from another
session.

### Skip with care

`forge-skip qa` is allowed but logged with a warning. `forge-skip
verify` is refused — if you really want to skip verify, use `forge-stop`
and re-invoke the next pipeline manually.

<!-- TODO: `/forge` command prose still says resume should inspect
`.dev/forge-context.yml`; bridge code is authoritative and resolves
`.dev/forge-context.<session>.yml`. -->
<!-- docs-refresh:end section=interrupting-and-resuming -->

## Status and Health

<!-- docs-refresh:start section=status-and-health -->
Core commands for "what's going on right now":

| Command | When |
|---|---|
| `/forge status` (or `~/bin/forge-bridge status`) | Rolling human-readable summary — pipeline + stage + recent activity + pending callbacks + artifacts + notes + infra-lock line when resolvable |
| `~/bin/forge-bridge context` | Active pipeline + last completed stage + next stage + notes + recent log entries + pending signals — the canonical machine-recovery view |
| `~/bin/forge-bridge health` | Per-pane check: do all 5 panes exist and run the expected worker process? Exits 0 only when every pane is `OK`. Output lines: `OK \| DEAD \| WRONG_PROCESS \| UNKNOWN pane=<name> idx=<n> …` and a `SUMMARY` line |
| `~/bin/forge-bridge preflight` | Kickoff snapshot: pwd, branch, merge state, halt status code |
| `~/bin/forge-bridge history [lines]` / `pipeline-log <slug> [lines]` | Recent activity across all pipelines / detail for one pipeline |
| `~/bin/forge-bridge usage [<worker>]` | Per-worker usage snapshot recorded at each task completion: normalized `headroom` (0-100 = % capacity remaining) + `confidence`. Claude workers report from the pane footer; Codex is always `unknown` (the CLI exposes no usage in pane text). Read-only — never scrapes a pane |
| `~/bin/forge-bridge infra-lock status` | Whether the global infra lock is free, held live, stale, foreign-host, or corrupt |

### When to run each

| Situation | First command |
|---|---|
| "What's happening right now?" | `forge-bridge context` |
| "Show me the human summary" | `/forge status` |
| "The orchestrator says a pane is wrong" | `forge-bridge health` |
| "How used up are the workers?" | `forge-bridge usage` |
| "Why is an infra stage waiting?" | `forge-bridge infra-lock status` |
| "Is this branch safe to dispatch on?" | `forge-bridge preflight` |
| "Where did we leave off last week?" | `forge-bridge context`, then `history 20` |

### Status file mechanics

`.dev/forge-status.<session>.md` is auto-maintained by the bridge as a
side effect of `dispatch` / `wait` / `callback` / `status`. It summarizes:

- Active pipeline + current stage (or "idle" with last completed)
- Next stage (per the canonical transition table)
- Recent activity (last 15 events: dispatches, completions, blocks)
- Pending callbacks (`response: null` entries)
- Artifacts (files produced so far)
- Notes (from `forge-context.<session>.yml`)
- Infra lock status when the bridge can resolve tmux identity and git
  common-dir

`/forge status` surfaces this file verbatim — the orchestrator should
not re-narrate or compress it (forge-status command body).

### Heartbeat event log

The bridge writes `_emit_event` lines to
`.dev/forge-tmp/orchestrator-events.log`:

```
DISPATCH | WAIT | CALLBACK | DIGEST | STAGE | STALL | ERROR | COMPLETE | USAGE | LOCK: pipeline=<slug> <key=value …>
```

Use it as a low-level audit stream when debugging. The current `/forge`
path is in-pane and does not create the older hidden-agent monitor layer.

<!-- TODO: `~/.claude/commands/forge-status.md` still says the status file
lives at `.dev/forge-status.md`; bridge code is authoritative and renders
`.dev/forge-status.<session>.md`. -->
<!-- docs-refresh:end section=status-and-health -->

## Blocked-on-You Notifications (`forge-watch`)

`forge-watch` inverts the polling workflow: instead of clicking through tabs
to find a pipeline waiting on you, it watches every live forge session and
fires a macOS notification only when one is actually blocked on a human.

It is strictly **read-only** against forge state — it never writes under any
project's `.dev/`, never sends keys to a pane, never touches tmux/session
state. Its only writes are its own cache (`~/.cache/forge-watch`) and config
(`~/.config/forge`). If it dies or is uninstalled, you are back to exactly
today's poll-the-tabs workflow; no pipeline depends on it.

### Commands

| Command | What |
|---|---|
| `forge-watch status` | One scan, print findings only — no notifications. The debug surface: shows everything, including status-only items (stale zombies, legacy contexts, abandoned pendings). |
| `forge-watch check` | One scan, print findings **and** deliver notifications with debounce. This is what the launchd agent runs. |
| `forge-watch ack <session\|slug\|project-dir>` | Silence every current condition for that target until it clears and re-enters. The "I know, leave me alone" verb. |
| `forge-watch install` | Write and load the launchd agent (30s interval) and fire a test notification. |
| `forge-watch uninstall` | Unload and remove the launchd agent (cache/config left in place). |

### What it notifies on

| Condition | Meaning |
|---|---|
| `NEEDS-DECISION` | A live pipeline finished a QA-family stage and is waiting for your call (fires after a short dwell, and only when nothing is dispatched — an in-flight stage suppresses it). |
| `WORKER-BLOCKED` | A worker sent `FORGE_BLOCKED` and is explicitly waiting on a human. |
| `STAGE-ERROR` | A stage completed with `error` status. |
| `PIPELINE-ERROR` | The bridge logged an `ERROR`/`GUARD_BLOCK` (dispatch guard, tier violation, callback publish failure, …). |
| `WORKER-STALLED` | An open dispatch has been pending past the bridge's STALE threshold (2× `FORGE_STALL_THRESHOLD_S`) — and is recent enough to still be actionable. |
| `WORKER-STALL-EVENT` | The bridge's own content-level stall detector reported a stuck worker. |
| `PIPELINE-COMPLETE` | A pipeline reached `complete` (info; disable with `FORGE_WATCH_NOTIFY_COMPLETE=0`). |
| `ZOMBIE-ACTIVE` | A context with recent/active work points at a session that is no longer live (e.g. an abandoned pipeline after a session restart). |

Status-only (never notify by default): `ZOMBIE-STALE-CONTEXT` (a dead session's
week-plus-old leftover context), `STALE-PENDING` (a months-old never-closed
proposal log — residue, not a live stall), and `LEGACY-CONTEXT` (a bare
`forge-context.yml` migration hint). These keep the notification stream honest
while still being visible in `forge-watch status`.

### How it finds sessions

Discovery is driven by `tmux list-sessions` — each session's working directory
is a project root. A context file counts as *live* only when its embedded
session name is live **and** that session's path matches the project root;
because `forge-N` names get reused across restarts, a name-only check would
alias one project's session onto another's stale context. Projects whose
sessions are dead can still be watched by listing their roots in
`~/.config/forge/watch-roots` (one path per line).

### Tuning

Environment variables win when set; otherwise `~/.config/forge/watch.env`
(parsed as `KEY=value` **data**, never sourced) supplies them; otherwise
defaults apply. `install` seeds `watch.env` from your current shell so the
launchd agent — which does not inherit `.zshrc` — agrees with your terminal.

| Variable | Default | Effect |
|---|---|---|
| `FORGE_STALL_THRESHOLD_S` | 600 | Stall threshold; STALE = 2× this. Match the bridge. |
| `FORGE_WATCH_DWELL_S` | 300 | How long a decision state must persist before `NEEDS-DECISION` fires. |
| `FORGE_WATCH_RENOTIFY_S` | 900 | Re-notify base for a persistent condition (×2 backoff each repeat, capped at 4h). |
| `FORGE_WATCH_ZOMBIE_AGE_D` | 7 | Window separating actionable zombies/stalls from old residue. |
| `FORGE_WATCH_NOTIFY_COMPLETE` | 1 | Notify on pipeline completion. |

### Notification permission gotcha

macOS attributes `osascript` notifications to **Script Editor**. If you never
see notifications (but `forge-watch status` clearly shows findings), grant
notification permission to Script Editor in **System Settings ▸ Notifications**,
then re-run `forge-watch check`. `install` fires one test notification so you
can catch this immediately.

## Recovery

<!-- docs-refresh:start section=recovery -->
### Compaction or session restart

When the orchestrator picks up after >5 minutes of silence (detected via
the timestamp on the most recent log entry), it runs `preflight` AND
`health` before the next dispatch (Hard Rule 18).

Recovery order:

1. **Quick state**: `~/bin/forge-bridge context` — active pipeline, last
   completed stage, next stage, notes, pending signals
2. **If context is stale or missing**: `forge-bridge history 20` to find
   entries with `response: null` (in-flight tasks), or `set-context
   --slug {slug}` to rebuild context from the pipeline log
3. **Resume the in-flight stage** via `forge-bridge wait` with the
   `--slug` / `--stage` / `--worker` from the pending log entry. `wait`
   will pick up an existing callback if one already arrived, or block
   for a new one
4. **If the worker died** (`wait` returns `STATUS=DEAD`), re-dispatch
   the stage from scratch
5. **If `wait` returns `STATUS=STALLED`**, follow Agent Failure Recovery
6. **If a background (digest) agent failed**, check the stage's output
   artifact on disk. If complete, re-spawn the digest via `forge-bridge
   digest`. If not, re-dispatch the stage

If the stage is one of `coding`, `qa`, `qa-fix`, `qa-retry`, or `verify`,
the infra lock may intentionally still be held while the stage is
non-terminal. Do not release it merely because the orchestrator restarted.

### Handling FORGE_BLOCKED

When `wait` returns `STATUS: BLOCKED`:

1. Read the CALLBACK message; if more context is needed, read the full
   artifact at `.dev/proposals/{slug}/` or `.dev/qa/{slug}/`
2. Resolve the issue (edit files, run commands, etc.)
3. Send a continuation message to the worker using `--force` because
   the worker still holds the original task — **do NOT `dispatch`
   again**, that would `/clear` and lose context:
   ```
   ~/bin/forge-bridge send --force {worker} "Fixed X. Continue."
   ```
4. After the continuation send succeeds, archive the consumed BLOCKED
   callback so the next `wait` does not re-read the stale callback:
   ```
   ~/bin/forge-bridge callback-consume --slug {slug} --stage {stage} --status BLOCKED
   ```
5. Wait again with the same args; the next callback resolves it

If the continuation send fails or the orchestrator crashes before sending,
leave the callback file in place. Resume will surface the same BLOCKED state
again.

For infra stages, the lock remains held during BLOCKED repair and the
continuation loop. Release only after terminal DONE/ERROR, or after an
explicit abort where the operator has confirmed no worker/service process
is still touching shared infra.

### Agent Failure Recovery

Background-agent failures follow this protocol:

1. **Log the failure**: `forge-bridge log-response --slug {slug}
   --response "AGENT_FAILED: {error}"`
2. **If retryable** (429 rate limit, timeout, transient API error):
   retry once with the same prompt
3. **If retry fails or non-retryable**: escalate to user with the
   error, options to fix, skip (non-critical only), or abort the
   pipeline
4. **Never auto-retry more than once per stage**

### Infra-lock recovery

`forge-bridge infra-lock status` reports:

| State | Meaning |
|---|---|
| `FREE` | No holder sidecar exists |
| `HELD live` | Another live tmux session owns the lock |
| `STALE` | Holder session is dead; the next acquire can steal it, or the operator can force-release after confirming the stage is stopped |
| `HELD foreign-host` | Holder is on another host; liveness is not verifiable locally |
| `ESCALATE` | The holder sidecar is corrupt and needs manual inspection |

`infra-lock acquire` waits with defaults `FORGE_INFRA_LOCK_TIMEOUT_S=1800`
and `FORGE_INFRA_LOCK_INTERVAL_S=15`. On timeout it prints holder metadata
and a force-release command. Do not run the force-release command blindly;
forcing while a worker still runs can collide on the shared DB or ports.

### Stall classification (the seven `wait` outcomes)

`forge-bridge wait` returns one of:

| STATUS | Meaning |
|---|---|
| `DONE` | Worker called `callback --status DONE`; ready to advance |
| `BLOCKED` | Worker called `callback --status BLOCKED`; needs orchestrator action |
| `ERROR` | Worker called `callback --status ERROR`; investigate the artifact |
| `STALLED` | Pane output hasn't changed in `FORGE_STALL_THRESHOLD_S` (default 600s) and a log entry is still pending |
| `PROMPTING` | Pane is showing a tool-approval prompt (`^ ❯ \d+\. ` for Claude); surface to user |
| `DEAD` | Pane no longer exists; re-dispatch from scratch |
| `TIMEOUT` | `--timeout` exceeded without resolution; investigate |

Per-stage timeouts: pass `--timeout <seconds>` to `wait` for
legitimately-long stages. Coding and QA typically need more than the
default 600s. See `references/stall-detection.md` for the per-stage
timeout table.

For infra stages, `PROMPTING`, `STALLED`, `TIMEOUT`, `DEAD`, and `BLOCKED`
are all non-terminal; the lock stays held unless the operator explicitly
aborts and safely force-releases.

### Multiple forge sessions, wrong pane

Symptom: the orchestrator reads or sends to a pane in a different forge
window than the one you're in.

Cause: the orchestrator did not pin its session (Hard Rule 0 added
2026-05-22), so `forge-bridge` re-resolved on every call via
`require_tmux_session`'s fallback path and picked a different `forge-*`
session.

Fix:

1. The orchestrator should `export TMUX_SESSION=<name>` at session
   start. `/forge` seeds the session from `.dev/.forge-session`; manual
   `/forge-orchestrator` should read the same file or use an explicit
   `TMUX_SESSION`.
2. If multiple `forge-*` sessions exist, the bridge now refuses to
   auto-pick and lists the candidates. The error message tells you to
   either `export TMUX_SESSION=<name>` or run from a project dir
   containing `.dev/.forge-session`.
3. If `.dev/.forge-session` refers to a session that no longer exists,
   the bridge errors loudly with the stale-file hint — delete the file
   or run `forge-start` to recreate the session.
<!-- docs-refresh:end section=recovery -->

## Multi-Worktree Concurrency

You can run more than one worktree of the same repo through a pipeline at the
same time. Each worktree has its own `forge-N` tmux session and its own `.dev/`
state, so the **reasoning stages** (proposal, review, incorporate, implementation,
impl-review) overlap freely. The catch is the **shared infra stack** — fixed-port
services + a shared Postgres — which only one pipeline may touch at a time.

A single cross-worktree **infra lock** enforces this automatically (orchestrator
Hard Rule 23; mechanics in the technical reference). The five infra stages
(`coding`, `qa`, `qa-fix`, `qa-retry`, `verify`) acquire the lock before running
and release it when the stage completes. You normally never touch the lock — it
is held and released for you. The cases where you *do* see it:

### A pipeline is waiting on the lock

While worktree A holds the lock for an infra stage, worktree B's next infra stage
**waits** (it has not dispatched a worker yet, so it shows as waiting — never a
false `STALLED`). You'll see `LOCK action=wait …` lines in the heartbeat event log
and an `Infra lock: HELD live by <slug> …` line in `forge-bridge status`. This is
normal; B proceeds the moment A releases.

Check who holds it at any time:

```bash
~/bin/forge-bridge infra-lock status
```

### A killed worktree releases automatically

If you kill a worktree mid-infra-stage (its tmux session dies), the lock is **not**
leaked: the next waiter detects the dead session and steals the lock under the
guard. No manual cleanup. `status` shows such a holder as `STALE dead-session`.

### Lock-wait ceiling (escalation)

`acquire` waits up to `FORGE_INFRA_LOCK_TIMEOUT_S` (default 1800s — infra stages
legitimately run 20–30 min). On the ceiling the orchestrator surfaces a structured
`INFRA_LOCK: TIMEOUT` block with the full holder metadata (host, session,
session_id, session_created, slug, stage, acquired_at, project_root) and a
**liveness verdict** (`live` / `stale` / `foreign-host`). Decide from that:

- **`liveness = stale`** → the holder's session is dead; the next acquire will
  steal it. Re-run, or force-release if you want it gone now.
- **`liveness = live`** → a real pipeline is mid-infra-stage. Let it finish, or see
  "abandoned holder" below.
- **`liveness = foreign-host`** → held on another host; liveness can't be verified
  from here (rare — forge worktrees are same-host in practice).

### Abandoned live holder (recovery)

A `HELD live` holder whose pipeline is abandoned (orchestrator compacted/idle, or
parked at `PROMPTING`/`STALLED`) does **not** auto-release. Recover deliberately:

1. `~/bin/forge-bridge infra-lock status` → note the holder's slug/session/stage.
2. Inspect that worktree/session. If the stage should continue, **resume the same
   `(slug, stage)`** — `acquire` is reentrant, so it re-adopts the lock (you'll see
   `ALREADY_HELD`). A resume that advanced to the next infra stage updates the
   holder in place (`stage_update`).
3. **Only** if the stage is confirmed stopped or you are intentionally aborting it:

   ```bash
   ~/bin/forge-bridge infra-lock release --slug <held-slug> --stage <held-stage> --force
   ```

   ⚠️ Force-release while an infra worker is still live can collide on the shared
   DB/ports. Confirm the stage (and any dev-server/test process it started) is
   actually stopped first.

### Resume safety

Resuming a pipeline (`/forge resume <slug>`) cannot deadlock against the lock: an
`acquire` for a `(slug, stage)` the same session already holds returns
`ALREADY_HELD` reentrantly, and `release` is idempotent. An interrupted pipeline's
lock is either re-adopted on resume or stolen by another worktree once its session
dies.

### Fresh-code guarantee

The lock serializes infra access; it does not by itself make the *running* server
reflect the current worktree. So `qa`, `qa-retry`, and `verify` **restart services
against their own worktree** before testing (identity-checked: they only stop a
process matching the project's expected dev-server shape, and **escalate** rather
than kill an unknown process on a configured port). `coding` relies on Playwright
autostart (set `reuseExistingServer: false` so its tests start fresh); `qa-fix`
runs no live tests.
