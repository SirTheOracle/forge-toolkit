# P1 native orchestrator experiments

This is the P1 experiment harness, not provider selection or a parity release.
Its `prepare` and `run` commands never install skills, change production
configuration, start a provider, attach to an existing tmux server, or call a
production broker. The automatic
portion invokes the real Codex sandbox **without a model**. Interactive host and
child-agent acceptance are separate, attended cases and remain NOT_RUN until
their actual traces have been reviewed. A sandbox primitive pass is not a native
worker pass, a protected-helper qualification, or a P1 completion verdict.

## Restart with scoped approvals

When the current session enforces `approval_policy=never`, approval in chat cannot
change that execution policy. Keep the existing worktree and Forge session.
Use the current client's permission control first: Ask for approval in the
desktop app, or `/permissions` in the CLI. If a fresh conversation is required,
reopen it against the same existing worktree and read the local
`.dev/proposals/codex-claude-orchestrator-parity-current-head-reconcile/p1-handoff.md`.

For a CLI process restart only, exit Codex to the shell in the **same Forge pane**
and run the optional helper there. Do not close/recreate the whole tmux session or
start another checkout. Confirm that the pane has no active worker delivery and
is appropriate for an attended operator experiment before changing its policy:

```bash
bash tests/forge-orchestrator/native-probe.sh session
```

This starts a new Codex conversation in the invoking terminal/pane, pins this
worktree, inherits the tmux environment, and supplies the P1 continuation task.
It does not create or switch tmux sessions, panes or worktrees. Desktop app users
can stay in the desktop app; the CLI helper is optional, not a migration step.
Add `--model NAME` to choose a model, or `--print` to inspect
the command without starting anything. It applies `config/codex-p1-session.toml`
as CLI overrides: on-request approvals reviewed by the user, workspace-write,
network denied and no additional writable roots. It does not edit global config,
project `.codex` files, credentials, hooks, or Forge worker settings.

There is deliberately no blanket command allow rule. A fresh session must request
individual approvals for host-side sandbox experiments, preparation of protected
fixture copies and required tmux/broker operations after verifying their paths and
dependencies. Broad approval of a worker-writable Python/shell helper would defeat
the P1 protection experiment. Keep approvals per invocation until the copied
helper closure has been reviewed. These settings do not make nested Seatbelt work
inside the sandbox; they permit requesting approved host execution where policy
allows it. Managed requirements still take precedence. Verify effective policy
after restart before attempting the native work.

Local validation on Codex 0.153.4: `doctor --json` with these overrides reports
`config.load: ok`, `approval policy: OnRequest`, and restricted filesystem and
network sandboxes. Doctor's aggregate exit was 1; this is validation of those
named configuration fields, not an all-health or native P1 pass. The 22 hermetic
harness tests and existing Codex worker policy smoke also pass.

