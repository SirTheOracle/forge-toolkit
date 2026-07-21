# Handoff: blocked-item lifecycle — plan is FROZEN at R2, produce implementation.md next

**Date:** 2026-07-11
**From:** seat session (incident triage → problem statement → full adversarial-proposal
run → external review R1 → codex review verified + incorporated as R2)
**Task for the NEXT session:** run **adversarial-implementation** on
`.dev/proposals/blocked-item-lifecycle/final-plan.md` (**Revision R2, post-codex-review**)
to produce `implementation.md` (exact diffs + coverage matrix + commit groups), then
**forge-coder** executes it. The plan is FROZEN — do not redesign, do not re-run the
adversarial process, do not relitigate the keep-open decision or the R2 judgment calls.

## 0. What happened today (context in 60 seconds)

1. SwiftBar showed a permanent red `! WORKER-BLOCKED · goparent-ai` while all worker
   panes streamed. Traced: pipeline `parenting-plan-neutral-attorney-disclaimer` (#256)
   blocked twice at `fix-code` (pre-existing Alembic fixture `varchar(32)` failure on
   clean HEAD); the goparentbugs orchestrator received the second BLOCKED callback
   (events log :1677) and moved on without consuming it or closing the pending —
   forge-watch re-raised WORKER-BLOCKED every scan, forever. Banner **acked** at
   operator request (`forge-watch ack parenting-plan-neutral-attorney-disclaimer`).
2. Problem statement written; operator approved the full adversarial run.
3. Full adversarial-proposal run (proposer-a MINIMAL / proposer-b ROBUST /
   synthesizer-c, 4 rounds + external review). Central fork adjudicated: C reversed
   its own synthesis to A's **keep-pending-OPEN** park model. External review
   (SOUND-WITH-FIXES, F1–F12) folded in as R1 — headline: ask-origin carve-out.
4. Operator supplied an independent codex review (`review-codex.md`). All 25 factual
   claims verified against source by 3 parallel agents: **24 CONFIRMED, 1 refuted**
   (SwiftBar newline injection — impossible, `esc()` strips whitespace per-segment
   before clipping). Everything else incorporated → **final-plan.md R2**.

## 1. Artifact map (all in `.dev/proposals/blocked-item-lifecycle/`, all untracked)

| File | Role |
|---|---|
| `problem-statement.md` | R1–R7 requirements (fixed) + §6 open questions |
| `proposal-A.md` / `proposal-B.md` | Round 1 (minimal / robust lenses) |
| `proposal-C.md` | Round 2 synthesis (lead-only, superseded by reconciliation) |
| `review-for-A.md` / `review-for-B.md` | isolated Round 3 inputs |
| `review-external.md` | Round 5 external review (F1–F12, SOUND-WITH-FIXES) |
| `review-codex.md` | operator-supplied independent review of R1 |
| `review-codex-verification.md` | per-claim verdicts w/ line evidence (24/25 confirmed) |
| `reconciliation-notes.md` | every verdict: 19 A/B + EXT-F1–F12 + CDX-1–27 |
| **`final-plan.md`** | **THE deliverable — Revision R2, 6 phases, implementation-ready** |

## 2. The R2 design in one paragraph (details are in the plan; plan is authority)

Keep the pipeline pending OPEN on park (arms the slug-scoped orphan guard against
silent advance-past-parked). `forge-bridge park` flips the callback to
`status: PARKED` and inserts durable sibling fields (`parked_at`/`parked_reason`/
`uncommitted`) into the open log entry — **log fields are authoritative** for every
parked surface (watcher/board/CLI); the callback is an optional transition artifact,
log/callback disagreement = repair finding. Session identity everywhere: callbacks
gain `session:`/`callback_id:` headers + **session-qualified filenames**
(`<slug>-<stage>.<session>.callback`, legacy-read fallback). A per-(root,slug,stage)
**flock + revalidate-under-lock** primitive serializes park/resolve/supersede/
publish/consume. Guard covers `cmd_dispatch` AND worker-target `cmd_send`
(`send --force` exempt only for same-item continuation); one-shot reasoned
`--allow-blocked` replaces the env-var hatch. COMPLETE is **qualified**
(`qualifier=incomplete parked=N blocked=M`) while session-scoped parked/blocked items
exist. Event-path BLOCKED emission **removed** (gated live scan is the sole source,
carries real payload). Ask-origin blocks (stage-mode `forge ask`) are carved out of
guard/park/escalation. Watcher: WORKER-BLOCKED→ITEM-BLOCKED rename (LAST, phase 6),
non-hot ITEM-PARKED (policy=daily), ITEM-BLOCKED-ABANDONED as a guard-bypass audit
backstop (evidence persisted in state.json, keyed callback_id+blocked-ts).
`forge parked` (--root/--session filters) + `forge parked --resolve <slug> <stage>`
(exit 0/10/1). Read-only `forge-bridge blocked-audit` migration dry-run lands BEFORE
guards activate. Six phases: identity/locking/parsers → watcher reconstruction +
surfaces → lifecycle verbs → migration/mirror audit → guards + contract → rename.

## 3. Next steps, in order

1. **adversarial-implementation** on `final-plan.md` R2 → `implementation.md`.
   forge-coder hard-gates on it (coverage matrix, exact old_string/new_string diffs,
   commit groups) and was already invoked once — it correctly REFUSED for missing
   implementation.md. Operator asked "is the plan enough?"; recommendation given and
   accepted direction: adversarial-implementation first (blast radius: ~8k lines of
   bridge/watcher/forge + lockstep docs + 4-5 test suites; coverage risk is the risk).
2. **forge-coder** executes implementation.md (per-group tests, no-auto-commit
   conventions per `.claude/forge-project.yml`).
3. QA/verify per plan's Testing Strategy (concurrency/CAS races, two-sessions-one-root,
   log+send boundary tests, log-only reconstruction, rapid blocked→resolve, infra-lock
   release classes, /forge status both parsers, SwiftBar full-title + ISO ages,
   qualified completion, migration residue).
4. Lockstep rule: `agents/forge-orchestrator.md` + `skills/forge-orchestrator/SKILL.md`
   edited identically (R1 pre-existing drift: agent md lacks callback-consume in the
   FORGE_BLOCKED path — plan fixes it).

## 4. Open residue (not this pipeline's scope — don't lose)

- **#256 goparent-ai fix uncommitted:** diffs applied on branch
  `fix/parenting-plan-neutral-attorney-disclaimer`, blocked only by the pre-existing
  fixture failure. Operator decision pending: waive gate + commit, or queue fixture
  fix first. The acked WORKER-BLOCKED banner no longer points at it.
- The pre-existing Alembic fixture failure (`varchar(32)` truncation, reproduces on
  clean HEAD) is untracked and will block any future parenting-plan-scoped fix-code
  test gate the same way.
- Two forge pipeline runs were live in goparent-ai during this session (goparent +
  goparentbugs); check `forge board` for their current state before dispatching
  anything there.

## 5. Memory pointers (already written)

- `blocked-item-lifecycle-status` — current status (R2, next adversarial-implementation)
- `crash-recovery-status`, `session-pin-hardening-status` — unrelated parallel tracks
- Operator rules that bind the next session: fixes via pipeline only (never ad-hoc
  seat edits); seat dispatches = analysis only; no outward actions without explicit
  go; background agents need output-file watchdogs.
