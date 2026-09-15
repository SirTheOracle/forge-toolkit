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

**Status: native loading/delegation and bounded protection measured; P1 INCOMPLETE.**
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

The local Codex CLI was 0.153.4, resolved to
`/opt/homebrew/Caskroom/codex/0.153.4/bin/codex`, SHA-256
`b973d440acac501fd2594a43e7ca9ce41e0a65b9dfb28d0d7a7837c99e1261e3`.
Its standalone sandbox needs `--permission-profile :workspace --cd ROOT` and
rejects `--strict-config`. The old interactive flags yielded read-only controls,
not a valid writable experiment. The actual Forge worker launcher is unchanged.
Native model sessions used Codex `gpt-6-astra` and Claude `claude-fable-5-1`;
Claude stream init events identify CLI 2.1.266.
The native worker probe reused Forge's containment flags, not its pinned model or
interactive launcher, and does not qualify the complete worker launch contract.

### Full-access continuation

No further permission restart was needed. Implementation, copied packages and
experiment artifacts stayed in the existing `forge-toolkit-forgetoolB` worktree.
`prepare --in-worktree` places evidence/repositories in `.dev/forge-p1` and the
candidate protected package in `.codex/forge-p1/<trial>-<packet-id>/operator/package`.
Protection is measured, not inferred from the `.codex` name or Unix mode bits.

Evidence below is relative to `.dev/forge-p1/` in this worktree:
The tracked `tests/forge-orchestrator/evidence/2026-09-14.json` indexes its hashes.

| Measurement | Result | Evidence |
| --- | --- | --- |
| Standalone sandbox matrix | 145 PASS: primary 35, linked 35, primary-nested 40, nested-linked 35 | `native-20260914-04/run-78f37752fd907a1b/report.json` |
| Actual copied file write-open | All 76 files denied and unchanged from each of four fixture roots plus source-worktree root; all positive controls allowed | `native-20260914-04/package-access-5a137b4592e1c953/report.json` |
| Native Codex contained execution | One traced command, 76 denied opens, real positive write; never approvals, workspace-write, network disabled, empty extra roots | `native-20260914-04/codex-worker-access-reviewed.json` and `codex-worker-access.jsonl` |
| Codex explicit protected entry | Exact file read and marker, native `exec`, exit 0 | `native-20260914-03/codex-entry.jsonl` |
| Claude explicit protected entry | Exact `Read` tool call and marker, native `--print`, exit 0 | `native-20260914-03/claude-entry-02.jsonl` |
| Codex native children | Four native spawns with `fork_turns:none`, A follow-up, joins, synthesis 124; native parent resume succeeded | `native-20260914-03/codex-children.jsonl`, `codex-child-native-calls-reviewed.json` |
| Claude native children | A/B and disk-only digest child, A/B follow-up after parent resume, C synthesis 124 | `native-20260914-03/claude-children.jsonl`, `claude-followup.jsonl` |
| Python dependency resolution | FAIL: a worker-writable project `yaml.py` wins bare Python import resolution | `native-20260914-04/closure-import-resolution.json` |

Both loading results are **explicit-load-tool-traced**, not loader-attested or
interactive/discovery acceptance. The loading marker was absent from the initial
prompt. Codex parent thread was `01a0a245-8b2d-73c3-8df3-d65619bd1e16`;
Claude child experiment session was `39d59893-3a42-4d11-bbda-0ecd21350d02`.
These basic capabilities are demonstrated; there is no evidence here that Codex
is fundamentally unable to load protected instructions or delegate natively.

Codex's native rollout exposes actual spawn/follow-up options, but child message
payloads are opaque. The reviewed extract retains options and message hashes,
not ciphertext or unrelated session context. Disjoint input contents and child
effective filesystem/network/approval and dummy-token inheritance are not fully
verified. Claude's traces expose disjoint A/B prompts and the digest child Read;
the model's isolation/ambient-authority claims are not independent policy proof.
The first Claude tool allowlist omitted SendMessage. That configuration error
was corrected and follow-up succeeded; it is not a missing Claude capability.
The synthetic digest lacked a CONFIDENCE field; this is not integrated Forge
digest-render/retry acceptance. Explicit Codex child closure was unavailable on
the tested API; completion notifications were observed instead.

