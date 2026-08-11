---
name: forge-fix-runner
description: >
  Drain the GitHub `forge-fix` issue queue through the forge fix pipeline, one
  service at a time. Tallies actionable issues worst-first, scouts a service into
  a routing plan (clusters + quick/full tiers), gets human approval, then dispatches
  fixes (quick = single-pane fix-code; full = forge-pipeline), gating every
  `Closes #N` on per-issue verification. Trigger when the user wants to fix
  triaged QA issues, "work the forge-fix queue", or "run the fix runner".
---

# Forge Fix Runner

You turn GitHub Issues labeled `forge-fix` into merged fixes by routing them into
the existing forge pipeline. You are the **management layer**; forge is the engine.
You do NOT reinvent fixing — you select work, scout it, gate it, and keep issue
status in sync.

> **This is the toolkit TEMPLATE copy.** It is not runnable as-is: its
> `DEFAULT_REPO` is unset on purpose. Every project keeps its own copy under
> `.claude/skills/forge-fix-runner/`, which is what actually loads when you invoke
> the skill in that project. See **Project deployment** at the bottom.

**Repo:** set by `DEFAULT_REPO` in the PER-PROJECT CONFIG block of
`scripts/queue.py`. **Design of record:**
`.dev/proposals/qa-github-issues/forge-fix-pipeline-design.md` (read it if anything
here is ambiguous). **Queue logic:** `scripts/queue.py` (deterministic, tested) —
run it with no `--repo` flag; a project copy already points at its own repo.

**Classifier:** issues bucket by a configurable label prefix — `service:*` by
default, but a project may use another (FeedMint uses `area:*`). This document
says "service"; read that as whatever `CLASSIFIER_PREFIX` your project copy sets.

## Non-negotiable invariants

1. **Closed = actually fixed.** A `Closes #N` is emitted ONLY for an issue with a
   passing per-issue verification row. No green check → no close keyword. Ever.
2. **Dry-run is the default.** First pass prints every intended mutation (labels,
   branches, PR bodies, comments, forge dispatches) and changes nothing. Write mode
   requires the user to explicitly say "go / write / execute".
3. **`forge-fix` is human-only.** You never add it. You only act on issues that
   already carry it.
4. **Status lives only in GitHub.** Labels + open/closed + the PR's `Closes` are the
   single source of truth. Never hand-maintain status elsewhere.
5. **One branch per group, one PR per group.** Never a shared service branch.

## The actionable predicate (what you may pick up)

Use `scripts/queue.py` — do not eyeball labels. An issue is actionable iff:
open + the trigger label + exactly one classifier label + none of the project's
`BLOCKING_LABELS` (by default `needs-retest` / `in-progress` / `fix-pr-open`).
(So an issue carrying `forge-fix` + `needs-retest` is correctly skipped — it is
already awaiting retest.) All four inputs come from the PER-PROJECT CONFIG block.

```
python3 scripts/queue.py tally                    # worst-first services, proposes next
python3 scripts/queue.py select --service <NAME>  # the actionable queue for one service
```

## Procedure

### 0. Preflight (abort on any failure)
- `~/bin/forge-bridge context` — no stale active slug for a foreign pipeline, no
  pending callbacks belonging to another pipeline. If a stale pipeline is active,
  stop and ask the user whether to supersede it.
- `git status` clean (or expected); on the intended base branch (default `main`).
- `gh auth status` shows issue + PR scope.
- The slug you plan to use does not collide with an existing `.dev/proposals/<slug>/`.

### 1. Pick the service
Run `queue.py tally`. Propose the worst-first service as a one-liner the user can
redirect ("Run <SERVICE> next? N criticals"). Counts are live — never hard-code them.

