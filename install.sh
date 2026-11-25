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
FORGE_CONFIG_DIR="${FORGE_CONFIG_DIR:-$HOME/.config/forge}"
MANAGED_POLICY_DIR="$FORGE_CONFIG_DIR/managed/codex-v1"

SKILL_NAMES=(
    forge-orchestrator forge-coder forge-fix-runner fix-coder
    adversarial-proposal adversarial-lite adversarial-implementation
    adversarial-qa adversarial-verify docs-refresh proposal-reviewer command-center
    fix-reproducer adversarial-investigate adversarial-fix-plan
    fix-plan-reviewer adversarial-fix-qa
)
# NOTE: proposal-reviewer ships via codex-skills/ ONLY (no skills/ counterpart);
# the Claude-side uninstall loop no-ops on it by the -d guard. Kept in the list
# so the Codex-side uninstall removes it.

# Operator files symlinked into ~/.claude (same anti-drift pattern as bins):
# "<repo-relative-src>:<dst-under-$HOME/.claude>"
OPERATOR_FILES=(
    "commands/forge.md:commands/forge.md"
    "commands/forge-orchestrator.md:commands/forge-orchestrator.md"
    "commands/forge-status.md:commands/forge-status.md"
    "agents/forge-orchestrator.md:agents/forge-orchestrator.md"
)

# Single bin manifest (was duplicated across install/uninstall/drift).
BIN_SCRIPTS=(forge-bridge forge-start forge-dispatch-review forge-dispatch-pr-review
             forge-stall-install-regex forge-watch forge forge-cc-hook forge-broker
             forge-git-request forge-sync-main forge-web forge-orchestrator-host forge-orchestrator-package)
# Legacy bin symlinks resolve libraries in this source tree. P3's copied
# candidate package must copy this same declared inventory, not glob imports.
LIB_FILES=(lib/forge_orchestrator.py lib/forge_assets.py lib/forge_install.py
           lib/forge_runtime.py lib/forge_native.py lib/forge_python_entry.py lib/forge_sha256sum.py lib/forge_git_entry.py)
MANAGED_POLICY_FILES=(codex-forge.config.toml codex-forge-runtime.json idle-prompts.yml)

# Stage prompt templates. bin/forge-bridge renders $FORGE_PROMPTS_DIR/<stage>.txt for
# every dispatch, so these ARE the pipeline's stage vocabulary — yet until now they were
# untracked, machine-local and unmanaged by this installer, exactly the defect class the
# five missing fix-pipeline skills were (#77 prerequisite).
FORGE_PROMPTS_DIR="$FORGE_CONFIG_DIR/prompts"
# A DECLARED array, not a glob over prompts/*.txt, for the same reason SKILL_NAMES is one:
# a glob cannot report a prompt that ships NOWHERE. It is blind to a missing prompt BY
# CONSTRUCTION, which is how 29 stage prompts stayed untracked while --check-drift
# reported green.
PROMPT_NAMES=(
    _ask_escalation _constraint_check _git_ident _scope_diff_check _unchanged_flow_sweep
    coding coding-fix fix-code fix-investigate fix-investigate-solo
    fix-plan fix-plan-review fix-plan-revise fix-plan-solo fix-qa fix-qa-retry
    fix-reproduce fix-scout impl-review implementation incorporate
    proposal proposal-lite qa qa-fix qa-retry review verify
)
# EXCLUSION 1 — synthetic. `_preamble` is NOT a file and must never become one:
# bin/forge-bridge's resolve_include special-cases the name and returns text rendered by
# _render_preamble from .claude/forge-project.yml, never touching disk. A real
# _preamble.txt would shadow it and silently change all 25 prompts that include it.
# `_operator_constraints` joins it for the SAME reason and one more: it is rendered PER
# SLUG from .dev/proposals/<slug>/constraints.yml, so a real _operator_constraints.txt
# would not merely shadow a synthetic — it would substitute STALE, SLUG-BLIND text for
# live operator constraints in every carrier prompt. It must never appear in
# PROMPT_NAMES and must never exist on disk. T-CC6 is the trap.
# Keep this array on ONE line and paren-free: T-PROMPT-INVENTORY parses it with
# `^NAME=\(([^)]*)\)`, and a paren in the body degrades three checks to a vacuous pass.
SYNTHETIC_PROMPTS=(_preamble _operator_constraints _qa_completion)
# EXCLUSION 2 — quarantined, and for an unrelated reason; do not conflate it with the
# line above. env-fix and qa-live-retry are not project-agnostic templates: they bake one
# private project's plaintext login, database UUIDs, schema, API routes, migration
# filenames, commit SHAs and infra names into the prompt body. This repo is PUBLIC and a
# credential committed here is NOT undone by `git revert`, so they are deliberately not
# vendored and are excluded from the present-but-undeclared drift direction (which would
# otherwise report them forever). Tracked as #79 — reference it, do not redact them here:
# the fix is moving that material into forge-project.yml and reaching it through the
# synthetic _preamble, the way every other prompt already does.
UNVENDORABLE_PROMPTS=(env-fix qa-live-retry)

