# forge-fix-runner — forge-toolkit specifics

Everything in `SKILL.md` (a symlink to the toolkit template) is identical across
projects. This file is the forge-toolkit-only part. `scripts/` is a real directory,
never a symlink — the PER-PROJECT CONFIG block in `queue.py` is the whole point.

**Repo:** `SirTheOracle/forge-toolkit` — **PUBLIC**. Nothing written to an issue,
PR body, or comment may carry an absolute home path, a private project name, or
prompt text. `bin/forge-issue-sync` enforces this at creation; the same discipline
applies to anything the runner writes.

## Classifier

`component:` — not `service:`. Values track the code layout:

| component | covers |
|---|---|
| `bridge` | `bin/forge-bridge` |
| `watch` | `bin/forge-watch` |
| `cc-hook` | `bin/forge-cc-hook`, `config/*-hooks.json` |
| `start` | `bin/forge-start`, worktree provisioning, session identity |
| `recover` | `bin/forge-recover` paths in `bin/forge` |
| `broker` | `forge-broker`, `forge codex-broker` |
| `skills` | `skills/`, `agents/`, `commands/`, orchestrator behavior |
| `swiftbar` | `swiftbar/` |
| `install` | `install.sh`, deployment topology |

## Blocking labels

Defaults (`needs-retest`, `in-progress`, `fix-pr-open`) plus **`operator-next`**.

`operator-next` marks the operator lane: `kind:live-gate` issues that only a human
can close by running the gate. They are admitted work, but not *runner* work, and
`test_operator_next_is_never_actionable()` pins that. Never move an issue from
`operator-next` to `forge-fix` to "make it drainable" — it isn't.

## Admission

`forge-fix` is human-only (invariant 3). The queue also carries `kind:*`,
`severity:*` and `source:*` labels applied at creation by `bin/forge-issue-sync`;
only `forge-fix` and `operator-next` admit work.

## Readiness probes

The suites are hermetic bash and each resolves the binary under test from its own
repo root, so a linked worktree is fully isolated until merge. There is no database
and no fixed port, so `INFRA_FREE_TEST_PATTERNS` genuinely matches here — unlike a
shared-DB project — and fan-out approaches 1x rather than 1.5-1.9x.

    --db-probe      (not applicable — no database; DB_FALLBACK_MARKERS is empty)
    --import-probe  bash --version

## Caveat unique to this project

**The runner fixes the framework it is running on.** `bin/*` and `skills/*` are
live-symlinked into `~/bin` and `~/.claude`, so an edit takes effect in every
running session immediately, including the session doing the fixing. Work in a
worktree, keep every touched script `bash -n` clean, and treat deployment as a
separate deliberate step — never assume a merged fix is live, and never assume an
unmerged one is not.
