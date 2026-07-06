# Handoff: Response return path — plan APPROVED, run adversarial-implementation

**Date:** 2026-07-04
**From:** adversarial-proposal session (follows `handoff-2026-07-04-response-return-path-ADVERSARIAL-NEXT.md`)
**Task for THIS session, in order:**
1. Confirm the tree is clean (§3 — operator commits the two pending changes; if still
   dirty, stop and ask).
2. Run the **LV-1/LV-3 live checks** (§2a item 1 — zero new code needed; can start with
   the forge-1 stand-down dispatch, §6). Record the results in
   `.dev/proposals/response-return-path/lv-results.md`; they determine whether Step 3b
   ships as planned or switches to transcript-derived correlation.
3. Run the FULL `adversarial-implementation` skill on the approved plan. Deliverable:
   vetted `implementation.md` (exact diffs, test specifications, coverage matrix proving
   every plan item — including all seven §2a R1 items — is addressed). Do NOT apply code
   changes — forge-coder executes the implementation doc in a later step, per the
   Phase A/B/C pattern.

## 1. Status: the plan is APPROVED (Revision R1)

The adversarial-proposal run completed 2026-07-04 (full 4-round Teams flow: two isolated
proposers → synthesis → isolated cross-critique → reconciliation). The operator reviewed
the plan, walked through the practical workflow, and approved moving to implementation.
**Then an independent external review (`review-codex.md`) was verified against the code
and folded in as §0 Revision R1 of final-plan.md (same day).** Its headline finding was
real: the plan's fail-safe claim ("--wait can never print a wrong answer") is CONDITIONAL
on Claude Code's prompt-queue ordering — under the adversarial ordering, the later did's
`--wait` would succeed with the wrong turn's answer. R1 therefore made LV-1/LV-3
**pre-implementation gates** and added five verified hardening items (see §2a).

**Input for this session (read in this order):**

1. `.dev/proposals/response-return-path/final-plan.md` — THE approved plan **including
   §0 Revision R1**. Binding.