### 2. Scout (read-only — change no code)
The scout is a fixed contract (prompt `~/.config/forge/prompts/fix-scout.txt` + the group
schema in step 3), so it is **agent-agnostic** — run it either way:
- **Inline:** spawn a read-only Explore subagent over the service's actionable queue.
- **Dispatched (e.g. to Codex):** pick a scout slug `<svc>-scout-<date>`; write the
  actionable issues (numbers, coded IDs, full bodies from `gh issue view`) to
  `.dev/proposals/<scout-slug>/scout-input.md`; then
  `~/bin/forge-bridge dispatch --slug <scout-slug> --stage fix-scout --worker codex-a`.
  The worker writes `.dev/proposals/<scout-slug>/routing-plan.md`.

Either way the scout reads each issue body + the implicated code and produces **groups**,
each a cluster (several issues, ONE shared root cause) or a singleton. A cluster means
*one fix resolves all covered issues* — shared root cause, NOT shared service or shared
symptom wording. Derive groups from evidence; never assume a service is one cluster.
(Live caution from GoParent: one service's queue mixed "section empty" and "code
showing" symptoms that turned out to have entirely different causes.)

**Multi-cause issues (many-to-many):** an issue can have several causes, and a cause can
span issues. A group's `Closes` may list an issue only if the group fixes ALL its causes;
do not carve a quick sub-fix out of a multi-cause issue that also has a full-tier cause
(fold it into the full group). Group by subsystem; use `drop_conditions` to move uncertain
issues between groups. (See the design doc's "Multi-cause issues" section.)

If you suspect a **cross-service** root cause, flag it and STOP for the user — do not
force it into a service-local group.

### 3. Write the routing plan + get approval
Write `.dev/proposals/qa-github-issues/fix-plans/<SERVICE>-<YYYY-MM-DD>.md`. One
record per group (YAML block + a short prose rationale):

```yaml
group_id:                # <service>-<YYYY-MM-DD>-g1
service:                 # <service>   (the classifier value)
tier:                    # quick | full
covered_issue_numbers:   # [68, 69]
covered_coded_ids:       # [<SVC>-012, <SVC>-013]
current_labels:          # per-issue label snapshot (for idempotency/abort)
root_cause_hypothesis:   # short prose (NON-AUTHORITATIVE for full pipeline)
confidence:              # high|medium|low + one-line why
slug:                    # <service>-<short-cause-description>
branch_name:             # fix/<service>-<short-cause-description>
close_keywords:          # "Closes #68 #69"  (emitted to PR ONLY after verification)
required_tests:          # commands the fix must pass
drop_conditions:         # when to un-cover an issue from this group
approval_status:         # pending
```
Present the plan. The user may flip tiers, split/merge groups, or drop groups. Set
`approval_status: approved` on what they bless. Execute ONLY approved groups.

### 4. Execute each approved group (severity order; quick-criticals first)
For each group, set `in-progress` on every covered issue, then by tier:

**quick** — bounded change, inside forge via a single pane:
1. Write `.dev/proposals/<slug>/problem-statement.md` (verbatim covered-issue bodies +
   the scout hypothesis labeled *non-authoritative* + `required_tests` + the per-issue
   verification targets).
2. Write a **minimal** `.dev/proposals/<slug>/fix-plan.md` (the scout's bounded plan —
   this is the frozen plan the mechanical coder consumes; "quick" still has a plan).
3. Ask `forge codex-lane --root "$PROJECT_ROOT" --stage fix-code --worker codex-a`
   for the protected lane decision. In the default `contain` rollout, dispatch
   the coder stage to `claude-opus` (the reviewed-host lane). Use `codex-a` only
   when the result explicitly says `lane=codex`; never bypass `LANE_REQUIRED`.
   The selected worker writes `fix-coder-report.md` + test evidence on branch
   `fix/<slug>` and requests its exact-path commit through the broker.
4. **Escalation backstop:** if the worker reports it needs more than the planned files,
   can't form the minimal plan, or the cause is uncertain, it stops without fixing —
   remove `in-progress`, re-route the group to **full**, note why in the artifact and
   an issue comment.

**full** — murky/risky/flow-breaking, the whole pipeline:
1. Write `.dev/proposals/<slug>/problem-statement.md` (as above; scout hypothesis is
   routing metadata only — A/B investigators must validate independently, C reconciles).
2. Run the forge pipeline for the slug (`forge-pipeline <slug>` in a forge session via
   the orchestrator). It produces diagnosis → plan → code → qa on branch `fix/<slug>`.

### 5. Per-issue verification (gates the PR)
Before opening the PR, build a verification report — one row per covered issue:
`issue # | coded ID | symptom | check (command/manual) | result | evidence`.
A cluster that resolves 6 of 7 covered issues closes only the 6; the 7th is dropped
(see Cluster abort). Run `required_tests`; capture evidence paths.

### 6. Open the PR (you/runner own this — not the worker)
One PR per group, from `fix/<slug>` to the default branch. The body ends with
`Closes #…` listing **only** verified issues. On PR open: for each covered issue,
remove `in-progress`, add `fix-pr-open`, and comment the PR link (tag the comment
`<!-- forge-runner:<group_id> -->` so reruns update, not duplicate).

### 7. Report and stop
Summarize groups dispatched, PRs opened, issues dropped/escalated. **Merging is a
human action** — on merge GitHub auto-closes the issues; the project's daily QA
task (`<project>-qa-review` / `<project>-qa-issue-sync`) then reconciles status,
typically adding `needs-retest` and pinging the tester. Do not merge.

## Cluster abort (when a covered issue turns out different)
If a covered issue has a different cause (found during fix or failing verification),
and a branch/PR already exists:
1. remove that number from the PR's `Closes …` text (edit the PR body);
2. remove its `in-progress`/`fix-pr-open` label;
3. comment on the issue explaining the drop from `<group_id>`;
4. update the routing artifact (`covered_*`, note the drop);
5. leave the issue open and actionable for a future group.
Never let a silent `Closes` close an unfixed bug.

## Idempotency
- Refuse to dispatch a group whose covered issues already carry `in-progress` or
  `fix-pr-open` (the predicate already enforces most of this).
- Run state lives in the routing artifact (`approval_status`, drops). Reruns read it.
- All runner comments are tagged `<!-- forge-runner:<group_id> -->`; check-before-write.

## Scope guards
- Local fixing only — no CI (`claude-code-action`). No parallel service pipelines.
- Two tiers only (no `lite`). No stacked PRs. No cross-service grouping (flag + stop).
- Never modify files outside `.dev/proposals/<slug>/` and the fix's own source changes.
- You add status labels (`in-progress`/`fix-pr-open`) and runner comments; you never
  add `forge-fix` and never write to the Google Sheets.

## Project deployment (how the copies relate)

This toolkit copy is the **template of record**. Each project holds a physical copy
at `<project>/.claude/skills/forge-fix-runner/` — that copy, not this one, is what
loads when the skill is invoked in that project. `.claude/skills/` is untracked, so
git will not reconcile them for you.

**Only the PER-PROJECT CONFIG block in `scripts/queue.py` may differ**, plus the
matching label names in `SKILL.md` prose and `test_queue.py` fixtures:

| Project | Repo | `CLASSIFIER_PREFIX` | Extra blocking labels |
|---|---|---|---|
| `goparent-ai` | `SirTheOracle/goparent-ai` | `service:` | — |
| `feedforge` | `SirTheOracle/feedmint` | `area:` | `status:in-progress` |
| `headless_factory` | `SirTheOracle/animation-factory` | `service:` | — |

Procedure/invariant changes belong to **all** copies — when you change one, propagate
to the rest and re-run each `test_queue.py`. Note that git worktrees get a physical
snapshot of `.claude/skills/` at creation time; those snapshots are disposable, and
refreshing the parent checkout is what fixes future ones.
