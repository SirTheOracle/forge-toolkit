# Forge Technical Reference

Developer-oriented reference for the forge multi-agent coding system. Living
document — content inside `docs-refresh` marker blocks is regenerated from
the source files declared in `.claude/docs-refresh.yml`. Manual prose
outside the markers is preserved.

## Architecture

<!-- docs-refresh:start section=architecture -->
Forge coordinates work inside a five-pane tmux session, with pane 1 acting as
the in-pane orchestrator and `~/bin/forge-bridge` as the file/tmux control bus.
The current `/forge` command does **not** spawn a hidden orchestrator agent:
it loads `~/.claude/skills/forge-orchestrator/SKILL.md` into pane 1 and the
user talks directly to that orchestrator for the rest of the run.

**Processes (per session, 5 tmux panes):**

| Pane | Process | Role |
|---|---|---|
| 0 | `claude --model claude-opus-4-8 --permission-mode acceptEdits` | `claude-opus` worker (HIGH tier) — incorporate, impl-review; HIGH-tier fallback for implementation + verify |
| 1 | `claude --model claude-opus-4-8` (no acceptEdits) | Orchestrator (Hard Rule 21) |
| 2 | `codex -m gpt-5.5 -c model_reasoning_effort=xhigh -c service_tier=fast` | `codex-a` worker (HIGH tier) — review, implementation (default), verify (default) |
| 3 | `codex -m gpt-5.5 -c model_reasoning_effort=medium -c service_tier=fast` | `codex-b` worker (THROUGHPUT tier) — qa, qa-retry |
| 4 | `claude --model claude-sonnet-4-6 --permission-mode acceptEdits` | `claude-sonnet` worker (THROUGHPUT tier) — coding, qa-fix, qa dispatch fallback |

**Control bus:** `~/bin/forge-bridge` — a single shell script that owns all
inter-pane messaging, dispatch logging, callback collection, stall
detection, status rendering, usage observation, callback lifecycle, and
cross-worktree infra locking. Workers never talk to each other directly;
everything goes through the bridge.

**State on disk** (per-project, under `.dev/`):

| File | Owner | Purpose |
|---|---|---|
| `.dev/.forge-session` | `forge-start` | tmux session name (e.g. `forge-2`) |
| `.dev/forge-log.yml` | `forge-bridge log` / `log-response` | Project-wide dispatch summary |
| `.dev/forge-context.<session>.yml` | `log-response` / `set-context` | Session-scoped active pipeline pointer, last stage, next stage, notes |
| `.dev/forge-status.<session-or-no-session>.md` | bridge side-effect of dispatch/wait/callback/status | Session-scoped human-readable rolling state; includes infra-lock status when available |
| `.dev/forge-usage.<session>.yml` | `callback` usage observer | Per-worker usage snapshot. Claude parses footer `ctx`; Codex records honest `unknown` |
| `.dev/proposals/{slug}/forge-log.yml` | bridge | Per-pipeline detail |
| `.dev/proposals/{slug}/*.md` | workers | Stage artifacts (final-plan.md, review-feedback.md, etc.) |
| `.dev/qa/{slug}/` | qa workers | issues.md, manifest.yaml, verification-report.yaml |
| `.dev/forge-tmp/{worker}-{stage}-{slug}.txt` | `dispatch` | Rendered stage prompt sent to worker |
| `.dev/forge-tmp/callbacks/{slug}-{stage}.callback` | `callback` | Canonical callback file consumed by `wait` |
| `.dev/forge-tmp/callbacks/archive/` | `callback-consume` | Archived non-terminal BLOCKED callbacks after a continuation succeeds |
| `.dev/forge-tmp/orchestrator-events.log` | `emit` / `_emit_event` | Heartbeat event stream. Public `emit` accepts `DISPATCH`, `WAIT`, `CALLBACK`, `DIGEST`, `STAGE`, `STALL`, `ERROR`, `COMPLETE`, and `USAGE`; bridge internals also write `LOCK` |
| `<git-common-dir>/forge-infra-lock/infra.{flock,holder}` | `infra-lock` | Cross-worktree mutex for shared infra stages |

**Invocation modes:**

