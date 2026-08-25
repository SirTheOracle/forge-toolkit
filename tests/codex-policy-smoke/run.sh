#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
python3 -m json.tool "$ROOT/config/codex-forge-runtime.json" >/dev/null
python3 -c 'import tomllib,sys;tomllib.load(open(sys.argv[1],"rb"))' "$ROOT/config/codex-forge.config.toml"
python3 - "$ROOT/config/codex-forge-runtime.json" "$ROOT/config/idle-prompts.yml" <<'PY'
import json, sys, yaml
runtime=json.load(open(sys.argv[1]))
idle=yaml.safe_load(open(sys.argv[2]))
interactive=runtime['codex']['supported_interactive_versions']
classifier=runtime['classifier']['supported_codex_versions']
assert interactive == classifier == idle['supported']['codex_versions']
assert '0.148.0' in interactive
assert '0.149.1' in interactive
# Containment-first gate: the allowlist records versions whose containment has
# been reviewed, but an unrecorded version is admitted when every measured
# containment check passes. "refuse" restores hard pinning.
assert runtime['codex'].get('unknown_version_policy') in ('probe', 'refuse')
PY
out="$(FORGE_CODEX_ROLLOUT=contain "$ROOT/bin/forge" codex-lane --root "$ROOT" --stage coding --worker codex-a)"
grep -q '^lane=reviewed-host$' <<<"$out"
out="$(FORGE_CODEX_ROLLOUT=contain "$ROOT/bin/forge" codex-lane --root "$ROOT" --stage review --worker codex-a)"
grep -q '^lane=codex$' <<<"$out"
out="$(FORGE_CODEX_ROLLOUT=contain "$ROOT/bin/forge" codex-lane --root "$ROOT" --stage coding --worker claude-opus)"
grep -q '^lane=reviewed-host$' <<<"$out"
out="$(FORGE_CODEX_ROLLOUT=contain "$ROOT/bin/forge" codex-lane --root "$ROOT" --stage review --worker claude-opus)"
grep -q '^lane=claude$' <<<"$out"
out="$(FORGE_CODEX_ROLLOUT=enforce "$ROOT/bin/forge" codex-lane --root "$ROOT" --stage coding --worker codex-a)"
grep -q '^lane=reviewed-host$' <<<"$out" # private runtime/origin gate remains unproven
"$ROOT/bin/forge" codex-launch --root "$ROOT" --session test --pane 3 --effort xhigh --print 2>/dev/null | grep -F -- '--ask-for-approval never'
if [ "${1:-}" != "--live" ]; then echo "policy smoke: hermetic PASS (live gates skipped)"; exit 0; fi
doctor="$($ROOT/bin/forge codex-doctor "$ROOT" || true)"
# Do NOT pin the installed version to the last recorded one: Homebrew moves
# ahead of the allowlist routinely, and asserting equality here is the same
# brittleness that made every upgrade a lockout.
grep -qE '^codex_version=[0-9]+\.[0-9]+\.[0-9]+$' <<<"$doctor"
for field in approval_never filesystem_restricted network_restricted cwd_exact; do grep -q "^$field=true$" <<<"$doctor"; done
# Containment proven => launchable, whether or not the version is recorded.
grep -q '^contained_ready=true$' <<<"$doctor"
grep -q '^launch_ready=true$' <<<"$doctor"
if ! grep -q '^version_supported=true$' <<<"$doctor"; then
  grep -q '^version_admitted_by=containment-probe$' <<<"$doctor"
fi
grep -q '^private_codex_home=false$' <<<"$doctor"
echo "Run the disposable-pane canaries: edit/test succeeds; main/sibling/protected/.git writes and direct commit fail without a prompt; auth file/env/keychain and child listener are inaccessible; broker exact commit and host publish succeed. Store the signed results below the worktree Git dir before enforce."
