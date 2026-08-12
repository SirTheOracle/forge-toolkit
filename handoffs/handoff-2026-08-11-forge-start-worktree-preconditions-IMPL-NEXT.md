# Handoff — `forge-start` worktree preconditions: make session start uniform

**Date:** 2026-08-11 · **Stage:** IMPL-NEXT · **Repo:** `forge-toolkit`
**Related:** `e24e1be` (worktrees moved inside the project), auto-worktree in
`forge-start` (2026-08-08), `.dev/proposals/fix-runner-session-fanout/`

---

## 1. The problem, stated the way the operator stated it

> "It makes no sense that you have to add `--here` to every project except goparent.
> `goparent-ai-goparentbugs` is not a project — that was a session which somehow
> created a directory. There's only one project: goparent. I do not expect goparent
> to be the exception when I work on more than one project."

Both points are correct and both are load-bearing:

1. **`--here` is a workaround, not a fix.** It disables the per-session worktree that
   `forge-start` exists to create. Using it everywhere silently reverts the framework
   to pre-2026-08-08 behaviour while the code believes worktrees are in use.
2. **A session worktree is not a project.** `goparent-ai-goparentbugs` is a *session
   artifact* left in the repo-sibling location used before `e24e1be`. It is currently
   indistinguishable from a project to anyone reading `ls`, and it holds a branch
   hostage (§2, B4).

**Today, exactly one of five session roots can cut a worktree.** `goparent-ai` works
only because it happens to satisfy four independent preconditions. It is the accident,
not the standard.

| Session root | Worktree cut? | Blocked by |
|---|---|---|
| `goparent-ai` | **yes** | — |
| `feedforge` | no | B1 |
| `promptlol` | no | B1 |
| `headless_factory` | no | B2 + B3 |
| `goparent-ai-goparentbugs` | no | B4 (and is itself the artifact of the problem) |

---

## 2. The four blockers, each verified on disk

### B1 — `.dev/` must be gitignored, and the check demands a *committed* fix
**Hits:** `feedforge`, `promptlol` · **Exit 4** · `bin/forge:701-705` and `:1078-1080`

```
forge: .dev/ is not gitignored in <worktree> — attention files would be committable.
       Fix: echo '.dev/' >> '<worktree>/.gitignore'  (untrack any tracked .dev paths)
```

The fix it prescribes cannot work as written in worktree mode: the worktree is cut from
`origin/<base>`, so an **uncommitted** `.gitignore` edit in the primary never reaches
it. The operator must commit a `.gitignore` change to every project purely to satisfy
a forge precondition.

**This is already solved elsewhere in the same file.** `_worktree_ensure_ignored()`
(`bin/forge:874-888`) makes `.forge-worktrees/` ignored by appending to
`.git/info/exclude` — repo-local, no commit, append-safe, and it verifies the result
with `check-ignore` before proceeding. The `.dev/` gate sits ~170 lines away and
`die`s instead.

### B2 — the post-seed guard tests an absolute snapshot, not a delta
**Hits:** `headless_factory` · **Exit 4** · `bin/forge:1256-1261`

```bash
tracked_after="$(git -C "$wt" status --porcelain -uno)"
if [ -n "$tracked_after" ]; then
    echo "forge worktree: tracked modifications appeared after seeding:" >&2
```

The message says *appeared after seeding*; the code says *any tracked modification
exists*. There is no before-snapshot, so anything dirty at checkout — for reasons
having nothing to do with seeding — aborts provisioning.

`headless_factory` trips this on 23 files:

```
.gitattributes:  assets/background_music/tracks/*.mp3 filter=lfs diff=lfs merge=lfs -text
```

The mp3s were committed **raw** and `filter=lfs` was added afterwards without
`git lfs migrate`. Git cleans the 2.6 MB working file into a 132-byte pointer and
compares it to a 2.6 MB raw blob in HEAD — a permanent mismatch. The primary looks
clean only because its index has cached stat info and skips the filter; a fresh
worktree has a fresh index, runs the filter, and sees all 23 as modified.
`git lfs checkout` does not help (verified). `git-lfs 3.7.1` is installed.

### B3 — dirty-base refusal on a file untouched since July
**Hits:** `headless_factory` · `bin/forge:1171-1173`

`CLAUDE.md` has been modified since **2026-07-20** (removing an obsolete "Forge Level 2"
section that references the retired `forge-dispatch`/`forge-worker` skills). Any
`forge-start` without `--allow-dirty-base` refuses. Independent of B2 — clearing this
alone still leaves B2.

### B4 — a branch checked out at a legacy sibling worktree is fatal
**Hits:** `goparentbugs` · **Exit 4** · `bin/forge:1242-1247`

```
forge worktree: branch 'forge/goparentbugs' is already checked out at
  /Users/…/automation/goparent-ai-goparentbugs
```

`forge/goparentbugs` lives in a **pre-`e24e1be` repo-sibling worktree**. The code can
already adopt a *free* existing branch (`:1248-1249`) but treats "checked out
somewhere" as terminal — even when that somewhere is a legitimate session worktree
that should simply be reused. Running `forge-start` from *inside* that worktree fails
identically; only `--here` works.

---

## 3. Root cause

`forge-start` began cutting a worktree per session on 2026-08-08. Every existing
session predated that and had never exercised the path. The preconditions were
therefore never validated against the real fleet — they were validated against
`goparent-ai`, which satisfies all of them.

