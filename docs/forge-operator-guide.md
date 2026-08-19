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
2. **`forge-start`** to provision a session-named Git worktree, seed its
   root-local Forge assets, create the 5-pane tmux session there, and write
   `.dev/.forge-session` with the session name. Worktrees are deliberately
   never auto-removed.

   Worktrees land in `.forge-worktrees/` **inside the project the session
   works on** — `goparent-ai/.forge-worktrees/goparent-ai-<session>` — so each
   project keeps its own worktrees and no project's sessions litter the
   directory your projects live in. The container is anchored at the main
   working tree, so starting a session from inside a worktree still lands flat
   beside its siblings instead of nesting. On first use Forge adds the
   container to the repository-local `.git/info/exclude` (never a tracked
   `.gitignore`), because an unignored container would make every later
   `git status` and dirty-base check see an untracked root.

   Override per project with `forge.worktree.parent` in
   `.claude/forge-project.yml`, or per invocation with `FORGE_WORKTREE_PARENT`;
   both still accept a path anywhere on disk. `forge.worktree.prefix` names the
   leaf (default: the repository directory name).

   Because the container sits inside the working tree, `git clean -xdf` at the
   project root will delete every worktree under it. The dot-prefix keeps it out
   of ripgrep and pytest collection by default, but it is not a `git clean`
   guard.

`forge-start --here [name]` is the escape hatch: it starts in the current
physical Git root and does not provision. Use `forge-start --here` when working
on `forge-toolkit` itself because the installed `~/bin/forge*` launchers point
at the primary toolkit checkout. Under `--here`, a subdirectory invocation is
collapsed to the Git toplevel, so `.dev/.forge-session` is no longer written in
the subdirectory. Re-run `forge register` at a primary root after the canonical
hook block changes; provisioning refreshes the worktree copy but intentionally
does not mutate the primary as a side effect.

### Starting a pipeline

From the orchestrator (pane 0) of your forge session, type:

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
   `~/.claude/skills/forge-orchestrator/SKILL.md` into the orchestrator (pane 0). You now
   **are** the in-pane orchestrator; there is no hidden background agent
   and no forwarding layer.
4. The orchestrator seeds itself with the canonical intent, the verbatim
   user line, the **physical code root** (`git rev-parse --show-toplevel`
   for this pane), the `root_identity` and `git_common_dir` values from
   `preflight`, the `host_session` from `identity`, and
   `.dev/forge-tmp/orchestrator-events.log`. The seed also states the
   standing requirement: every mutating dispatch must carry matching
   structured delivery fields — a prose-only root is invalid.
5. It runs Hard Rule 0 first (`~/bin/forge-bridge identity` — a live host
   probe, **no** `TMUX_SESSION` export; HALT unless `identity_state=MATCH`),
   then `preflight` + `health` (Hard Rule 18), then enters the requested mode.

### Other `/forge` forms

| You type | What happens |
|---|---|
| `/forge pipeline <slug>` | Load the in-pane orchestrator and enter Pipeline Mode |
| `/forge start pipeline <slug>` | Same as `/forge pipeline <slug>` |
| `/forge fix-pipeline <slug> [--reproduce]` | Load the orchestrator and enter Fix Pipeline Mode (including the investigate↔fix-plan alternation tracked in `.dev/.forge-fix-alternation`) |
| `/forge resume <slug>` | Load the in-pane orchestrator with the resume preamble (cd, `identity` (confirm `MATCH`), preflight, context, inspect pending callback, resume `wait` or dispatch next) |
| `/forge status` | Local: runs `~/bin/forge-bridge status` and prints verbatim |
| `/forge pause` | In-pane pause: stop dispatching, leave callbacks intact, print bridge status, explain `/forge resume <slug>` |
| anything else | Treated as an ad-hoc request; handled by the in-pane orchestrator unless the wording is an exact pipeline trigger |

### Subsequent messages

Once `/forge` has loaded the orchestrator, the user speaks to the orchestrator (pane 0)
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

