#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
python3 -m json.tool "$ROOT/config/codex-forge-runtime.json" >/dev/null
python3 -c 'import tomllib,sys;tomllib.load(open(sys.argv[1],"rb"))' "$ROOT/config/codex-forge.config.toml"
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
"$ROOT/bin/forge" codex-launch --root "$ROOT" --session test --pane 2 --effort xhigh --print 2>/dev/null | grep -F -- '--ask-for-approval never'
if [ "${1:-}" != "--live" ]; then echo "policy smoke: hermetic PASS (live gates skipped)"; exit 0; fi
doctor="$($ROOT/bin/forge codex-doctor "$ROOT" || true)"
for field in approval_never filesystem_restricted network_restricted cwd_exact version_supported; do grep -q "^$field=true$" <<<"$doctor"; done
grep -q '^private_codex_home=false$' <<<"$doctor"
echo "Run the disposable-pane canaries: edit/test succeeds; main/sibling/protected/.git writes and direct commit fail without a prompt; auth file/env/keychain and child listener are inaccessible; broker exact commit and host publish succeed. Store the signed results below the worktree Git dir before enforce."
