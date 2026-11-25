# Orchestrator acceptance and experiments

## P6 local acceptance

```bash
python3 tests/forge-orchestrator/acceptance.py --output .dev/p6-acceptance-RUN
```

Use a new directory for each run. This executes the finite local orchestrator,
bridge, broker, skill, fix-runner, launcher, recovery and lock suites. It uses
disposable local process/tmux fixtures, never model calls, sign-in, installation
or a production profile change. Native-management opt-ins and attach are forced
off. Keep source stable until it finishes; drift, a failed/missing suite or an
interrupted run makes the aggregate fail. Individual opt-in skips remain in the
logs and native workflow cases are explicitly `not-run` in the report.

`report.json` records exact source/tool identities, commands, exits and log
digests. Its schema is intentionally different from native conformance and
cannot be registered. Related local regression suites are mapped to the finite
affected issue paths, but do not close or requalify those original defects.
The committed scope and results are in [p6-status.md](p6-status.md).

## Historical P1 native experiments

This is the P1 experiment harness, not provider selection or a parity release.
Its `prepare`, `run`, `package-access` and `runtime-audit` commands never install skills, change production
configuration, start a provider, attach to an existing tmux server, or call a
production broker. The automatic
portion invokes the real Codex sandbox **without a model**. Interactive host and
child-agent acceptance are separate, attended cases whose status comes from
reviewed native traces, not this automatic report. A sandbox primitive pass is not a native
worker pass, a protected-helper qualification, or a P1 completion verdict.
`native-worker` is a separate, explicit opt-in that starts a real Codex model run.

## Restart with scoped approvals

The 2026-09-14 full-access continuation successfully runs the native OS sandbox
in the existing worktree. No further restart or global permission-rule change is
needed for the bounded probes. The guidance below is for a restricted session;
full access itself does not qualify the intended reviewed-host profile.

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
named configuration fields, not an all-health or native P1 pass. The 37 hermetic
harness tests and existing Codex worker policy smoke also pass.

