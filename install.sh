#!/bin/bash
# install.sh — Install the forge toolkit
#
# What it does:
#   1. Symlinks bin/ scripts to ~/bin/
#   2. Copies skills/ to ~/.claude/skills/ (Claude Code)
#   3. Copies codex-skills/ to ~/.codex/skills/ (Codex)
#   4. Prints instructions for hooks setup
#
# Usage:
#   ./install.sh              # install everything
#   ./install.sh --uninstall  # remove symlinks and skills

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/bin"
CLAUDE_SKILLS_DIR="$HOME/.claude/skills"
CODEX_SKILLS_DIR="$HOME/.codex/skills"
SETTINGS_FILE="$HOME/.claude/settings.json"

SKILL_NAMES=(
    forge-orchestrator forge-coder adversarial-proposal adversarial-lite
    adversarial-implementation adversarial-qa adversarial-verify docs-refresh
    proposal-reviewer command-center
)
# NOTE: proposal-reviewer ships via codex-skills/ ONLY (no skills/ counterpart);
# the Claude-side uninstall loop no-ops on it by the -d guard. Kept in the list
# so the Codex-side uninstall removes it.

# Operator files symlinked into ~/.claude (same anti-drift pattern as bins):
# "<repo-relative-src>:<dst-under-$HOME/.claude>"
OPERATOR_FILES=(
    "commands/forge.md:commands/forge.md"
    "agents/forge-orchestrator.md:agents/forge-orchestrator.md"
)

# Single bin manifest (was duplicated across install/uninstall/drift).
BIN_SCRIPTS=(forge-bridge forge-start forge-dispatch-review forge-dispatch-pr-review
             forge-stall-install-regex forge-watch forge forge-cc-hook)

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { printf "${CYAN}%s${NC}\n" "$1"; }
ok()    { printf "${GREEN}%s${NC}\n" "$1"; }
warn()  { printf "${YELLOW}%s${NC}\n" "$1"; }
err()   { printf "${RED}%s${NC}\n" "$1"; }

# ── Drift check (read-only) ────────────────────────────────
# Reports every divergence between the repo and the installed state without
# touching anything. Exit 0 = fully converged, exit 1 = drift found.

if [ "${1:-}" = "--check-drift" ]; then
    info "Checking repo ↔ installed drift (read-only)..."
    DRIFT=0

    for script in "${BIN_SCRIPTS[@]}"; do
        src="$SCRIPT_DIR/bin/$script"; dst="$BIN_DIR/$script"
        if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
            ok "  bin/$script — linked"
        elif [ -L "$dst" ]; then
            err "  bin/$script — symlink points elsewhere: $(readlink "$dst")"; DRIFT=1
        elif [ -f "$dst" ]; then
            if cmp -s "$src" "$dst"; then warn "  bin/$script — regular file (byte-identical; re-run install to converge)"; DRIFT=1
            else err "  bin/$script — regular file, CONTENT DIFFERS"; DRIFT=1; fi
        else
            err "  bin/$script — not installed"; DRIFT=1
        fi
    done

    for skill_dir in "$SCRIPT_DIR"/skills/*/; do
        skill_name="$(basename "$skill_dir")"; dst="$CLAUDE_SKILLS_DIR/$skill_name"
        if [ ! -d "$dst" ]; then err "  skills/$skill_name — not installed (claude)"; DRIFT=1
        elif diff -rq --exclude=.DS_Store "$skill_dir" "$dst" >/dev/null 2>&1; then ok "  skills/$skill_name — identical (claude)"
        else err "  skills/$skill_name — DIFFERS from installed (claude):"; diff -rq --exclude=.DS_Store "$skill_dir" "$dst" 2>&1 | sed 's/^/      /'; DRIFT=1; fi
    done
    for skill_dir in "$SCRIPT_DIR"/codex-skills/*/; do
        [ -d "$skill_dir" ] || continue
        skill_name="$(basename "$skill_dir")"; dst="$CODEX_SKILLS_DIR/$skill_name"
        if [ ! -d "$dst" ]; then err "  codex-skills/$skill_name — not installed (codex)"; DRIFT=1
        elif diff -rq --exclude=.DS_Store "$skill_dir" "$dst" >/dev/null 2>&1; then ok "  codex-skills/$skill_name — identical (codex)"
        else err "  codex-skills/$skill_name — DIFFERS from installed (codex):"; diff -rq --exclude=.DS_Store "$skill_dir" "$dst" 2>&1 | sed 's/^/      /'; DRIFT=1; fi
    done

    for pair in "${OPERATOR_FILES[@]}"; do
        src="$SCRIPT_DIR/${pair%%:*}"; dst="$HOME/.claude/${pair##*:}"
        if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then ok "  ${pair##*:} — linked"
        elif [ -e "$dst" ]; then err "  ${pair##*:} — not a toolkit symlink"; DRIFT=1
        else err "  ${pair##*:} — not installed"; DRIFT=1; fi
    done

    echo ""
    if [ "$DRIFT" -eq 0 ]; then ok "No drift — repo and installed state converged."; exit 0
    else err "Drift found (see above). Re-run ./install.sh to converge, or reconcile by hand."; exit 1; fi