1. **`/forge` canonical in-pane mode** — parses `pipeline`, `fix-pipeline`,
   `resume`, `status`, and `pause`, then loads the orchestrator SKILL into
   the current pane when orchestration is needed. There is no spawner or
   forwarding agent.
2. **`/forge-orchestrator` manual entry** — no-argument escape hatch that
   loads the same orchestrator SKILL directly for debugging or one-off manual
   driving.

The legacy background-agent definition at
`~/.claude/agents/forge-orchestrator.md` may share the body, but it is no
longer on the `/forge` path.

<!-- TODO: Lower-tier command prose still names legacy `.dev/forge-status.md`
and `.dev/forge-context.yml`; bridge code is authoritative and writes
session-scoped `forge-status.<session>.md` and `forge-context.<session>.yml`. -->
<!-- TODO: Earlier orchestrator role/worker-selection prose still calls
claude-sonnet the qa "local fallback"; Hard Rule 22 is authoritative:
claude-sonnet is a throughput dispatch fallback, and the true local fallback
is gated pane-1 Agent Teams qa/qa-retry under infra-lock. -->
<!-- docs-refresh:end section=architecture -->

## Bridge Commands

<!-- docs-refresh:start section=bridge-commands -->
The bridge splits into tmux-required commands (operate on panes; need an
active forge session) and file-based commands that do not require pane focus.
File-based commands still resolve a forge project root from cwd when they
need project state, and session-scoped filenames use the active session name
or the `no-session` sentinel.

### Tmux-required commands

| Command | Purpose |
|---|---|
| `send <pane> <message>` | Type into a pane; enforces per-target log-before-send hook |
| `send --force <pane> <msg>` | Bypass the log check for non-pipeline follow-ups such as BLOCKED continuations |
| `read <pane> [lines]` | Read last N lines from a pane |
| `focus <pane>` | Switch tmux focus to a pane |
| `back` | Return focus to the orchestrator pane |
| `dispatch --slug <s> --stage <s> --worker <w> [--clear] [--dry-run] [--supersede]` | Render a stage prompt, enforce stage/worker tier, guard against open same-slug pendings, log, and send |
| `wait --slug <s> --stage <s> --worker <w> [--timeout <s>] [--digest-template <name>] [--interval <s>]` | Block until callback or stall classification; emit one structured block |
| `stall-check [--project-root <p>] <pane>` | Classify pane state (IDLE / ACTIVE / STALLED / PROMPTING / COMPLETED-PENDING-LOG-RESPONSE / DEAD / UNKNOWN) |
| `health` | Verify all 5 panes exist and run the expected worker process; exits 0 only if all OK |

### No-tmux commands (no pane focus required)

| Command | Purpose |
|---|---|
| `preflight` | Kickoff snapshot: pwd, branch, merge state, halt status code |
| `signal <from> <message>` | Write a signal file to `.dev/signals/` |
| `check-signals` | List pending signals |
| `clear-signals` | Remove all signal files |
| `review-status` | Pending/completed commit-review counts and blocking items |
| `log --slug <s> --stage <s> --from <f> --to <t> --prompt <p>` | Log a dispatch entry |
| `log-response --slug <s> --response <r> [--to <pane>] [--stage <s>] [--file <path:action>]...` | Update a pending entry; `--to` disambiguates when multiple pending |
| `history [lines]` | Project-wide summary |
| `pipeline-log <slug> [lines]` | Per-pipeline detail |
| `status` | Render and print `.dev/forge-status.<session-or-no-session>.md` |
| `context` | Show current pipeline state |
| `set-context --slug <s>` | Manually set the active pipeline (auto-derives state) |
| `add-note <text>` | Annotate context; persists in the session-scoped forge context file |
| `usage [<worker>]` | Show the per-worker usage snapshot from `forge-usage.<session>.yml` (read-only; never scrapes a pane) |
| `callback --slug <s> --stage <s> --status <DONE\|BLOCKED\|ERROR> [--message <m>] [--worker <w>] [--quiet]` | Worker-side declarative completion. Terminal DONE/ERROR closes the pending before publishing; BLOCKED publishes a canonical callback and keeps the pending open |
| `callback-consume --slug <s> --stage <s> [--status BLOCKED]` | Archive a consumed non-terminal BLOCKED callback after a successful `send --force` continuation |
| `infra-lock <acquire\|release\|status> --slug <s> --stage <t> [--timeout N] [--interval I] [--force]` | Cross-worktree mutex for `coding`, `qa`, `qa-fix`, `qa-retry`, and `verify` |
| `digest --slug <s> --stage <s> --template <name> [--worker <w>]` | Render digest prompt to `.dev/forge-tmp/` for ad-hoc Agent dispatch |
| `emit <TYPE> --slug <s> [--stage <s>] [key=value …]` | Orchestrator-driven heartbeat emit (TYPE: DISPATCH \| WAIT \| CALLBACK \| DIGEST \| STAGE \| STALL \| ERROR \| COMPLETE \| USAGE) |
| `stall-check-status [--project-root <p>]` | List panes with pending dispatch and stale stall-check coverage |
| `alias-self-test [--strict]` | Verify pane alias maps stay in lockstep |

