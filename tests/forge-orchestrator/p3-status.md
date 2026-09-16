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
  probes include owner-writable canaries, create/overwrite/rename/unlink and
  symlink-redirection attempts, and workspace positive controls. Both package
  and protected authentication input directories receive canaries.
  Nested registered-worktree layouts remain unsupported.
  Invocation-owned canaries are removed even on interruption.
- Stable per-root, private native authentication namespaces survive package
  restaging. `auth --package PATH` invokes the native login UI without reading,
  copying or capturing credentials. macOS gets an inventoried keychain-locator
  alias, not a copy of the credential store. Ambient HOME remains isolated.
- Native instruction/configuration namespaces, including added empty directories
  and bundled system skills, participate in identity and drift checks. Native
  discovery aliases refuse. Codex's auxiliary code-mode executable and the
  native sandbox executable are inventoried, together with rg and zsh.
- Codex disables ambient hooks/plugins/shell snapshots and host skill discovery;
  all registered project roots are explicitly untrusted. Its native config API
  verifies those effective values. Claude uses safe mode and default permissions.
  P4 still owns provider-aware native attention hooks.
- `qualify --native` includes real native loading harnesses, with tool-output
  coverage checks for the entire protocol and adapter. Codex uses its native
  app-server lifecycle; Claude uses native Read and requires an observed compact
  boundary. Unit tests cover evidence interpretation, not successful native model
  execution. The copied qualifier selects its pinned worker rather than PATH,
  refuses a different worker, and retains reports when final integrity drifts.
- Codex child qualification now checks native spawn/follow-up/join ordering,
  completed child-owned answers, separate A/B results, fresh C synthesis and
  child-side disk reads for a path-only digest. Parent summaries, timed-out joins,
  stale answers and premature synthesis fail. These are primitive probes, not
  P5/P6 full pipeline acceptance; authenticated execution is still unverified.
- A P2 candidate/conformance producer requires every admission check and protected
  raw-evidence hashes. The host rechecks capability evidence on later admission,
  so changed transcripts invalidate the qualification. Publication never registers
  a profile. Synthetic schema tests explicitly do not establish native proof.
- Utility write-denial results are now named `worker_containment`, separate from
  `native_policy`. A successful sandbox utility cannot certify model-led policy.
- The Codex model-led policy runner requires one exact native command execution,
  a successful exit, a fresh nonce, workspace-positive controls and the digest of
  every expected denied mutation. Actual uncontained execution fails its test.
  It runs separately for primary and linked roots; utility evidence is not reused.
- Native duplicate-skill probes now cover fresh, changed and removed decoys for
  both provider adapters. They require fresh candidate reads and reject decoy
  reads or marker contamination. Existing assets and registered profiles refuse
  the disposable overlay. Descriptor-relative creation/cleanup resists parent
  symlink swaps and preserves concurrently changed files.
- Claude's empty MCP argument now uses `{"mcpServers":{}}`. The installed native
  CLI rejected the previous `{}` before starting a model turn. A regression test
  covers the required shape.
- Claude loading and duplicate probes now use a persistent native stream. Fresh
  entry, reinvocation, compaction and decoy changes share one process/session;
  resume and restart use separate processes and check session identities.
  Transport cleanup also covers failed launches and background descendants.
- Claude child verification matches native task starts, forwarded child-owned
  results, SendMessage generations and completion notifications. It rejects
  missing/early joins, changed task identities, cross-contaminated prompts and
  parent-produced digest reads. This runner is not yet authenticated evidence.
- Concurrent legacy/candidate verification now has a runner. Both original
  native processes must stay alive through interleaved fresh reads; wrong-entry
  reads fail. Global legacy file hashes and symlink targets must remain unchanged.
  Only that read-only legacy client uses normal HOME; candidate isolation is
  unchanged. `--legacy-model` explicitly selects its model.
- Copied helpers now pin the actual macOS Git executable instead of Apple's
  dispatcher. Its helper directory, logical aliases and resolved file bytes
  participate in staging and drift detection. A static audit records remaining
  script/configuration/execution gaps as unsupported, never as native proof.

## Deliberately Not Admission

