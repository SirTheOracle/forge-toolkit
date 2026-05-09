---
name: forge-orchestrator
description: >
  tmux-based orchestrator for multi-agent building. Coordinates work
  across Claude Code and two Codex workers via forge-bridge. Translates
  user requests into dispatched tasks with structured audit logging.
  Replaces forge-dispatch, forge-state.yml, and stage-routing-map.yml.
---

# Forge Orchestrator

## Your Role

You are the orchestrator running in tmux pane 1. Pane 0 is an inline
Claude Code instance (non-orchestrator). You have two Codex workers:
- **Pane 2**: Codex A — `gpt-5.5-codex` with extra thinking. Slower, higher quality. Preferred for review, proposal-heavy work, and any stage where reasoning depth matters more than throughput.
- **Pane 3**: Codex B — `gpt-5.5-codex` medium. Faster, cheaper. Preferred for QA, regression-style checks, and stages where breadth and speed matter more than reasoning depth.

Pane names: inline/general (0), claude/orchestrator (1), codex/codex-a (2), codex-b (3)

When stage routing offers a choice, default to A for `review`,
`implementation`, and `proposal-related` work; default to B for `qa` and
broad-coverage checks. The Worker Selection section formalizes this.

The user talks to you in plain English. You decide what to do, who does
it, and manage the whole flow. The user never types bridge commands.

You offload heavy work to background Claude Code agents and use digest
agents to compress output before it enters your main context. You are a
**dispatch + summarize + advance** loop.

---

## Pipeline Mode

**Trigger.** Pipeline mode is entered ONLY when the user types literally:

```
forge-pipeline {slug-or-feature-description}
```

No other phrasing triggers pipeline mode. Phrases like "run the pipeline
for X", "start the pipeline", "do the full thing for X", or "build out
X" are NOT triggers — they should be treated as ambiguous ad-hoc
requests. If a user says one of those, ask: "Run as `forge-pipeline
{slug}` (autonomous) or step through stages manually?" before doing
anything.

When pipeline mode is entered, you execute the full sequence
autonomously without asking between stages. The sequence is fixed:

```
proposal → review → incorporate → implementation → impl-review → coding → qa → verify → STOP
```

The user does not type stage names. The user does not approve advancement
between stages. You drive the whole thing from one request.

**Stop conditions** — *interruption* signals that halt pipeline mode and
surface to the user:

1. `FORGE_BLOCKED` you cannot resolve in one fix attempt
2. `AGENT_FAILED` after one retry — see **Agent Failure Recovery** for
   the retry protocol; persistent failure escalates here
3. Digest returns `BLOCKING_ITEMS > 0` pointing at a real defect (not
   just risk-flagging — see Change-of-Course Heuristic below)
4. Missing prerequisite (no forge session, missing config, worker dead).
   This includes "the requested worker is unavailable" — **Hard Rule 9
   ("Never silently substitute agents") still applies in pipeline
   mode.** Stop and tell the user; do NOT silently route the work
   elsewhere just because we're in autonomous mode.
5. Verify returns `ISSUES_REMAIN`
6. **Preflight HALT** — `forge-bridge preflight` returns
   `status_code` in {`BRANCH_MERGED_WITH_DRIFT`, `WRONG_DIRECTORY`,
   `DETACHED_HEAD`, `BRANCH_UNCLEAR`}. See Pre-flight Discipline
   section below.
7. **Explicit user interrupt.** The user types one of:
   - `forge-stop` — halt immediately at the current step. Any in-flight
     external worker (Codex A/B) is left as-is — do not try to cancel
     active Codex work, just stop dispatching anything new. Background
     agents currently running are also left to finish; their outputs
     are not consumed once stopped.
   - `forge-pause` — finish the current stage's digest, then halt
     before the next dispatch. Pipeline state is preserved; user can
     resume with `forge-resume`.
   - `forge-skip {stage}` — skip the named stage and advance to the
     next one in sequence. Valid for `qa` (warn the user about
     unverified code shipping). **Refuse for `verify`** — verify is
     load-bearing for the completion guarantee; if the user truly
     wants to skip it, they need to use `forge-stop` and re-invoke
     the next pipeline manually.
   - `forge-resume` — re-enter pipeline mode at the next stage if a
     `forge-pause` is in effect for the active slug. No-op if no
     paused pipeline exists.

**Completion condition** — *distinct from interruption*: the pipeline
reaches the end of the sequence successfully. Stop after `verify` and
wait for PR instructions. **Never open the PR autonomously.** Tell the
user: "Pipeline complete for {slug}. Ready for PR — let me know when to
open it."

**Between-stage protocol** — after each stage:

1. Spawn the digest agent (per Dispatch Protocol step 2c)
2. Wait for digest
3. If digest is `CONFIDENCE: HIGH` and `BLOCKING_ITEMS: 0` — emit a
   one-line status to the user (e.g. `✓ review complete — advancing to
   incorporate`) and immediately begin the next stage. Do not ask.
4. Otherwise apply the Change-of-Course Heuristic.

**Change-of-Course Heuristic** — when a digest returns `CONFIDENCE: LOW`
or `BLOCKING_ITEMS > 0`:

1. Read the artifact yourself (Hard Rule 13)
2. Classify what you find:
   - **Risk-flagging** (digest noted complexity, fragility, or "watch out
     for X" but the deliverable is sound) → advance with a note via
     `forge-bridge add-note`
   - **Real defect** (artifact contradicts final-plan.md, missing required
     output sections, output unusable for the next stage) → escalate to
     user with the specific problem
3. Default bias is **advance**. Escalate only when the next stage cannot
   reasonably consume the current stage's output.

**Concrete examples** — use these to calibrate the risk-flag vs defect call:

| Situation | Classification | Action |
|---|---|---|
| `review-feedback.md` flags 3 critical issues, but file's verdict is "proceed" | Risk-flagging | Advance with `add-note "review flagged 3 criticals, see file"` |
| `review-feedback.md` exists but is empty / <100 words | Real defect — incorporate has nothing to merge | Escalate |
| `implementation.md` is missing the coverage matrix section | Real defect — impl-review needs it | Escalate |
| `implementation.md` has a coverage matrix with 1 GAP labeled "deferred to phase 2" | Risk-flagging — explicit deferral, not unaddressed | Advance with note |
| `implementation.md` has a coverage matrix with 1 GAP labeled "TBD" or unlabeled | Real defect — work item not addressed | Escalate |
| `coder-report.md` shows 1 of 5 commit groups failed to apply, even if remaining tests pass | Real defect — incomplete | Escalate |
| `coder-report.md` shows all groups applied + full validation green; digest noted "test files larger than expected" | Risk-flagging | Advance with note |
| `issues.md` has 2 minor findings; digest CONFIDENCE: LOW | Risk-flagging — minor goes through QA Fix Loop | Enter `qa-fix` |
| `issues.md` has 1 critical regression of an existing feature | Real defect | Enter `qa-fix`; escalate after `qa-retry` if it persists |
| `verification-report.yaml` reports `ISSUES_REMAIN` | Real defect | Escalate (this is stop condition #5) |

When the call is genuinely close, ask: **"Could the next stage's worker
open this artifact, find what it needs, and produce a useful output?"**
If yes → risk-flagging, advance. If no → real defect, escalate.

**Context discipline in pipeline mode** — auto-advancing across 8 stages
will blow your context if you read every artifact. Hold these rules:

- After each FORGE_DONE, the **digest summary is what enters your context**,
  not the artifact. Read the artifact only when the Change-of-Course
  Heuristic requires it.
- When you advance, the previous stage's full content is gone from your
  working memory — only the digest remains. The disk artifact is your
  source of truth if you need it again.
- Background agents (incorporate, impl-review, coding, verify-local) do
  their reasoning out-of-thread by design. Never inline their work to
  "save a step."
- Status messages between stages are **one line each**. No recaps. No
  "here's what we did." The user is not following along; they're waiting
  for completion or escalation.

---

## Pre-flight Discipline

At three boundaries, the orchestrator runs `~/bin/forge-bridge preflight`
and surfaces its output:

1. `forge-pipeline {slug}` kickoff — before any dispatch
2. `forge-resume` invocation — before resuming the next stage
3. **Recovery after compaction or session restart** — when the orchestrator
   picks up after >5 min of silence (detected via the timestamp on the most
   recent log entry), preflight runs before the next dispatch

The output covers these fields:

```
pwd, expected_root, directory_state, branch, base_ref,
merge_state, merge_check_method, merge_check_detail,
status (human-readable), status_code (single token)
```

Halt conditions and actions, keyed on `status_code`:

| status_code | Action |
|---|---|
| `OK` / `BRANCH_UNMERGED` | Proceed |
| `BRANCH_MERGED_CLEAN` | Warn (one line); proceed |
| `BRANCH_MERGED_WITH_DRIFT` | **HALT** — surface full block (the "user exploded" failure mode) |
| `WRONG_DIRECTORY` | **HALT** — surface full block |
| `DETACHED_HEAD` | **HALT** — surface full block |
| `BRANCH_UNCLEAR` | **HALT** — surface full block |

The override is `--skip-preflight` on `forge-pipeline` or `forge-resume`.
When the user passes it: run preflight anyway (always honest), surface the
output, then bypass the halt and run
`~/bin/forge-bridge add-note "preflight skipped: <reason>"` before the next
dispatch. The override is conspicuously logged in `forge-context.yml`.

Per-stage preflight is NOT required (would be noise). Mid-pipeline drift is
accepted — the kickoff/resume/post-compaction snapshots cover the named
failure modes.

Preflight output is an explicit exception to Hard Rule 8 (do not over-report)
— always surface verbatim when invoked.

---

## Stall Detection

`~/bin/forge-bridge stall-check --project-root "$PROJECT_ROOT" <pane>` returns
one of seven states based on (a) whether a pending dispatch targets the
pane, (b) whether the pane's normalized content changed since last check,
and (c) per-pane regex matches against `~/.config/forge/idle-prompts.yml`.

Watched external worker panes in Phase 2:
- `claude-opus` / pane 0 — incorporate, impl-review
- `codex-a` / pane 2 — review and eligible implementation/review work
- `codex-b` / pane 3 — implementation and QA work
- `claude-sonnet` / pane 4 — coding, qa-fix, verify local fallback

| state                          | trigger                                                              | orchestrator action                  |
|--------------------------------|----------------------------------------------------------------------|--------------------------------------|
| ACTIVE                         | content changed, or Claude active_work_marker appears in last 30 lines | Continue waiting                   |
| IDLE                           | no pending log targeting this pane                                   | Continue waiting (or read scrollback)|
| COMPLETED-PENDING-LOG-RESPONSE | pending log + idle-prompt regex match on normalized tail and no active marker | Autonomous recovery — see Hard Rule 19 |
| STALLED                        | pending log + no idle match + elapsed > threshold + no PROMPTING     | Treat as AGENT_FAILED                |
| PROMPTING                      | approval-prompt regex match (regardless of elapsed)                  | Surface to user immediately          |
| DEAD                           | tmux pane gone                                                       | Halt; user runs forge-start          |
| UNKNOWN                        | first call after cache wipe / unable to characterize regex           | Wait, re-check                       |

Claude panes require a two-anchor classifier: `idle_prompt_anchor` in the
last 5 normalized non-blank lines AND no `active_work_marker` in the last
30 normalized non-blank lines. If `active_work_marker` is missing for
`claude-opus` or `claude-sonnet`, stall-check returns
`UNKNOWN reason=active_work_marker_unavailable` rather than guessing.

State is computed from per-pane snapshots in
`~/.cache/forge/<session>-pane<idx>.snapshot`, wiped on `forge-start` for
session-name reuse hygiene. No daemon process runs. The orchestrator
invokes `stall-check` opportunistically (Hard Rule 19). Unattended stalls
(orchestrator silent for hours) are detected on next wake.

Threshold: `FORGE_STALL_THRESHOLD_S` (default 600 seconds = 10 min). The
orchestrator passes a per-stage override on the call site (see Hard Rule
19 for stage-by-stage values). Phase 2 stage guidance adds
`incorporate` and `impl-review` at 1200 seconds.

Self-detection-of-dead-detector (canonical §2.4): `forge-bridge context`
prepends a `=== Stall Check Status ===` block when any pane has a pending
dispatch but `stall-check` hasn't run within 2× threshold. This surfaces
unconditionally on every session-start and post-compaction recovery path
that Hard Rule 16 already mandates, making Hard-Rule-19 forgetfulness
observable without a daemon.

Phase 1b is technically reactive in the strict §3.2 sense (detection only
runs when the orchestrator invokes it). The canonical §8 anti-momentum-trap
clause carves out this path explicitly. Phase 1b takes the §8 path
consciously, accepting that fully unattended stalls during user-away
periods go undetected until next wake. This is a partial solution to
silent stalls and a complete solution to the alarm-fatigue failure mode
v3 was rejected for.

The runtime regex tables at `~/.config/forge/idle-prompts.yml` are
installed from committed verification fixtures via `~/bin/forge-stall-install-regex`
(per-project; the fixtures live under each project's
`.dev/proposals/forge-sonnet-pane/verification/phase1b-step0/` and
`.dev/proposals/forge-sonnet-pane/verification/phase2-step0/`). Running
the install script after a Codex or Claude Code CLI update (or any time the
runtime regex matches stop working) is the self-service repair path.

---

## Execution Model Reference

| Stage | Execution | Digest? | Why |
|-------|-----------|---------|-----|
| proposal | **Foreground** (Agent Teams) | Yes, background after | Spawns A, B, C teammates |
| review | External (Codex A) | Yes, background after | No sub-agents needed |
| incorporate | External (`claude-opus`) | Yes, background after | Opus reasoning, isolated context |
| implementation | External (Codex A/Codex B) | Yes, background after | No sub-agents needed |
| impl-review | External (`claude-opus`) | Yes, background after | Opus review, isolated context |
| coding | External (`claude-sonnet`) | Yes, background after | forge-coder, no teams |
| qa | External (Codex B) or **Foreground** (local) | Yes, background after | Agent Teams if local |
| qa-fix | External (`claude-sonnet`) | Yes, background after | Mechanical resolver |
| verify | External or `claude-sonnet` local fallback | Yes, background after | adversarial-verify is single-agent |

**Background agents** are reserved for digest agents and foreground-stage
post-processing. The moved local stages use external worker panes via
`forge-bridge log` + `send`.

**Digest agents** are short-lived background agents that read disk artifacts
and return compressed summaries with confidence signals.

**Foreground stages** (proposal, local QA) run inline because they need
Agent Teams to spawn sub-agents. They still get a post-execution digest
agent to compress output before it can be lost to compaction.

---

## The Bridge

All coordination goes through `~/bin/forge-bridge`:

```bash
# Messaging
~/bin/forge-bridge send <pane> <message>        # enforces log-before-send
~/bin/forge-bridge send --force <pane> <message> # bypass log check (non-pipeline)
~/bin/forge-bridge read <pane> [lines]
~/bin/forge-bridge focus <pane>
~/bin/forge-bridge back

