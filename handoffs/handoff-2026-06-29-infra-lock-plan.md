# Plan — Cross-Worktree Infra Mutex (serialize infra-touching stages)

**Date:** 2026-06-29
**Author:** session (Opus 4.8)
**Status:** PLAN — **revised R4** per Codex review R4
(`handoff-2026-06-29-infra-lock-plan-review-r4.md`, 2026-06-29; a focused pass on
the callback lifecycle, supersedes R1–R3 reviews). **Decisions D1 + D2 resolved
by the user (2026-06-29):** D1 = **restart-on-entry** (stage-specific per R3-5),
D2 = **keep local QA fallback + amend Hard Rule 22**. R4 verdict: *"architecture
stable; the lock, owner identity, restart policy, skill mirroring, and Shape B
fixes are coherent"* — one last callback-lifecycle contract change, now folded in
(§0.R4). **Implementation-ready.** Not yet implemented.
**Scope:** new `forge-bridge infra-lock` subcommand + orchestrator-side
integration in `skills/forge-orchestrator/SKILL.md` (incl. a **Hard Rule 22
amendment** and **Hard Rule 23**) + docs. **No worker prompt/template changes.**
**Revised in R2:** a *small* change to `wait`/`callback` IS now in scope — a
callback-consumption mechanism so the non-terminal hold loop can't re-read a
stale callback (R2-1). `LOCK` events stay internal to `cmd_infra_lock` (not via
`cmd_emit` — see §8).

> Source problem statement: "Serialize infra-touching stages across worktrees."
> Decision for v1 (from the statement): **a lock, not isolation.** One
> cross-worktree mutex; infra stages run one at a time globally; reasoning
> stages stay parallel. Per-worktree DB/port isolation is explicitly deferred.

---

## 0. Review incorporation (R1)

Codex review verdict: "right v1 direction; needs a tighter lifecycle model
before coding." All nine findings accepted; one additional finding (service
teardown) added by the lead from a follow-up check the review did not cover.

