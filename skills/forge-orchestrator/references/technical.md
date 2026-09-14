# Codex worker trust contract

## Identities

`physical_code_root` is the canonical Git top-level of the pane's actual
worktree. `root_identity` is SHA-256 of physical root, NUL, and that worktree's
absolute Git dir. `git_common_dir` is shared infrastructure identity only; it
must never collapse two linked-worktree deliveries. `forge.expected_root` is a
validation target resolved relative to the project root, not ownership.

## Contain launch

The interactive binary is the one recorded in `config/codex-forge-runtime.json`
(`codex.binary`); admission is by MEASURED containment — `forge codex-doctor` — not by
version. `codex.supported_interactive_versions` is an advisory record of reviewed
versions, and an unrecorded version is admitted when every containment check passes
(`codex.unknown_version_policy=probe`). Forge
passes `-C <physical root> --strict-config --sandbox workspace-write
--ask-for-approval never -c sandbox_workspace_write.network_access=false -c
sandbox_workspace_write.writable_roots=[]`. Named doctor fields must report
Never, restricted filesystem, restricted network, and exact cwd. Aggregate
doctor failure is recorded separately because unrelated state-database checks
do not define launch readiness.

Current file-store authentication means private `CODEX_HOME` is not active.
Keyring migration, parent no-op authentication, and child auth/credential
denial are live gates. Root-local TMPDIR is likewise disabled pending proof.
`codex exec` is deferred; interactive panes are the only supported surface.

## Deliveries and protected state

The bridge creates the delivery envelope. Protected delivery authority lives
under a root-identity namespace in the repository's common Git directory;
index transaction journals and backups live under the physical worktree's Git
directory. Worker markers, lifecycle requests and responses, and all `.dev`
files are untrusted. Launch manifests and broker journals are never
worker-writable — measured 2026-08-07, when a live contained pane was denied
creating `.git/forge` in both a main checkout and a linked worktree. Enforcement
is established but its source is not, so re-run that probe after any Codex
version bump. Exact delivery/session/incarnation/root/stage/capability/prompt-
digest matching, expiry, and a single unambiguous active owner are required.
Host control uses a server-authenticated loopback handshake. The network-deny
sandbox is a containment control, not an authentication boundary. A separate
credential-free lifecycle queue accepts only active-delivery, delivery-result,
strict reconcile-delivery, and read-only verify-session-incarnation; its handler
has no route to process() or effectful actions. Lifecycle responses are advisory
and never proof. Effectful `.dev` request files remain non-authoritative; commit
and publication requests must arrive on authenticated host control. The callback
artifact/owner checks prevent accidental divergence but do not authenticate one
pane against another pane in the same writable root.

## Lanes

Contain permits only workspace and materialized-review Codex work. Commit,
publish, dependency, browser, network, and live-QA capabilities use the
reviewed host lane until their named gate is protected and passing. Enforce
adds exact-path commit. Publish-enabled separately adds idempotent PR review
publication. The managed TOML is a desired-policy/hash reference, not a loaded
private runtime: the current interactive client still loads ambient config,
MCP, rules, and file-store authentication. Doctor reports that surface by
named fields. Unknown or changed surface blocks effectful/unattended gates;
contain remains limited by its explicit CLI sandbox, approval, network, and
root flags.

## Recovery and rollback

`needs_permission` is non-terminal: keep any infra lock, answer in the pane or
explicitly move to the reviewed lane, then re-enter wait. `forge codex-broker
stop --root <worktree>` puts the broker into draining state, completes claimed
work, and refuses identity-changing reuse; prepared index transactions recover
from the protected backup before new work. Rollback disables effects, preserves
journals/backups, restarts old panes at a clean boundary with contain flags,
and never grants ambient Git or network authority.

## P1 orchestrator experiments: 2026-09-14

**Status: harness implemented; native P1 acceptance BLOCKED, not complete.**
This work follows the P1 section of
`.dev/proposals/codex-claude-orchestrator-parity-current-head-reconcile/final-plan.md`.
It does not enable a Codex orchestrator or implement P2-P6. Those requirements
remain outstanding; passing harness tests must not close the parity feature.

`tests/forge-orchestrator/native-probe.sh` prepares a new, disposable source-copy
packet and runs bounded native Codex sandbox canaries. Primary, linked,
primary-with-nested and nested-linked roots are separate cells. Create,
overwrite, rename, unlink and symlink-redirection tests include workspace positive
controls and parent-side verification of actual mutations. Package/plan drift,
malformed output and native launch failures cannot become a protection pass.
The snapshot is not claimed to be a qualified runtime dependency closure.

`tests/forge-orchestrator/README.md` and the 16 cases in
`fixtures/native-cases.json` define the remaining attended native experiments
for both providers: actual closure protection, exact loading and reload,
duplicate discovery, isolated proposal/digest children, host control and locks,
dispatch/wait, asks, recovery, approvals and hook attribution. Actual Git-pointer
mutation, actual helper/config protection and interactive worker/child policy
remain unmeasured; sandbox canaries do not substitute for those checks.

The local Codex CLI was 0.153.4, resolved to
`/opt/homebrew/Caskroom/codex/0.153.4/bin/codex`, SHA-256
`b973d440acac501fd2594a43e7ca9ce41e0a65b9dfb28d0d7a7837c99e1261e3`.
Its sandbox utility uses the unified `codex sandbox -- COMMAND` form and rejects
`--strict-config`. The harness preserves explicit workspace-write, never approval,
network denial and empty additional writable roots, but omits that unsupported
flag for the sandbox primitive. The actual interactive worker launcher is unchanged.

The native attempt exited 71 with `sandbox_apply: Operation not permitted` before
the canary executed. Harness exit was 3 (BLOCKED), the primary cell has no canary
observations, and dependent topology/native-host cells are NOT_RUN. This proves
the current execution environment cannot run this nested sandbox experiment;
it does **not** prove Codex cannot orchestrate or that writes were denied by the
intended worker policy. Evidence is retained at
`/private/tmp/forge-p1-native-20260914-b/run-9e760a37d22bafaa/report.json`.
The temporary location itself is not presumed worker-protected.

Validation: 19 hermetic harness tests, shell syntax/diff checks, and existing
Codex policy smoke tests pass. The aggregate suite includes the new hermetic
tests; the full aggregate/native provider suites were not run in this session.
No providers, production tmux sessions, brokers, locks, rollout defaults or
installed skills were modified. Resume native P1 in an operator terminal that
permits the OS sandbox, using a new packet outside worker-writable roots; retain
all missing native cases until their real evidence is available. Do not advance
P1 on the strength of these unit tests.
