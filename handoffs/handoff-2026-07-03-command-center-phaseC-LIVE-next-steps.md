# Handoff: Command Center v2 — Phase C LIVE; v2 build COMPLETE

**Date:** 2026-07-03 (evening session, follows `handoff-2026-07-03-command-center-phaseB-LIVE-next-steps.md`)
**From:** Phase B loose ends + full Phase C pipeline + go-live session
**State:** Phase C (spawn/registry/populate) is designed-through-adversarial-pipeline, built, tested, committed, pushed, deployed live, and smoke-verified end-to-end. Command Center v2 (Phases A+B+C) is feature-complete. This handoff is the resume point for the real-use shakedown.

## Where things stand (all verified, not aspirational)

### Phase B leftovers closed first (this session, before Phase C)
- **forge-1's stale NEEDS-DECISION cleared** — issue-reporting had already merged as PR #163; a close-out dispatch (`--answers`-less, per handoff) made the orchestrator close the pipeline state. Second live exercise of the dispatch path.
- **PR #168 merged** (squash `3b48125`) + both leftover local branches deleted (content verified in main first).
- **Infra-lock harness recreated + committed** (`ea7dfa1`): the lost 58-assertion scratchpad suite is now `tests/forge-infra-lock/run.sh` — 63 hermetic assertions, green, zero behavior deltas vs the merged code. Prerequisites 2–4 from the design (symlinks, hook-config reconcile, clean tree) verified already done.

### Phase C shipped (main, pushed through `fd35b30`)
Pipeline: adversarial-implementation (impl-A surgical / impl-B coverage → synth-C → cross-critique → **lead-reconciled** `implementation.md`) → forge-coder (lead, inline). Artifacts in `.dev/proposals/command-center-phase-c/` (phase-c-plan.md, impl-A/B/C, reviews, feedback, implementation.md, coder-report.md).