Killing all sessions (2026-08-11) forced every root down the new path at once, and
four latent defects surfaced together. **None were introduced by the fan-out work on
`feat/fix-runner-session-fanout`**; `bin/forge` and `bin/forge-start` are byte-identical
to `main` on that branch (`git diff --stat main..HEAD -- bin/forge bin/forge-start` is
empty).

---

## 4. Recommended changes, in priority order

### T1 — `.dev/` should self-remediate via `info/exclude` (fixes B1)
**Files:** `bin/forge:701-705`, `bin/forge:1078-1080`
**Model:** `_worktree_ensure_ignored()` at `:874-888` — copy its shape.

Replace both `die`/`return 2` sites with an ensure-then-verify helper that appends
`/.dev/` to `$(git rev-parse --path-format=absolute --git-common-dir)/info/exclude`
and re-checks with `check-ignore`. Only fail if the write or the re-check fails.

- Unblocks `feedforge` and `promptlol` with **zero commits to those repos**.
- Symmetric with how the sibling problem is already handled 170 lines away.
- Keep `--force` for the genuinely-tracked-`.dev`-paths case; `info/exclude` does not
  untrack an already-tracked file, so that must still be detected and reported.

### T2 — make the post-seed guard a true delta (fixes B2)
**File:** `bin/forge:1256-1261`

Snapshot `git status --porcelain -uno` **before** seeding, diff against the after
snapshot, and fail only on paths that changed state. Preserves the guard's actual
intent (catching seeding that dirties the tree) while ignoring pre-existing dirt.

Deliberately preferred over an LFS-specific carve-out: the same class of false
positive arises from any clean/smudge filter, `core.autocrlf`, or a stale index.

### T3 — reuse a session worktree instead of dying (fixes B4)
**File:** `bin/forge:1242-1247`

When `$branch` is checked out at a path that is itself a forge session worktree,
**adopt and reuse it** — that is what the operator means by `forge-start <name>` for an
existing session. Fail only when the owner is genuinely foreign (e.g. a hand-made
worktree outside the session convention).

### T4 — migrate or retire legacy sibling worktrees
Not code — inventory and cleanup, but it is why B4 exists at all.

`goparent-ai-goparentbugs` sits beside the project and reads as a project. Either
migrate it under `goparent-ai/.forge-worktrees/` or retire it once its branch is
merged. Also present: **six** `~/.codex/worktrees/*/goparent-ai` worktrees
(detached HEAD), which `git worktree list` reports and which any per-root scan must
tolerate.

### T5 — per-project remediation, only if T1/T2 are declined
- `feedforge`, `promptlol`: commit `.dev/` to `.gitignore`.
- `headless_factory`: `git lfs migrate import --include="assets/background_music/tracks/*.mp3"`
  (**rewrites history**) or drop the LFS filter; plus commit or discard `CLAUDE.md`.

T5 pushes forge's preconditions into every project's git history. T1/T2 keep them in
forge, which is where they belong.

---

## 5. Verification

A precondition audit that runs against every root without creating sessions is the
real deliverable here — the fleet was never checked as a fleet, which is how four
defects hid at once. Suggested: `forge worktree ensure --dry-run` extended to report
B1-B4 per root, plus a `tests/forge-worktree/` case per blocker:

- `T-WT-DEV-EXCLUDE` — a repo with un-gitignored `.dev/` provisions cleanly, and
  `info/exclude` carries `/.dev/` afterwards.
- `T-WT-PRESEED-DIRT` — a repo with a tracked file dirty **before** seeding provisions
  cleanly; a seed step that dirties a tracked file still fails.
- `T-WT-BRANCH-REUSE` — a branch checked out at an existing session worktree is reused,
  not refused.

Acceptance: **all five session roots start with the bare command** —
`forge-start <name>` — with no `--here` and no `--allow-dirty-base`.

---

## 6. Current state (as left, 2026-08-11)

All five sessions are running via workarounds, and are expected to keep working:

```bash
cd goparent-ai              && forge-start goparent            # worktree mode
cd goparent-ai-goparentbugs && forge-start --here goparentbugs # B4
cd headless_factory         && forge-start --here animate      # B2 + B3
cd feedforge                && forge-start --here feedmint     # B1
cd promptlol                && forge-start --here promptlol    # B1
```

**Session names are not free-form.** The broker pins a session name per root in
`<git-common-dir>/forge/broker-v1/roots/<root_id>`; starting with a different name
raises `BROKER_IDENTITY_MISMATCH` (`bin/forge-broker:1466-1485`) and tears the session
down. Recorded: `goparent`, `goparentbugs`, `animate`, `feedmint`, `promptlol`. A
stale entry for `forge-1` remains under `goparent-ai` from a probe whose worktree was
removed; harmless, but it is the same stale-incarnation class as
`.dev/proposals/broker-stale-incarnation/`, whose plan is complete and unimplemented.

Committed today: `feedforge@306a6f4` (worktree seed declaration, on branch
`fix/image-content-missing-file-404` — may want moving), `goparent-ai@02a620a`
(unrelated test fix).

---

## 7. Do not

- **Do not** normalise `--here` into the docs or the SKILL as the way to start a
  session. It silently disables per-session isolation, and the fan-out work on
  `feat/fix-runner-session-fanout` assumes one worktree per session.
- **Do not** weaken the post-seed guard to a warning. It is the only thing standing
  between a mis-seeded worktree and a pipeline that verifies the wrong source tree.
  Make it a delta (T2); do not make it advisory.
- **Do not** treat `goparent-ai-goparentbugs` as a project when auditing.
