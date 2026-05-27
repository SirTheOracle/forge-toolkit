# Forge Handoff — 2026-05-26 (orphan-log-entries fix-plan)

Session-continuity doc. Written so the current session can be cleared
(no compaction needed). Pick this up fresh.

This is a **continuation** of `handoff-2026-05-26.md` (the "Open thread —
orphan log entries" section). That handoff left the orphan-log fixes
undecided; this session took them through diagnosis + adversarial fix-planning
+ an independent code review. **No production code has been touched yet** — the
deliverable is a vetted plan awaiting your go to implement.

---

## TL;DR — where we are and the one next action

- The orphan-log-entries **fix plan is complete, vetted twice, and ready to
  implement**: `forge-files/.dev/proposals/orphan-log-entries/fix-plan.md`
  (**v2**, Status: ACTIVE, CONFIDENCE: MEDIUM, BLOCKING_ITEMS: 0).
- It went through the full 4-round `adversarial-fix-plan` skill, THEN an
  independent review (`review-codex.md`) that found 5 real implementation gaps —
  **all 5 verified against `~/bin/forge-bridge` and folded into v2.**
- **Immediate next action (awaiting user go):** implement the code. The user
  asked "does this need `adversarial-implementation`?" — answer given: **no**
  (wrong pipeline + design judgment already spent). The real choice is
  **hand-roll (recommended) vs `fix-coder`**. User has NOT yet said go.
- **Hard constraint:** edits to `~/bin/forge-bridge` and
  `~/.claude/agents/forge-orchestrator.md` are **user-reviewed via `git diff`**,
  with timestamped `.bak-pre-<reason>-YYYYMMDDTHHMMSS` backups, **no
  auto-commit**.

---

## What this session did

1. **Read the prior handoff** (`handoff-2026-05-26.md`), picked the "confirm
   diagnosis first" path.
2. **Confirmed the diagnosis AND corrected it.** The prior handoff claimed "0
   silent stalls." Re-analysis *per-pipeline* (the prior trace was cross-pipeline
   and masked danglers) found: of 34 pending entries — **25 moved on** (closed
   same-pipeline successor → discipline gap), **1 is live** (<30 min, excluded),
   **8 are dangling terminal one-shots** that the log alone can't classify as
   completed-vs-stalled. So "0 stalls" is too strong; it's "no mid-pipeline
   freezes, 8 ambiguous one-shots." This drove a **PARTIALLY CONVERGED** diagnosis.
3. **Wrote inputs** to `forge-files/.dev/proposals/orphan-log-entries/`:
   `problem-statement.md`, `diagnosis.md` (PARTIALLY CONVERGED, CONFIDENCE
   MEDIUM, the H3 stall hypothesis deferred behind Required Data D1).
4. **Ran `adversarial-fix-plan`** (full 4 rounds, Agent Teams). Produced
   `fixA.md` (Surgical), `fixB.md` (Robust), `fixC.md` (synthesis),
   `review-for-A.md`, `review-for-B.md`, `reconciliation-notes.md`, and
   `fix-plan.md` (v1). Team `fixp-orphan-log-entries` created and deleted cleanly.
5. **Incorporated `review-codex.md`** (an independent Codex review the user
   dropped in). Verified all 5 findings against `forge-bridge`, then amended
   `fix-plan.md` → **v2** by hand (not via the skill's revise mode — the findings
   were concrete and bounded). Added a "Review Incorporation (v2)" section.

---

## The deliverable — fix-plan.md v2 (read this file; it is the spec)

`forge-files/.dev/proposals/orphan-log-entries/fix-plan.md` is prescriptive to
the line. Summary of the committed change set:

**Root cause (H2):** entry *creation* is decoupled from *closure*; the only
auto-closer is `cmd_callback`→`cmd_log_response` (forge-bridge:1484), which only
fires on a worker callback. Three classes orphan: (1) local `to: claude` work
(no callback), (2) stage-advance without close, (3) rapid re-dispatch.

**The changes (file-level; fix-coder/hand-roll produces diffs):**

