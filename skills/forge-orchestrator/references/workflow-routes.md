# Build and Fix Routes

## Selection Before Assignment

Run the protected `forge route --root {project_root} --slug {slug} --stage
{stage}`. The command is read-only: it neither assigns work nor probes a model.
It filters reasoning tier, advertised worker skill, and capability lane before
considering availability. Observe eligible panes through bridge health/read;
repeat `--unavailable WORKER` for each observed unavailable pane. Unknown
availability is not permission to launch into a busy pane.

Report `requested_worker`, `selected_worker`, and `reason`. Carry them to
dispatch as `--requested-worker`, `--worker`, and `--route-reason`. Only
`status: selected` exits zero. `wait`, `refused`, and `local-prerequisites`
exit nonzero and authorize no dispatch. The route output is not a capability
grant; ordinary identity, asset, policy, lock, constraints, pending, and
delivery guards still apply at dispatch.

Use `--worker` for an explicit user request. Never replace an ineligible explicit
request. Use `--assigned-worker` after assignment: an unavailable assigned worker
halts/waits. Do not mark a worker unavailable merely because its lane is
ineligible. Capability restrictions alone do not open the local-QA exception.

Current contain-policy build selection:

| Stage | Execution |
| --- | --- |
| proposal | Local isolated native A/B/C, not worker-dispatchable |
| review | codex-a; wait if unavailable |
| incorporate / impl-review | claude-opus preferred among eligible HIGH workers |
| implementation | codex-a, then eligible claude-opus before assignment |
| coding / qa-fix | claude-sonnet on reviewed-host |
| qa / qa-retry | claude-sonnet on reviewed-host |
| verify | claude-opus on reviewed-host, different from latest QA worker |

All fix stages are HIGH and worker-dispatched. Default to eligible claude-opus;
Codex A is an option only for stages with Codex worker assets and an eligible
lane. A Codex host can coordinate Claude workers without acquiring their skills
or permissions. Never load a Claude-only fix skill as a Codex native child.

## Local Exceptions

For proposal, require native isolated A/B generation, independent critique and
follow-up, fresh C synthesis, disk contracts, local pending log/close, and a
path-only digest child. Missing primitives halt; neither a single-context
substitute nor a shell-launched model is an equivalent implementation.

For local build QA/QA-retry, require all of: Codex B observed unavailable, no
eligible throughput worker available, native isolated live-QA children under
the selected host policy, and the exact session/slug/stage infra lock held.
`local-prerequisites` merely lists these checks; it does not certify them.
Use the provider adapter. Render before logging, hold the lock through all
children and asks, close the local pending on every terminal exit, confirm no
unsafe process remains, release, then digest. Never send a worker callback for
local work. Never generalize this exception to fix stages.

## Workflow Decisions

Keep the canonical sequence and conditional branches unchanged for both hosts:

- Build: proposal, review, conditional incorporation, implementation, impl-review,
  coding, QA, conditional qa-fix then qa-retry, verify, bridge verify-decision
  and finalize. Stop for the operator's PR instructions; never open a PR.
- Fix: both `forge-fix-pipeline SLUG` and `/forge fix-pipeline SLUG`
  use the same packet and HIGH routes. Optional reproduce precedes investigation;
  re-tier and budget checks precede planning; review may authorize one revision.
  Code precedes QA. Stop for operator verification and PR.
- INSUFFICIENT EVIDENCE, SPEC FIDELITY REJECT, TIER-REDUCED, exhausted budget,
  missing artifacts, or STILL REPRODUCES halt. Never upgrade these to HIGH/0.
- Allow only the canonical one automatic investigate/plan alternation and one
  plan-review/revise cycle. Further cycles need an operator decision.
- A fix-QA correction requires operator authorization and HIGH fix-code before
  fix-qa-retry, never the throughput build qa-fix route or a local correction.
- Retry QA reads the bridge snapshot (`prior-issues.md` or `prior-fix-issues.md`)
  before replacing its canonical report. Every omitted prior major/critical
  remains open. Digest-only retries read the same snapshot without dispatch,
  reset, pending creation, or worker rerun.

## Recovery

Use delivery/session/incarnation/root identity, never artifact mtime or a generic
Stop event, for terminality. Queued, stale, foreign, or superseded callbacks do
not authorize advancement. Keep locks during asks, BLOCKED, waits, pause, stalled
turns, or dead-worker repair. Use owned continuation/supersede/park operations;
unknown legacy rows remain unadopted and require explicit proven log-response
recovery. Do not force-reap a delivery to manufacture acceptance.

After a failed digest, preserve terminal callback identity and retry the digest
from disk. On resume, reload the canonical protocol, adapter, context and actual
artifacts before choosing the next stage. Completed work is not redispatched.
Missing/contradictory commit evidence, including an unevidenced no-change claim,
blocks rather than causing a fake commit or an ERROR-to-DONE conversion.

Local regression results establish these contracts, not native model behavior.
Unmeasured child capabilities and external defect gates remain explicit P6
qualification limits; do not silently enable a provider profile from fixtures.
