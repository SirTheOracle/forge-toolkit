# Handoff: Command Center v2 — Phase A LIVE; next steps

**Date:** 2026-07-03
**From:** Phase A implementation + go-live + hygiene session
**State:** Phase A is built, tested, committed, pushed, installed, and verified against live traffic. This handoff is the resume point for whatever comes next (live usage shakedown, the small backstop items, or Phase B).

## Where things stand (all verified, not aspirational)

### Phase A shipped (forge-toolkit main, pushed through `8dd15d1`)

- `8256814` — `bin/forge` facade (dispatch/preflight/register/gc/status/board), `bin/forge-cc-hook` (single-file Python, STRUCTURAL pane-1 role gate via `TMUX_PANE` — the live sessions carry no `FORGE_ROLE`/`TMUX_SESSION` stamps), `config/claude-cc-hooks.json`, `tests/forge-cc/run.sh` (29 PASS).
- `5be9349` — forge-watch attention ingestion (read-only; one lifecycle state per session: queued > done > working; `NEEDS-PERMISSION` in the notify/debounce/ack path, superseded by a later Stop) + `status --board` (`cc-board/1` JSON, hot/active/maintenance-collapsed). Harness 53 → **67 PASS, 0 FAIL**.
- `c8ee553` — install.sh symlinks the new bins.
- Deliberation trail + `implementation.md` + `coder-report.md` in `.tmpFiles/` (now gitignored — future pipeline artifacts belong in `.dev/proposals/<slug>/`).

### Live infrastructure (running now)

- `~/bin`: **everything is a symlink into the toolkit repo** — forge, forge-cc-hook, forge-watch, forge-bridge, forge-start, forge-dispatch-pr-review. Manual-copy drift can no longer recur.
- forge-watch launchd agent loaded (`com.forge.watch`, 30s tick); `~/.config/forge/watch-roots` holds all four roots.
- All four roots registered: CC hooks merged into each project's `.claude/settings.json` (headless_factory kept PostToolUse, feedforge kept PreToolUse, goparent-ai kept permissions, promptlol created fresh). Backups in the session scratchpad (`settings-backups/`).
- **Live round trip verified end-to-end** on forge-4: dispatch event → bridge inject → `prompt.forge-4.json` with matching `dispatch_id` (acceptance) → `stop.forge-4.json` (snippet_source=`last_assistant_message`) → board `SESSION-DONE` with response snippet. Bonus finding: a live pane-1 session picked up newly-merged hooks **without restart**.
- Board surfaces the 2 real hot rows (`NEEDS-DECISION` forge-1, `ZOMBIE-ACTIVE` forge-4); 19 maintenance rows collapsed.

### Repo hygiene done this session

