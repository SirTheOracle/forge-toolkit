# Handoff — `worker-context-hygiene` implementation.md review loop (NEXT)

**Date:** 2026-07-23. **Author:** team-lead (Fable 5).
**For:** a fresh session (possibly a smaller model) continuing the review loop. **Context can be
cleared before reading this** — everything needed is below or linked. **Do not re-plan, do not
re-synthesize, do not re-litigate settled dispositions.** Your job is narrow: run the
review→reconcile→verify loop until a review round comes back clean, then hand to the coding stage.

---

## 1. What this is

`worker-context-hygiene` adds enforced context hygiene for the four forge worker panes
(claude-opus/0, codex-a/2, codex-b/3, claude-sonnet/4): threshold-gated reuse
(`FORGE_WORKER_MIN_HEADROOM=75`, `<=` resets), semantically-proven `/clear` resets at safe
boundaries only, crash-conservative delivery generations, a verify-decision → finalize terminal
flow with a durable outbox, and observe→enforce rollout. Authoritative spec:
`.dev/proposals/worker-context-hygiene/final-plan.md` (12 steps + testing strategy). The
implementation document translating it into exact diffs is
`.dev/proposals/worker-context-hygiene/implementation.md` — currently **Revision R3**.

## 2. What is DONE (do not redo any of this)

- **Full 4-round adversarial-implementation (2026-07-23):** isolated Surgical A / Coverage B →
  synthesis C → both critiqued C from original contexts → C reconciled. That produced R1.
  Deliberation trail (impl-A/B/C, impl-notes-A/B, review-for-A/B, impl-feedback-A/B) is all in
  `.dev/proposals/worker-context-hygiene/`.
- **Review round 1** (`implementation-review-feedback.md`, 9 P0 / 9 P1 / 14 P2) → **R2** +
  `implementation-review-disposition.md` (28 accept / 2 partial / 2 reject-with-evidence).
- **Review round 2** (`implementation-review-feedback-r2.md`, 5 P0 / 8 P1 / 4 P2) → **R3** +
  `implementation-review-disposition-r2.md` (14 accept / 3 partial / 0 reject).
- **R3's headline fixes were verified at the call-site level by the lead** (not just claimed):
  fd-7 `_terminal_lock` acquired first in verify-decision (~1630), park-resolve handoff (~1711),
  finalize (~1831), hygiene-abandon (~2004); `trap _hygiene_release_all RETURN` at all 5 critical
  sections; `_hygiene_journal_preflight` before any `/clear` (~1187, ~1835);
  `awaiting-verify-decision` blocks sends on both paths (~929, ~1353); installer seed uses
  `$SCRIPT_DIR` (not `$REPO_ROOT`); `_hygiene_crash_at delivered` call sites exist (~949, ~1034).
- **NO production code has been changed.** Everything so far is documents under
  `.dev/proposals/worker-context-hygiene/`. `bin/forge-bridge` etc. are untouched.

## 3. Current gate

**R3 is NOT cleared for coding.** The Definition of Done in implementation.md gates on an
independent re-review of R3. The operator runs that review in a separate channel and drops the
result in as `.dev/proposals/worker-context-hygiene/implementation-review-feedback-r3.md`
(naming follows the previous two rounds). Wait for the operator to provide it — do not write a
review yourself and treat it as independent.

## 4. The loop procedure (repeat per review round N)

When `implementation-review-feedback-r<N>.md` appears:

1. **Read the whole review file.** Findings are numbered P0-x / P1-x / P2-x with evidence lines
   into implementation.md and the live `bin/forge-bridge`.
2. **Spot-check 3 findings yourself before anything else** — pick mechanically checkable ones
   (a variable name, a missing call site, a schema field) and grep both implementation.md and the
   live sources. Track record so far: the reviewer is **9-for-9** on lead spot-checks across two
   rounds. If your spot-checks confirm, treat remaining findings as presumptively valid.