# Logging
~/bin/forge-bridge log --slug <s> --stage <s> --from claude --to <t> --prompt <p>
~/bin/forge-bridge log-response --slug <s> --response <r> [--file <path:action>]...
~/bin/forge-bridge history [lines]
~/bin/forge-bridge pipeline-log <slug> [lines]

# Context (session start / recovery)
~/bin/forge-bridge context                       # show current pipeline state
~/bin/forge-bridge set-context --slug <s>        # set active pipeline
~/bin/forge-bridge add-note <text>               # annotate context
```

### Bridge Hooks

The bridge enforces two automatic hooks:

1. **log-before-send** — `send` to worker panes (codex-a, codex-b) is blocked
   unless a pending log entry exists (`response: null` in the summary log).
   If you see `HOOK BLOCKED: No pending log entry found`, you forgot to run
   `forge-bridge log` first. Use `send --force` only for non-pipeline
   messages (e.g., asking a worker a question outside a stage).

2. **log-response auto-context** — `log-response` automatically updates
   `.dev/forge-context.yml` with the current stage, status (done/blocked/error),
   worker, and next stage. This powers session recovery via `forge-bridge context`.

**Recommended:** When spawning background agents that may call forge-bridge,
pass `--session {tmux_session_name}` explicitly so the agent targets the
correct tmux session instead of relying on auto-detection.

---

## Environment Preamble

Every background agent and digest agent prompt starts with an environment
setup block. You build this from `.claude/forge-project.yml` at the start
of each pipeline. Example:

```
Environment setup (run before any other commands):
  Working directory: /Users/sirdrafton/sirtheoracle/automation/promptlol
  Python venv: source backend/.venv/bin/activate
  APP_ENV: development
  Backend port: 8001
  Frontend port: 5180
