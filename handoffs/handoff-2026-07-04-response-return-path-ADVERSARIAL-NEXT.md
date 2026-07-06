# Handoff: Seat→Worker RESPONSE RETURN PATH is missing — run adversarial-proposal

**Date:** 2026-07-04
**From:** first real-use shakedown session (follows `handoff-2026-07-03-command-center-phaseC-LIVE-next-steps.md`)
**Task for THIS session:** run the FULL `adversarial-proposal` skill on the problem below.
Investigation is DONE (evidence in §2 — do not re-derive it from scratch; verify, then build on it).
Deliverable: vetted final-plan.md for the response return path. Do NOT implement before the plan is approved.

## 1. Why this exists (operator verdict)

First real seat-driven request worked one-way only: dispatch delivered, worker did the
work and answered — and the seat received NOTHING back. Operator's words: "a one-way
post with nothing in response … not acceptable as far as user experience." This is the
top blocker for Command Center v2 being usable for real work. Everything else (spawn,
registry, board, asks) shipped and works.

## 2. Investigation findings (verified 2026-07-04, evidence on disk)

Live trace of the failing round trip (root `~/sirtheoracle/automation/headless_factory`,
dispatch id `cc-20260704T160236Z-4b7e0d`, files under `.dev/attention/`):

1. 16:02:36 dispatch sent from seat → `dispatch-cc-20260704T160236Z-4b7e0d.json`.
2. 16:02:37 forge-1 accepted (`prompt.forge-1.json`, `variant: dispatch-accept`,
   dispatch_id matches). Outbound path + correlation: WORKING.
3. 16:03:39 forge-1 finished (`stop.forge-1.json`). Three return-path failures:
   - **G1 — full answer discarded at capture.** The Stop hook HAS the entire final
     message (`last_assistant_message`) and keeps only the last 400 chars:
     `bin/forge-cc-hook:133` → `snip(body[-400:], 400)`. (Also why board snippets
     start mid-word.) Full text now exists only in tmux scrollback.
   - **G2 — no retrieval channel.** No verb joins stop↔dispatch for the operator
     (no `forge reply`, no `dispatch --wait`) even though dispatch_id correlation
     exists end-to-end. Board shows ~80 chars of the 400-char tail on a `done` row,
     poll-only; SESSION-DONE is policy `never` (no notification, bell discipline).
   - **G3 — worker's counter-question dies silently.** forge-1's answer ENDED IN A
     QUESTION back to the operator; hook flagged it (`looks_like_question: true`) and
     16:04:39 an idle `notify.forge-1.json` ("waiting for your input") followed.
     NOTHING consumes either signal. forge-1 sat waiting; the seat had no way to know.
     (`forge ask` only fires when a worker explicitly invokes it — a plain
     conversational reply-with-question has no path to the board.)

**Root cause (design-level):** v2 treated the return path as *state signaling*
(lifecycle rows + explicit escalations), not *message transport*. Dispatch is a real
message system; the reply is a status LED.

## 3. Draft plan shape — INPUT to the adversarial run, not binding

- **P1 transport:** Stop hook persists full `last_assistant_message` as a payload file
  keyed by dispatch_id (payloads/ pointer-file pattern already exists for dispatches);
  additive response event. New `forge reply [@session|<dispatch-id>]` prints the full
  response; `forge dispatch --wait [--timeout N]` polls until the correlated stop lands
  and prints it inline (ask→answer in one command).
- **P2 conversational loop:** response-to-a-seat-dispatch with `looks_like_question`
  (and/or trailing idle_prompt) → hot `NEEDS-REPLY` board row with paste-ready reply
  command. Distinct from NEEDS-ASK. Noise guard: gate on correlated dispatch + recency.
- **P3 push (policy decision for operator):** macOS notification on response-to-seat
  dispatch vs. current bell discipline (only blockers ring). Also: seat-skill behavior —
  when the seat agent dispatches for the operator it should use --wait and relay.

Open questions the plan must answer: --wait default or opt-in; response payload GC
(7d attention GC covers it?); NEEDS-REPLY false-positive rate (every done row ending
in "?"); how `reply` picks "latest" when multiple dispatches are in flight; hook write
size limits / redaction for full-message payloads (snip() currently redacts — the full
payload must too); does queued-dispatch absorption (mid-turn) still correlate the right
stop to the right dispatch for --wait.

## 4. Constraints (carry-forward, must not violate)

- Everything in the Phase A/B/C handoff constraint lists still holds. Key ones here:
  hooks fail-open; forge-watch stays READ-ONLY (renders/detects, never writes);
  `cc-*/1` schemas additive-only; `--answers` owns ask/callback consumption;
  attention GC = 7d/-maxdepth 2; bell discipline (policy `never` on lifecycle rows)
  changes only as a DELIBERATE operator-approved decision; bridge verbs untouched.
- Redaction: any persisted full response MUST pass the same redact() the snippets use.
- The seat never scrapes panes as the primary mechanism (capture-pane is the fallback
  that this plan exists to eliminate).

## 5. Session state at handoff (IMPORTANT — uncommitted work in tree)

Working tree on `main` (at `afa1e3b`) holds TWO verified-but-uncommitted changes,
per the no-auto-commit protocol (operator reviews + commits):

1. **Registry shape-check hardening** — `registry_write` refuses non-`cc-registry/1`
   files + non-dict repos entries (was: adopted any YAML dict). Tests T3b/T3c added.
2. **Human board** — `forge board`/`forge status` now render a pretty board (NEEDS
   YOU / SESSIONS table / PIPELINES / maintenance collapsed; `--all` expands;
   NEEDS-ASK rows print a paste-ready `--answers` command). JSON contract moved to
   `forge board --json` (+ legacy `forge-watch status --board`, byte-unchanged).
   Raw findings remain at `forge-watch status`. Engine: FORGE_WATCH_BOARD=2 pretty,
   FORGE_WATCH_SHOWALL=1. Seat skill updated in BOTH copies (installed + toolkit,
   byte-identical).

Suites all green: forge-watch **98** (88+10 pretty), forge-cc **57**, spawn **31**
(29+2 registry), forge-start 22, infra-lock 63. `install.sh --check-drift`: zero.
Bins are symlinks → changes already LIVE. Recommend committing both before the
adversarial run (two commits: `fix(forge): registry adopts only cc-registry/1 files`,
`feat(board): human-first forge board; JSON via --json`).

## 6. Loose ends

- forge-1 is still holding its unanswered counter-question from the trace above
  ("map user_edits_present to a friendly message + overwrite button?") — harmless
  idle; either answer it via dispatch or dispatch a stand-down when convenient.
  It is ALSO a real product-UX suggestion for headless_factory worth triaging.
- Known snippet wart (subsumed by P1): tail-truncation makes board snippets start
  mid-word.
- Prior remaining items (unchanged): two-worktree infra-lock pipeline-level run;
  rest of the real-use shakedown (ask path self-verify, new-project spawn).