3. **Reconcile and revise.** The original teammate agents from the old session are unreachable —
   spawn ONE fresh agent (general-purpose) with: the review file, implementation.md, the two
   existing disposition files (so settled items aren't re-opened), and instructions to
   (a) verify every finding at the call site, (b) disposition each Accept / Partially accept /
   Reject-with-evidence (rejections need a concrete trace), (c) revise implementation.md in place,
   bump the header revision (R3→R4→…) naming the review file it incorporates, (d) write
   `implementation-review-disposition-r<N>.md` with one row per finding: verdict, evidence, what
   changed, new line refs, (e) `bash -n` every rewritten function body and `py_compile` every
   changed Python heredoc. If the review is small (only P2s), you may do this inline instead of
   spawning — same steps.
4. **Verify the revision yourself at the call-site level.** THE most important lesson of this
   effort: **R2 claimed a terminal-serialization fix that never landed** (revalidation was added
   under the worker locks, but the racing mutators hold the lifecycle lock, which worker locks
   don't exclude). Never accept "fixed" from the reconciling agent — grep the claimed helper /
   guard / call site in implementation.md and confirm it exists where claimed. Useful pattern:
   `grep -n "<new_helper_name>" implementation.md` and read the surrounding block.
5. **Update memory** (`~/.claude/projects/-Users-sirdrafton-sirtheoracle-automation-forge-toolkit/memory/worker-context-hygiene-impl-status.md`
   + its MEMORY.md index line) with the round outcome and the NOT-ready-for-coder status.
6. **Report to the operator**: accept/partial/reject counts, what you spot-checked, anything the
   reconciler rejected, and that the next independent review is awaited.

**Loop exit:** a review round that raises no P0 and no P1 (P2-only or clean). Then proceed to §6.

## 5. Settled decisions — do NOT re-open (each was adversarially argued and traced)

1. **Round-1 P0-1 "reorder" REJECTED:** reset coverage intentionally covers the PRE-delivery
   generation. A dispatch's delivery MUST invalidate its own reset (the pane then runs a stage and
   is dirty again). Finalize-retry reuse works because finalize's reset has no delivery after it
   (covers == latest → KEEP_RESET_PROVEN). Do not "fix" this ordering.
2. **Round-1 P0-2 "deadlock" REJECTED:** the journal lock (fd 8) is a LEAF — acquired and released
   entirely inside `_hygiene_write`, never held while acquiring another lock — so no cycle exists.
3. **Lock order (global, R3):** terminal(fd 7) > worker(fds 10–13, canonical order) >
   lifecycle(fd 9) > journal(fd 8, leaf). Fixed-fd worker locks were EMPIRICALLY verified working
   under this Mac's bash 3.2.57; a subprocess-holder alternative deadlocked. Keep fixed fds.
4. **Reset proof:** visible-screen-only capture (`capture-pane -p`, no `-S`); proof = provider
   session-id change OR (anchor newly present AND absent at baseline AND fingerprint changed);
   idle-alone is NEVER success. The earlier line-index model was a confirmed bug — don't return to it.
5. **Mode table:** observe = legacy completion + audit-only (`HYGIENE_BYPASSED`, `OBSERVE_ONLY`),
   never persists new terminal vocabulary; enforce owns the new states. Enforce-only commands are
   mode-gated. Bare `COMPLETE` requires four proven resets + published outbox completion ID;
   hygiene-degraded gives only `COMPLETE qualifier=hygiene-disabled`.
6. **Three R3 partials — the first things the next reviewer will poke at** (context in
   `implementation-review-disposition-r2.md`):
   - P1-3: incarnation filtering in shared `_completion_unresolved` / `_unresolved_blocked_items`
     is SPECIFIED as a bounded diff but deferred (shared lifecycle code, global-edit protocol);
     over-counting fails safe (refuses).
   - P1-6: a callback that cannot write the journal after lock-timeout escalates loudly; it cannot
     guarantee invalidation on an unwritable disk. Known residual.
   - P2-2: supersede post-close crash window (reset+close done, replacement unsent) is documented;
     recovery = existing orphan-log detection; no new transaction state.

## 6. After the loop exits (clean review)

1. Update the memory status file to CLEARED-FOR-CODER.
2. Coding goes through the pipeline, never ad-hoc (operator rule: fixes/implementation via forge
   or forge-fix pipeline; seat dispatches are analysis-only). The intended stage is
   **forge-coder against implementation.md** in forge-toolkit. Ask the operator before dispatching
   anything — they gate all outward actions (standing seat-operator rule).
3. Remind the operator of the plan's own rollout gates before enforce mode: Step 0 reset-proof
   spike on disposable panes is the implementation critical path; hermetic suites + disposable
   live-session gate before `FORGE_WORKER_HYGIENE_MODE=enforce`.

## 7. Verification snippets (copy-paste)

```bash
cd /Users/sirdrafton/sirtheoracle/automation/forge-toolkit/.dev/proposals/worker-context-hygiene
head -6 implementation.md                          # header must name current revision + review file
grep -cE '\| *GAP' implementation.md               # 0 expected
grep -cE '^\| *P[012]-' implementation-review-disposition-r2.md   # 17 (round-2); new rounds analogous
grep -n "_terminal_lock\|_hygiene_release_all\|_hygiene_journal_preflight" implementation.md | head
bash -n /Users/sirdrafton/sirtheoracle/automation/forge-toolkit/bin/forge-bridge   # live bridge untouched + parseable
```

## 8. File map (all in `.dev/proposals/worker-context-hygiene/`)

| File | Role |
|---|---|
| `final-plan.md` | Authoritative plan (R1 of the PLAN — distinct from implementation revisions) |
| `problem-statement.md` | Scope boundary |
| `implementation.md` | **The deliverable** — Revision R3 |
| `implementation-review-feedback.md` | Round-1 independent review (of impl R1) |
| `implementation-review-disposition.md` | Round-1 dispositions (28/2/2) |
| `implementation-review-feedback-r2.md` | Round-2 independent review (of impl R2) |
| `implementation-review-disposition-r2.md` | Round-2 dispositions (14/3/0) |
| `impl-A/B/C.md`, `impl-notes-*`, `review-for-*`, `impl-feedback-*` | Adversarial deliberation trail (read-only history) |
| `proposal-*.md`, `plan-review-r*.md`, `execution-method.md` | Plan-phase history (read-only) |

Memory files already track this status: `worker-context-hygiene-impl-status.md` (+ MEMORY.md index).
Handoffs directory is git-tracked; this file should be committed by the operator with their normal flow
(do not commit anything without an explicit go — standing rule).
