---
name: forge-orchestrator
description: This skill should be used when the user invokes Forge orchestration, starts a Forge build or fix pipeline, resumes a pipeline, or requests Forge status in a qualified Codex host pane.
---

# Forge Orchestrator

Read the explicitly pinned canonical protocol and Codex adapter from the
candidate entry supplied by the qualified launcher. In the source checkout,
these are `../../skills/forge-orchestrator/SKILL.md` and
`../../skills/forge-orchestrator/references/codex.md`, relative to this skill's
parent directory. Do not fall back to a same-named global skill, a remembered
protocol, or a project-local duplicate. Missing protected entry is a blocker.

Reload both files with native file-read tools after reinvocation, compaction,
resume and restart. Preserve session ownership and pending callback identity.
Use native isolated children, not shell-launched providers or parent execution.
