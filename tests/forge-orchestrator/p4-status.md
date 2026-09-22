# P4 Status

P4 implementation is **complete under the user's local-only acceptance scope**
as of 2026-09-22. Protected candidate hook wiring is implemented for both
providers. Live model-led event attribution remains deferred, not passed.

## Implemented

- Read-only `forge-orchestrator-host observe` validates the protected binding,
  process/seat identity and optional event pane. Legacy Claude attribution requires
  the existing native process and five-pane receipts. Invalid, drifting, racing or
  unavailable bindings report `unknown`; aliases and environment stamps do not
  establish a provider.
- Health, stall and usage select the existing Claude/Codex parsers from that
  observation. A provider/incarnation change cannot retain the old host's usage.
  Host stall classification never applies worker pending/completion rules.
- Dispatch, host ask and native attention records carry additive provider fields.
  Host hook records correlate available launch, incarnation, native session and
  turn identity. Worker or child Stops cannot write canonical host completion;
  mismatched adapters/turns are ignored. Both transcript formats are supported.
- Permission hooks produce no stdout and leave native approval decisions alone.
  Malformed payload fields are inert. No automatic hook trust is granted, and a
  dispatch record alone cannot establish native hook emission.
- Watcher/task/SwiftBar readers display Claude, Codex or unknown, preserve legacy
  records, and use the canonical orchestrator role. Pane 0 remains handoff-only;
  worker reset/hygiene aliases remain excluded.
- Local regression fixtures use repository assets, physical temporary paths,
  and current tmux layout receipts instead of installed-user prose or stale layouts.

Existing parser tables and native adapter event mappings are reused; their
configuration files require no parser changes for these observation features.

## Protected Hook Wiring

- Candidate staging renders provider-specific commands pointing to the copied
  `forge-cc-hook`, with quoted absolute paths, and inventories the generated file.
  Codex receives those definitions as native CLI configuration; Claude receives
  an explicit settings file. No global or project hook file is imported.
- Codex hooks are enabled but initially untrusted. Native `/hooks` approval remains
  required; there is no bypass flag, automatic trust write or permission grant.
  Only native `hooks.state` enable/disable and SHA-256 trust records may vary in
  the private Codex config. Hook code, all other config fields and package inputs
  remain pinned. A changed command loses its native trust.
- Broker startup attestation uses the same narrow trust-state identity. Native
  trust review does not invalidate a running broker; unrelated configuration
  changes still refuse. No executable file can claim the trust-state exception.
- A new, binary-scoped `authentication-p4` namespace leaves existing candidates
  and credentials alone. Staging initializes bundled Codex skills with networking
  denied and no model turn, then freezes their inventory before native startup.
  This prevents normal native initialization from invalidating the package.
- Claude no longer uses safe mode, which suppresses even explicit hooks. It keeps
  default/manual permissions, empty user/project/local setting sources, disabled
  slash skills and implicit instruction/memory discovery, plus strict empty MCP
  configuration. Its native qualification checker understands this explicit policy.
- Generated commands were executed from copied packages against local tmux
  fixtures for both providers. Prompt, permission, tool-resolution and Stop
  events work; permission stdout stays empty and worker Stops remain separate.

Actual native attribution is deferred under the user's no-additional-model-call
constraint, not passed. P4b's integrated preview and P5/P6 remain separate phases.
No production provider switch or global installation was performed.

## Local Verification

On 2026-09-21: observation/event/parser/board tests 17 passed, local tmux host
launch tests 20 passed, assets/installer tests 9 passed, copied-package tests 32
passed, command-center tests 205 passed, watcher tests 429 passed, bridge tests
558 passed (zero failed or not run). Shell syntax and `git diff --check` pass.
Logs are in `.dev/forge-p3/p4-*-20260921*.log` in this worktree. These are local
fixtures and process checks, not authenticated model or native-hook acceptance.
No additional model calls, API billing, sign-ins or external services were used.
Local `main` was merged (already up to date), and the bridge fixture's owned tmux
server was stopped. Unrelated session settings remain untouched.

The 2026-09-22 closeout adds offline native Codex hook discovery and hash-trust
checks, copied-package event execution, configuration-drift refusal tests and
Claude policy fixtures. The native discovery check denies all network access
and invokes only configuration RPCs, never a thread or model turn. Its trust
write affects only its disposable fixture. Logs are `.dev/p4-*-20260922*.log`.

All 127 focused closeout tests passed: copied package/runtime (36), native
trace/policy fixtures (29), host launch (20), observation (17), broker runtime
(16), and profile admission (9). A targeted repeat also verifies the source
client's system-Python TOML compatibility. Python syntax and `git diff --check`
pass. Local `main` is already merged; test-owned brokers and tmux servers were
cleaned up. No model run, sign-in, paid API call or production profile change
was needed to finish P4 implementation.
