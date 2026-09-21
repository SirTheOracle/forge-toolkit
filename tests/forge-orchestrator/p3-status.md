# P3 Status

P3 implementation is **complete under the revised local-only acceptance scope**
approved by the user on 2026-09-21. P4 implementation can begin. Authenticated
live-model validation is explicitly deferred, not passed; this is not a claim
that the complete provider-selection feature is production-ready.

## Accepted Scope

The user approved closing P3 with the implemented feature changes and focused
local regressions, without additional model runs, API billing, new services, or
separate account sign-ins. This supersedes the original plan's requirement to
finish authenticated qualification before beginning P4. Historical capability
reports retain their actual not-run/unsupported results. Production admission
checks have not been bypassed, and no production provider profile is enabled.

P3 owns reproducible protected assets, explicit native loading, primitive child
adapters, installer/digest correctness, protected runtime inputs, and local
package-bound qualification. P2's management boundary and builtin launch-request
transport must remain valid with the copied package.

P4 owns provider-aware observation and attention hooks. P4b owns the integrated
dispatch/ask/callback/digest-retry preview. P5/P6 own full workflow and rollout
acceptance. None is silently required to pass a P3 primitive test.

P1 explicitly measured different host policies. Codex uses workspace-write and
on-request approval. Claude uses default/manual approval; approved Claude host
and child commands are not claimed worker-contained. P3 does not impose stronger
Claude child sandboxing as an invented parity prerequisite. Contained-worker
protection and model-led host policy remain distinct checks.

## Implemented

- One canonical protocol, thin native adapters, explicit provider/stage/skill
  inventory, complete tracked digests, corrected QA paths, and local proposal
  constraint/frame-challenge inputs.
- Preflight before dispatch/reset/pending mutation. Digest failures preserve
  truthful DONE and callback identity; digest-only retries do not redispatch.
- Full installer drift reporting, absent-in-both detection, repeat-install and
  safe uninstall, with customizations and existing bound copies preserved.
- Distinct content-addressed candidates and explicit discovery entries. No
  replacement of global Claude assets or existing conversations.
- Copied runtime/control helpers, isolated Python, pinned native dependencies,
  protected broker startup/handshake/reuse, and package/alias/drift checks.
- A closed copied Git helper directory for guarded builtins and HTTP(S) fetch.
  Unneeded upstream Perl/shell helpers are not reachable through GIT_EXEC_PATH.
- Presence and absence of Python startup archives, global shell startup, and
  managed provider configuration participate in identity. Unqualified custom
  global startup inputs still refuse; they are not automatically certified.
- Runtime measurement executes real copied Python, Git, router, host and broker
  entries under hostile HOME/PATH/Python/Git/helper overrides.
- Read-only `forge-start --inspect-request --populate-existing SESSION` exercises
  the actual versioned builtin parser without creating panes or admitting a host.
- Dedicated `orchestrator-profile probe` exercises the same management admission
  and protected write boundary without registering/disabling a profile.
  Generic-router calls refuse. Direct, Python-interpreter and shell-wrapped
  management calls are tested under the worker policy.
- Management prepared from a candidate binds that original package's complete
  dependency closure. Its dependencies are rechecked before loading the verifier
  and included in subsequent candidates' worker write-denial measurements.
- Native runners cover explicit loading/reloading, duplicate discovery, A/B
  follow-up and C synthesis, path-only child digest, concurrent unchanged legacy
  reads, Codex model-led policy, and Claude native configuration/manual refusal.
  Claude refusal requires matching native tool and permission-denial records;
  model claims and ordinary command errors cannot establish permission evidence.
- All required checks must pass before the P2 candidate/conformance producer
  publishes anything. No force-qualification flag or synthetic admission path
  was added.

## Local Closeout Verification

On 2026-09-21, all 93 focused local tests passed with no failures or skips:
assets/installer/digest recovery (9), copied package/runtime (32), native trace
parsers and adapter fixtures (28), Git protections (10), and broker runtime (14).
Hard Rule 24's skill/agent copies also compare identically. Commands and log
hashes are recorded in `evidence/p3-checkpoint.json` under `local_closeout`.
No additional model calls, account sign-ins, or external services were used for
this closeout. Test-owned broker/tmux processes were cleaned up.

Implementation remains at c837d4b; this closeout changes documentation/evidence
only. Local main was merged (already up to date). Unrelated user files remain
untouched. No further runtime changes were needed for the revised P3 scope.

## Earlier Evidence

The protected reports indexed in `evidence/p3-checkpoint.json` cover both
candidate providers and both primary/separate-linked worktrees. Package
integrity, runtime measurement, builtin request transport, topology and actual
worker containment pass. Management's operator positive control and all four
worker denial forms pass for both topologies.
Each provider/topology denied all 3,225 write-open attempts and 930 canary
mutations, with successful workspace controls.

In those historical reports, the overall management check remains not-run because
the native host side was not measured. Codex effective configuration passes.
Both isolated native authentication namespaces were unavailable. Subsequent
sign-in attempts were canceled on 2026-09-21; no credentials were copied and no
login process remains. Authentication is no longer a P3 implementation blocker.

Standalone package tests: 32 passed. Native trace/parser tests: 28 passed.
Explicit native management sandbox tests: both primary and linked passed.
The earlier broader run at
`.dev/forge-p2/requests-1789686415046213000/report.json` ran 215 orchestrator
tests (213 passed, two opt-in native skips), all 71 start checks, all 31 spawn checks and
hermetic worker policy. Final full regression evidence is under
`.dev/forge-p3/validation-1789686859134375000/`: bridge 558 passed, zero failed or
not-run; embedded web-bridge 32 passed; orchestrator 217 run (215 passed, two
opt-in skips); installer/drift/uninstall passed. The opt-in management tests were
then run explicitly: both passed. The owned tmux server is stopped. Both retained
candidate packages still verify after the full run. Log hashes are in the index.
The bridge emitted its pre-existing missing generated technical-reference warning.

## Deferred Live Validation

Candidate loading, reinvocation/compaction/resume/restart, real child execution,
duplicate discovery, model-led policy/management, Claude native initialization,
and concurrent legacy reads have not passed authenticated candidate acceptance.
Local parser/fixture tests do not establish those live behaviors. Do not run
native qualification or initiate sign-ins without a new user request.

P4 owns provider-aware observation/attention; P4b owns the integrated preview;
P5/P6 own full workflows and rollout. These remain subsequent implementation
phases, not reasons to reopen P3 or expand its test infrastructure. The existing
production admission path still requires live evidence, so this closeout alone
does not enable Codex orchestration in a production session. Any change to that
admission policy must be explicit, not fabricated evidence or a silent bypass.

No global installation, profile registration, live-session migration, or provider
switch has been performed. All work remains in the current Forge worktree.