- `386fd40` — **registry**: `~/.config/forge/registry.yml` (`FORGE_REGISTRY_FILE` override), `cc-registry/1`, MAP keyed by repo_id = realpath of the git common dir (worktree-invariant, clone-distinct); `fcntl.flock` + tmp/`os.replace` + re-parse-before-publish; LOUD refuse on corrupt YAML / unexpected shape / dup-alias; `color_slot`/`port_range` reserved **null** (declaration, not allocation); `register --alias` + `register --seed` (watch-roots import, registry-only, bypasses hooks).
- `cd45b43` — **`forge spawn`**: billing preflight FIRST (dry-run *reports* the verdict, rc 2; real path dies); register skipped in dry-run (mutates nothing — T14); ensure by session_path identity (healthy → no-op + stale-event clear; unhealthy → needs-repair recorded, never reused/killed); tmux duplicate-session error as the create mutex, loud COLLISION for name-at-other-root; session-wide `-e` stamps (TMUX_SESSION/FORGE_INITIATIVE/FORGE_ROOT); first-launch nudge keyed to CREATE; `on_spawn` from forge-project.yml (name appended as final argv; failure → rc 4, session ALIVE, board row); post-spawn verify then clear-on-PASS-only. Events: `cc-spawn/1`, overwrite-keyed `spawn-<session>-<state>.json`, swept by the 7d attention GC.
- `db766aa` — **`forge-start --populate-existing`**: validates 1-pane, inherits the session, per-pane `FORGE_ROLE` launch prefixes (pane1 orchestrator, 0/4 worker, codex unstamped; EMPTY in plain mode), EXIT-trap + success sentinel (reports partial-split, tears down only this run's panes, never kill-session, never touches `.dev/.forge-session`). Plain no-arg path byte-identical — proven by a golden argv log captured from the PRE-edit script (`tests/forge-start/`, 22 asserts incl. two real-tmux proofs).
- `307a894` — **forge-watch**: `SPAWN-NEEDS-REPAIR` / `SPAWN-POPULATE-FAILED` (hot) / `SPAWN-FIRST-LAUNCH` (hot, policy once), all `needs-input`, ZOMBIE_AGE_S residue cutoff, additive-only. 77 → **88 PASS**.
- `c908b68` — config template documents `forge.control_center.on_spawn`.
- `fd35b30` — fix: `mapfile` → portable while-read (`/bin/bash` is 3.2) — caught on the FIRST live spawn; the leftover 1-pane session was then correctly refused as needs-repair (invariant validated on a real partial state).

### Live deployment (running now)
- bins live via existing `~/bin` symlinks (no install.sh run needed; skills untouched this phase).
- **Registry seeded live**: `forge register --seed` → all four roots (headless_factory, feedforge, goparent-ai, promptlol).
- **Live smoke passed every DoD item** on scratch roots (since cleaned): real 5-pane spawn with stamps verified + hooks merged + registry entry + board pickup + first-launch row → ensure re-run no-op → same-name/other-root COLLISION refused → `on_spawn` exit-3 → rc 4, headless session alive, `SPAWN-POPULATE-FAILED` on the board → cleanup verified (`tmux ls` exactly forge-1..4).

## Next steps (in recommended order)
1. **Real-use shakedown** (unchanged from Phase B handoff, now covering spawn too): run normal pipelines; first real worker `forge ask` self-verifies stage-mode; first genuinely-new project spawn (`forge spawn --root <new>`) exercises hook install + registry + populate end-to-end. Watch for: double notifications, rows that don't clear, orchestrator re-consuming callbacks.
2. **Two-worktree infra-lock live check** (still gated on live services): two worktrees of a real project, concurrent pipelines — reasoning stages overlap, infra stages serialize; B's qa hits B's code.
3. Possible small hardening (non-blocking, from coder-report): `mkdir -p .dev` before the register gitignore gate (a `.dev/` gitignore pattern can't match a not-yet-existing dir — new roots currently need `mkdir .dev` first; error is loud and self-explanatory); registry shape-check adopts any valid-YAML dict (only `repos` is validated).

## Facts the next session must not violate
- Everything in the Phase A + B handoff lists still holds (bridge verbs untouched; forge-watch read-only; hook fail-open; subscription-only billing; `--answers` owns callback consumption; `cc-*/1` schemas additive-only; attention GC 7d/-maxdepth 2 is intended retention).
- Phase C additions: **identity is session_path, never the name** — a healthy live session at the target root IS the initiative regardless of name (ensure/no-op); needs-repair sessions are recorded and refused, never reused, never killed; populate mode NEVER runs `kill-session` and NEVER touches `.dev/.forge-session`; the plain `forge-start` no-arg path must stay byte-identical (golden test guards it — update the golden only as a deliberate act); registry is declaration only (color/ports reserved null, no allocation); `spawn-<session>-<state>.json` filenames are the removal contract — spawn clears its own failure states on verified success, first-launch is never auto-cleared; `register --seed` is registry-only (no hooks).
- forge-start launch strings carry `FORGE_ROLE` ONLY in populate mode — forge-1..4 remain stamp-free/structural (HC5).

## Known open oddities
- The Phase B list still stands (proposal-reviewer phantom in SKILL_NAMES; `~/.claude/commands/forge.md` + orchestrator agent def untracked; install.sh skill step is rm-rf+copy; bridge dry-run tier guard rejects `proposal`).
- goparent-ai ZOMBIE-ACTIVE (plan-generation-request-hang) is stale residue of a CONCLUDED investigation (all 6 issues were host-suspend artifacts, zero product bugs) — no live session owns that root; it ages into maintenance, or the newly-shipped `forge spawn` can re-point a session at goparent-ai to close it out properly (first real spawn use-case!).
- Process lesson ×3 this session: background agents in heavy-multi-file-read roles (synthesizer, reconciler) die silent. The proven recovery ladder: write-early heartbeat contract at spawn → two-strike watchdog (ping at strike 1 — it recovered C2 once) → stop-then-respawn → **lead does the round inline** (all pipeline artifacts on disk = zero loss). Codified in memory `subagent-watchdog-discipline` + `command-center-v2-status`.
