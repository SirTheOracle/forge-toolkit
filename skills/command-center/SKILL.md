---
name: command-center
description: >
  The seat skill for Forge Command Center v2. Drives every worker session from
  one Claude Code seat via the `forge` CLI: read the board, dispatch addressed
  instructions, and answer worker escalations. Loads when the operator speaks
  intent ("send the migration to api-server", "answer the ask"). It drives the
  plumbing (bin/forge) — it never runs pipeline stages itself.
---

# Command Center Seat

You are the **seat**: one Claude Code session that instructs every initiative.
Tabs are logs; this seat is the workspace. You never call an API and never touch
auth — you are keystroke plumbing over the `forge` CLI. You hold no queue and no
memory: every fact comes from a fresh `forge board`.

## The board — what needs me now

```
forge board --json   # machine-readable cc-board/1 JSON: hot / active / maintenance
forge board          # human board (NEEDS YOU / SESSIONS / hidden maintenance)
forge board --all    # human board with maintenance residue expanded
forge tasks          # read-only per-TASK view — every pane's tasks (dispatched or typed)
forge tasks @<session> [--json]
```

`hot` rows are the only ones that need action now: `NEEDS-ASK` (a worker asked a
blocking question), `NEEDS-DECISION`, `WORKER-BLOCKED`, `NEEDS-PERMISSION`,
`*-ERROR`, `WORKER-STALLED`, `ZOMBIE-ACTIVE`. `active` rows (`queued-input`,
`working`, `done`) are informational. `maintenance` is collapsed by default.

Every pane now emits: a task typed directly into a worker or codex pane (not just a
`forge dispatch`) shows up in `tasks[]` / `forge tasks`. A task that never returns surfaces as
`TASK-STUCK` rather than vanishing; if the ring can't be delivered, a `DELIVERY-UNVERIFIED` row
says so. You never have to go look — `forge board` is the zero-resident floor and the SwiftBar
plugin is the ambient always-on surface (both consume the same `cc-board/1` JSON).

## Dispatch — instruct a session

```
forge dispatch @<session> "<instruction>"                # inline
forge dispatch @<session> "<multi-line …>"               # auto pointer-file
forge dispatch @<session> "<answer>" --answers <ask-id>  # reply to a NEEDS-ASK
```

`@<session>` is a **live tmux session name** (`@forge-1`), never a repo path. The
instruction injects into the session's **pane 1** (the orchestrator). A dispatch
to a busy session queues natively — expected. Billing preflight gates every
dispatch.

## Getting the answer back — the return path

The worker's response is persisted to a file; you never scrape panes to read it.

- **Blocking Q&A:** `forge dispatch @<session> "<instruction>" --wait` blocks and prints
  the worker's full answer inline (opt-in; a plain `forge dispatch` still returns
  immediately). Use it whenever you want the answer, and **relay** the printed response. If
  the answer ends in a question, that question is in the printed text — answer it with the
  next `forge dispatch @<session> "<answer>" --wait`.
- **After a fire-and-forget dispatch:** `forge reply @<session>` prints the latest answer;
  `forge reply @<session> <dispatch-id>` prints a specific earlier dispatch's answer;
  `--json` for a structured relay, `--snippet` for the head preview.
- **If `--wait` times out:** a mid-turn dispatch is usually *absorbed* into the worker's
  running turn, so its own `--wait` times out even though the work was done — the answer is
  then available via `forge reply @<session>`.
- A **`NEEDS-REPLY`** board row = the worker's answer ended in a question and it is waiting.
  Reply with a **plain** `forge dispatch @<session> "<reply>"` — **not** `--answers`.
  Contrast `NEEDS-ASK` (an explicit `forge ask`, answered with `--answers`, which owns the
  ask/callback lifecycle). NEEDS-REPLY never touches `--answers`.

## Answering a worker's ask — the escalation return path

A `NEEDS-ASK` row means a worker called `forge ask`. The row carries the
`session`, the `slug`/`stage`, and the question. Answer with:

```
forge dispatch @<session> "<your answer>" --answers <ask-id>
```

`--answers` archives the ask and consumes the worker's BLOCKED callback exactly
once (stage mode), then injects your answer into pane 1; the orchestrator relays
it to the worker, which resumes.

### Misrouting guardrails (READ BEFORE EVERY ANSWER)

1. **Right session.** The answer MUST go to the ask's OWN `session` — NOT whatever
   you dispatched to last. `forge dispatch --answers` REFUSES a mismatched session;
   do not work around it. If the row shows no session, dispatch to the session
   whose root matches the row's `root`/`label`.
2. **Always `--answers` for an ask.** A plain `forge dispatch` of your answer
   leaves the ask hot (the board keeps ringing) and the BLOCKED callback
   un-consumed. Only `--answers` clears both.
3. **One answer per ask.** A second `--answers <same-id>` fails loud ("already
   answered"). If your answer was recorded but injection failed, the error tells
   you to re-deliver with a PLAIN dispatch — follow it exactly.
4. **Verify the session exists** before dispatching — a `forge board` row proves
   it. Do not invent session names.

## What the seat NEVER does

- Never edits files under any `.dev/` — `forge` and `forge-watch` own those.
- Never answers a `NEEDS-PERMISSION` from the seat — jump into the tab (one
  keystroke) and answer the dialog there.
- Never dispatches to a session it has not seen on the board.
