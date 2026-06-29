# Review - Stage Pane Routing Plan Gaps

**Reviewed plan:** `handoffs/handoff-2026-06-28-stage-pane-routing-plan.md`
**Date:** 2026-06-28
**Reviewer:** Codex
**Status:** Needs revision before implementation

## Summary

The plan identifies the two real routing defects: `implementation` may fall
back to `codex-b`, and `verify` is currently routed only to throughput-tier
workers. The proposed direction is sound if the user confirms that `verify` is
high-reasoning work.

Do not execute the plan as written yet. It has several gaps that can leave the
system internally contradictory or make the docs refresh produce stale output.
The highest-risk gap is that the plan remains "prompt/doc only" even though
`forge-bridge dispatch` currently accepts any canonical worker for any stage.
That means the new rule would still be advisory, not enforced.

## Findings

### High - Proposed Hard Rule 22 contradicts itself on `proposal`

Section C1 says:

- high-tier stages include `proposal`
- high-tier stages run only on Opus pane 0 or Codex A pane 2
- high-tier stages are never executed in pane 1
- `proposal` is the sole stage that runs in pane 1

Those cannot all be true. The current SKILL already has a related tension:
`Your Role` says the orchestrator never executes stage work in pane 1, but the
`proposal` stage runs adversarial-proposal inline because Agent Teams requires
local foreground execution.

Recommended revision:

- Define `proposal` as a named local exception before the tier table.
- Do not list `proposal` in the same "HIGH-tier stages run only on pane 0 or
  pane 2" sentence.
- Wording should be closer to:
  - `proposal`: local high-reasoning exception, allowed only in pane 1 because
    Agent Teams is orchestrator-local in Phase 1.
  - dispatched HIGH stages: `review`, `incorporate`, `implementation`,
    `impl-review`, `verify`; these run only on pane 0 or pane 2.
  - all other stage work remains forbidden in pane 1.

### High - Prose-only enforcement does not make routing reliable

The plan keeps scope to prompt/doc changes. Current `bin/forge-bridge`
validation only checks that `--worker` resolves to one of:

```text
claude-opus | claude-sonnet | codex-a | codex-b
```

It does not reject illegal stage/worker combinations. Therefore these would
still render or dispatch unless the orchestrator follows the prose exactly:

```bash
forge-bridge dispatch --slug X --stage implementation --worker codex-b
forge-bridge dispatch --slug X --stage verify --worker claude-sonnet
```

Recommended revision:

- Either add a small `forge-bridge dispatch` stage-worker policy guard, or
  explicitly downgrade the goal to "prompt-level guidance only."
- If enforcement is added, include negative dry-run tests:
  - `implementation + codex-b` must fail
  - `verify + codex-b` must fail
  - `verify + claude-sonnet` must fail
  - `proposal + any dispatch worker` should fail or remain documented as
    non-dispatchable
- Keep positive dry-run tests:
  - `implementation + codex-a`
  - `implementation + claude-opus` when fallback is chosen
  - `verify + codex-a`
  - `verify + claude-opus`

### High - Claude fallback stages need explicit `--clear` rules

Changing `implementation` fallback from `codex-b` to `claude-opus` introduces
new same-pane Claude reuse:

```text
incorporate -> implementation fallback -> impl-review -> maybe verify fallback
all can use claude-opus pane 0
```

The existing `implementation` dispatch example has no `--clear` because the
normal worker is Codex A. If fallback goes to Opus pane 0 after `incorporate`,
`--clear` is required under Hard Rule 20. The plan mentions `--clear` for
verify examples, but it does not call out the new implementation fallback case
or update Hard Rule 20's examples, which currently mention `verify after coding`
because verify used to run on Sonnet.

Recommended revision:

- Add explicit fallback examples:
  - `implementation --worker claude-opus --clear`
  - `verify --worker claude-opus --clear` when Opus was already used in the
    pipeline
- Update Hard Rule 20 examples from Sonnet-oriented verify reuse to:
  - `implementation` fallback after `incorporate`
  - `impl-review` after `incorporate` or `implementation` fallback
  - `verify` after `impl-review` when routed to `claude-opus`
- Update docs technical-reference wording that currently says `verify after
  coding` is the same-pane clear example.

### High - docs-refresh target/source drift can regenerate the wrong content

The plan says to run `/docs-refresh` or hand-edit marker sections. The local
manifest currently targets:

```yaml
path: docs/technical-reference.md
```

but the tracked file in this repo is:

```text
docs/forge-technical-reference.md
```

There is no `docs/technical-reference.md` in the current repo. Running
docs-refresh as-is risks creating/updating the wrong file while leaving
`docs/forge-technical-reference.md` stale.

There is also a source mismatch. The manifest's `forge.orchestrator` source is:

```text
/Users/sirdrafton/.claude/agents/forge-orchestrator.md
```

but `/forge` currently reads:

```text
/Users/sirdrafton/.claude/skills/forge-orchestrator/SKILL.md
```

Those two installed files currently differ. If docs-refresh reads the agent
body before it is mirrored from the runtime SKILL, the regenerated docs can
preserve stale routing.

Recommended revision:

- Fix `.claude/docs-refresh.yml` to target `docs/forge-technical-reference.md`
  before using docs-refresh.
- Either change `forge.orchestrator` to the runtime installed SKILL path, or
  make the plan's order explicit:
  1. edit toolkit source
  2. sync installed runtime SKILL
  3. sync installed agent body
  4. confirm the relevant routing sections match
  5. run docs-refresh
- Add an acceptance check that no `docs/technical-reference.md` was created.

### Medium - Existing Codex A "QA fallback" reference is not covered

`docs/forge-technical-reference.md` currently describes pane 2 as:

