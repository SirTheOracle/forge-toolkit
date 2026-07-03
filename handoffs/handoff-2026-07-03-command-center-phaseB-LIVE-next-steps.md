# Handoff: Command Center v2 — Phase B LIVE; next steps

**Date:** 2026-07-03 (afternoon session, follows `handoff-2026-07-03-command-center-phaseA-LIVE-next-steps.md`)
**From:** Phase A backstops + full Phase B pipeline + go-live session
**State:** Phase B (the escalation layer) is designed-through-adversarial-pipeline, built, tested, committed, pushed, deployed live, and smoke-verified end-to-end. This handoff is the resume point for the real-use shakedown or Phase C.

## Where things stand (all verified, not aspirational)

### Phase A leftovers closed first (this session, before Phase B)

- **All four `.dev/`-untrack commits are on GitHub.** feedforge/goparent-ai/promptlol pushed on their existing branches. headless_factory's pre-push hook blocks re-pushing merged branches, so `a55e68a` was cherry-picked onto `chore/untrack-forge-dev-state` → **PR #168** (open, ready to merge; the local `fix/gemini-image-batch-stranded-recovery` branch still shows ahead-1 — its content is in the PR, safe to delete after merge).
- **Periodic gc backstop shipped** (`10daf48`): `forge gc` (no-root) now sweeps registered watch-roots ∪ live tmux session paths and exits 0 explicitly; new `forge gc --install/--uninstall` writes+loads `com.forge.gc` launchd agent (daily 03:17 + RunAtLoad; logs `~/.cache/forge-gc/`). Installed live, verified (exit 0, stale swept, fresh kept). install.sh uninstall unloads both agents.

### Phase B shipped (main, pushed through `7251034`)

Pipeline: adversarial-implementation (impl-A surgical / impl-B coverage → synth-C → cross-critique → reconciled `implementation.md`) → forge-coder, all artifacts in `.dev/proposals/command-center-phase-b/` (impl-A/B/C, reviews, feedback, implementation.md, coder-report.md).