| Change | Where | What |
|---|---|---|
| `_patch_response_by_ts` (NEW helper) | forge-bridge | No-context patch primitive: rewrite `response: null`→string on a ts anchor in BOTH per-pipeline + summary log, reusing the regex at 817-851, **without** calling `update_context`. Must land FIRST. |
| `cmd_dispatch` guard + `--supersede` | forge-bridge ~1320 | After dry-run return (1366-1369): slug-scoped CHECK — refuse if ANY pending exists for the same `--slug` (reuse `ALIAS_TO_CANONICAL`) unless `--supersede`. With `--supersede`: **loop**-close all same-slug pendings via the helper, **deferred to just before `cmd_log` at 1387** (after `require_tmux_session` 1371 + `/clear` 1382). Add to usage 1334 + help. |
| D2 WARN | forge-bridge `cmd_log_response` 771-788 + 856-859 | When `len(candidates)>1`: still close `candidates[-1]`, emit stderr WARN **+ audit event** naming N-1 skipped. **Audit event is the durable signal** (callback path suppresses stderr at 1484-85). Exit code MUST stay 0. |
| D3 status staleness | forge-bridge `_render_status_file` heredoc 1044-1106 / 1155 | Read `FORGE_STALL_THRESHOLD_S` from env (default 600) IN the heredoc, compute `stale=2*threshold` locally (the 2366 constant is out of scope). Parse UTC `Z` ts vs `datetime.now(timezone.utc)` (1148 uses local now()); skip on missing/malformed; works on yaml + regex-fallback (1074-1100) paths. Append `(STALE — pending since <ts>, possible orphan)` to "Current stage" (1155). Label-only, doesn't change which pending is chosen. |
| Orchestrator prose | forge-orchestrator.md | 3 edits enforcing close-before-advance for the **12** `to: claude` local stages, using EXISTING `log-response --to claude --stage {stage}` (both flags, to clear ambiguity guard 778-783). Targets: "Advancing Through Stages" step 1 (~624-641); proposal-stage detail (518-528); Hard Rule 5 (~815). NOT the 6 claude-sonnet/opus entries (guard covers those). |
| D1 migration script | one-shot fix-coder artifact (NOT a permanent verb) | Enumerate pending from each SUMMARY log, classify at RUNTIME (counts NOT hardcoded), rewrite via the helper in BOTH summary + matching per-pipeline log: moved-on (has closed successor)→`FORGE_ORPHAN_CLOSED`; dangling-terminal→`FORGE_ORPHAN_QUARANTINED` (preserves H3 evidence); fresh <30min→untouched. Back up both files; post-pass consistency check; fail closed on bad YAML. |

**Locked design decisions (do NOT relitigate without cause):**
- **Slug-scoped predicate**, not `(stage,to)` — the latter misses class-2
  (diagnosis.md:77, feedforge #83/#84). Both planners conceded this.
- **`--supersede` must LOOP** — `cmd_log_response` closes only `candidates[-1]`
  (count=1, 786/823) and errors on filterless multi-pending (778-783). A single
  delegated call silently fails to clear duplicates. Convergent A+B catch.
- **No `FORGE_DISPATCH_GUARD` env kill-switch** — declined (A over B). `--supersede`
  is the legitimate per-call bypass; a global silent disable would re-enable the
  bug. Loud failure + `.bak` file-revert is the recovery.
- **Watchdog/auto-arm-`wait` (D4) + terminal-stale flag (D5) + a new `close`
  verb — all DEFERRED.** H3 evidence is weak; Required Data D1 is low-severity;
  there's no orchestrator-pane stall signal to poll (`idle-prompts.yml` has
  regexes for the 4 worker panes only). Build D4 only if D1 (git history /
  scrollback on the quarantined terminals) confirms a real stall.
- **Diagnosis refinement (verified):** the diagnosis's "17 to:claude*" is really
  12 genuine local `to: claude` + 6 worker-pane (`claude-sonnet`=4/5,
  `claude-opus`=1/2) that DO get callbacks. The class-1 instruction targets the
  12; the guard covers the 6. Counts drift; don't hardcode.

