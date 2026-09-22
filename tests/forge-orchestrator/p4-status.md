# P4 Status

P4 is **in progress**. The provider-aware observation and attention-record work
is implemented. Protected candidate hook activation remains implementation work;
it is not merely a missing live test and is not silently reassigned to P4b.

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

## Remaining P4 Work

`bin/forge-orchestrator-package` still stages Codex with hooks disabled and Claude
with safe mode and empty explicit settings. Therefore these updated adapters do
not yet establish attention emission from a protected candidate launch.

Wire only the intended copied attention hooks into the protected native launch
configuration, preserve the existing manual trust/approval policy and immutable
package identity, and cover the configuration/adapter path with local fixtures.
Do not copy credentials or global hooks, enable a production profile, or weaken
admission checks to make the fixtures pass. Existing candidates must remain intact.

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
