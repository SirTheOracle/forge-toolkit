# P5 Status

P5 implementation and local acceptance are **complete as of 2026-09-23**.
This phase implements provider-neutral build/fix routing and local-fallback contracts.
Acceptance follows the user's local-only scope: no paid API/model runs, account
sign-ins, browser testing, external environments, global installation, or live
session migration. Native full-workflow qualification is not inferred from
local fixtures, and production profile admission remains unchanged.

## Delivered

- Read-only `forge route` uses reasoning tier, advertised worker-provider
  support, capability lane, then observed availability. It reports requested and
  selected workers and the reason. `forge codex-lane` shares the same lane logic.
- Under contain, QA/QA-retry select claude-sonnet and verify selects claude-opus
  before assignment. Explicit ineligible requests refuse. An unavailable
  assigned worker waits; it is never silently replaced or tier-downgraded.
- Protected dispatch validates the route before reset, supersede, pending or
  send. Legacy dispatch also refuses an ineligible capability lane before those
  effects. Verify excludes the latest QA worker and refuses missing, malformed,
  ambiguous or pending QA history rather than selecting an older closed row.
- Both hosts share the full canonical build/fix protocol and worker assets.
  Fix stages remain HIGH and worker-dispatched; existing Claude workers are
  sufficient. The fix-QA correction path requires authorized HIGH fix-code, not
  the build pipeline's throughput qa-fix route.
- Local proposal still requires native isolated A/B/C plus critique/follow-up
  semantics. Local build QA additionally requires Codex B unavailable, no
  eligible throughput worker available, native live-QA child capability and the
  owned infra lock. Capability absence blocks fallback, never permits local fix
  execution or sequential parent substitution. The selector reports prerequisites,
  not a fabricated capability pass.
- Local QA renders local log-response/ask handling instead of worker callbacks.
  Protected local pending creation checks the exact infra lock. Worker QA retains
  delivery callbacks. Both adapters preserve close, safe terminal release, then
  path-only digest ordering.
- Real retry dispatch/local rendering preserves prior findings before report
  overwrite. Build/fix retry prompts and digests consume `prior-issues.md` /
  `prior-fix-issues.md` and correction evidence. Missing prior reports refuse;
  omitted critical/major findings remain open. Digest-only retry leaves the
  snapshot and worker delivery untouched.
- Conditional stop rules remain: insufficient evidence, spec rejection,
  re-tier/budget halt, bounded revision/alternation, missing artifacts,
  STILL REPRODUCES, and operator verification/PR stop. Recovery retains exact
  callback ownership, nonterminal locks and explicit unknown-row refusal.
- Regression checks now recognize P3's constraint-bearing local proposal and
  structured verify YAML. The fix-runner checks the tracked protocol/guide even
  when the explicitly ignored, private generated technical reference is absent;
  when present, that document is still checked.

## Verification

Stable-source checks on 2026-09-23:

- Orchestrator suite: 264 tests, 262 passed, two opt-in native skips; no failures
  or errors (`.dev/p5-orchestrator-final.log`). Includes the 22 new P5 routing,
  rendering, retry, lock and refusal tests.
- Bridge suite: 558 passed, zero failed, zero not run
  (`.dev/p5-bridge-final.log`), including callback ownership/repetition, asks,
  blocked continuation, supersede/park, dead-incarnation refusal, reset/send
  ordering, infra-lock terminality, constraints/evidence modes and finalization.
- Adversarial skill contracts: 66 passed, zero failed
  (`.dev/p5-skills-final.log`).
- Fix-runner protocol/real-Git fixtures: 21 passed, zero failed
  (`.dev/p5-fix-runner-final.log`).
- Broker suite: passed, including five Python context tests and local Git,
  replay, lifecycle, queue and ownership checks (`.dev/p5-broker.log`).
- Asset inventory, shell syntax and `git diff --check`: passed. Focused
  assets/installer tests: nine passed (`.dev/p5-assets-final.log`).

The first broad run overlapped source edits: package checks correctly refused
source drift, and a bridge fixture cleanup returned a nonzero status after
publishing its callback. Its failures are retained, not called passes. The
stable orchestrator and bridge repeats above passed. The earlier cleanup failure
did not recur; no callback assertion was weakened to obtain a pass. Logs remain
in this worktree's ignored `.dev/p5-*.log` files.

Local main was merged (already up to date) and remains an ancestor of this
branch. All changes are in this session's worktree. Test-owned processes were
cleaned up by their harnesses; existing sessions and the pre-existing untracked
`.agents/`, `.claude/settings.json`, and `.codex/` were left untouched. No PR was
created.

The route matrix exercises both host adapters' asset/render contracts, all
build/fix worker routes, five rollout policies and explicit invalid choices.
It is not four model-led pipeline executions. Existing bridge tests exercise
real local tmux with shell fixtures, not provider models. No launcher test
attaches to a production pane.

## Qualification Limits

P4b's native integrated preview, actual isolated proposal/QA children, and the
four complete model-led build/fix cells remain unrun under the no-additional-cost
scope. The local-QA child capability is a required precondition, not certified
by the presence of a spawn tool or a successful worker QA test.

The original plan's external defect gates remain prerequisites to a **full
native parity** claim: reset/send/pending ordering (#55), exact delivery-bound
DONE (#57), new/untracked commit accounting (#80), evidenced no-op completion
(#49), unattended stall/dead-worker recovery (#74), and supersede/incarnation
recovery (#42/#61). Existing local regressions preserve the current behaviors;
this phase neither closes those issues nor invents a new no-op terminal result.
Legacy callback compatibility (#56) still cannot establish qualified freshness;
unknown legacy ownership (#73) is not adopted automatically.

P6 must retain those limits when deciding rollout. No native evidence was
fabricated, no failed gate was reclassified as passed, no admission bypass was
introduced, and no production provider profile was enabled.