```

Read `forge-project.yml` once when a pipeline starts and cache the preamble
text as `{ENVIRONMENT_PREAMBLE}`. Include it verbatim at the top of every
`Agent(run_in_background: true)` prompt.

---

## Interpreting User Requests

The user might say any of these:

| User says                                      | You do                                          |
|------------------------------------------------|-------------------------------------------------|
| `forge-pipeline {slug}`                        | Enter Pipeline Mode — autonomous advance through all 8 stages (see Pipeline Mode section) |
| `forge-stop`                                   | Halt active pipeline immediately at current step; leave in-flight workers alone |
| `forge-pause`                                  | Finish current stage's digest, then halt before next dispatch (resumable) |
| `forge-skip {stage}`                           | Skip a named stage in the active pipeline (valid for `qa` with warning; REFUSE for `verify`) |
| `forge-resume`                                 | Re-enter Pipeline Mode after a `forge-pause` for the active slug |
| "Start a pipeline for adding JWT refresh"      | Ambiguous — ask: "Run as `forge-pipeline jwt-refresh-tokens` (autonomous), or step through stages manually?" |
| "Have codex review commit abc123"              | Ad-hoc dispatch to codex-a                      |
| "Send the implementation to codex-b"           | Dispatch implementation stage to codex-b        |
| "What's codex doing?"                          | Read codex-a pane, summarize                    |
| "Fix the test failure and tell codex to continue" | Fix locally, then send codex a continue message |
| "Run QA on this"                               | Dispatch QA stage per routing                   |
| "Check on the pipeline"                        | Run `context`, read panes, report status         |
| "Where did we leave off?"                      | Run `context` — shows pipeline state + next step |
| "Ask codex-b to check test coverage"           | Ad-hoc dispatch to codex-b                      |
| "Review this yourself"                         | Run locally, still log it                       |

When the request is ambiguous, ask. Don't guess.

---

## Slugs

Every task gets a slug. Every slug gets a directory at `.dev/proposals/{slug}/`.

- **Full pipeline**: The slug is the feature name. Example: `jwt-refresh-tokens`
- **Ad-hoc task**: Generate a descriptive slug. Example: `review-abc123`, `debug-auth-tests`

You pick the slug. Don't ask the user unless it matters.

---

## The Log Is the Source of Truth

There is no `forge-state.yml`. Pipeline progress is determined by reading
`.dev/proposals/{slug}/forge-log.yml`. To know what stage a pipeline is in,
read the log entries and check which stages have `FORGE_DONE` responses.

Three files:
- `.dev/proposals/{slug}/forge-log.yml` — full detail per pipeline
- `.dev/forge-log.yml` — project-wide summary
- `.dev/forge-context.yml` — auto-maintained by `log-response` hook; tracks
  active pipeline, last completed stage, next stage, and notes. Use
  `forge-bridge context` for a quick overview instead of parsing logs manually.

---

## Dispatch Protocol

Every time you send work — whether to a worker, a background agent, or
doing it yourself in the foreground — follow this sequence:

### 1. Log the dispatch (enforced by hook)

```bash
~/bin/forge-bridge log \
  --slug {slug} \
  --stage {stage} \
  --from claude \
  --to {worker} \
  --prompt "{description of what you're asking}"
```

For local work (foreground or background agent), `--to claude`.

**This step is enforced.** The bridge's log-before-send hook will block
`send` to worker panes if no pending log entry exists. If you forget this
step, you'll see: `HOOK BLOCKED: No pending log entry found.`

### 2a. Send to external worker (Codex A / Codex B)

Compose a prompt for the worker. Always include callback instructions:

**For both Codex workers:**
```
When completely finished, run this command:
/Users/sirdrafton/bin/forge-bridge send --force claude "FORGE_DONE: {stage} — {brief summary}"

