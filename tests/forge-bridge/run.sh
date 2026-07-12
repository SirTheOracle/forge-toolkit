#!/bin/bash
# tests/forge-bridge/run.sh — identity core for bin/forge-bridge (session-pin hardening).
# Harness modeled on tests/forge-infra-lock/run.sh: hermetic mkR roots, real tmux for
# liveness tests (skips cleanly when tmux is unavailable), PASS/FAIL counters,
# EXIT-trap cleanup. bash-3.2-safe.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BRIDGE="$ROOT/bin/forge-bridge"
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); printf '  ok: %s\n' "$1"; }
bad(){ FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$1"; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/fbid.XXXXXX")"; WORK="$(cd "$WORK" && pwd -P)"
trap 'rm -rf "$WORK"' EXIT

# ---- Pure-helper extraction (no main dispatch): ownership_root + _same_root_sessions ----
FNS="$WORK/fns.sh"
sed -n '/^ownership_root()/,/^}$/p; /^_same_root_sessions()/,/^}$/p' "$BRIDGE" > "$FNS"
# shellcheck disable=SC1090
. "$FNS"

# mkR <name> — hermetic project root with forge-project.yml + expected_root pin.
mkR(){ local d="$WORK/$1"; mkdir -p "$d/.claude" "$d/.dev/proposals"; printf 'name: %s\nforge:\n  expected_root: %s\n' "$1" "$d" > "$d/.claude/forge-project.yml"; printf '%s' "$d"; }

echo "== ownership_root / _same_root_sessions (pure helpers) =="

rootA="$(mkR rootA)"; rootB="$(mkR rootB)"
mkdir -p "$rootA/sub/dir"

# T-OWN-1: subdir-created path resolves to the same ownership root as the root itself.
if [ "$(ownership_root "$rootA/sub/dir")" = "$(ownership_root "$rootA")" ] \
   && [ "$(ownership_root "$rootA")" = "$rootA" ]; then
    ok "T-OWN-1 subdir collapses to project root"
else
    bad "T-OWN-1 subdir collapses to project root (got: $(ownership_root "$rootA/sub/dir") vs $(ownership_root "$rootA"))"
fi

# T-OWN-2: symlink alias canonicalizes to the same root as the real path.
ln -s "$rootA" "$WORK/aliasA"
if [ "$(ownership_root "$WORK/aliasA")" = "$(ownership_root "$rootA")" ]; then
    ok "T-OWN-2 symlink alias collapses"
else
    bad "T-OWN-2 symlink alias collapses (got: $(ownership_root "$WORK/aliasA"))"
fi

# T-SRS-1 / T-SRS-2 via the FORGE_TMUX_LIST seam (name<TAB>path, one per line):
# sessA rooted at rootA, sessB rooted at a rootA SUBDIR (same ownership root, custom
# non-forge names), other rooted at rootB (unrelated).
LIST="$WORK/tmuxlist"
printf 'sessA\t%s\nsessB\t%s/sub/dir\nother\t%s\n' "$rootA" "$rootA" "$rootB" > "$LIST"
srs_out="$(FORGE_TMUX_LIST="$LIST" _same_root_sessions "$rootA")"
if printf '%s\n' "$srs_out" | grep -qx 'sessA' && printf '%s\n' "$srs_out" | grep -qx 'sessB'; then
    ok "T-SRS-1 lists BOTH custom-named same-root sessions"
else
    bad "T-SRS-1 lists BOTH custom-named same-root sessions (got: $(printf '%s' "$srs_out" | tr '\n' ' '))"
fi
if printf '%s\n' "$srs_out" | grep -qx 'other'; then
    bad "T-SRS-2 unrelated-root session excluded"
else
    ok "T-SRS-2 unrelated-root session excluded"
fi

echo
printf 'forge-bridge: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