fi

# ── Uninstall ──────────────────────────────────────────────

if [ "${1:-}" = "--uninstall" ]; then
    info "Uninstalling forge toolkit..."

    # Unload the forge-watch launchd agent before removing its symlink, else it
    # keeps firing every 30s against a now-dangling path.
    for agent in com.forge.watch com.forge.gc; do
        AGENT_PLIST="$HOME/Library/LaunchAgents/$agent.plist"
        if [ -f "$AGENT_PLIST" ]; then
            launchctl unload "$AGENT_PLIST" 2>/dev/null || true
            rm -f "$AGENT_PLIST"
            ok "  Unloaded and removed $agent launchd agent"
        fi
    done

    # Remove bin symlinks
    for script in "${BIN_SCRIPTS[@]}"; do
        if [ -L "$BIN_DIR/$script" ]; then
            rm "$BIN_DIR/$script"
            ok "  Removed ~/bin/$script"
        fi
    done

    # Remove Claude skill directories
    for skill in "${SKILL_NAMES[@]}"; do
        if [ -d "$CLAUDE_SKILLS_DIR/$skill" ]; then
            rm -rf "$CLAUDE_SKILLS_DIR/$skill"
            ok "  Removed ~/.claude/skills/$skill"
        fi
    done

    # Remove operator-file symlinks (never a regular file — those are unmanaged)
    for pair in "${OPERATOR_FILES[@]}"; do
        dst="$HOME/.claude/${pair##*:}"
        if [ -L "$dst" ]; then
            rm "$dst"
            ok "  Removed $dst"
        fi
    done

    # Remove Codex skill directories
    for skill in "${SKILL_NAMES[@]}"; do
        if [ -d "$CODEX_SKILLS_DIR/$skill" ]; then
            rm -rf "$CODEX_SKILLS_DIR/$skill"
            ok "  Removed ~/.codex/skills/$skill"
        fi
    done

    warn ""
    warn "Manual cleanup needed:"
    warn "  1. Remove the PostToolUse hook from ~/.claude/settings.json"
    warn "  2. Remove git post-commit hooks from your projects"
    ok ""
    ok "Uninstall complete."
    exit 0
fi

# ── Install ────────────────────────────────────────────────

echo ""
info "Installing forge toolkit..."
echo ""

# ── Step 1: Bin scripts ───────────────────────────────────

info "Step 1: Symlinking bin scripts to ~/bin/"

mkdir -p "$BIN_DIR"

for script in "${BIN_SCRIPTS[@]}"; do
    src="$SCRIPT_DIR/bin/$script"
    dst="$BIN_DIR/$script"

    if [ -L "$dst" ]; then
        existing="$(readlink "$dst")"
        if [ "$existing" = "$src" ]; then
            ok "  $script — already linked"
            continue
        else
            warn "  $script — updating symlink (was: $existing)"
            rm "$dst"
        fi
    elif [ -f "$dst" ]; then
        warn "  $script — ~/bin/$script exists as a regular file, skipping"
        warn "    Remove it manually if you want forge-toolkit to manage it"
        continue
    fi

    ln -s "$src" "$dst"
    chmod +x "$src"
    ok "  $script — linked"
done

# Verify ~/bin is in PATH
if ! echo "$PATH" | tr ':' '\n' | grep -q "^$BIN_DIR$"; then
    warn ""
    warn "  ~/bin is not in your PATH. Add this to your shell profile:"
    warn "    export PATH=\"\$HOME/bin:\$PATH\""
fi

echo ""

# ── Step 2: Claude Code Skills ───────────────────────────

info "Step 2: Copying Claude Code skills to ~/.claude/skills/"

mkdir -p "$CLAUDE_SKILLS_DIR"

for skill_dir in "$SCRIPT_DIR"/skills/*/; do
    skill_name="$(basename "$skill_dir")"
    dst="$CLAUDE_SKILLS_DIR/$skill_name"

    if [ -d "$dst" ]; then
        rm -rf "$dst"
        cp -R "$skill_dir" "$dst"
        ok "  $skill_name — updated"
    else
        cp -R "$skill_dir" "$dst"
        ok "  $skill_name — installed"
    fi
done

echo ""

# ── Step 3: Codex Skills ─────────────────────────────────

info "Step 3: Copying Codex skills to ~/.codex/skills/"

if [ -d "$HOME/.codex" ]; then
    mkdir -p "$CODEX_SKILLS_DIR"

    for skill_dir in "$SCRIPT_DIR"/codex-skills/*/; do
        [ -d "$skill_dir" ] || continue
        skill_name="$(basename "$skill_dir")"
        dst="$CODEX_SKILLS_DIR/$skill_name"

        if [ -d "$dst" ]; then
            rm -rf "$dst"
            cp -R "$skill_dir" "$dst"
            ok "  $skill_name — updated"
        else
            cp -R "$skill_dir" "$dst"
            ok "  $skill_name — installed"
        fi
    done
else
    warn "  ~/.codex/ not found — skipping Codex skills"
    warn "  Install Codex CLI first, then re-run ./install.sh"
