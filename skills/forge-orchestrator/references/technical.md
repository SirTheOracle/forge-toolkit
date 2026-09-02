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