Every `commit`/`live-qa` stage — today `coding`, `qa`, `qa-fix`, `qa-retry`,
`verify`, `fix-code`, `fix-qa`, `fix-qa-retry` — is wrapped in
`forge-bridge infra-lock` so parallel worktrees do not collide on fixed ports
or, more importantly, on the shared test database. `workspace` stages
(`proposal`, `adhoc`, `review`, `incorporate`, `implementation`, `impl-review`,
and the `fix-scout` / `fix-plan*` / `fix-investigate*` / `fix-reproduce` family)
never lock and can run in parallel across worktrees. `dispatch` refuses an infra
stage outright when the caller does not hold the lock (`INFRA_LOCK_REQUIRED`,
exit 5).

For dispatched infra stages, the orchestrator acquires before dispatch
and releases only after terminal `DONE` or `ERROR`. It intentionally holds
the lock through `PROMPTING`, `STALLED`, `TIMEOUT`, `DEAD`, and `BLOCKED`.

### Capability routing on `coding`

`coding` is capability-routed in the **current physical worktree**. Before
every mutating dispatch the orchestrator runs `identity`, a worktree-aware
`preflight`, `health`, and `forge codex-lane --root <physical-root> --stage
coding`. A linked worktree is a valid, distinct root even though it shares a
Git common dir with the main checkout. In `contain` and `broker-shadow` the
`commit` capability routes to `reviewed-host`; in `enforce` Codex may edit
while the exact-path broker owns the commit. Workers never run direct Git
mutations.

### Planning a batch

Before handing out two or more tasks, run `~/bin/forge-bridge usage --refresh`
once and keep the snapshot with the plan — it live-measures all four workers
plus the orchestrator (pane 0) and prints a per-worker recommendation. A single dispatch does not
need it; the dispatch-time gate is strictly stronger.

### Batch end

The completion summary runs `forge parked --root <root> --session <session>`.
Exit 10 means parked or blocked residue remains — report the run **INCOMPLETE**
and enumerate the items rather than calling it done.

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
| `~/bin/forge-bridge usage [<worker>] [--refresh] [--json]` | Per-worker usage snapshot recorded at each task completion: normalized `headroom` (0-100 = % capacity remaining) + `confidence`. Claude parses `ctx: Nk (P%)`; Codex parses `Context N% left` when rendered. A valid anchor for either provider publishes normalized numeric headroom with `confidence=high`; missing or malformed input remains `unknown` and never means safe or exhausted. Plain form is read-only — observation never authorizes clearing or compaction. `--refresh` live-measures the four workers and the orchestrator (pane 0) first; `--json` adds a per-worker `recommendation`. See "Active context management" below |
| `~/bin/forge-bridge reset-idle --worker <w>` | Reset an idle worker off the dispatch critical path. Refuses on an open pending, non-idle health, a terminal state, `observe` mode, and the orchestrator (pane 0) |
| `~/bin/forge-bridge infra-lock status` | Whether the global infra lock is free, held live, stale, foreign-host, or corrupt |
| `~/bin/forge-bridge hygiene-status` | Show hygiene mode, reset capability, thresholds, residue, terminal state, and activation blockers |
| `~/bin/forge-bridge hygiene-gc [--days N] [--dry-run]` | Conservatively inspect or reap old terminal journals from dead sessions |
| `~/bin/forge-bridge identity` | Host/target session descriptor — `host_session`, `target_session`, `identity_state`. Exits 0 on `MATCH`/`CROSS_SESSION_DECLARED`, 3 otherwise. Run first when a pane "looks wrong" (Hard Rule 0) |
| `~/bin/forge-bridge blocked-audit [--root <path>] [--json]` | Read-only census of BLOCKED/PARKED callbacks, half-parked records, duplicate open pendings, and stale state keys. **MUTATES NOTHING** — the diagnostic for "what's still holding this pipeline open" |

Codex CLI 0.144.5 is the supported V2 floor. Numeric usage requires a Forge
session created with the V2 status-line contract; older or incompatible builds
degrade to `unknown`. Existing panes keep their startup configuration: recreate
sessions only at an operator-approved boundary, then dispatch a trivial task and
compare recorded headroom with the visible footer. Before rollout, confirm
`~/bin/forge-bridge` and `~/bin/forge-start` are symlinks to this toolkit; if
either is a regular file, use the normal installer reconciliation instead of
overwriting it ad hoc. Forge never restarts, clears, or compacts active work to
observe usage.

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

