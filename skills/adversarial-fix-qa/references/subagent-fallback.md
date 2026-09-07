# Subagent Fallback — adversarial-fix-qa

When `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` is not enabled (or `TeamCreate` fails), the adversarial-fix-qa skill cannot use the persistent-teammate workflow. This document specifies a degraded but functional fallback using sequential `Agent` subagent calls and file-based handoffs.

The fallback produces the same final artifacts (`fix-issues.md`, `fix-manifest.yaml`, `reconciliation-notes.md`) but loses three properties:

- **No persistent agent context.** Round 3 critique cannot resume from Round 1 context; the critic must re-read everything cold.
- **No parallel A and B.** Round 1 runs sequentially.
- **No SendMessage feedback loop.** All coordination is via file artifacts on disk.

This is acceptable for one-off runs but adds ~30-50% to wall-clock time.

## When to use

- Only when `TeamCreate` returns an error like "Agent Teams not enabled" or the env var is unset
- After confirming Agent Teams is genuinely unavailable — do not pre-emptively fall back

## Fallback architecture

```
Round 1a (sequential)  Lead spawns qa-confirmer-a subagent
                       Subagent writes qa-A.md
Round 1b (sequential)  Lead spawns regression-hunter-b subagent
                       Subagent writes qa-B.md
                       (Round 1 artifact gate runs as in normal flow)
Round 2                Lead spawns synthesizer-c subagent
                       Reads qa-A.md, qa-B.md
                       Writes qa-C.md, review-for-A.md, review-for-B.md
                       (Round 2 artifact gate runs as in normal flow)
Round 3a               Lead spawns critic-a subagent (cold)
                       Inputs: qa-A.md, review-for-A.md
                       Writes critique-a.md
Round 3b               Lead spawns critic-b subagent (cold)
                       Inputs: qa-B.md, review-for-B.md
                       Writes critique-b.md
Round 4                Lead spawns reconciler-c subagent
                       Reads critique-a.md, critique-b.md, qa-C.md, fix-review.md
                       Writes fix-issues.md, fix-manifest.yaml, reconciliation-notes.md
                       (Output Quality Gate runs as in normal flow)
```

## Differences from the Teams flow

### Round 1 sequencing

```python
# Normal Teams flow: parallel
Agent(name="qa-confirmer-a", ...)
Agent(name="regression-hunter-b", ...)

# Fallback: sequential, blocking
Agent(subagent_type="general-purpose", prompt=<<<A's prompt>>>)
# wait for completion (Agent call returns when done)
Agent(subagent_type="general-purpose", prompt=<<<B's prompt>>>)
```

The fallback subagents do NOT use `team_name` and do NOT call `SendMessage` — they just write their report file and return.

### Round 3 cold critics

Without Teams, the original A and B cannot be "resumed." Round 3 critics are fresh subagents that read both their target QA report AND their assigned review file with no prior context.

The critic's prompt must include:

- The role file (`agents/fix-qa-critic.md`)
- The full content of the target QA report (qa-A.md or qa-B.md)
- The full content of the assigned review file (review-for-A.md or review-for-B.md)
- An explicit note: "You are critiquing on behalf of the original Round 1 agent. Treat that agent's findings as authoritative; your job is to defend them against the synthesizer's interpretation."
- An isolation rule (read ONLY the two files above; do NOT read qa-C.md or the other agent's files)

The critic writes `critique-a.md` (or `critique-b.md`) to the output directory.

### Round 4 reconciler reads files instead of inline messages

In the Teams flow, both critiques arrive as inline messages to synthesizer-c via SendMessage. In the fallback, the reconciler reads `critique-a.md` and `critique-b.md` from disk.

The reconciler prompt embeds:

- The reconciler role file (`agents/fix-qa-reconciler.md`)
- The authoritative formats (`references/fix-issues-format.md`, `references/fix-manifest-schema.yaml`)
- Paths to all input files (qa-A.md, qa-B.md, qa-C.md, critique-a.md, critique-b.md, fix-review.md if present)
- The Hard Rules from SKILL.md (verdict-vs-counts consistency, re-emerged advisories, etc.)

## Coordination via files

Each round's outputs are the next round's inputs. The lead is responsible for:

- Verifying each round's required artifacts exist on disk before spawning the next subagent
- Running the same gates as the Teams flow (Round 1 artifact gate, Round 2 artifact gate, Output Quality Gate)
- Writing the early-stop artifact pair if Step 0 trips
- Writing the stub artifact pair if any subagent fails to produce required outputs after one retry

## Retry policy in fallback

Same as Teams: one retry per failure. If a subagent produces an incomplete artifact:

```python
# Retry the same subagent with a more specific prompt
Agent(subagent_type="general-purpose", prompt=<<<retry prompt with the specific gap named>>>)
```

If the retry still fails, write the stub artifact pair and surface to user.

## Lead context pressure in fallback

The fallback puts more pressure on the lead's context window because:

- The lead must hold all subagent outputs across rounds (no per-teammate context)
- The lead must re-read source files when verifying gates

Mitigation: the lead reads only the artifacts strictly needed for each gate (qa-A.md and qa-B.md last 50 lines for severity counts; not the full reports). The Output Quality Gate is the heaviest read because it must verify structure and consistency across all three final artifacts.

## What does NOT change in fallback

- All Hard Rules apply identically
- All gates apply identically (artifact gates, quality gates, Output Quality Gate)
- All format requirements apply identically (qa-working-format, fix-issues-format, fix-manifest-schema)
- The four-outcome verdict applies identically (PASS / FIX_FAILED / REGRESSION / BOTH / BLOCKED)
- The defense-in-depth rules apply identically (missing/malformed Status, Verdict)
- Hard Rule 5 (always write fix-issues.md and fix-manifest.yaml) applies identically — including the stub-artifact-pair clause

## When to recommend the user enable Teams

If the fallback runs successfully, the QA result is valid. But the experience is degraded. Suggest at the end of the run:

> "This run used the subagent fallback because Agent Teams was unavailable. To enable Teams for future runs, set `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in Claude Code's settings. This restores parallel A/B execution and the Round 3 resume-with-context behavior, cutting wall-clock time by ~30-50%."
