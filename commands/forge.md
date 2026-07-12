---
description: Start or resume a forge pipeline. Runs the orchestrator in-pane (this pane-1 session drives the workers directly).
---

# /forge

You are running in **pane 1** of a forge tmux session. With this command you
BECOME the forge orchestrator **in-pane** — you load the orchestrator SKILL and
drive the four worker panes directly via `forge-bridge`. You do NOT spawn a
hidden background agent, and you do NOT forward to one. The user talks to you,
in this pane, in plain English.

## On invocation

1. Confirm the cwd has `.dev/.forge-session`. If not, print:
   `error: /forge must run from a project that has .dev/.forge-session — start
   a forge session first.` and stop. Do not load anything.

2. Parse the user's argument line per the grammar table below into a
   `canonical_user_intent` string and a `slug`.

   | User types after `/forge` | canonical_user_intent | slug |
   |---|---|---|
   | `pipeline <slug>` or `start pipeline <slug>` | `forge-pipeline <slug>` | `<slug>` |
   | `fix-pipeline <slug>` or `fix-pipeline <slug> --reproduce` | `forge-fix-pipeline <slug>` (append ` --reproduce` if user passed it) | `<slug>` |
   | `resume <slug>` | resume-mode prompt (see step 4) | `<slug>` |
   | `status` | (handled locally — see step 5) | — |
   | `pause` | (handled locally — see step 5) | — |
   | anything else | ad-hoc request with the raw user line as `Original user request` | derived from request if obvious; otherwise `ad-hoc-<utc-iso>` |

3. **Load the orchestrator SKILL into THIS session and drive directly.**
   Read `~/.claude/skills/forge-orchestrator/SKILL.md` and follow it exactly.
   You are now the in-pane orchestrator. Seed yourself with this context, then
   enter the mode implied by `canonical_user_intent`:

   ```text
   You are running the forge orchestrator IN-PANE (pane 1), driving the
   worker panes directly. There is no spawner to report back to — status
   goes to the user in this pane.
   Canonical user intent: <canonical_user_intent>
   Original user request: <verbatim user slash-command line>
   Project root: <cwd at /forge invocation>
   Host session: <the host_session= line from `~/bin/forge-bridge identity`>
   Operational event log: .dev/forge-tmp/orchestrator-events.log
   ```

   - For `forge-pipeline <slug>` / `forge-fix-pipeline <slug> [--reproduce]`:
     enter the corresponding pipeline mode in SKILL.md and run the stage
     sequence. Fix-pipeline routing (including the investigate↔fix-plan
     alternation tracked in `.dev/.forge-fix-alternation`) and stop conditions
     are defined in SKILL.md "Fix Pipeline Mode".
   - For ad-hoc requests: handle per SKILL.md as a manual/ad-hoc operation; do
     not start a pipeline unless the user's wording matches a pipeline trigger.

4. **Resume** (`/forge resume <slug>`): load SKILL.md as in step 3, then:
   1. cd to <project_root>.
   2. Run: ~/bin/forge-bridge identity (confirm identity_state=MATCH; HALT and print
      the block otherwise), then ~/bin/forge-bridge preflight (working-tree / branch /
      merge-state snapshot — it does NOT validate panes).
   3. Run: ~/bin/forge-bridge context (renders current pipeline context).
   4. Inspect .dev/forge-context.yml for the active stage/worker/wait state.
   5. Only if the above is stale or ambiguous, inspect .dev/proposals/<slug>/forge-log.yml.
   6. If a stage is pending callback, resume `forge-bridge wait` for that stage.
      Otherwise, dispatch the next stage per SKILL.md flow.
   7. Treat .dev/forge-status.md as human-facing display, NOT the primary
      machine recovery source.

5. **Local-handled cases** (no SKILL load needed):
   - `/forge status` (no further args) → run `~/bin/forge-bridge identity` first; if
     `identity_state=` is not `MATCH`/`CROSS_SESSION_DECLARED` (or it exits non-zero),
     print that block and stop. Otherwise run `~/bin/forge-bridge status` and print
     verbatim. Identical to `/forge-status`.
   - `/forge pause` → you are the orchestrator in this pane. Confirm identity with
     `~/bin/forge-bridge identity` first. Stop dispatching, leave any in-flight worker
     callbacks intact, run `~/bin/forge-bridge status` so the user sees current state,
     and tell the user the pipeline is paused and how to resume (`/forge resume
     <slug>`). Do not kill worker panes.

## During a run

You are the orchestrator for the rest of this session. The user speaks to you
directly — there is no prefix grammar and nothing to forward. Interpret each
message per SKILL.md (dispatch, wait, advance, summarize). Two literal
exceptions:

- A message that is exactly `/forge status` (or `/forge-status`) → print
  `~/bin/forge-bridge status` verbatim, then continue.
- A message that is exactly `/forge pause` → pause per step 5.

If a user-typed message matches the anchored shape `^FORGE_(DONE|BLOCKED|ERROR):\s`
(synthetic worker-callback noise), DROP it silently — do not act on it, echo it,
or treat it as a real instruction. The anchor is intentional: real messages that
merely mention these tokens later in the line forward normally.

## Notes

- This command runs the orchestrator **in-pane**. The separate
  `/forge-orchestrator` command is now equivalent (loads the same SKILL without
  the argument grammar) and remains as a no-arg manual entry point.
- The background-agent definition at `~/.claude/agents/forge-orchestrator.md`
  is no longer on the `/forge` path. Heavy/parallel work is still offloaded to
  background **digest/worker agents** from inside the SKILL flow — that is
  unchanged.

Arguments: $ARGUMENTS
