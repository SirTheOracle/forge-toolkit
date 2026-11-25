# P1 Native Feasibility Closeout

Date: 2026-09-14 (local). Branch: `feat/orchestrator-p1-native-probes`.

**Decision: Codex can be the orchestrator. Continue the planned implementation.**
P1's bounded investigation is complete with the restrictions below, not an
all-green production acceptance or provider admission. No native-loading or
native-child capability failure justifies dropping Codex from P2-P6. The current
canonical skill is still Claude-specific; loading it is not executing a portable
workflow. P2/P3 must implement that distinction, not merely add a selector.

The evidence indices are `2026-09-14.json`, `2026-09-14-runtime.json`,
`2026-09-14-workflow.json`, and `2026-09-14-closure.json` in this directory.
The last index supersedes outstanding experiment statuses in earlier snapshots.
Raw evidence stays under this worktree's ignored `.dev/forge-p1/` and protected
`.codex/forge-p1/` directories; hashes are review pointers, not portable CI proof.

## Acceptance Matrix

PASS below applies only to the stated measurement. LIMITED retains a specific
unproved claim; it does not silently convert NOT_RUN into success. P1 step 3
requires the new Codex host-control path; it does not require repeating the entire
existing Claude workflow. Claude full workflow qualification remains in P5/P6.

| Case | Codex | Claude / Disposition |
| --- | --- | --- |
| closure | PASS for measured primary/linked closure; nested baseline FAIL, candidate write-open PASS | Same copied helpers/configs inventoried; Claude reviewed host/children are not claimed worker-contained. Production immutable runtime/alias closure is P3. |
| load-fresh | PASS, explicit-load-tool-traced | PASS, explicit-load-tool-traced, authenticated native interactive Read. Both also read the protected canonical skill for compatibility only. |
| load-duplicates | LIMITED: local discovery and explicit pinned read recorded | LIMITED: native slash entry was unavailable in the isolated settings profile; explicit protected Read succeeds despite conflicting local decoys. No automatic discovery winner certified. |
| load-reinvoke | PASS for explicit reload; global skills/list refresh sees changed metadata | PASS for explicit reload and changed local decoy. Authenticated changed-global invocation NOT_RUN for either provider: credential-free homes are logged out. The supported entry does not depend on it. |
| load-compact-resume-restart | PASS, separate native transition/read evidence in runtime packet | PASS: fresh Read after resume, compaction, and new-session restart, not a remembered marker. |
| children-proposal | LIMITED: native non-forked A/B, both follow-ups, C and completion observed; exact message plaintext opaque | PASS: native exact disjoint A/B inputs, both follow-ups report other canary UNKNOWN, fresh C receives only 11 and 17 and returns 28. |
| children-digest | LIMITED: native isolated child and disk reads observed; exact message body opaque | PASS: Agent prompt equals artifact path alone, one child Read, zero parent artifact Reads, digest completion. |
| identity-tmux | PASS: actual native host, owned server, copied identity MATCH and observation | PASS for native approved identity MATCH on the linked-root host. |
| broker-registration | PASS: native host starts copied broker and launches authenticated contained worker | Existing Claude path not requalified by P1; P5/P6 regression required. |
| infra-lock | PASS: native acquire/status, linked-root contention, normal release | Shared implementation; no new Claude workflow acceptance claimed. |
| dispatch-wait | PASS: two real bounded worker deliveries and genuine callbacks | Existing Claude workflow not rerun here. |
| digest-render | PASS with limitation: missing template halts advancement; retry uses existing immutable template without redispatch | Template-byte replacement not tested; no production digest inventory installed. |
| ask-answer | PASS with limitation: genuine delivery-bound ask, one public answer, duplicate refused, continuation | Cross-root answer-side callback cleanup still warns; owning host recovery works. Not automatic cleanup parity. |
| callback-recovery | PASS: native host exit/resume retains callback ID, pending timestamp and lock, then terminal release | No broad crash-recovery or unattended recovery claim. |
| permissions | PASS for scoped host approvals/denial and measured worker/child policy; profile-management verbs NOT_RUN | Native manual approvals/denials observed; child can write-open all seven dummy protected targets. New profile authority verbs belong to P2, not P1. |
| hooks-attention | PASS for native busy/idle/permission/completion callbacks, zero decision output, worker Stop separation | Same native proof. LIMITED: legacy host envelope omits provider; provider-aware production projection belongs to P4. |