```text
codex-a worker - review, implementation (preferred), QA fallback
```

That conflicts with the SKILL stage details, where QA is `codex-b` preferred
with `claude-sonnet` local fallback. The plan's current-state table says QA is
Codex B / Sonnet, so it misses this existing technical-reference drift.

Recommended revision:

- Include removal of the Codex A "QA fallback" wording in C5.
- Expand acceptance beyond `codex-b` grep. Suggested checks:

```bash
rg -n "implementation.*codex-b|codex-b.*implementation|verify.*(codex-b|claude-sonnet)|QA fallback|qa.*codex-a" \
  skills/forge-orchestrator/SKILL.md docs/forge-technical-reference.md docs/forge-operator-guide.md
```

Expected result should be no illegal routing references, except any intentional
historical note in the review/handoff documents.

### Medium - `verify` exclusion wording becomes ambiguous after re-tiering

Today verify chooses the worker that did not run latest QA from the two-worker
throughput set. After re-tiering verify to `codex-a` or `claude-opus`, the
candidate set is disjoint from the normal QA workers (`codex-b` or
`claude-sonnet`). The old phrase "pick the OTHER worker" no longer maps cleanly:
if QA was `codex-b`, both `codex-a` and `claude-opus` are "other."

Recommended revision:

- Replace the old exclusion algorithm with:
  - default verify to `codex-a`
  - fall back to `claude-opus` only if Codex A is unavailable or explicitly
    unsuitable
  - confirm the selected high worker is not the latest QA worker; this should
    always be true under current QA routing
- Keep the exclusion rule as a guard against future QA routing changes, not as
  the primary selection algorithm.

### Medium - The tier taxonomy mixes `MED`, `LOW`, and `THROUGHPUT`

Section 2 labels QA and QA retry as `MED`, while C1 defines only HIGH-tier and
THROUGHPUT-tier stages. The user's principle distinguishes high-thought from
mechanical/throughput routing, and says QA on Codex B is acceptable.

Recommended revision:

- Use one vocabulary throughout the plan:
  - `HIGH`: proposal exception plus dispatched high-reasoning stages
  - `THROUGHPUT`: coding, qa, qa-fix, qa-retry
- If QA is intentionally medium-reasoning but still throughput-routed, say that
  explicitly once and avoid introducing a third routing tier.

### Medium - Acceptance checks are too weak and too noisy

The proposed check:

```bash
grep -n 'codex-b' SKILL.md docs/forge-technical-reference.md
```

is not a reliable acceptance test. It will include legitimate Codex B references
for QA and aliases, and it will not catch illegal references involving
`claude-sonnet` verify or `codex-a` QA fallback.

Recommended revision:

- Use targeted positive and negative checks:

```bash
rg -n "implementation.*codex-b|codex-b.*implementation|verify.*(codex-b|claude-sonnet)|QA fallback|qa.*codex-a" \
  skills/forge-orchestrator/SKILL.md docs/forge-technical-reference.md docs/forge-operator-guide.md

rg -n "implementation.*claude-opus|verify.*(codex-a|claude-opus)|Reasoning-tier routing|proposal.*local" \
  skills/forge-orchestrator/SKILL.md docs/forge-technical-reference.md
```

- If bridge enforcement is added, add explicit positive and negative
  `dispatch --dry-run` tests as described above.

### Low - The plan should state whether runtime prompt templates are in scope

Stage dispatch renders `~/.config/forge/prompts/{stage}.txt`. I did not find
routing decisions encoded in the `implementation.txt` or `verify.txt` templates;
they use `{worker}` for callback instructions. That means no prompt-template
edit appears necessary for this plan.

Recommended revision:

- Add a short "checked, no change" line for `~/.config/forge/prompts/*.txt`.
  This prevents a future implementer from either missing the templates or
  editing them unnecessarily.

## Suggested Plan Amendments

1. Resolve D1 before implementation. If `verify` is HIGH, make it a dispatched
   high-tier stage with default `codex-a`, fallback `claude-opus`.
2. Rewrite C1 so `proposal` is a named local exception, not part of the
   "dispatched HIGH stages run only on pane 0 or pane 2" rule.
3. Decide whether to add bridge-level stage-worker enforcement. If not, update
   the plan title/acceptance to make clear this is advisory prompt routing.
4. Add all new same-pane Claude `--clear` cases introduced by Opus fallback.
5. Fix docs-refresh target/source drift before relying on `/docs-refresh`.
6. Add Codex A "QA fallback" cleanup to the technical-reference changes.
7. Replace broad grep acceptance with targeted illegal-route checks and, if
   bridge enforcement is added, positive/negative dry-run tests.

## Revised Acceptance Criteria

- `proposal` is documented as the only local pane-1 stage, and the rule text no
  longer says it must run only on pane 0 or pane 2.
- Dispatched high stages are consistently documented as:
  - `review`: `codex-a`
  - `incorporate`: `claude-opus`
  - `implementation`: `codex-a`, fallback `claude-opus`
  - `impl-review`: `claude-opus`
  - `verify`: `codex-a`, fallback `claude-opus`
- No current source or docs say:
  - `implementation` can use `codex-b`
  - `verify` can use `codex-b` or `claude-sonnet`
  - Codex A is a QA fallback
- `--clear` is documented for every repeated Claude-pane dispatch introduced by
  the new routing.
- docs-refresh updates `docs/forge-technical-reference.md`, not
  `docs/technical-reference.md`.
- Runtime installed SKILL and installed agent body are synced before docs are
  regenerated, or docs-refresh is pointed at the runtime installed SKILL.
- If bridge enforcement is in scope, illegal stage/worker dispatches fail in
  `--dry-run`; if it is out of scope, the plan explicitly accepts that routing
  is not mechanically enforced.