### Automatic hooks

1. **log-before-send** — `send` to a worker pane blocks unless a pending
   log entry targets the same canonical pane. `send --force` is reserved
   for non-pipeline follow-ups such as BLOCKED continuations.
2. **same-slug open-pending guard** — `dispatch` refuses to open a new
   stage when any existing `response: null` entry remains for the same
   slug, unless the operator intentionally uses `--supersede`.
3. **stage/worker tier guard** — `dispatch` rejects HIGH stages sent to
   throughput panes and throughput stages sent to HIGH panes. `proposal`
   is not dispatchable.
4. **log-response auto-context** — `log-response` auto-updates the
   session-scoped `.dev/forge-context.<session>.yml` with current stage,
   status, worker, and next stage. `qa`/`qa-fix`/`qa-retry` do NOT
   auto-advance; the orchestrator decides.
5. **callback terminality contract** — DONE/ERROR callbacks close the
   pending entry before atomically publishing the callback file. BLOCKED
   callbacks publish the canonical callback but intentionally keep the
   pending open until the continuation is sent and `callback-consume`
   archives the BLOCKED callback.
6. **require_pane_count** — validates the session has at least
   `FORGE_REQUIRED_PANES` (5 in Phase 2) panes at every tmux session
   entry point.
7. **preflight halts** — `cmd_preflight` returns one of these status
   codes; the orchestrator halts on `BRANCH_MERGED_WITH_DRIFT`,
   `WRONG_DIRECTORY`, `DETACHED_HEAD`, `BRANCH_UNCLEAR`.

### Session resolution

`require_tmux_session` resolves the active tmux session for every
tmux-required command, in priority order:

1. `TMUX_SESSION` env var (explicit override)
2. `tmux display-message -p '#{session_name}'` — only if `$TMUX` is set
   (i.e. command runs inside a tmux pane)
3. Walk up from cwd looking for `.dev/.forge-session`; if multiple live
   `forge-*` sessions exist, file naming resolves to the no-session sentinel
   and tmux-required commands continue to the explicit candidate check
4. Auto-pick a `forge-*` session that has the required pane count —
   **refuses to pick if more than one candidate exists**, listing the
   candidates and requiring an explicit `TMUX_SESSION` or a cwd-rooted
   `.dev/.forge-session`
<!-- docs-refresh:end section=bridge-commands -->

## Orchestrator Hard Rules

<!-- docs-refresh:start section=orchestrator-hard-rules -->
The orchestrator body declares 24 Hard Rules (0-23). One-line summaries
below; see `~/.claude/skills/forge-orchestrator/SKILL.md` for full text and
rationale.

