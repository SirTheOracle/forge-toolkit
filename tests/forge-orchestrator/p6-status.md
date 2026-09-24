# P6 Status

P6 now includes an explicit **attended local-release implementation**, in addition
to the 2026-09-23 diagnostics and acceptance work. It supports protected local
validation, operator opt-in registration, independent Claude/Codex profiles,
new-session selection and provider-scoped disable. Native model qualification
remains a separate, unchanged opt-in standard; local release does not establish
full model-led workflow parity.

The user's revised scope remains local-only: no paid API/model runs, sign-ins,
browser tests, external environments, global install, or live-session migration.
No production profile was registered or enabled. Existing sessions and Claude's
default remain unchanged.

## Local Release Implementation

- `qualify --local-release` measures package integrity, runtime closure, worker
  containment, management-worker denial, hook transport and topology without
  model calls or sign-in. It emits `forge-orchestrator-local-release/1` only when
  all local gates pass; failed local validation exits 3 without a candidate.
  Native checks remain `not-run`.
- Registration requires the protected operator entry, explicit `--provider` and
  `--accept-local-validation`. The native qualification path rejects local-grade
  evidence. Raw artifacts, management closure and package/provider/tool hashes
  are rechecked at every admission, just as for native qualification.
- A root-scoped provider catalog preserves each provider's immutable profile
  and revision. Codex registration/disable no longer replaces Claude. Old
  snapshots remain readable, and migration refuses while old helpers still own
  session resources. First registration also refuses while an unbound legacy
  session owns the root. New sessions default to Claude even after a stopped Codex
  session; ensuring a live Codex session still preserves its provider.
- Launch, ensure and readiness expose `validation=local`. Readiness also reports
  `native_qualification=not-run` and `scope=attended-local-release`; it never
  upgrades full-workflow parity. Model-free fixture launches cover both providers,
  default selection, no hot switch, disabled admission and retained bindings.
- The operator guide contains the complete stage/validate/register/start/disable
  procedure using copied helpers and independent provider revisions. Local
  registration does not waive nested-worktree containment limits or create a
  second same-root owner. Activation of this current live worktree is not part
  of the implementation and has not been performed.

## Verification (2026-09-24)

The source-stable broad run in `.dev/p6-acceptance-20260924/` passed all eight
local suites: orchestrator (290 tests, two intentional native opt-in skips),
bridge (558 checks), broker, skills, fix-runner, launcher, recovery and infra-lock.
No model calls, sign-ins or global installation were performed.

After that run, final changes added first-registration legacy-session refusal,
the local qualifier's failure exit code, their tests and corresponding guide
text. The first 67-test focused run passed 66 tests and exposed a profile fixture
consulting the default tmux server. Giving profile tests disposable sockets
fixed the isolation defect without relaxing the production check. The complete
67-test focused rerun passed against unchanged final source inputs. It covers
host launch/recovery, provider profiles, local release, protected records and
copied-package local publication/CLI behavior.

The eight-suite run was not repeated after those narrow final changes. Exact
source/log hashes and both focused outcomes are retained in
[`evidence/p6-closeout-20260924.json`](evidence/p6-closeout-20260924.json).
These are implementation tests, not production admission or native parity proof.

## Delivered

- `forge orchestrator-rollout --root PATH [--provider claude|codex] [--check]`
  reports the actual protected admission decision, exact verified profile,
  package/protocol/helper and provider identities, binding and refusal reason.
  Normal inspection exits zero; `--check` exits 3 when ineligible. Neither
  invocation starts a model, writes a profile, replaces a pane or grants authority.
- Native eligibility is labelled `registered-prerequisites-only`. Existing native
  conformance covers primitives; the command explicitly reports
  `full_workflow_parity=not-established`. No new bypass or fabricated pass was
  introduced. The launcher and dispatch boundaries still revalidate admission.
- Real local tmux/broker fixtures prove profile disable and protocol drift block
  new delivery registration while retaining active-delivery inspection and strict
  owned-callback reconciliation/replay. Forged callback hashes still refuse and
  leave ownership intact. These tests seed a previously owned delivery and do
  not claim to have executed a model workflow.
