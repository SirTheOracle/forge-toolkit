#!/bin/bash
# Harness for bin/forge (dispatch/pointer/billing/merge, hermetic) + forge-cc-hook
# (hermetic FORGE_CC_PANE_META branches + a real-tmux structural role-gate test).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FORGE="$ROOT/bin/forge"; HOOK="$ROOT/bin/forge-cc-hook"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/fc.XXXXXX")"; SESS="fctest-$$"
trap 'tmux kill-session -t "$SESS" 2>/dev/null; rm -rf "$WORK"' EXIT
PASS=0; FAIL=0
# register writes ~/.config/forge/watch-roots — isolate HOME so test runs never
# pollute the real registry. _toolkit_root resolves via $0, unaffected.
FHOME="$WORK/home"; mkdir -p "$FHOME"
reg(){ HOME="$FHOME" "$FORGE" register "$@"; }
ok(){ PASS=$((PASS+1)); printf '  ok: %s\n' "$1"; }
bad(){ FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$1"; }
new_root(){ R="$WORK/$1"; mkdir -p "$R/.dev"; git -C "$R" init -q 2>/dev/null; echo '.dev/' > "$R/.gitignore"; TSV="$WORK/$1.tsv"; printf 'forge-x\t%s\n' "$R" > "$TSV"; }
meta(){ printf '%s\t%s\t%s' "$1" "forge-x" "$2"; }   # pane_index, path

echo "── dispatch: inline schema + marker (hermetic) ──"
new_root d1
out=$(FORGE_TMUX_LIST="$TSV" FORGE_DISPATCH_DRY_RUN=1 "$FORGE" dispatch @forge-x "short instruction" --allow-api-billing --sender seat 2>&1)
ev=$(ls "$R"/.dev/attention/dispatch-*.json 2>/dev/null | head -1)
[ -n "$ev" ] && ok "inline dispatch wrote an event" || bad "no event: $out"
python3 - "$ev" <<'PY' && ok "inline event fields correct" || bad "inline schema wrong"
import json,sys,hashlib
e=json.load(open(sys.argv[1]))
assert e["schema"]=="cc-dispatch/1" and e["event"]=="dispatch"
assert e["session"]=="forge-x" and e["target_pane"]==1 and e["mode"]=="inline"
assert e["state"]=="queued-input" and e["sender"]=="seat" and e["answers_ask_id"] is None
assert e["instruction_sha256"]==hashlib.sha256(b"short instruction").hexdigest()
PY
echo "$out" | grep -q 'dispatch_id:' && ok "inline inject carries the marker" || bad "no marker"

echo "── dispatch: pointer mode (multiline) atomic + hash ──"
new_root d2
FORGE_TMUX_LIST="$TSV" FORGE_DISPATCH_DRY_RUN=1 "$FORGE" dispatch @forge-x "$(printf 'line one\nline two\nline three')" --allow-api-billing >/dev/null 2>&1
ev=$(ls "$R"/.dev/attention/dispatch-*.json | head -1)
python3 - "$ev" "$R" <<'PY' && ok "pointer payload atomic + sha256 matches, no residue" || bad "pointer mismatch"
import json,sys,hashlib,os
e=json.load(open(sys.argv[1])); root=sys.argv[2]
assert e["mode"]=="pointer"
p=os.path.join(root, e["payload_path"]); body=open(p).read()
assert hashlib.sha256(body.encode()).hexdigest()==e["instruction_sha256"]
assert "line one" in body and "line three" in body
assert not [x for x in os.listdir(os.path.dirname(p)) if ".tmp." in x]
PY

echo "── dispatch: secret redaction in snippet ──"
new_root d3
FORGE_TMUX_LIST="$TSV" FORGE_DISPATCH_DRY_RUN=1 "$FORGE" dispatch @forge-x "deploy with sk-abcdef1234567890 now" --allow-api-billing >/dev/null 2>&1
ev=$(ls "$R"/.dev/attention/dispatch-*.json | head -1)
grep -q 'sk-abcdef' "$ev" && bad "secret leaked into snippet" || ok "secret redacted in snippet"

echo "── billing preflight ──"
new_root d4
ANTHROPIC_API_KEY=sk-x FORGE_TMUX_LIST="$TSV" FORGE_DISPATCH_DRY_RUN=1 "$FORGE" dispatch @forge-x "x" >/dev/null 2>&1 \
  && bad "dispatch proceeded with API key" || ok "dispatch refused with ANTHROPIC_API_KEY set"
pfout=$(ANTHROPIC_API_KEY=sk-x "$FORGE" preflight --root "$R" 2>&1); pfrc=$?
{ [ "$pfrc" -eq 2 ] && echo "$pfout" | grep -q FAIL; } && ok "preflight reports FAIL (exit 2)" || bad "env key missed (exit=$pfrc)"
ANTHROPIC_API_KEY=sk-x "$FORGE" preflight --root "$R" --allow-api-billing >/dev/null 2>&1 && ok "--allow-api-billing → exit 0" || bad "override did not exit 0"
mkdir -p "$R/.claude"; printf '{"apiKeyHelper":"/bin/echo"}' > "$R/.claude/settings.local.json"
pfout=$(env -u ANTHROPIC_API_KEY "$FORGE" preflight --root "$R" 2>&1) || true
echo "$pfout" | grep -q FAIL && ok "apiKeyHelper in settings.local.json caught" || bad "apiKeyHelper missed"
rm -f "$R/.claude/settings.local.json"

echo "── forge-cc-hook: hermetic branches (FORGE_CC_PANE_META) ──"
new_root h1
out=$(printf '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"},"permission_suggestions":["allow-once"]}' | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" permissionrequest)
[ -z "$out" ] && ok "PermissionRequest: zero stdout (fail-open)" || bad "hook printed stdout: $out"
pf=$(ls "$R"/.dev/attention/perm.forge-x.*.json | head -1)
python3 -c 'import json,sys;e=json.load(open(sys.argv[1]));assert e["tool_name"]=="Bash" and e["state"]=="needs-input" and "allow-once" in e["permission_suggestions"]' "$pf" && ok "perm row structured" || bad "perm row wrong"
printf '{"last_assistant_message":"token=ghp_ABCDEFGHIJKLMNOPQRSTUVWX all done"}' | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" stop
grep -q 'ghp_ABCDEF' "$R/.dev/attention/stop.forge-x.json" && bad "secret leaked in stop snippet" || ok "stop snippet redacted"
printf '{"last_assistant_message":"which table, users or orders?"}' | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" stop
python3 -c 'import json;assert json.load(open("'"$R"'/.dev/attention/stop.forge-x.json"))["looks_like_question"] is True' && ok "trailing ? → looks_like_question" || bad "question flag missed"
# pane 0 (worker) writes nothing; non-forge root inert
rm -f "$R"/.dev/attention/stop.* 2>/dev/null
printf '{"last_assistant_message":"worker done"}' | FORGE_CC_PANE_META="$(meta 0 "$R")" "$HOOK" stop
test ! -f "$R/.dev/attention/stop.forge-x.json" && ok "pane-0 (worker) Stop ignored by role gate" || bad "worker Stop leaked"
N="$WORK/notforge"; mkdir -p "$N"
printf '{"prompt":"hi"}' | FORGE_CC_PANE_META="$(meta 1 "$N")" "$HOOK" userpromptsubmit
test ! -d "$N/.dev" && ok "hook inert in non-forge root (no .dev)" || bad "hook wrote into non-forge root"

echo "── forge-cc-hook: STRUCTURAL path on a real tmux session (NO FORGE_ROLE) ──"
if command -v tmux >/dev/null 2>&1; then
  new_root h2
  tmux new-session -d -s "$SESS" -c "$R"; tmux split-window -t "$SESS" -c "$R"   # pane 0 + 1
  p1=$(tmux list-panes -t "$SESS" -F '#{pane_index} #{pane_id}' | awk '$1==1{print $2}')
  p0=$(tmux list-panes -t "$SESS" -F '#{pane_index} #{pane_id}' | awk '$1==0{print $2}')
  printf '{"last_assistant_message":"done here"}' | env -u FORGE_ROLE -u TMUX_SESSION TMUX_PANE="$p1" "$HOOK" stop
  test -f "$R/.dev/attention/stop.$SESS.json" && ok "pane-1 structural fire wrote event (no FORGE_ROLE)" || bad "structural pane-1 silent"
  rm -f "$R/.dev/attention/"stop.*.json 2>/dev/null
  printf '{"last_assistant_message":"worker done"}' | env -u FORGE_ROLE -u TMUX_SESSION TMUX_PANE="$p0" "$HOOK" stop
  test ! -f "$R/.dev/attention/stop.$SESS.json" && ok "pane-0 structural gate wrote nothing" || bad "pane-0 wrote (worker leaked)"
  tmux kill-session -t "$SESS" 2>/dev/null
else
  echo "  (skip: no tmux)"
fi

echo "── hook merge tool (on COPIES of live settings) ──"
for pair in "headless_factory:PostToolUse" "feedforge:PreToolUse" "goparent-ai:__nohooks__"; do
  name="${pair%%:*}"; expect="${pair##*:}"
  src="/Users/sirdrafton/sirtheoracle/automation/$name/.claude/settings.json"
  [ -f "$src" ] || { echo "  (skip $name — settings absent)"; continue; }
  C="$WORK/reg-$name"; mkdir -p "$C/.claude" "$C/.dev"; git -C "$C" init -q; echo '.dev/' > "$C/.gitignore"
  cp "$src" "$C/.claude/settings.json"
  reg "$C" >/dev/null 2>&1
  python3 - "$C/.claude/settings.json" "$expect" <<'PY' && ok "$name: preserved + 4 CC hooks added" || bad "$name merge broke data"
import json,sys
d=json.load(open(sys.argv[1])); expect=sys.argv[2]; h=d["hooks"]
for ev in ("UserPromptSubmit","Stop","PermissionRequest","Notification"):
    assert isinstance(h.get(ev),list) and any("forge-cc-hook" in x["hooks"][0]["command"] for x in h[ev]), ev
if expect!="__nohooks__": assert expect in h, f"lost {expect}"
if "permissions" in d: assert d["permissions"]
PY
  before=$(md5 -q "$C/.claude/settings.json" 2>/dev/null || md5sum "$C/.claude/settings.json")
  reg "$C" >/dev/null 2>&1
  after=$(md5 -q "$C/.claude/settings.json" 2>/dev/null || md5sum "$C/.claude/settings.json")
  [ "$before" = "$after" ] && ok "$name: re-register idempotent" || bad "$name idempotency broken"
done

echo "── merge tool: refuse ungitignored; --force; no-write on invalid/non-object ──"
U="$WORK/ungit"; mkdir -p "$U/.claude" "$U/.dev"; git -C "$U" init -q; : > "$U/.gitignore"
echo '{}' > "$U/.claude/settings.json"
reg "$U" >/dev/null 2>&1 && bad "did not refuse ungitignored .dev" || ok "refuses when .dev not gitignored"
reg "$U" --force >/dev/null 2>&1 && ok "--force overrides gitignore gate" || bad "--force failed"
V="$WORK/badjson"; mkdir -p "$V/.claude" "$V/.dev"; git -C "$V" init -q; echo '.dev/' > "$V/.gitignore"
printf '{ broken' > "$V/.claude/settings.json"; b0=$(md5 -q "$V/.claude/settings.json" 2>/dev/null || md5sum "$V/.claude/settings.json")
regout=$(reg "$V" 2>&1) || true
echo "$regout" | grep -qi 'not valid JSON' && ok "refuses invalid JSON" || bad "did not refuse invalid JSON"
b1=$(md5 -q "$V/.claude/settings.json" 2>/dev/null || md5sum "$V/.claude/settings.json")
[ "$b0" = "$b1" ] && ok "invalid file bytes unchanged (no clobber)" || bad "clobbered invalid file"
W="$WORK/nonobj"; mkdir -p "$W/.claude" "$W/.dev"; git -C "$W" init -q; echo '.dev/' > "$W/.gitignore"
printf '{"hooks":"not-an-object"}' > "$W/.claude/settings.json"; w0=$(md5 -q "$W/.claude/settings.json" 2>/dev/null || md5sum "$W/.claude/settings.json")
reg "$W" >/dev/null 2>&1 && bad "accepted non-object .hooks" || ok "refuses non-object .hooks"
w1=$(md5 -q "$W/.claude/settings.json" 2>/dev/null || md5sum "$W/.claude/settings.json")
[ "$w0" = "$w1" ] && ok "non-object file bytes unchanged (no clobber)" || bad "clobbered non-object file"

echo "── gc: watch-roots ∪ tmux sweep, TTL, exit 0 (hermetic) ──"
G="$WORK/gcroot"; mkdir -p "$G/.dev/attention/payloads"
G2="$WORK/gcroot2"; mkdir -p "$G2/.dev/attention"
echo '{}' > "$G/.dev/attention/dispatch-old.json";  touch -t 202601010000 "$G/.dev/attention/dispatch-old.json"
echo 'p' > "$G/.dev/attention/payloads/old.txt";    touch -t 202601010000 "$G/.dev/attention/payloads/old.txt"
echo '{}' > "$G/.dev/attention/dispatch-new.json"
echo '{}' > "$G2/.dev/attention/stop-old.json";     touch -t 202601010000 "$G2/.dev/attention/stop-old.json"
GRF="$WORK/gc-roots"; printf '# comment header\n%s\n\n' "$G" > "$GRF"        # G registered
GTSV="$WORK/gc.tsv"; printf 'gc-sess\t%s\n' "$G2" > "$GTSV"                  # G2 live-session only
FORGE_WATCH_ROOTS_FILE="$GRF" FORGE_TMUX_LIST="$GTSV" "$FORGE" gc; grc=$?
[ "$grc" -eq 0 ] && ok "gc sweep exits 0" || bad "gc exited $grc"
test ! -f "$G/.dev/attention/dispatch-old.json" && ok "registered root: stale event GC'd" || bad "stale event survived in watch-root"
test ! -f "$G/.dev/attention/payloads/old.txt" && ok "registered root: stale payload GC'd" || bad "stale payload survived"
test -f "$G/.dev/attention/dispatch-new.json" && ok "fresh event kept (TTL respected)" || bad "fresh event deleted"
test ! -f "$G2/.dev/attention/stop-old.json" && ok "tmux-only root also swept (union)" || bad "tmux-only root missed"
echo '{}' > "$G/.dev/attention/dispatch-mid.json"; touch -t "$(date -v-3d +%Y%m%d%H%M 2>/dev/null || date -d '3 days ago' +%Y%m%d%H%M)" "$G/.dev/attention/dispatch-mid.json"
FORGE_WATCH_ROOTS_FILE="$GRF" FORGE_TMUX_LIST="$GTSV" "$FORGE" gc --days 1
test ! -f "$G/.dev/attention/dispatch-mid.json" && ok "--days overrides TTL" || bad "--days ignored"

echo "═══ PASS: $PASS  FAIL: $FAIL ═══"; [ "$FAIL" -eq 0 ]
