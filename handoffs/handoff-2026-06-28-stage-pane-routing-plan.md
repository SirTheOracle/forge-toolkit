# Plan — Reasoning-Tier Routing for Forge Pipeline Stages

**Date:** 2026-06-28
**Author:** session (Opus 4.8)
**Status:** IMPLEMENTED 2026-06-28 (revised per Codex review). Awaiting user commit.
**Scope:** prompt/doc **+ a bridge-level enforcement guard** (scope upgraded
from prompt-only after review finding #2). No other `forge-bridge` logic
changed.

> Review incorporated: `handoff-2026-06-28-stage-pane-routing-plan-review.md`.
> §3 records how each finding was resolved.

---

## 1. Problem

The pane-1 orchestrator spends Opus tokens doing work that should be
dispatched, and when it does dispatch, high-thought stages don't reliably
land on a high-reasoning pane. The pipeline **stages** and the **skills**
per stage are correct — the defect is purely **stage → pane routing**.

Principle: every **high-thought** stage runs on a **high-reasoning pane**
(Opus pane 0 or Codex A pane 2); only **mechanical** stages go to the
**throughput tier** (Sonnet pane 4 or Codex B pane 3).

## 2. Routing — tiers (single vocabulary: HIGH / THROUGHPUT)

- **HIGH** → Opus pane 0, Codex A pane 2
- **THROUGHPUT** → Sonnet pane 4, Codex B pane 3

| Stage | Tier | Worker (default → fallback) | Was |
|---|---|---|---|
| proposal | HIGH | **local pane-1** (Agent Teams) — not dispatchable | unchanged |
| review | HIGH | codex-a (only) | unchanged |
| incorporate | HIGH | claude-opus | unchanged |
| implementation | HIGH | codex-a → **claude-opus** | was codex-a → ~~codex-b~~ |
| impl-review | HIGH | claude-opus | unchanged |
| coding | THROUGHPUT | claude-sonnet | unchanged |
| qa | THROUGHPUT (medium-reasoning, throughput-routed) | codex-b → claude-sonnet (local) | unchanged |
| qa-fix | THROUGHPUT | claude-sonnet | unchanged |
| qa-retry | THROUGHPUT | codex-b / claude-sonnet | unchanged |
| verify | HIGH | **codex-a** → **claude-opus** | was ~~sonnet / codex-b~~ |

`qa` is intentionally medium-reasoning but throughput-routed — there is **no
third tier**. The two real fixes: `implementation` fallback and `verify`.

## 3. Review findings — resolution

| Finding | Resolution |
|---|---|
| **H1** Hard Rule 22 self-contradicts on `proposal` | Rule 22 now defines `proposal` as a *named local exception* in its own bullet, NOT inside the "dispatched HIGH stages run only on pane 0/2" sentence. |
| **H2** Prose-only enforcement stays advisory | **Added a bridge tier guard** in `cmd_dispatch` (`bin/forge-bridge`). Illegal stage/worker pairs are rejected before the `--dry-run` return. User confirmed this scope upgrade. |
| **H3** Claude fallback needs `--clear` rules | Added `--clear` fallback examples for `implementation→claude-opus` and `verify→claude-opus`; updated Hard Rule 20 examples (pane-0 reuse: impl-review/impl-fallback/verify-fallback; pane-4: qa-fix after coding); fixed the techref "verify after coding" clear example. |
| **H4** docs-refresh target/source drift | `.claude/docs-refresh.yml` target → `docs/forge-technical-reference.md`; `forge.orchestrator` source → the installed SKILL (canonical body `/forge` reads). |
| **M5** Codex A "QA fallback" stale wording | Removed from techref pane table; added an explicit "Codex A is **not** a QA fallback" line in SKILL Worker Selection. |
| **M6** verify exclusion wording ambiguous | Rewritten: default codex-a, fallback claude-opus; the "≠ latest QA worker" rule is now a *guard* (auto-satisfied under current QA routing), not the primary selector. |
| **M7** tier taxonomy mixed MED/LOW/THROUGHPUT | Collapsed to HIGH / THROUGHPUT everywhere; qa annotated as medium-but-throughput. |
| **M8** acceptance checks too weak/noisy | Replaced bare `grep codex-b` with targeted negative+positive `rg` and bridge `--dry-run` positive/negative tests (§6). |
| **L9** prompt templates in scope? | **Checked — no change.** `~/.config/forge/prompts/{implementation,verify}.txt` use `{worker}` only for callback wiring; no routing is encoded there. |

## 4. Change set (as built)

1. **Hard Rule 22 (Reasoning-tier routing, bridge-enforced)** — added to
   SKILL body (all 3 copies). proposal = named local exception.
2. **bin/forge-bridge** — tier guard in `cmd_dispatch` (36 lines), placed
   after worker-canonical validation, before the `--dry-run` return:
   - HIGH stages {review, incorporate, implementation, impl-review, verify}
     → reject unless worker ∈ {claude-opus, codex-a}
   - THROUGHPUT stages {coding, qa, qa-fix, qa-retry} → reject unless
     worker ∈ {claude-sonnet, codex-b}
   - `proposal` → rejected as non-dispatchable (local-only)
   - unlisted stages (fix-pipeline, commit-review, ad-hoc) fall through
3. **implementation** fallback codex-b → **claude-opus** (+ `--clear`).
4. **verify** re-tiered HIGH: **codex-a** default, **claude-opus** fallback
   (+ `--clear`); exclusion downgraded to a guard.
5. **Your Role / Worker Selection** — tier map is now the first routing
   check; pane-0-vs-pane-1 hardened; Codex-A-not-a-QA-fallback noted.
6. **Hard Rule 20** — `--clear` examples updated for the new pane-0 reuse.
7. **docs/forge-technical-reference.md** — pane table, exec table,
   Hard-Rules table (new row 22), and the clear example updated.
8. **.claude/docs-refresh.yml** — target + source drift fixed.

## 5. Files touched & sync (this session's contribution)

| File | Lines | Notes |
|---|---|---|
| `bin/forge-bridge` | 36 | tier guard; mirrored to `~/bin/forge-bridge` (was identical) |
| `skills/forge-orchestrator/SKILL.md` | 156 | body edits + footer hash; mirrored to `~/.claude/skills/.../SKILL.md` (frontmatter preserved) |
| `~/.claude/agents/forge-orchestrator.md` | — | untracked; body re-spliced from edited SKILL, own preamble preserved |
| `docs/forge-technical-reference.md` | 35 | tables + clear example |
| `.claude/docs-refresh.yml` | 15 | target/source drift |
| `docs/forge-operator-guide.md` | 0 | checked — no routing claims, untouched |

`bin/forge-start` and the pre-existing `M` on the docs were **not** this
session's work (already modified at session start).

## 6. Acceptance — verified

- **Bridge dry-run, negatives rejected:** `implementation→codex-b`,
  `verify→codex-b`, `verify→claude-sonnet`, `review→claude-sonnet`,
  `coding→codex-a`, `proposal→<any>` — all fail with the Hard Rule 22
  error (toolkit **and** installed bridge). ✓
- **Bridge dry-run, positives allowed:** `implementation→{codex-a,
  claude-opus}`, `verify→{codex-a, claude-opus}`, `coding→claude-sonnet`,
  `qa→codex-b` — all pass the guard. ✓
- **Negative rg** over SKILL + techref + operator-guide + installed copies:
  only explanatory/negating references remain (push-back rows, "bridge
  rejects…", "not a QA fallback", changelog) — no illegal route
  instruction. ✓
- **Positive rg:** Hard Rule 22 present in all 3 bodies; exec tables show
  implementation→claude-opus fallback and verify→codex-a/claude-opus. ✓
- **docs-refresh:** target is `docs/forge-technical-reference.md`; no stray
  `docs/technical-reference.md` created. ✓
- **Mirror integrity:** installed SKILL body == toolkit body; agent body
  (post-marker) == toolkit body; installed bridge == toolkit bridge. ✓
- **Syntax:** `bash -n` clean on toolkit and installed bridge. ✓

## 7. Edit protocol followed

Backups of all 8 targets saved to scratchpad before editing; toolkit
edited first then mirrored; SKILL footer hash regenerated (body-above-footer
sha256, method documented in the footer). **Not committed** — left for user
review.

## 8. Risks & follow-ups

- **Codex A bottleneck:** review + implementation + verify all default to
  Codex A. Mitigated by claude-opus fallback on implementation/verify; watch
  pane-2 contention on the first live run.
- **Phase 2 (deferred):** move `proposal` off pane 1 to the Opus worker
  (pane 0) to fully thin the coordinator — needs a `proposal` stage template
  + worker-side Agent-Teams-then-callback flow. Separate handoff.
- **Hygiene gap (unchanged):** `~/.claude/agents/forge-orchestrator.md` and
  `~/.claude/commands/forge*.md` remain untracked by the toolkit. The agent
  body was synced by hand this session; a tracking fix is out of scope.

## 9. Rollback

Backups in
`…/scratchpad/forge-routing-backups-20260628-211218/` (toolkit-* and
installed-*). Restore the relevant pair, or `git checkout` the toolkit
files and re-mirror.