| # | Rule | Summary |
|---|---|---|
| 0 | Session pinning | Step 0 on every invocation: resolve the tmux session, verify it exists, and `export TMUX_SESSION=<name>` before any bridge call |
| 1 | Always log before sending | No unlogged dispatches; the bridge enforces this. `send --force` only for non-pipeline messages |
| 2 | Always include callback instructions | Every task sent to a worker must specify how the worker reports completion |
| 3 | The user never types bridge commands | Orchestrator handles all bridge invocation |
| 4 | The pipeline log is the source of truth | Read `.dev/proposals/{slug}/forge-log.yml` to know what happened |
| 5 | Local work gets logged and closed too | Every stage has a log entry, even local `to: claude` work, and local entries must be closed with `log-response --to claude --stage ...` |
| 6 | One task at a time per worker | Wait for `FORGE_DONE` before sending the next |
| 7 | When in doubt, ask the user | Exception: in pipeline mode bias is to advance — "doubt" means a real defect, not procedural uncertainty |
| 8 | Don't over-report | Give the user what they need, not a wall of terminal output |
| 9 | Never silently substitute agents | If a requested worker is unavailable, tell the user; don't fall back |
| 10 | Digest agents read disk artifacts, never pane output | Every digest agent reads from `.dev/proposals/{slug}/` or `.dev/qa/{slug}/` |
| 11 | Every background/digest agent prompt includes the environment preamble | Built from `.claude/forge-project.yml` at pipeline start |
| 12 | Every digest and background report ends with CONFIDENCE + BLOCKING_ITEMS | `CONFIDENCE: HIGH/MEDIUM/LOW` and `BLOCKING_ITEMS: N` |
| 13 | On LOW confidence or any blocking items, read the full artifact | Don't rely on compressed digest alone for gating |
| 14 | Pipeline stages use `forge-bridge dispatch`, not raw `send` | Templates live in `~/.config/forge/prompts/{stage}.txt`; raw `send` only for ad-hoc messages. Never `$(cat …)`, never `/tmp/` |
| 15 | Use `add-note` to annotate context mid-pipeline | Notes persist in the session-scoped forge context file |
| 16 | Start every new session with `context` | Load the current pipeline state from disk before acting |
| 17 | Every FORGE_DONE triggers a digest agent BEFORE any artifact read | Hook-enforced under `.dev/proposals/`, `.dev/reviews/`, `.dev/qa/`. Bypass via `.dev/.forge-digest-ack` |
| 18 | Pre-flight is mandatory at fresh dispatch boundaries | Run `preflight` AND `health` at kickoff / resume / post-compaction; halt on HALT codes or non-OK panes |
| 19 | Stall detection lives in `forge-bridge wait` | Don't call `stall-check` directly during a pipeline run |
| 20 | `dispatch --clear` between same-pane Claude dispatches | Claude panes accumulate context; pass `--clear` when re-using one. Codex panes don't need it |
| 21 | Worker permission-mode and ident contract | Pane-launch flags fixed; git idents are repo-local only (`git -C ... config` — never `--global`) |
| 22 | Reasoning-tier routing (bridge-enforced) | HIGH stages (review, incorporate, implementation, impl-review, verify) → codex-a or claude-opus only; THROUGHPUT stages (coding, qa, qa-fix, qa-retry) → claude-sonnet or codex-b. `proposal` is the local pane-1 exception (not dispatchable). A local pane-1 `qa` / `qa-retry` fallback is allowed only when throughput routing is unavailable and the infra lock is held. `dispatch` rejects illegal stage/worker pairs |
| 23 | Cross-worktree infra lock | `coding`, `qa`, `qa-fix`, `qa-retry`, and `verify` acquire `infra-lock`; reasoning stages never lock. Release only on terminal DONE/ERROR, not while PROMPTING/STALLED/TIMEOUT/DEAD/BLOCKED |
<!-- docs-refresh:end section=orchestrator-hard-rules -->

## Stage Routing

<!-- docs-refresh:start section=stage-routing -->
A full pipeline runs eight stages autonomously when triggered by
`forge-pipeline {slug}`:

```
proposal → review → incorporate → implementation → impl-review → coding → qa → verify
```

### Stage → worker map

