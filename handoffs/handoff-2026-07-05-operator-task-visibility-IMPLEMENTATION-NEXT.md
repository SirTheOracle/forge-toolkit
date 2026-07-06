# Handoff: Operator task visibility — adversarial run COMPLETE, implementation next

**Date:** 2026-07-05
**From:** adversarial-proposal session (follows `handoff-2026-07-05-operator-task-visibility-NEXT.md`)
**Task for the NEXT session, in order:**
1. **Confirm operator approval of `final-plan.md`.** As of this handoff the plan is
   written and summarized to the operator but NOT yet approved. If not approved, stop
   and walk the operator through it (summary pattern: core determination → 8 steps →
   decisions left to them → honest bounds). Do nothing else first.
2. **Get the operator's Q1 answer** (ambient delivery surface: which menubar-class app /
   phone push or not). The plan deliberately does not assume it; implementation of
   Step 5 layer 2 needs the choice. Also collect the standing "go" decisions list (§4).
3. **Run the pre-implementation verification gates** (§3 — the Step 7 codex gates;
   analogous to the LV gates in the return-path phase). Record results in
   `.dev/proposals/operator-task-visibility/verify-results.md`.
4. Run the FULL `adversarial-implementation` skill on the approved plan. Deliverable:
   vetted `implementation.md` (exact diffs, test specs, coverage matrix proving every
   plan step 1–8 is addressed). Do NOT apply code — forge-coder executes in a later
   step, per the Phase A/B/C and return-path pattern.

## 1. Status: adversarial-proposal run COMPLETE 2026-07-05

Full 4-round Teams flow ran clean (zero agent deaths this run): two isolated proposers
(A=simplicity, B=scalability) → synthesizer C (independent code exam first) → isolated
cross-critique → reconciliation. Notable deliberation facts: **B reversed itself on its
own daemon** after re-verifying the launchd plist (no `KeepAlive` — nothing resident to
die), and A conceded the one HIGH defect in its own design (per-pane keying reimports
the LWW collapse → per-turn keying). Reconciliation: 22 ACCEPTED / 3 PARTIAL /
0 REJECTED.

**Input for the next session (read in this order):**
1. `.dev/proposals/operator-task-visibility/final-plan.md` — THE plan (450 ln). Binding
   once approved. Includes: §5 latency contract, §6 open-question positions, §9
   hard-constraint compliance table.
2. `.dev/proposals/operator-task-visibility/reconciliation-notes.md` — why each decision
   landed (don't re-litigate settled debates; the reasoning is here).
3. `.dev/proposals/operator-task-visibility/problem-statement.md` — operator-verbatim
   value bar, evidence timeline, §5 inviolable constraints, §2 codex-hooks CORRECTION.
   (proposal-A/B/C.md + review-for-*.md are the deliberation trail; consult only on
   ambiguity.)

## 2. Plan shape (orientation only — final-plan.md is authoritative)

**Root cause:** three local gaps (emission / state / delivery), NOT an architectural
one. **No new resident daemon, no second ledger** — the launchd 30s job spawns a fresh
process per scan (zero resident-death modes); the per-id attention files are already
the durable per-task log.

Eight steps, execution order:
1. `bin/forge-cc-hook`: role branch replaces the pane-1-only drop. Worker
   UserPromptSubmit mints per-turn `task_id` (`dispatch_id` if present, else
   `ptask-<ts>-<hex>`) → `wprompt.<sess>.p<pane>.json`; worker Stop writes durable
   per-turn `wstop.<sess>.p<pane>.<task_id>.json`; worker PermissionRequest →
   `wperm.*`. Canonical `stop.<session>.json` untouched — worker Stop can NEVER mark
   session done. **Per-turn keying is load-bearing** (per-pane = LWW collapse).
   Recorded assumption: wprompt sibling LWW is safe only because turns are sequential
   per pane (same assumption as existing dispatch correlation).
2. `bin/forge-watch` scan_attention ingests `w*` files → task-scoped PANE-DONE
   (policy 'never'; `FORGE_WATCH_NOTIFY_PANE_DONE` knob, default 0). Board-noise rule:
   SESSIONS = latest-per-pane; full per-turn list in `tasks[]` recency window; older →
   maintenance collapse.
3. `tasks[]` in cc-board/1 (additive) as a read-time fold over dispatch-<id>.json +
   payloads/response.<id>.txt + wstop files; TASKS board section; read-only
   `forge tasks` verb (projection only — no task-creating verb ever).
4. `forge gc`: 7d sweep becomes task-aware — UNTERMINATED task records exempt;
   forge-watch raises `TASK-STUCK`. (A stuck task must never vanish silently.)
5. Delivery = three layers: (a) zero-resident floor — `forge board` + heartbeat
   freshness line; (b) **load-bearing ambient surface** — menubar-class, consumes
   cc-board/1 ONLY, launchd-supervised, renders its own staleness, app-swappable
   (operator picks per Q1); (c) hardened ring — terminal-notifier (nonzero on fail →
   `DELIVERY-UNVERIFIED` row) + `delivered.log` audit + `selftest`/`--confirm` +
   daily re-test; per-event receipt = existing `acked` primitive. osascript stays
   fallback.
6. Event-driven latency: detached `nohup forge-watch check &` fired from forge-cc-hook
   (turn-ends), forge-bridge `_emit_event` (BLOCKED/COMPLETE now ~1s not 30s), and
   forge dispatch/ask writers. Existing non-blocking flock coalesces. Latency contract:
   ~1s typical / ≤30s guaranteed / burst-stragglers wait for next trigger or floor
   (known bound, accepted v1). Ring-TIMING change is operator-gated (bell discipline).
7. Codex parity: managed lifecycle hook (requirements.toml) + thin adapter mapping
   codex stdin keys onto the internal shape, reusing redaction/snippet/write body.
   `agent:"codex"` ⇒ worker. `notify` slot untouched. `CODEX-EMISSION-OFF` maintenance
   row for untrusted roots. GATED on §3 verifications.
8. Direction A = one skill paragraph (structural registration does the work; milestones
   optional, never load-bearing). Direction B (overseer agent) deferred — never a
   polling LLM loop.

Directions verdict: C's GOAL ships v1 without a daemon; A later (thin); B deferred.

## 3. Pre-implementation verification gates (Step 7 codex items)

All were doc-verified only — never run on this box. Analogous to LV-1/LV-3 last phase:
1. **requirements.toml semantics** — placement (user-level vs repo-level vs
   MDM-implied), and consciously accept "managed hooks cannot be disabled by users"
   (off-switch lost). If it doesn't fit, fall back to non-managed `/hooks` per-hash
   trust review (one-time; forge roots are already trusted).
2. **Real codex payload capture** — run a codex Stop/UserPromptSubmit hook in a scratch
   root and confirm field names (`last_assistant_message`, `prompt`, `session_id`,
   `cwd`, `turn_id`). NOTE: this executes a hook in a live codex session — treat as an
   outward action, needs explicit operator go under current rules.
3. **`$TMUX_PANE` inheritance** into codex processes (pane precision is optional —
   `agent` tag classifies without it; fail-open regardless).
Record all three in `verify-results.md`. Only gate 1's OUTCOME changes diffs (managed
vs /hooks path); gates 2–3 confirm the adapter's field mapping.

