# Handoff — Cross-Worktree Infra Lock: IMPLEMENT

**Date:** 2026-06-29
**Author:** session (Opus 4.8)
**Status:** Spec **converged at R4**, fully reviewed, **not yet implemented**.
Nothing edited in `bin/forge-bridge`, the skills, or the docs. No commits.
**This doc = the implementation runbook.** The authoritative spec is the plan
doc; this orients you and gives the order + the gotchas.

---

## 0. Read these first (authoritative spec + review trail)

1. **`handoffs/handoff-2026-06-29-infra-lock-plan.md`** — THE SPEC (revised R4).
   Read it whole. Key sections: §3 decisions, §4 lock files, §5 command surface,
   §6 constraints, §7 integration (Shapes A/B + callback lifecycle), §8 files
   touched, §9 verification, §11 restart-on-entry. The `§0 / §0.R2 / §0.R3 /
   §0.R4` tables are the R1→R4 audit trail (what changed and why).
2. Review trail (context for *why* the design is shaped this way — read if a
   decision seems odd before changing it):
   - `handoff-2026-06-29-infra-lock-plan-review.md` (R1 → lifecycle rewrite)
   - `…-review-r2.md` (identity + local-fallback + staleness)
   - `…-review-r3.md` (precision gaps from R2 fixes)
   - `…-review-r4.md` (callback lifecycle — the riskiest piece)

If you change the architecture, you are re-opening settled ground — don't,
unless implementation surfaces a contradiction. The architecture has been stable
since R1; R2–R4 only tightened mechanics.

---

## 1. What this feature is (one paragraph)

Forge runs one pipeline per git worktree, share-nothing, **except** they all hit
one shared infra stack (fixed-port backend/frontend + a shared Postgres). This
adds a single **cross-worktree mutex** so the **infra-touching stages never run
simultaneously** across worktrees, while the five reasoning stages stay fully
parallel. It is a **lock, not a scheduler**; per-worktree isolation is deferred.

- **Infra stages locked:** `coding`, `qa`, `qa-fix`, `qa-retry`, `verify`.
- **Never locked (reasoning):** `proposal`, `review`, `incorporate`,
  `implementation`, `impl-review`.
- **Lock owner:** orchestrator-side — acquire before `dispatch`, release on
  **stage terminality** (not on a single `wait` return).

## 2. Resolved decisions (do not re-litigate)