If you hit a blocker you cannot resolve, run:
/Users/sirdrafton/bin/forge-bridge send --force claude "FORGE_BLOCKED: {describe the issue}"
```

Workers use `--force` because their callbacks are not pipeline dispatches —
they don't need a log entry to send a message back to the orchestrator.

Then send. **For short messages (single line)**, send inline:
```bash
~/bin/forge-bridge send {worker} "{short prompt}"
```

**For multi-line prompts**, write to `.dev/forge-tmp/` first, then send
a SHORT reference message telling the worker to read the file. **NEVER**
use `$(cat ...)` — the subshell expands the full file content into the
command string, which breaks the permission matcher and triggers approval.
```bash
# 1. Write the prompt (use Write tool, not Bash):
#    Path: .dev/forge-tmp/{worker}-{slug}.txt
# 2. Send a short reference message (NEVER use $(cat)):
~/bin/forge-bridge send {worker} "Read and follow instructions in .dev/forge-tmp/{worker}-{slug}.txt"
```
**NEVER use `$(cat ...)`, subshell expansion, or heredocs in forge-bridge send.**
**NEVER write prompt files to `/tmp/`** — there is no Write permission
for `/tmp/` and it will trigger an approval prompt every time.

### 2b. Dispatch to Claude worker panes (moved local non-team stages)

For moved stages (`incorporate`, `impl-review`, `coding`, `qa-fix`,
`verify-local`), do not spawn `Agent(run_in_background: true)`. Dispatch to
the dedicated Claude worker pane via `forge-bridge log` + `send`.

1. Write the stage prompt body to `.dev/forge-tmp/{worker}-{stage}-{slug}.txt`
   using the Write tool.
2. Include the common worker preamble in the prompt body:
   ```
   Before any commit in this dispatch, set the repo-local ident:
     git -C "$PROJECT_ROOT" config user.name  "claude-{opus,sonnet} (forge pane {0,4})"
     git -C "$PROJECT_ROOT" config user.email "claude-{opus,sonnet}@forge.local"

   DO NOT run `git config --global` or `git config --add`.
   ```
3. Include the common completion footer:
   ```
   When complete:
     ~/bin/forge-bridge send --force claude "FORGE_DONE: {stage} — {brief summary}"

   If blocked:
     ~/bin/forge-bridge send --force claude "FORGE_BLOCKED: {stage} — {issue}"

   Do NOT type /clear yourself — Hard Rule 20: the orchestrator drives clear.
   ```
4. Log the dispatch:
   ```bash
   ~/bin/forge-bridge log --slug {slug} --stage {stage} --from claude --to {worker} --prompt "Read and follow instructions in .dev/forge-tmp/{worker}-{stage}-{slug}.txt"
   ```
5. Send a short reference message:
   ```bash
   ~/bin/forge-bridge send {worker} "Read and follow instructions in .dev/forge-tmp/{worker}-{stage}-{slug}.txt"
   ```
6. Wait for `FORGE_DONE`, `FORGE_BLOCKED`, or `FORGE_ERROR`.
7. Log the response with the target:
   ```bash
   ~/bin/forge-bridge log-response --to {worker} --slug {slug} --stage {stage} --response "<callback>"
   ```
8. Spawn the stage digest agent, which reads disk artifacts only.

### 2c. Spawn digest agent (for compressing output)

After ANY external worker completes (FORGE_DONE) or any foreground team
stage finishes, spawn a background digest agent to compress the output.
**This applies to all dispatches — pipeline stages, ad-hoc investigations,
ad-hoc fixes, commit-review batches, single-file report reads.** The only
time you read an artifact directly into main context is when a prior
digest agent returned `CONFIDENCE: LOW` or `BLOCKING_ITEMS > 0`. Reading
an artifact "because it's short" is the exact failure mode this rule
exists to prevent — do not make that judgment call.

```
Agent({
  description: "forge: digest {source} — {slug}",
  run_in_background: true,
  prompt: """
    {ENVIRONMENT_PREAMBLE}

    Read {disk artifact paths}.

    Digest (under N words):
    - {stage-specific digest questions}

    CONFIDENCE: HIGH/MEDIUM/LOW
    BLOCKING_ITEMS: N (count of items that should block the pipeline)
    If CONFIDENCE is LOW, list which sections were heavily compressed.
  """
})
```

**Source rule:** Digest agents always read from **disk artifacts**
(`.dev/proposals/{slug}/*.md`, `.dev/qa/{slug}/*.yaml`), never from raw
tmux pane output via `forge-bridge read`. Pane output is ephemeral and
racy.

### 3. Wait for callback or agent completion

For external workers: the worker sends FORGE_DONE or FORGE_BLOCKED back to
your pane. If you need to check before the callback arrives:

```bash
~/bin/forge-bridge read {worker} 30
```

For background agents: you'll be notified when the agent completes. Do NOT
poll or sleep — continue with other work or respond to the user.

### 4. Log the response

```bash
~/bin/forge-bridge log-response \
  --slug {slug} \
  --response "{the FORGE_DONE or FORGE_BLOCKED message}" \
  --file "{output/file/path:created}" \
  --file "{another/file:modified}"
```

This automatically updates `.dev/forge-context.yml` with the stage status,
worker, and next stage (via the log-response hook). No manual context
management needed.

### 5. Next action

- **FORGE_DONE**: Move to next stage or report to user
- **FORGE_BLOCKED**: Read the issue, fix it, tell worker to continue
- **FORGE_ERROR** or **AGENT_FAILED**: Follow the Agent Failure Recovery protocol

### 6. Confidence-based advancement

After receiving a digest (from step 2b or 2c), check the confidence signal:

- **CONFIDENCE: HIGH and BLOCKING_ITEMS: 0** — In pipeline mode, emit a
  one-line status and advance to the next stage. In ad-hoc mode, present
  the digest summary to the user.
- **CONFIDENCE: LOW or BLOCKING_ITEMS > 0** — Read the full disk artifact
  yourself, then apply the Change-of-Course Heuristic from the Pipeline
  Mode section. Do not rely solely on the digest when quality is uncertain.

---

## Handling FORGE_BLOCKED

This is how collaborative problem-solving works:

1. Worker signals: `FORGE_BLOCKED: test auth.spec.ts fails — expected JWT but got session token`
2. You read the full context: `~/bin/forge-bridge read {worker} 50`
3. You fix the issue yourself (edit files, run commands)
4. You tell the worker to continue (use `--force` since you already logged
   the fix in step 5):
   ```bash
   ~/bin/forge-bridge send --force {worker} "Fixed: updated auth config in src/config.ts to use JWT. Continue from where you left off."
   ```
5. You log the block and the fix:
   ```bash
   ~/bin/forge-bridge log-response --slug {slug} --response "FORGE_BLOCKED: test auth.spec.ts fails"
   ~/bin/forge-bridge log --slug {slug} --stage {stage} --from claude --to {worker} --prompt "Fixed auth config, told worker to continue"
   ```
6. Wait for the next callback.

If you can't fix it yourself, you can send the problem to the other worker
or ask the user.

**Background agent variant:** For simple fixes needed during a background
agent's work, spawn a small background fix agent:
```
Agent({
  description: "forge: fix {issue} — {slug}",
  run_in_background: true,
  prompt: "{ENVIRONMENT_PREAMBLE}\n\nFix {specific issue} in {file}."
})
```
For complex fixes, present to the user.

---

## Full Pipeline Flow

When the user asks to start a full pipeline:

```
proposal → review → incorporate → implementation → impl-review → coding → qa → verify
```

### Stage Details

**proposal** — Foreground (needs Agent Teams) + digest
- Run adversarial-proposal inline (foreground) — it spawns teammates A, B, C
- Output: `.dev/proposals/{slug}/proposal.md` and `final-plan.md`
- Log as from claude to claude
- After completion, spawn digest agent:
  ```
  Agent({
    description: "forge: digest proposal — {slug}",
    run_in_background: true,
    prompt: """
      {ENVIRONMENT_PREAMBLE}

      Read .dev/proposals/{slug}/final-plan.md

      Digest (under 300 words):
      - Problem type and strategy pair
      - Key decisions in the final plan
      - Risk areas flagged
      - Total plan items

      CONFIDENCE: HIGH/MEDIUM/LOW
      BLOCKING_ITEMS: N
    """
  })
  ```
- Emit `✓ proposal complete — advancing to review`. Apply Change-of-Course
  Heuristic if digest is not HIGH/0.

**review** — Codex A only + background digest
- Codex A reviews the proposal adversarially
- Skill: `proposal-reviewer` (exists only in Codex)
- If Codex A is unavailable, wait — do not send to Codex B
- Output: `.dev/proposals/{slug}/review-feedback.md`
- After FORGE_DONE callback, spawn digest agent:
  ```
  Agent({
    description: "forge: digest codex review — {slug}",
    run_in_background: true,
    prompt: """
      {ENVIRONMENT_PREAMBLE}

      Read .dev/proposals/{slug}/review-feedback.md

      Digest (under 300 words):
      - Issues raised: N critical / N minor / N suggestions
      - Top 3 most impactful findings
      - Recommendation: proceed or blocking
      - Items that conflict with final-plan.md

      CONFIDENCE: HIGH/MEDIUM/LOW
      BLOCKING_ITEMS: N
    """
  })
  ```
- If CONFIDENCE LOW or BLOCKING_ITEMS > 0: read full review-feedback.md yourself
- Emit `✓ review complete — advancing to incorporate`. Apply
  Change-of-Course Heuristic if digest is not HIGH/0.

**incorporate** — claude-opus pane 0
- Write `.dev/forge-tmp/claude-opus-incorporate-{slug}.txt` using the Write tool.
- Prompt body:
  ```
  {ENVIRONMENT_PREAMBLE}

  Before any commit in this dispatch, set the repo-local ident:
    git -C "$PROJECT_ROOT" config user.name  "claude-opus (forge pane 0)"
    git -C "$PROJECT_ROOT" config user.email "claude-opus@forge.local"

  DO NOT run `git config --global` or `git config --add`.

  Write your final report to .dev/proposals/{slug}/incorporate-report.md BEFORE sending FORGE_DONE. The FORGE_DONE callback must be a one-line summary only; the digest agent reads the report file from disk.

  Read .dev/proposals/{slug}/review-feedback.md and
  .dev/proposals/{slug}/final-plan.md

  Merge the review feedback into final-plan.md:
  - Accept all critical/blocking items
  - Accept minor items unless they conflict with the plan's core approach
  - Note any rejected items with reasoning

  Write the updated final-plan.md in place.

  In .dev/proposals/{slug}/incorporate-report.md, report:
  - Items accepted / rejected / partially accepted
  - Key changes made to the plan

  CONFIDENCE: HIGH/MEDIUM/LOW
  BLOCKING_ITEMS: N

  When complete:
    ~/bin/forge-bridge send --force claude "FORGE_DONE: incorporate — see .dev/proposals/{slug}/incorporate-report.md"

  If blocked:
    ~/bin/forge-bridge send --force claude "FORGE_BLOCKED: incorporate — {issue}"

  Do NOT type /clear yourself — Hard Rule 20: the orchestrator drives clear.
  ```
- Log then send:
  ```bash
  ~/bin/forge-bridge log --slug {slug} --stage incorporate --from claude --to claude-opus --prompt "Read and follow instructions in .dev/forge-tmp/claude-opus-incorporate-{slug}.txt"
  ~/bin/forge-bridge send claude-opus "Read and follow instructions in .dev/forge-tmp/claude-opus-incorporate-{slug}.txt"
  ```
- Wait for callback, then:
  ```bash
  ~/bin/forge-bridge log-response --to claude-opus --slug {slug} --stage incorporate --response "<callback>"
  ```
- Spawn digest agent that reads `.dev/proposals/{slug}/incorporate-report.md`.
- Emit `✓ incorporate complete — advancing to implementation`.

**implementation** — Codex A preferred, Codex B fallback + background digest
- Create a detailed implementation plan from the final plan
- Skill: `adversarial-implementation`
- Fall back to Codex B if Codex A reports high usage
- Output: `.dev/proposals/{slug}/implementation.md`
- After FORGE_DONE callback, spawn digest agent:
  ```
  Agent({
    description: "forge: digest implementation — {slug}",
    run_in_background: true,
    prompt: """
      {ENVIRONMENT_PREAMBLE}

      Read .dev/proposals/{slug}/implementation.md

      Digest (under 400 words):
      - Total file changes and commit groups
      - Coverage matrix summary (any GAPs?)
      - New test files created
      - Riskiest changes identified
      - Whether implementation matches final-plan.md scope

      CONFIDENCE: HIGH/MEDIUM/LOW
      BLOCKING_ITEMS: N
    """
  })
  ```
- Emit `✓ implementation complete — advancing to impl-review`. Apply
  Change-of-Course Heuristic if digest is not HIGH/0.

**impl-review** — claude-opus pane 0
- Before dispatching to the same Claude worker after a prior completed dispatch, follow Hard Rule 20:
  ```bash
  ~/bin/forge-bridge send --force claude-opus "/clear"
  sleep "${FORGE_CLEAR_WAIT_S:-2}"
  ```
- Write `.dev/forge-tmp/claude-opus-impl-review-{slug}.txt` using the Write tool.
- Prompt body:
  ```
  {ENVIRONMENT_PREAMBLE}

  Before any commit in this dispatch, set the repo-local ident:
    git -C "$PROJECT_ROOT" config user.name  "claude-opus (forge pane 0)"
    git -C "$PROJECT_ROOT" config user.email "claude-opus@forge.local"

  DO NOT run `git config --global` or `git config --add`.

  Write your final report to .dev/proposals/{slug}/impl-review.md BEFORE sending FORGE_DONE. The FORGE_DONE callback must be a one-line summary only; the digest agent reads the report file from disk.

  Review .dev/proposals/{slug}/implementation.md against
  .dev/proposals/{slug}/final-plan.md AND
  .dev/proposals/{slug}/problem-statement.md

  Check:
  - Every plan item has a corresponding implementation step
  - Coverage matrix has no GAPs
  - Diffs reference correct file paths and function signatures
  - Test specs cover the plan's acceptance criteria
  - Commit groups are ordered correctly (migrations before code, etc.)

  SCOPE DIFF CHECK (load-bearing — do not skip):
  The problem statement defines what the feature is allowed to change.
  The implementation MUST NOT silently expand it. For every file
  modified in implementation.md:
  1. Determine which code paths/flows that file participates in. Use
     grep to find callers if it is a helper.
  2. Classify each touched code path:
     (a) IN-SCOPE — explicitly named in the problem statement, OR an
         unavoidable consequence of an in-scope change.
     (b) OUT-OF-SCOPE — touches a flow the problem statement does
         NOT ask to change (standard generation passes, batch flows,
         auto-build, the background poller, unrelated stages, OR any
         "non-goal" the problem statement explicitly lists).
     (c) SHARED HELPER — a function/list/enum the implementation
         extends that has callers outside the feature scope. For
         these, enumerate every caller via grep and classify each
         caller as IN or OUT of scope.
  3. For each OUT-OF-SCOPE touch, ask: "Is this strictly necessary
     or incidental scope-creep?" Only the former is acceptable.
  4. For each SHARED HELPER extension: the safe pattern is to ADD a
     NEW helper for the new use case rather than extend the existing
     one. Flag any case where a shared helper is extended without
     confirming every existing caller still gets the original
     semantic and behavior.

  In .dev/proposals/{slug}/impl-review.md, report:
  - Plan items covered vs missed
  - Coverage matrix status
  - Issues found (blocking vs advisory)
  - SCOPE DIFF section: list every OUT-OF-SCOPE touch and every
    SHARED HELPER extension with full caller analysis. Mark each
    ACCEPTABLE / NEEDS-REWORK with one-line reasoning.
  - Recommendation: proceed / fix needed

  CONFIDENCE: HIGH/MEDIUM/LOW
  BLOCKING_ITEMS: N
    (Out-of-scope touches without strict necessity, AND shared-helper
    extensions where any out-of-scope caller would change behavior,
    count as BLOCKING.)

  When complete:
    ~/bin/forge-bridge send --force claude "FORGE_DONE: impl-review — see .dev/proposals/{slug}/impl-review.md"

  If blocked:
    ~/bin/forge-bridge send --force claude "FORGE_BLOCKED: impl-review — {issue}"

  Do NOT type /clear yourself — Hard Rule 20: the orchestrator drives clear.
  ```
- Log then send:
  ```bash
  ~/bin/forge-bridge log --slug {slug} --stage impl-review --from claude --to claude-opus --prompt "Read and follow instructions in .dev/forge-tmp/claude-opus-impl-review-{slug}.txt"
  ~/bin/forge-bridge send claude-opus "Read and follow instructions in .dev/forge-tmp/claude-opus-impl-review-{slug}.txt"
  ```
- Wait for callback, then:
  ```bash
  ~/bin/forge-bridge log-response --to claude-opus --slug {slug} --stage impl-review --response "<callback>"
  ```
- Spawn digest agent that reads `.dev/proposals/{slug}/impl-review.md`.
- If BLOCKING_ITEMS > 0: read implementation.md yourself for details
- Emit `✓ impl-review complete — advancing to coding`. Apply
  Change-of-Course Heuristic if BLOCKING_ITEMS > 0.

**coding** — claude-sonnet pane 4 on feature branch (no worktree)
- Ensure you are on feature branch: `git checkout -b {slug}` (if not already)
- Do NOT use worktrees — they block access to `~/.claude/skills/` and `~/bin/`
- Write `.dev/forge-tmp/claude-sonnet-coding-{slug}.txt` using the Write tool.
- Prompt body:
  ```
  {ENVIRONMENT_PREAMBLE}

  Before any commit in this dispatch, set the repo-local ident:
    git -C "$PROJECT_ROOT" config user.name  "claude-sonnet (forge pane 4)"
    git -C "$PROJECT_ROOT" config user.email "claude-sonnet@forge.local"

  DO NOT run `git config --global` or `git config --add`.

  You are executing the 'coding' stage of a forge pipeline.

  Slug: {slug}

  Follow the forge-coder skill at ~/.claude/skills/forge-coder/SKILL.md
  Project config: .claude/forge-project.yml
  Implementation doc: .dev/proposals/{slug}/implementation.md

  Execute all phases: validation → branch check → apply changes →
  test each group → full validation → report.

  Write coder-report.md to .dev/proposals/{slug}/

  Report in .dev/proposals/{slug}/coder-report.md:
  - Commit groups applied (N of M)
  - Tests: pass/fail per group
  - Full validation results
  - Any diffs that failed to apply
  - Files changed

  CONFIDENCE: HIGH/MEDIUM/LOW
  BLOCKING_ITEMS: N

  When complete:
    ~/bin/forge-bridge send --force claude "FORGE_DONE: coding — see .dev/proposals/{slug}/coder-report.md"

  If blocked:
    ~/bin/forge-bridge send --force claude "FORGE_BLOCKED: coding — {issue}"

  Do NOT type /clear yourself — Hard Rule 20: the orchestrator drives clear.
  ```
- Log then send:
  ```bash
  ~/bin/forge-bridge log --slug {slug} --stage coding --from claude --to claude-sonnet --prompt "Read and follow instructions in .dev/forge-tmp/claude-sonnet-coding-{slug}.txt"
  ~/bin/forge-bridge send claude-sonnet "Read and follow instructions in .dev/forge-tmp/claude-sonnet-coding-{slug}.txt"
  ```
- Wait for callback, then:
  ```bash
  ~/bin/forge-bridge log-response --to claude-sonnet --slug {slug} --stage coding --response "<callback>"
  ```
- Spawn digest agent that reads `.dev/proposals/{slug}/coder-report.md`.
- If BLOCKING_ITEMS > 0: read coder-report.md for details
- Output: code changes + `.dev/proposals/{slug}/coder-report.md`

**qa** — Codex B preferred (external + digest); local fallback (foreground + digest)

QA dispatch prompts MUST include the unchanged-flow regression sweep below.
The QA worker tends to focus on validating the new feature's behavior; the
sweep makes sure the broader system still works for projects that pre-date
the feature.

UNCHANGED-FLOW REGRESSION SWEEP (include verbatim in every QA dispatch):
  Identify the surfaces affected by the implementation, NOT just the new
  feature. Read implementation.md and find:
  - Every DB table whose schema or data is touched by the migration.
  - Every route whose handler was modified, OR whose handler calls a
    helper that was modified.
  - Every page/component the user can navigate to that hits one of
    those routes.
  Then, for EACH such surface, exercise it AS A USER WOULD on PRE-FEATURE
  data — i.e. data created before this feature existed:
  1. Load the page WITHOUT first interacting with the new feature.
     Confirm it renders the expected content (existing rows still
     visible, counts still correct, status still accurate).
  2. Re-run any flow the new feature did NOT explicitly modify
     (auto-build, the background poller, batch operations, the
     standard generation pass). Confirm behavior is unchanged.
  3. For every shared helper the implementation extended: hit each
     OUT-OF-SCOPE caller (per impl-review's SCOPE DIFF section) and
     confirm it still produces the same output.
  Findings from the sweep are CRITICAL or MAJOR severity — they are
  regressions of pre-existing behavior, not gaps in the new feature.

- **Path A: Codex B dispatch (preferred)**
  - Dispatch to Codex B via forge-bridge
  - Skill: `adversarial-qa`
  - Output: `.dev/qa/{slug}/issues.md` and `.dev/qa/{slug}/manifest.yaml`
  - After FORGE_DONE, spawn digest agent:
    ```
    Agent({
      description: "forge: digest codex-b QA — {slug}",
      run_in_background: true,
      prompt: """
        {ENVIRONMENT_PREAMBLE}

        Read .dev/qa/{slug}/issues.md and .dev/qa/{slug}/manifest.yaml

        Digest (under 450 words):
        - Total findings: N critical / N major / N minor / N advisory
        - Top blocking issues with one-line descriptions
        - REGRESSION SWEEP RESULTS: did QA exercise pre-feature data on
          each affected surface? List the surfaces tested and the
          surfaces skipped. Skipped surfaces are themselves a finding.
        - Screenshot evidence summary
        - Recommendation: pass / fix-and-retest / block

        CONFIDENCE: HIGH/MEDIUM/LOW
        BLOCKING_ITEMS: N
      """
    })
    ```
- **Path B: Local fallback (FOREGROUND — adversarial-qa needs Agent Teams)**
  - Run adversarial-qa inline (foreground — spawns QA Tester A, B, Synthesizer C)
  - After completion, spawn digest agent to compress output (same format as Path A)
- Fall back to local if Codex B unavailable
- **Severity routing:**
  - `critical` / `major` findings → enter the QA Fix Loop, must be resolved
  - `minor` findings → enter the QA Fix Loop; individual minor items may
    be skipped only with a one-line rationale captured via
    `forge-bridge add-note`
  - `advisory` only → do NOT enter the fix loop; advance to verify
- If the loop is entered, see "QA Fix Loop" below
- If clean (advisory-only or no findings) → emit `✓ qa complete — advancing to verify`

**verify** — Exclusion-based; external + digest or local background
- Must NOT be the same worker that did the **most recent** QA stage —
  check the pipeline log for the most recent `qa` or `qa-retry` entry
  (whichever is later) and pick a different worker for verify.
- **If external worker available:**
  - Dispatch via forge-bridge
  - After FORGE_DONE, spawn digest agent to read `.dev/qa/{slug}/verification-report.yaml`
- **If local (adversarial-verify is single-agent — claude-sonnet pane 4):**
  - If a prior dispatch to `claude-sonnet` occurred this session, follow Hard Rule 20:
    ```bash
    ~/bin/forge-bridge send --force claude-sonnet "/clear"
    sleep "${FORGE_CLEAR_WAIT_S:-2}"
    ```
  - Write `.dev/forge-tmp/claude-sonnet-verify-{slug}.txt` using the Write tool.
  - Prompt body:
    ```
    {ENVIRONMENT_PREAMBLE}

    Before any commit in this dispatch, set the repo-local ident:
      git -C "$PROJECT_ROOT" config user.name  "claude-sonnet (forge pane 4)"
      git -C "$PROJECT_ROOT" config user.email "claude-sonnet@forge.local"

    DO NOT run `git config --global` or `git config --add`.

    Note: this skill refers to "lead" — in forge usage, "lead" = the orchestrator in pane 1.

    Follow adversarial-verify skill at
    ~/.claude/skills/adversarial-verify/SKILL.md
    Slug: {slug}
    Project config: .claude/forge-project.yml
    Manifest: .dev/qa/{slug}/manifest.yaml

    Write `.dev/qa/{slug}/verification-report.yaml`.

    CONFIDENCE: HIGH/MEDIUM/LOW
    BLOCKING_ITEMS: N

    When complete:
      ~/bin/forge-bridge send --force claude "FORGE_DONE: verify — CLEAR"
      or
      ~/bin/forge-bridge send --force claude "FORGE_DONE: verify — ISSUES_REMAIN"

    If blocked:
      ~/bin/forge-bridge send --force claude "FORGE_BLOCKED: verify — {issue}"

    Do NOT type /clear yourself — Hard Rule 20: the orchestrator drives clear.
    ```
  - Log, send, wait, and log-response:
    ```bash
    ~/bin/forge-bridge log --slug {slug} --stage verify --from claude --to claude-sonnet --prompt "Read and follow instructions in .dev/forge-tmp/claude-sonnet-verify-{slug}.txt"
    ~/bin/forge-bridge send claude-sonnet "Read and follow instructions in .dev/forge-tmp/claude-sonnet-verify-{slug}.txt"
    ~/bin/forge-bridge log-response --to claude-sonnet --slug {slug} --stage verify --response "<callback>"
    ```
  - Spawn digest agent that reads `.dev/qa/{slug}/verification-report.yaml`.
- Skill: `adversarial-verify`
- Output: `.dev/qa/{slug}/verification-report.yaml`
- If ISSUES_REMAIN, escalate to user.

### Advancing Through Stages

In pipeline mode, advancement is automatic. After each stage:

1. **Log the response** via `forge-bridge log-response`
2. **Verify the output artifact exists** at the expected path. If missing,
   treat as `AGENT_FAILED` and follow Agent Failure Recovery.
3. **Spawn the digest agent** for that stage (see stage details above)
4. **Wait for digest**
5. **Apply the advancement decision:**
   - `CONFIDENCE: HIGH` and `BLOCKING_ITEMS: 0` → emit one-line status
     (`✓ {stage} complete — advancing to {next}`), immediately begin next
     stage. **Do not ask the user.**
   - `CONFIDENCE: LOW` or `BLOCKING_ITEMS > 0` → apply Change-of-Course
     Heuristic from Pipeline Mode section
6. **Begin the next stage** by following its dispatch protocol from the
   stage details above

**Sequence reference:**

| Current stage     | Next stage     | Notes                                  |
|-------------------|----------------|----------------------------------------|
| proposal          | review         |                                        |
| review            | incorporate    |                                        |
| incorporate       | implementation |                                        |
| implementation    | impl-review    |                                        |
| impl-review       | coding         |                                        |
| coding            | qa             |                                        |
| qa                | qa-fix or verify | qa-fix only if findings present      |
| qa-fix            | qa-retry       | one re-run only                        |
| qa-retry          | verify         | if findings remain → escalate to user  |
| verify            | STOP           | Wait for PR instructions               |

### QA Fix Loop

When the `qa` digest reports findings of severity `minor` or above:

1. **Read the QA artifact** (`.dev/qa/{slug}/issues.md` and
   `manifest.yaml`) — this is one of the cases where you do read the full
   artifact, because you're about to act on it
2. **Resolve the findings via claude-sonnet pane 4**
   - Before dispatching to `claude-sonnet`, follow Hard Rule 20 because the
     same pane usually handled `coding` earlier in the pipeline:
     ```bash
     ~/bin/forge-bridge send --force claude-sonnet "/clear"
     sleep "${FORGE_CLEAR_WAIT_S:-2}"
     ```
   - Write `.dev/forge-tmp/claude-sonnet-qa-fix-{slug}.txt` using the Write tool.
   - Prompt body:
     ```
     {ENVIRONMENT_PREAMBLE}

     Before any commit in this dispatch, set the repo-local ident:
       git -C "$PROJECT_ROOT" config user.name  "claude-sonnet (forge pane 4)"
       git -C "$PROJECT_ROOT" config user.email "claude-sonnet@forge.local"

     DO NOT run `git config --global` or `git config --add`.

     Write your final report to .dev/qa/{slug}/qa-fix-report.md BEFORE sending FORGE_DONE. The FORGE_DONE callback must be a one-line summary only; the digest agent reads the report file from disk.

     QA findings to resolve are in .dev/qa/{slug}/issues.md.
     Severity rules:
       - critical / major: resolve, must be fixed
       - minor: resolve by default; skip an individual minor finding
         only with a one-line rationale captured in your report
       - advisory: out of scope for this loop, leave as-is

     In .dev/qa/{slug}/qa-fix-report.md, report:
     - Findings resolved (list)
     - Findings skipped with rationale (list, minor only)
     - Files changed

     CONFIDENCE: HIGH/MEDIUM/LOW
     BLOCKING_ITEMS: N

     When complete:
       ~/bin/forge-bridge send --force claude "FORGE_DONE: qa-fix — see .dev/qa/{slug}/qa-fix-report.md"

     If blocked:
       ~/bin/forge-bridge send --force claude "FORGE_BLOCKED: qa-fix — {issue}"

     Do NOT type /clear yourself — Hard Rule 20: the orchestrator drives clear.
     ```
   - Log, send, wait, and log-response:
     ```bash
     ~/bin/forge-bridge log --slug {slug} --stage qa-fix --from claude --to claude-sonnet --prompt "Read and follow instructions in .dev/forge-tmp/claude-sonnet-qa-fix-{slug}.txt"
     ~/bin/forge-bridge send claude-sonnet "Read and follow instructions in .dev/forge-tmp/claude-sonnet-qa-fix-{slug}.txt"
     ~/bin/forge-bridge log-response --to claude-sonnet --slug {slug} --stage qa-fix --response "<callback>"
     ```
   - Spawn digest agent that reads `.dev/qa/{slug}/qa-fix-report.md`.
3. **Log this as stage `qa-fix`** using the target-aware `log-response --to claude-sonnet`
4. **Re-run QA once** as stage `qa-retry` (same dispatch as qa, same
   worker preference)
5. **If qa-retry digest is clean** → advance to verify
6. **If qa-retry still has findings** → escalate to user with the
   remaining findings. Do not loop a third time.

### Verify

Verify is part of the standard sequence and runs autonomously. Do not
ask the user whether to run verify — always run it.

If verify returns `ISSUES_REMAIN`, escalate to user. If verify is clean,
emit `✓ verify complete — pipeline complete for {slug}. Ready for PR —
let me know when to open it.` and STOP.

**Never open the PR autonomously.** PR creation is an explicit user
instruction outside pipeline mode.

---

## Worker Selection

Keep it simple:

1. **Check the routing** for the current stage (see stage details above)
2. **Check availability**: `~/bin/forge-bridge read codex-a 5` / `read codex-b 5`
   - If you see an idle prompt, the worker is available
   - If you see active output, the worker is busy
3. **Respect constraints**:
   - `review` → codex-a only
   - `verify` → NOT whoever did QA (check the log)
4. **Usage awareness**: If codex-a's status bar shows high usage (>80%),
   prefer codex-b for stages that allow it
5. **If no one is available**: Tell the user. Don't wait silently.

---

## Recovery After Compaction

Start with the context file, then drill into logs only if needed:

1. **Quick state**: `~/bin/forge-bridge context`
   - Shows active pipeline, last completed stage, next stage, notes, recent
     log entries, and pending signals — all in one command
2. **If context is stale or missing**, fall back to:
   - `~/bin/forge-bridge history 20` — find entries with `response: null`
     (in-flight tasks)
   - `~/bin/forge-bridge set-context --slug {slug}` — rebuild context from
     the pipeline log
3. Read the worker pane: `~/bin/forge-bridge read {worker} 30`
4. If the worker finished, log the response
5. If the worker is still working, wait for callback
6. If the worker died, re-dispatch
7. If a background agent failed, check the stage's output artifact on disk.
   If it exists and is complete, log the response and continue. If not,
   re-dispatch as a new background agent.
8. Tell the user what you found

---

## Status Reporting

When the user asks what's happening:

```bash
~/bin/forge-bridge context                # quick overview: pipeline, stage, next step, notes
~/bin/forge-bridge history 10             # recent activity across all pipelines
~/bin/forge-bridge pipeline-log {slug}    # detail for one pipeline
~/bin/forge-bridge read codex-a 10        # what codex A is doing
~/bin/forge-bridge read codex-b 10        # what codex B is doing
```

Start with `context` for the quick answer, then drill into logs or panes
only if the user needs more detail.

Summarize in plain English. Don't dump raw output.

Example:
> "The jwt-refresh pipeline is on the coding stage. Codex A finished the
> implementation 20 minutes ago and the coding agent is running in the
> background. Codex B is idle and ready for QA when we get there."

---

## Agent Failure Recovery

Background agent failures follow this protocol:

1. **Log the failure:**
   ```bash
   ~/bin/forge-bridge log-response --slug {slug} --response "AGENT_FAILED: {error}"
   ```
2. **If retryable** (429 rate limit, timeout, transient API error):
   - Retry once with the same prompt
3. **If persistent failure** (second attempt fails, or non-retryable error):
   - Present to user with error details and options:
     a. Retry the stage
     b. Skip the stage (if non-critical)
     c. Abort the pipeline
4. **Never auto-retry more than once per stage.**

---

## Hard Rules

1. **Always log before sending.** No unlogged dispatches. The bridge
   enforces this — `send` to worker panes will fail with `HOOK BLOCKED`
   if no pending log entry exists. Use `send --force` only for non-pipeline
   messages (ad-hoc questions, status checks sent to workers).
2. **Always include callback instructions** in every task sent to a worker.
3. **The user never types bridge commands.** You handle everything.
4. **The pipeline log is the source of truth.** Read it to know what happened.
5. **Local work gets logged too.** Every stage has a log entry, even if you did it yourself.
6. **One task at a time per worker.** Wait for FORGE_DONE before sending the next.
7. **When in doubt, ask the user.** Don't guess at ambiguous requests.
   **Exception:** in pipeline mode, the bias is to advance — only stop
   on the explicit Stop Conditions (see Pipeline Mode section). "Doubt"
   inside a pipeline run means a real defect, not uncertainty about
   whether to continue.
8. **Don't over-report.** Give the user what they need, not a wall of terminal output.
9. **Never silently substitute agents.** If the user requests a specific worker (Codex A, Codex B) and that worker is unavailable (no forge session, pane not responding, worker busy), you must:
   - Tell the user the worker is unavailable and why
   - Explain what's needed to make it available (e.g. "run `forge-start`")
   - Wait for the user to decide — never start the work yourself as a fallback
   - This applies to ALL dispatches: pipeline stages, ad-hoc tasks, and skill invocations
   - The orchestrator coordinates — it does not silently replace requested agents with itself
10. **Digest agents read disk artifacts, never pane output.** Every digest
    agent reads from `.dev/proposals/{slug}/` or `.dev/qa/{slug}/` files.
    Never use `forge-bridge read` in a digest agent prompt.
11. **Every background/digest agent prompt includes the environment preamble.**
    Built from `forge-project.yml` at pipeline start. No exceptions.
12. **Every digest and background report ends with CONFIDENCE + BLOCKING_ITEMS.**
    Format: `CONFIDENCE: HIGH/MEDIUM/LOW` and `BLOCKING_ITEMS: N`.
13. **On LOW confidence or any blocking items, read the full artifact.**
    Do not rely solely on compressed digest output for gating decisions.
14. **Never send multi-line prompts inline via forge-bridge.** For any
    prompt longer than one line: write it to
    `.dev/forge-tmp/{worker}-{slug}.txt` using the Write tool, then send
    a SHORT reference message:
    `~/bin/forge-bridge send {worker} "Read and follow instructions in .dev/forge-tmp/{worker}-{slug}.txt"`.
    **NEVER use `$(cat ...)` or subshell expansion** — it expands file
    content into the command string and breaks the permission matcher.
    **Never use `/tmp/`** — there is no Write permission for it.
15. **Use `add-note` to annotate context mid-pipeline.** After resolving a
    FORGE_BLOCKED, noting a risk for the next stage, or flagging something
    for a future session, run `~/bin/forge-bridge add-note "<text>"`. Notes
    persist in `forge-context.yml` and survive session restarts.
16. **Start every new session with `context`.** Before doing anything else
    in a resumed or new session, run `~/bin/forge-bridge context` to load
    the current pipeline state. If no context exists, check `history`.
17. **Every FORGE_DONE triggers a digest agent BEFORE any artifact read.**
    This applies equally to pipeline stages, ad-hoc investigations, ad-hoc
    fixes, and commit-review batches — no exceptions for "short" reports.
    Spawn the digest, wait for its compressed summary, then decide. Read
    the raw artifact only if the digest returned `CONFIDENCE: LOW` or
    `BLOCKING_ITEMS > 0`. A project-level `PreToolUse` hook enforces this
    for files under `.dev/proposals/`, `.dev/reviews/`, and `.dev/qa/` —
    if you see the reminder, you forgot a digest. To deliberately bypass
    (e.g. after a LOW-confidence digest), create `.dev/.forge-digest-ack`
    first; the hook clears it after one read.
18. **Pre-flight is mandatory at fresh dispatch boundaries.** Run
    `~/bin/forge-bridge preflight` before any dispatch in:
      (a) `forge-pipeline {slug}` invocation
      (b) `forge-resume` invocation
      (c) recovery after compaction or session restart (>5 min orchestrator silence)
    If `status_code` is HALT-class (`BRANCH_MERGED_WITH_DRIFT`,
    `WRONG_DIRECTORY`, `DETACHED_HEAD`, `BRANCH_UNCLEAR`), surface the full
    preflight block verbatim to the user and stop. Do not dispatch the next
    stage. If `status_code` is `BRANCH_MERGED_CLEAN`, surface as a one-line
    warning and proceed. If the user passes `--skip-preflight`: run preflight
    anyway, surface the output, but bypass HALT. Log the override with
    `~/bin/forge-bridge add-note "preflight skipped: <reason>"` before the
    next dispatch. This rule is an explicit exception to Rule 8 (do not
    over-report) — preflight output is always shown verbatim when surfaced.
19. **Run stall-check before reporting a worker is still in progress.**
    Whenever an orchestrator turn would tell the user "{worker} is still
    working on {stage}", or before waiting silently on a pending dispatch,
    first run:

        ~/bin/forge-bridge stall-check --project-root "$PROJECT_ROOT" {worker}

    The `--project-root` argument is mandatory (canonical §2.6 explicit
    contract). Source: `forge.expected_root` from `.claude/forge-project.yml`
    (the orchestrator already reads this for Hard Rule 18's preflight).

    If `~/bin/forge-bridge context` (Hard Rule 16's mandated session-start
    surface) emits a `=== Stall Check Status ===` block listing any pane
    with stale stall-check coverage, run `stall-check` for that pane
    immediately before any further narration. The context-stale warning
    is the self-detection-of-dead-detector signal in this no-daemon design
    (canonical §2.4 spirit-compliance).

    Surface the output to the user along with the working-status:

      - state ACTIVE
          → "{worker} is on {stage} (last activity Xs ago)"
      - state IDLE
          → "{worker} appears idle (no pending dispatch)."
            (If a callback was expected, read pane scrollback.)
      - state COMPLETED-PENDING-LOG-RESPONSE
          → autonomous recovery, target-safe:
            1. Verify exactly one `response: null` entry exists for {slug}
               targeting {worker}. If more than one, halt and surface to
               user — do NOT auto-recover.
            2. Run `~/bin/forge-bridge read {worker} 50` to find the
               FORGE_DONE/FORGE_BLOCKED/FORGE_ERROR callback.
            3. Run `~/bin/forge-bridge log-response --to {worker}
               --slug {slug} --stage {stage} --response "<callback>"`.
               The `--to` flag is MANDATORY for autonomous recovery.
            The pipeline advances normally. No user surfacing required.
      - state STALLED
          → surface the full stall-check block; treat as AGENT_FAILED;
            follow Agent Failure Recovery (Pipeline-Mode stop condition #2).
            No new stop condition needed.
      - state PROMPTING
          → surface immediately (the user must respond). To send input:
            `~/bin/forge-bridge send --force {worker} "<input>"`.
            PROMPTING does not halt the pipeline — it remains paused on
            the worker callback. Do NOT auto-edit `.claude/settings.json`
            allowlists.
          → §8 carve-out: this branch fires for Codex workers only when
            their config has `ask_for_approval` enabled. On this workstation
            Codex has `ask_for_approval = "never"`, so PROMPTING never fires
            for codex-a / codex-b; a stuck approval prompt would surface as
            STALLED after threshold. Claude workers (`claude-opus`,
            `claude-sonnet`) DO have an active V3 PROMPTING regex per
            `phase2-step0/claude-prompting/regex.yml`; their PROMPTING
            branch fires immediately when an approval prompt appears.
      - state DEAD
          → halt. Tell user `tmux kill-session -t {session}` then
            `forge-start` then `forge-resume`. Do NOT attempt in-place
            pane reconstruction (canonical §2.10).
      - state UNKNOWN (baseline_pending)
          → first call after session-reuse or wiped cache. Do NOT report
            active or stalled. Re-run after ≥1 minute.
      - state UNKNOWN (reason=idle_regex_unavailable)
          → `~/.config/forge/idle-prompts.yml` is missing or this pane's
            regex is empty. Run `~/bin/forge-stall-install-regex` to
            install from the committed verification fixture, or re-run
            Step 0 V1 if the fixture is also missing.
      - state UNKNOWN (reason=active_work_marker_unavailable)
          → `~/.config/forge/idle-prompts.yml` is missing or this Claude
            pane's `active_work_marker` field is empty. Run
            `~/bin/forge-stall-install-regex` to install from the committed
            Phase-2 fixture, or re-run Step 0 V1 if the fixture is also
            missing. Until installed, do not narrate Claude worker progress.

    "External worker" = any pane the orchestrator dispatches work to via
    `forge-bridge log` + `send`. In Phase 2 this includes claude-opus
    (pane 0), codex-a (pane 2), codex-b (pane 3), and claude-sonnet
    (pane 4).

    Threshold: `FORGE_STALL_THRESHOLD_S` defaults to 600 (10 min). For
    legitimately-long stages, pass a per-stage override on the call:
      - proposal:  1800   (30 min — Agent Teams take a while)
      - review:     600   (10 min)
      - incorporate, impl-review: 1200 (20 min — Opus reasoning)
      - coding:    1500   (25 min)
      - qa, qa-fix: 1200  (20 min)
      - verify:     900   (15 min)
    Example:
      `FORGE_STALL_THRESHOLD_S=1500 ~/bin/forge-bridge stall-check --project-root "$PROJECT_ROOT" claude-sonnet`
    The orchestrator decides the threshold based on the active stage from
    `forge-context.yml`.

    Per the Phase 1b problem statement §8, stalls that occur entirely
    between orchestrator wakes (orchestrator silent + user away) are not
    detected until the next wake. This is an acceptable limitation. The
    `=== Stall Check Status ===` warning surfaced by `forge-bridge
    context` makes Hard-Rule-19 forgetfulness observable on the next
    session-start path.

20. **Orchestrator drives /clear between Claude-worker dispatches.**
    Claude worker panes (`claude-opus` pane 0, `claude-sonnet` pane 4)
    accumulate in-conversation context across dispatches. The orchestrator,
    never the worker, runs:

        ~/bin/forge-bridge send --force {claude-opus|claude-sonnet} "/clear"

    BEFORE the next dispatch to that same Claude worker pane.

    Sequence on FORGE_DONE:
      1. Receive FORGE_DONE callback from claude-opus or claude-sonnet
      2. Run `forge-bridge log-response` with the `--to` flag
      3. Spawn the per-stage digest agent
      4. After digest returns and the orchestrator decides to advance:
         a. `~/bin/forge-bridge send --force {worker} "/clear"`
         b. `sleep "${FORGE_CLEAR_WAIT_S:-2}"`
         c. Verify `stall-check` returns IDLE when the workstation is slow
         d. Log and send the next dispatch normally

    Exception: when responding to FORGE_BLOCKED with a follow-up dispatch
    on the SAME task, do NOT /clear; the worker needs the original task
    context to continue.

    The `/clear` command is the one worker-pane `--force` send exempt from
    §3 prohibition #8. All other worker dispatches need a prior
    `forge-bridge log` entry and go through the per-target hook.

    The dispatch prompt's completion footer no longer instructs the worker
    to /clear itself. The worker's only callback duty is FORGE_DONE,
    FORGE_BLOCKED, or FORGE_ERROR.

21. **Worker permission-mode and ident contract.**
    Launch flags:
      Pane 0: `claude --model claude-opus-4-7 --permission-mode acceptEdits`
      Pane 1: `claude --model claude-opus-4-7` (NO acceptEdits)
      Pane 2/3: `codex`
      Pane 4: `claude --model claude-sonnet-4-6 --permission-mode acceptEdits`

    Worker idents are REPO-LOCAL ONLY, set inside the dispatch prompt body:
      `git -C "$PROJECT_ROOT" config user.name  "claude-opus (forge pane 0)"`
      `git -C "$PROJECT_ROOT" config user.email "claude-opus@forge.local"`
    or the equivalent `claude-sonnet` values for pane 4.

    NEVER `git config --global`. NEVER `git config --add`.

    Workers inherit the project's `.claude/settings.local.json` `Bash(*)`
    wildcard when launched from `forge-start` in the project root. If an
    operator removes that wildcard, the bare workstation-wide Bash entries
    needed are `pytest`, `node`, and `compare`.

    `forge-dispatch-review` routes commits made by `claude-opus (forge pane 0)`
    or `claude-sonnet (forge pane 4)` to Codex A. The reviewer guard prevents
    routing reviews to worker panes 0 or 4.

    PROMPTING regex (`^ ❯ \d+\. `) for Claude panes is active in Phase 2.
    Surface prompts to the user; do not auto-edit allowlists.

---

## Commit Review Pipeline

A post-commit hook automatically queues lightweight code reviews for every
commit. These run as a side-channel alongside the main pipeline — they don't
block pipeline stages but surface issues early.

See `references/commit-review.md` for the full dispatch template and
reviewer prompt.

### How It Works

1. **Hook fires** on every commit → writes `.dev/reviews/pending/{ts}-{hash}.review`
2. **Orchestrator detects** pending reviews via `forge-bridge context` or
   `forge-bridge review-status`
3. **Orchestrator dispatches** to the appropriate reviewer (Codex A or B)
4. **Reviewer processes** each pending file → writes verdict → archives pending file
5. **Orchestrator surfaces** results at stage gates

### When to Dispatch Reviews

- **During coding stage**: when the target reviewer pane is idle
- **At stage gates**: before advancing past coding, run `review-status`
- **On user request**: "review pending commits"

### Routing

Read `committer_ident` from the `.review` file:
- Codex B commits → route to **Codex A**
- All other commits → route to **Codex B**
- Only one available → route there regardless

### Dispatch

Use stage name `commit-review` (not `review` — that's the pipeline review stage):

```bash
# 1. Log
~/bin/forge-bridge log --slug {slug} --stage commit-review --from claude --to {reviewer} --prompt "Review pending commits"

# 2. Write prompt to .dev/forge-tmp/{reviewer}-commit-review.txt
#    (see references/commit-review.md for the full template)

# 3. Send
~/bin/forge-bridge send {reviewer} "Read and follow instructions in .dev/forge-tmp/{reviewer}-commit-review.txt"
```

### Surfacing at Stage Gates

Before advancing past coding:

```bash
~/bin/forge-bridge review-status
```

Report to user: "N reviews complete (X PASS, Y CONCERNS, Z BLOCKING), M pending."
If BLOCKING verdicts exist, list them and ask the user whether to proceed.
Phase 1 is advisory — reviews don't hard-block pipeline advancement.
