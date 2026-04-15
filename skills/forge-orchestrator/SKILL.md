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
- **Pane 2**: Codex A
- **Pane 3**: Codex B

Pane names: inline/general (0), claude/orchestrator (1), codex/codex-a (2), codex-b (3)

The user talks to you in plain English. You decide what to do, who does
it, and manage the whole flow. The user never types bridge commands.

You offload heavy work to background Claude Code agents and use digest
agents to compress output before it enters your main context. You are a
**dispatch + summarize + gate** loop.

---

## Execution Model Reference

| Stage | Execution | Digest? | Why |
|-------|-----------|---------|-----|
| proposal | **Foreground** (Agent Teams) | Yes, background after | Spawns A, B, C teammates |
| review | External (Codex A) | Yes, background after | No sub-agents needed |
| incorporate | **Background agent** | No (agent IS the worker) | Simple file merge |
| implementation | External (Codex A/Codex B) | Yes, background after | No sub-agents needed |
| impl-review | **Background agent** | No (agent IS the worker) | Simple comparison |
| coding | **Background agent** | No (agent IS the worker) | forge-coder, no teams |
| qa | External (Codex B) or **Foreground** (local) | Yes, background after | Agent Teams if local |
| verify | External or **Background agent** (local) | Yes if external | adversarial-verify is single-agent |

**Background agents** run via `Agent(run_in_background: true)`. They save
context by keeping their full reasoning out of the main conversation.

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
| "Have codex review commit abc123"              | Ad-hoc dispatch to codex-a                      |
| "Start a pipeline for adding JWT refresh"      | Begin full pipeline, start at proposal          |
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

### 2b. Dispatch as background agent (local non-team stages)

For stages that run as background agents (incorporate, impl-review, coding,
verify-local), spawn a Claude Code agent instead of working inline:

```
Agent({
  description: "forge: {stage} — {slug}",
  run_in_background: true,
  prompt: """
    {ENVIRONMENT_PREAMBLE}

    {stage-specific instructions — see Stage Details below}

    Report (under N words):
    - {stage-specific report items}

    CONFIDENCE: HIGH/MEDIUM/LOW
    BLOCKING_ITEMS: N (count of items that should block the pipeline)
  """
})
```

### 2c. Spawn digest agent (for compressing output)

After any external worker completes (FORGE_DONE) or any foreground team
stage finishes, spawn a background digest agent to compress the output:

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

### 6. Confidence-based gating

After receiving a digest (from step 2b or 2c), check the confidence signal:

- **CONFIDENCE: HIGH and BLOCKING_ITEMS: 0** — Present the digest summary
  to the user. Proceed to the gating question.
- **CONFIDENCE: LOW or BLOCKING_ITEMS > 0** — Read the full disk artifact
  yourself before making a gating decision. Do not rely solely on the
  digest when quality is uncertain.

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
- Present digest to user. Gate: "Proceed to review?"

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
- Present digest to user. Gate: "Proceed to incorporate?"

**incorporate** — Background agent
- Do NOT run this inline. Spawn a background agent:
  ```
  Agent({
    description: "forge: incorporate review — {slug}",
    run_in_background: true,
    prompt: """
      {ENVIRONMENT_PREAMBLE}

      Read .dev/proposals/{slug}/review-feedback.md and
      .dev/proposals/{slug}/final-plan.md

      Merge the review feedback into final-plan.md:
      - Accept all critical/blocking items
      - Accept minor items unless they conflict with the plan's core approach
      - Note any rejected items with reasoning

      Write the updated final-plan.md in place.

      Report (under 200 words):
      - Items accepted / rejected / partially accepted
      - Key changes made to the plan

      CONFIDENCE: HIGH/MEDIUM/LOW
      BLOCKING_ITEMS: N
    """
  })
  ```
- Log as from claude to claude
- Present: "Incorporated N/M review items. Updated final-plan.md."

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
- Present digest. Gate: "Proceed to impl-review?"