The launcher uses the documented [CLI configuration overrides](https://learn.chatgpt.com/docs/config-file/config-advanced)
and [on-request approval mode](https://learn.chatgpt.com/docs/agent-approvals-security).
The current-client permission controls are described in [Permissions](https://learn.chatgpt.com/docs/permission-modes).

## Run the bounded automatic portion

Use `--in-worktree` to keep the fixture repositories and evidence under `.dev/forge-p1`
and the candidate protected package under `.codex/forge-p1`. Both stay inside the
current checkout; no existing Forge session is changed. The exact package path
is in `package.json`. `.codex` is a measured candidate, not a protection guarantee
for arbitrary providers or permission profiles.

Run from an operator terminal that can execute Codex's OS sandbox. No environment
activation, credential copying, policy bypass or installer is needed.

```bash
mkdir -p .dev/forge-p1
bash tests/forge-orchestrator/native-probe.sh prepare --in-worktree \
  --directory .dev/forge-p1/trial-01
bash tests/forge-orchestrator/native-probe.sh run \
  --directory .dev/forge-p1/trial-01
bash tests/forge-orchestrator/native-probe.sh package-access \
  --directory .dev/forge-p1/trial-01
```

The parent directory must exist and the final directory must be new. `run` is
single-use: retain its evidence and prepare trial-02 to retry. `package-access`
can be repeated; each invocation uses new evidence and positive-control paths.
Preparation does not prove protection. Without `--in-worktree`, the original
external-packet interface remains available, but is not appropriate for the
current session's locality requirement. `/tmp` and `$TMPDIR` are often writable
by workers and are not presumed protected locations.

The generated `packet.json` binds the source package inventory and canary plans.
`run-*/report.json` retains bounded execution outcomes, exact sandbox argv,
executable hashes, per-canary observations and independent parent-side filesystem
checks. Raw provider diagnostics are not retained; hashes and recognized sandbox
errors are recorded instead. No authentication files, tokens or environment dump
are collected. Preparation status is not a measured capability report.

Exit codes for `run` and `package-access`: **1** failed check/integrity or invalid input; **2** primitives
completed but P1 remains incomplete; **3** native prerequisite blocked. `prepare`
returns **0** only for successful fixture creation. There is intentionally no
automatic P1 PASS/admission output. Timeouts default to 20 seconds per process
(maximum 120); the invocation-owned process group is killed on timeout. A sandbox
launch failure stops dependent cells and cannot count as write denial.

The installed 0.153.4 CLI requires
`codex sandbox --permission-profile :workspace --cd ROOT -- COMMAND` for this
primitive. Top-level interactive `--sandbox`/approval flags did not select the
utility's writable profile: every positive-control write was denied. Those runs
are failed controls, not protection evidence. The sandbox utility also rejects
`--strict-config`. Older recognized clients retain the platform-subcommand form.
The standalone `:workspace` profile is not an attestation of interactive worker
settings. No Forge worker launch flags are changed.

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

`package-access` separately attempts write-open on every actual copied file,
without writing bytes, for all four fixture roots and the source-worktree root
when using the local layout. It verifies positive-control writes, exact output
inventory, hashes and modes independently in the parent. Allowed protected opens,
launch failures and missing observations cannot pass. This does not prove actual
file rename/unlink denial, mutate the real `.git` pointer, exercise profile
management, qualify transitive dependencies or measure an interactive worker.
No future P2 registration API is pretended to exist. The source snapshot includes
tracked `bin`, `config`, `prompts`, the canonical orchestrator and the existing
Codex proposal/QA skills; ignored backups and personal/global digests are excluded.
This is an initial inventory, **not** a claim of complete runtime dependency closure.

## Latest measured results

The final P1 investigation disposition is recorded in
[`evidence/p1-closeout.md`](evidence/p1-closeout.md), with all 16 categories and
their explicit limitations. Native Codex orchestration is feasible; production
provider selection remains P2-P6 work. Historical incomplete statuses below are
superseded by that closeout, not converted into automatic admission.

The final closure packet has 127 copied files and 2,097 audited entries. Its
native hook observer is `fixtures/hook_capture.py`: install only its protected
copy with an inventoried operator-owned JSON configuration containing `provider`,
`python`, `adapter`, `root`, `environment` and `evidence`. `evidence` must already
exist. The observer records real native input and adapter status, emits no
decision output, and never forwards SubagentStop as parent Stop. It is not a
production hook installer or authorization mechanism. Nonzero adapter results
remain failures in the evidence even though the observer itself does not block.

`native-worker` disables ambient hooks/plugins to isolate containment. The
attended hook-enabled worker is a separate measured case. An opt-in candidate
also reproduces the successful exact-pointer rule:

```bash
bash tests/forge-orchestrator/native-probe.sh native-worker \
  --directory .dev/forge-p1/YOUR_PACKET \
  --audit .dev/forge-p1/YOUR_PACKET/runtime-audit-ACTUAL_ID/report.json \
  --topology primary-nested --protect-git-pointers
```

This selects a named native permission profile with exact read-only rules for
the audited absolute `.git` files, network disabled and approvals never. It does
not change Forge launch flags, protect future pointers, or qualify rename/unlink.
Without the flag, the baseline nested-pointer failure remains reproducible.
Both successful native invocations and standalone primitives still exit 2;
the harness deliberately has no automatic whole-P1 success/admission result.

### Earlier runtime/workflow measurements

The runtime continuation retains the 145 canary checks and expands the copied
package to 121 files. Native Codex execution denies all 2,088 inventoried
package/runtime/pointer write-opens from primary and linked roots, with successful
workspace controls. The primary-with-nested root FAILS: its nested worktree's
actual `.git` pointer can be opened for write. No pointer bytes were changed.
Topologies are qualified separately; nested layouts are not silently admitted.

The bare-Python project-import defect is addressed in the fixture runtime by
`-I -S -B`, protected pure-Python PyYAML, pinned tool wrappers and a whitelist
environment. This is not a production runtime installer. The audit inventories
stdlib/native dependencies and explicitly lists platform-cached libraries; it
does not certify all possible Forge execution paths or alias replacement.

```bash
bash tests/forge-orchestrator/native-probe.sh prepare --in-worktree --runtime \
  --directory .dev/forge-p1/runtime-trial-01
bash tests/forge-orchestrator/native-probe.sh runtime-audit \
  --directory .dev/forge-p1/runtime-trial-01
# Use the actual report path printed above, not a guessed audit ID.
bash tests/forge-orchestrator/native-probe.sh native-worker \
  --directory .dev/forge-p1/runtime-trial-01 \
  --audit .dev/forge-p1/runtime-trial-01/runtime-audit-ACTUAL_ID/report.json \
  --topology primary
```

Both audit commands retain each invocation separately. Native execution requires
the exact successful tool command, unchanged inputs and a real positive control;
the model's answer alone is never evidence. Exit 2 still means P1 INCOMPLETE.

`host_probe.py` provides invocation-owned tmux fixtures for attended work. It
never selects the default server. Do not send its shell `command()` into a pane
running a native client. Use `binding(topology, index)` for a freshly observed
native-host command binding: tmux's displayed `$N` session ID must become numeric
`N` in the `TMUX` environment value. It validates the owned socket, session and
actual pane ID instead of assuming pane index equals pane ID.
The binding also carries the observed `TMUX_SESSION` name for contained worker
asks whose native sandbox denies querying the tmux socket. This routing hint does
not replace the bridge's identity and delivery validation.

The workflow runtime also includes pinned `basename`, `cp` and `mktemp`, plus a
P1-only `shasum -a 256 [FILE]` shim using the isolated Python runtime. This avoids
adding Perl's runtime/import closure. Other hashing modes and multiple inputs
are deliberately unsupported; this is not a general system utility replacement.

The earlier `evidence/2026-09-14-runtime.json` covers native host identity/approval,
child policy and loading transitions. Its workflow statuses are historical.

The subsequent `evidence/2026-09-14-workflow.json` records native-host dispatch,
digest retry and question/recovery work, including the local `forge ask` fix.
It supersedes those workflow statuses, not the remaining loading, authority or
hook gates. P1 is still incomplete; the native host was not worker-contained.

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

P2 request implementation progress and the versioned spawn-to-populate transport
are documented in [`p2-status.md`](p2-status.md). Codex host admission remains
unavailable until the protected P2/P3 bindings exist. The new request tests do
not turn P1's experimental profiles into production authorization.

```bash
bash tests/forge-orchestrator/run.sh
```

These tests use temporary directories, synthetic subprocess results and an
unsandboxed execution of the **canary fixture only** to check that allowed
protected writes are detected as failures. They never launch a native provider
and never count as native acceptance. The runner discovers every `test_*.py`,
including host binding, hook observer, and experimental worker-profile tests.