| Stage | Worker | Notes |
|---|---|---|
| proposal | local (Agent Teams) | HIGH tier, local pane-1 exception. Spawns A, B, C teammates in the orchestrator's foreground — NOT dispatched via the bridge |
| review | codex-a | HIGH. Adversarial proposal review. Codex-a only; do not silently fall back |
| incorporate | claude-opus | HIGH. Merge review feedback into final-plan.md |
| implementation | codex-a (**claude-opus** fallback, `--clear`) | HIGH. Adversarial implementation doc. Fall back to the other HIGH pane only on explicit high-usage signal — never to a throughput pane |
| impl-review | claude-opus (with `--clear`) | HIGH. Verify implementation against plan + scope diff check |
| coding | claude-sonnet | THROUGHPUT. Execute the implementation via the `forge-coder` skill. No worktrees |
| qa | codex-b; claude-sonnet dispatch fallback; pane-1 local fallback only when throughput routing is unavailable | THROUGHPUT (medium-reasoning, throughput-routed). Adversarial QA + regression sweep; all paths hold infra-lock |
| qa-fix | claude-sonnet (with `--clear`) | THROUGHPUT. Resolve QA findings — only entered if qa digest has findings |
| qa-retry | codex-b or claude-sonnet; pane-1 local fallback only when throughput routing is unavailable | THROUGHPUT. Re-run QA after qa-fix; one re-run only; all paths hold infra-lock |
| verify | **codex-a (claude-opus fallback)** | HIGH. Final verification. Exclusion guard: MUST NOT be the same worker that ran the most recent qa stage (auto-satisfied under current QA routing) |

### Infra-lock coverage

Five infra-touching stages are serialized globally across worktrees with
`forge-bridge infra-lock`: `coding`, `qa`, `qa-fix`, `qa-retry`, and
`verify`. The reasoning-only stages `proposal`, `review`, `incorporate`,
`implementation`, and `impl-review` are explicitly out of scope and should
never acquire the lock.

Shape A wraps dispatched infra stages:

1. `infra-lock acquire --slug <s> --stage <stage>`
2. `dispatch ...`
3. `wait ...`
4. Release only when `wait` returns terminal `DONE` or `ERROR`

The lock intentionally stays held while a stage is `PROMPTING`, `STALLED`,
`TIMEOUT`, `DEAD`, or `BLOCKED`, because those states do not prove the
worker has stopped touching shared ports or the shared database. For a
normal BLOCKED continuation, the orchestrator sends `send --force`, then
runs `callback-consume --slug <s> --stage <stage> --status BLOCKED` after
the continuation succeeds. If the BLOCKED state came from a worker
`forge ask`, the Command Center answer path is different: `forge dispatch
@<session> "<answer>" --answers <ask-id>` consumes the BLOCKED callback
before injecting the answer into pane 1, so the orchestrator relays the
answer to the worker with `send --force` and does **not** run its own
`callback-consume`.

Shape B is the pane-1 local Agent Teams fallback for inline `qa` / `qa-retry`
when throughput routing is unavailable: acquire the lock, open a local
`to: claude` log entry, run the Agent Teams QA inline under the held lock,
close the entry on every exit, release, then render the digest.

### Transition table

| Current stage | Next stage | Notes |
|---|---|---|
| proposal | review | |
| review | incorporate | |
| incorporate | implementation | |
| implementation | impl-review | |
| impl-review | coding | |
| coding | qa | |
| qa | qa-fix or verify | qa-fix only if findings present |
| qa-fix | qa-retry | one re-run only |
| qa-retry | verify | if findings remain → escalate to user |
| verify | STOP | Wait for PR instructions; never open the PR autonomously |

### Stop conditions (pipeline mode)

1. `FORGE_BLOCKED` the orchestrator cannot resolve in one fix attempt
2. `AGENT_FAILED` after one retry
3. Digest returns `BLOCKING_ITEMS > 0` pointing at a real defect
4. Missing prerequisite (no forge session, missing config, worker dead)
5. Verify returns `ISSUES_REMAIN`
6. Preflight HALT (`BRANCH_MERGED_WITH_DRIFT`, `WRONG_DIRECTORY`, `DETACHED_HEAD`, `BRANCH_UNCLEAR`)
7. Explicit user interrupt (`forge-stop`, `forge-pause`, `forge-skip <stage>`)

### Dispatch protocol (per stage)