---

## The 5 review findings (verified this session — why v2 ≠ v1)

All verified against `~/bin/forge-bridge`:

1. **`cmd_log_response` is NOT pure — it writes context.** Non-`FORGE_DONE/
   BLOCKED/ERROR` → `status="unknown"` (722-729) → unconditional `update_context`
   (856-859) rewrites `last_stage_status: unknown` + empty `next_stage` (386-394)
   AND emits a STAGE heartbeat (413). → sentinels MUST go through the new
   no-context helper, never the public verb.
2. **D2 stderr WARN invisible on callback path** — `cmd_callback` runs
   `cmd_log_response …>/dev/null 2>&1` (1484-85). → audit event is the durable
   signal; needs a callback-path test.
3. **D3 can't reuse the 2366 constant** — it's in `cmd_stall_check_status`'s
   heredoc, out of scope for `_render_status_file`'s heredoc (no threshold,
   local now() at 1148). → read env + robust UTC parse + skip-on-malformed.
4. **Migration must patch BOTH logs** — `status` reads per-pipeline (1068);
   `has_pending_log_for`/count read summary (`$SUMMARY_LOG`). `cmd_log_response`
   already patches both (696, 842-851); migration must too + consistency check.
5. **Defer destructive supersede-close** — `require_tmux_session` (1371) + `/clear`
   (1382) sit between dry-run return and `cmd_log` (1387). Closing first then
   failing leaves a slug with closed-old + no-replacement. → close just before 1387.