- `.dev/` untracked in ALL four project repos (it was gitignored but pre-existing tracked files defeated `git check-ignore` — the design's "register cleanly" claim was wrong for three of them): headless_factory `a55e68a` (on `fix/gemini-image-batch-stranded-recovery`), feedforge `155c73b` (on `fix/wpengine-auth-header-shim`), goparent-ai `fc910de` (main), promptlol `c62683c` (on `goparent-card-full-suite`). **None pushed.**
- Toolkit: `.tmpFiles/` + `.claude/worktrees/` gitignored (`a84fe07`); `forge-dispatch-pr-review` adopted + `config/claude-hooks.json` reconciled to the live hook shape (`cee39c6`); skills drift resolved — live edits adopted into toolkit (`487e53d`) and the forked `adversarial-proposal` merged (`8dd15d1`: installed refinements as base + toolkit's "Building the Teammate Prompts" and "Error Handling" sections). **All 8 skills byte-identical between `~/.claude/skills` and the toolkit.**
- infra-lock merged to main + pushed earlier (`b5a3a00`); worktree-infra-lock branch content is in main.

## Next steps (in recommended order)

1. **Push the four project repos' `.dev/`-untrack commits** (and merge/land the fix branches they sit on as their own work completes). Low urgency, just don't lose them.
2. **Periodic `forge gc` backstop** (small): GC only runs when `forge` runs; forge-watch is read-only and cannot delete. Add a launchd/cron job (or a `forge gc` call from the forge-watch launchd tick wrapper — but keep forge-watch itself read-only). Documented as the one Phase-A limitation in `coder-report.md`.
3. **Live shakedown before Phase B** (recommended): put `forge board` in a visible pane, dispatch real work (`forge dispatch @forge-N "..."`), let the bell run. The remaining round-2 test-6 slices verify themselves in normal use: mid-turn absorption live, pointer-file (multiline) dispatch live, a real PermissionRequest event.
4. **Phase B** when ready — the escalation layer (design mini-specs 3, 6, 7):
   - `forge ask --session-scope "<q>"` (attention event only) and `forge ask --slug S --stage T --worker W "<q>"` (event + `forge-bridge callback --status BLOCKED --quiet`; no-pending → session-scope fallback with warning, never fail the worker).
   - Answer lifecycle owned by dispatch: `forge dispatch @<sess> "<answer>" --answers <ask-id>` archives the ask event + `callback-consume` exactly once, then injects. Phase A already reserved `answers_ask_id` in the dispatch schema and the `ask` variant in the attention envelope.
   - Command-center seat skill (verb reference, `@session` addressing, misrouting guardrails) + one contract paragraph in worker skills (ask-worthy vs proceed-and-note; stage templates must surface slug/stage/worker).
   - Same pipeline as Phase A: adversarial-implementation scoped to Phase B → forge-coder. Inputs: `.tmpFiles/command-center-v2-design.md` (Escalation section), `.tmpFiles/command-center-v2-r2-test-results.md`, Phase A's `implementation.md` (schema contracts to extend, not break).
5. **Phase C** (later): spawn/registry (ensure-semantics, single-root invariant, populate-existing) + separate infra-lock leftovers (live two-worktree check, test-harness recreation per infra-lock handoffs).

## Facts the next session must not violate

- All Phase A schema contracts are pinned in `.tmpFiles/implementation.md` Section 1 (`cc-dispatch/1`, `cc-attention/1`, `cc-board/1`, merge algorithm, preflight checklist) — extend, never break.
- forge-watch is read-only: never writes/deletes under `.dev/`, never touches panes. Deletion is `bin/forge`'s job (GC).
- Hook fail-open invariant: `forge-cc-hook permissionrequest` exits 0 with zero stdout on every path.
- Role gate is structural (pane 1) with `FORGE_ROLE` env override; all four hook events are orchestrator-only in Phase A (worker escalation = Phase B ask path).
- Dispatch reuses `TMUX_SESSION=<sess> forge-bridge send claude` — never reimplement the 3-step split. Bridge verbs stay untouched.
- Acceptance: UserPromptSubmit `dispatch_id` match (idle) OR next Stop ≥ dispatch time (absorption). One Stop per turn.
- Billing: subscription-only; preflight fails loud on `ANTHROPIC_API_KEY`/`apiKeyHelper`/provider routing; no `CLAUDE_CONFIG_DIR` machinery anywhere.

## Known open oddities

- `proposal-reviewer` is listed in install.sh `SKILL_NAMES` but exists in neither the toolkit nor `~/.claude/skills` (phantom entry).
- `~/.claude/commands/forge.md` + the forge-orchestrator agent definition remain untracked by the toolkit (long-standing hygiene gap, noted in memory).
- Running `./install.sh` wholesale is now safe for bins and *nearly* safe for skills (all in sync), but its skill step is still `rm -rf` + copy — installed-side `.bak` files would be deleted. Prefer targeted operations or clean the `.bak`s first.
- Process lesson (memory: `subagent-watchdog-discipline`): background agents get an output-file watchdog at spawn; replacements get distinct output paths; "no ping reply" ≠ dead.