## Measured Boundaries

The closure packet contains 127 copied files and 2,097 runtime/package/pointer
entries. All 145 standalone canaries pass. Baseline native containment denies
the full inventory in the primary root but permits write-open of the nested
worktree's actual `.git` pointer from its parent. No pointer bytes were changed.
A native named profile with exact read-only entries for audited `.git` files
denies all 2,097 opens, including a reproducible harness run. A read-only glob
candidate is rejected by the CLI. This is a candidate, not a launcher change:
future pointer creation, rename/unlink and dependency alias replacement are not
certified. Unqualified nested layouts must remain unavailable in P2/P3 admission.

Codex 0.153.4 host/children use `gpt-6-astra`, workspace-write, network restricted,
on-request approvals. Native worker hook/denial tests use `gpt-5.5`, approvals
never. The native hook compatibility label says `bypassPermissions` for that
worker, while actual permission-profile metadata and seven denied writes prove
workspace containment; the label alone is not an authority attestation.

Claude 2.1.272 uses `claude-opus-5[1m]`, default/manual host mode, native Agent
and SendMessage. The child inherits the parent tmux identity and can read the
outside-workspace dummy token and write-open the seven audit targets after the
exact one-time command approval. Codex children share dummy-token readability but
deny those opens without escalation. These are measured authority differences,
not grounds to invent stronger child sandboxing as a parity prerequisite.
No real credential was read or copied by the probes.

Claude A/B native inputs are plaintext-auditable. Codex exposes `fork_turns:none`,
native child identity, follow-up and joins, but opaque message bodies. The latter
are hashed, never decrypted or represented as plaintext-attested. Context
isolation is observed; exact disjoint plaintext content remains unverified.
Any product promise requiring that stronger audit must remain unavailable.

Both providers emitted actual PermissionRequest events into the copied adapter;
all seven observed adapter results have exit 0 and empty stdout/stderr. Approval
choices were made by the P1 controller for individual commands, not persistent
prefixes or bypass modes. This is not a human usability study. Both native worker
Stop events left their host Stop records byte-identical. A hook-enabled Codex
worker also denied all seven protected writes with a successful workspace control.
Watch triggering was disabled in the copied helper environment, so these are
native adapter/event-file results, not an end-to-end command-center UI test.

Codex hook config merges with global hooks. Invocation-only exact-hash trust was
reviewed for the protected observer; the ambient global hook was explicitly
disabled. `hooks={}` and an initially misquoted dotted override were insufficient.
No global hook trust or installed skill files were edited. Claude's initial
minimal environment lacked USER/LOGNAME and failed authentication; restoring
ordinary keychain discovery worked without copying credentials. Its native
auto-updater moved 2.1.271 to 2.1.272 before the authenticated run; resume/restart
then pinned 2.1.272 with auto-update disabled. Native cache/plugin startup effects
are not claimed absent. Codex ambient MCP startup failures did not supply any
tools to the tested workflow and are not reported as passing health checks.

## Implementation Handoff

P2 owns provider/model selection, protected profile registration and revision
authority, session binding, and refusal of unqualified layouts. P3 owns protected
reproducible runtime/assets, explicit native loading, canonical skill refactoring,
native child adapters and complete digest assets. Use the measured native APIs;
do not replace children with sequential parent work or shell-provider substitutes.
P4 owns provider-aware host attention and observation. P4b proves the integrated
attended preview; P5/P6 cover full build/fix/fallback parity and new-session rollout.
**P1 plus P6 alone cannot deliver the requested selector and orchestration parity.**

The P1 series also fixed dedicated-tmux broker binding and contained local asks;
the final closure commit adds probes/tests, not production provider enablement.
This fixture's native clients and owned server are stopped. Earlier workflow
deliveries are terminal, its broker stopped and shared lock free. Existing Forge
sessions, production defaults and worker doctor policy were not changed.

Validation: 54 harness tests pass through the all-tests runner; shell syntax and
hermetic Codex policy smoke pass. No full aggregate rerun. The previous workflow
packet retains 238 command-center passes and 39 failures identical to baseline;
those existing failures are not claimed fixed by this test-only closeout.