fi

echo ""

# ── Step 3.5: Operator files (command + agent definition) ─
# ~/.claude/commands/forge.md and ~/.claude/agents/forge-orchestrator.md were
# long-standing UNTRACKED operator state; the toolkit now owns them. Symlink
# with the same regular-file guard the bins use.

info "Step 3.5: Symlinking operator files into ~/.claude/"

for pair in "${OPERATOR_FILES[@]}"; do
    src="$SCRIPT_DIR/${pair%%:*}"
    dst="$HOME/.claude/${pair##*:}"
    mkdir -p "$(dirname "$dst")"

    if [ -L "$dst" ]; then
        existing="$(readlink "$dst")"
        if [ "$existing" = "$src" ]; then
            ok "  ${pair##*:} — already linked"
            continue
        else
            warn "  ${pair##*:} — updating symlink (was: $existing)"
            rm "$dst"
        fi
    elif [ -f "$dst" ]; then
        if cmp -s "$src" "$dst"; then
            rm "$dst"   # byte-identical regular file → safe to converge to a symlink
        else
            warn "  ${pair##*:} — exists as a DIFFERENT regular file, skipping"
            warn "    Diff/merge it into $src, then re-run"
            continue
        fi
    fi

    ln -s "$src" "$dst"
    ok "  ${pair##*:} — linked"
done

echo ""

# Worker context-hygiene reset-capability seed (worker-context-hygiene). Copy the
# fail-closed seed to the runtime path only if the operator has none; never clobber.
_rcf="${FORGE_RESET_CAPABILITY_FILE:-$HOME/.config/forge/reset-capability.yml}"
if [ ! -f "$_rcf" ]; then
    mkdir -p "$(dirname "$_rcf")"
    cp "$SCRIPT_DIR/config/reset-capability.yml" "$_rcf"
    echo "installed fail-closed reset-capability seed -> $_rcf (edit proven:true after the spike + live gate)"
fi

# ── Step 4: Hooks config ─────────────────────────────────

info "Step 4: Claude Code hooks setup"
echo ""

if [ -f "$SETTINGS_FILE" ]; then
    if grep -q "forge-dispatch-pr-review" "$SETTINGS_FILE" 2>/dev/null; then
        ok "  PostToolUse hook already configured in settings.json"
    else
        warn "  You already have a ~/.claude/settings.json."
        warn "  Merge the following into your settings.json 'hooks' section:"
        echo ""
        cat "$SCRIPT_DIR/config/claude-hooks.json"
        echo ""
    fi
else
    warn "  No ~/.claude/settings.json found."
    warn "  Create one or add the hooks section from config/claude-hooks.json"
fi

echo ""

# ── Step 5: Per-project setup ─────────────────────────────

info "Step 5: Per-project setup (do this in each project)"
echo ""
echo "  a) Copy the forge-project config template:"
echo "     mkdir -p .claude"
echo "     cp $SCRIPT_DIR/config/forge-project.example.yml .claude/forge-project.yml"
echo "     # Edit with your project's ports, paths, and test commands"
echo ""
echo "  b) Install the git post-commit hook:"
echo "     cp $SCRIPT_DIR/hooks/post-commit-review.sh scripts/hooks/"
echo "     cp $SCRIPT_DIR/hooks/install-hooks.sh scripts/"
echo "     bash scripts/install-hooks.sh"
echo ""
echo "  c) Add .dev/ to your .gitignore:"
echo "     echo '.dev/' >> .gitignore"
echo ""

# ── Done ──────────────────────────────────────────────────

ok "Installation complete!"
echo ""
info "Quick start:"
echo "  forge-start              # Launch a forge tmux session"
echo "  forge-bridge help        # See all forge-bridge commands"
echo "  forge-bridge context     # Show current forge context"
echo "  forge-watch status       # One-shot scan for blocked pipelines"
echo "  forge-watch install      # Enable background blocked-on-you notifications"
echo "  forge gc --install       # Enable the daily attention-GC launchd backstop"
echo ""
info "Ambient surface + hardened ring (operator-gated live steps):"
echo "  brew install terminal-notifier    # verifiable ring (osascript is the fallback)"
echo "  # SwiftBar: install SwiftBar, then symlink the ambient plugin into its plugins dir:"
echo "  ln -s $SCRIPT_DIR/swiftbar/forge-board.5s.sh \"\$HOME/Library/Application Support/SwiftBar/forge-board.5s.sh\""
echo "  forge-watch selftest              # fire one real notification, then --confirm <id>"
echo ""
info "Skills available (Claude Code + Codex):"
echo "  /adversarial-proposal    # 4-round adversarial planning (Opus)"
echo "  /adversarial-lite        # 2-round lite planning (Sonnet + Opus)"
echo "  /adversarial-implementation  # Implementation from plan"
echo "  /adversarial-qa          # Adversarial QA testing"
echo "  /adversarial-verify      # Verification of QA findings"
echo "  /docs-refresh            # Living documentation refresh"
echo "  /proposal-reviewer       # Independent proposal review (Codex only)"
echo ""
