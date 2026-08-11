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

### 3. Write the plan DIRECTORY + get approval  (INITIATOR ONLY)

**Location — the PRIMARY root, never your worktree.** Your worktree is itself
disposable and gets pruned; the plan must outlive it and resolve identically from
every sibling session:

    PRIMARY_ROOT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
    PLAN_DIR="$PRIMARY_ROOT/.dev/proposals/qa-github-issues/fix-plans/<SERVICE>-<YYYY-MM-DD>"

That is the same anchor the infra lock resolves, and it is byte-identical from a
linked worktree and from the primary.

    <PLAN_DIR>/
        plan.md            # deal table + one YAML record per bucket   (human audit; SEALED)
        deal.json          # CANONICAL mechanical source               (SEALED)
        packets/<gid>.md   # one self-contained task packet per bucket (SEALED)
        manifest.json      # sha256 of every sealed file, written at approval
        claims/            # MUTABLE — created on demand by `queue.py claim`
        journal/<queue>.md # MUTABLE — append-only, exactly ONE writer per file

**Immutability rule (invariant 4, applied literally).** After approval, `plan.md`,
`deal.json` and `packets/` are **read-only to every session** and their hashes are
sealed in `manifest.json`. `deal.json` is **canonical** for anything a claim or an
execute-mode decision depends on; `plan.md` is the human artifact. `claim` and
execute mode both refuse on a seal mismatch. No session writes per-bucket status
anywhere. Progress is reconstructed from GitHub: `in-progress` ⇒ claimed,
unstarted-or-running; `fix-pr-open` + an open PR carrying the bucket's `Closes` set ⇒
done; issue closed ⇒ merged. This removes the three-concurrent-writers lost-update
shape entirely.

**Grouping rules — now load-bearing, not advice.** Two concurrently executing
branches must not touch one file, and a bucket can never see another bucket's
unmerged work:

- **Groups that would edit the same files MUST be merged into one bucket.**
- **A group that consumes a symbol another group creates MUST be merged into that
  group.**
- **`depends_on` must be empty.** A non-empty value is refused at `deal`,
  `deal --verify`, `claim` and `packet-check`. There is no execution path for it:
  every bucket branches from a freshly fetched `origin/<base_branch>` and no session
  waits for a merge, so a later bucket branches from a ref that does not contain the
  earlier one's work. Queue locality supplies **ordering**, not **ancestry** — keeping
  a chain in one queue would only make the failure sequential instead of parallel.
  Re-introducing chains needs an explicit merge gate or stacked ancestry; both are out
  of scope. Do not smuggle one in as queue order.

**Packet contract** — `packets/<gid>.md` **is** the `problem-statement.md`. Step 4 no
longer writes one; it copies this in. YAML front matter first (mechanically validated
by `packet-check`), verbatim issue bodies after:

    ---
    group_id · service · tier · slug · branch_name · queue_id · plan_dir
    base_branch · base_sha
    covered_issue_numbers · covered_coded_ids · current_labels
    root_cause_hypothesis (NON-AUTHORITATIVE) · confidence · anchors
    required_tests · infra_required · close_keywords · drop_conditions
    verification_targets:   # ONE ROW PER COVERED ISSUE
      - issue · coded_id · symptom · check · evidence
    ---
    VERBATIM `gh issue view` body for every covered issue.

`packet-check` gates the invariant: **every `covered_issue_number` has a verification
row, and no row names an issue the bucket does not cover** — an issue with no row
could never earn its `Closes`.

