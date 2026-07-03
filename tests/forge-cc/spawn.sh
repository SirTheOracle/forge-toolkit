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

# ── spawn decision logic (T6-T14): fake tmux via FORGE_TMUX_BIN ────────────
# Recording fake: logs argv; behavior tuned via FAKE_NEW_RC (new-session exit)
# and FAKE_PANES (list-panes line count). list-sessions always fails (the
# at-root listing goes through FORGE_TMUX_LIST when a session should be seen).
FAKE="$WORK/faketmux"; cat > "$FAKE" <<'SH'
#!/bin/bash
echo "$@" >> "${TMUX_LOG:?}"
case "$1" in
  list-sessions) exit 1 ;;
  new-session)   exit "${FAKE_NEW_RC:-0}" ;;
  list-panes)    n="${FAKE_PANES:-5}"; i=0; while [ "$i" -lt "$n" ]; do echo "$i"; i=$((i+1)); done ;;
  show-environment) shift; while [ "${1:-}" != "-t" ] && [ $# -gt 0 ]; do shift; done; echo "TMUX_SESSION=${2:-}" ;;
  *) exit 0 ;;
esac
SH
chmod +x "$FAKE"
TL(){ TMUX_LOG="$WORK/tmux.$1.log"; : > "$TMUX_LOG"; }

echo "── spawn: sanitize + default name + dry-run billing=OK (T6) ──"
new_repo "my app"
TL t6
out=$(TMUX_LOG="$TMUX_LOG" FORGE_TMUX_BIN="$FAKE" spawn --root "$R" --dry-run 2>&1); rc=$?
{ [ "$rc" -eq 0 ] && echo "$out" | grep -q 'new-session -d -s forge-my-app-my-app' && echo "$out" | grep -q 'billing=OK'; } \
  && ok "T6 sanitized default name + billing=OK in dry-run" || bad "T6 wrong (rc=$rc): $out"
echo "$out" | grep -q 'forge-my app' && bad "T6 unsanitized space leaked" || ok "T6 no raw space in the tmux target"

echo "── spawn: name collision at a different root (T7) ──"
new_repo colroot; CR="$R"; new_repo otherroot; OR="$R"
printf 'colname\t%s\n' "$OR" > "$WORK/t7.tsv"
TL t7
out=$(TMUX_LOG="$TMUX_LOG" FORGE_TMUX_BIN="$FAKE" FAKE_NEW_RC=1 FORGE_TMUX_LIST="$WORK/t7.tsv" spawn colname --root "$CR" --no-populate 2>&1); rc=$?
{ [ "$rc" -ne 0 ] && echo "$out" | grep -q 'COLLISION'; } \
  && ok "T7 same-name/other-root refused loud" || bad "T7 collision not refused (rc=$rc): $out"

echo "── spawn: ensure no-op + stale-event clear (T8) ──"
new_repo ensroot
printf 'sess-at-root\t%s\n' "$R" > "$WORK/t8.tsv"
mkdir -p "$R/.dev/attention"
echo '{}' > "$R/.dev/attention/spawn-sess-at-root-needs-repair.json"
TL t8
out=$(TMUX_LOG="$TMUX_LOG" FORGE_TMUX_BIN="$FAKE" FAKE_PANES=5 FORGE_TMUX_LIST="$WORK/t8.tsv" spawn --root "$R" 2>&1); rc=$?
{ [ "$rc" -eq 0 ] && echo "$out" | grep -q 'no-op'; } && ok "T8 healthy session at root → ensure no-op" || bad "T8 ensure failed (rc=$rc): $out"
test ! -f "$R/.dev/attention/spawn-sess-at-root-needs-repair.json" \
  && ok "T8 stale needs-repair cleared on ATTACH" || bad "T8 stale event kept"
out=$(TMUX_LOG="$TMUX_LOG" FORGE_TMUX_BIN="$FAKE" FAKE_PANES=5 FORGE_TMUX_LIST="$WORK/t8.tsv" spawn --root "$R" --dry-run 2>&1)
echo "$out" | grep -q 'PLAN ATTACH session=sess-at-root' && ok "T8 dry-run reports PLAN ATTACH" || bad "T8 dry-run plan wrong: $out"

echo "── spawn: unhealthy session → needs-repair, never killed (T9) ──"
new_repo reproot
printf 'sick-sess\t%s\n' "$R" > "$WORK/t9.tsv"
TL t9
out=$(TMUX_LOG="$TMUX_LOG" FORGE_TMUX_BIN="$FAKE" FAKE_PANES=1 FORGE_TMUX_LIST="$WORK/t9.tsv" spawn --root "$R" 2>&1); rc=$?
{ [ "$rc" -ne 0 ] && echo "$out" | grep -q 'needs-repair'; } && ok "T9 unhealthy → refused with needs-repair" || bad "T9 (rc=$rc): $out"
grep -q 'kill-session' "$TMUX_LOG" && bad "T9 kill-session was invoked" || ok "T9 kill-session never invoked"
python3 -c 'import json,sys;e=json.load(open(sys.argv[1]));assert e["schema"]=="cc-spawn/1" and e["event"]=="spawn-state" and e["state"]=="needs-repair" and e["session"]=="sick-sess"' \
  "$R/.dev/attention/spawn-sick-sess-needs-repair.json" 2>/dev/null \
  && ok "T9 needs-repair event written (cc-spawn/1)" || bad "T9 event missing/wrong"