`.dev/forge-status.<session-or-__nosession__>.md` is auto-maintained by
the bridge as a side effect of `dispatch` / `wait` / `callback` /
`status`. It summarizes:

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
DISPATCH | WAIT | CALLBACK | DIGEST | STAGE | STALL | ERROR | COMPLETE | USAGE: pipeline=<slug> <key=value …>
```

Bridge internals also append `WARN_DUP_PENDING`, `GUARD_BLOCK`,
`SUPERSEDE`, `SUPERSEDE_AUDIT`, `CALLBACK_CONSUME`, `PARK`, `CROSS`,
`IDENTITY`, and `LOCK` events. Use the log as a low-level audit stream when
debugging. The current `/forge` path is in-pane and does not create the
older hidden-agent monitor layer.

The terminal `COMPLETE` event is **qualified** when a pipeline finishes
`verify` while session-scoped parked or blocked items remain: it carries
`qualifier=incomplete parked=N blocked=M` instead of a bare completion.
Treat a qualified `COMPLETE` as "pipeline reached verify but still has open
lifecycle items" — run `blocked-audit` to enumerate them.

<!-- TODO: `~/.claude/commands/forge-status.md` still says the status file
lives at `.dev/forge-status.md`; bridge code is authoritative and renders
`.dev/forge-status.<session>.md`. -->
<!-- docs-refresh:end section=status-and-health -->

### Active context management

Context hygiene has two halves. The **gate** (shipped) decides whether a worker may start a
new stage. **Active management** (this feature) makes usage visible and measures it at every
moment a pane's context actually changes.

| Command | What it does |
|---|---|
| `~/bin/forge-bridge usage` | Read-only snapshot. Freshness is **per-record** `measured_at`, so a row that has not been re-measured shows `STALE` even when another worker's callback just refreshed the file |
| `~/bin/forge-bridge usage --refresh` | Live-measures all four workers **and the orchestrator (pane 0)**, then renders. The batch-planning snapshot — run it once before distributing two or more tasks. In-flight panes are sampled, not skipped; their number is a **floor** |
| `~/bin/forge-bridge usage --json` | Same data, stable keys, plus a tier-free `recommendation` per worker: `reset-first` \| `ok` \| `busy` \| `unknown` |
| `~/bin/forge-bridge reset-idle --worker <w>` | Reset an **idle** worker off the dispatch critical path. Refuses on an open pending, non-idle health, a terminal state, `observe` mode, and the orchestrator (pane 0) |
| `~/bin/forge-bridge hygiene-status` | Now also prints every **effective** threshold and the last ten hygiene decisions |

Five thresholds, five different questions:

| Knob | Default | Question |
|---|---|---|
| `FORGE_WORKER_MIN_HEADROOM` (+`_CLAUDE`/`_CODEX`) | 75 | Should a new stage start in this conversation? **This is the gate — unchanged.** |
| `FORGE_CONTEXT_ALERT_HEADROOM` (+`_CLAUDE`/`_CODEX`) | 25 | Is this worker in trouble? (board finding + send warning) |
| `FORGE_ORCHESTRATOR_ALERT_HEADROOM` | 30 | Does the orchestrator (pane 0) need a handoff? |
| `FORGE_USAGE_MAX_AGE_S` | 900 | **Display only** — is this reading too old to show as current? It is *never* a gate input; hygiene evidence has no wall-clock expiry |
| `FORGE_SEND_MIN_HEADROOM` | 0 (off) | Opt-in hard floor on non-`--force` sends. This one is **strictly below** the floor; the other four are inclusive |

Plus `FORGE_USAGE_SAMPLE_INTERVAL_S` (300, `0` disables the mid-stage sampler) and the
watcher-side mirror `FORGE_WATCH_ALERT_HEADROOM` (25, set in `~/.config/forge/watch.env`).

**What you will see.** Every dispatch and every direct send now prints one
`HYGIENE <worker>: <ACTION> — <reason>` line to stderr, so the check is visible from the orchestrator (pane 0)
instead of only in the events log. `forge-bridge status` gains a **Worker context** table and
a **Recent hygiene decisions** tail. When a worker crosses the alert threshold the menubar
grows a `◐N` segment (after `⏸N`, before `✓`) and `forge board` shows a `WORKER-LOW-CONTEXT`
row — visible and silent, no notification. **The row clears itself**: it is rebuilt from the
ledger on every scan, so it disappears on its own once that pane is reset or reads healthy
again.

**Why the menubar shows `1! ◐1` for one low pane.** The two counters answer different
questions and deliberately overlap, exactly as `DELIVERY-UNVERIFIED` already does: `◐N`
counts low-context panes, and `N!` counts board rows you have not looked at yet. Acking the
row from `forge board` removes it from `N!` and leaves `◐N` until the pane is actually reset.

**Direct sends warn, they never block by default.** A send is a *continuation* — resetting
the pane would destroy the exact context the send exists to continue — so a low reading
prints a loud warning and delivers anyway. Set `FORGE_SEND_MIN_HEADROOM` if you want a hard
floor; `--force` is never refused by it, because `--force` is the BLOCKED fix-and-continue
path.

**The orchestrator (pane 0) is observed, never touched.** Its reading lands under the ledger's top-level
`orchestrator:` key. At or below its threshold you get a board row and one notification; the
response is to write a handoff note and start a fresh orchestrator session (pane 0). Forge sends no reset,
no compact, and no keystroke of any kind to the orchestrator (pane 0).

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
| `ITEM-BLOCKED` | A queue item is blocked at a stage (worker sent `FORGE_BLOCKED`); a human is needed. |
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

If the stage is an infra stage (`coding`, `qa`, `qa-fix`, `qa-retry`, `verify`,
`fix-code`, `fix-qa`, `fix-qa-retry`), the infra lock may intentionally still be
held while the stage is non-terminal. Do not release it merely because the
orchestrator restarted.

### Handling FORGE_BLOCKED

Every worker BLOCKED must end in exactly one terminal action — **fix +
continue** (steps below), **supersede + re-dispatch** (`dispatch …
--supersede`, see the work-start guard), or **park** (below). Operator
`forge ask` blocks are exempt (see the Command Center ask exception). The
bridge refuses new dispatches and worker sends until the block is resolved.

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

The callback file is session-qualified
(`.dev/forge-tmp/callbacks/{slug}-{stage}.{session}.callback`, with a
legacy unqualified read fallback), so `callback-consume` and the next
`wait` resolve the same session's callback.

**Work-start guard.** While an unresolved BLOCKED item exists for the root,
both `dispatch` and worker-target `send` refuse to start *new* work
(`GUARD_BLOCK reason=unresolved-blocked-item`). The fix-and-continue
`send --force` above is exempt because it targets the same worker that holds
the block; a `dispatch --supersede` is exempt for the same slug. To
deliberately proceed past the guard, pass a one-shot `--allow-blocked
"<reason>"` (the non-empty reason is logged). Ask-origin BLOCKED items
(`forge ask`) are carved out — never guarded or parked.

**Parking instead of fixing.** If a BLOCKED item can't be resolved now but
you want the pipeline to keep moving, park it:

```
~/bin/forge-bridge park --slug {slug} --stage {stage} --reason "<why>" [--uncommitted]
```

`park` keeps the pending OPEN, writes a durable `parked_at`/`parked_reason`/
`uncommitted` record into the pending log entry (authoritative), flips the
callback to `status: PARKED`, and releases the infra lock. Close it later
with `park --resolve --slug {slug} --stage {stage} [--note …]`. A pipeline
that reaches `verify` with parked/blocked items still open reports a
**qualified** completion (`qualifier=incomplete parked=N blocked=M`); run
`blocked-audit` to enumerate the residue.

A parked slug cannot advance without `--supersede`, and re-running `park`
on an already-parked item is a safe no-op. A resumed orchestrator that sees
`STATUS: PARKED` treats the item as already parked — it skips it and does
**not** re-park or release the lock again.

Command Center ask exception: if the BLOCKED state came from a worker
running `forge ask --slug {slug} --stage {stage} --worker {worker}
"<question>"`, and the operator answers with `forge dispatch @<session>
"<answer>" --answers <ask-id>`, that answer dispatch has already archived
the BLOCKED callback before injecting the answer into the orchestrator (pane 0). In that case,
relay the answer to the worker with `send --force` and do **not** run a
second `callback-consume`.

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

Alongside `STATUS` / `STAGE` / `SLUG` / `WORKER` / `CALLBACK`, `wait` also
prints `CALLBACK_ID` and `PENDING_TIMESTAMP` — pass both to
`verify-decision` when closing a clean verify.

**Delivery binding on resume.** Every dispatch is registered with the
root-scoped broker and stamped with a `delivery_id` on both the pending log
entry and the callback. A callback that does not match the selected pending
entry is refused as `CALLBACK_IDENTITY_CHANGED` rather than silently
accepted — inspect the artifact and the current forge log instead of
replaying the status line. Pre-broker entries carry a blank `delivery_id`
and remain closable.

For infra stages, `PROMPTING`, `STALLED`, `TIMEOUT`, `DEAD`, and `BLOCKED`
are all non-terminal; the lock stays held unless the operator explicitly
aborts and safely force-releases.

### Multiple forge sessions, wrong pane

Symptom: the orchestrator reads or sends to a pane in a different forge
window than the one you're in.

Cause: the caller's live identity did not match the intended target, or
multiple same-root sessions made automatic selection ambiguous. The bridge now
resolves identity live on every command; `TMUX_SESSION` is only a mismatch
signal, not a routing override.

Fix:

1. Run `~/bin/forge-bridge identity` and verify `identity_state=MATCH`
   (or `CROSS_SESSION_DECLARED` for an explicit cross-session send).
2. If multiple same-root `forge-*` sessions exist, the bridge returns
   `AMBIGUOUS` rather than choosing one. Run from the intended pane or use the
   explicit `--cross-session --target-session <name>` form for `send` or
   `callback`.
3. `.dev/.forge-session` is diagnostics-only. If it names a dead session,
   recreate the Forge session at an approved boundary; do not treat the stale
   marker as a routing instruction.
<!-- docs-refresh:end section=recovery -->

## Codex containment and rollout

The default is `contain`. New panes use the exact-version-gated interactive
binary, currently validated through 0.148.0,
and explicit Never/workspace-write/network-off/empty-extra-roots flags. The
current file credential store means private `CODEX_HOME`, unattended default,
and authentication isolation remain **unproven**; do not enable them merely
because aggregate `codex doctor` exits zero or nonzero. Read the named approval,
filesystem, network, cwd, version, auth, and live-canary fields independently.
The installed `codex-forge.config.toml` is a desired-policy/hash reference; the
interactive client does not load it as an isolated home. `codex-doctor` therefore
also reports the ambient config path and MCP count instead of implying they were
removed. Explicit launch flags define contain mode, while any unknown or changed
ambient surface keeps effectful and unattended gates closed.

Lane matrix: workspace and materialized review may use Codex in contain;
commit and publish require protected broker gates; dependency/browser/network/
live-QA stay reviewed-host. `broker-shadow` validates without effects,
`enforce` enables exact-path commit, and `publish-enabled` separately enables
idempotent review publication. Restart mixed old panes before changing state.

### G-B re-home runbook

1. Freeze deliveries and wait for a terminal callback.
2. Record both physical roots, Git dirs/common dir, branches, full heads,
   ahead/behind, porcelain-v2, staged/unmerged state, delivery IDs, callbacks,
   and locks. Run `forge rehome-audit` and stop on any refusal.
3. Explicitly enumerate handoff artifacts; never copy arbitrary untracked data.
4. Start the destination-root session, verify identity/preflight/health/doctor/
   broker, and run a no-write delivery canary.
5. Terminalize the source only after the exact canary callback, then resume from
   the recorded destination envelope.

`STATUS: needs_permission` is non-terminal. Keep the infra lock and answer in
the pane or explicitly move to the recovery lane; do not widen allowlists.
Rollback first disables effects/publishing, drains the broker, restores any
prepared index backup, preserves journals, restarts at a clean boundary in
contain, and keeps unsupported work on the reviewed host lane.

Inspect the root-scoped broker with `forge codex-broker status --root <worktree>`.
If `status` or `start` reports an uninitialized broker state (including a
parseable `pid.json` without `pid`), run `forge codex-broker start --root
<worktree> --session <live-session> --incarnation <session-created> --mode
contain` from the destination root. `start` validates the live broker lock and
recreates the runtime record; do not repair `pid.json` by hand or use a
`--dry-run` send as a substitute.
Before changing rollout mode, replacing a session incarnation, or completing a
rollback, use `forge codex-broker stop --root <worktree>`; stop is authenticated,
drains claimed work, and prevents a live daemon from being silently reused under
a different session or mode. Its server-authenticated loopback control channel is reachable by the host lane,
not by network-denied Codex panes. A policy-denied lifecycle call may use
`.dev/forge-broker/lifecycle`; only `active-delivery`, `delivery-result`, and
strict `reconcile-delivery` are implemented there. Responses are worker-writable
and advisory. Files under `.dev/forge-broker/requests` and the lifecycle queue
cannot authorize a commit or publication.

Before entering the lifecycle queue, a contained client requires the advertised
`lifecycle_queue: 1` capability and probes the protected `broker.lock` read-only.
Only contention from the daemon's lifetime-exclusive lock makes a queue attempt
viable. This transport check does not replace identity validation: host start,
stop, status, orphan-reaping, and stale-incarnation handling still require the
exact PID/start-stamp checks. Missing, symlinked, inaccessible, unlocked, or
otherwise invalid lock state fails closed to `CONTROL_UNREACHABLE`.

Broker refusal exits are: 3 for ordinary refusal, 4 for CONTROL_UNREACHABLE, 5
for CONTROL_QUEUED, and 6 for DELIVERY_ALREADY_ACTIVE. A callback ending in
`TERMINALIZE_DEFERRED` has closed its pending entry and published its artifact;
the host startup, wait, and next-dispatch paths reconcile it. `TERMINALIZE:
PENDING` or `TERMINALIZE-REFUSED` requires operator inspection.
`CONTROL_QUEUED` is advisory and can occur only after the live exclusive lock
was observed and the bounded lifecycle-response wait expired.

Protected broker state stays under the repository's common Git directory. A
contained pane was measured unable to write there on 2026-08-07 (codex-cli
0.146.1, main checkout and linked worktree); re-run that probe after any Codex
version bump. Verify the resolved location with
`forge-broker paths --root <root> --json`, then confirm `lifecycle_queue: 1` in
status. For an expired abandoned delivery with no valid callback, use the
explicit host command `forge codex-broker reap --root <root> --pane <n>`; it
cancels without artifact evidence and emits `DELIVERY_REAPED`.

Callbacks are accepted only after their delivery-bound artifact matches the
selected pipeline entry. A replayed callback ID, an old callback timestamp, or a
different `delivery_id` is reported as `CALLBACK_IDENTITY_CHANGED`; inspect the
artifact and current Forge log rather than replaying the status line.

## Multi-Worktree Concurrency

You can run more than one worktree of the same repo through a pipeline at the
same time. Each worktree has its own `forge-N` tmux session and its own `.dev/`
state, so the **reasoning stages** (proposal, review, incorporate, implementation,
impl-review) overlap freely. The catch is the **shared infra stack** — fixed-port
services + a shared Postgres — which only one pipeline may touch at a time.

A single cross-worktree **infra lock** enforces this automatically (orchestrator
Hard Rule 23; mechanics in the technical reference). The infra stages
(`coding`, `qa`, `qa-fix`, `qa-retry`, `verify`, `fix-code`, `fix-qa`,
`fix-qa-retry`) acquire the lock before running and release it when the stage
completes. You normally never touch the lock — it is held and released for you.
A fix pipeline and a build pipeline now block each other on these stages; that is
correct, because they share one database. The cases where you *do* see it:

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

## Worker Context Hygiene

The bridge — not you, and not the orchestrator prose — owns worker `/clear`. A worker
pane is reset at safe boundaries only: **before a dispatch** (when its evidence says the
context is dirty or unproven) and at **terminal cleanup** (all four panes, before the
pipeline's single bare `COMPLETE` is published). You never type `/clear` into a worker
pane, and `dispatch --clear` no longer sends a raw clear — it coalesces into the same
confirmed-reset decision.

### Modes

- `enforce` (**default** since 2026-07-23, after the spike + live rollout gate) — the
  accepted steady state. Dirty/unproven panes are reset (with semantic proof) before new
  work; a clean verify closes via `verify-decision` → `finalize`.
- `FORGE_WORKER_HYGIENE_MODE=observe` — the kill switch: every boundary still computes
  and audits its decision (`HYGIENE_DECISION` events) but never blocks, never resets, and
  closes pipelines the legacy way (with a loud `HYGIENE_BYPASSED` alongside each legacy
  completion). Export it to roll the feature back.

A typo'd mode is a hard error, never a silent fall-through.

### Activation preflight (before flipping to enforce)

Run `forge-bridge hygiene-status`. It refuses to advise `enforce` while any of these
exist: an open pending, a verify/DONE callback awaiting interpretation, or nonzero
parked/blocked residue. It also shows each family's reset capability. A fresh install is
fail-closed: `~/.config/forge/reset-capability.yml` ships both families `proven: false`;
flip a family to `proven: true` only after the reset-proof spike + a live gate in a
disposable session proved that family's `/clear` semantics (banner redraw or session-id
change — idle alone never counts).

### Closing a pipeline under enforce

1. `verify` returns DONE → context reads `awaiting-verify-decision` (all worker sends,
   including `--force`, are blocked from here until resolution).
2. `forge-bridge wait` output now includes `CALLBACK_ID:` and `PENDING_TIMESTAMP:` —
   pass them to `forge-bridge verify-decision --slug <s> --callback-id <id>
   --pending-timestamp <ts>`. A CLEAR report with zero residue prints `FINALIZE_READY`;
   with parked/blocked residue it prints `VERIFIED_INCOMPLETE` (resolve each item; the
   last `park --resolve` hands off with `PARK_RESOLVED FINALIZE_READY`).
3. `forge-bridge finalize --slug <s>` — preflights all four panes first (nothing is
   cleared if any pane is busy), clears only panes lacking proven coverage, then
   publishes the completion through a durable outbox. The single bare
   `COMPLETE completion_id=…` is the only green completion signal.

### Retrying finalize

`finalize` is idempotent and crash-safe: every retry converges to the same
`completion_id`, re-uses already-proven resets (no redundant `/clear`), and a partially
published outbox (`outbox-pending`) simply resumes. If it keeps failing, **stop the
retries first**, then decide:

- fix the cause (pane busy, report changed, residue appeared) and retry, or
- `forge-bridge hygiene-abandon --slug <s> --reason "<why>"` — the audited escape hatch.
  It marks the run `cleanup-abandoned` (never "complete"), invalidates every worker's
  cleanliness evidence, and unblocks unrelated work. It refuses once a completion has
  been published.

### Rollback / downgrade

Set `FORGE_WORKER_HYGIENE_MODE=observe` (or unset it). Existing journals are inert;
a missing journal is normal and just means "unproven". Contexts already at
`next_stage: complete` are legacy completions and are not retroactively finalized. If a
downgraded bridge needs to reconstruct context, run the older bridge's
`set-context --slug <slug>` from the owning session. `forge-bridge hygiene-gc` reaps
dead-incarnation terminal journals conservatively (malformed files are always retained).

### Orchestrator-banner flag day

The pane-renumber release is a flag day: stop and restart every live forge session before
dispatching with the new toolkit. Historical `.pN.` attention records retain their original
indices and are not migrated; transient old labels on day-one board rows are expected. Reset
repo-local git `user.name` values to the new worker indices, or accept one stale-ident cycle.
The worker grid occupies the lower 60% of the window, so short terminals may leave each worker
with only three or four visible rows; enlarge the terminal rather than changing the layout.
