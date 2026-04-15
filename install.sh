#!/bin/bash
# install.sh — Install the forge toolkit
#
# What it does:
#   1. Symlinks bin/ scripts to ~/bin/
#   2. Copies skills/ to ~/.claude/skills/
#   3. Prints instructions for hooks setup
#
# Usage:
#   ./install.sh              # install everything
#   ./install.sh --uninstall  # remove symlinks and skills

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/bin"
SKILLS_DIR="$HOME/.claude/skills"
SETTINGS_FILE="$HOME/.claude/settings.json"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { printf "${CYAN}%s${NC}\n" "$1"; }
ok()    { printf "${GREEN}%s${NC}\n" "$1"; }
warn()  { printf "${YELLOW}%s${NC}\n" "$1"; }
err()   { printf "${RED}%s${NC}\n" "$1"; }

# ── Uninstall ──────────────────────────────────────────────

if [ "${1:-}" = "--uninstall" ]; then
    info "Uninstalling forge toolkit..."

    # Remove bin symlinks
    for script in forge-bridge forge-start forge-dispatch-review; do
        if [ -L "$BIN_DIR/$script" ]; then
            rm "$BIN_DIR/$script"
            ok "  Removed ~/bin/$script"
        fi
    done

    # Remove skill directories
    for skill in forge-orchestrator forge-coder adversarial-proposal adversarial-lite \
                 adversarial-implementation adversarial-qa adversarial-verify docs-refresh; do
        if [ -d "$SKILLS_DIR/$skill" ]; then
            rm -rf "$SKILLS_DIR/$skill"
            ok "  Removed ~/.claude/skills/$skill"
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

for script in forge-bridge forge-start forge-dispatch-review; do
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

# ── Step 2: Skills ────────────────────────────────────────

info "Step 2: Copying skills to ~/.claude/skills/"

mkdir -p "$SKILLS_DIR"

for skill_dir in "$SCRIPT_DIR"/skills/*/; do
    skill_name="$(basename "$skill_dir")"
    dst="$SKILLS_DIR/$skill_name"

    if [ -d "$dst" ]; then
        # Update existing
        rm -rf "$dst"
        cp -R "$skill_dir" "$dst"
        ok "  $skill_name — updated"
    else
        cp -R "$skill_dir" "$dst"
        ok "  $skill_name — installed"
    fi
done

echo ""

# ── Step 3: Hooks config ─────────────────────────────────

info "Step 3: Claude Code hooks setup"
echo ""

if [ -f "$SETTINGS_FILE" ]; then
    if grep -q "forge-dispatch-review" "$SETTINGS_FILE" 2>/dev/null; then
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

# ── Step 4: Per-project setup ─────────────────────────────

info "Step 4: Per-project setup (do this in each project)"
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
echo ""
info "Skills available in Claude Code:"
echo "  /adversarial-proposal    # 4-round adversarial planning (Opus)"
echo "  /adversarial-lite        # 2-round lite planning (Sonnet + Opus)"
echo "  /adversarial-implementation  # Implementation from plan"
echo "  /adversarial-qa          # Adversarial QA testing"
echo "  /adversarial-verify      # Verification of QA findings"
echo "  /docs-refresh            # Living documentation refresh"
echo ""