## 4. Operator decisions to collect (beyond plan approval)

- **Q1 surface:** which ambient app (SwiftBar/xbar/other), phone push yes/no.
- **Ring-timing go:** Step 6 makes rings fire sooner (same conditions, no new rings).
- **terminal-notifier install:** optional one-line brew install; without it ring
  failure is not self-detecting (ambient + board carry the bar).
- **PANE-DONE ring** stays default-off; flipping is config, not code.
- **Live-fire schedule:** selftest + acceptance run (4 tasks: 2 dispatched, 1 typed
  pane 0, 1 codex; all four provably signal) — each requires a per-action go.

## 5. Hard context (unchanged from prior handoff — still binding)

- **ALL project work remains FROZEN** until this ships with value (GoParent's
  final-plan.md still waits unreviewed).
- **Seat-operator interaction rules in force:** no tmux/dispatch/notification/osascript
  without explicit per-action go; constraints carried verbatim (flag interpretations);
  a stop order covers diagnostics too.
- problem-statement.md §5 constraint list is inviolable; final-plan.md §9 maps
  compliance item-by-item — keep that mapping true through implementation.
- Subagent watchdog discipline: verify bytes on disk / transcript growth, ping once,
  lead-does-it-inline as last resort (this run needed none; the 07-04 run needed all).

## 6. Repo / environment state at handoff

- Toolkit on `main` at 2415505; bins symlinked into ~/bin → **working tree is LIVE**.
- Working tree: `docs/forge-operator-guide.md` + `docs/forge-technical-reference.md`
  modified (return-path docs follow-up, uncommitted per no-auto-commit protocol);
  5 untracked handoffs (3× 2026-07-04, 2026-07-05-NEXT, this file). Operator commits.
- Suites green at: forge-cc 91, forge-watch 121, spawn 31, forge-start 22,
  infra-lock 63. Implementation must state expected new totals per suite (return-path
  pattern) and rewrite the existing "pane-0 Stop ignored" test to assert namespaced
  emission instead.
- Codex facts verified 2026-07-05: codex-cli 0.142.5; lifecycle hooks available
  (hooks.json / `[hooks]`); `notify` slot single and OCCUPIED (Computer Use client) —
  chaining not setting; docs: https://developers.openai.com/codex/hooks
- Live evidence dirs (READ-ONLY): `~/sirtheoracle/automation/headless_factory/.dev/attention/`,
  `~/.cache/forge-watch/state.json`. `watch.env`: NOTIFY_REPLY=1, NOTIFY_DONE=1.
