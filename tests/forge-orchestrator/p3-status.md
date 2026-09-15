# P3 Status

P3 is **in progress, not complete**. This checkpoint implements protected asset
staging and fail-closed prerequisite measurement. It does not enable a production
Codex or Claude orchestrator profile. P1 already established that Codex can
orchestrate; the remaining work is production qualification, not feasibility.

## Implemented

- One canonical protocol with explicit Claude/Codex native child adapters and
  a Codex entry. Local proposal renders constraints and frame challenges;
  local QA adapters preserve the gated exception and infra-lock protocol.
- Closed provider/stage/skill inventory and complete project-agnostic digest
  inventory, including fix helpers and local proposal. Unsupported worker
  routes fail rather than loading the other provider's skill. Fix QA outputs
  live under `.dev/qa/{slug}`; upstream fix inputs remain under proposals.
- Candidate dispatch checks assets before reset, supersede or pending creation.
  Digest errors retain DONE and callback identity, publish no partial path,
  and return nonzero. Retry renders/children without redispatch or consumption.
- Installer reports all drift (#86), verifies absent-in-both source/install
  assets, preserves existing bins/skills/commands/configuration, and uninstalls
  only unchanged owned assets, including preserving added empty directories.
  Ownership updates are locked. Real-home uninstall refuses live native clients.
- Explicit `install.sh --stage-candidate --root ROOT --provider codex|claude
  --model MODEL` creates a copied, content-addressed candidate under the root's
  protected common-Git store. It does not update global discovery, bin links,
  legacy copies, live panes, registrations or production defaults.
- Distinct discovery document names the exact protected canonical body and
  adapter. Copied control helpers pin paths, reject inherited startup/override
  environments, isolate Python imports, vendor pure-Python YAML and inventory
  Python/native dependencies and aliases. P2 host admission verifies copied
  package inputs when a profile references package.json. This is integrity
  machinery, not a claim that the entire runtime has been natively certified.
- Runtime inputs participate in candidate identity so upgrades can stage a new
  candidate without deleting the old one. Source-copy races refuse and roll back
  only the invocation's unpublished directory. Native OS identity is independent
  of shell PATH. Shell helpers use an actual `env -i` re-exec, including removal
  of exported functions; watch registration names the executable public entry.
- Copied broker startup and handshake attest the full package inventory.
  Expected receipt hashes are checked before importing a verifier or helper.
  Real isolated-daemon tests cover fresh startup, authenticated handshake,
  unchanged reuse, changed-helper refusal and pre-import execution sentinels.
- `forge-orchestrator-package qualify --package PATH` records protected,
  package/binary/topology-bound **prerequisite** measurements. Native utility
  write-open probes include owner-writable canaries and a positive workspace
  control. Nested registered-worktree layouts remain unsupported.
  Invocation-owned canaries are removed even on interruption.

## Deliberately Not Admission

The prerequisite report schema is `forge-orchestrator-capability/1`, not P2's
`forge-orchestrator-qualification/1`. It always advertises an empty provider set.
It cannot be used to register a qualified profile. The current qualifier does
not yet implement the model-led load/reload/child, management/hook and complete
runtime qualification runner, or produce a P2 candidate/conformance pair.
Do not relabel these not-run checks as pass or wire this report into admission.

The isolated Codex configuration uses OS keyring credentials only, never copied
auth.json or inherited API keys. A native login-status check in the private HOME
fails because no default macOS keychain is available. Keeping private CODEX_HOME
but restoring ordinary HOME locates the keychain and reports not logged in.
That experiment is diagnostic only; ambient HOME is not an approved closure.
A temporary private-HOME `Library/Keychains` alias also located the keychain
without restoring ambient HOME, but the private CODEX_HOME still had no login.
The diagnostic alias was removed; no authentication flow has been started.
No credentials were read, copied or stored by these probes. Native authenticated
verification remains blocked until a deliberate isolated authentication path is
implemented and verified. Do not solve it by silently exposing file credentials
to workers or by restoring ambient hooks/configuration.

## Remaining P3 Acceptance

1. Complete native authentication/configuration isolation without widening
   worker/child authority or changing existing sessions.
2. Implement and run authenticated fresh/duplicate/reinvoke/compact/resume/
   restart loading and native proposal/digest/local-QA child qualification on
   these exact copied bytes, including legacy/candidate concurrent rereads.
3. Finish full runtime/alias/implicit-input closure auditing, including native
   helper subprocesses, Python startup/cache inputs, provider auxiliary binaries,
   HOME input additions and direct/indirect control-entry approval behavior.
4. Produce the protected P2 candidate/conformance pair only after real native
   management, hook transport, worker policy and topology evidence passes.
   Prove broker startup/status/handshake/reuse against the full packaged closure.
5. Re-run all P3 acceptance negatives (including stale daemon, source/alias
   replacement and newly introduced native discovery inputs) plus existing
   lifecycle/callback regressions before declaring P3 complete.

Package staging and fixture tests are not replacements for these requirements.

## Evidence

`evidence/p3-checkpoint.json` records the latest package-bound native prerequisite
result. Both primary and linked write-open probes tested 2,721 protected targets,
including writable canaries, with zero opened targets and successful workspace
positive controls. The report still says `admission: unsupported`.

Focused package tests: 11 passed. P0 contract suite: 60 passed. Watcher: 429 passed.
Full bridge rerun: 558 passed, zero failed or not-run, recorded in
`.dev/forge-p3/validation-1789499850198176000/forge-bridge.log`.
The source-stable `.dev/forge-p2/requests-1789499925635407000/report.json` records
157 orchestrator tests (two opt-in native skips), 71 start checks, 31 spawn checks
and the hermetic worker-policy suite passing. Native skipped checks remain
unsupported, not qualified. Full bridge/web aggregate results are retained under
the worktree-local `.dev/forge-p3/validation-*` directories; use the latest
completed report, not an earlier run taken during source edits.