out=$(TMUX_LOG="$TMUX_LOG" FORGE_TMUX_BIN="$FAKE" FAKE_PANES=1 FORGE_TMUX_LIST="$WORK/t9.tsv" spawn --root "$R" --dry-run 2>&1); rc=$?
{ [ "$rc" -eq 3 ] && echo "$out" | grep -q 'PLAN NEEDS-REPAIR'; } && ok "T9 dry-run PLAN NEEDS-REPAIR rc=3" || bad "T9 dry-run (rc=$rc): $out"

echo "── spawn: billing gates; dry-run reports the verdict (T10) ──"
new_repo billroot
TL t10
out=$(ANTHROPIC_API_KEY=sk-x TMUX_LOG="$TMUX_LOG" FORGE_TMUX_BIN="$FAKE" spawn --root "$R" 2>&1); rc=$?
{ [ "$rc" -ne 0 ] && echo "$out" | grep -q 'billing preflight'; } && ok "T10 real spawn refused with API key" || bad "T10 (rc=$rc): $out"
test ! -e "$R/.claude/settings.json" && ok "T10 refused before register (no mutation)" || bad "T10 mutated before billing gate"
out=$(ANTHROPIC_API_KEY=sk-x TMUX_LOG="$TMUX_LOG" FORGE_TMUX_BIN="$FAKE" spawn --root "$R" --dry-run 2>&1); rc=$?
{ [ "$rc" -eq 2 ] && echo "$out" | grep -q 'billing=FAIL' && echo "$out" | grep -q 'DRY-RUN new-session'; } \
  && ok "T10 dry-run still prints the plan with billing=FAIL, rc=2" || bad "T10 dry verdict (rc=$rc): $out"

echo "── spawn: on_spawn parse — valid and malformed (T11) ──"
new_repo osroot
mkdir -p "$R/.claude"
printf 'forge:\n  control_center:\n    on_spawn: ["echo", "POPULATED"]\n' > "$R/.claude/forge-project.yml"
TL t11
out=$(TMUX_LOG="$TMUX_LOG" FORGE_TMUX_BIN="$FAKE" spawn --root "$R" --dry-run 2>&1)
echo "$out" | grep -q 'DRY-RUN on_spawn: echo POPULATED  *forge-' && ok "T11 declared on_spawn parsed + name appended" || bad "T11 parse: $out"
printf 'forge:\n  control_center:\n    on_spawn: "not-a-list"\n' > "$R/.claude/forge-project.yml"
out=$(TMUX_LOG="$TMUX_LOG" FORGE_TMUX_BIN="$FAKE" spawn --root "$R" 2>&1); rc=$?
{ [ "$rc" -ne 0 ] && echo "$out" | grep -q 'must be a non-empty list'; } && ok "T11 malformed on_spawn dies loud" || bad "T11 malformed (rc=$rc): $out"

echo "── spawn: gitignore gate + --force (T12) ──"
UG="$WORK/ungit"; mkdir -p "$UG/.dev"; git -C "$UG" init -q; : > "$UG/.gitignore"; UG="$(cd "$UG" && pwd -P)"
TL t12
out=$(TMUX_LOG="$TMUX_LOG" FORGE_TMUX_BIN="$FAKE" spawn --root "$UG" --no-populate 2>&1); rc=$?
[ "$rc" -ne 0 ] && ok "T12 non-gitignored .dev refused" || bad "T12 accepted ungitignored root"
out=$(TMUX_LOG="$TMUX_LOG" FORGE_TMUX_BIN="$FAKE" spawn --root "$UG" --no-populate --force 2>&1); rc=$?
{ [ "$rc" -eq 0 ] && echo "$out" | grep -q 'SPAWNED'; } && ok "T12 --force proceeds to SPAWNED" || bad "T12 --force (rc=$rc): $out"

echo "── spawn: stale .forge-session does not block CREATE (T13) ──"
new_repo staleroot
echo "dead-session-name" > "$R/.dev/.forge-session"
TL t13
out=$(TMUX_LOG="$TMUX_LOG" FORGE_TMUX_BIN="$FAKE" spawn --root "$R" --dry-run 2>&1); rc=$?
{ [ "$rc" -eq 0 ] && echo "$out" | grep -q 'DRY-RUN new-session'; } \
  && ok "T13 stale .forge-session ignored (identity is session_path)" || bad "T13 (rc=$rc): $out"

echo "── spawn: dry-run mutates nothing (T14) ──"
new_repo dryroot
TL t14
rm -f "$REG"; : > "$FHOME/.config/forge/watch-roots"
out=$(TMUX_LOG="$TMUX_LOG" FORGE_TMUX_BIN="$FAKE" spawn --root "$R" --dry-run 2>&1)
{ test ! -e "$R/.claude/settings.json" && test ! -s "$FHOME/.config/forge/watch-roots" \
  && test ! -e "$REG" && [ -z "$(ls "$R/.dev/attention" 2>/dev/null)" ]; } \
  && ok "T14 dry-run wrote nothing (hooks/watch-roots/registry/events)" || bad "T14 dry-run mutated state"

echo
echo "═══════════════════════════════════════"
printf 'PASS: %d\nFAIL: %d\n' "$PASS" "$FAIL"
echo "═══════════════════════════════════════"
[ "$FAIL" -eq 0 ]