One record per bucket in `deal.json` (mirrored into `plan.md` prose):

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
approval_status:         # pending | approved | dropped
base_branch:             # main   <- BARE. `origin/` is added at USE, never stored.
base_sha:                # 40-hex sha of origin/<base_branch> at grouping time
anchors:                 # symbol anchors (function/class names), never line numbers
depends_on:              # [] — MUST be empty (see the grouping rules above)
infra_required:          # derived (S6); absent => treat as true
weight:                  # derived: TIER_BASE[tier] + len(covered_issue_numbers)
queue:                   # S1|S2|S3 — operator-editable BEFORE approval only
packet:                  # packets/<group_id>.md
```

> **`base_branch` is stored bare.** Store `main`, not `origin/main`, and build
> `origin/$base_branch` at the point of use. Storing the qualified ref produced
> `origin/origin/main` at every branch cut and every moved-ground check.

**Then deal and check:**

```bash
python3 scripts/queue.py deal --plan "$PLAN_DIR/deal.json" --sessions 3 --write
python3 scripts/queue.py deal --verify --plan "$PLAN_DIR/deal.json"
python3 scripts/queue.py packet-check --plan-dir "$PLAN_DIR"
```

Present the deal table with **per-queue weight totals alongside bucket counts**, so a
single-bucket S3 reads as a heavy bucket and not as a bug. (There are no "units":
chains are refused, not collapsed.) The derived `infra_required` value is shown per
bucket. The user may flip tiers, split/merge groups, drop groups, or move a bucket
between queues — then re-run `deal --verify`. Before approving a 3-way deal, check
headroom with `~/bin/forge-bridge usage --refresh`; deal to 2 if it is thin.

**The approval transaction — ordered, and every step is idempotent.** Owner-aware
status and `ROOT_CONFLICT` both assume every queue has a claim, so the initiator's own
transition cannot be left implicit. Do these in exactly this order; a crash at any
boundary is recoverable by re-running:

1. Set `approval_status: approved` on the blessed buckets; re-run `deal --verify` and
   `packet-check`. **Execute ONLY approved buckets.**
2. `python3 scripts/queue.py seal --plan-dir "$PLAN_DIR"` — the artifacts freeze.
3. Apply `in-progress` to every covered issue of every approved bucket
   (check-before-write, so re-running adds nothing twice). On a **partial failure do
   not roll back**: record which issues were labelled in `journal/S1.md`, report the
   failures, and **stop** — do not claim, do not print the tab block.
   `reconcile --dry-run` is the recovery path; the labels are not lost work.
4. `python3 scripts/queue.py claim --plan-dir "$PLAN_DIR" --queue S1 --root "$(git rev-parse --show-toplevel)"`
   — the initiator claims **before** any assisting tab can.
5. **Only now** print the tab block (step 3b).

`in-progress` at approval time is what makes a second tab's `tally` refuse. It also
creates an orphaned-label class if a session dies, which is why `reconcile` ships in
the same change and is the documented remedy.

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

   **`fix-code` is an infra stage (Hard Rule 23) — wrap the dispatch in Shape A.**
   Its capability class is `commit`, so the bridge REFUSES the dispatch outright
   (`INFRA_LOCK_REQUIRED`, exit 5) unless this session already holds the lock for
   this exact `(slug, stage)`. This is not optional:

   ```bash
   ~/bin/forge-bridge infra-lock acquire --slug <slug> --stage fix-code
   ~/bin/forge-bridge dispatch --slug <slug> --stage fix-code --worker <W>
   #   dispatch FAILS -> infra-lock release --slug <slug> --stage fix-code ; surface ; STOP
   # wait loop: DONE|ERROR -> release. HOLD through PROMPTING/STALLED/TIMEOUT/DEAD/BLOCKED.
   ~/bin/forge-bridge infra-lock release --slug <slug> --stage fix-code
   ```

   Under fan-out a `WAIT reason=held-by-live-session` here is **expected** — a
   sibling session is on the shared stack. Wait it out. A `TIMEOUT` is a conflict
   with work being done, so surface it (see **Surfacing**). Never
   `release --force` another session's lock.

   Full tier inherits the same protection through the orchestrator, which locks
   `fix-code` / `fix-qa` / `fix-qa-retry` per Hard Rule 23.

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

### 5. Per-issue verification (gates the PR) — UNDER THE INFRA LOCK

This step runs `required_tests` **inline in your own session**, with no dispatch
and no `wait` to key off — so it takes **Shape B**
(`forge-orchestrator/SKILL.md`, Hard Rule 23). It is not bookkeeping: this is
where the backend suites actually execute, and GoParent's
`backend/tests/conftest.py` runs a **session-scoped autouse** fixture that
`pg_terminate_backend()`s every other connection to the shared
`goparent_platform_test` database. Two sessions verifying at once corrupt each
other's failure counts **silently**, and a corrupted failure count is how a
`Closes #N` reaches an unfixed bug.

```bash
~/bin/forge-bridge infra-lock acquire --slug <slug> --stage fix-verify
# run required_tests here; capture evidence paths; build the verification table
~/bin/forge-bridge infra-lock release --slug <slug> --stage fix-verify
```

**Release on the success path AND on the failure path.** A step 5 that errors
before the release leaks the lock and blocks every sibling session until a
dead-session steal or an operator force-release. Do **not** wrap the release in a
blanket `trap EXIT` — that would release while a test process may still be live.

`fix-verify` is a **lock label, not a dispatchable stage**: `infra-lock acquire`
validates neither `--slug` nor `--stage`, so it works with no bridge change, and
`fix-verify` has no `stage_capabilities` entry, so nothing will ever dispatch it.

Build the verification report — one row per covered issue:
`issue # | coded ID | symptom | check (command/manual) | result | evidence`.
A cluster that resolves 6 of 7 covered issues closes only the 6; the 7th is dropped
(see Cluster abort).

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