1. **Dispatch:** `forge-bridge dispatch --slug <s> --stage <s> --worker <w> [--clear]` — renders prompt from `~/.config/forge/prompts/{stage}.txt`, enforces tier, writes to `.dev/forge-tmp/`, logs, sends.
2. **Wait:** `forge-bridge wait --slug <s> --stage <s> --worker <w> [--timeout <s>] [--digest-template <name>]` — blocks until callback or stall classification. Returns one structured block on stdout.
3. **Spawn digest agent:** when `--digest-template` was passed and STATUS=DONE, the bridge returns `DIGEST_PROMPT: <path>`. The orchestrator spawns a background agent with a one-line "follow this file" prompt.
4. **Advance / change-of-course:** if digest is `CONFIDENCE: HIGH` and `BLOCKING_ITEMS: 0`, emit a one-line status and begin the next stage. Otherwise apply the Change-of-Course Heuristic.

Stage templates should include the worker's forge identity and escalation
command:

```text
forge ask --slug {slug} --stage {stage} --worker {worker} "<question>"
```

That command writes a `NEEDS-ASK` board row and a BLOCKED callback. When
the operator answers with `forge dispatch @<session> "<answer>" --answers
<ask-id>`, the answer path archives the BLOCKED callback before injecting
the answer into the orchestrator pane. The orchestrator should relay the
answer to the worker with `send --force`; treating `callback-consume` as a
required second step creates a double-consume race.

Codex panes don't need `--clear`. Claude panes accumulate in-conversation
context, so `--clear` is mandatory when re-dispatching to a pane that
already ran a prior stage. The common pane-0 (claude-opus) reuse cases are
impl-review after incorporate, the implementation fallback after
incorporate, and the verify fallback after impl-review; pane-4
(claude-sonnet) reuse is qa-fix after coding.
<!-- docs-refresh:end section=stage-routing -->

## Pane Layout and Classifier

<!-- docs-refresh:start section=pane-layout-and-classifier -->
### Canonical pane names and aliases

The bridge's `pane_index()` / `pane_name()` functions resolve every alias
to a canonical name; `alias-self-test --strict` keeps the maps in
lockstep across every internal Python `ALIAS_TO_CANONICAL` map.

| Idx | Canonical | Aliases |
|---|---|---|
| 0 | `claude-opus` | `inline`, `general`, `claude-general`, `opus` |
| 1 | `claude` | `orchestrator`, `claude-orchestrator` |
| 2 | `codex-a` | `codex` |
| 3 | `codex-b` | — |
| 4 | `claude-sonnet` | `sonnet` |

### Stall classifier

Stall detection lives inside `forge-bridge wait`; the orchestrator does
not normally call `stall-check` directly during a pipeline run. The
classifier captures the last ~50 lines of a pane, normalizes it via the
V3 strip rules (ANSI escapes, working spinners, ellipses, trailing
whitespace), then matches against the regex tables in
`~/.config/forge/idle-prompts.yml`.

### `idle-prompts.yml` schema

For each worker kind (`claude-opus`, `claude-sonnet`, `codex-a`,
`codex-b`), three optional regex fields:

| Field | What it matches |
|---|---|
| `idle_prompt_anchor` | The static prompt line shown when the worker is idle and ready for input. Claude: `[(Opus\|Sonnet\|Haiku) X.Y] ctx: Nk (P%)`. Codex: `gpt-X.Y … · ~/path` |
| `active_work_marker` | Pattern shown while the worker is actively running a tool. Claude: `<dingbat> word… (Ns`. Codex: `• Working (N…` |
| `past_work_marker` | Completion line left in scrollback after a tool finishes. Claude: `<dingbat> word for Ns`. Codex: `─ Worked for Ns` |
| `approval_prompt` | Pattern shown when the worker needs user approval. Claude: `^ ❯ \d+\. ` (Phase 2). Codex: empty |

The installed `idle-prompts.yml` allows Claude footers with optional
`(1M context)`, and both Codex panes use the same idle/active/past regex
family. The bridge loads the table from
`FORGE_IDLE_PROMPTS_FILE` or `~/.config/forge/idle-prompts.yml`.

Classifier outputs one of:
`IDLE`, `ACTIVE`, `STALLED`, `PROMPTING`, `COMPLETED-PENDING-LOG-RESPONSE`,
`DEAD`, `UNKNOWN`.

`STALLED` fires when the snapshot hash hasn't changed for
`FORGE_STALL_THRESHOLD_S` seconds (default 600) AND there's a pending log
entry for that pane. Per-stage timeouts on `wait --timeout` override the
global threshold for legitimately-long stages (e.g. coding, qa).