### Protected runtime and native host continuation

The import defect above is resolved for the new **fixture runtime**, not for
arbitrary installed helpers. `prepare --runtime` copies pure-Python PyYAML into
the protected package and pins Python to `-I -S -B`. A whitelist environment
excludes project Python imports, shell startup variables, ambient Forge overrides
and user Git configuration. The entry supports the copied helpers' `-c`, stdin
and protected-script forms; external scripts and unsupported modes refuse.
This is experimental packaging, not a production admission or installer API.

New evidence is indexed by `tests/forge-orchestrator/evidence/2026-09-14-runtime.json`.
Paths below are relative to `.dev/forge-p1/`:

| Measurement | Result | Evidence |
| --- | --- | --- |
| Runtime isolation | Protected PyYAML loads despite hostile project/Python startup paths | `runtime-20260914-01/isolated-import.json` |
| Copied package | 121 actual files deny write-open from five roots, positive controls succeed | `runtime-20260914-03/package-access-e496ab9333af817f/report.json` |
| Runtime and actual Git pointers | Primary/linked pass; nested-parent and source-root cells expose nested `.git` pointer write-open | `runtime-20260914-03/runtime-audit-8e4c04612d41a2d1/report.json` |
| Native contained worker | 2,088 package/runtime/pointer files denied from primary and linked roots; actual command traces and positive controls | `runtime-20260914-03/native-worker-fab24055416bc6b2/report.json`, `native-worker-23c06b965202ac20/report.json` in the same packet |
| Native nested-parent worker | FAIL: nested linked worktree `.git` pointer opens for write; bytes remain unchanged | `runtime-20260914-02/native-runtime-reviewed.json` |
| Codex reviewed host | Actual sandbox denial, one-time native approvals and protected bridge `identity_state=MATCH` | `runtime-20260914-03/host-8d2b8bec/host-identity-approved.json` |
| Real worker registration | Live Codex worker argv/PID attestation, authenticated registration, normal cancellation and broker stop | `runtime-20260914-03/host-8d2b8bec/registration.json` |
| Shared infra lock | Acquire, cross-root contender timeout with live holder, status and normal release | `runtime-20260914-02/host-14bf2203/report.json` |
| Native children | A/B follow-up, C synthesis, disk-only child, independently recorded effective policies and 2,088 denied child write-opens | `runtime-20260914-03/host-8d2b8bec/codex-interactive-reviewed.json` |

The runtime audit covers resolved Python/stdlib/tools and native dependencies.
Sixteen platform-cached library names are explicitly trusted as OS components;
they are not copied or claimed to have been individually mutated. This does not
certify every possible helper path or dependency-alias replacement. Actual nested
pointer write-open is a topology failure, not proof of a performed destructive
write. Primary and linked support must be evaluated separately from nested roots.

The native host and all four children report workspace-write, network disabled,
on-request approval in their native rollout metadata. A child also successfully
read an explicitly staged **dummy** token outside its workspace. No real token was
read: this demonstrates shared readability, not secret isolation. Child message
payloads remain opaque; native `fork_turns:none`, follow-up and completion are
observed, but plaintext disjoint-input attestation remains incomplete.

Identity testing required literal, freshly observed tmux binding because the
first native tool execution did not retain the expected ambient binding. Two
one-time approvals succeeded; one malformed command with an extra trailing period
was rejected before execution. No persistent allow rule was selected. Review was
performed by the P1 test controller, so this is not a human usability study.
Registration and lock results above are operator-driven native-fixture supporting
evidence, not a claim that the Codex host completed those integrated gates.

The real dedicated-server test found a broker defect: its minimal subprocess
environment discarded the inherited tmux socket. `bin/forge-broker` now binds
custom sockets from validated startup context, persists that binding and refuses
cross-socket daemon reuse. Requests cannot choose a socket. Default/legacy
bindings remain compatible; the lifecycle action list is unchanged.

### Native workflow continuation

