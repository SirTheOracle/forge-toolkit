# Handoff — `qa-retrospective` Skill → Adversarial Implementation (NEXT)

**Date:** 2026-07-14. **Author:** team-lead (Opus). **For:** a fresh session starting the
implementation stage. **Context can be cleared before reading this** — everything needed is below or
linked. **Do not re-litigate the design; it is settled and reviewed.**

---

## 1. What this is

A new Forge Framework skill, `qa-retrospective`, that mines the accumulated `.dev/qa/` QA record across
product repos to find QA-process failures, escapes, and — the headline capability — **recurring/
regressed defects that were fixed-and-verified and then popped back up**. It productizes the one-off
manual audit in `docs/qa-skill-audit-2026-07/`. Target repo: **forge-toolkit** (bash + markdown +
now stdlib-only Python).

## 2. Status: design COMPLETE, verified, revised. Ready for `adversarial-implementation`.

Full adversarial-proposal ran (2 proposers isolated → synthesizer → critiques → reconciliation), then
an independent **Codex review** (`review-codex.md`) was **verified finding-by-finding** and
incorporated. The plan is at **v2**. No open design questions remain except execution details that
belong to the implementer.

## 3. The one immediate next step

Run **`adversarial-implementation`** against the final plan:

- Plan (the input): `.dev/proposals/qa-retrospective-skill/final-plan.md`  ← **v2, this is the source of truth**
- Output dir (same slug): `.dev/proposals/qa-retrospective-skill/`
- Produces: `implementation.md` (exact diffs, file contents, test specs, coverage matrix) → then
  `forge-coder` → QA against forge-toolkit.

Nothing else needs to happen first. Do **not** re-open the proposal.

## 4. Artifacts (all under `.dev/proposals/qa-retrospective-skill/`)

| File | Role |
|---|---|
| `final-plan.md` | **v2 — the deliverable to implement.** Read this first. |
| `review-codex-disposition.md` | Per-finding verification evidence + accept/scoped dispositions (why v2 differs from v1). |
| `review-codex.md` | The original independent Codex review (all findings verified TRUE). |
| `problem-statement.md` | Original problem statement. |
| `proposal-A/B/C.md`, `review-for-A/B.md`, `reconciliation-notes.md` | Deliberation trail (background only). |

Precedent to encode (the methodology): `docs/qa-skill-audit-2026-07/{synthesis.md,qa-mining-*.md}`.

## 5. Locked decisions — DO NOT re-open

1. **Full parity on both LLMs (operator-confirmed).** Ship `skills/qa-retrospective/**` (Claude) AND a
   full `codex-skills/qa-retrospective/**` mirror (+ `agents/openai.yaml`). Wire `install.sh`
   (`SKILL_NAMES` 22-29, uninstall loop, completion list 349-356) and `README.md`. Trees stay
   byte-identical except the Codex-only `openai.yaml`; drift check enforces it. This is a hard
   requirement.
2. **Stage-scoped observations, not one run verdict** (the core data-model correction). A RunRecord
   carries `verdict_observations[]` (stage/artifact/field/raw/normalized/timestamp) → derived
   `qa_verdict`/`fix_qa_verdict`/`verify_verdict`. Verified counterexamples that force this:
   `goparent-ai/.dev/qa/professional-ledger` (QA `environment_blocked`/`no_confirmed_product_defects`
   vs verify `ISSUES_REMAIN`) and `feedforge/.dev/qa/curation-pillar-management`
   (`blocked`→`PASS`→`CLEAR`).
3. **Deterministic code is stdlib-only.** Verified: forge-toolkit has zero `.py` files and no Python
   dependency manifest; Python 3 stdlib has no YAML parser. Use a constrained line-oriented parser for
   the known QA-artifact shapes + regex fallback that never promotes malformed YAML to trusted facts.
   No PyYAML, no pip installs. `extract.py`, `scheduled_stream.py`, `validate_citations.py`,
   `ledger_apply.py`, `import_t0.py` all stdlib-only and behave identically under both runtimes.
4. **Event-sourced ledger, transactionally applied by a tested utility** (`ledger_apply.py`) —
   `events[]` append-only truth, derived chain state; base-revision + dedup + lock + atomic temp/rename
   + rollback; **no standing-state mutation on an incomplete/failed audit**. The orchestrator invokes
   it; the synthesizer only emits proposed deltas.
5. **Two-granularity identity:** `category:` = coarse escape-vs-enhancement gate only; `symptom_class`
   = fine miner-assigned controlled vocabulary. **Two recurrence match paths:** same-surface and an
   explicit **cross-surface** path (stronger root-cause/template/fix-lineage evidence) — the
   cross-surface path is required for the T3 "raw JSON in PDF" chain (spans evaluation-prep +
   hearing-ready + mediation).
6. **Frozen chain id** `RECID-<project>-<NNNN>` minted once at first-seen; `surface[]` /
   `symptom_aliases[]` grow underneath; mutable `display` slug is separate.
7. **Finder ≠ promoter:** miner proposes candidate recurrences (never asserts); synthesizer disposes
   via adversarial disconfirmation with a written per-candidate rebuttal.
8. **Deferred to v2 (do NOT build now):** cron trigger, standalone `recurrence-adjudicator` agent,
   full per-execution nested-stream modelling (v1 = aggregated trend only), numeric confidence weights.

## 6. Hard constraints for the implementer

- **Read-only over product repos.** The skill's only writes are under its `--out`/staging dir + the two
  standing files (`recidivism-ledger.yaml`, `recommendations-ledger.yaml`, `baseline.yaml`) in
  `forge-toolkit/docs/qa-retrospective/`. Read-only is tested by before/after **tree-hash** (not
  `git status`), and `--out` must resolve outside every product root (symlinks resolved).
- **Forge global-edit protocol applies** to any edit of forge-bridge/orchestrator/installer plumbing:
  backup + toolkit-mirror lockstep + git-diff review, **no auto-commit**. (See user memory
  `forge-global-edit-protocol`.) `install.sh` and `README.md` edits fall under this.
- **Tests live at `tests/qa-retrospective/{run.sh,fixtures/}`** consistent with existing
  `tests/*/run.sh`. Fixtures for the verified edge cases are enumerated in final-plan.md §6 (T1,
  T-STAGE, T-SEV, T2, T3, T-XSURF, T4, T5, T-LEDGER, T-GH, T-SCHED, T-COMPARE, T-DURABLE, T-CITE,
  T-COLLIDE, T6, T7, T8, T-DEP, T-INSTALL, T-ORCH).
- **GitHub:** `gh issue list` default limit is 30 (verified) — always paginate/`--limit` for all
  issues; snapshot title/body/URL/hash; distinguish legitimate `[]` from auth/rate/repo/label error.
- **Nested scheduled stream is real:** `goparent-ai/.dev/qa/scheduled-service-browser-qa/runs/` has 45
  dated executions / 476 `issues.json` — ingest as a separate aggregated stream, never 1 record nor 476.

## 7. Suggested first actions for the implementer

1. Read `final-plan.md` (v2) end to end, then `review-codex-disposition.md` for the "why."
2. Invoke `adversarial-implementation` with `final-plan.md` as the plan and the same proposal slug dir
   as output.
3. When it asks for scope confirmation: implement **v1 as specified**, full Claude+Codex parity,
   stdlib-only, deferring the §5.8 v2 items.

**Do not commit anything** without the operator's go (standing rule). The implementation stage produces
`implementation.md`; coding happens later via `forge-coder` through the pipeline.