### Health vs stall-check

`forge-bridge health` is a session-level "are all 5 panes alive and
running the right process" check; it iterates the canonical pane list,
runs a lightweight version of the classifier, and exits non-zero on any
`DEAD` / `WRONG_PROCESS` / `UNKNOWN`. Use it at kickoff, resume, and
post-compaction (Hard Rule 18).

`forge-bridge stall-check` is a per-pane fine-grained classifier used
internally by `wait`. Don't call it directly during a pipeline run
(Hard Rule 19).

`forge-bridge context` may prepend a `=== Stall Check Status ===` warning
when pending dispatches have not been classified recently; the orchestrator
surfaces that warning before acting.
<!-- docs-refresh:end section=pane-layout-and-classifier -->

## Project Config (`.claude/forge-project.yml`)

<!-- docs-refresh:start section=project-config -->
Every forge project declares a `.claude/forge-project.yml` at its root.
The orchestrator reads it once at pipeline start to build the
`ENVIRONMENT_PREAMBLE` that prefixes every background/digest agent prompt
(Hard Rule 11). The bridge reads `forge.expected_root` and `forge.base_ref`
during `preflight` to detect `WRONG_DIRECTORY` / merge-state halts, and
`_render_preamble` reads service ports plus backend virtualenv activation.

### Schema (observed)

| Section | Field | Purpose |
|---|---|---|
| `project` | `name`, `description` | Identity. Surfaced in agent preambles |
| `forge` | `expected_root` | Absolute path to project root. `preflight` halts if pwd differs |
| `forge` | `base_ref` | Git ref for merge-state checks. Default: `origin/HEAD` or `origin/main` |
| `forge` or top-level | `app_env` | Optional environment label included in the generated preamble when present |
| `services.backend` | `command`, `working_dir`, `port`, `health_check`, `activate_venv` | Dev server config |
| `services.frontend` | `command`, `working_dir`, `port`, `health_check` | Dev server config |
| `testing.backend` | `command`, `working_dir`, `activate_venv` | Backend test runner |
| `testing.frontend` | `unit_command`, `e2e_command`, `working_dir`, `playwright_*` | Frontend test runner + Playwright config |
| `testing.screenshot` | `full_page`, `viewport`, `working_dir` | Playwright screenshot recipes |
| `auth` | `test_email`, `test_password`, `login_endpoint` | E2E test credentials |
| `qa` | `output_dir`, `evidence_dir`, `smoke_pages[]`, `core_workflows[]`, `pixelmatch.*` | QA pipeline settings |

### Example (`promptlol`)

```yaml
project:
  name: "Shield Platform"
  description: "White-label AI-powered tools platform"

forge:
  expected_root: "/Users/sirdrafton/sirtheoracle/automation/promptlol"
  base_ref: "origin/main"

services:
  backend:
    command: "uvicorn src.app.main:app --reload --host 0.0.0.0 --port 8001"
    working_dir: "backend"
    port: 8001
    health_check: "http://localhost:8001/docs"
  frontend:
    command: "npm run dev"
    working_dir: "frontend"
    port: 5180
    health_check: "http://localhost:5180"

testing:
  backend:
    command: "pytest"
    working_dir: "backend"
  frontend:
    unit_command: "npm run test:run"
    e2e_command: "npm run test:e2e"
    working_dir: "frontend"

qa:
  output_dir: ".dev/qa"
  smoke_pages:
    - { name: "Start Project", path: "/start-project", requires_auth: false }
    - { name: "Home", path: "/app", requires_auth: true }
    - { name: "Script Generator", path: "/app/generator", requires_auth: true }
    - { name: "Assessment Wizard", path: "/app/assessment", requires_auth: true }
    - { name: "Content Library", path: "/app/library", requires_auth: true }
  core_workflows:
    - name: "Login and generate script"
      description: "Login as test user, navigate to /app/generator, create a script"
    - name: "Assessment flow"
      description: "Navigate to /app/assessment, complete the wizard end-to-end"
```

Source: `~/sirtheoracle/automation/promptlol/.claude/forge-project.yml`.
<!-- docs-refresh:end section=project-config -->