The prerequisite report schema is `forge-orchestrator-capability/1`, not P2's
`forge-orchestrator-qualification/1`. Current native reports advertise an empty provider set.
It cannot by itself register a qualified profile. The authenticated load/reload
and child, model-policy, duplicate and concurrent legacy harnesses have not run
successfully against an authenticated candidate yet. Claude effective configuration
and model-policy, management/hook and complete runtime qualification remain unfinished.
The new producer is gated on all these
checks, so it does not currently publish a qualified candidate.
Do not relabel not-run checks as pass or use synthetic tests as admission evidence.

The isolated Codex configuration uses OS keyring credentials only, never copied
auth.json or inherited API keys. The protected keychain locator now works, but
both private provider namespaces report logged out. A native Codex device login
was started and expired after 15 minutes without authorization. Claude's native
login was inspected and cancelled without entering a code. No credentials were
read, copied or stored by the probe controller. No authentication process remains
running. Do not restore ambient configuration to make the checks pass.

In this worktree, start a fresh native login with:

```sh
python3 -B .dev/forge-p3/continue-native.py auth codex
```

Use `auth claude` for the other provider and `qualify codex` or `qualify claude`
after login. The helper stages/verifies current copied bytes and stays in this
worktree. Native first startup can materialize bundled skills; that correctly
invalidates an earlier discovery snapshot and requires restaging. No permission
restart or global installation is needed.

A separate read-only legacy Claude streaming experiment successfully read the
installed global `commands/forge.md` through the native Read tool. The same
process's second turn received a provider API refusal (`reasoning_extraction`),
so it did not establish reread or concurrent coexistence. Raw exploratory output
is in `.dev/forge-p3/legacy-stream-a89a9bf8a27c45099c7bfdcf96f33115/`.
This is not protected admission evidence and no workaround was attempted.

## Remaining P3 Acceptance

1. Complete the pending native logins and verify credential persistence through
   restaging without widening worker/child authority or changing existing sessions.
2. Run authenticated fresh/duplicate/reinvoke/compact/resume/restart loading and
   native proposal/digest/local-QA child qualification on
   these exact copied bytes, including legacy/candidate concurrent rereads.
3. Finish full runtime/alias/implicit-input closure auditing, including native
   helper subprocesses, Python startup/cache inputs, provider auxiliary binaries,
   HOME input additions and direct/indirect control-entry approval behavior.
4. Exercise the protected P2 candidate/conformance producer only after real native
   management, hook transport, worker policy and topology evidence passes.
   Finish Claude effective-configuration/model-policy and management/hook runners.
   Prove broker startup/status/handshake/reuse against the full packaged closure.
5. Re-run all P3 acceptance negatives (including stale daemon, source/alias
   replacement and newly introduced native discovery inputs) plus existing
   lifecycle/callback regressions before declaring P3 complete.

Package staging and fixture tests are not replacements for these requirements.

## Evidence

`evidence/p3-checkpoint.json` records the latest package-bound native prerequisite
result. Both primary and linked probes tested 2,873 write-open targets and 525
mutation attempts apiece. All were denied and workspace positive controls passed.
Codex's effective native configuration also passes. Authentication remains
unavailable; the report still says `admission: unsupported`. The runtime audit
identifies 14 Git scripts with unqualified interpreter/include dependencies, plus
the remaining repository-config and native subprocess-closure requirements.

Latest source-stable broader run recorded under
`.dev/forge-p2/requests-1789595569370194000/` ran 197 orchestrator tests
(195 passed, two opt-in native skips), including 25 package tests and 26 native
trace/fixture tests. All 71 start checks, 31 spawn checks and hermetic worker policy passed.
The runner's dedicated tmux server is stopped. Both candidate authentication
statuses were rechecked and remain logged out. No new login was started and no
login or model process remains running. Local `main` is already merged.

Prior checkpoint regression evidence: P0 contract suite: 60 passed. Watcher: 429 passed.
Full bridge rerun: 558 passed, zero failed or not-run, recorded in
`.dev/forge-p3/validation-1789499850198176000/forge-bridge.log`.
The source-stable `.dev/forge-p2/requests-1789499925635407000/report.json` records
157 orchestrator tests (two opt-in native skips), 71 start checks, 31 spawn checks
and the hermetic worker-policy suite passing. Native skipped checks remain
unsupported, not qualified. Full bridge/web aggregate results are retained under
the worktree-local `.dev/forge-p3/validation-*` directories; use the latest
completed report, not an earlier run taken during source edits.