- **D1 = restart-on-entry, stage-specific.** `qa`/`qa-retry`/`verify` restart the
  shared services against *their own* worktree before testing (so B doesn't test
  A's code). `coding` is **excluded** (keeps forge-coder's "never manage
  services" constraint; relies on Playwright `webServer` autostart). `qa-fix` =
  no restart by default. Restart uses a **config-driven, identity-checked** port
  rule (only stop a process matching the project's expected service shape;
  unknown process on a configured port → **escalate**, don't kill). Spec §11.
- **D2 = keep local QA fallback + amend Hard Rule 22.** Local `qa`/`qa-retry`
  (inline pane-1 Agent Teams when codex-b is down) stays; Rule 22 gains a named
  pane-1 exception gated on `(codex-b unavailable) AND (held infra lock)`. Spec
  §7 "Hard Rule 22 reconciliation".

---

## 3. Implementation order (front-loads the risky shared-surface change)

> Edit protocol (from prior handoffs, MANDATORY): timestamped **backup** →
> edit the **repo** copy → **mirror to the installed runtime path** → review
> `git diff` → **NO auto-commit** (user commits). Re-verify repo↔installed
> parity as a preflight (see §5).

1. **Preflight.** Back up `bin/forge-bridge` (timestamped `.bak`). Confirm
   `diff -q bin/forge-bridge ~/bin/forge-bridge` is identical (was at planning
   time). Note the skills are **NOT** all in sync (see §5).

2. **R4-2 terminal callback ordering — DO THIS FIRST (highest blast radius:
   touches the terminal path EVERY stage uses).** In `cmd_callback`
   (`bin/forge-bridge`, ~1921-2038): for `DONE`/`ERROR`, **close the pending log
   entry first** (drop the `|| true` at ~2006 on the terminal path; a failed
   close must abort and NOT publish), **then publish the callback atomically**
   (write temp → `mv` into `…/callbacks/{slug}-{stage}.callback`). Ship with the
   **`DONE`-publish-race test** (§9 of the spec) before anything else. Mirror to
   `~/bin`.

3. **Non-terminal callback lifecycle + `callback-consume` (R3-3 / R4-1 / R4-3).**
   - `cmd_callback` `BLOCKED`: write callback atomically; **do NOT** call
     `cmd_log_response`; leave the dispatch pending `response:null` open. The
     canonical callback file is the message source of truth; `add-note` is
     best-effort only (make it YAML/arg-safe — current `cmd_add_note` ~2512-2544
     interpolates into Python source; do not feed it raw callback text).
   - `cmd_wait` (~2047-2127): **read-only** — emit the canonical callback's
     status/message and return; **do NOT archive/consume** (read and consume are
     split).
   - New subcommand **`callback-consume --slug --stage --status BLOCKED`**:
     atomic `mv` of the canonical non-terminal callback to
     `…/callbacks/archive/{slug}-{stage}.{callback_id}.callback` (`mkdir -p`,
     preserve bytes, same-fs rename). No canonical `BLOCKED` present →
     **structured no-op**. Add to the main case (~3267-3347) + `--help`.

4. **`cmd_infra_lock` + helpers (the mutex itself).**
   - Anchor: `$(git rev-parse --path-format=absolute --git-common-dir)/forge-infra-lock/`
     with `infra.flock` (brief flock guard only) + `infra.holder` sidecar.
   - Sidecar fields: `host, session, session_id (#{session_id}),
     session_created (#{session_created}), tmux_pid, slug, stage, project_root,
     pid, acquired_at`. Corrupt sidecar → safe escalation, never a traceback.
   - Subcommands: `acquire` (poll loop w/ ceiling), `release`, `status`,
     `release --force`. Branch logic + status verdicts: spec §5.
   - `tmux_owner_live(host, name, id, created)` predicate; **owner key =
     `(host, session_id, session_created, slug)`** for BOTH acquire same-session
     detection AND release match (release omits only `stage`).
   - `LOCK` events via `_emit_event` (internal; **no** `cmd_emit` whitelist
     change). Reuse the `fcntl.flock`/`os.replace` idiom already in
     `_observe_usage` (~389/476).
   - Add the infra-lock line to `_render_status_file` (~1410-1598).
   - Env vars: `FORGE_INFRA_LOCK_TIMEOUT_S` (default 1800),
     `FORGE_INFRA_LOCK_INTERVAL_S` (default 15).
   - Mirror to `~/bin`.

5. **Orchestrator SKILL** (`skills/forge-orchestrator/SKILL.md` → mirror to the
   installed orchestrator SKILL): wrap the five infra stages per Shapes A/B
   (spec §7), incl. the Shape B local **log → run → log-response (close on every
   exit incl. failure) → release → digest** order and the dispatch-failure
   release branch. **Amend Hard Rule 22** (D2 exception) + add **Hard Rule 23**.
   Add the anti-divergence acceptance grep ("ONLY stage that executes locally"
   vs "qa local fallback").

6. **Restart-on-entry in the INSTALLED skills (D1, stage-specific).** Edit repo
   AND mirror to the paths workers actually read (see §5):
   - `qa`/`qa-retry` → `~/.codex/skills/adversarial-qa/SKILL.md`
   - `verify` → `~/.claude/skills/adversarial-verify/SKILL.md`
   - (coding/forge-coder: **do not edit** — excluded by D1.)
   Add the config-driven, identity-checked restart + unknown-process escalation.

7. **Docs:** `docs/forge-technical-reference.md` (Rule 22 amendment + Rule 23,
   `infra-lock` contract, `callback-consume`, callback-archive + non-terminal
   pending semantics, lock files, env vars); `docs/forge-operator-guide.md`
   (multi-worktree concurrency, lock-wait escalation/recovery, resume-safety,
   live-abandoned-holder recovery §6a).

8. **Test harness** (spec §9) — real temp tmux sessions (`tmux new-session -d`),
   NOT bare `TMUX_SESSION` env (a held lock must read *live* or blocking tests
   are meaningless). Then the two-real-worktree manual check.

---

## 4. Verified code facts (line numbers — re-confirm before editing; file is ~129KB)

`bin/forge-bridge`:
- Main dispatch case: **~3267-3347**. `cmd_dispatch` **1725-1910** (creates the
  `response:null` pending via `cmd_log` near 1901 — this is why acquire-before-
  dispatch means no false STALLED).
- `cmd_wait` **2047-2127**; returns on callback-file existence **2092-2097**;
  `_wait_emit_callback` reads but doesn't consume **2129-2163**.
- `cmd_callback` **1921-2038**; **writes callback file (1987-1999) BEFORE
  `cmd_log_response` (2001-2007), with `|| true` at ~2006** ← the R4-2 race.
- `timestamp()` = second-resolution `date -u` **256-258** ← why `wait --after`
  was withdrawn.
- Stall classifier: STALLED **2881-2896**; IDLE-when-no-pending **2823-2831**;
  COMPLETED-PENDING-LOG-RESPONSE **2870-2879**.
- `_emit_event` **319-329** (writes `.dev/forge-tmp/orchestrator-events.log`);
  emit whitelist **2226/2228/3337** (do NOT touch — LOCK is internal).
- `_render_status_file` **1410-1598**; `_render_preamble` **1632-1670**.
- `_observe_usage` flock/`os.replace` idiom **~389/476** (reuse pattern).
- `cmd_add_note` **2512-2544** (fragile — Python-source interpolation; keep
  best-effort).
- Hard Rule 22 (orchestrator SKILL) **1048-1067**; Hard Rule 5 (local log close)
  **904-912**; local QA Path B **636-644**; qa-retry **735-736**.

---

## 5. Skill-path divergence — DO NOT edit repo-only (R3-4)

Runtime prompts point workers at INSTALLED skills, and one **differs from repo**:

| Stage | Worker reads (installed) | repo == installed? |
|---|---|---|
| qa, qa-retry | `~/.codex/skills/adversarial-qa/SKILL.md` | **DIFFERENT** (must mirror!) |
| verify | `~/.claude/skills/adversarial-verify/SKILL.md` | identical (still mirror) |
| coding | `~/.claude/skills/forge-coder/SKILL.md` | identical (not edited) |

Restart-on-entry edits MUST land in the installed path or qa/qa-retry workers
keep the old instructions. Add an acceptance check that **fails if restart-on-
entry text appears only in repo `skills/` and not in the installed runtime file.**
Verify with `shasum repo installed` + a targeted `grep` in both.

Prompt path refs: `~/.config/forge/prompts/{qa,qa-retry,verify,coding}.txt`.

---

## 6. The five things most likely to be gotten wrong

1. **Release on a single `wait` return.** NO. Release on **stage terminality**
   only (`DONE`/`ERROR` callback). HOLD through `PROMPTING`/`STALLED`/`TIMEOUT`/
   `DEAD`/`BLOCKED`. (R1/F1, spec §6-§7.)
2. **Archiving the BLOCKED callback inside `wait`.** NO — deadlocks resume before
   continuation. `wait` reads-and-leaves; `callback-consume` archives **after
   `send --force` succeeds**. (R4-1.)
3. **Letting a polling `wait` see `DONE` before the pending closes.** Close
   first, then publish atomically. (R4-2.)
4. **Owner-key mismatch between acquire and release.** Both use
   `(host, session_id, session_created, slug)`. Host checked first (session ids
   aren't host-unique); `session_created` guards tmux-server-restart id reuse.
   (R2-2/R2-3/R3-1.)
5. **Editing repo skills only / killing any process on a port.** Mirror to
   installed paths; only stop processes matching the project's expected service
   shape, else escalate. (R3-4/R3-7.)

---

## 7. Success criteria (from the problem statement)

- Two worktrees run concurrently; reasoning stages overlap freely; infra stages
  never overlap on the shared DB/ports.
- Killing a worktree mid-infra-stage auto-releases the lock (dead-session steal);
  others proceed without manual cleanup.
- A pipeline waiting on the lock surfaces as waiting (event log + `/forge
  status`), never a false `STALLED`.
- No new shared *state* — only the mutex (ephemeral sidecar).
- Removal is a clean revert: delete `cmd_infra_lock` + the orchestrator wrap +
  doc/skill edits and `rm -rf <git-common-dir>/forge-infra-lock/`. No schema, no
  migration.

---

## 8. Current status snapshot

- Plan: **R4, converged.** All four review rounds incorporated. Two product
  decisions (D1, D2) resolved by the user.
- Code: **untouched.** No backup taken yet, no edits, no commits.
- Next action: start at §3 step 1 (preflight + backup), then step 2 (R4-2).
