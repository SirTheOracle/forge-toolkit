# Forge control center (`web/`)

A locally run browser layer over the Forge system. It **renders what `.dev/` and the CLIs
already expose** and **invokes commands that already exist**. It is not a second source of
truth, it has no database, and it never writes pipeline state.

## Setup

Prerequisites: Python 3.11+, npm, and tmux. The build/test dependencies support Node 20.x at 20.19+, Node 22.x at 22.12+, or Node 24+. Use the active project Python environment for the backend installation.

direnv is already active in this repository. **No `source`, no `PYTHONPATH=` prefix.**

```
python3 -m pip install -e 'web/backend[test]'
npm --prefix web/frontend ci
npm --prefix web/frontend run build
bin/forge-web                      # or the installed `forge-web`
```

`bin/forge-web --check` reports **every** unmet precondition at once and exits 1 without
starting anything. Browser tests need a browser binary:
`npm --prefix web/frontend run e2e:install` downloads `chromium-headless-shell` into the
Playwright cache, outside this repository.

## What it reads, and what stays authoritative

| Shown | Read from | Authoritative source |
|---|---|---|
| Live sessions | `tmux list-sessions` (name, physical path, `$N` session id, created) | tmux |
| Worktree label | `~/.config/forge/registry.yml` **only on exact realpath match**, else the root basename | the physical root |
| Objective | `.dev/proposals/<slug>/problem-statement.md`, first 400 plain-text characters | the file |
| Session pending stage | owned `response: null` entries in `.dev/proposals/<slug>/forge-log.yml` | the log |
| Board rows, ack scope | `forge board --json` | `forge-watch` |
| Asks | `.dev/attention/ask-*.json` | the records |
| Feed | `.dev/forge-tmp/orchestrator-events.log` + current attention records | those files |
| Responses | `.dev/attention/payloads/response.<dispatch-id>.txt` | the payload |

`.dev/` stays authoritative. Nothing here caches pipeline truth, and a GET never repairs,
rewrites or creates a source file.

## Two stage fields, deliberately

`forge-watch` computes supersession across `(slug, stage)` with **no session binding**, so a
sibling session finishing `coding` for the same slug suppresses another owner's open `coding`
row on the board. The web field is therefore labelled **"Session pending stage"** and computed
per owning session. **The two can legitimately disagree** under same-root, same-slug
concurrency. Both are shown with their provenance; neither is corrected into the other, and
the watcher's own rows are never mutated.

## Ack scope, and why the preview is an estimate in BOTH directions

`forge-watch ack TARGET` matches `session | slug | basename(root) | root` with an OR, across
the **full** board — including the collapsed maintenance rows the UI hides. The preview shows
what is visible in your fleet plus an aggregate count of matches outside it.

Ack **rescans at ack time**, so a condition can appear as well as disappear: the applied count
can be **higher** or lower than the preview. The UI never says "up to N".

Ack does **not** resolve asks or parks.

### "Not applied: watcher busy"

`forge-watch` takes its state lock before dispatching a mode. With the lock held, `ack` exits
**0 with empty stdout** and one stderr line — it did nothing. The control center classifies
that specific, positively identified case as **not applied** and retries it, at most five
attempts, starting retries within 15 s, with jittered backoff. **Every other unclear
outcome** — a timeout, truncated output, an unrecognised message, `rc 75` on the ack path — is
**unknown and is never retried**.

## Partial outcomes

An operation is never collapsed into an exit code:

- **Answer delivered; cleanup failed** — the answer reached pane 0 but the callback was not
  consumed. Reconcile the callback from pane 0. **Never re-answer the archive.**
- **Answer recorded; delivery failed** — only this case offers a plain redispatch.
- **`PARK PARTIAL`** — the parked record stands but the infra lock was not released.
- **`FINALIZE_DEFERRED`** — the terminal cleanup write failed; retry finalize.
- **Spawn partial** — the session is alive and on the board but population was incomplete.
- **Pause/stop** are **requests**. Stop does not kill tmux or any running worker.

## Spawn

The picker offers the union of registered roots and live-discovered roots, deduplicated by
physical path. Spawn preserves `forge spawn`'s ensure, registration, billing, name-collision
and partial-startup semantics. **No worktree is ever created implicitly.**

## Terminal-required

A native permission or trust selector in a pane is **not** a Forge ask and cannot be answered
by a pane-0 dispatch. The control center shows it as **terminal required** and offers **no**
answer control. Attach to the session in tmux. Ordinary messaging to pane 0 is still
available, but it is not modal approval.

## Pause, then resume

Resume through the **composer**: send `forge-resume` as ordinary free text. There are no
dedicated Resume or Skip controls, and nothing in this action set can bypass verify.

## Restart and duplicates

Every mutation carries a client-generated `request_id`. The same id with the same body returns
the same receipt and launches nothing twice; the same id with a different body is refused.
Receipts are **in memory only** and are bounded. A server restart loses them and may leave a
child process running: the UI reports the outcome as **uncertain** and nothing is replayed.
Refresh `.dev/` before acting again.

## Collection and action recovery

The fleet shows each active slug and objective. Board findings join exact session/root
and recorded incarnation; sessionless notices remain project evidence. Collection failures
retain successful observations with stale labels. Board read age advances independently of
board scans. File work rotates across sessions and proposal/attention directories; large
inventories take multiple ticks, and missing owner/time proof is omitted from session feeds.
Callback, task and episode adapters include only current proved ownership. A callback
directory above 256 entries refuses pending actions until the selection can be proved.

The browser retains composer, answer and entry-note drafts, polls queued operations, and
keeps results on the originating session. Reconcile same request reuses its exact body and
request ID. Mutation bodies include an expected server instance; a restarted server refuses
old intents before lookup or launch. Active reservations cannot expire while work is queued
or running. Completed receipts remain bounded; uncertainty after eviction is explicit.

Each parked entry has its own Resolve control; pending/current and concurrent entries have
separate Park controls. Callback ID/hash/absence is captured when that entry is displayed.
Changing the acknowledgement scope replaces its exact target, matching preview and outside
fleet aggregate. Completed ack output reports actual applied conditions, aggregating details
outside the visible fleet.

Child PATH pins this checkout and the active Python before preserving operator toolchain
paths. Startup proves PyYAML is importable by that Python. Operator credentials, billing and
configuration remain inherited; ambient tmux/role identity and test seams are removed,
then trusted Forge binaries and identity enforcement are pinned. Test fixture variables are
accepted only with the existing server-side test override switch.

The browser fixture copies the launcher, package and production assets into its own
`.e2e-checkout` and installs an isolated venv. `npm run test:e2e:ci` actually launches the
headless shell and fails when it cannot run. To inspect the disposable service without a
browser test runner, use `node scripts/e2e-server.mjs` from `web/frontend`; set
`FORGE_WEB_E2E_PORT` if another fixture uses the default port. The optional
`scripts/fixture-smoke.py --port PORT` exercises its HTTP action lifecycle after fresh startup.
Never point the fixture smoke script at a live service; it requires the fixture root marker.