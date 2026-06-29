# Handoff: APPLY the forge session-scoped-state fix (start a fresh session here)

**Date:** 2026-06-02
**Status:** DESIGN + IMPLEMENTATION DOCS COMPLETE AND VETTED — **not yet applied to code.**
The fix was designed via the adversarial-proposal skill and turned into an exact,
source-verified implementation via the adversarial-implementation skill. This
session's job is to APPLY it under the global-edit protocol, accounting for ONE
live pipeline (goparent-ai) that is mid-run.

## How to start the new session

Open a fresh session in `/Users/sirdrafton/sirtheoracle/automation/forge-toolkit`
and say:

> "Apply the forge session-scoped-state fix per
> `handoffs/handoff-2026-06-02-session-scoped-state-IMPLEMENT.md`. Read
> `.dev/proposals/forge-session-scoped-state/implementation.md` and execute it
> under the global-edit protocol. Coordinate the live goparent-ai pipeline."

Also read first:
- `~/.claude/projects/-Users-sirdrafton-sirtheoracle-automation-forge-toolkit/memory/`
  (`forge-global-edit-protocol`, `codex-soft-reset`, `forge-perpipeline-log-corruption`)
- The prior design handoff: `handoffs/handoff-2026-06-01-session-scoped-state.md`

## What's already done (do NOT redo)

The root cause was confirmed; the SOLUTION was designed and the diffs written +
validated on a scratch bridge. Artifacts (NOTE: `.dev/` is gitignored — these are
untracked, reference by absolute path):

- **`.dev/proposals/forge-session-scoped-state/final-plan.md`** — the vetted design.
- **`.dev/proposals/forge-session-scoped-state/implementation.md`** — THE deliverable
  to execute: 13 steps, exact `old_string → new_string` diffs (source-verified;
  the doc opens with a "Verified anchors" table because the plan's line numbers
  were stale), a zero-gap 17-row coverage matrix, scope + shared-helper matrices,
  full Accept/Partial/Reject reconciliation, change groups, and a Definition of Done.
- **`.dev/proposals/forge-session-scoped-state/tests/test-session-scope.sh`** —
  runnable suite (tmux PATH-stub harness, merged single-runner). Validated:
  **66 passed / 0 failed post-fix; ~37 failed pre-fix** (it discriminates the bug).
- Full audit trail also on disk: `impl-A.md`, `impl-B.md`, `impl-C.md`,
  `review-for-{A,B}.md`, `impl-notes-{A,B}.md`, `proposal-{A,B,C}.md`,
  `reconciliation-notes.md`.

## What the fix changes (one-paragraph orientation)

Forge runtime state is project-scoped; it must be session-scoped. The fix
introduces a unified per-call resolver `_resolve_session` (+ `_session_or_sentinel`,
`_context_file`, `_count_live_forge_candidates`) and makes these per-session:
the context pointer (`forge-context.<session>.yml`), the rendered status file
(`forge-status.<session>.md`), and the send-hook pending check (per-entry
`session:` filter, copied verbatim from the shipping `cmd_stall_check_status`
precedent). No-session fallback = `__nosession__` sentinel; multi-live-session
walk-up ambiguity → refuse → sentinel (the "B-lite" guard). `.forge-session`
stays a single file (registry deferred). All in `bin/forge-bridge` + a one-line
SKILL note. See implementation.md for the exact steps.

## ★ Operational coordination — ONE live pipeline ★

**goparent-ai is RUNNING and in-flight** and MUST be accounted for:

```
project: /Users/sirdrafton/sirtheoracle/automation/goparent-ai
pipeline (slug): court-ready-phase-2
next_stage: implementation
context file (pre-upgrade): .dev/forge-context.yml   ← shared, will be superseded
```

Other projects are NOT in-flight (do not need a resume): headless_factory =
FINISHED (user-confirmed 2026-06-02); feedforge + forge-canary-sandbox =
`next_stage: complete`; promptlol = stale (May 9, abandoned). Only goparent-ai
needs the post-upgrade resume.

### Why goparent-ai is affected and exactly what to do

The change edits `~/bin/forge-bridge` — the installed binary **every project/
session invokes**. After the mirror is synced, the next `forge-bridge context`
in goparent-ai's session resolves `forge-context.<session>.yml`, which does not
exist yet → it sees "no active pipeline." State is **fully regenerable** from the
untouched per-slug log; resume with ONE command per in-flight session.

Nothing is killed/corrupted: it's a shell-script edit invoked per command — no
tmux session, Codex/Claude worker, or per-slug log is touched. In-flight worker
callbacks still close (open log entries have no `session:` field; the
never-skip-legacy rule matches them regardless).

### The one real risk to time around — mid-command race

If a `log-response`/callback fires in the instant `~/bin/forge-bridge` is being
overwritten, that single command could run against a half-written script. Apply
during a QUIESCENT moment (no orchestrator mid-`dispatch`/`callback`). goparent-ai
is between stages (`next_stage: implementation`), so pausing it briefly is easy.