- `c455ba1` — **`forge ask`** (`--session-scope` | `--slug S --stage T --worker W`; stage mode rides the bridge's existing `callback --status BLOCKED --quiet`; no-open-pending → session-scope fallback + warning; NEVER fails the worker) and **`forge dispatch --answers <ask-id>`** (order is load-bearing: billing preflight → atomic archive-claim = exactly-once gate → session-mismatch guard → `callback-consume` (stage asks only) → event write with `answers_ask_id` → inject; double-answer/unknown/traversal fail loud pre-inject; post-claim failures route through `_dispatch_fail` with "answer recorded, re-deliver with a PLAIN dispatch" guidance). forge-cc harness 35 → **57 PASS**.
- `851b32b` — **forge-watch `NEEDS-ASK`**: session-optional ingestion, hot, carries question snippet + slug/stage, first in NOTIFY (ask > BLOCKED), `STATE_OF → needs-input`, NOT superseded by a later Stop (unlike NEEDS-PERMISSION), `ZOMBIE_AGE_S` residue cutoff, WORKER-BLOCKED twin suppressed on `(root,slug,stage)`, archived asks invisible (depth-1 glob). forge-watch harness 67 → **77 PASS**.
- `7251034` — **`skills/command-center/SKILL.md`** (seat skill: board/dispatch/ask-answer verbs, @session addressing, misrouting guardrails), `install.sh SKILL_NAMES += command-center`, escalation contract paragraph in BOTH forge-coder skills, orchestrator SKILL.md answer-relay sequence (**seat's `--answers` consumes the callback; orchestrator must NOT re-consume**).

### Live deployment (running now)

- bins live via the existing `~/bin` symlinks; skills synced byte-identical: `~/.claude/skills/{command-center,forge-coder,forge-orchestrator}`, `~/.codex/skills/forge-coder`.
- **Stage templates surfaced (S9):** `~/.config/forge/prompts/_ask_escalation.txt` partial + `<<<INCLUDE _ask_escalation>>>` in all 23 stage templates (inserted after the `_git_ident`/`_preamble` anchor). Bridge dry-run renders verified for coding/verify/qa — workers see their exact `forge ask --slug … --stage … --worker …` line. Backup: `~/.config/forge/prompts.bak-20260703T1710`. NOTE: prompts are out-of-repo operator config — the toolkit does NOT track them.
- **Live smoke passed end-to-end on forge-4/promptlol:** ask → event (0600) → board hot `NEEDS-ASK` row with question → `dispatch @forge-4 … --answers <id>` → `DISPATCHED … answers=<id>`, ask archived (not deleted), dispatch event `answers_ask_id` set, **acceptance confirmed** (`prompt.forge-4.json` carried the matching dispatch_id) → double-answer refused (`already answered (archived)`, rc=1) → board clean.

### Known deviations / adaptations (full detail: `coder-report.md`)

- **D1:** `|| true` on the two `IFS read`s of newline-less python output in `bin/forge` — `read` returns 1 at EOF-without-newline and `set -e` killed `cmd_ask` mid-function (10 harness FAILs before the fix). The implementation.md diffs are otherwise applied verbatim.
- The DoD's whole-file `python3 compile()` of `bin/forge-watch` is unrunnable as written (it's a bash script); satisfied via harness + per-heredoc-block compile.
- Live DoD items 4 (stage-mode smoke) and 5 (session-mismatch) deliberately skipped: 4 needs an open pipeline stage (only live open pending is forge-1's REAL 4-day NEEDS-DECISION — don't contaminate it); 5 needs two sessions sharing a root (live layout doesn't have one). Both fully covered hermetically; 4 self-verifies on the first real worker escalation.

## Next steps (in recommended order)

1. **Answer forge-1's real NEEDS-DECISION** (issue-reporting, qa-retry, idle 5d now) — it predates the ask path (no ask event), so answer it as a plain `forge dispatch @forge-1 "<decision>"` after reading the question in the tab. It is the oldest hot row on the board.
2. **Real-use shakedown of the escalation loop:** run normal pipelines; the first worker that hits a blocking decision exercises live stage-mode (one NEEDS-ASK row, no WORKER-BLOCKED twin, `wait` returns BLOCKED, `--answers` resumes it, callback lands in `callbacks/archive/`). Watch for: double notifications, rows that don't clear, the orchestrator wrongly re-consuming (its SKILL.md now forbids it).
3. **Merge PR #168** (headless_factory untrack) when convenient; land the other three fix branches with their own work.
4. **Phase C** when ready — spawn/registry (design "Spawn" section + inherited v1 debt list): ensure-semantics, single-root invariant, `--populate-existing` with the error-trap contract, name sanitization-by-transformation, registry schema + `fcntl.flock` locking, first-launch dialogs as expected states. Plus infra-lock leftovers (live two-worktree check, test-harness recreation per infra-lock handoffs).

## Facts the next session must not violate

- Everything in the Phase A handoff's list still holds (schema contracts pinned in Phase A `implementation.md` §1; forge-watch read-only; hook fail-open; dispatch reuses `TMUX_SESSION=<sess> forge-bridge send claude`; acceptance = UserPromptSubmit id match OR next Stop ≥ dispatch time; subscription-only billing).
- Phase B additions: the ask event extends the reserved `cc-attention/1` ask envelope **plus an additive `ask_id`**; filename `<ask_id>.json`; `archive/` is the exactly-once claim (never delete a live ask by hand); ask-id charset `ask-[0-9A-Za-z-]+`; `NEEDS-ASK` outranks `WORKER-BLOCKED`; asks are NOT stop-superseded; the seat's `--answers` dispatch owns callback consumption — the orchestrator never re-consumes; `forge ask` must never exit nonzero on a non-usage path (worker safety).
- GC (`-maxdepth 2` under `.dev/attention/`) reaches `archive/` and `payloads/` — 7-day TTL applies to archived asks too; that is the intended retention, not a bug.
- Bridge verbs remain untouched — Phase B calls `callback`/`callback-consume`/`send` as-is.

## Known open oddities

- `proposal-reviewer` is still a phantom in `SKILL_NAMES` (exists in codex-skills only, not skills/ or ~/.claude/skills).
- `~/.claude/commands/forge.md` + the forge-orchestrator agent definition remain untracked by the toolkit (long-standing hygiene gap, in memory).
- install.sh's skill step is still `rm -rf` + copy; all synced skills are currently byte-identical so a wholesale run is safe today, but installed-side edits would be clobbered — prefer targeted sync.
- `forge-bridge dispatch --dry-run` tier guard rejected `proposal` for all workers — pre-existing pane-1-only routing, not a Phase B regression.
- Process lesson (this session): synthesizer C ran 21 min silent then delivered — respawned C2 had to be stood down. Background agents doing long single-write work should get a **write-early heartbeat contract** (create the output file immediately, append as they go) at spawn time, not after silence (extends memory: `subagent-watchdog-discipline`).
