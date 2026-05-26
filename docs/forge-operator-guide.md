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

### Starting a pipeline (canonical path)

From pane 1 of your forge session, type:

```
/forge pipeline <slug>
```

What happens:

1. `/forge` confirms `.dev/.forge-session` exists in the cwd; refuses
   otherwise.
2. It assembles a canonical spawn prompt containing `Canonical user
   intent`, `Original user request`, `Project root`, `Tmux session`,
   and the path to the heartbeat event log.
3. It spawns the orchestrator as a background agent named
   `pipeline-<slug>` via `Agent({subagent_type: "forge-orchestrator"})`
   and immediately streams the agent's output with `Monitor`.
4. A second `Monitor` tails the bridge heartbeat event log
   (`.dev/forge-tmp/orchestrator-events.log`), filtered to the active
   slug. `tail -F -n 0` skips backlog so resumes don't replay.
5. The orchestrator runs Hard Rule 0 first (pin session, `export
   TMUX_SESSION`), then `preflight` + `health` (Hard Rule 18), then
   enters Pipeline Mode and dispatches `proposal`.

### Other `/forge` forms

| You type | What happens |
|---|---|
| `/forge pipeline <slug>` | Spawn orchestrator in pipeline mode (above) |
| `/forge resume <slug>` | Spawn orchestrator with the resume preamble (cd, preflight, context, inspect pending callback, resume `wait` or dispatch next) |
| `/forge status` | Local: runs `~/bin/forge-bridge status` and prints verbatim |
| `/forge pause` | Forwards literal `forge-pause` to the active pipeline agent via `SendMessage`. Errors if none active |
| anything else | Treated as an ad-hoc dispatch; forwarded to the active agent if one exists, otherwise spawns an ad-hoc agent |

### Subsequent messages

Once an agent is running, every following message in the same chat is
classified by prefix only:

- `/forge …` — re-enter the grammar above (does NOT spawn a second
  pipeline agent — second `/forge pipeline` errors loudly)
- Other `/…` — handled as that command's own slash command; not
  forwarded
- `local:…` — strip the prefix; handle as a local request in pane 1
- Anything else — forwarded verbatim via `SendMessage({to:
  "pipeline-<slug>", message: "…"})`

### Escape hatch

`/forge-orchestrator` loads the orchestrator body directly into the
current session for manual driving (debugging, one-off stage runs). The
behavioral rules below apply equally to both modes.
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
4. Inspect `.dev/forge-context.yml` for the active stage / worker / wait
   state
5. If a stage is pending callback, resume `forge-bridge wait` for that
   stage. Otherwise, dispatch the next stage per the transition table

`.dev/forge-status.md` is **human-facing display**, not the primary
machine recovery source. Use `forge-bridge context` (Hard Rule 16) to
load the canonical state.

### Skip with care

`forge-skip qa` is allowed but logged with a warning. `forge-skip
verify` is refused — if you really want to skip verify, use `forge-stop`
and re-invoke the next pipeline manually.
<!-- docs-refresh:end section=interrupting-and-resuming -->

## Status and Health

<!-- docs-refresh:start section=status-and-health -->
Five commands cover "what's going on right now":

| Command | When |
|---|---|
| `/forge status` (or `~/bin/forge-bridge status`) | Rolling human-readable summary — pipeline + stage + recent activity + pending callbacks + artifacts + notes |
| `~/bin/forge-bridge context` | Active pipeline + last completed stage + next stage + notes + recent log entries + pending signals — the canonical machine-recovery view |
| `~/bin/forge-bridge health` | Per-pane check: do all 5 panes exist and run the expected worker process? Exits 0 only when every pane is `OK`. Output lines: `OK \| DEAD \| WRONG_PROCESS \| UNKNOWN pane=<name> idx=<n> …` and a `SUMMARY` line |
| `~/bin/forge-bridge preflight` | Kickoff snapshot: pwd, branch, merge state, halt status code |
| `~/bin/forge-bridge history [lines]` / `pipeline-log <slug> [lines]` | Recent activity across all pipelines / detail for one pipeline |

### When to run each

| Situation | First command |
|---|---|
| "What's happening right now?" | `forge-bridge context` |
| "Show me the human summary" | `/forge status` |
| "The orchestrator says a pane is wrong" | `forge-bridge health` |
| "Is this branch safe to dispatch on?" | `forge-bridge preflight` |
| "Where did we leave off last week?" | `forge-bridge context`, then `history 20` |

### Status file mechanics

`.dev/forge-status.md` is auto-maintained by the bridge as a side effect
of `dispatch` / `wait` / `callback`. It summarizes:

- Active pipeline + current stage (or "idle" with last completed)
- Next stage (per the canonical transition table)
- Recent activity (last 15 events: dispatches, completions, blocks)
- Pending callbacks (`response: null` entries)
- Artifacts (files produced so far)
- Notes (from `forge-context.yml`)

`/forge status` surfaces this file verbatim — the orchestrator should
not re-narrate or compress it (forge-status command body).

### Heartbeat event log

`~/.claude/commands/forge.md` starts a second `Monitor` against
`.dev/forge-tmp/orchestrator-events.log` that streams the bridge's
own `_emit_event` lines:

```
DISPATCH | WAIT | CALLBACK | DIGEST | STAGE | STALL | ERROR | COMPLETE: pipeline=<slug> <key=value …>
```

This is filtered with `grep -F 'pipeline=<slug>'` so the user sees only
the active pipeline's events. `tail -F -n 0` skips backlog so resumes
don't replay old events.
<!-- docs-refresh:end section=status-and-health -->

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
4. Wait again with the same args; the next callback resolves it

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

### Multiple forge sessions, wrong pane

Symptom: the orchestrator reads or sends to a pane in a different forge
window than the one you're in.

Cause: the orchestrator did not pin its session (Hard Rule 0 added
2026-05-22), so `forge-bridge` re-resolved on every call via
`require_tmux_session`'s fallback path and picked a different `forge-*`
session.

Fix:

1. The orchestrator should `export TMUX_SESSION=<name>` at session
   start. Verify by inspecting the spawn prompt — it should contain
   `Tmux session: forge-N`.
2. If multiple `forge-*` sessions exist, the bridge now refuses to
   auto-pick and lists the candidates. The error message tells you to
   either `export TMUX_SESSION=<name>` or run from a project dir
   containing `.dev/.forge-session`.
3. If `.dev/.forge-session` refers to a session that no longer exists,
   the bridge errors loudly with the stale-file hint — delete the file
   or run `forge-start` to recreate the session.
<!-- docs-refresh:end section=recovery -->