CODEX_SKILL_NAMES=(forge-orchestrator forge-coder fix-coder adversarial-proposal
    adversarial-lite adversarial-implementation adversarial-qa adversarial-verify
    docs-refresh proposal-reviewer)
DIGEST_NAMES=(proposal proposal-lite review incorporate implementation impl-review
    coding coding-fix qa qa-retry qa-fix verify fix-reproduce fix-investigate
    fix-investigate-solo fix-plan fix-plan-solo fix-plan-review fix-plan-revise
    fix-code fix-qa fix-qa-retry fix-scout)
FORGE_DIGESTS_DIR="$FORGE_CONFIG_DIR/digests"
owned() {
    python3 "$SCRIPT_DIR/lib/forge_install.py" "$1" "$2" "$3" "$FORGE_CONFIG_DIR/install-ownership.json"
}

if [ "${1:-}" = "--stage-candidate" ]; then
    shift
    exec python3 "$SCRIPT_DIR/bin/forge-orchestrator-package" stage "$@"
fi

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
    python3 "$SCRIPT_DIR/lib/forge_assets.py" check || DRIFT=1

    for name in "${DIGEST_NAMES[@]}"; do
        if ! cmp -s "$SCRIPT_DIR/digests/$name.txt" "$FORGE_DIGESTS_DIR/$name.txt"; then
            err "  digests/$name.txt — missing or DIFFERS"; DRIFT=1
        fi
    done
    for dst in "$FORGE_DIGESTS_DIR"/*.txt; do
        [ -e "$dst" ] || continue
        name="$(basename "$dst" .txt)"
        case " ${DIGEST_NAMES[*]} " in *" $name "*) ;; *) err "  undeclared digest: $name"; DRIFT=1 ;; esac
    done

    for library in "${LIB_FILES[@]}"; do
        if [ ! -f "$SCRIPT_DIR/$library" ] || [ -L "$SCRIPT_DIR/$library" ]; then
            err "  $library - declared library missing or symlinked"; DRIFT=1
        fi
    done
    for library in "$SCRIPT_DIR"/lib/*.py; do
        [ -e "$library" ] || continue
        case " ${LIB_FILES[*]} " in
            *" lib/$(basename "$library") "*) ;;
            *) err "  lib/$(basename "$library") - present but undeclared"; DRIFT=1 ;;
        esac
    done

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

    # FORWARD: walk the DECLARED inventory, not the shipped directories. A glob over
    # skills/*/ cannot report a skill that ships NOWHERE — it is blind to a missing skill
    # BY CONSTRUCTION, which is how five fix-pipeline skills stayed absent from this repo
    # while --check-drift reported green. proposal-reviewer is the one documented
    # codex-only deviation (see the note beside SKILL_NAMES) and is skipped here by name.
    for skill_name in "${SKILL_NAMES[@]}"; do
        [ "$skill_name" = proposal-reviewer ] && continue
        skill_dir="$SCRIPT_DIR/skills/$skill_name"; dst="$CLAUDE_SKILLS_DIR/$skill_name"
        if [ ! -d "$skill_dir" ]; then err "  skills/$skill_name — DECLARED in SKILL_NAMES but no source directory in the repo"; DRIFT=1
        elif [ ! -d "$dst" ]; then err "  skills/$skill_name — not installed (claude)"; DRIFT=1
        elif diff -rq --exclude=.DS_Store "$skill_dir" "$dst" >/dev/null 2>&1; then ok "  skills/$skill_name — identical (claude)"
        else err "  skills/$skill_name — DIFFERS from installed (claude):"; diff -rq --exclude=.DS_Store "$skill_dir" "$dst" 2>&1 | sed 's/^/      /' || true; DRIFT=1; fi
    done
    # REVERSE: a skills/ directory absent from SKILL_NAMES still INSTALLS (that loop is a
    # glob) but never UNINSTALLS, because both uninstall loops walk SKILL_NAMES — a silent
    # one-way door. The forward half above cannot see it.
    for skill_dir in "$SCRIPT_DIR"/skills/*/; do
        [ -d "$skill_dir" ] || continue
        skill_name="$(basename "$skill_dir")"
        case " ${SKILL_NAMES[*]} " in
            *" $skill_name "*) ;;
            *) err "  skills/$skill_name — present in the repo but NOT declared in SKILL_NAMES (it would install and never uninstall)"; DRIFT=1 ;;
        esac
    done
    for skill_name in "${CODEX_SKILL_NAMES[@]}"; do
        skill_dir="$SCRIPT_DIR/codex-skills/$skill_name"; dst="$CODEX_SKILLS_DIR/$skill_name"
        if [ ! -d "$skill_dir" ]; then err "  codex-skills/$skill_name — DECLARED but absent from source"; DRIFT=1
        elif [ ! -d "$dst" ]; then err "  codex-skills/$skill_name — not installed (codex)"; DRIFT=1
        elif diff -rq --exclude=.DS_Store "$skill_dir" "$dst" >/dev/null 2>&1; then ok "  codex-skills/$skill_name — identical (codex)"
        else err "  codex-skills/$skill_name — DIFFERS from installed (codex):"; diff -rq --exclude=.DS_Store "$skill_dir" "$dst" 2>&1 | sed 's/^/      /' || true; DRIFT=1; fi
    done

    for pair in "${OPERATOR_FILES[@]}"; do
        src="$SCRIPT_DIR/${pair%%:*}"; dst="$HOME/.claude/${pair##*:}"
        if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then ok "  ${pair##*:} — linked"
        elif [ -e "$dst" ]; then err "  ${pair##*:} — not a toolkit symlink"; DRIFT=1
        else err "  ${pair##*:} — not installed"; DRIFT=1; fi
    done

    for managed in "${MANAGED_POLICY_FILES[@]}"; do
        src="$SCRIPT_DIR/config/$managed"; dst="$MANAGED_POLICY_DIR/$managed"
        if [ ! -f "$dst" ]; then
            err "  managed/codex-v1/$managed — not installed"; DRIFT=1
        elif cmp -s "$src" "$dst"; then
            ok "  managed/codex-v1/$managed — identical"
        else
            err "  managed/codex-v1/$managed — DIFFERS from repository source"; DRIFT=1
        fi
    done
    if [ ! -f "$FORGE_CONFIG_DIR/idle-prompts.yml" ]; then
        err "  idle-prompts.yml — runtime classifier not installed"; DRIFT=1
    elif cmp -s "$MANAGED_POLICY_DIR/idle-prompts.yml" "$FORGE_CONFIG_DIR/idle-prompts.yml"; then
        ok "  idle-prompts.yml — runtime classifier converged"
    else
        err "  idle-prompts.yml — operator override differs from managed classifier"; DRIFT=1
    fi

    # FORWARD · declared-but-absent. Walk PROMPT_NAMES, not a glob — same reason as the
    # SKILL_NAMES loop above.
    for prompt_name in "${PROMPT_NAMES[@]}"; do
        src="$SCRIPT_DIR/prompts/$prompt_name.txt"; dst="$FORGE_PROMPTS_DIR/$prompt_name.txt"
        if [ ! -f "$src" ]; then
            err "  prompts/$prompt_name.txt — DECLARED in PROMPT_NAMES but no source file in the repo"; DRIFT=1
        elif [ ! -f "$dst" ]; then
            err "  prompts/$prompt_name.txt — not installed"; DRIFT=1
        elif cmp -s "$src" "$dst"; then
            ok "  prompts/$prompt_name.txt — identical"
        else
            err "  prompts/$prompt_name.txt — DIFFERS from repository source"; DRIFT=1
        fi
    done
    # REVERSE (repo): a prompts/*.txt absent from PROMPT_NAMES would install (if the
    # install loop globbed) and never uninstall — the same silent one-way door the skills
    # reverse half guards. Here it is stricter: an undeclared file never installs at all,
    # so it is dead weight that no drift direction would otherwise mention.
    for src in "$SCRIPT_DIR"/prompts/*.txt; do
        [ -f "$src" ] || continue
        prompt_name="$(basename "$src" .txt)"
        case " ${PROMPT_NAMES[*]} " in
            *" $prompt_name "*) ;;
            *) err "  prompts/$prompt_name.txt — present in the repo but NOT declared in PROMPT_NAMES (it would never install and never uninstall)"; DRIFT=1 ;;
        esac
    done
    # REVERSE (live) · present-but-undeclared. THE direction a glob-based check misses,
    # and the one that matters: a .txt sitting in the live prompts directory that this
    # repo does not track is a stage whose prompt cannot be reviewed, shipped or
    # drift-checked. Both exclusion lists are honoured here BY NAME, for the two separate
    # reasons documented at their declarations.
    if [ -d "$FORGE_PROMPTS_DIR" ]; then
        for dst in "$FORGE_PROMPTS_DIR"/*.txt; do
            [ -f "$dst" ] || continue
            prompt_name="$(basename "$dst" .txt)"
            case " ${PROMPT_NAMES[*]} " in *" $prompt_name "*) continue ;; esac
            case " ${SYNTHETIC_PROMPTS[*]} " in
                *" $prompt_name "*) err "  prompts/$prompt_name.txt — shadows a synthetic include"; DRIFT=1; continue ;;
            esac
            # Silently, not as a warning: reporting these two on every run forever is the
            # exact noise the exclusion exists to remove. The reason lives at the
            # UNVENDORABLE_PROMPTS declaration, where a reader who wonders why they are
            # missing from prompts/ will look.
            case " ${UNVENDORABLE_PROMPTS[*]} " in *" $prompt_name "*) continue ;; esac
            err "  prompts/$prompt_name.txt — present in the live prompts directory but NOT tracked in prompts/ (add it to prompts/ and PROMPT_NAMES)"; DRIFT=1
        done
    fi

    echo ""
    if [ "$DRIFT" -eq 0 ]; then ok "No drift — repo and installed state converged."; exit 0
    else err "Drift found (see above). Existing assets are preserved; reconcile explicitly or stage a distinct candidate."; exit 1; fi
fi

# ── Uninstall ──────────────────────────────────────────────

if [ "${1:-}" = "--uninstall" ]; then
    # A legacy native session can reread these paths after compaction. Refuse
    # real-home uninstall while native clients are live; isolated HOME fixtures
    # own no running user clients and remain independently uninstallable.
    python3 - "$HOME" <<'PY'
import os, pathlib, pwd, subprocess, sys
if pathlib.Path(sys.argv[1]).resolve() == pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir).resolve():
    result = subprocess.run(['/usr/bin/pgrep', '-x', 'claude|codex'], capture_output=True)
    if result.returncode == 0:
        raise SystemExit('UNINSTALL_REFUSED: native Claude/Codex clients are live; leave legacy discovery intact until they exit.')
    if result.returncode != 1:
        raise SystemExit('UNINSTALL_REFUSED: could not establish that native clients are stopped.')
PY
    info "Uninstalling forge toolkit..."

    # No ambient launchd or candidate discovery assets are owned by this installer.
    for script in "${BIN_SCRIPTS[@]}"; do
        owned remove "$SCRIPT_DIR/bin/$script" "$BIN_DIR/$script"
    done
    for skill in "${SKILL_NAMES[@]}"; do
        owned remove "$SCRIPT_DIR/skills/$skill" "$CLAUDE_SKILLS_DIR/$skill"
    done
    for skill in "${CODEX_SKILL_NAMES[@]}"; do
        owned remove "$SCRIPT_DIR/codex-skills/$skill" "$CODEX_SKILLS_DIR/$skill"
    done
    for pair in "${OPERATOR_FILES[@]}"; do
        owned remove "$SCRIPT_DIR/${pair%%:*}" "$HOME/.claude/${pair##*:}"
    done
    for managed in "${MANAGED_POLICY_FILES[@]}"; do
        owned remove "$SCRIPT_DIR/config/$managed" "$MANAGED_POLICY_DIR/$managed"
    done
    owned remove "$SCRIPT_DIR/config/idle-prompts.yml" "$FORGE_CONFIG_DIR/idle-prompts.yml"
    owned remove "$SCRIPT_DIR/config/orchestrator-assets.json" "$FORGE_CONFIG_DIR/orchestrator-assets.json"
    for name in "${PROMPT_NAMES[@]}"; do
        owned remove "$SCRIPT_DIR/prompts/$name.txt" "$FORGE_PROMPTS_DIR/$name.txt"
    done
    for name in "${DIGEST_NAMES[@]}"; do
        owned remove "$SCRIPT_DIR/digests/$name.txt" "$FORGE_DIGESTS_DIR/$name.txt"
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
for library in "${LIB_FILES[@]}"; do
    if [ ! -f "$SCRIPT_DIR/$library" ] || [ -L "$SCRIPT_DIR/$library" ]; then
        err "Required library missing or symlinked: $library"
        exit 1
    fi
done
echo ""

# Validate the whole declared closure before any installation mutation.
python3 "$SCRIPT_DIR/lib/forge_assets.py" check
for script in "${BIN_SCRIPTS[@]}"; do
    owned link "$SCRIPT_DIR/bin/$script" "$BIN_DIR/$script"
done
for skill in "${SKILL_NAMES[@]}"; do
    [ "$skill" = proposal-reviewer ] && continue
    owned copy "$SCRIPT_DIR/skills/$skill" "$CLAUDE_SKILLS_DIR/$skill"
done
if [ -d "$HOME/.codex" ]; then
    for skill in "${CODEX_SKILL_NAMES[@]}"; do
        owned copy "$SCRIPT_DIR/codex-skills/$skill" "$CODEX_SKILLS_DIR/$skill"
    done
fi
for pair in "${OPERATOR_FILES[@]}"; do
    owned link "$SCRIPT_DIR/${pair%%:*}" "$HOME/.claude/${pair##*:}"
done
for managed in "${MANAGED_POLICY_FILES[@]}"; do
    owned copy "$SCRIPT_DIR/config/$managed" "$MANAGED_POLICY_DIR/$managed"
done
owned copy "$SCRIPT_DIR/config/idle-prompts.yml" "$FORGE_CONFIG_DIR/idle-prompts.yml"
owned copy "$SCRIPT_DIR/config/orchestrator-assets.json" "$FORGE_CONFIG_DIR/orchestrator-assets.json"
for name in "${PROMPT_NAMES[@]}"; do
    owned copy "$SCRIPT_DIR/prompts/$name.txt" "$FORGE_PROMPTS_DIR/$name.txt"
done
for name in "${DIGEST_NAMES[@]}"; do
    owned copy "$SCRIPT_DIR/digests/$name.txt" "$FORGE_DIGESTS_DIR/$name.txt"
done

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
