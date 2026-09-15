# P2: Provider Selection and Protected Launch Binding

P2 implements the qualified Claude/Codex launch path. It is no longer a selector
that unconditionally refuses Codex. Production admission still requires P3's
protected provider assets and a native qualification report for their exact
runtime, management entry, dependencies and physical-root topology. Fixture
executables and fixture reports do not qualify a production model.

## Implemented Contract

- Closed --orchestrator claude|codex on forge-start and forge spawn. Invalid
  requests fail before mutation. Creation defaults to Claude; omitted provider
  on qualified reuse retains the existing provider.
- Root-scoped profile inspect/register/disable, revision CAS, retained snapshots,
  exact hook argv/config/dependency checks and protected conformance reports.
  Project declarations and environment stamps cannot grant authority.
- A distinct copied management entry, not broad approval of generic forge.
  Generic register/disable refuse even with operator-like environment values.
  Bootstrap is an explicit operator action, never an installer/spawn side effect.
- One host backend reserves durable preparing state before tmux creation. It
  captures actual root/session/incarnation/pane and idle-shell process, journals
  the hook process before execution, and requires real creator ancestry plus
  a one-use CAS claim before population. A launch locator is not a capability.
- Approved built-in/custom hooks receive versioned request environment and the
  unchanged final positional session argument. Intermediaries must belong to the
  qualified closure. Changed hook bytes/config refuse before use.
- Five-pane geometry, persisted claude identity and worker aliases/commands stay
  intact. Pane zero adds role/provider. Bare codex still means worker pane 3.
  Explicit pane environments prevent injection from a long-running tmux server.
- Ready binds actual provider executable/hash/version/argv/PID/start and the
  authenticated broker startup/process/closure identity. Required state changes
  precede attach/switch. Attach failure cannot rewrite ready or kill workers.
  Partial construction cleans only its new panes, never a populate host.
- Ensure checks actual live identity and fixed profile/package/daemon identity.
  Mismatch, replacement, competing construction and reused/stolen handoffs refuse
  without adopting/restarting another host. Explicit legacy Claude ensure needs
  five pane receipts and a corroborated native Claude process. Ordinary
  unconverted legacy behavior remains separately routed.
- Bridge send/dispatch/pending-row creation and broker delivery registration check
  new admission. Daemon checks do not recurse into its own control socket.
  Status is read-only and explicitly unverified, not an admission capability.
- Terminal handoff requires no live prior root/process/hook, stopped original
  broker, closed deliveries/pending work, resolved effect journals/infra ownership,
  and no unconsumed requests/callbacks. Terminal audit residue is retained.
  Retry creates a new id/generation and preserves all old launch history.
- Explicit interrupted-preparation recovery handles selected/unselected records
  only after terminal observation. It never kills resources, adopts a shell,
  reconstructs current from environment, or uses age as authority. Original
  callback/result/recovery/stop paths are not gated on a new profile.

## Operator Interfaces

Provision and register the physical worktree before qualifying its runtime.
Profiles cannot transfer automatically to newly created worktrees. Qualified
forge-start therefore uses --here; worktree creation/registration is an explicit
preceding operation. Legacy default provisioning is unchanged.

    bin/forge-orchestrator-host prepare-management --root /absolute/worktree
    forge orchestrator-profile inspect --root /absolute/worktree --from candidate.json

Preparation prints management_entry. Invoke that exact copied entry, through its
own native operator approval, for register/disable:

    /absolute/protected/orchestrator/management-v1/bin/forge orchestrator-profile register --root /absolute/worktree --from candidate.json --expect-revision 0
    /absolute/protected/orchestrator/management-v1/bin/forge orchestrator-profile disable --root /absolute/worktree --expect-revision 1
    forge-start --here --orchestrator codex --no-attach
    forge spawn --root /absolute/worktree --orchestrator codex
    forge orchestrator-status --root /absolute/worktree
    forge orchestrator-recover --root /absolute/worktree --launch-id EXACT_ID

The candidate schema is forge-orchestrator-candidate/1: exact root identity;
on_spawn kind/argv/supported_providers/transport_version; file path/hash inventory;
package/core/adapter/helpers identities; provider argv/executable/version/evidence;
closed tool paths; project-config SHA (or null); protected report path/hash.
The executable validator is validate_candidate in bin/forge-orchestrator-host.
Inspect validates without running the hook. P3 produces real qualification
reports; there is deliberately no production force-qualification command.
Bootstrap refuses an existing management entry instead of overwriting its bytes.

## Verification Scope

test_host_launch.py uses actual isolated tmux servers, compiled non-model
providers, a real copied broker, process observation and copied management/hooks.
It covers direct/spawn/custom launch, ensure/mismatch, replacement, aliases and
geometry, environment isolation, disabled/changed profiles, terminal retry,
concurrent creators, stolen/reused handoff, dead creator/live hook, partial cleanup
and attach failure. Storage/request/profile/runtime suites add publication-crash,
CAS, malformed/unsafe records and input-drift cases.

    bash tests/forge-orchestrator/run.sh
    env FORGE_ORCHESTRATOR_NATIVE_MANAGEMENT=1 python3 -m unittest discover -s tests/forge-orchestrator -p test_management_sandbox.py

Native management tests measure Codex's actual sandbox utility without model
calls: ordinary workspace edits succeed, protected writes and actual management
mutations fail for primary and linked roots. They use the launcher's environment
without ambient TMPDIR; a writable temp grant containing the primary repository
can defeat linked-root denial. The launcher clears client AND pane environments.
These tests do not certify nested-worktree pointer protection, native model/child
policy, skill loading, or the full P3 runtime closure. Each advertised production
topology still needs its own report.

P3 must restrict native approvals to the intended verbs and protected entries.
Broad approval of arbitrary generic Forge/legacy-hook execution is not a safe
management boundary: ordinary legacy custom hooks intentionally remain unchanged.
The native tests establish contained-worker denial, not safety of every possible
privileged wrapper. The runtime-closure/hook-conformance report must cover those
indirect paths before production custom-hook admission is qualified.

P3 can proceed against these implemented interfaces. P4-P6 remain separate phases;
P2 does not claim their observation or end-to-end workflow parity.

## Closeout Verification (2026-09-15)

- Orchestrator runner: 137 tests, 135 passed and two opt-in native checks skipped.
- Explicit native management run: both skipped checks passed (primary/linked).
- Existing start golden suite: 71 passed; spawn suite: 31 passed.
- Bridge P0/contract regression suite: 60 passed; broker suite: passed.
- Isolated web install/drift/uninstall and launcher checks: passed.
- Policy smoke: hermetic pass; production live-model gates not run.
- Shell syntax and git diff whitespace checks: passed. Local main was already
  merged; no production installation, model launch or live-session migration ran.

Retained combined evidence in this worktree:
.dev/forge-p2/requests-1789493756650823000/report.json.
