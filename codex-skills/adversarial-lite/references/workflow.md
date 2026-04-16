# Codex Workflow Reference

Use this when constructing prompts for the native Codex workflow.

## Proposer Spawn Template

Use two separate subagents. Spawn them in parallel when possible.

```text
You are Proposer {A|B} for an adversarial-lite planning run.

Working directory: {cwd}
Output directory for artifacts: {output_dir}

Problem statement:
{problem_statement}

Source files to read:
{source_files}

Assigned strategy:
{strategy}

Role instructions:
{full references/proposer-lite.md}

Proposal format:
{full references/proposal-format-lite.md}

Isolation rule:
Do not read proposal-{other}.md, final-plan.md, or any other proposal-directory artifact.
Do not inspect the other investigator's work. Do not modify production code.

Return the complete proposal markdown in your final answer.
```

Use `fork_context: false` when the runtime supports it. Prefer `gpt-5.4-mini` for A/B when model overrides are available.

## Synthesizer Spawn Template

Use a fresh subagent for C.

```text
You are Synthesizer C for an adversarial-lite planning run.

Working directory: {cwd}
Output directory: {output_dir}
Mode: {CONVERGED|DIVERGED}

FIRST: Read these source files before reading either proposal:
{source_files}

Problem statement:
{problem_statement}

After source examination, read:
- {output_dir}/proposal-A.md
- {output_dir}/proposal-B.md

Role instructions:
{full references/synthesizer-lite.md}

Return the complete final-plan.md markdown in your final answer.
```

Prefer `gpt-5.4` or the current high-capacity model for C.

## Lead Artifact Writes

After each subagent returns:

1. Write A's returned markdown to `proposal-A.md`.
2. Write B's returned markdown to `proposal-B.md`.
3. Write C's returned markdown to `final-plan.md`.

The lead should write artifacts locally rather than relying on subagent forked-workspace file changes.

## Minimal Execution Record

Create `execution-method.md`:

```markdown
# Execution Method

- **Skill:** adversarial-lite
- **Isolation method:** Codex subagents with fork_context=false
- **Proposer A:** {agent id/model}
- **Proposer B:** {agent id/model}
- **Synthesizer C:** {agent id/model}
- **Model override available:** yes/no
- **Convergence:** CONVERGED/DIVERGED
```
