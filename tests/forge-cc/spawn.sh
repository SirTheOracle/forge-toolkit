#!/bin/bash
# Harness for bin/forge spawn/registry (Phase C) — hermetic sibling of run.sh
# (run.sh stays pristine; this file carries the registry + spawn assertions).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FORGE="$ROOT/bin/forge"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/fcs.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
PASS=0; FAIL=0
# Isolate HOME (watch-roots + hook merge) AND the registry file. PyYAML lives in
# the REAL user-site (Python derives it from HOME), so pin it via PYTHONPATH or
# every registry call would see ModuleNotFoundError under the fake HOME.
FHOME="$WORK/home"; mkdir -p "$FHOME"
USERSITE="$(python3 -c 'import site; print(site.getusersitepackages())')"
REG="$WORK/registry.yml"
reg(){ HOME="$FHOME" PYTHONPATH="$USERSITE" FORGE_REGISTRY_FILE="$REG" "$FORGE" register "$@"; }
spawn(){ HOME="$FHOME" PYTHONPATH="$USERSITE" FORGE_REGISTRY_FILE="$REG" "$FORGE" spawn "$@"; }
ok(){ PASS=$((PASS+1)); printf '  ok: %s\n' "$1"; }
bad(){ FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$1"; }
new_repo(){ R="$WORK/$1"; mkdir -p "$R/.dev"; git -C "$R" init -q 2>/dev/null; echo '.dev/' > "$R/.gitignore"; R="$(cd "$R" && pwd -P)"; }
rid_of(){ ( cd "$(git -C "$1" rev-parse --path-format=absolute --git-common-dir)" && pwd -P ); }

echo "── registry: entry shape (T1) ──"
new_repo r1
reg "$R" --alias apiserver >/dev/null 2>&1 || bad "register r1 failed"
python3 - "$REG" "$(rid_of "$R")" "$R" <<'PY' && ok "T1 entry: schema/alias/repo_id/reserved-null fields" || bad "T1 entry shape wrong"
import sys, yaml
reg = yaml.safe_load(open(sys.argv[1])); rid = sys.argv[2]; path = sys.argv[3]
assert reg["schema"] == "cc-registry/1"
e = reg["repos"][rid]
assert e["alias"] == "apiserver" and e["repo_id"] == rid and e["path"] == path
assert e["path_exists"] is True
assert e["color_slot"] is None and e["port_range"] is None   # reserved — declaration, not allocation
assert e["registered_at"]
PY

echo "── registry: duplicate alias refused (T2) ──"
new_repo r2
out=$(reg "$R" --alias apiserver 2>&1); rc=$?
{ [ "$rc" -ne 0 ] && echo "$out" | grep -q "already registered"; } \
  && ok "T2 dup alias for a different repo refused loud" || bad "T2 dup alias accepted (rc=$rc): $out"

echo "── registry: corrupt file refused, bytes untouched (T3) ──"
new_repo r3
cp "$REG" "$WORK/reg.bak"
printf '{unclosed' > "$REG"   # genuinely unparseable ('':: x ['' would parse as a dict)
before=$(md5 -q "$REG" 2>/dev/null || md5sum "$REG" | cut -d' ' -f1)
out=$(reg "$R" 2>&1); rc=$?
after=$(md5 -q "$REG" 2>/dev/null || md5sum "$REG" | cut -d' ' -f1)
{ [ "$rc" -ne 0 ] && echo "$out" | grep -qi "corrupt" && [ "$before" = "$after" ]; } \
  && ok "T3 corrupt registry: loud refuse, file untouched" || bad "T3 corrupt handling wrong (rc=$rc): $out"
cp "$WORK/reg.bak" "$REG"

echo "── registry: concurrent writes flock-serialized (T4) ──"
rm -f "$REG"
for i in 1 2 3 4 5 6 7 8; do new_repo "c$i"; done
for i in 1 2 3 4 5 6 7 8; do reg "$WORK/c$i" >/dev/null 2>&1 & done
wait
python3 - "$REG" <<'PY' && ok "T4 flock: valid YAML, 8 distinct repos survived concurrency" || bad "T4 flock lost writes"
import sys, yaml
reg = yaml.safe_load(open(sys.argv[1]))
entries = list(reg["repos"].values())
assert len({e["repo_id"] for e in entries}) == 8, len(entries)
PY
ls "$REG".tmp.* >/dev/null 2>&1 && bad "T4 tmp residue left" || ok "T4 no tmp residue"

echo "── registry: seed from watch-roots (T5) ──"
rm -f "$REG"
new_repo s1; S1="$R"; new_repo s2; S2="$R"
NOTGIT="$WORK/notgit"; mkdir -p "$NOTGIT"
mkdir -p "$FHOME/.config/forge"
printf '%s\n%s\n%s\n' "$S1" "$S2" "$NOTGIT" > "$FHOME/.config/forge/watch-roots"
out=$(reg --seed 2>&1); rc=$?
python3 - "$REG" <<'PY' && ok "T5 seed: 2 git roots imported" || bad "T5 seed entries wrong"
import sys, yaml
reg = yaml.safe_load(open(sys.argv[1]))
assert len(reg["repos"]) == 2
PY
echo "$out" | grep -q "skip (not a git repo)" && ok "T5 non-git line skipped with warning" || bad "T5 non-git skip warning missing: $out"
test ! -e "$S1/.claude/settings.json" && ok "T5 seed bypasses hook merge (no settings.json)" || bad "T5 seed installed hooks"

echo
echo "═══════════════════════════════════════"
printf 'PASS: %d\nFAIL: %d\n' "$PASS" "$FAIL"
echo "═══════════════════════════════════════"
[ "$FAIL" -eq 0 ]
