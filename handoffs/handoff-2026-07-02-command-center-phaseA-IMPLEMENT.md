# Handoff: Run adversarial-implementation for Command Center v2 — Phase A

**Date:** 2026-07-02
**From:** design/verification session (R1 + R2 reviews folded, round-2 empirical tests executed)
**Task for next session:** invoke the **adversarial-implementation** skill, scoped to **Phase A only**, to produce a vetted `implementation.md` for the command center. Do not re-litigate the design — it is verified and settled; the remaining work is contract precision and diffs.

## Inputs (read in this order)

1. `.tmpFiles/command-center-v2-design.md` — the settled design (treat as `final-plan.md`). R1+R2 folded; round-2 test outcomes annotated inline.
2. `.tmpFiles/command-center-v2-r2-test-results.md` — empirical test results; the acceptance-detection and hook-payload facts are load-bearing.
3. `.tmpFiles/command-center-v2-design-review-r2.md` — R2 review; its Findings + Testing Gaps sections are a requirements checklist for Phase A.
4. `.tmpFiles/command-center-v2-design-feedback.md` — R1 review (background; build-vs-reuse map).

## Phase A scope (from the design's "Implementation order")

Against the four *existing* `forge-1..4` sessions — no spawn, no registry, no account machinery:

1. **`bin/forge` facade** (new script; bridge verbs untouched) with `forge dispatch @<session> "<instruction>"`:
   - writes a dispatch event to target root's `.dev/attention/` BEFORE injecting (schema is mini-spec 1);
   - injects into **pane 1** via the bridge's `cmd_send` 3-step split mechanics (reuse, don't reimplement: select-pane + settle, `send-keys -l`, standalone Enter);
   - pointer-file mode for long/multi-line payloads (atomic tmp+mv, dispatch_id + hash in the one-liner).
2. **Hook layer** in project-scoped `.claude/settings.json` per registered root: UserPromptSubmit, Stop, PermissionRequest, Notification. All hooks: local atomic writes only, gate internally on `TMUX_SESSION` stamp + `FORGE_ROLE=orchestrator`, exit 0 fast.
   - Install via a **JSON merge tool** — live repos already have hooks (headless_factory: PostToolUse; feedforge: PreToolUse; goparent-ai: permissions). Merge, validate, never overwrite.
3. **forge-watch extensions**: `.dev/attention/*.json` ingestion into the existing evaluate/debounce/ack path (poll model — no new API), `status --board` machine-readable output with priority classes (hot / active / maintenance-collapsed), install forge-watch + launchd job + create `~/.config/forge/watch-roots`.
4. **Billing preflight** (small): fail loud if `ANTHROPIC_API_KEY` in env or `apiKeyHelper`/provider routing in settings.

**The implementation doc's first section must BE the mini-spec contracts** (design doc "Required mini-specs" 1–5): dispatch event schema, attention event schema, status-file schema, hook install/merge strategy, billing preflight checklist. Produce and critique them in the same adversarial pass.

## Settled facts the implementers must not violate (all empirically verified 2026-07-02, Claude Code 2.1.198)

- **Absorption**: a dispatch landing mid-turn is queued by the TUI and consumed *within the running turn* — NO separate UserPromptSubmit or Stop fires for it. Acceptance = matching UserPromptSubmit **or** next pane-1 Stop after dispatch time. Idle-landing dispatches get a normal own turn.
- **`last_assistant_message` ships in the Stop payload** (undocumented) — prefer it, keep transcript-tail (JSONL) fallback. Absorption case: it contains both responses concatenated.
- **PermissionRequest** carries `tool_name`, `tool_input`, `permission_suggestions`; it CAN allow/deny, so the center's handler must be fail-open (exit 0, zero stdout — verified the dialog survives). **Notification** messages are generic ("Claude needs your permission" / "Claude is waiting for your input") — visibility-only; `idle_prompt` fires on any ~60s idle.
- **Stop cadence**: exactly one Stop per turn (multi-tool turns, long tool calls, absorbed messages included).
- **Roles**: per-pane env only via launch-command prefix (`FORGE_ROLE=orchestrator claude …` pane 1; `worker` panes 0/4); session-wide stamps via `new-session -e`. `TMUX_PANE` also reaches hooks (bonus attribution key).
- **UserPromptSubmit** carries full prompt text (dispatch_id markers round-trip); no matchers on UserPromptSubmit/Stop (always fire); UserPromptSubmit default timeout 30s and blocks prompt processing — hooks must be fast.
- **Ghost prompt-suggestions** in the input box are NOT submitted by bare Enter; literal text replaces them.
- **Single subscription (decided)**: no `CLAUDE_CONFIG_DIR` machinery anywhere. Keychain findings preserved in the r2 results doc only for a hypothetical future second plan.
- **`callback --quiet` exists** in the live bridge (needed later for Phase B `forge ask`; not Phase A).
- Bridge internals (live `~/bin/forge-bridge`): `cmd_send` split at `:844-854`; pane aliases `:234/:245`; dispatch pointer-file pattern in `cmd_dispatch` `:1743`; state files `.dev/forge-context.<session>.yml` etc.
- forge-watch (`bin/forge-watch`, 779 lines, tests `tests/forge-watch/run.sh` = 53 PASS): read-only contract — never writes under `.dev/`, never touches panes. Attention ingestion must preserve this (attention files are written by hooks/`forge`, forge-watch only reads). Existing conditions/debounce/ack: `:668-672`, `:622-665`.

## Prerequisites (do BEFORE or alongside — from the design's hygiene list)

1. Merge `worktree-infra-lock` (`40df73a`) → main; push (main is 2 ahead of origin).
2. After merge, verify `~/bin/forge-bridge` byte-identity with toolkit; replace manual `~/bin` copies with install.sh symlinks (install.sh skips regular files — they never converge otherwise).
3. Reconcile `config/claude-hooks.json` (points at `forge-dispatch-review`) vs installed `forge-dispatch-pr-review` (untracked in `~/bin`).
4. Commit: modified docs, `bin/forge-watch`, `tests/forge-watch/`, handoffs, and the `.tmpFiles` design/review/results docs if desired.

## Verification for the implementation doc (Phase-A slice of round-2 test 6)

Idle-pane dispatch → event written → accepted via UserPromptSubmit match; mid-turn dispatch → stays `queued-input` → closed by next Stop; multiline pointer-file atomic + hash match; hook installer preserves existing hooks (test against copies of the three live settings.json files); attention events enter forge-watch debounce/ack; board collapses maintenance rows, surfaces the 2 live actionable rows; billing preflight catches a planted `ANTHROPIC_API_KEY`; forge-watch 53-test harness still passes.

## Explicitly out of scope

Phase B (`forge ask`, command-center skill), Phase C (spawn/registry), any bridge verb changes, any `CLAUDE_CONFIG_DIR` work, port assignment.
