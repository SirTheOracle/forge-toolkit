# Handoff — `stale-alert-lifecycle` Fix Plan → fix-plan-reviewer (NEXT)

**Date:** 2026-07-21 (session ran into 2026-07-22 early AM). **Author:** team-lead (Fable 5).
**For:** a fresh session starting the review stage. **Context can be cleared before reading this** —
everything needed is below or linked. **Do not re-diagnose or re-plan; both are settled, adversarially
vetted, and code-verified.**

---

## 1. What this is

Recurring operator-facing bug: SwiftBar / `forge board` showed stale hot alerts (3× NEEDS-ASK,
1× WORKER-STALLED for goparent-ai pipelines) that were factually resolved days earlier and never
cleared. Root cause (CONVERGED, execution-verified): **hot-alert lifecycle closure depends
exclusively on one happy-path action (`forge dispatch --answers` / `--supersede`) with no
reconciliation against forge-log state**; only backstop is the 7-day ZOMBIE window. Full mechanism
detail: `.dev/proposals/stale-alert-lifecycle/diagnosis.md` (§3a asks, §3b orphan pendings, §6
constraints/hazards — §6 is load-bearing for review).

## 2. What is DONE

- **Live cleanup (2026-07-21, applied + verified):** 3 resolved `ask-*.json` archived to
  `goparent-ai/.dev/attention/archive/`; orphan `fix-code` pending in
  `pp-holiday-schedule-real-names/forge-log.yml` closed as FORGE_SUPERSEDED (`.bak-pre-orphan-close-20260721`
  backup beside it). `forge board --json` verified **0 hot rows**. The four reported symptoms are gone;
  the plan below targets recurrence only. NO source code has been changed yet.
- **adversarial-fix-plan COMPLETE** — full 4-round cycle (isolated Surgical A / Robust B → synthesis C
  → both critiqued from original contexts → reconciliation). Deliverable:
  `.dev/proposals/stale-alert-lifecycle/fix-plan.md` — **Status ACTIVE, CONFIDENCE: HIGH,
  BLOCKING_ITEMS: 0**. Audit trail: `reconciliation-notes.md` (14 ACCEPTED / 2 PARTIAL / 0 REJECTED).
  Working artifacts all present in the same dir (fixA/fixB/fixC, review-for-A/B, problem-statement).

## 3. The plan in one paragraph (details in fix-plan.md — source of truth)

CENTER position, two independently shippable commit groups. **Group 1 (`bin/forge-watch`, read-only,
ship first, fully resolves the alert symptom class):** C1 extract `_read_log_entries()` (all
entries), `pending_entries` stays a byte-identical thin filter (tests call it directly —
run.sh:1946/:2012); C2 build `RESOLVED_STAGES_BY_ROOT` (stage has **parsed log AND ≥1 entry AND all
closed**) + `closed_max_ts`; C3 WORKER-STALLED demotes to existing status-only STALE-PENDING iff a
strictly-newer CLOSED dispatch-ts exists (dispatch-ts vs dispatch-ts — like-kind, dodges the §6
timestamp trap); C4 per-site NEEDS-ASK suppression for stage-mode asks on the resolved-stage
predicate (session-scope never suppressed); C5/D3 `has_live_pending` at the NEEDS-DECISION gate
ONLY (:1018) — an orphan otherwise hides a LIVE decision prompt (the §6-dominant hazard); zombie
path stays raw. **Group 2 (`bin/forge-bridge`, staged writes, ship second):** D1 ask archival on
terminal close at BOTH sites (cmd_log_response AND the --supersede loop — supersede gap caught in
Round 3), gated on no-open-pending, non-fatal, + optional BLOCKED-twin consume; D2
`WARN_ORPHAN_PENDING` audit for cross-`to` orphans, NO auto-close (P1-WC incarnation strictness).

## 4. Verified facts a reviewer should NOT re-litigate (all code-verified by ≥2 agents)

- A lingering ask file is NOT inert: `_callback_ask_origin` (bridge:~2957-2969) reports it
  unresolved and `forge park` REFUSES (bridge:~3244-3248). Real second-order bug; fixed by D1;
  deliberately staged second on scope grounds.
- ITEM-BLOCKED-unmask is UNREACHABLE under the final predicate: ITEM-BLOCKED emits only from
  `live_blocked_rows` ← `pending_owners` ← OPEN pendings (watcher:637-693) — mutually exclusive
  with zero-open suppression; leftover callback renders as status-only CALLBACK-FOREIGN (:691).
  Convergently proven by A and B independently; reconciler re-verified and dropped its own
  post-scan co-drop filter.
- "No open pending" alone is UNSAFE (corrupt log ⇒ hides a live ask); the predicate must require
  a successfully parsed log with ≥1 entry, all closed. Fail-safe direction is always keep-alerting.
- Env is prod-rigor: bin/ tools are live via symlinks (~/bin, ~/.claude); global-edit protocol
  (backup + git-diff review, NO auto-commit) applies to forge-watch/forge-bridge edits.

## 5. The one immediate next step

Run **`fix-plan-reviewer`** (single-agent adversarial review):

- Inputs: `.dev/proposals/stale-alert-lifecycle/diagnosis.md` + `fix-plan.md`
- Output: `fix-review.md` in the same dir
- If review finds issues → re-invoke `adversarial-fix-plan` (auto-enters revise mode on
  fix-review.md presence). If clean → **`fix-coder`** executes fix-plan.md
  (commit group 1 then group 2, tests with each group, per [[fixes-via-pipeline-only]]).

## 6. Ops notes for the next session

- Memory file `stale-swiftbar-alerts-diagnosis.md` is current (diagnosis + plan status + next steps).
- Multi-agent ops gotcha observed twice this session: teammate SendMessage completions can arrive
  LATE or not at all — never diagnose "stuck" from silence; check artifact mtimes / transcript growth
  (`~/.claude/projects/<proj>/<session>/subagents/*.jsonl`) and arm a filesystem watchdog on expected
  output paths. A premature duplicate synthesizer was spawned this session because of exactly this;
  it was stopped before writing anything.
- Uncommitted repo residue from this session (intentional, commit when user says so):
  `.dev/proposals/stale-alert-lifecycle/*` (all pipeline artifacts) + this handoff. goparent-ai state
  edits (ask archive moves, forge-log patch + .bak) live outside git concerns.