- Read-only report tests cover both providers, absent/disabled profiles, changed
  dependencies, explicit provider refusal, stable profile snapshots and no
  silent host switch. A proven terminal boundary permits a fresh host/profile
  without rewriting the prior binding; unconsumed callbacks still refuse.
  Existing launch tests retain omitted-provider behavior,
  same-root ownership, interrupted preparation, terminal handoff and legacy
  session non-interference.
- `tests/forge-orchestrator/acceptance.py` runs eight finite local suites and
  records commands, exits, log hashes and source/tool identities. It checks
  pre/post source stability, disables native-management opt-ins and attachment,
  and emits a distinct local-only report that cannot be registered as conformance.
  Missing, failed, interrupted or source-drifted runs cannot pass.
- README and operator guide document explicit provider selection, root-scoped
  readiness, separate candidate staging, retained operator-entry disable/CAS,
  safe terminal boundaries, callback recovery and the absence of global rollout.
- Acceptance harness repairs keep the ignored private technical reference
  optional, with tracked operator documentation mandatory. The skill harness
  now runs all three Python check blocks on Apple Bash 3.2 and propagates Python
  failures; its old nested heredocs could skip checks while returning success.

## Acceptance Scope

The finite affected-path report maps related regression suites to #55, #57,
#80, #49, #74, #42/#61, #62, #73, #56 and #83/#84/#85/#86/#87/#89. The mapping
does not substitute an aggregate suite pass for an original-defect reproduction
or close any issue. Original-defect requalification remains explicitly recorded
as not requalified.

Unrun native cells remain: integrated attended preview, complete Claude/Codex
build and fix pipelines, actual isolated local-proposal children, native live-QA
fallback and unattended stall recovery. No fixed soak, model/API call, credential
copy, global activation/drain, same-root second owner or autonomous PR is needed
to finish the local implementation. Those exclusions are not evidence for full
native parity.

Current worktree inspection reports `ORCHESTRATOR_NOT_QUALIFIED: operator profile
is absent`. The explicit Codex launch option is implemented, but installation
and local test success alone do not enable it. This status is a rollout decision,
not a filesystem-permission problem and not a request to restart the session.

## Earlier Verification (2026-09-23)

The broad run in `.dev/p6-acceptance-01/` retained stable source/tool inputs:

- Orchestrator: 273 tests, 271 passed, two intentional native opt-in skips.
- Bridge: 558 passed, zero failed or not run.
- Broker: passed, including its Python and shell checks.
- Fix-runner: 21 passed; launcher: 71 passed; recovery: 59 passed.
- Infra-lock: initially 149 passed and one failed because the ignored private
  generated reference was absent. The corrected full rerun passed all 149
  applicable checks; the optional absent document is explicitly not checked.
- Skills: the initial exit-zero/41-pass result was **invalid**: Apple Bash
  skipped two Python blocks. After fixing the harness, both `/bin/bash` and
  Homebrew Bash execute all 66 assertions successfully. A regression injects
  Python exit 23 into each block and proves the suite cannot report success.

After the broad run, final edits were limited to the read-only readiness
terminal-boundary correction, its guide/tests, and the two test-harness repairs.
The full host-launch suite (24 tests), readiness/report suite (eight tests),
infra-lock suite (149 checks) and skills suite (66 checks on each Bash) passed
their focused reruns. The untouched bridge/broker/worker paths were not rerun
again. This is scoped change verification, not a claim that all eight suites
were rerun on the final source. The initial aggregate failure and invalid skill
result are retained, not rewritten as passes.

Exact source checkpoints and log hashes are retained in
`evidence/p6-closeout.json`; raw logs remain in this worktree's ignored `.dev/`.
Asset inventory, shell syntax and `git diff --check` passed. Local `main` was
already up to date and remains an ancestor. All source edits stay in this
worktree; test-owned processes were cleaned up. Pre-existing `.agents/`,
`.claude/settings.json` and `.codex/` were untouched. No PR was created.
