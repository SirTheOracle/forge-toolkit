# Handoff — Cross-Worktree Infra Lock: IMPLEMENTED

**Date:** 2026-06-29
**Author:** session (Opus 4.8)
**Status:** **Implemented** on git worktree branch `worktree-infra-lock`
(`.claude/worktrees/infra-lock/`). All repo edits done, mirrored to the installed
runtime paths, **65 automated assertions pass**. **NOT committed** (per edit
protocol — user commits). One manual step remains (live two-worktree run).

Implements the R4-converged spec: `handoff-2026-06-29-infra-lock-plan.md`.
Runbook followed: `handoff-2026-06-29-infra-lock-IMPLEMENT.md`.

---

## What was built (in §3 order)

1. **R4-2 terminal callback ordering** (`bin/forge-bridge` `cmd_callback`).
   Terminal `DONE`/`ERROR` now **closes the pending log entry first, then
   publishes the callback atomically** (temp→`mv`). The old `|| true` is gone — a
   failed close aborts and does **not** publish. Hardened `cmd_log_response` so
   its second (patch) python block propagates failure (it previously dropped the
   exit code), which is what makes "failed close aborts" real.
2. **Non-terminal `BLOCKED` + `callback-consume`** (R3-3/R4-1/R4-3).
   `BLOCKED` publishes the callback atomically and **keeps the pending open** (no
   `log-response`). `wait` stays **read-only** (already was). New
   `callback-consume --slug --stage --status BLOCKED` atomically archives the
   canonical callback to `callbacks/archive/{slug}-{stage}.{callback_id}.callback`;
   structured no-op when there's nothing to consume. Added a `callback_id` field
   to the callback file (filename-safe id) to name archives. Wired into the main
   case + `--help`.
3. **`cmd_infra_lock` + helpers** (the mutex). Anchor
   `$(git rev-parse --path-format=absolute --git-common-dir)/forge-infra-lock/`
   (override `FORGE_INFRA_LOCK_DIR`), `infra.flock` guard + `infra.holder`
   sidecar. `acquire`/`release`/`status`/`release --force`. Owner key
   `(host, session_id, session_created, slug)`; `tmux_owner_live` uses **real
   tmux**. `LOCK` events via `_emit_event` (internal). All sidecar I/O is
   `yaml.safe_*` (injection-safe); corrupt sidecar → safe `ESCALATE`. Env
   `FORGE_INFRA_LOCK_TIMEOUT_S=1800`, `FORGE_INFRA_LOCK_INTERVAL_S=15`.
   `_render_status_file` gained an `Infra lock:` line.
   Test seam: data-only `FORGE_LOCK_SELF_*` overrides for the *acquiring*
   identity (never eval'd; liveness still uses real tmux — respects R2-9).
4. **Orchestrator SKILL** (`skills/forge-orchestrator/SKILL.md`). Added **Hard
   Rule 23** (terminality rule + Shapes A/B + DEAD sub-policy + BLOCKED
   hold-and-continue + env). **Amended Hard Rule 22** for the gated local
   `qa`/`qa-retry` pane-1 fallback (D2). Wrapped all five infra stages
   (coding/qa/qa-fix/qa-retry/verify) with acquire/release in Stage Details +
   QA Fix Loop. Footer hash regenerated (`33c0f6…`).
5. **Restart-on-entry** in the **installed** skills (D1, R3-4): config-driven,
   identity-checked, **escalate-not-kill** on unknown port processes — added to
   `~/.codex/skills/adversarial-qa/SKILL.md` (qa/qa-retry) and
   `~/.claude/skills/adversarial-verify/SKILL.md` (verify), and the repo copies.
   coding/qa-fix deliberately excluded.
6. **Docs:** `docs/forge-technical-reference.md` (new "Cross-Worktree Infra Lock"
   section + bridge-command rows + Rule 23 row) and `docs/forge-operator-guide.md`
   (new "Multi-Worktree Concurrency" section: lock-wait escalation, abandoned-
   holder recovery, resume safety, fresh-code guarantee).

## Mirrored to installed runtime paths (deployed)

- `bin/forge-bridge` → `~/bin/forge-bridge` (was byte-identical; straight copy).
- `skills/adversarial-verify/SKILL.md` → `~/.claude/skills/...` (straight copy).
- `skills/forge-orchestrator/SKILL.md` **body** → `~/.claude/skills/...`,
  **preserving the installed file's own frontmatter** (repo↔installed differ only
  in frontmatter — the known hygiene gap; verified frontmatter byte-identical to
  pre-edit backup, body lossless incl. all 18 `---` rules).
- `~/.codex/skills/adversarial-qa/SKILL.md` edited **in place** (it diverges from
  the repo by ~68 lines — NOT mirrored from repo, which would regress it; the
  restart block was added to both independently).

Backups: `~/.forge-edit-backups/20260629T180040Z-infra-lock/`.

## Tests (all green)

Scratchpad harnesses (real temp tmux sessions for liveness, per F8/R2-9):
- **callback lifecycle — 15/15**: BLOCKED keeps pending open; consume archives;
  consume no-op; DONE closes-then-publishes; failed close does NOT publish; no
  temp leaks.
- **infra-lock — 25/25**: mutual exclusion (live holder blocks+timeouts), steal
  on dead session, server-restart id reuse steal, reentrancy, stage-update,
  CONFLICT (same live session/different slug), foreign-host wait + release no-op,
  full-key release no-op, idempotent + force release, corrupt→ESCALATE (no
  traceback), injection-safe session_id.
- **acceptance — 18/18**: installed bridge has the subcommands; restart-on-entry
  is in the **installed** skills (not repo-only); forge-coder constraint intact;
  anti-divergence (Rule 22 contradiction removed); Rule 23 + terminality +
  callback-consume present; both unit suites re-pass **against `~/bin`**.

## Remaining manual step (needs a live project + services)

The **two-real-worktree concurrency run** (spec §9 final; the D1 restart-on-entry
live tests; unknown-process-on-port escalation): start two worktrees of a real
forge project, run concurrent pipelines, confirm reasoning stages overlap and
infra stages never overlap on the shared DB/ports, and that B's qa hits B's code.
Cannot be done without a live forge session + running services.

## Commit / revert

- Branch `worktree-infra-lock` holds the repo edits (6 files, +995/−28). Review
  `git diff`, then commit + merge as desired. **Not auto-committed.**
- Clean revert: `git checkout` the 6 files, restore installed paths from the
  backup dir, `rm -rf <git-common-dir>/forge-infra-lock/`. No schema/migration.