| # | Finding (review) | Sev | Resolution |
|---|---|---|---|
| F1 | Release on *every* `wait` outcome breaks mutual exclusion (PROMPTING/STALLED/TIMEOUT aren't proof the worker stopped) | High | **ACCEPTED — core rewrite.** Release is now keyed on **stage terminality**, not on one `wait` return. Release only on terminal callback (`DONE`/`ERROR`); **hold** through `PROMPTING`/`STALLED`/`TIMEOUT`/`DEAD`/`BLOCKED`. §6, §7. |
| F2 | Dispatch failure after acquire leaks the lock | High | **ACCEPTED.** Explicit "dispatch failed after acquire → release immediately" branch (worker never started ⇒ infra untouched ⇒ safe). No blanket `trap EXIT` (unsafe while a worker runs). §7. |
| F3 | Local QA / QA-retry fallback (inline pane-1, no dispatch/wait) is uncovered | High | **ACCEPTED — verified** (`skills/forge-orchestrator/SKILL.md` qa Path B + qa-retry). Added a second integration shape: `acquire → run inline → digest → release`. §7. |
| F4 | Same-session + different-slug auto-steal is unsafe (live session, slug A worker may still run) | High | **ACCEPTED.** Dropped name-only steal. Holder now records the tmux **session id** (`#{session_id}`); steal only when the recorded session id is not live. Same **live** session + different slug → **CONFLICT escalation**, never auto-steal. §4, §5. |
| F5 | Same-slug reentrancy ignores stage; can hide a stale holder | Med | **ACCEPTED.** Reentrancy is stage-aware: same `(session_id, slug, stage)` → `ALREADY_HELD`; same `(session_id, slug)` + different stage → update `stage`/`acquired_at`, emit `LOCK action=stage_update`. §5. |
| F6 | "Crash-safe by construction" is overclaimed | Med | **ACCEPTED.** Reworded to **dead-session recovery**. Live-abandoned and foreign-host holders need operator recovery; recovery docs added. §6. |
| F7 | `status` should report stale/dead, not just held/free | Med | **ACCEPTED.** `status` now evaluates local liveness: `FREE` / `HELD live` / `STALE dead-session` / `HELD foreign-host`. Force-release shown only in stale/timeout guidance, with a collision warning. §5. |
| F8 | Test harness can't prove live-holder blocking (no real tmux ⇒ holder looks dead ⇒ B steals not blocks) | Med | **ACCEPTED.** Tests use real temp tmux sessions (or a `FORGE_TMUX_LIVENESS_CMD` injection seam). New cases added. §9. |
| F9 | Event-whitelist intent unclear | Low | **ACCEPTED.** `LOCK` is **internal-only** (emitted by `cmd_infra_lock` via `_emit_event`). No `cmd_emit` whitelist change; `forge-bridge emit LOCK …` is not a public event. §8. |
| F10 | **(lead)** Infra stages don't tear down services — release-on-terminal doesn't free ports / guarantees fresh code | — | **NEW.** Documented as a v1 boundary + minimal SKILL mitigation (restart-on-entry). The lock serializes DB/test-state and prevents concurrent port-bind races; cross-worktree dev-server staleness belongs to the deferred isolation track. §11. |

**Assumption-language corrections (R1):** fixed local ports + shared DB are
verified in *sampled* configs (promptlol backend `8001` / frontend `5180`;
local DB `localhost:5432/shield_platform`) — not asserted universally; pgvector
is verified for **FeedForge**, not all projects; `qa-fix` locking is
**conservative** (its prompt does not itself require live tests), justified by
its place in the infra-heavy QA loop; the backup→edit→mirror→no-commit edit
protocol is sourced from **prior handoffs** (durable), referenced here by
procedure rather than by a Claude-side memory slug a Codex reviewer can't see.

---

## 0.R2 Review incorporation (R2)

Codex R2 verdict: "strong improvement but still has implementation blockers."
All ten findings accepted. Two of them surface **product decisions only the
user can settle** (D1 service staleness, D2 local-QA-fallback policy) — flagged
below and carried to the user; the rest are resolved in the body.

| # | Finding (R2) | Sev | Resolution |
|---|---|---|---|
| R2-1 | Non-terminal `BLOCKED` loop can't work — `cmd_wait` returns on *any* existing callback file; `_wait_emit_callback` never consumes it, so a re-`wait` re-reads the stale `BLOCKED` and can wedge the held lock | High | **ACCEPTED — verified** (`bin/forge-bridge:2092-2097`, `:2129-2163`). Added a callback-consumption mechanism. *(The R2 `wait --after <ts>` design was **withdrawn and replaced** by archive-on-consume in R3-2 — see below.)* Scope updated: a small `wait`/`callback` change is now in scope. §7, §8. |
| R2-2 | Ownership compare must include `host` — tmux session ids aren't globally unique, so a local `$43` could match/release a foreign-host holder | High | **ACCEPTED.** Owner key is now `(host, session_name, session_id, session_created, slug)`. Acquire checks `h.host == my_host` **before** any same-session branch; release requires host match. §4, §5. |
| R2-3 | Session id alone doesn't survive tmux **server restart** (ids restart low; a new session can reuse `$0`) | High | **ACCEPTED.** Store + compare `session_created` (epoch) alongside id; liveness predicate renamed `tmux_owner_live(host, name, id, created)`. Matching id + different `created` ⇒ stale, not live. Server `#{pid}` stored for diagnostics. §4, §5. |
| R2-4 | Plan relies on local QA fallback but Rule 22 says proposal is the ONLY local stage / "all other stage work forbidden in pane 1" — a live contradiction | High | **ACCEPTED — verified** (`SKILL.md:1048-1067`); **D2 RESOLVED = keep + amend.** Hard Rule 22 amended to name local `qa`/`qa-retry` fallback a pane-1 exception gated on `(codex-b unavailable) AND (held infra lock)`; Shape B retained; anti-divergence acceptance check added. §7, §8. |
| R2-5 | Service staleness left "optional" ⇒ v1 may still run B's QA against A's running server (wrong worktree code); "port collisions impossible" is overstated | High | **ACCEPTED — D1 RESOLVED = restart-on-entry.** Minimal SKILL-level restart-on-entry is in scope (each infra stage restarts services against *its* worktree before testing); "port collisions impossible" → "concurrent port-bind races impossible." §8 item 5, §11. |
| R2-6 | Shape B omits required local-stage log closure — Rule 5: the `dispatch` guard refuses the next stage until the prior local entry is closed | Med | **ACCEPTED — verified** (`SKILL.md:904-912`). Shape B now spells out the local log lifecycle: `log` (open) → inline run → `log-response --to claude --stage …` (close) → digest → release. §7. |
| R2-7 | `DEAD` handling underspecified — `dispatch` needs a valid pane/session; repair may be required first | Med | **ACCEPTED.** `DEAD` split into: pane-dead/session-repairable (hold, repair, re-dispatch); session-killed (dead-session steal / abort force-release); repair-abandoned (force-release only after confirming no child service/test proc). §7. |
| R2-8 | Timeout escalation lacks the metadata operators need to judge a force-release | Med | **ACCEPTED.** Timeout block now prints `host, session, session_id, session_created, slug, stage, acquired_at, project_root, liveness-verdict`. `project_root` added to the sidecar. §5. |
| R2-9 | `FORGE_TMUX_LIVENESS_CMD` test seam risks arbitrary shell eval in production | Med | **ACCEPTED.** Dropped the env-eval seam. Tests use **real temp tmux sessions** only; no production injection surface. Added a negative test that session-id/sidecar values can't inject shell syntax. §9. |
| R2-10 | A few phrases still overstate universality | Low | **ACCEPTED.** "single Postgres + pgvector" → "shared Postgres; some projects (e.g. FeedForge) use pgvector"; "port collisions impossible" → "concurrent port-bind races impossible"; scope line updated to admit the `wait`/`callback` change. §1, §2, header. |

**Decisions — RESOLVED by the user (2026-06-29):**
- **D1 — Service staleness → restart-on-entry.** v1 includes the minimal
  SKILL-level restart-on-entry (each infra stage restarts services against its
  own worktree before testing). Full guarantee holds: each infra stage tests its
  own code, one at a time. Adds the QA/verify SKILL edits (§8 item 5). §11.
- **D2 — Local QA fallback → keep + amend Rule 22.** Local `qa`/`qa-retry`
  fallback stays; Hard Rule 22 is amended to name it a pane-1 exception gated on
  `(codex-b unavailable) AND (held infra lock)`. Shape B retained; add the
  anti-divergence acceptance check. §7.

---

## 0.R3 Review incorporation (R3)

Codex R3 verdict: "right v1 direction; remaining blockers are precision gaps
introduced by the R2 fixes — after these the implementation is bounded and
testable." All eight findings accepted; all verified in code by the lead.

| # | Finding (R3) | Sev | Resolution |
|---|---|---|---|
| R3-1 | `release` matches `(host, session_id, slug)` but acquire's owner key is `(host, session_id, session_created, slug)` — a stale holder with a reused id could be released | High | **ACCEPTED.** `release` now matches the **full** owner key `(host, session_id, session_created, slug)`; `stage` is the only deliberately-omitted field (so stage-update works). §5. |
| R3-2 | `attempt_ts`/`wait --after` is unsafe — bridge `timestamp()` is **second-resolution** (`:256-258`), so a same-second continuation callback gets ignored; and `ts:=now` is a local var that doesn't survive resume | High | **ACCEPTED — redesigned.** Dropped `attempt_ts`/`--after` entirely. Replaced with **archive-on-consume**: a re-`wait` blocks for a *fresh* file (no timestamp compare). Durable state = filesystem + the open pending entry (R3-3). *(R3 archived **inside** `cmd_wait`; R4-1 later moved consumption to an explicit post-continuation `callback-consume` — see §0.R4 — to fix a resume deadlock. The final contract is in §7.)* §5, §7. |
| R3-3 | `cmd_callback` auto-closes the pending log entry for **every** status incl. `BLOCKED` (`:2001-2007`) — so after `BLOCKED→continue` there's no open pending; stall-check reads `IDLE`, the held lock looks abandoned | High | **ACCEPTED — verified.** `cmd_callback` no longer closes the pending on **non-terminal** status (`BLOCKED`): it records the blocked message (note) and **keeps the pending open**; only terminal `DONE`/`ERROR` close it. The open pending is the durable "in-flight under held lock" signal stall-check needs. §7. |
| R3-4 | Restart-on-entry edits to repo `skills/` won't reach workers — prompts read **installed** paths; `~/.codex/skills/adversarial-qa` ≠ repo | High | **ACCEPTED — verified** (qa/qa-retry → `~/.codex/skills/adversarial-qa/SKILL.md`, **differs** from repo; verify → `~/.claude/skills/adversarial-verify/SKILL.md`; coding → `~/.claude/skills/forge-coder/SKILL.md`). Edit protocol extended: edit repo skill **then mirror to the installed path(s) workers actually read**, verify by hash/grep in both. Acceptance check fails if restart text is repo-only. §8. |
| R3-5 | "Each infra stage restarts services" conflicts with forge-coder's "never start/stop services" constraint, and over-applies to `qa-fix` | High | **ACCEPTED — D1 made stage-specific.** Restart-on-entry applies to **qa, qa-retry, verify** only (they validate a running app). **coding** keeps the forge-coder constraint (relies on Playwright `webServer` autostart from its own dir; residual noted). **qa-fix** does **no** restart by default (prompt runs no live tests). Guarantee wording qualified. §11. |
| R3-6 | Shape B failure before `log-response` leaks both the lock **and** the open local pending (next dispatch refused) | High | **ACCEPTED.** Explicit Shape B failure branch: on inline failure/abort, **close** the local entry (`FORGE_ERROR`/`AGENT_FAILED`) **then** release (after confirming no unsafe service proc), then surface. §7. |
| R3-7 | "Kill whatever occupies the fixed ports" is unsafe — could kill an unrelated process on a shared dev machine | Med | **ACCEPTED.** Restart-on-entry uses a **config-driven, identity-checked** port-process rule: only stop a process whose port + command/cwd matches the project's expected service shape; an **unknown** process on a configured port → **structured escalation**, not a silent kill; log pid/command/cwd/port/worktree of anything stopped. §11. |
| R3-8 | Shape B holds the lock through digest, which only reads disk artifacts (Rule 10) and needs no infra | Med | **ACCEPTED.** Shape B releases **right after** the local run writes artifacts + the local entry closes; the digest runs **after** release (matching Shape A's effective lifecycle). §7. |

**Minor (R3):** §6 wording "session id no longer live" → "session **identity**
(id + creation time)"; the premature "implementation-ready" header is downgraded
(above); the repo-vs-`~/bin` byte-identical claim is treated as an
**implementation-time preflight**, re-verified before editing.

---

## 0.R4 Review incorporation (R4) — callback lifecycle

Codex R4 reviewed **only** the R3 callback-lifecycle redesign (the new,
shared-surface piece). Verdict: archive-based consumption is the right direction,
but **consumption must be tied to "continuation committed," not "wait read it,"**
and terminal publication must be ordered after the log close. Both accepted;
both verified in code.

| # | Finding (R4) | Sev | Resolution |
|---|---|---|---|
| R4-1 | **Archiving inside `wait` deadlocks resume before continuation.** If the orchestrator dies after `wait` returns `BLOCKED` (callback already archived) but before `send --force`, resume sees an open pending + empty canonical path and waits forever for a callback the still-blocked worker won't send | High | **ACCEPTED — my R3 design was wrong here.** Split *read* from *consume*: `cmd_wait` **reads and leaves** the canonical `BLOCKED` callback in place. A new explicit `forge-bridge callback-consume --slug S --stage T --status BLOCKED` archives it, called by the orchestrator **only after `send --force` succeeds**. Resume before continuation re-surfaces the durable `BLOCKED` (no deadlock); resume after consume blocks for the fresh callback. §7, §8. |
| R4-2 | **Terminal publish races the pending close.** `cmd_callback` writes the callback file *then* `cmd_log_response` (with `|| true`), so a polling `wait` can return `DONE` before the pending is closed — the orchestrator then releases the lock and the next `dispatch` hits an open-pending guard | High | **ACCEPTED — verified** (`:1987-1999` before `:2001-2007`; `|| true` at `:2006`). Terminal `DONE`/`ERROR`: **close the pending first, then publish the callback atomically** (write temp → `mv` into canonical path only after the close succeeds). **Drop `|| true` on terminal** log-response — a failed close must not publish a successful terminal callback. §8. |
| R4-3 | `add-note` is too fragile to be the non-terminal source of truth — it needs an existing context file and interpolates the note into Python source (`:2512-2544`) | Med | **ACCEPTED.** The **canonical callback file** (while blocked) / **archived callback** (after consume) is the source of truth for the blocked message; `add-note` is **best-effort observability only**, made YAML/arg-safe, and its failure never blocks writing the `BLOCKED` callback. §7, §8. |
| R4-4 | Archive naming/atomicity unspecified | Med | **ACCEPTED.** `mkdir -p` the archive dir; unique name `{slug}-{stage}.{callback_id}.callback`; preserve contents byte-for-byte; **atomic same-filesystem `mv`**; `callback-consume` with no canonical `BLOCKED` present returns a **structured no-op** so the orchestrator doesn't re-wait blindly. §8. |
| R4-5 | Verification missed the **before-continuation** resume window (the one R3 broke) | Med | **ACCEPTED.** Added: resume-before-continuation re-surfaces `BLOCKED`; continuation-fails leaves canonical `BLOCKED` visible; `DONE`-publish race (aggressive `wait` poll vs `callback DONE`) must not return terminal before the pending closes. §9. |

**Note on blast radius:** R4-2 changes the **terminal callback path used by every
stage**, not just infra stages — the highest-risk edit in the plan. It fixes a
race that exists today, but it must ship with a `forge-bridge` backup and the
`DONE`-publish-race test before anything else. Flagged in §8.

---

## 1. Problem

Forge is share-nothing per worktree: each pipeline has its own `forge-N` tmux
session and its own `.dev/` state, so two worktrees of the same repo run fully
independent pipelines with no on-disk collision. That isolation holds for
everything **except external infrastructure**: workers read `services.*`
(ports, dev-server commands, health checks) and `testing.*` from
`.claude/forge-project.yml` and point at **one shared stack** — shared local
services such as a fixed-port backend/frontend and a shared Postgres (some
projects, e.g. FeedForge, use pgvector). When two worktrees run concurrently,
the stages that bring up services or exercise the live DB collide (same DB,
same ports, interleaved test state).

We want N worktrees running pipelines at once against that single shared stack,
with the infra-touching stages never overlapping, while the five reasoning
stages keep running fully in parallel.

---

## 2. Confirmed infra-stage set (verified, not assumed)

The statement's working assumption was `coding, qa, verify`. Confirmed against
how workers actually consume `forge-project.yml` — the env preamble is built by
`_render_preamble` (`bin/forge-bridge:1632–1670`) and injected into every stage
template via `<<<INCLUDE _preamble>>>`; the qa / verify / coder SKILLs are where
live services are actually required.

| Stage | Tier | Infra touch | Lock? |
|---|---|---|---|
| proposal | HIGH (local) | none — preamble only | **no** |
| review | HIGH | none | **no** |
| incorporate | HIGH | none | **no** |
| implementation | HIGH | none | **no** |
| impl-review | HIGH | none | **no** |
| **coding** | THROUGHPUT | runs test suite after each commit group (may auto-start services / hit DB) | **yes** |
| **qa** | THROUGHPUT | hard — requires live backend+frontend, binds ports, hits DB | **yes** |
| **qa-fix** | THROUGHPUT | conservative — prompt resolves QA findings + reports changed files; does **not** itself mandate live tests (`prompts/qa-fix.txt`); locked as part of the infra-heavy QA loop | **yes (conservative)** |
| **qa-retry** | THROUGHPUT | hard — re-runs qa | **yes** |
| **verify** | HIGH | hard — live app, re-runs e2e/screenshots | **yes** |

**Refinement over the assumption:** the guess was right about coding/qa/verify
but missed the **qa-fix / qa-retry** loop stages, where two worktrees most
realistically collide (qa→qa-fix→qa-retry→verify). v1 locks **all five**
infra-touching stages (user-confirmed 2026-06-29). The five reasoning stages
never touch the lock.

> Coarseness note: `coding` is the longest stage, so locking it serializes the
> most infra time globally. This is the accepted v1 trade-off — locking it is
> what makes **concurrent port-bind races impossible by construction** actually
> hold. (Note: "impossible by construction" applies to *simultaneous* start
> attempts; a fixed port may still be *occupied* by a prior holder's
> still-running server — that staleness gap is Decision D1 / §11, not the
> port-bind race.)

---

## 3. Decisions (open questions → resolved)

| Question | Decision | Rationale |
|---|---|---|
| **Lock owner** | **Orchestrator-side** — acquire before `dispatch`; release keyed on **stage terminality** (not on a single `wait` return — see F1/§6/§7) | One integration point beside dispatch; **no worker pane sits idle-blocked**, so the stall classifier cannot misread the wait (see §6). Coarser hold (spans worker think-time) accepted for v1. Worker-side tightening deferred. |
| **Mechanism** | **Pidfile + flock-guarded steal**, holder identity = `(host, session_name, session_id #{session_id}, session_created #{session_created}, slug)`; liveness = `tmux_owner_live(host, name, id, created)` (R2-2/R2-3) | Fits Forge's file-based, no-daemon idiom; spans separate CLI invocations naturally; sidecar doubles as the observability source. Host guards against cross-host session-id collisions; `session_created` guards against tmux-server-restart id reuse; a **dead** holder never wedges a live waiter (steal under guard). |
| **Lock location** | `$(git rev-parse --path-format=absolute --git-common-dir)/forge-infra-lock/` | Worktrees share `.git` but not `.dev/`. The git **common dir** is the one anchor all worktrees resolve identically. Inside `.git` ⇒ untracked ⇒ clean revert, no migration. Must use `--path-format=absolute` (bare `--git-common-dir` returns a relative `.git`, unstable across worktree cwds). |
| **Granularity** | **One global infra lock** | No DB/port/service split without concrete need (per statement). |
| **Wait ceiling** | `FORGE_INFRA_LOCK_TIMEOUT_S` default **1800s**; poll interval `FORGE_INFRA_LOCK_INTERVAL_S` default **15s** | Infra stages legitimately run long (qa/coding 20–30 min), so a waiter may wait that long. On ceiling: escalate to user, never silent-forever. Independent of per-stage `wait --timeout` (acquire happens before dispatch). |

---

## 4. Lock files (under the shared anchor)

`<git-common-dir-abs>/forge-infra-lock/`:

- **`infra.flock`** — guard file, `flock`'d **only** during the brief
  check-then-act critical section. Never held across invocations.
- **`infra.holder`** — sidecar YAML, **present ⟺ lock is held**:
  ```yaml
  host:           <hostname>         # R2-2: ownership compares include host
  session:        forge-N            # tmux session name
  session_id:     $3                 # tmux #{session_id} (F4)
  session_created: 1782139047        # tmux #{session_created} epoch (R2-3: survives server restart id reuse)
  tmux_pid:       6282               # tmux server #{pid} — diagnostic only (R2-3)
  slug:           <pipeline-slug>
  stage:          <coding|qa|qa-fix|qa-retry|verify>
  project_root:   <abs worktree root> # R2-8: lets recovery "inspect that worktree"
  pid:            <acquiring pid>     # informational
  acquired_at:    <iso8601>
  ```
  Ephemeral (removed on release). This is the only "state," and it is the
  observability source for `/forge status`. No schema, no migration → clean
  revert is `rm -rf` the dir. A corrupt/unparseable sidecar must produce a safe
  escalation (treated as "held, unverifiable → escalate"), never a traceback
  (R2-9 test case). `project_root`/`tmux_pid` are recovery/observability
  metadata (constraint 5), not coordination state — still a mutex, not a registry.

---

## 5. Command surface — `forge-bridge infra-lock <action>`

Lives logically beside `dispatch`/`wait`/`callback`. Reuses the existing
`fcntl.flock` + atomic `os.replace` idiom already in `_observe_usage`
(`bin/forge-bridge:~389/476`). Python heredoc for the critical section and
sidecar I/O; bash `cmd_infra_lock` wrapper; new case entry at the main dispatch
(`~3267–3347`).

- **`acquire --slug S --stage T [--timeout N] [--interval I]`**
  Poll loop. Each iteration takes the flock guard briefly and branches on the
  holder (see §6), then releases the guard. On wait: emit
  `LOCK action=wait held_by=<slug>` and sleep `I`. On success: print
  `INFRA_LOCK: ACQUIRED slug=… stage=… session=…`, emit `LOCK action=acquired`,
  exit 0. On ceiling: emit `LOCK action=timeout`, print a structured escalation
  block with the full holder metadata needed to judge a force-release (R2-8) —
  `held_host`, `session`, `session_id`, `session_created`, `slug`, `stage`,
  `acquired_at`, `project_root`, and the computed `liveness` verdict
  (`live`/`stale`/`foreign-host`) — and `waited=…s`; exit non-zero so the
  orchestrator surfaces it to the user.

- **`release --slug S --stage T`**
  Flock guard; remove `infra.holder` **iff** the **full owner key**
  `(host, session_id, session_created, slug)` is mine (R3-1 — must match the key
  acquire uses, else a reused-`session_id` stale holder could be wrongly
  released); emit `LOCK action=released`. Idempotent no-op (with a
  `LOCK action=release_noop` note) if not the holder. **`stage` is the only
  deliberately-omitted field** (so a stage-update holder still releases cleanly).

- **`status`** *(read-only, evaluates local liveness — F7)*
  Prints one of (verdict via `tmux_owner_live`, host-aware per R2-2/R2-3):
  - `FREE`
  - `HELD live by <slug> stage <stage> session <forge-N> ($id created <c>) since <t>` — host+id+created match a live session
  - `STALE dead-session holder by <slug> session <forge-N>` — host matches but no live session with that id+created (next acquire steals)
  - `HELD foreign-host by <slug> host <H>` — held on another host; liveness unverifiable here

  Consumed by `_render_status_file` and humans. The exact `release --force`
  command is surfaced **only** in the `STALE`/timeout guidance, with a warning
  that force-release can cause collisions if the holder is in fact still running.

- **`release --force`**
  Operator escalation path. Remove the holder unconditionally; emit
  `LOCK action=force_release`. Surfaced only in stale/timeout guidance.

### Acquire branch logic (inside the flock guard) — revised per F4/F5 + R2-2/R2-3

```
h = read(infra.holder)
if h absent                                  -> write holder(me); ACQUIRED

elif h.host != my_host                       -> NOT AVAILABLE (foreign host)        # R2-2: host checked FIRST
                                                emit LOCK action=wait reason=foreign-host; wait

elif h.host == my_host
     and h.session_id == my_session_id
     and h.session_created == my_session_created:     # genuinely THIS live session (R2-3)
        if h.slug == my_slug:
            if h.stage == my_stage           -> ALREADY_HELD                        # reentrant (F5)
            else                             -> update holder.stage/acquired_at;
                                                emit LOCK action=stage_update; ACQUIRED
        else                                 -> CONFLICT                            # same live session, different slug (F4)
                                                emit LOCK action=conflict; escalate (do NOT steal)

elif h.host == my_host
     and not tmux_owner_live(h.host, h.session, h.session_id, h.session_created)
                                             -> steal (dead session / server-restart id reuse);  # R2-3
                                                emit LOCK action=steal; ACQUIRED

else                                         -> NOT AVAILABLE (a different live local session holds it) -> wait
```

- `tmux_owner_live(host, name, id, created)` = on `host`, a live tmux session
  exists whose `#{session_id}` == `id` **and** `#{session_created}` == `created`.
  The `created` match (R2-3) is what makes a **tmux-server restart** safe: ids
  restart from low values after a restart, so a new session reusing `$0` has a
  *different* `created` and the stale holder is correctly seen as dead, not live.
- **Host is compared first** (R2-2): tmux session ids are server-local, not
  globally unique, so a foreign-host `$43` must never match a local `$43`. A
  foreign-host holder is `NOT AVAILABLE` (liveness unverifiable cross-host) →
  the waiter escalates on the ceiling rather than steal. Forge worktrees of one
  repo are same-host in practice.
- **Same live session, different slug = CONFLICT, not steal** (F4): a live
  session may still be running slug A's infra worker; auto-stealing for slug B
  would admit B to the shared stack concurrently. Surface, require operator action.

---

## 6. How the hard constraints are met

1. **Narrow scope** — lock wraps only the five infra stages; the five reasoning
   stages never call it. Not pipeline-granular.
2. **One contained exception to share-nothing** — a single mutex carrying only
   coordination metadata (who holds it, for observability). No registry, no
   allocation tables, no persisted shared state.
3. **Crash-safe via dead-session recovery** *(narrowed per F6 — not "by
   construction")* — a holder whose tmux **session identity (id + creation time)**
   is no longer live on this host is detected and stolen by the next waiter under
   the flock guard, at
   the moment it matters (contention). This covers the common failure (a killed
   worktree/session). It does **not** auto-recover: a live session whose
   orchestrator stopped after acquiring, a session parked at `PROMPTING`/
   `STALLED`, or a foreign-host holder — those surface via `status`
   (`STALE`/`HELD live`/`HELD foreign-host`) and the wait-ceiling escalation,
   and are cleared by operator `release --force` after confirming the stage is
   stopped (see §6a recovery).
4. **No false stalls** — acquire runs **before** `dispatch`, and `dispatch` is
   what writes the `response: null` pending log entry. While a pipeline waits on
   the lock, **no pending entry and no dispatched worker pane exist**, so the
   stall classifier (`STALLED` = unchanged snapshot + pending log entry,
   `bin/forge-bridge:~2881`) physically cannot fire. No `stall-check` change.
   (Verified: `cmd_stall_check` emits `IDLE`, not `STALLED`, with no pending
   dispatch, `bin/forge-bridge:2823-2831`.)
5. **Visible waits** — every action (`acquired`/`wait`/`released`/`steal`/
   `stage_update`/`conflict`/`timeout`/`force_release`) routes through
   `_emit_event` → `orchestrator-events.log`; `_render_status_file` shows an
   infra-lock line; bounded wait escalates to the user via the timeout block +
   `release --force`.
6. **Forge conventions** — Hard Constraints up front, structured stdout blocks,
   heartbeat emits, `.dev/`-scoped artifacts (lock anchor is the one necessary
   exception — it must be worktree-shared, hence the git common dir). Sits
   beside `dispatch`/`callback`, not a bolt-on.
7. **Resume-safe** — `acquire` returns `ALREADY_HELD` reentrantly when the
   holder is my own `(session_id, slug, stage)`, so `/forge resume <slug>` cannot
   deadlock against a lock it already held; a resume that advanced to the next
   infra stage updates the holder in place (`stage_update`) rather than blocking.
   `release` is idempotent, so an interrupted process's lock is either re-adopted
   on resume or stolen by another worktree when this session dies.

### 6a. Recovery for live-abandoned holders (F6)

When a holder is `HELD live` but its pipeline is abandoned (orchestrator
compacted/idle, or parked at `PROMPTING`/`STALLED`), the lock will **not**
auto-release. Operator path:
1. `forge-bridge infra-lock status` → identify holder slug/session/stage.
2. Inspect that worktree/session; if the stage should continue, resume the same
   `(slug, stage)` — it re-adopts the lock reentrantly.
3. Only if the stage is confirmed stopped or is being intentionally aborted:
   `forge-bridge infra-lock release --force` (warned: force-release while an
   infra worker is still live can cause a collision).

---

## 7. Orchestrator integration (orchestrator-side; workers untouched)

In `skills/forge-orchestrator/SKILL.md`, the lock wraps **only** the five infra
stages `{coding, qa, qa-fix, qa-retry, verify}`. The release rule is the R1
core change: **release on stage terminality, not on a single `wait` return**
(F1).

> **Terminality rule.** Release **only** when the stage reaches terminal
> completion — i.e. the worker reports `callback DONE` or `callback ERROR`
> (surfaced as `wait` `STATUS=DONE|ERROR`). **Hold** the lock through every
> non-terminal `wait` outcome — `PROMPTING`, `STALLED`, `TIMEOUT`, `DEAD`,
> `BLOCKED` — because none of them prove the worker has stopped touching infra
> (a stall verdict isn't a stopped process; a prompt may resume an infra
> command; an orchestrator unblocking a `BLOCKED` stage may itself run infra
> commands before continuing). The held lock is reclaimed later by the same
> stage's eventual terminal callback, by dead-session steal, or by operator
> `release --force` on an explicit abort.

### Shape A — dispatched infra stage

```
infra-lock acquire --slug S --stage T [--timeout N]    # blocks visibly; escalates on ceiling
dispatch           --slug S --stage T --worker W [--clear]
    └─ if dispatch FAILS (orphan-pending guard / tmux validation / cmd_send):   # F2
         infra-lock release --slug S --stage T  →  surface the dispatch error  →  STOP
loop:
  wait --slug S --stage T --worker W [--timeout M]
    DONE | ERROR         -> infra-lock release --slug S --stage T ; advance/handle   # terminal: pending closed
    PROMPTING            -> surface to user; on approval, re-enter loop (HOLD)
    BLOCKED              -> wait LEAVES the BLOCKED callback in place; pending stays open (R3-3/R4-1);
                            run repair (may touch infra under the held lock);
                            send --force continuation;
                            on send success: callback-consume --status BLOCKED ; re-enter loop  # R4-1: archive only after continuation committed
                            on send fail / crash before send: canonical BLOCKED stays → resume re-surfaces it
    STALLED | TIMEOUT    -> Agent-Failure-Recovery; re-enter loop or abort (HOLD;
                            abort path force-releases after confirming stop)
    DEAD                 -> see DEAD sub-policy below (HOLD by default)           # R2-7
```

**Callback lifecycle (required `wait`/`callback` change — R2-1 → R3-2/R3-3 →
R4).** Today `cmd_wait` returns the instant the callback file exists and never
consumes it (`:2092-2097`, `:2129-2163`); `cmd_callback` writes the callback file
*then* closes the pending for **every** status incl. `BLOCKED`, suppressing the
close failure (`:1987-1999` then `:2001-2007`). Both break hold-and-continue.
Evolution of the fix: R2's `attempt_ts`/`wait --after` was withdrawn (R3 —
second-resolution `timestamp()`, no resume durability); R3's archive-inside-`wait`
was withdrawn (R4 — deadlocks if the orchestrator dies between `wait`-returns-
`BLOCKED` and `send --force`). **Final contract — consumption is
continuation-committed, terminal publish is close-ordered:**

```text
cmd_callback BLOCKED:
  write the complete BLOCKED callback atomically to the canonical path (temp → mv)
  do NOT log-response; KEEP the dispatch pending response:null open      # R3-3
  (optional) add-note best-effort, YAML/arg-safe; its failure never blocks the write  # R4-3

cmd_wait:
  if a canonical callback exists -> emit its status/message and RETURN
  (it does NOT archive/consume — read and consume are split)             # R4-1

orchestrator on BLOCKED:                                                 # Shape A
  read the blocked message (canonical callback = source of truth)        # R4-3
  run repair while the lock stays HELD
  send --force continuation
    success -> forge-bridge callback-consume --slug S --stage T --status BLOCKED  # archive NOW  # R4-1
               then re-enter wait  (blocks for the worker's fresh callback)
    fail / orchestrator stops before send -> canonical BLOCKED stays visible -> resume re-surfaces it

cmd_callback DONE|ERROR:
  close the pending log entry FIRST (no `|| true`; a failed close aborts) # R4-2
  publish the terminal callback atomically (temp → mv) only AFTER the close succeeds

orchestrator on DONE|ERROR:
  wait returns terminal (pending already closed) -> infra-lock release -> advance
```

Why this is durable at both resume boundaries:
- **Before continuation:** the canonical `BLOCKED` callback is left in place, so
  resume re-surfaces the same blocked state — no deadlock (R4-1).
- **After continuation:** `callback-consume` has archived `BLOCKED`, so a re-`wait`
  finds an empty canonical path and blocks for the worker's next callback — no
  stale re-read, no timestamps (R3-2 intent preserved, R4-1 ordering fixed).
- **In-flight signal:** the pending stays `response:null` through the whole
  non-terminal span, so stall-check sees it (not falsely `IDLE`, `:2823-2831`)
  and `/forge status` shows the stage in-flight under the held lock (R3-3).
- **Terminal hand-off:** the pending is closed *before* the `DONE`/`ERROR`
  callback is visible, so the orchestrator can't observe terminal, release the
  lock, and race the next dispatch against an open pending (R4-2).

**R2-7 — `DEAD` sub-policy.** `DEAD` = the worker pane is gone; `dispatch` needs
a live session + correct pane count, so re-dispatch may require repair first:
- **pane dead, session repairable** → keep lock HELD, repair pane/session
  (`health`), then re-dispatch (re-acquire is reentrant).
- **session will be killed/restarted** → the lock auto-resolves via dead-session
  steal once the session dies; or force-release as part of an explicit abort.
- **repair abandoned** → `release --force` **only after** confirming no child
  service/test process from the dead stage is still bound to the shared ports.

`dispatch`-failure is the one branch that releases *without* a terminal callback
— safe precisely because the worker never started, so infra was never touched
(F2). No blanket `trap release EXIT`: that would release while a worker is still
live.

### Shape B — local fallback infra stage (F3)

`qa` Path B and `qa-retry` run `adversarial-qa` **inline in pane 1** (Agent
Teams) when codex-b is unavailable — there is no `dispatch`/`wait` to key off.
Per Rule 5 the local stage must also be **logged and closed**, or the
`dispatch` guard refuses the next stage (R2-6):

```
infra-lock acquire --slug S --stage T [--timeout N]
log         --slug S --stage T --from claude --to claude --prompt "<local qa run>"   # R2-6 open pending
restart-on-entry (qa): bring up THIS worktree's services (R3-5/R3-7)                 # under the held lock
run adversarial-qa inline (pane 1)                              # lock HELD
  ├─ success: log-response --to claude --response "FORGE_DONE: ..."   # R2-6 close
  └─ FAILURE/ABORT (R3-6): log-response --to claude --response "FORGE_ERROR|AGENT_FAILED: ..."   # close anyway
infra-lock release --slug S --stage T   # release as soon as artifacts written + pending CLOSED (R3-8)
                                        #   (failure path: release/force-release only after confirming no unsafe svc proc)
forge-bridge digest --slug S --stage T --template qa ; consume digest   # AFTER release — digest reads disk only (Rule 10)
```

Two R3 corrections vs the prior Shape B:
- **Close the local pending on *every* exit (R3-6).** Inline QA that errors or
  aborts before the close would otherwise leak *both* the lock *and* an open
  pending (the next `dispatch` guard refuses open pendings). The failure branch
  closes with `FORGE_ERROR`/`AGENT_FAILED` and only then releases (force-release
  only after confirming no service/test process is still unsafe).
- **Release before the digest (R3-8).** The digest agent reads disk artifacts,
  not the live stack (Rule 10), so it needs no infra lock. Releasing right after
  the local entry closes — and running the digest afterward — matches Shape A's
  effective lifecycle and keeps lock-hold time to the actual infra work.

Release is orchestrator-driven on inline completion (no worker callback); the
lock brackets `restart-on-entry → inline run → log-response close`, nothing more.

### Hard Rule 22 reconciliation (R2-4) — required, not just docs

Rule 22 today states proposal "is the ONLY stage that executes locally" and
"All other stage work is forbidden in pane 1" (`SKILL.md:1048-1067`) — which the
existing local QA fallback already contradicts, and which Shape B depends on.
**Decision D2 — RESOLVED = keep + amend:** amend Rule 22 to name **local
`qa`/`qa-retry` fallback** as a second pane-1 exception *alongside proposal*,
explicitly gated on `(codex-b unavailable) AND (held infra lock)`. Update Worker
Selection + Stage Details so local QA is allowed only under that condition.
Shape B is retained. (Rejected alternative: drop the fallback / require codex-b
dispatch — would have removed resilience when codex-b is down.)

Ship an acceptance check that greps for the contradiction pair
("ONLY stage that executes locally" vs "qa local fallback") so the two rules
can't silently re-diverge.

New **Hard Rule 23** documents the lock, both shapes, and that it may
**intentionally remain held** while an infra stage is prompting, stalled, timed
out, dead-pending-redispatch, or being unblocked. The five reasoning stages are
explicitly out of its scope.

---

## 8. Files touched

Per the established edit protocol (documented in prior handoffs):
**timestamped backup → edit repo copy → mirror to the installed runtime path →
`git diff` review → NO auto-commit.** Repo↔installed parity is an
**implementation-time preflight** (R3 minor): `bin/forge-bridge`↔`~/bin` were
byte-identical at planning time but must be re-checked before editing; the
**skills are NOT all in sync** (see item 5).

1. **`bin/forge-bridge`** → mirror to `~/bin/forge-bridge` —
   - `cmd_infra_lock` + helpers (`tmux_owner_live(host,name,id,created)`, sidecar
     I/O via the existing `fcntl.flock`/`os.replace` idiom); main-dispatch case
     entry; infra-lock line in `_render_status_file` (`1410–1598`); `--help`.
   - **`cmd_wait`** (R4-1): read-only — emit the canonical callback's
     status/message and return; **do not archive/consume** (read and consume are
     split). (No `--after`/`attempt_ts` — withdrawn R3; no archive-inside-wait —
     withdrawn R4.)
   - **`callback-consume` (new subcommand, R4-1):** `--slug --stage --status BLOCKED`
     → atomically `mv` the canonical non-terminal callback to
     `…/callbacks/archive/{slug}-{stage}.{callback_id}.callback` (`mkdir -p`,
     contents preserved byte-for-byte, same-filesystem rename). If no canonical
     `BLOCKED` is present → **structured no-op** (so the orchestrator doesn't
     re-wait blindly, R4-4). Called by the orchestrator only **after** `send --force`
     succeeds.
   - **`cmd_callback` — non-terminal `BLOCKED`** (R3-3/R4-3): write the callback
     atomically (temp → `mv`); do **not** `cmd_log_response`; leave the pending
     `response:null` open. The canonical callback is the message source of truth;
     `add-note` is best-effort only (YAML/arg-safe; failure never blocks the write).
   - **`cmd_callback` — terminal `DONE`/`ERROR`** (R4-2, **highest blast radius —
     used by every stage**): close the pending log entry **first** (drop the
     `|| true`; a failed close aborts and does not publish), **then** publish the
     terminal callback atomically (temp → `mv`). Ship with a `forge-bridge` backup
     and the `DONE`-publish-race test (§9) before any other edit.
   - *These are the changes to existing `wait`/`callback` logic; the pre-R2
     "no change to dispatch/callback/stall-check" claim is retired.*
   - **`LOCK` events stay internal** to `cmd_infra_lock` via `_emit_event`
     (bypasses the `cmd_emit` whitelist) — no `cmd_emit` whitelist change (F9).
2. **`skills/forge-orchestrator/SKILL.md`** → mirror to the installed orchestrator
   SKILL — wrap infra stages (Shapes A/B); **amend Hard Rule 22** (name local QA
   fallback a pane-1 exception, D2); add **Hard Rule 23**; Shape B local
   log/close lifecycle + failure branch (R2-6/R3-6); the anti-divergence grep.
3. **`docs/forge-technical-reference.md`** — Hard Rule 22 amendment + Hard Rule
   23, `infra-lock` contract, callback-archive + non-terminal-pending semantics,
   lock-file layout, env vars (`FORGE_INFRA_LOCK_TIMEOUT_S`,
   `FORGE_INFRA_LOCK_INTERVAL_S`).
4. **`docs/forge-operator-guide.md`** — multi-worktree concurrency, lock-wait
   escalation/recovery, resume-safety, live-abandoned-holder recovery.
5. **Restart-on-entry skills (D1) — edit repo AND mirror to the INSTALLED paths
   workers actually read (R3-4):**
   - `qa`/`qa-retry` workers read **`~/.codex/skills/adversarial-qa/SKILL.md`**
     (currently **differs** from repo — must mirror, not just edit repo).
   - `verify` reads **`~/.claude/skills/adversarial-verify/SKILL.md`**.
   - (coding reads `~/.claude/skills/forge-coder/SKILL.md` — **not edited**; see
     R3-5: coding keeps its no-service-management constraint.)
   - Edit repo skill → mirror to the installed path(s) → verify by hash/grep in
     **both**. Acceptance check fails if restart-on-entry text is repo-only.
   - The restart-on-entry step itself (config-driven, identity-checked port
     handling) is specified in §11. The lock stays a pure mutex — no service
     management in `cmd_infra_lock`.

**No change** to `~/.config/forge/prompts/*.txt` — the lock and the local-stage
lifecycle live orchestrator-side; restart-on-entry lives in the QA/verify SKILL
bodies (installed paths), not the dispatch prompts.

---

## 9. Verification

Scratchpad harness against a temp git common dir, using **real temporary tmux
sessions** — `tmux new-session -d -s forge-lock-test-A` / `-B`, capturing each
`#{session_id}`/`#{session_created}` (R2-9: no `FORGE_TMUX_LIVENESS_CMD`
env-eval seam — it was dropped to avoid an arbitrary-shell-eval surface in
production). Liveness must be *real*, or a held lock looks dead and the blocking
tests are meaningless (F8).

Cases (✱ = F-series; ★ = added per R2):

- **Mutual exclusion (live holder)** — A (live session) acquires `qa`; B
  `acquire` **blocks** (not steal); A releases; B proceeds.
- **Steal on dead session** — A acquires; kill A's session; B `acquire` steals.
- **Reentrancy** — A re-`acquire` same `(slug, qa)` → `ALREADY_HELD`.
- ✱ **Stage-aware reentrancy (F5)** — A holds `(slug, qa)`; `acquire (slug, verify)`
  → holder updated, `LOCK action=stage_update`.
- ✱ **Same live session, different slug (F4)** — `(slugA, qa)` held; same live
  session acquires `slugB` → **CONFLICT**, **no steal**.
- ★ **Foreign-host colliding session id (R2-2)** — sidecar `host=foreign` with a
  `session_id` that matches a *local* live session → acquire **waits/escalates**,
  release **no-ops** (must NOT treat foreign holder as mine).
- ★ **Server-restart id reuse (R2-3)** — sidecar `session_id` matches a live
  session but `session_created` differs → verdict **STALE**, acquire **steals**
  (not `ALREADY_HELD`/`CONFLICT`).
- ▲ **Release full-key match (R3-1)** — sidecar with same `host`/`session_id`/
  `slug` but **different `session_created`** → ordinary `release` **no-ops**
  (while acquire classifies STALE and steals under the guard).
- ▲ **BLOCKED keeps pending open (R3-3)** — after `BLOCKED→send --force`, a
  `response: null` pending **still exists** for `(slug, stage)`, so `stall-check`
  can classify the in-flight worker (not falsely `IDLE`) and `/forge status`
  shows the stage in-flight under a held lock.
- ◆ **Read/consume split — resume BEFORE continuation (R4-1)** — stage returns
  `BLOCKED`; orchestrator **restarts before** repair/`send --force`; resume must
  **re-surface the same `BLOCKED`** (canonical callback still present), not block
  forever.
- ◆ **Consume after continuation (R4-1)** — `BLOCKED` → repair → `send --force`
  succeeds → `callback-consume` archives it; re-`wait` blocks for the worker's
  **fresh** callback; the worker's next callback **within the same second** is
  observed (no timestamp suppression); lock released only on the eventual `DONE`.
- ◆ **Continuation fails (R4-1)** — `send --force` fails (or `callback-consume`
  finds no canonical `BLOCKED`): canonical `BLOCKED` remains **visible** for
  retry; structured no-op, not a blind re-wait.
- ◆ **Terminal publish ordering (R4-2)** — `wait` polls aggressively while
  `cmd_callback DONE` runs: `wait` must **not** return `DONE` until the pending
  log entry is closed; a failed terminal `log-response` must **not** publish a
  successful terminal callback.
- ★ **Local fallback log/lock (R2-6/F3)** — Shape B: assert the local entry is
  **opened and closed** (`log` → `log-response`), the lock is held across the
  close, and the next dispatch is not blocked by an open pending after release.
- ▲ **Shape B failure cleanup (R3-6)** — inline QA fails/aborts *after* `log`:
  assert the local entry is **closed** (`FORGE_ERROR`/`AGENT_FAILED`) **and** the
  lock released, so the next dispatch is not refused by an open pending.
- ▲ **Shape B releases before digest (R3-8)** — assert the lock is **released**
  before the digest agent runs (digest needs no infra lock).
- ★ **DEAD repair (R2-7)** — `wait` returns `DEAD`; re-dispatch fails on pane
  validation; assert the recovery path does **not** leak a live holder
  indefinitely (repair-then-redispatch under hold, or steal/force on abort).
- ✱ **Dispatch failure releases (F2)** — acquire ok; dispatch fails (pre-existing
  `response: null`) → lock **released**.
- ✱ **Non-terminal holds (F1)** — `PROMPTING`/`STALLED`/`TIMEOUT` → lock **held**;
  only `DONE`/`ERROR` releases.
- **Idempotent + force release** — `release` when not holder = no-op;
  `release --force` clears a foreign holder.
- **Ceiling escalation** — `acquire --timeout 2` vs a live held lock →
  `INFRA_LOCK: TIMEOUT` block (full holder metadata, R2-8) + non-zero exit.
- ✱/★ **Corrupt + injection-safe sidecar (F8/R2-9)** — malformed `infra.holder`
  → safe escalation, not a traceback; and adversarial `session_id`/sidecar values
  cannot inject shell syntax into the lock script.
- **No false stall** — no pending log entry exists during a lock wait (so
  `stall-check` cannot classify STALLED).
- ▲ **Restart-on-entry, qa/verify (R3-5)** — start a server from worktree A;
  acquire from B; B's qa restarts services against B → assert B's requests hit
  **B's** worktree code.
- ▲ **Unknown process on a configured port (R3-7)** — occupy a configured port
  with a non-Forge process → restart-on-entry **escalates**, does **not** kill it.
- ▲ **Stage-specific restart policy (R3-5)** — assert restart-on-entry text is
  present for qa/qa-retry/verify and **absent** for coding/qa-fix; forge-coder's
  "never start/stop services" constraint is intact.
- ▲ **Installed-path deployment (R3-4)** — assert restart-on-entry text is in the
  **installed** runtime skills (`~/.codex/skills/adversarial-qa/SKILL.md`,
  `~/.claude/skills/adversarial-verify/SKILL.md`), not just repo `skills/`.

Final manual check: two real worktrees running concurrent pipelines — reasoning
stages overlap; infra stages never overlap on the shared DB/ports.

---

## 10. Out of scope (v1) / clean revert

- No per-worktree DB or port isolation; no pool, allocator, or lease registry.
- No scheduler/queue/priorities — this is a lock, not a coordinator.
- No serializing whole pipelines — only the infra-touching stages.
- Removal is a clean revert: delete `cmd_infra_lock` + the orchestrator wrap +
  doc edits and `rm -rf <git-common-dir>/forge-infra-lock/`. No schema, no
  tracked artifacts, no migration.

---

## 11. Service teardown / staleness — restart-on-entry (F10 lead finding; D1 resolved)

A check the R1 review did not cover, escalated by R2 (R2-5) and resolved as
Decision D1 = **restart-on-entry** (in scope for v1). Background: **infra stages
do not tear down the services they start.** `adversarial-qa` starts servers
backgrounded
(`{{backend_command}} &` / `{{frontend_command}} &`,
`skills/adversarial-qa/SKILL.md:523-524`) and reuses-if-already-running
(`:206`); the only shutdown in the skill targets Agent-Teams teammates
(`:361-364`), not services. So when a holder reaches terminal completion and the
lock releases, a dev-server it started **stays up**.

Implications for v1:

- **What the lock *does* deliver, soundly:** DB / test-state serialization (only
  one infra stage mutates the shared Postgres at a time) and elimination of the
  concurrent **port-bind race** (two stages never call service-start
  simultaneously). These are the collisions the problem statement names ("same
  DB, … interleaved test state").
- **What the lock alone does *not* solve (closed by D1 below):** a left-running
  backend from worktree A serves **A's code** to worktree B's next infra stage,
  because qa "reuse-if-running" (`:206`) adopts A's server instead of starting
  B's. The mutex sequences the stages but does not make the *running server*
  reflect the *current* holder's worktree. **D1 = restart-on-entry closes this
  for the app-exercising stages (qa/qa-retry/verify)**; coding/qa-fix carry the
  documented residuals below. (Full per-worktree service/DB *isolation* remains
  the deferred track; restart-on-entry is the in-scope v1 answer to staleness.)

### Decision D1 (R2-5) — RESOLVED = restart-on-entry, made stage-specific (R3-5)

The user chose restart-on-entry (2026-06-29). R3-5 then showed that a blanket
"each infra stage restarts services" both **conflicts** with forge-coder's hard
constraint ("Never start/stop long-running services directly",
`skills/forge-coder/SKILL.md:11-18`) and **over-applies** to `qa-fix` (whose
prompt runs no live tests). So D1 is **stage-specific**:

| Stage | Restart-on-entry? | Rationale |
|---|---|---|
| **qa, qa-retry, verify** | **Yes — required** | Skills explicitly exercise a *running app*; this is exactly where B-reuses-A's-server bites. Restart against this worktree before testing. |
| **coding** | **No** — unchanged | Keeps the forge-coder constraint; coding's tests self-start via Playwright `webServer` autostart from coding's own `working_dir`. *Residual:* if a project's Playwright sets `reuseExistingServer: true`, coding could still adopt a prior holder's server — mitigation: recommend `reuseExistingServer: false` (or an equivalent forge-project setting) so coding tests always start fresh. Documented, not silently assumed. |
| **qa-fix** | **No** by default | Prompt resolves findings + writes a report; no live tests. Restart only if a given qa-fix is configured to run live/e2e checks. |

So the guarantee, precisely stated: **for qa/qa-retry/verify, each stage tests
its own worktree's code (restart-on-entry); coding tests its own code via
Playwright autostart subject to `reuseExistingServer`; qa-fix runs no live app.**
Not the unqualified "every infra stage tests its own code."

**Safe port-process selection (R3-7).** "Kill whatever occupies the port" is
unsafe on a shared dev machine. The restart step must:
- read the expected backend/frontend **ports + working_dir + command** from
  `.claude/forge-project.yml`;
- stop a process on a configured port **only if** its command/cwd matches the
  project's expected dev-server shape (i.e. a Forge-owned service for *some*
  worktree of this repo);
- if a configured port is held by an **unknown** process (not the expected
  shape), **escalate** with a structured message — do **not** kill it;
- **log** what was stopped: pid, command, cwd, port, and inferred owning worktree.

- Verification: D1 tests in §9 (start A's server; acquire from B; B restarts and
  its requests hit B's code) + the unknown-process-on-port escalation case.

(Rejected alternative: ship the lock alone and downgrade the guarantee.) Honest
framing holds: **the lock serializes infra access; the restart-on-entry SKILL
step — not the mutex — manages service lifecycle, and only for the app-exercising
stages.**