**impl-review** — Background agent
- Do NOT run this inline. Spawn a background agent:
  ```
  Agent({
    description: "forge: impl-review — {slug}",
    run_in_background: true,
    prompt: """
      {ENVIRONMENT_PREAMBLE}

      Review .dev/proposals/{slug}/implementation.md against
      .dev/proposals/{slug}/final-plan.md

      Check:
      - Every plan item has a corresponding implementation step
      - Coverage matrix has no GAPs
      - Diffs reference correct file paths and function signatures
      - Test specs cover the plan's acceptance criteria
      - Commit groups are ordered correctly (migrations before code, etc.)

      Report (under 400 words):
      - Plan items covered vs missed
      - Coverage matrix status
      - Issues found (blocking vs advisory)
      - Recommendation: proceed / fix needed

      CONFIDENCE: HIGH/MEDIUM/LOW
      BLOCKING_ITEMS: N
    """
  })
  ```
- If BLOCKING_ITEMS > 0: read implementation.md yourself for details
- Present: "Impl review: N/N plan items covered. M issues. Proceed to coding?"

**coding** — Background agent on feature branch (no worktree)
- Ensure you are on feature branch: `git checkout -b {slug}` (if not already)
- Do NOT use worktrees — they block access to `~/.claude/skills/` and `~/bin/`
- Spawn a background agent:
  ```
  Agent({
    description: "forge: coding — {slug}",
    run_in_background: true,
    prompt: """
      {ENVIRONMENT_PREAMBLE}

      You are executing the 'coding' stage of a forge pipeline.

      Slug: {slug}

      Follow the forge-coder skill at ~/.claude/skills/forge-coder/SKILL.md
      Project config: .claude/forge-project.yml
      Implementation doc: .dev/proposals/{slug}/implementation.md

      Execute all phases: validation → branch check → apply changes →
      test each group → full validation → report.

      Write coder-report.md to .dev/proposals/{slug}/

      Report (under 400 words):
      - Commit groups applied (N of M)
      - Tests: pass/fail per group
      - Full validation results
      - Any diffs that failed to apply
      - Files changed

      CONFIDENCE: HIGH/MEDIUM/LOW
      BLOCKING_ITEMS: N
    """
  })
  ```
- If BLOCKING_ITEMS > 0: read coder-report.md for details
- Output: code changes + `.dev/proposals/{slug}/coder-report.md`

**qa** — Codex B preferred (external + digest); local fallback (foreground + digest)
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

        Digest (under 400 words):
        - Total findings: N critical / N major / N minor / N advisory
        - Top blocking issues with one-line descriptions
        - Regression status (existing features)
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
- Present digest. Gate.

**verify** — Exclusion-based; external + digest or local background
- Must NOT be the same worker that did QA (check pipeline log)
- **If external worker available:**
  - Dispatch via forge-bridge
  - After FORGE_DONE, spawn digest agent to read `.dev/qa/{slug}/verification-report.yaml`
- **If local (adversarial-verify is single-agent — background works):**
  ```
  Agent({
    description: "forge: verify — {slug}",
    run_in_background: true,
    prompt: """
      {ENVIRONMENT_PREAMBLE}

      Follow adversarial-verify skill at
      ~/.claude/skills/adversarial-verify/SKILL.md
      Slug: {slug}
      Project config: .claude/forge-project.yml
      Manifest: .dev/qa/{slug}/manifest.yaml

      Report (under 300 words):
      - Verdict: CLEAR or ISSUES_REMAIN
      - Findings verified fixed vs still open
      - Checks still passing vs regressed
      - Cycle number

      CONFIDENCE: HIGH/MEDIUM/LOW
      BLOCKING_ITEMS: N
    """
  })
  ```
- Skill: `adversarial-verify`
- Output: `.dev/qa/{slug}/verification-report.yaml`
- If ISSUES_REMAIN, escalate to user.

### Advancing Through Stages

After each stage completes:
1. Log the response
2. Check if the output files exist
3. Determine the next stage by reading the pipeline log
4. If the next stage is a background agent (incorporate, impl-review, coding, verify-local), spawn the agent and wait for completion
5. If the next stage is foreground (proposal, local QA), run it inline then spawn a digest agent
6. If the next stage needs external dispatch, select the worker and send it
7. Report progress to the user between stages

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
