Run `~/bin/forge-bridge status` to render and display the current forge pipeline state.

The status file lives at `.dev/forge-status.md` and is rolled forward by the bridge as a side effect of dispatch/wait/callback. Reading it should not require the orchestrator to search through logs or pane scrollback — it already summarizes:

- Active pipeline + current stage (or "idle" with last completed)
- Next stage (per the canonical transition table)
- Recent activity (last 15 events: dispatches, completions, blocks)
- Pending callbacks (response: null entries)
- Artifacts (files produced so far)
- Notes (from forge-context.yml)

Surface the output verbatim — do not re-narrate or compress it. The user is reading it for the same reason they ran the command.

Arguments: $ARGUMENTS