## Apply procedure (ordering minimizes live blast radius)

The implementation.md change groups already encode this; the key sequencing point
is that the TOOLKIT copy is edited + tested with ZERO live impact, and the live
`~/bin/forge-bridge` is only touched at the final sync step.

1. **Backups first** (global-edit protocol). Save current
   `~/bin/forge-bridge` + the two orchestrator copies into
   `~/.cache/forge/edit-backups/2026-06-02-session-scope/` (rollback = restore).
2. **Apply Steps 1–12 to the TOOLKIT copy** `bin/forge-bridge` exactly as diffed.
   This does NOT affect any running pipeline (nothing invokes the toolkit copy
   directly).
3. **Gate on the toolkit copy** (still zero live impact):
   - `bash -n bin/forge-bridge` clean
   - `grep -c FORGE_STATUS_FILE_NAME bin/forge-bridge` == 0 (dead const removed)
   - `FORGE_BRIDGE=bin/forge-bridge bash .dev/proposals/forge-session-scoped-state/tests/test-session-scope.sh`
     → 0 failed (66 assertions). Optionally run once against PRE-FIX source to
     confirm it FAILS (~37) — proof it discriminates.
4. **Quiesce goparent-ai** — make sure its orchestrator is not mid-callback.
5. **Sync mirrors (this is the live-impacting step):** copy the patched bridge to
   `~/bin/forge-bridge`; apply the one-line SKILL note identically to toolkit
   `skills/forge-orchestrator/SKILL.md`, `~/.claude/agents/forge-orchestrator.md`,
   and `~/.claude/skills/forge-orchestrator/SKILL.md` (bodies must match).
   `diff -q` toolkit vs `~/bin` should be clean.
6. **Verify installed copy:** `~/bin/forge-bridge alias-self-test --strict`
   → `OK (13 aliases, 4 maps)`.
7. **Resume goparent-ai (the one in-flight pipeline):** in its session/project,
   `forge-bridge set-context --slug court-ready-phase-2`
   → writes `forge-context.<session>.yml` from the per-slug log; pipeline resumes,
   zero data loss. Confirm with `forge-bridge context` showing
   `active_pipeline: court-ready-phase-2`, `next_stage: implementation`.
8. **Manual M1** (once): on a real `forge-start`/tmux session, confirm a live
   `send-keys` keystroke still lands in pane 0 (the stub can't cover real keys).
9. Leave the legacy shared `forge-context.yml` files on disk (ignored post-fix;
   operator may `rm` at leisure). **NO auto-commit** — show the `git diff` and let
   the user review/commit.

## Hard constraints (carry over verbatim)

- **Global-edit protocol:** backup first; edit BOTH toolkit source AND the
  installed mirrors (`~/bin/forge-bridge`, `~/.claude/agents/forge-orchestrator.md`,
  `~/.claude/skills/forge-orchestrator/SKILL.md`); show the git diff; **NO
  auto-commit.**
- **Do NOT "fix" the documented out-of-scope corner.** The `"unknown"`-stamp
  false-negative on the UNPINNED single-candidate auto-detect path is intentionally
  left as-is (pinned path is immune; treating `"unknown"` as legacy would desync
  from the verbatim `cmd_stall_check_status` filter precedent). It is documented +
  pinned by tests 8.4.5 (immunity) and 8.4.3b (the documented skip). Do not touch it.
- **Do NOT reintroduce `reset-workers` / `/clear` automation.** Session-scoping is
  its prerequisite; that work stays parked on `feat/per-pane-context-reduction`.
- Must stay correct for the single-session common path (covered by the suite).

## Definition of done (from implementation.md §8)

bash -n clean · 4 helpers before `require_tmux_session` · shadow `CONTEXT_FILE`
local in `update_context`/`cmd_context`/`cmd_add_note`/`_render_status_file` ·
`cmd_context` has NO `require_tmux_session` · `cmd_log` keeps the literal
`"unknown"` stamp · `has_pending_log_for` heredoc passes 3 argv, ALIAS map
byte-identical, both call sites pass `$SESSION` · `cmd_status` reads the
per-session status file via the shared `_status_file_name` · dead const removed
(grep==0) · suite 66/0 post-fix (and fails pre-fix) · `alias-self-test --strict`
OK on the installed copy · mirrors synced + `diff -q` clean · goparent-ai resumed
via `set-context --slug court-ready-phase-2`.

## Repo state at handoff

- Branch `main` @ `0c963a9`. Working tree: the only untracked item is the
  `.dev/proposals/forge-session-scoped-state/` artifact set (gitignored) + earlier
  handoff(s). No code applied yet.
- The per-pane-context work remains on `feat/per-pane-context-reduction` (not main,
  not installed).