The `workflow-20260914-01` fixture now exercises the actual Codex host, not just
operator-driven primitives: protected entry read, scoped approval, broker start,
infra-lock acquire and linked-root contention, contained `forge codex-launch`,
legal dispatch, genuine callback, wait, missing-template handling and a native
disk-only digest child. The retry selects the existing immutable `digest`
template; it does not demonstrate replacing template bytes. The callback identity
is unchanged and no worker redispatch occurs for that retry.

This exposed and fixed a production `forge ask` defect. Local worker asks used
cross-session callback flags, forcing tmux access denied by worker containment.
Local asks now use the existing exact-pending self-callback path. Explicit
`--root` relays retain cross-session validation; no identity gate, lifecycle
action or worker policy was weakened. A newly staged protected copy was audited
and the native worker produced its genuine delivery-bound BLOCKED callback.

The native host was exited and resumed while that callback and the shared lock
remained live. It recovered the same callback ID and pending timestamp. A public
answer was recorded once and duplicate answering refused. Cross-root answer-side
callback consumption still warned; the owning host continued via public commands.
The old BLOCKED cleanup must not consume a newer DONE. This residual warning is
not a claim of automatic cross-root cleanup parity.

Fixture findings also matter for subsequent phases: TMUX requires numeric `N`,
not the displayed `$N`; contained asks need a session routing hint; long commands
need file-pointer sends; and visible-banner reset proof failed in a short tiled
pane but succeeded at 100x53. No reset/send guard was bypassed. The qualified
reset profile is disposable and uses banner transition, not a percentage-only
pristine predicate. Production reset configuration was not changed.

Claude's additional native child Read of an outside-workspace dummy token
succeeded with default permission mode and a read-only tool allowlist. This
demonstrates shared readability, not isolated machine authority or a complete
child OS-policy attestation. Hooks remained disabled in these experiments.

Validation: 45 targeted tests pass. The command-center suite has 238 passes and
39 identical failures with both the immutable HEAD baseline and patched `forge`:
38 prose synchronization assertions and one existing real-bridge relay failure.
The suite used a dedicated tmux socket. Installed prose was not modified to make
the comparison pass. Existing worker policy smoke passes (live gates skipped).

P1 remains INCOMPLETE: genuine hook/provider attribution, remaining global and
Claude loading cases, full child-policy reconciliation and profile-management
authority still need evidence. Nested-parent pointer protection still fails.
The workflow and cleanup evidence is indexed in
`tests/forge-orchestrator/evidence/2026-09-14-workflow.json`.

### Remaining gates (earlier snapshot)

Native interactive protected loading and Codex reload after compaction/resume
now have traced evidence. Separate reports cover fresh restart and local duplicate
discovery; these do not certify changed global discovery. Claude's corresponding
interactive checks use normal client auth and scoped Read approvals, not a copied
credential or system-prompt replacement. Hooks were disabled to isolate loading
and permission tests and therefore cannot count as hook/attention acceptance.

P1 remains INCOMPLETE: full provider/topology loading reconciliation, remaining
child inheritance checks, profile-management authority, native-host registration
and lock integration, legal dispatch/wait, rendered digest/missing-template retry,
ask/answer, callback recovery and genuine provider-aware hooks are still required.
The current fixtures establish useful capabilities; they neither enable provider
selection nor close P1/P2-P6. Preserve this distinction in status and PR text.

Earlier `sandbox_apply: Operation not permitted` reports are historical only.
The preserved copy is beside the proposal's `p1-handoff.md`. Trials 01/02 have
failed positive controls and are retained as invalid protection evidence; the
corrected trials 03/04 passed. Do not attribute that difference solely to layout:
the standalone profile invocation was corrected too.

Validation: 37 hermetic harness tests and the full broker suite (including five
socket-binding unit tests) pass. Existing Codex policy smoke passes with its live
gates skipped. Native sandbox/package-access exits
are deliberately 2 (primitives complete, P1 incomplete). The full aggregate and
integrated native workflow suites were not run. Existing production tmux sessions,
brokers, locks, rollout defaults, installed skills and worker containment settings
were not changed. No PR or production provider admission follows from this report.