The launcher uses the documented [CLI configuration overrides](https://learn.chatgpt.com/docs/config-file/config-advanced)
and [on-request approval mode](https://learn.chatgpt.com/docs/agent-approvals-security).
The current-client permission controls are described in [Permissions](https://learn.chatgpt.com/docs/permission-modes).

## Run the bounded automatic portion

The current preparation command requires an external disposable packet. The
2026-09-14 session now requires worktree-local work and evidence: adapt the fixture
layout and prove protection for its in-worktree operator package before following
the external-packet example below. The example remains the original harness
interface, not authorization to disregard the new locality requirement.

Run from an operator terminal that can execute Codex's OS sandbox. No environment
activation, credential copying, policy bypass or installer is needed.

```bash
mkdir -p "$HOME/.local/state/forge-p1"
bash tests/forge-orchestrator/native-probe.sh prepare \
  --directory "$HOME/.local/state/forge-p1/trial-01"
bash tests/forge-orchestrator/native-probe.sh run \
  --directory "$HOME/.local/state/forge-p1/trial-01"
```

The parent directory must exist, and the final directory must be new and outside
the source checkout. Each packet is single-use: retain it and prepare trial-02 to
retry. Preparation creates only disposable repositories and copied source files;
it does not prove protection. Use an operator-owned location outside **all**
worker writable roots. `/tmp` and `$TMPDIR` are often worker-writable and are
suitable for hermetic tests, not a presumed protected native package. A temporary
path is not rejected by the primitive: it should expose actual unexpected writes.

The generated `packet.json` binds the source package inventory and canary plans.
`run-*/report.json` retains bounded execution outcomes, exact sandbox argv,
executable hashes, per-canary observations and independent parent-side filesystem
checks. Raw provider diagnostics are not retained; hashes and recognized sandbox
errors are recorded instead. No authentication files, tokens or environment dump
are collected. Preparation status is not a measured capability report.

Exit codes for `run`: **1** failed check/integrity or invalid input; **2** primitives
completed but P1 remains incomplete; **3** native prerequisite blocked. `prepare`
returns **0** only for successful fixture creation. There is intentionally no
automatic P1 PASS/admission output. Timeouts default to 20 seconds per process
(maximum 120); the invocation-owned process group is killed on timeout. A sandbox
launch failure stops dependent cells and cannot count as write denial.

The installed 0.153 CLI uses `codex sandbox -- COMMAND`; older clients used a
platform subcommand. The harness selects only a recognized help shape. The
sandbox utility rejects `--strict-config`, so that flag is not used for this
primitive. Workspace-write, never-approval, network denial and empty additional
writable roots remain explicit. No interactive worker launch flags are changed.

## Automatic coverage and its limits

Four independent roots cover primary `--here` geometry, a linked worktree, a
primary root containing a nested worktree, and that nested linked worktree.
Nothing calls `forge-start --here`: that launcher starts production CLIs and is
outside this probe's authority. The common Git directory, worktree Git directory,
root identity and pointer path are recorded for each fixture.

Each root has create, overwrite, rename, unlink and symlink-redirection attempts
against canaries in the operator directory, broker profile/state directories,
worktree Git directory, common infra-lock directory and an outside-workspace
directory. Ordinary workspace mutations are the positive controls. The nested
primary also tests its nested worktree's resolved Git metadata directory.

The automatic portion does **not** mutate the actual `.git` pointer, execute
profile-management verbs, or prove actual helper/config-file immutability. It
does not measure a live worker's effective policy. Those stronger tests must be
performed on disposable copies during the attended closure/permissions cases.
No future P2 registration API is pretended to exist. The source snapshot includes
tracked `bin`, `config`, `prompts`, the canonical orchestrator and the existing
Codex proposal/QA skills; ignored backups and personal/global digests are excluded.
This is an initial inventory, **not** a claim of complete runtime dependency closure.

## Attended native acceptance

`operator/package/p1/native-cases.json` contains the required cases and dependency
ordering for **both** Claude and Codex. Keep a separate result per provider,
topology and case. Do not replace a failed case with a shell stub, textual marker,
manually injected callback, fabricated hook event or a passing test suite.

1. Before any approved host effect, trace the actual privileged dependency closure
   of the copied Forge commands. Inventory interpreters, Python/PyYAML includes,
   native executables, configs, hooks, transitive helpers and resolved symlinks.
   Pin all Forge paths to this copy, not installed `~/bin` or source symlinks.
   Prove the actual package/config/profile paths cannot be altered by the intended
   contained workers. Owner bits and content hashes alone are insufficient.
2. Use a new disposable native conversation for explicit file loading. The probe
   entry is `operator/package/p1/SKILL.md`; its random marker is absent from the
   initiating prompt. Read the copied canonical orchestrator separately to audit
   its compatibility, without executing its hard-coded installed commands.
   Codex's candidate interactive policy is workspace-write with on-request
   approval, not Forge's never-approval worker policy. Retain measured native
   `/status`/policy and CLI identity. Do not use bypass flags or substitute the
   system prompt. User/global config and auth stay in place and are not copied;
   their effective influence must be measured, not assumed absent.
3. Exercise fresh load, duplicate discovery, explicit reinvocation, changed
   discovery, compaction, resume and restart. Use disposable project decoys for
   local duplicate tests. A global-discovery test needs a separately isolated
   environment; never edit real global skill files to simulate one.
4. Run native isolated proposal children A/B, join/follow-up, and C synthesis;
   then a disk-only digest child. Use dummy per-child canaries and dummy tokens,
   not real credentials. Distinguish context isolation from inherited machine
   authority and record the actual child policy. No sequential-parent substitute.
5. Only after the preceding protection gate, create a dedicated disposable tmux
   server/socket and five shell panes. Scope all tmux operations to that socket
   (a protected wrapper may be necessary; inventory it). The existing launchers
   are not a fixture factory. Start only new, invocation-owned brokers against
   fixture roots and real session/incarnation identities. Exercise existing
   bridge identity, observation, authenticated registration, lock, dispatch/wait,
   digest, ask/answer, callback-recovery and hook-attention paths individually.
   Keep default constraints/hygiene/lifecycle gates. A gate that cannot currently
   pass is a measured implementation gap, not permission to bypass it.
6. For copied bridge calls, set `FORGE_BROKER_BIN` and `FORGE_FORGE_BIN` to copied
   absolute paths; point `FORGE_PROMPTS_DIR`, `FORGE_IDLE_PROMPTS_FILE` and
   `FORGE_RESET_CAPABILITY_FILE` at the copied assets. Use only the P1 digest
   fixture through `FORGE_DIGESTS_DIR`, under the template name `digest`.
   Ensure every other invoked helper resolves into the inventoried closure.
   Do not set fake identity, broker-test, or enforcement-disable variables.
7. Preserve evidence before cleanup. Stop only an invocation-owned disposable
   broker/session after terminality or confirmed safe abort; do not clear a live
   lock or use a global tmux kill command. Failed experiments retain their files.

Record each attended result with: provider/version/model, root/topology, session
and process/child identity, package/profile/helper hashes, case ID, actual argv
or tool calls, approval count/scope, sanitized evidence paths/hashes, outcome and
remaining limitations. Label loading evidence `loader-attested`,
`explicit-load-tool-traced`, or `behavioral-only`. Only the first two can prove
the named entry; neither certifies every discovery mechanism automatically.

All 16 native cases are required evidence categories. A PASS must cite actual
observations and verified dependencies; missing/blocked cases remain explicit.
`packet.json` is a worksheet, not an authorization mechanism. An attended report
must reconcile it with the retained automatic report; editing worksheet statuses
cannot qualify a host. P1 is not complete until both providers' applicable cases
are resolved and the named remaining implementation gaps are reported. No
production provider is enabled by any artifact in this directory.

## Hermetic tests

```bash
bash tests/forge-orchestrator/run.sh
```

These tests use temporary directories, synthetic subprocess results and an
unsandboxed execution of the **canary fixture only** to check that allowed
protected writes are detected as failures. They never launch a native provider
and never count as native acceptance.