(Plus a minor #4-adjacent: orchestrator examples already use `--to claude
--stage`, which clears the ambiguity guard — reinforced, not changed.)

---

## Next action — implement (awaiting user go)

**Do NOT use `adversarial-implementation`.** Reasons given to user: (a) it's a
*build-pipeline* skill consuming `final-plan.md`, not `fix-plan.md` — category
mismatch; the fix-pipeline's implementation stage is `fix-coder`; (b) the design
judgment is already spent (4-round adversarial + independent review); what's left
is mechanical translation + QA on the fiddly bits (the YAML regex string-replace;
dual-log migration consistency). The original handoff said the same: "too small."

**Pick one (user's call):**
- **Hand-roll in-session (recommended):** backup both files → edit `forge-bridge`
  (helper → guard+`--supersede` → D2 → D3) → orchestrator prose → write +
  dry-run the migration against log COPIES → present the full `git diff` for
  review before any commit.
- **`fix-coder`:** the pipeline's mechanical executor; reads `fix-plan.md`,
  applies, runs tests, writes `fix-coder-report.md`. Equivalent; more ceremony.
- **`fix-plan-reviewer` first:** SKIP — `review-codex.md` already served this role.

**Implementation order (from plan Sequencing):** helper (step 0) → forge-bridge
edits (guard/supersede 1a, D2 1b, D3 1c) → orchestrator prose (step 2) →
migration (step 3, after guard lands).

**Smoke tests (match session-pinning convention):**
- `~/bin/forge-bridge alias-self-test --strict` → expect OK
- `~/bin/forge-bridge health` from an active project
- Cause-fix: in `forge-canary-sandbox` (0 pending), dispatch without closing →
  second dispatch refused; `dispatch --supersede` → prior closed `FORGE_SUPERSEDED`,
  one new pending. Re-run the count snippet → pending doesn't grow with an open prior.
- `--supersede` leaves `forge-context.yml` byte-unchanged until a real callback.
- Multi-pending supersede closes ALL (feedforge #36-39 shape).
- Migration `--dry-run` on COPIES → both logs patched consistently; <30-min
  untouched; YAML still parses; `status` no longer shows a stale orphan as current.
- D2 callback-path: duplicate pendings + `forge-bridge callback` → exit 0 + audit
  event present (stderr WARN absent).
- D3: with small `FORGE_STALL_THRESHOLD_S`, stale→tagged, fresh→untagged,
  malformed/missing ts→untagged + no error, chosen pending unchanged.
- Regressions: `dispatch --dry-run` writes NO entry; `has_pending_log_for` send
  hook (549-562) unchanged; `cmd_callback`→auto-log-response happy path still closes.

---

## Verified forge-bridge line anchors (current as of this session)

- `cmd_dispatch` ~1320; dry-run return 1366-1369; `require_tmux_session` 1371;
  `/clear` 1382; `cmd_log` (pending birth) 1387; `cmd_send` 1388.
- `cmd_log_response` 669; status derivation 722-729; candidates matcher 771;
  ambiguity guard 778-783; `candidates[-1]` 786; string-replace 817-851
  (pipeline 826-840, summary 842-851); `update_context` call 856-859.
- `update_context` context-write 386-394; STAGE event 413.
- `cmd_callback` auto-log-response suppressed `>/dev/null 2>&1` 1484-1485.
- `_render_status_file` heredoc 1044-1174; per-pipeline read 1068; regex fallback
  1074-1100; local `now()` 1148; `pending[-1]` 1103-1106; "Current stage" 1155.
- `cmd_stall_check_status`: `2*FORGE_STALL_THRESHOLD_S` at 2366; session-scoped
  filter at 2395.
- `ALIAS_TO_CANONICAL` map ~2368-2374 (also inline in cmd_log_response heredoc 742-749).
- `has_pending_log_for` 289; used in `cmd_send` 549-562.
- `cmd_alias_self_test` 2423; `cmd_health` 2504.
- orchestrator.md: "Advancing Through Stages" step 1 ~624-641; proposal stage
  detail 518-528; Hard Rule 5 ~815; logs `--from claude --to claude` at 522.

(Verify before editing — these were accurate this session but the file is live.)

---

## File map

- **Proposal dir:** `/Users/sirdrafton/sirtheoracle/automation/forge-files/.dev/proposals/orphan-log-entries/`
  - `fix-plan.md` ← **THE SPEC (v2)**. `diagnosis.md`, `problem-statement.md` ← inputs.
  - `review-codex.md` ← the independent review (left in place; intentionally NOT
    named `fix-review.md` so it won't trigger the skill's revise mode).
  - `reconciliation-notes.md`, `fixA/B/C.md`, `review-for-A/B.md` ← adversarial trail.
- **Code to edit (global):** `~/bin/forge-bridge`,
  `~/.claude/agents/forge-orchestrator.md`.
- **Stage templates:** `~/.config/forge/prompts/*.txt` (NO change — all 24
  already instruct `callback`); `~/.config/forge/idle-prompts.yml` (worker-pane
  regexes only).
- **Live data with orphans:** `feedforge`, `goparent-ai`, `headless_factory` —
  `.dev/forge-log.yml` (summary) + `.dev/proposals/{slug}/forge-log.yml` (per-pipeline).

---

## Still-open threads (NOT this session's work — carried from prior handoff)

- **Session-pinning fix bundle + docs-refresh scaffold** (forge-bridge +
  orchestrator + forge-files/docs) — landed in the PRIOR session, **still
  awaiting your `git diff` review**. Don't conflate with the orphan-log edits.
- **Stale `~/.dev/.forge-session`** contains `phase2-smoke` — harmless (errors
  loudly) but should be `rm`'d eventually.
- **Memory updates never written** (prior handoff flagged these; still not done):
  a memory entry for the session-pinning bundle; one for the orphan-log
  investigation/fix-plan so a future session doesn't redo it. Consider writing
  after the code lands.

---

## Constraints / environment reminders

- **No auto-commit. No editing `forge-bridge`/orchestrator without showing the
  user the `git diff`.** Timestamped `.bak-pre-<reason>-YYYYMMDDTHHMMSS` backups.
- **direnv:** never use `source .venv/bin/activate` / `source .env` — venv + env
  are auto-active. Run commands directly.
- `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` is enabled (adversarial skills work).
- User runs many forge pipelines in parallel (forge-1..7, canary). A live
  `configurable-categories-tones/review` entry in feedforge was in-flight during
  this session — not an orphan.
