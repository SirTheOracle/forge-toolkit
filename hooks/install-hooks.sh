#!/bin/bash
# install-hooks.sh — Install forge hooks into the local git hooks directory.
#
# Respects core.hooksPath (works with husky, lefthook, etc.)
# Chains safely with existing shell-script hooks.
# Uses git rev-parse --show-toplevel for robustness.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$REPO_ROOT" ]; then
    echo "ERROR: Not inside a git repository."
    exit 1
fi

HOOK_SRC="$REPO_ROOT/scripts/hooks/post-commit-review.sh"
HOOKS_DIR="$(git config core.hooksPath 2>/dev/null || echo "$REPO_ROOT/.git/hooks")"
HOOK_DST="$HOOKS_DIR/post-commit"

if [ ! -f "$HOOK_SRC" ]; then
    echo "ERROR: $HOOK_SRC not found."
    exit 1
fi

chmod +x "$HOOK_SRC"
mkdir -p "$HOOKS_DIR"

if [ -f "$HOOK_DST" ]; then
    # Check if already installed
    if grep -q "post-commit-review.sh" "$HOOK_DST" 2>/dev/null; then
        echo "Hook already installed at $HOOK_DST"
        exit 0
    fi
    # Verify existing hook is a shell script before chaining
    if head -1 "$HOOK_DST" | grep -q '^#!.*sh'; then
        echo "" >> "$HOOK_DST"
        echo "# --- forge post-commit review hook ---" >> "$HOOK_DST"
        echo "\"$HOOK_SRC\"" >> "$HOOK_DST"
        echo "Hook chained to existing $HOOK_DST"
    else
        echo "WARNING: Existing $HOOK_DST is not a shell script."
        echo "Cannot chain automatically. Install manually:"
        echo "  Add this line to your hook: $HOOK_SRC"
        exit 1
    fi
else
    cat > "$HOOK_DST" << HOOKEOF
#!/bin/bash
"$HOOK_SRC"
HOOKEOF
    chmod +x "$HOOK_DST"
    echo "Hook installed at $HOOK_DST"
fi

# Verify installation
echo ""
if [ -x "$HOOK_DST" ]; then
    echo "Verified: $HOOK_DST is executable."
else
    echo "WARNING: $HOOK_DST is not executable."
fi

if bash -n "$HOOK_DST" 2>/dev/null; then
    echo "Verified: syntax OK."
else
    echo "WARNING: syntax check failed."
fi

echo ""
echo "Done. The post-commit review hook will queue reviews in .dev/reviews/pending/"
