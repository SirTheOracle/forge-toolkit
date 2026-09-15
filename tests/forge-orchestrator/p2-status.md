# P2 Implementation Status

P2 is **IN PROGRESS**. This first commit implements request parsing and transport,
not protected profiles, launch records or production Codex readiness. P1 already
demonstrated native feasibility; its findings are not being reopened or used to
drop Codex from the implementation.

## Implemented

- `forge-start` and `forge spawn` accept the closed option
  `--orchestrator claude|codex`. Missing, empty, repeated and unknown values fail
  before registration, worktree, broker, hook or tmux effects.
- `forge-start` parses the entire argument list, including flags following the
  session name. It rejects extra arguments instead of silently dropping a request.
- New sessions still default to Claude. Omitted provider on legacy spawn/ensure
  keeps the existing no-population path. Explicit-provider reuse and duplicate
  creation races refuse until the protected live-binding reader is implemented;
  pane count is not provider evidence. No existing session is restarted or killed.
- Spawn passes `FORGE_ORCHESTRATOR_PROVIDER=claude|codex` and
  `FORGE_ORCHESTRATOR_REQUEST_VERSION=1` to its populate hook. The session name
  remains the final positional argument. Built-in hook argv remains
  `forge-start --populate-existing SESSION`; custom hook argv is unchanged.
- Populate validates the complete stamp and any explicit CLI request before
  touching tmux. Partial stamps, unknown transport versions, invalid providers
  and CLI/stamp contradictions fail. The stamp is a request, not authority.
  Standalone starts do not use ambient stamps as global provider defaults.
- Codex requests currently return `ORCHESTRATOR_NOT_QUALIFIED`, including
  dry-run/no-populate/force paths. Project declarations and environment flags
  cannot enable them. There is no silent Claude fallback or temporary bypass.

## Remaining P2 Work

1. Operator-only profile inspection/registration/disable, protected revisioned
   snapshots, exact hook closure/conformance validation and management denial.
2. Protected spawn-to-populate handoff with actual pane/PID/start identity;
   refuse live providers and unowned/ambiguous idle shells before respawn or text.
   Existing legacy populate behavior is unchanged by this first commit and is
   **not yet the new qualified path**.
3. Preparing/ready/failed launch records, separate current-generation CAS,
   immutable root/provider/profile/package bindings, and observed process epochs.
4. Broker startup closure/handshake identity and read-only bridge comparison;
   refuse new effects on mismatch while preserving original-delivery recovery.
5. Full matching-provider reuse and race handling, attach ordering, failure
   cleanup, aliases/machine output and the complete P2 negative matrix.

P3's protected executable/assets qualification remains a prerequisite for actual
Codex host admission. Completing only the selector does not complete P2.

## Verification

`test_requests.py` covers parsing, zero-effect refusals, unchanged Claude launch
argv, legacy custom-hook transport, omitted-provider reuse and explicit reuse/race
refusal. Its processes and tmux are stubs; it does not certify native authority.
The normal orchestrator runner includes these tests. Existing launcher goldens
and spawn/registry tests remain unchanged.
