# P2 Implementation Status

P2 is **IN PROGRESS**, not complete. Request transport, protected record primitives,
read-only diagnostics and broker startup identity are implemented. Profile admission
and the actual protected host-launch path are not implemented. P1 already
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
- The shared record library validates root, seat, process/start/version, fixed
  package/core/adapter/helper inputs and admission-evidence fields. It provides
  no-follow bounded JSON reads, exclusive first reservation, revision/generation
  CAS, durable preparing-before-reference publication and ready/failed transitions.
  Failed and unselected transactions cannot be automatically reused. These are
  storage primitives, not a qualified writer or a completed launch transaction.
- `forge orchestrator-status --root PATH` reads the protected binding without
  creating state. `forge-bridge orchestrator-status` first uses the existing live
  bridge root resolver, without its audit-write wrapper. Both expose role/provider
  separately and explicitly return `admission=unverified`; they never route or
  admit from stored metadata. The loader resolves the sibling toolkit library,
  ignores workspace shadow modules and does not use/create its bytecode cache.
- The broker retains startup entry/interpreter/imported-file hashes, protocol and
  PID/start identity in memory and its protected startup record. Its authenticated
  hello binds this identity to the server nonce. `control --expect-runtime-sha256`
  checks the authenticated identity, current inputs and live PID/start before
  sending a request; it cannot fall back to an unpinned lifecycle queue.
- Changed startup files refuse new delivery registration. Existing inspection,
  original-delivery recovery and shutdown stay available. Reuse does not kill a
  daemon merely because its source changed. This inventory is not P3's complete
  immutable runtime/dependency qualification.
- `LIB_FILES` declares the shared module; legacy symlinked callers resolve it in
  the source tree. The installer checks required libraries before installation and
  reports forward/reverse library inventory drift. Protected candidate copying,
  closure hashes and active-package retention remain P3 work.

## Remaining P2 Work

1. Operator-only profile inspection/registration/disable, protected revisioned
   snapshots, exact hook closure/conformance validation and management denial.
2. Protected spawn-to-populate handoff with actual pane/PID/start identity;
   refuse live providers and unowned/ambiguous idle shells before respawn or text.
   Existing legacy populate behavior is unchanged by this first commit and is
   **not yet the new qualified path**.
3. Integrate the record primitives into the sole host launcher, bind actual
   observations, commit ready/failed before attach/switch, and implement verified
   terminal handoff/retry. No current launcher creates these records yet.
4. Admission-time live bridge/process/profile/package/broker comparison and
   revalidation before effects. Diagnostics are not that gate.
5. Full matching-provider reuse and race handling, attach ordering, failure
   cleanup, aliases/machine output and the complete P2 negative matrix.

P3's protected executable/assets qualification remains a prerequisite for actual
Codex host admission. Completing only the selector does not complete P2.

## Unresolved Prerequisite

Section 4 requires an admitted operator-only entry for profile registration and
disable. P1's closeout explicitly records profile-management verbs as `NOT_RUN`.
The existing full-access development session, same-user ownership, an environment
stamp, and a content hash do not establish that authority. There is no registration
bypass or new management action on the worker lifecycle queue.

The management entry and its effective native denial tests must be implemented
and qualified before custom-hook admission can open. This intersects P3's protected
runtime/policy packaging; it is not uncertainty about whether Codex can orchestrate.
Completing these prerequisites requires coordinated P2/P3 work. Regardless of
phase ordering, the remaining launcher/handoff work above is still required.

## Verification

`test_requests.py` covers parsing, zero-effect refusals, unchanged Claude launch
argv, legacy custom-hook transport, omitted-provider reuse and explicit reuse/race
refusal. Its processes and tmux are stubs; it does not certify native authority.
The normal orchestrator runner includes these tests and the new record, status and
broker-runtime suites. Storage tests cover wrong root, stale CAS, competing create,
crash-before-reference, terminal rewrite refusal, malformed JSON, symlink/hardlink
redirection and changed live descriptors. Handshake tests check legacy evidence,
tampered proofs, runtime drift and process mismatch before token disclosure.
A disposable real broker test changes only its copied source and verifies refusal
of new registration, unchanged daemon identity, inspection and orderly shutdown.
None of these tests qualify profile management or the native host launch path.
Existing launcher goldens and spawn/registry tests remain unchanged.
