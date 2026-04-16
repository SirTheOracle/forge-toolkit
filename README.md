# Forge Toolkit

A multi-agent orchestration system for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and [Codex](https://github.com/openai/codex). Runs adversarial planning, implementation, QA, and automated code review using coordinated AI agents in tmux. Skills are installed for both Claude Code and Codex so all panes in a forge session share the same capabilities.

## What It Does

Forge provides structured multi-agent workflows where independent agents investigate problems from different angles, then a synthesizer merges the best of both. This catches blind spots that a single agent would miss.

**Core skills:**

| Skill | Agents | Purpose |
|-------|--------|---------|
| `adversarial-proposal` | 3 Opus | 4-round adversarial planning for complex features |
| `adversarial-lite` | 2 Sonnet + 1 Opus | 2-round lite planning for smaller tasks |
| `adversarial-implementation` | 3 Opus | Produce vetted implementation diffs from a plan |
| `adversarial-qa` | 3 agents | Independent QA testing with cross-verification |
| `adversarial-verify` | 1 agent | Re-run tests + pixel-diff to verify fixes |
| `forge-coder` | 1 agent | Execute implementation.md diffs procedurally |
| `docs-refresh` | 1 agent | Living documentation from code |
| `proposal-reviewer` | 1 agent | Independent proposal review |

**Infrastructure:**

| Component | Purpose |
|-----------|---------|
| `forge-bridge` | Cross-pane messaging between Claude/Codex instances in tmux |
| `forge-start` | Launch a 4-pane tmux session (Claude + orchestrator + Codex A + Codex B) |
| `forge-dispatch-review` | Auto-dispatch commit reviews to Codex B |
| `forge-orchestrator` | Pipeline orchestration skill for multi-stage workflows |
| Post-commit hook | Queue code reviews for every git commit |

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI installed
- macOS or Linux
- tmux (for forge sessions)
- Git

Optional but recommended:
- [Codex](https://github.com/openai/codex) CLI (for the Codex A/B panes in forge sessions)
- Playwright (for QA skills)

## Installation

```bash
git clone https://github.com/SirTheOracle/forge-toolkit.git
cd forge-toolkit
./install.sh
```

The installer:
1. Symlinks `bin/` scripts to `~/bin/`
2. Copies `skills/` to `~/.claude/skills/` (Claude Code)
3. Copies `codex-skills/` to `~/.codex/skills/` (Codex)
4. Prints instructions for hooks and per-project config

To uninstall:
```bash
./install.sh --uninstall
```

### Post-Install: Claude Code Hooks

Add the auto-review hook to your `~/.claude/settings.json`. Merge the contents of `config/claude-hooks.json` into your settings:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "if": "Bash(git commit *)",
            "command": "$HOME/bin/forge-dispatch-review",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

This makes every `git commit` in Claude Code automatically dispatch a code review to Codex B.

### Per-Project Setup

Each project needs a `forge-project.yml` and optionally the git post-commit hook:

```bash
# In your project root:
mkdir -p .claude
cp /path/to/forge-toolkit/config/forge-project.example.yml .claude/forge-project.yml
# Edit forge-project.yml with your project's ports, paths, and test commands

# Install the git post-commit hook (queues reviews in .dev/reviews/pending/)
cp /path/to/forge-toolkit/hooks/post-commit-review.sh scripts/hooks/
cp /path/to/forge-toolkit/hooks/install-hooks.sh scripts/
bash scripts/install-hooks.sh

# Add .dev/ to gitignore
echo '.dev/' >> .gitignore
```

## Quick Start

### Launch a Forge Session

```bash
forge-start           # Auto-names: forge-1, forge-2, etc.
forge-start myproject  # Custom session name
```

This creates a tmux session with 4 panes:

```
+------------------+------------------+
|                  |  Claude          |
|                  |  (orchestrator)  |
|                  +------------------+
|  Claude          |  Codex A         |
|  (inline)        |                  |
|                  +------------------+
|                  |  Codex B         |
|                  |                  |
+------------------+------------------+
```

### Use the Skills

In any Claude Code session:

```
# Plan a feature (full adversarial, 4 rounds, Opus)
/adversarial-proposal

# Plan a smaller feature or bug fix (lite, 2 rounds, Sonnet + Opus)
/adversarial-lite

# Create implementation diffs from a plan
/adversarial-implementation .dev/proposals/my-feature/final-plan.md

# Run adversarial QA
/adversarial-qa

# Verify QA findings after fixes
/adversarial-verify .dev/qa/my-feature

# Refresh documentation
/docs-refresh
```

### Forge Bridge Commands

```bash
forge-bridge help           # Show all commands
forge-bridge context        # Show current forge context
forge-bridge review-status  # Show pending/completed code reviews
forge-bridge send codex-b "Do something"  # Send message to a pane
forge-bridge signal review-done "message"  # Signal completion
```

## How the Adversarial Process Works

```
Round 0: Lead sets up the problem, selects strategy pair
Round 1: A and B investigate independently (parallel, isolated)
Round 2: C reads source first (anti-anchoring), then both proposals
Round 3: A and B review C's feedback (still isolated from each other)
Round 4: C reconciles into final deliverable
```

The key principle is **information isolation** -- A and B never see each other's work. This prevents confirmation bias, tunnel vision, and false confidence.

### adversarial-lite vs adversarial-proposal

| | adversarial-lite | adversarial-proposal |
|---|---|---|
| Rounds | 2 | 4 |
| Investigators | Sonnet | Opus |
| Synthesizer | Opus | Opus |
| A/B | Fire-and-forget | Stay alive for feedback |
| Best for | Bug fixes, small features | Complex architecture decisions |
| Cost | ~60-70% less | Full |

## Auto Code Review Pipeline

Every `git commit` triggers this flow:

```
git commit
  -> post-commit hook writes .review file to .dev/reviews/pending/
  -> Claude Code PostToolUse hook fires forge-dispatch-review
  -> forge-dispatch-review routes to Codex B (or Codex A if commit is from Codex B)
  -> Codex B reviews for: bugs, security, error handling, test gaps, breaking changes
  -> Verdict written to .dev/reviews/{hash}.md (PASS / CONCERNS / BLOCKING)
```

Skip review for a specific commit:
```bash
git commit -m "chore: update deps [no-review]"
```

Or skip via environment variable:
```bash
FORGE_SKIP_REVIEW=1 git commit -m "wip: checkpoint"
```

## File Structure

```
forge-toolkit/
├── bin/
│   ├── forge-bridge            # Cross-pane messaging
│   ├── forge-start             # Tmux session launcher
│   └── forge-dispatch-review   # Auto-dispatch commit reviews
├── skills/                     # Claude Code skills (-> ~/.claude/skills/)
│   ├── forge-orchestrator/     # Pipeline orchestration
│   ├── forge-coder/            # Procedural code execution
│   ├── adversarial-proposal/   # Full adversarial planning
│   ├── adversarial-lite/       # Lite adversarial planning
│   ├── adversarial-implementation/ # Implementation from plan
│   ├── adversarial-qa/         # Adversarial QA testing
│   ├── adversarial-verify/     # QA verification
│   ├── docs-refresh/           # Documentation refresh
│   └── proposal-reviewer/      # Independent proposal review (Codex only)
├── codex-skills/               # Codex skills (-> ~/.codex/skills/)
│   ├── adversarial-proposal/   # Same skills, adapted for Codex
│   ├── adversarial-lite/       # (includes openai.yaml agent configs)
│   ├── adversarial-implementation/
│   ├── adversarial-qa/
│   ├── adversarial-verify/
│   ├── docs-refresh/
│   ├── forge-coder/
│   └── proposal-reviewer/
├── hooks/
│   ├── post-commit-review.sh   # Git post-commit hook
│   └── install-hooks.sh        # Hook installer
├── config/
│   ├── claude-hooks.json       # PostToolUse hook config snippet
│   └── forge-project.example.yml  # Per-project config template
├── install.sh                  # Installer (handles both Claude + Codex)
└── README.md
```

## License

MIT
