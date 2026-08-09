# Codex worker trust contract

## Identities

`physical_code_root` is the canonical Git top-level of the pane's actual
worktree. `root_identity` is SHA-256 of physical root, NUL, and that worktree's
absolute Git dir. `git_common_dir` is shared infrastructure identity only; it
must never collapse two linked-worktree deliveries. `forge.expected_root` is a
validation target resolved relative to the project root, not ownership.

## Contain launch

The supported interactive binary is `/opt/homebrew/bin/codex` 0.147.0. Forge
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
directory. Worker markers, requests, responses, and `.dev` files are
untrusted. Exact delivery/session/incarnation/root/stage/capability/prompt-
digest matching, expiry, and a single unambiguous active owner are required.
Launch manifests and broker journals are never worker-writable.
Host control uses a server-authenticated loopback handshake; the attested
network-deny sandbox prevents a contained pane from reaching that endpoint,
and the client never sends its token before verifying the broker proof.
Effectful `.dev` request files are never authoritative; commit and publication
requests must arrive on that authenticated host transport. Enabling a future
Codex mutation lane requires a separately proven worker-origin transport.

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
