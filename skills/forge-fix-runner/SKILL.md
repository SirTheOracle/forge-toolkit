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

> **Project specifics** — repo name, classifier vocabulary, extra blocking labels,
> the seed set, the readiness probes (including the two verbatim probe commands
> execute mode needs), and any project-local caveats — live in `PROJECT.md` beside
> this file. **Read it first.** Everything in *this* document is identical in every
> project.

**Repo:** set by `DEFAULT_REPO` in the PER-PROJECT CONFIG block of
`scripts/queue.py` (each project's `scripts/` is a real directory, never a symlink).
**Design of record:**
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
6. **One worktree per session.** A session executes in its own git worktree and
   claims at most one queue. Two sessions in one physical root corrupt each other's
   forge context and pane topology; `queue.py claim` refuses it mechanically with
   `ROOT_CONFLICT`, comparing `realpath(git rev-parse --show-toplevel)` — never
   `$PWD`, or two sessions in different subdirectories of one worktree both pass.
7. **A session never merges, never rebases or resolves another session's branch or
   conflict, and never force-releases the infra lock.** Integration is the operator's,
   through `main`, on their cadence.

> **What fan-out buys, stated once and un-skimmably.** With the infra mutex correctly
> extended, `fix-code`, `fix-qa`, `fix-qa-retry` and step-5 verification **serialize
> globally across worktrees** — they contend for one shared test database. Expect
> wall-clock ≈ *(sum of every bucket's infra time)* + *(the heaviest queue's non-infra
> time)*: roughly **1.5–1.9×** on a full-tier-heavy queue and close to **1×** on an
> all-quick queue. **Those numbers are an estimate pending timing data, never a
> contract.** "The wall-clock of the heaviest queue" would need a per-worktree test
> database and per-worktree ports, which are deliberately out of scope. **The
> delivered value is distribution automation** — nobody hand-deals `g1 … g7` every
> morning — not throughput. `infra_required` is therefore `True`-only in practice on
> GoParent today.

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

Written for **concurrency**: under fan-out a sibling pipeline is *normal*, and the
dangerous failure is a tab that "helpfully" supersedes a live one. The destructive
branches below were written for one-pipeline-at-a-time and must not fire.

- `~/bin/forge-bridge identity` — require `identity_state=MATCH` **from a real
  in-pane probe**. A headless tab in a root with exactly one live session *adopts that
  session's identity* and would then read and write its context. If you are not in a
  pane of your own session: **stop**.
- `~/bin/forge-bridge context` — this is **per-session**; everything it shows is your
  own tab's. **Never offer to supersede. Never act on a `legacy shared context`
  hint.** A sibling's active slug is not yours to see or to clear.
- `~/bin/forge-bridge infra-lock status` — `HELD live by <other slug>` is the
  **expected** state under fan-out. Report it and continue. **Never
  `release --force`** (invariant 7).
- `git status` clean, and **not** a detached HEAD. Do **not** require the branch to be
  `main`: `forge worktree ensure` cuts `forge/<session>`, so a correctly provisioned
  fan-out worktree is never on `main` and that check would refuse every assisting tab.
  What must hold is a clean, non-detached worktree and a fetched remote base that
  resolves: `git fetch origin && git rev-parse --verify origin/<base_branch>`.
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

### 3b. Open the assisting tabs — BEFORE you start bucket 1

`forge worktree ensure` refuses to cut a worktree when the source root has **tracked
modifications** and a session is live there. `e24e1be` stopped the worktree
*container* dirtying `git status`, but the refusal keys off
`git status --porcelain -uno` — tracked changes — which is exactly what you create the
moment bucket 1 starts. So tabs 2 and 3 must be opened first. Print exactly this:

```
Open BOTH tabs now, before I start bucket 1:
  /forge-fix-runner execute <ABS-PLAN-DIR> S2
  /forge-fix-runner execute <ABS-PLAN-DIR> S3
```

If a tab is opened late and hits `source has tracked modifications`, the remedy is
`forge-start --allow-dirty-base <session>`, and for fan-out that is **correct, not
merely tolerated**: the worktree is cut from the resolved base, and an executor
branches every bucket from a fresh `origin/<base_branch>` anyway, so it must not
inherit your uncommitted work. The tool's warning exists for the ordinary case, which
this is not.

### 4. Execute mode — how an assisting tab enters

Entered **only** as `/forge-fix-runner execute <abs-plan-dir> <queue-id>`. Both
arguments are required; if either is missing, **stop and ask** — never fall through to
step 1.

> **You do not triage. Steps 1, 2 and 3 do not exist for you.**

1. Preflight (step 0). `queue.py verify-seal --plan-dir <abs>`. Load `deal.json` (the
   canonical source — not `plan.md`); refuse if the queue id is absent, if the seal
   does not verify, or if any bucket in the queue is not `approval_status: approved`.
2. **Claim first, then lock, then check readiness.** This order is not negotiable:

   ```bash
   python3 scripts/queue.py claim --plan-dir <abs> --queue <id> --root "$(git rev-parse --show-toplevel)"
   ~/bin/forge-bridge infra-lock acquire --slug <first-slug> --stage fix-verify
   python3 scripts/queue.py readiness --plan-dir <abs> --queue <id> --root "$(git rev-parse --show-toplevel)" \
       --db-probe   "<see PROJECT.md>" \
       --import-probe "<see PROJECT.md>"
   ~/bin/forge-bridge infra-lock release --slug <first-slug> --stage fix-verify
   # readiness FAILED -> release the lock, then
   #   queue.py release --plan-dir <abs> --queue <id> --owner-token <t>
   #   print the remediation and STOP. Never execute a queue you cannot verify.
   ```

   The readiness check is not a formality. On GoParent it exercises `pytest`, whose
   `conftest.py` runs a session-scoped autouse fixture that terminates every other
   connection to the shared test database. Running it **before** claiming and
   **outside** the lock would perform the exact cross-session collision this design
   exists to prevent — and two tabs in one root can both reach it before
   `ROOT_CONFLICT` refuses either. It covers **every toolchain the queue uses**, not
   one command from the first bucket.

   **Both probe commands are project-specific and live in `PROJECT.md`. Use them
   verbatim.** They exist because of two measured hazards:
   - **`--db-probe`** — an unseeded worktree does *not* fail to start; it resolves the
     hardcoded `DATABASE_URL` default in the app config and would silently target a
     different database.
   - **`--import-probe`** — a symlinked `.venv` carries an editable-install `.pth`
     pointing at the **primary** checkout's source. The probe in `PROJECT.md`
     reproduces what pytest does (`sys.path.insert(0,'src')`); a **bare**
     `import app` resolves to the primary even on a correctly seeded worktree and
     would fail the healthy case.
3. Work the buckets in listed order (4.5).
4. Drained → `queue.py release`, confirm no open pending, confirm you hold no infra
   lock, print the summary from `journal/<queue>.md`, **stop**. No requesting more
   work, no idling.

**Recovery — a reopened tab given the same queue id.** `claim` steals the stale claim
of a dead session automatically. Then, before re-cutting anything:

- Re-derive per-bucket status from `gh issue list` + `gh pr list`; skip buckets that
  are done (`fix-pr-open` + an open PR carrying their `Closes` set).
- For the first not-done bucket, **check for an existing `fix/<slug>` branch first** —
  a bucket a dead session had started looks identical to an unstarted one, and
  `git checkout -b` fails outright if the branch already exists. In order:
  - the recorded root still exists and the branch is checked out there → **reattach**
    to that worktree and continue;
  - else `forge recover --dry-run` reports bridge residue → run `forge recover` first;
  - else `git worktree prune`, then re-cut.
  **Never silently discard committed or uncommitted work.** If the branch carries
  commits you did not make, or uncommitted work you cannot attribute, stop and surface
  it.

### 4.5 Per-bucket protocol (identical for the initiator)

1. `git fetch origin`. If `origin/<base_branch>` has moved from `base_sha`, run the
   **anchor re-check**: every recorded anchor must still resolve (`git grep -n` on the
   symbol, never the line). Any anchor missing ⇒ **INVALIDATED**: comment on the
   covered issues, remove `in-progress`, leave them **open and actionable**, append the
   reason to `journal/<queue>.md`, and **continue to the next bucket**. Report
   invalidations at the end; do not surface mid-run.

   > This is **conservative invalidation**, deliberately — not "re-scope and carry
   > on". Its cost is one re-scout; the cost of the alternative is a wrong `Closes`,
   > so the check is tuned to over-invalidate. Be honest about its limit: **a symbol
   > can survive with changed semantics and pass this check.** That is a known
   > false-negative, not a claim of safety.
   >
   > **Full-tier late-overlap checkpoint.** A full-tier investigation can discover
   > file overlap with a bucket a sibling has *already started*. When it does, stop,
   > journal it, and surface it — that is a conflict with work being done.

2. `gh issue view` each covered issue; drop from `close_keywords` any that are now
   closed or already carry `fix-pr-open`. All gone ⇒ skip the bucket (journal it,
   report it).
3. `git checkout -b fix/<slug> "origin/$base_branch"` — **always from freshly fetched
   `origin/<base_branch>`, never from the previous bucket's branch.** This is how "no
   stacked PRs" becomes mechanical rather than aspirational. Note the single
   `origin/`: `base_branch` is stored bare precisely so this is not
   `origin/origin/main`.
4. Copy `packets/<gid>.md` to `.dev/proposals/<slug>/problem-statement.md` (it already
   *is* the problem statement); write `fix-plan.md` for the quick tier.
5. Execute by tier (below). Both tiers run their infra stages under the lock; the
   bridge refuses them otherwise (`INFRA_LOCK_REQUIRED`, exit 5).
6. **Step-5 verification under the infra lock (Shape B)** — see step 5.
7. Open the PR, then move to the next bucket **immediately**. Never wait for the
   operator's merge.

> **Surface to the operator ONLY on a conflict with work being done** — a
> rebase/merge conflict against `origin/<base_branch>`, a bucket whose files an open
> `fix/*` PR already changed, an infra-lock `TIMEOUT` escalation, or the full-tier
> overlap checkpoint above. Use `forge ask --session-scope`. Otherwise work silently
> to the end of your queue.

For each bucket, by tier:

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
the shared test database. Two sessions verifying at once corrupt each
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
4. **in execute mode the plan directory is sealed**, so instead of editing the
   artifact, append the drop and its reason to `journal/<queue>.md` — one writer per
   file, never read by another session, purely for the end-of-run report. (During
   triage, before approval, editing the artifact is still correct.) The same applies to
   recording an invalidation reason. Steps 1–3 and 5 are GitHub mutations and are
   unchanged;
5. leave the issue open and actionable for a future group.
Never let a silent `Closes` close an unfixed bug.

## Idempotency
- Refuse to dispatch a group whose covered issues already carry `in-progress` or
  `fix-pr-open` (the predicate already enforces most of this).
- **`in-progress` is applied at APPROVAL time, not at dispatch.** That is what makes a
  second tab's `tally` refuse instead of producing a rival grouping. It also means a
  crashed session leaves permanently non-actionable issues — so `queue.py reconcile`
  is the paired remedy and **ships in the same change**. Reconcile is **owner-based**:
  a live claim, a live tmux incarnation, an open referencing PR, or a surviving
  `fix/*` branch each protect the label. **Elapsed time never does** — a full-tier
  bucket can legitimately run for a day. A foreign-host claim is reported, never
  auto-removed: its liveness is unverifiable from here.
  Write the plain label `in-progress` in **every** project; FeedMint's
  `status:in-progress` is an inbound mirror owned by the QA sync and is read-only to
  the runner.
- Run state lives in the sealed artifact (`approval_status`, drops) plus GitHub.
  Reruns read both; nothing else is authoritative.
- All runner comments are tagged `<!-- forge-runner:<group_id> -->`; check-before-write.

## Scope guards
- Local fixing only — no CI (`claude-code-action`).
- Two tiers only (no `lite`). No stacked PRs. No cross-service grouping (flag + stop).
- **No parallel TRIAGE.** Exactly one session tallies, scouts, groups and deals. A
  second grouping of one queue is the failure this guard exists to prevent.
- **Parallel EXECUTION is permitted**: at most 3 sessions (initiator + 2), one claimed
  queue each, one bucket at a time per session, **each session in its own git
  worktree**. Never a second plan for one service concurrently; never two sessions on
  one queue id; never a bucket split across sessions.
- Never modify files outside `.dev/proposals/<slug>/` and the fix's own source
  changes — with **one narrow, stated exception**: a session may create
  `<plan-dir>/claims/<queue>.json` and append to `<plan-dir>/journal/<queue>.md` under
  the primary root. It may write **nothing else** there, never another queue's
  journal, and never any sealed file. An unstated exception is exactly the thing that
  drifts.
- You add status labels (`in-progress`/`fix-pr-open`) and runner comments; you never
  add `forge-fix` and never write to the Google Sheets.

## Maintainer note — deployment topology

*(Operators can skip this section; it is for whoever edits the toolkit.)*

`skills/forge-fix-runner/SKILL.md` in `forge-toolkit` is the **only** copy of this
document. Each project's `<project>/.claude/skills/forge-fix-runner/SKILL.md` is a
**symlink** to it. `scripts/` stays a **physical directory** in every project — the
PER-PROJECT CONFIG block is the whole point, and a symlinked `scripts/` would erase
it. That is exactly the verified topology of `~/.claude/skills/forge-fix-runner/`
(SKILL.md symlink, real `scripts/` dir).

**Convert one project first.** Symlink resolution is proven for `~/.claude/skills/`;
a project-level `.claude/skills/` is a *different loader path*. Convert
one project, invoke the skill there, confirm it loads. Only then convert the other
two. **Fallback if it does not resolve:** keep physical copies and rely on
`test_matches_template_outside_config_block()` to catch drift either way.

**The worktree inversion, stated so nobody debugs it twice:** a git worktree gets a
*physical snapshot* of `.claude/` at creation time, and `shutil.copytree(…,
symlinks=True)` **preserves the symlink** — so after conversion a worktree's copy is a
symlink pointing at the **primary** checkout. A toolkit edit you are testing inside a
forge-toolkit worktree is therefore *not* what loads. Edit and test from the primary
checkout, or accept that the worktree copy is inert.

Project-specific prose lives in `PROJECT.md` next to each project's copy:

| Project | Repo | `CLASSIFIER_PREFIX` | Extra blocking labels |
|---|---|---|---|
| `<project-a>` | `<org>/<repo-a>` | `service:` | — |
| `<project-b>` | `<org>/<repo-b>` | `area:` | `status:in-progress` |
| `<project-c>` | `<org>/<repo-c>` | `service:` | — |

Procedure/invariant changes belong to **all** copies — when you change one, propagate
to the rest and re-run each `test_queue.py`. Note that git worktrees get a physical
snapshot of `.claude/skills/` at creation time; those snapshots are disposable, and
refreshing the parent checkout is what fixes future ones.
