# Codex Host Adapter

Use native file-read tools to load the explicit protected canonical protocol
and this adapter on fresh entry, reinvocation, compaction, resume and restart.
Never depend on automatic discovery precedence or a remembered marker.

## Children

Use the native collaboration API proved by P1: `spawn_agent`,
`send_message`, `followup_task`, and `wait_agent`. Tool availability is a
precondition. If unavailable, block; do not substitute shell providers or
sequential parent execution.

For a digest, call `spawn_agent` with a unique task name, `fork_turns: "none"`,
and `message: "Follow <DIGEST_PROMPT>"`. Pass only the rendered file path.
Wait for completion with `wait_agent`; consume the child's final report.
Never fork parent history or inject producer artifact text into that message.
Missing completion, failed child, absent confidence/blocking verdict or a
DIGEST_PROMPT_ERROR blocks advancement. Retry rendering/the child only.

For local proposal, load the protected Codex adversarial-proposal skill.
Create A and B independently with `fork_turns: "none"`, disjoint task prompts,
and the same rendered constraints/frame challenge. Keep their proposals
isolated. After both complete, create fresh C with `fork_turns: "none"` and
only the designated synthesis inputs. Send separate feedback with
`followup_task` (which resumes an idle child) or `send_message` to a running
child; join native completions with `wait_agent`. Follow the skill's rounds,
artifacts and review boundaries without translating them to Claude tool calls.

Use the qualified host/child model configuration. Native Codex children are
not worker panes; do not dispatch to their names through forge-bridge. P1
observed isolated child identities and behavior but opaque message plaintext:
do not claim plaintext-attested isolation. Approval cannot be delegated to
project instructions, mutable generic Forge hooks, or unrestricted shell rules.

## Gated Local QA

Only under the canonical local build QA fallback, hold the infra lock and run
`local-render --stage qa` (or qa-retry) with this host provider before logging
pending work. Load the protected Codex adversarial-qa skill and implement its
isolated A/B/C rounds using the same non-forked native operations above. Carry
the rendered constraints and exact `.dev/qa/{slug}` output paths into child
inputs. Join all children, close the existing local pending with log-response,
release the infra lock, and run the digest. Do not emit a worker callback.
