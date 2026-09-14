#!/bin/bash
# Exercise the bridge's note writers without a live tmux session.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BRIDGE="$ROOT/bin/forge-bridge"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/forge-context-notes.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
CONTEXT_PATH="$WORK/forge-context.test.yml"
DEV_DIR="$WORK"
FORGE_WORKER_HYGIENE_MODE=enforce

require_identity() { :; }
_require_root_cwd() { :; }
_context_file() { printf '%s\n' "$CONTEXT_PATH"; }
timestamp() { printf '2026-09-14T00:00:00Z'; }
_emit_event() { :; }
eval "$(sed -n '/^update_context() {/,/^}/p; /^cmd_add_note() {/,/^}/p' "$BRIDGE")"

printf 'active_pipeline: demo\nnotes: []\n' > "$CONTEXT_PATH"
note1='regex ^\s*FOO\b; quote "yes"; slash \'
note1+=$'\n'
note1+='next line: \\'
note2='Python-looking "); __import__("os"); # and $HOME'
cmd_add_note "$note1" >/dev/null
cmd_add_note "$note2" >/dev/null

assert_notes() {
    NOTE_1="$note1" NOTE_2="$note2" python3 - "$CONTEXT_PATH" <<'PY'
import os
import sys
import yaml

with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
assert data["active_pipeline"] == "demo"
assert data["notes"] == [os.environ["NOTE_1"], os.environ["NOTE_2"]]
PY
}

assert_notes
update_context demo commit-review done worker
assert_notes

printf 'active_pipeline: demo\nnotes:\n  - "invalid \\s"\n' > "$CONTEXT_PATH"
update_context demo commit-review done worker
python3 - "$CONTEXT_PATH" <<'PY'
import sys
import yaml

with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
assert data["active_pipeline"] == "demo"
assert data["notes"] == []
PY