2. `.dev/proposals/response-return-path/reconciliation-notes.md` — why each decision
   landed where it did (useful when a diff-level question re-opens a settled debate:
   don't re-litigate, the notes have the reasoning).
3. `.dev/proposals/response-return-path/review-codex.md` — the external review R1
   answers; every finding was code-verified before adoption.
4. `.dev/proposals/response-return-path/problem-statement.md` — evidence + constraints.
   (proposal-A/B/C.md and review-for-*.md are the deliberation trail; consult only if
   the plan is ambiguous somewhere.)

## 2. Plan shape (orientation only — final-plan.md is authoritative)

Two independently-shippable layers over the existing dispatch_id correlation:

- **Layer 1 (transport, ships first):** Stop hook persists the full redacted answer to
  `payloads/response.<dispatch-id>.txt` (per-did files, never overwritten; 64 KiB cap
  `FORGE_CC_RESPONSE_MAX`, redact() before write, 0600, fail-open with session-keyed
  fallback); prompt hook `re.search`→`re.findall` + additive `dispatch_ids`; stop event
  gains additive `question_snippet` (tail-anchored) / `response_paths` / `truncated` /
  `full_bytes` and the head-anchored `snippet`; new `forge reply @session [<did>]
  [--json|--snippet]`; `forge dispatch --wait [--timeout N]` (opt-in, exit 124 on
  timeout with a paste-ready `forge reply` hint).
- **Layer 2 (fire-and-forget):** forge-watch NEEDS-REPLY hot row (additive, read-only;
  gate = sender=='seat' + looks_like_question + recency + structural not-superseded;
  NEEDS-ASK takes precedence), default `policy='never'` (board row, no bell), ring only
  via `FORGE_WATCH_NOTIFY_REPLY=1`. Plus seat-skill "return path" section (both copies
  byte-identical) and the documented-but-default-off `FORGE_WATCH_NOTIFY_DONE` knob.

### 2a. Revision R1 items (external-review hardening, all code-verified — binding)

1. **LV-1/LV-3 are PRE-IMPLEMENTATION gates.** They need ZERO new code — the live hooks
   already write `prompt.<session>.json`/`stop.<session>.json`. Dispatch two
   instructions in quick succession to a live worker session and inspect the event
   files (this can double as the forge-1 stand-down dispatch, §6). If LV-1 confirms the
   standard ordering (one UPS per submitted prompt, at dequeue), direct did-keying and
   the file-existence `--wait` predicate ship as planned; if not, Step 3b switches to
   transcript-derived correlation BEFORE any diffs are written for it.
2. **Prompt hook filters captured ids against real `dispatch-<id>.json` records**
   (fake `[dispatch_id:...]` markers embedded in instruction text are dropped; scalar
   `dispatch_id` prefers the first VERIFIED id).
3. **Stop event carries `response_dispatch_ids`** (the answered ids, recorded) —
   `reply` headers, NEEDS-REPLY, and tests key off it.
4. **NEEDS-REPLY gates on the ANSWERED dispatch** via a new `dispatch_by_id` collection
   (same scan pass), NOT `latest_disp` (newest-by-time = wrong dispatch in exactly the
   mid-turn case); fallback to `latest_disp` for old events without the field.
5. **`forge reply` validates the did argument** (`re.fullmatch(r'[A-Za-z0-9._-]+', …)`,
   exit 2) BEFORE any path construction — mirrors the ask-id guard at `bin/forge:332`.
6. **`FORGE_WATCH_NOTIFY_REPLY`/`FORGE_WATCH_NOTIFY_DONE` added to forge-watch
   `ALLOWED_KEYS` (`:99-103`) + `cfg_bool`** — watch.env (what the launchd agent reads)
   rejects unknown keys.
7. Seat skill's second copy named explicitly: `~/.claude/skills/command-center/SKILL.md`.

Key invariant to preserve in every diff (R1-corrected wording): a did's response file
can only be created from a prompt file containing that did, and no OTHER dispatch's
`--wait` can consume it; the stronger "never prints a wrong answer" holds ONLY under
LV-1-verified ordering — which is why LV-1 runs first. Lifecycle `stop.<session>.json`
stays session-keyed; forge-watch derivation and its existing tests must not change.

Test plan: R1–R14 (tests/forge-cc/run.sh; R12 fake-marker filter, R13 reply id-guard,
R14 RESPONSE_MAX robustness), W1–W9 (tests/forge-watch/run.sh; W8 answered-id keying,
W9 watch.env allowlist), G-1 (GC regression). LV-2 remains a pre-adoption live check at
the coder/QA stage; the implementation doc must carry all LV steps forward explicitly.

## 3. PRECONDITION — commit the two pending changes FIRST (operator action)

The working tree on `main` (at `afa1e3b`) still holds the two verified-but-uncommitted
changes from the shakedown session, touching **the same files this implementation will
diff against** (`bin/forge`, `bin/forge-watch`, `skills/command-center/SKILL.md`, tests):

1. Registry shape-check hardening (spawn.sh 29→31)
2. Human-first `forge board` (forge-watch 88→98; JSON via `--json`)

Suggested commits (unchanged from the prior handoff):
`fix(forge): registry adopts only cc-registry/1 files` and
`feat(board): human-first forge board; JSON via --json`.

Do NOT start adversarial-implementation on a dirty base — the proposers would write
diffs against uncommitted context, and a later operator commit-review could shift the
anchor lines. If the tree is still dirty at session start, ask the operator to commit
(or explicitly bless the dirty base) before spawning implementers.

## 4. Constraints (carry-forward, unchanged)

All Phase A/B/C constraint lists still hold: hooks fail-open; forge-watch READ-ONLY;
`cc-*/1` additive-only; `--answers` owns ask/callback consumption (NEEDS-REPLY replies
are PLAIN dispatches, never `--answers`); attention GC 7d/-maxdepth-2 (zero GC changes —
the plan proves coverage); bell discipline default-never (ring knobs default 0; flipping
them is the operator's decision — the operator signaled they'll likely enable
`FORGE_WATCH_NOTIFY_REPLY=1` after live shakedown, but ship default-off per plan);
bridge verbs untouched; redact() on every persisted payload; no pane scraping.

Baseline suites that must stay green: forge-cc 57, forge-watch 98, spawn 31,
forge-start 22, infra-lock 63. `install.sh --check-drift`: zero at handoff.
Bins are symlinks → any applied change is LIVE immediately (another reason the coder
step, not this session, applies diffs).

## 5. Process notes (lessons that cost time before)

- Heavy-multi-file-read background agents can die silent (3 deaths in the Phase C run).
  Use the proven ladder: write-early heartbeat expectation, bytes-on-disk check before
  assuming progress, one wake-up ping, then lead-does-it-inline fallback. All artifacts
  on disk = zero work lost.
- Teams tooling in this environment: no TeamCreate/TeamDelete — spawn named background
  agents (implicit team), SendMessage to resume by name, shutdown_request to terminate.
- Isolation discipline paid off in the proposal run (C overrode its own design on A's
  evidence; B caught a snippet-anchoring regression). Keep implementer A (surgical) and
  B (coverage) strictly isolated per the skill.

## 6. Loose ends (unchanged unless noted)

- forge-1 (headless_factory) may still be idle holding its counter-question ("map
  user_edits_present to a friendly message + overwrite button?"). The operator answered
  in spirit during plan review: **friendly message + overwrite button** is the desired
  product behavior — worth dispatching as the stand-down/answer when convenient; it is
  also a real headless_factory backlog item. Since LV-1/LV-3 now run BEFORE
  implementation (§2a item 1) and need only live dispatches, the stand-down dispatch is
  a natural first half of that check.
- Prior remaining items: two-worktree infra-lock pipeline-level run; rest of the
  real-use shakedown (ask path self-verify, new-project spawn).
