#!/bin/bash
# Harness for bin/forge (dispatch/pointer/billing/merge, hermetic) + forge-cc-hook
# (hermetic FORGE_CC_PANE_META branches + a real-tmux structural role-gate test).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FORGE="$ROOT/bin/forge"; HOOK="$ROOT/bin/forge-cc-hook"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/fc.XXXXXX")"; SESS="fctest-$$"
trap 'tmux kill-session -t "$SESS" 2>/dev/null; rm -rf "$WORK"' EXIT
PASS=0; FAIL=0
export FORGE_WATCH_TRIGGER=0     # Step 6: no test spawns a background forge-watch check
# register writes ~/.config/forge/watch-roots — isolate HOME so test runs never
# pollute the real registry. _toolkit_root resolves via $0, unaffected.
FHOME="$WORK/home"; mkdir -p "$FHOME"
# PyYAML lives in the REAL user-site (derived from HOME); pin it or register's
# registry write sees ModuleNotFoundError under the fake HOME (Phase C).
USERSITE="$(python3 -c 'import site; print(site.getusersitepackages())')"
reg(){ HOME="$FHOME" PYTHONPATH="$USERSITE" "$FORGE" register "$@"; }
STUB="$WORK/fake-bridge"; cat > "$STUB" <<'SH'
#!/bin/bash
echo "$@" >> "${BRIDGE_LOG:?}"
echo "PWD=$PWD" >> "${BRIDGE_LOG:?}"
exit "${BRIDGE_RC:-0}"
SH
chmod +x "$STUB"
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

echo "── dispatch: redaction boundary — English words with key-shaped substrings survive ──"
new_root d3b
FORGE_TMUX_LIST="$TSV" FORGE_DISPATCH_DRY_RUN=1 "$FORGE" dispatch @forge-x "check the task-notification path and risk-assessment doc" --allow-api-billing >/dev/null 2>&1
ev=$(ls "$R"/.dev/attention/dispatch-*.json | head -1)
python3 - "$ev" <<'PY' && ok "task-notification / risk-assessment survive redaction" || bad "boundary fix regressed: words mangled"
import json,sys
s=json.load(open(sys.argv[1]))["instruction_snippet"]
assert "task-notification" in s and "risk-assessment" in s and "«redacted»" not in s, s
PY
new_root d3c
FORGE_TMUX_LIST="$TSV" FORGE_DISPATCH_DRY_RUN=1 "$FORGE" dispatch @forge-x "set token=abc123 but keep atoken=xyz789" --allow-api-billing >/dev/null 2>&1
ev=$(ls "$R"/.dev/attention/dispatch-*.json | head -1)
python3 - "$ev" <<'PY' && ok "KV boundary: token= redacts, atoken= untouched" || bad "KV boundary wrong"
import json,sys
s=json.load(open(sys.argv[1]))["instruction_snippet"]
assert "token=«redacted»" in s and "atoken=xyz789" in s, s
PY

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

echo "── forge-cc-hook: posttooluse resolves pending permission events ──"
new_root pt1
printf '{"tool_name":"Bash","tool_input":{"command":"npm test"}}' | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" permissionrequest >/dev/null
PH=$(python3 -c "import hashlib;print(hashlib.sha256(b'npm test').hexdigest()[:8])")
test -f "$R/.dev/attention/perm.forge-x.$PH.json" && ok "permissionrequest wrote perm event" || bad "no perm event"
printf '{"tool_name":"Bash","tool_input":{"command":"npm test"}}' | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" posttooluse >/dev/null
test ! -f "$R/.dev/attention/perm.forge-x.$PH.json" && ok "posttooluse archives the matching perm event" || bad "perm event survived posttooluse"
ls "$R/.dev/attention/archive/perm.forge-x.$PH.json.resolved."* >/dev/null 2>&1 && ok "resolved perm preserved in archive/" || bad "no archived copy"

new_root pt2
printf '{"tool_name":"AskUserQuestion","tool_input":{}}' | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" permissionrequest >/dev/null
EH=$(python3 -c "import hashlib;print(hashlib.sha256(b'').hexdigest()[:8])")
printf '{"tool_name":"Read","tool_input":{}}' | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" posttooluse >/dev/null
test -f "$R/.dev/attention/perm.forge-x.$EH.json" && ok "empty-hash collision guarded: Read cannot resolve AskUserQuestion perm" || bad "wrong tool resolved the perm"
printf '{"tool_name":"AskUserQuestion","tool_input":{}}' | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" posttooluse >/dev/null
test ! -f "$R/.dev/attention/perm.forge-x.$EH.json" && ok "matching tool resolves the AskUserQuestion perm" || bad "AskUserQuestion perm not resolved"

new_root pt3
printf '{"tool_name":"Bash","tool_input":{"command":"pytest"}}' | FORGE_CC_PANE_META="$(meta 0 "$R")" "$HOOK" permissionrequest >/dev/null
WH=$(python3 -c "import hashlib;print(hashlib.sha256(b'pytest').hexdigest()[:8])")
test -f "$R/.dev/attention/wperm.forge-x.p0.$WH.json" && ok "worker permissionrequest wrote wperm event" || bad "no wperm event"
printf '{"tool_name":"Bash","tool_input":{"command":"pytest"}}' | FORGE_CC_PANE_META="$(meta 0 "$R")" "$HOOK" posttooluse >/dev/null
test ! -f "$R/.dev/attention/wperm.forge-x.p0.$WH.json" && ok "worker posttooluse archives the wperm event" || bad "wperm survived posttooluse"

echo "── forge-cc-hook: AskUserQuestion content capture (Q1-Q5) ──"
AQ='{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"Deploy to prod?","header":"Deploy","multiSelect":false,"options":[{"label":"yes"},{"label":"no"},{"label":"dry-run"}]}]}}'
EH=$(python3 -c "import hashlib;print(hashlib.sha256(b'').hexdigest()[:8])")
new_root aq1
printf '%s' "$AQ" | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" permissionrequest >/dev/null
test -f "$R/.dev/attention/perm.forge-x.$EH.json" && ok "Q1 keying unchanged (empty-command hash)" || bad "Q1 perm file missing or re-keyed"
python3 - "$R/.dev/attention/perm.forge-x.$EH.json" <<'PY' && ok "Q1 question fields captured" || bad "Q1 question fields wrong"
import json,sys
e=json.load(open(sys.argv[1]))
assert e["question_snippet"]=="Deploy to prod?"
assert e["question_options"]==["yes","no","dry-run"]
assert e["question_count"]==1 and e["multi_select"] is False
assert e["tool_name"]=="AskUserQuestion" and e["command"]==""
PY
new_root aq2
printf '%s' "$AQ" | FORGE_CC_PANE_META="$(meta 0 "$R")" "$HOOK" permissionrequest >/dev/null
python3 - "$R/.dev/attention/wperm.forge-x.p0.$EH.json" <<'PY' && ok "Q2 worker wperm carries question fields" || bad "Q2 wperm question fields wrong"
import json,sys
e=json.load(open(sys.argv[1]))
assert e["question_snippet"]=="Deploy to prod?" and e["question_options"][0]=="yes"
PY
new_root aq3
printf '{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"use api_key: sk-abcdef1234567890 ?","options":[{"label":"token=ghp_ABCDEFGHIJKLMNOPQRSTUVWX"}]}]}}' | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" permissionrequest >/dev/null
{ ! grep -q 'sk-abcdef' "$R/.dev/attention/perm.forge-x.$EH.json" && ! grep -q 'ghp_ABCDEF' "$R/.dev/attention/perm.forge-x.$EH.json"; } \
  && ok "Q3 question text + option labels redacted" || bad "Q3 secret leaked into perm record"
new_root aq4
q4ok=1
for payload in '{"tool_name":"AskUserQuestion","tool_input":{}}' \
               '{"tool_name":"AskUserQuestion","tool_input":{"questions":"what"}}' \
               '{"tool_name":"AskUserQuestion","tool_input":{"questions":[{}]}}' \
               '{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"","options":["notadict"]}]}}'; do
  rm -f "$R/.dev/attention/perm.forge-x.$EH.json"
  printf '%s' "$payload" | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" permissionrequest >/dev/null || q4ok=0
  python3 -c 'import json,sys;e=json.load(open(sys.argv[1]));assert "question_snippet" not in e and "question_options" not in e' \
    "$R/.dev/attention/perm.forge-x.$EH.json" 2>/dev/null || q4ok=0
done
[ "$q4ok" -eq 1 ] && ok "Q4 malformed questions shapes fail-open (record legacy-shaped, exit 0)" || bad "Q4 fail-open violated"
new_root aq5
printf '%s' "$AQ" | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" permissionrequest >/dev/null
printf '%s' "$AQ" | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" posttooluse >/dev/null
test ! -f "$R/.dev/attention/perm.forge-x.$EH.json" && ok "Q5 posttooluse resolves the enriched AskUserQuestion perm" || bad "Q5 enriched perm not resolved"

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
rm -f "$R"/.dev/attention/stop.* "$R"/.dev/attention/wstop.* 2>/dev/null
printf '{"last_assistant_message":"worker done"}' | FORGE_CC_PANE_META="$(meta 0 "$R")" "$HOOK" stop
{ ls "$R"/.dev/attention/wstop.forge-x.p0.*.json >/dev/null 2>&1 && test ! -f "$R/.dev/attention/stop.forge-x.json"; } \
  && ok "pane-0 worker Stop → namespaced wstop; canonical stop.<session> never created" || bad "worker Stop wrong (leaked to canonical or no wstop)"
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
  rm -f "$R/.dev/attention/"stop.*.json "$R/.dev/attention/"wstop.*.json 2>/dev/null
  printf '{"last_assistant_message":"worker done"}' | env -u FORGE_ROLE -u TMUX_SESSION TMUX_PANE="$p0" "$HOOK" stop
  { ls "$R/.dev/attention/"wstop."$SESS".p0.*.json >/dev/null 2>&1 && test ! -f "$R/.dev/attention/stop.$SESS.json"; } \
    && ok "pane-0 structural → namespaced wstop; canonical untouched" || bad "pane-0 structural wrong"
  tmux kill-session -t "$SESS" 2>/dev/null
else
  echo "  (skip: no tmux)"
fi

echo "── forge-cc-hook: worker emission (namespaced per-turn) ──"
new_root w1
printf '{"prompt":"do the thing"}' | FORGE_CC_PANE_META="$(meta 0 "$R")" "$HOOK" userpromptsubmit
python3 - "$R" <<'PY' && ok "worker UserPromptSubmit → wprompt (ptask, role=worker, agent=claude)" || bad "worker prompt wrong"
import json,os,sys
e=json.load(open(os.path.join(sys.argv[1],".dev","attention","wprompt.forge-x.p0.json")))
assert e["role"]=="worker" and e["agent"]=="claude" and e["event"]=="userpromptsubmit", e
assert e["task_id"].startswith("ptask-"), e["task_id"]
assert e["prompt_snippet"]=="do the thing" and e["variant"]=="worker-prompt"
PY
printf '{"last_assistant_message":"worker finished the thing"}' | FORGE_CC_PANE_META="$(meta 0 "$R")" "$HOOK" stop
python3 - "$R" <<'PY' && ok "worker Stop → wstop keyed by recovered task_id; canonical stop.<sess> UNTOUCHED" || bad "worker stop wrong"
import json,os,sys,glob
adir=os.path.join(sys.argv[1],".dev","attention")
ws=glob.glob(os.path.join(adir,"wstop.forge-x.p0.ptask-*.json")); assert len(ws)==1, ws
e=json.load(open(ws[0]))
assert e["role"]=="worker" and e["agent"]=="claude" and e["variant"]=="worker-snippet"
assert e["prompt_snippet"]=="do the thing"                  # copied from the sibling wprompt
assert "worker finished" in e["snippet"]
assert not os.path.exists(os.path.join(adir,"stop.forge-x.json"))   # HARD CONSTRAINT
PY
new_root w1b
printf '{"prompt":"<task-notification> <task-id>abc123def456</task-id> done"}' | FORGE_CC_PANE_META="$(meta 0 "$R")" "$HOOK" userpromptsubmit
python3 - "$R" <<'PY' && ok "hook snip: <task-notification> survives redaction unmangled (sk- boundary)" || bad "sk- boundary regressed: tag mangled to ta«redacted»"
import json,os,sys
e=json.load(open(os.path.join(sys.argv[1],".dev","attention","wprompt.forge-x.p0.json")))
assert e["prompt_snippet"].startswith("<task-notification>") and "«redacted»" not in e["prompt_snippet"], e["prompt_snippet"]
PY
printf '{"prompt":"real key sk-abcdef1234567890 here"}' | FORGE_CC_PANE_META="$(meta 0 "$R")" "$HOOK" userpromptsubmit
python3 - "$R" <<'PY' && ok "hook snip: a real sk- key still redacts" || bad "real key leaked through hook snip"
import json,os,sys
e=json.load(open(os.path.join(sys.argv[1],".dev","attention","wprompt.forge-x.p0.json")))
assert "sk-abcdef" not in e["prompt_snippet"] and "«redacted»" in e["prompt_snippet"], e["prompt_snippet"]
PY
new_root w2
for m in one two; do
  printf '{"prompt":"turn %s"}' "$m" | FORGE_CC_PANE_META="$(meta 0 "$R")" "$HOOK" userpromptsubmit
  printf '{"last_assistant_message":"answer %s"}' "$m" | FORGE_CC_PANE_META="$(meta 0 "$R")" "$HOOK" stop
done
n=$(ls "$R"/.dev/attention/wstop.forge-x.p0.*.json 2>/dev/null | wc -l | tr -d ' ')
[ "$n" -eq 2 ] && ok "two worker Stops in one pane → two distinct wstop files (no LWW collapse)" || bad "got $n wstop (LWW collapse!)"
new_root w3
out=$(printf '{"tool_name":"Bash","tool_input":{"command":"ls"},"permission_suggestions":["allow-once"]}' | FORGE_CC_PANE_META="$(meta 2 "$R")" "$HOOK" permissionrequest)
[ -z "$out" ] && ok "worker PermissionRequest: zero stdout (fail-open)" || bad "worker perm printed: $out"
wpm=$(ls "$R"/.dev/attention/wperm.forge-x.p2.*.json 2>/dev/null | head -1)
python3 -c 'import json,sys;e=json.load(open(sys.argv[1]));assert e["role"]=="worker" and e["tool_name"]=="Bash" and e["state"]=="needs-input"' "$wpm" && ok "wperm row structured (role=worker)" || bad "wperm wrong"

echo "── forge-cc-hook: pane-1 canonical path unchanged ──"
new_root h1d
printf '{"last_assistant_message":"orchestrator answer?"}' | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" stop
python3 -c 'import json,sys;e=json.load(open(sys.argv[1]));assert e["role"]=="orchestrator" and e["variant"]=="snippet" and e["looks_like_question"] is True and "agent" not in e' "$R/.dev/attention/stop.forge-x.json" \
  && ok "pane-1 stop canonical (no agent field; byte-shape preserved)" || bad "pane-1 canonical drifted"
ls "$R"/.dev/attention/wstop.* >/dev/null 2>&1 && bad "pane-1 wrote a wstop" || ok "pane-1 writes NO wstop"

echo "── forge-cc-hook: worker prompt carrying a backed [dispatch_id] keys the task ──"
new_root h1e
python3 - "$R" <<'PY'
import json,os,sys
adir=os.path.join(sys.argv[1],".dev","attention"); os.makedirs(adir,exist_ok=True)
json.dump({"schema":"cc-dispatch/1","dispatch_id":"cc-relay-1","session":"forge-x"},open(os.path.join(adir,"dispatch-cc-relay-1.json"),"w"))
PY
printf '{"prompt":"do X [dispatch_id:cc-fake] then subtask [dispatch_id:cc-relay-1]"}' | FORGE_CC_PANE_META="$(meta 4 "$R")" "$HOOK" userpromptsubmit
python3 -c 'import json;assert json.load(open("'"$R"'/.dev/attention/wprompt.forge-x.p4.json"))["task_id"]=="cc-relay-1"' \
  && ok "backed relayed dispatch_id is the task_id (unbacked cc-fake filtered)" || bad "task_id not the relayed id"

echo "── forge-cc-hook --codex: adapter maps codex payload → worker emission ──"
new_root cx1
printf '{"prompt":"codex task","turn_id":"turn-abc","session_id":"sid","cwd":"%s"}' "$R" \
  | FORGE_CC_PANE_META="$(meta 2 "$R")" "$HOOK" --codex userpromptsubmit
python3 -c 'import json,os,sys;e=json.load(open(os.path.join(sys.argv[1],".dev","attention","wprompt.forge-x.p2.json")));assert e["agent"]=="codex" and e["role"]=="worker" and e["task_id"]=="turn-abc"' "$R" \
  && ok "codex UserPromptSubmit → agent=codex, task_id=turn_id (native correlation)" || bad "codex prompt wrong"
printf '{"last_assistant_message":"codex done, ok?","turn_id":"turn-abc","stop_hook_active":false}' \
  | FORGE_CC_PANE_META="$(meta 2 "$R")" "$HOOK" --codex stop
python3 -c 'import json,os,sys;e=json.load(open(os.path.join(sys.argv[1],".dev","attention","wstop.forge-x.p2.turn-abc.json")));assert e["agent"]=="codex" and e["task_id"]=="turn-abc" and e["looks_like_question"] is True' "$R" \
  && ok "codex Stop → wstop keyed on turn_id, agent=codex, question detected" || bad "codex stop wrong"

echo "── forge-cc-hook --codex: argv-list command permission (crash fix + writer↔resolver key identity) ──"
# T1 — argv-list permissionrequest writes a stringified wperm (closes the AttributeError crash).
new_root cxp1
LH=$(python3 -c "import hashlib;print(hashlib.sha256(b'bash -lc pytest').hexdigest()[:8])")
printf '{"tool_name":"shell","tool_input":{"command":["bash","-lc","pytest"]}}' \
  | FORGE_CC_PANE_META="$(meta 2 "$R")" "$HOOK" --codex permissionrequest >/dev/null; rc=$?
{ [ "$rc" -eq 0 ] && test -f "$R/.dev/attention/wperm.forge-x.p2.$LH.json"; } \
  && ok "codex list-command perm → wperm, no crash, stringified hash" || bad "codex perm crash/miskey (rc=$rc)"
python3 -c 'import json,sys;e=json.load(open(sys.argv[1]));assert e["agent"]=="codex" and e["tool_name"]=="shell" and e["command"]=="bash -lc pytest"' \
  "$R/.dev/attention/wperm.forge-x.p2.$LH.json" && ok "wperm stringified command + agent=codex" || bad "wperm fields wrong"
# T2 — same argv list at posttooluse resolves it (writer↔resolver hash identity).
printf '{"tool_name":"shell","tool_input":{"command":["bash","-lc","pytest"]}}' \
  | FORGE_CC_PANE_META="$(meta 2 "$R")" "$HOOK" --codex posttooluse >/dev/null
test ! -f "$R/.dev/attention/wperm.forge-x.p2.$LH.json" \
  && ok "codex posttooluse archives the matching wperm (hashes match)" || bad "codex wperm survived"

echo "── forge-cc-hook --codex: apply_patch argv list does not crash ──"
# T3 — apply_patch argv list (patch text as element 1). Newline-free patch body: a raw '\n'
# is INVALID JSON under Python's strict json.load and would be swallowed by except Exception
# (no file written); this exercises the identical list.encode() crash path without that trap.
new_root cxp2
printf '{"tool_name":"apply_patch","tool_input":{"command":["apply_patch","*** Begin Patch pytest *** End Patch"]}}' \
  | FORGE_CC_PANE_META="$(meta 3 "$R")" "$HOOK" --codex permissionrequest >/dev/null; rc=$?
{ [ "$rc" -eq 0 ] && ls "$R"/.dev/attention/wperm.forge-x.p3.*.json >/dev/null 2>&1; } \
  && ok "codex apply_patch list → wperm, no crash" || bad "apply_patch crashed (rc=$rc)"

echo "── forge-cc-hook --codex: empty/missing command + tool_name resolve-guard on the list path ──"
# T4 — empty/missing command → empty-hash, no crash; mismatched tool_name does NOT resolve.
new_root cxp3
EH=$(python3 -c "import hashlib;print(hashlib.sha256(b'').hexdigest()[:8])")
printf '{"tool_name":"mcp__x__do","tool_input":{}}' \
  | FORGE_CC_PANE_META="$(meta 2 "$R")" "$HOOK" --codex permissionrequest >/dev/null; rc=$?
{ [ "$rc" -eq 0 ] && test -f "$R/.dev/attention/wperm.forge-x.p2.$EH.json"; } \
  && ok "codex empty command → empty-hash wperm, no crash" || bad "empty command crashed/miskey"
printf '{"tool_name":"other","tool_input":{}}' \
  | FORGE_CC_PANE_META="$(meta 2 "$R")" "$HOOK" --codex posttooluse >/dev/null
test -f "$R/.dev/attention/wperm.forge-x.p2.$EH.json" \
  && ok "mismatched tool_name does NOT resolve codex wperm (guard survives list path)" || bad "wrong tool resolved codex wperm"

echo "── forge-cc-hook: string command (Claude) byte-identical + zero-stdout invariant ──"
# T5 — string command through the orchestrator writer (site 3): identical hash + zero stdout.
new_root cxp4
PH=$(python3 -c "import hashlib;print(hashlib.sha256(b'npm test').hexdigest()[:8])")
out=$(printf '{"tool_name":"Bash","tool_input":{"command":"npm test"}}' \
  | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" permissionrequest)
{ test -f "$R/.dev/attention/perm.forge-x.$PH.json" && [ -z "$out" ]; } \
  && ok "string command hashes identically + zero stdout (Claude byte-identical)" || bad "string re-keyed or emitted stdout"
# T5b (guardian completeness add) — orchestrator writer+resolver (sites 3 & 4) on an argv LIST.
# Claude never sends a list today, but the shared helper now makes these two sites list-tolerant;
# this proves site-3↔site-4 key identity directly (the only otherwise-untested list path).
new_root cxp5
LH2=$(python3 -c "import hashlib;print(hashlib.sha256(b'bash -lc make').hexdigest()[:8])")
printf '{"tool_name":"Bash","tool_input":{"command":["bash","-lc","make"]}}' \
  | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" permissionrequest >/dev/null; rc=$?
{ [ "$rc" -eq 0 ] && test -f "$R/.dev/attention/perm.forge-x.$LH2.json"; } \
  && ok "orchestrator list-command perm → stringified perm (site 3 helper)" || bad "orch list writer crash/miskey (rc=$rc)"
printf '{"tool_name":"Bash","tool_input":{"command":["bash","-lc","make"]}}' \
  | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" posttooluse >/dev/null
test ! -f "$R/.dev/attention/perm.forge-x.$LH2.json" \
  && ok "orchestrator posttooluse resolves the list-keyed perm (site 4 helper, key identity)" || bad "orch list resolver miskey"

echo "── forge-cc-hook: Step 6 worker trigger detaches AND fires (sentinel) ──"
new_root tg1
SENT="$WORK/fired.$$"; rm -f "$SENT"
STUBFW="$WORK/slowwatch"; printf '#!/bin/bash\ntouch %q\nsleep 30\n' "$SENT" > "$STUBFW"; chmod +x "$STUBFW"
start=$(date +%s)
printf '{"last_assistant_message":"done"}' | FORGE_WATCH_TRIGGER=1 FORGE_WATCH_BIN="$STUBFW" FORGE_CC_PANE_META="$(meta 4 "$R")" "$HOOK" stop
elapsed=$(( $(date +%s) - start ))
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -f "$SENT" ] && break; sleep 0.3; done
{ [ "$elapsed" -lt 5 ] && [ -f "$SENT" ]; } && ok "worker Stop trigger detaches (<5s) AND fires (sentinel touched)" || bad "trigger blocked ${elapsed}s or never fired"

echo "── forge-cc-hook: Step 6 orchestrator Stop trigger detaches AND fires (sentinel) ──"
new_root tg2
SENT="$WORK/fired-orch.$$"; rm -f "$SENT"
STUBFW="$WORK/slowwatch2"; printf '#!/bin/bash\ntouch %q\nsleep 30\n' "$SENT" > "$STUBFW"; chmod +x "$STUBFW"
start=$(date +%s)
printf '{"last_assistant_message":"orchestrator done"}' | FORGE_WATCH_TRIGGER=1 FORGE_WATCH_BIN="$STUBFW" FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" stop
elapsed=$(( $(date +%s) - start ))
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -f "$SENT" ] && break; sleep 0.3; done
{ [ "$elapsed" -lt 5 ] && [ -f "$SENT" ]; } && ok "orchestrator Stop trigger detaches AND fires (Diffs 6a/6b)" || bad "orchestrator trigger blocked ${elapsed}s or never fired"

echo "── hook merge tool (on COPIES of live settings) ──"
for pair in "headless_factory:PostToolUse" "feedforge:PreToolUse" "goparent-ai:__nohooks__"; do
  name="${pair%%:*}"; expect="${pair##*:}"
  src="/Users/sirdrafton/sirtheoracle/automation/$name/.claude/settings.json"
  [ -f "$src" ] || { echo "  (skip $name — settings absent)"; continue; }
  C="$WORK/reg-$name"; mkdir -p "$C/.claude" "$C/.dev"; git -C "$C" init -q; echo '.dev/' > "$C/.gitignore"
  cp "$src" "$C/.claude/settings.json"
  reg "$C" >/dev/null 2>&1
  python3 - "$C/.claude/settings.json" "$expect" <<'PY' && ok "$name: preserved + 5 CC hooks added" || bad "$name merge broke data"
import json,sys
d=json.load(open(sys.argv[1])); expect=sys.argv[2]; h=d["hooks"]
for ev in ("UserPromptSubmit","Stop","PermissionRequest","Notification","PostToolUse"):
    assert isinstance(h.get(ev),list) and any("forge-cc-hook" in x["hooks"][0]["command"] for x in h[ev]), ev
if expect!="__nohooks__": assert expect in h, f"lost {expect}"
if "permissions" in d: assert d["permissions"]
PY
  python3 - "$C/.codex/hooks.json" <<'PY' && ok "$name: .codex/hooks.json gains 4 CC events" || bad "$name codex merge incomplete"
import json,sys
h=json.load(open(sys.argv[1]))["hooks"]
for ev in ("UserPromptSubmit","Stop","PermissionRequest","PostToolUse"):
    assert isinstance(h.get(ev),list) and any("--codex "+ev.lower() in x["hooks"][0]["command"] for x in h[ev]), ev
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

echo "── gc: task-aware sweep (unterminated EXEMPT), TTL, exit 0 (hermetic) ──"
G="$WORK/gcroot"; mkdir -p "$G/.dev/attention/payloads"
G2="$WORK/gcroot2"; mkdir -p "$G2/.dev/attention"
OLD=202601010000
D3="$(date -v-3d +%Y%m%d%H%M 2>/dev/null || date -d '3 days ago' +%Y%m%d%H%M)"
echo '{}' > "$G/.dev/attention/dispatch-old.json";  touch -t $OLD "$G/.dev/attention/dispatch-old.json"   # unterminated
echo '{}' > "$G/.dev/attention/dispatch-term.json"; touch -t $OLD "$G/.dev/attention/dispatch-term.json"  # terminated
echo 'r' > "$G/.dev/attention/payloads/response.term.txt"; touch -t $OLD "$G/.dev/attention/payloads/response.term.txt"
echo 'p' > "$G/.dev/attention/payloads/misc.txt";   touch -t $OLD "$G/.dev/attention/payloads/misc.txt"
echo 'r' > "$G/.dev/attention/payloads/response.cc-old.txt"; touch -t $OLD "$G/.dev/attention/payloads/response.cc-old.txt"
echo 'r' > "$G/.dev/attention/payloads/response.cc-new.txt"  # fresh
echo '{}' > "$G/.dev/attention/dispatch-new.json"           # fresh
printf '{"task_id":"ptask-t"}' > "$G/.dev/attention/wprompt.forge-x.p0.json"; touch -t $OLD "$G/.dev/attention/wprompt.forge-x.p0.json"
echo '{}' > "$G/.dev/attention/wstop.forge-x.p0.ptask-t.json"; touch -t $OLD "$G/.dev/attention/wstop.forge-x.p0.ptask-t.json"
printf '{"task_id":"ptask-open"}' > "$G/.dev/attention/wprompt.forge-x.p2.json"; touch -t $OLD "$G/.dev/attention/wprompt.forge-x.p2.json"
echo '{}' > "$G2/.dev/attention/stop-old.json";     touch -t $OLD "$G2/.dev/attention/stop-old.json"
GRF="$WORK/gc-roots"; printf '# comment header\n%s\n\n' "$G" > "$GRF"
GTSV="$WORK/gc.tsv"; printf 'gc-sess\t%s\n' "$G2" > "$GTSV"
GFWC="$WORK/gc-fwcache"; mkdir -p "$GFWC"
FORGE_WATCH_ROOTS_FILE="$GRF" FORGE_TMUX_LIST="$GTSV" FORGE_WATCH_CACHE_DIR="$GFWC" "$FORGE" gc; grc=$?
[ "$grc" -eq 0 ] && ok "gc sweep exits 0" || bad "gc exited $grc"
test -f "$G/.dev/attention/dispatch-old.json" && ok "UNTERMINATED aged dispatch RETAINED (stuck task never vanishes)" || bad "unterminated dispatch deleted"
test ! -f "$G/.dev/attention/dispatch-term.json" && ok "terminated aged dispatch GC'd" || bad "terminated dispatch survived"
test ! -f "$G/.dev/attention/payloads/response.term.txt" && ok "terminated response payload GC'd" || bad "term response survived"
test ! -f "$G/.dev/attention/payloads/misc.txt" && ok "stale generic payload GC'd" || bad "stale payload survived"
test ! -f "$G/.dev/attention/payloads/response.cc-old.txt" && ok "aged bare response payload GC'd" || bad "aged response survived"
test -f "$G/.dev/attention/payloads/response.cc-new.txt" && ok "fresh response payload survives" || bad "fresh response deleted"
test -f "$G/.dev/attention/dispatch-new.json" && ok "fresh event kept (TTL respected)" || bad "fresh event deleted"
test ! -f "$G/.dev/attention/wstop.forge-x.p0.ptask-t.json" && ok "terminated worker turn (wprompt+wstop) GC'd" || bad "terminated worker turn survived"
test -f "$G/.dev/attention/wprompt.forge-x.p2.json" && ok "UNTERMINATED worker prompt (no wstop) RETAINED" || bad "unterminated wprompt deleted"
test ! -f "$G2/.dev/attention/stop-old.json" && ok "tmux-only root also swept (union)" || bad "tmux-only root missed"
echo '{}' > "$G/.dev/attention/dispatch-mid.json";  touch -t "$D3" "$G/.dev/attention/dispatch-mid.json"
echo 'r' > "$G/.dev/attention/payloads/response.mid.txt"; touch -t "$D3" "$G/.dev/attention/payloads/response.mid.txt"
echo '{}' > "$G/.dev/attention/dispatch-stuck.json"; touch -t "$D3" "$G/.dev/attention/dispatch-stuck.json"
FORGE_WATCH_ROOTS_FILE="$GRF" FORGE_TMUX_LIST="$GTSV" FORGE_WATCH_CACHE_DIR="$GFWC" "$FORGE" gc --days 1
test ! -f "$G/.dev/attention/dispatch-mid.json" && ok "--days overrides TTL for a TERMINATED dispatch" || bad "--days ignored"
test -f "$G/.dev/attention/dispatch-stuck.json" && ok "--days still EXEMPTS an unterminated dispatch" || bad "--days deleted a stuck task"
mkfile_sz() { dd if=/dev/zero of="$1" bs=1024 count="$2" 2>/dev/null; }
mkfile_sz "$GFWC/launchd.out" $((11 * 1024))   # 11MB > 10MB cap
printf 'small\n' > "$GFWC/launchd.err"          # under cap
FORGE_WATCH_ROOTS_FILE="$GRF" FORGE_TMUX_LIST="$GTSV" FORGE_WATCH_CACHE_DIR="$GFWC" "$FORGE" gc >/dev/null
[ "$(stat -f%z "$GFWC/launchd.out")" -eq 0 ] && ok "gc truncates launchd.out past 10MB cap" || bad "launchd.out not truncated"
[ -s "$GFWC/launchd.err" ] && ok "gc leaves under-cap launchd.err alone" || bad "launchd.err wrongly truncated"

echo "── forge ask: session-scope event + secret redaction ──"
new_root a1
BRIDGE_LOG="$WORK/bl1"; : > "$BRIDGE_LOG"
BRIDGE_LOG="$BRIDGE_LOG" FORGE_BRIDGE_BIN="$STUB" TMUX_SESSION=forge-x \
  "$FORGE" ask --session-scope "leak sk-abcdef1234567890 token" --root "$R" >/dev/null 2>&1 \
  && ok "ask --session-scope exits 0" || bad "session-scope ask nonzero"
ak=$(ls "$R"/.dev/attention/ask-*.json 2>/dev/null | head -1)
[ -n "$ak" ] && ok "session-scope ask wrote an event" || bad "no ask event"
grep -q 'sk-abcdef' "$ak" && bad "secret leaked in ask snippet" || ok "ask snippet redacted"
python3 -c 'import json,sys;e=json.load(open(sys.argv[1]));assert e["event"]=="ask" and e["variant"]=="ask" and e["mode"]=="session-scope" and e["slug"] is None and e["session"]=="forge-x" and e["ask_id"].startswith("ask-")' "$ak" && ok "session-scope fields (ask-* id, null slug, session)" || bad "session-scope schema wrong"
[ ! -s "$BRIDGE_LOG" ] && ok "session-scope fires NO bridge callback" || bad "session-scope called the bridge"

echo "── forge ask: stage mode (open pending) → event + BLOCKED --quiet callback ──"
new_root a2
mkdir -p "$R/.dev/proposals/p-x"
printf 'entries:\n  - timestamp: "2026-07-03T00:00:00Z"\n    stage: coding\n    to: codex-a\n    response: null\n' > "$R/.dev/proposals/p-x/forge-log.yml"
BRIDGE_LOG="$WORK/bl2"; : > "$BRIDGE_LOG"
BRIDGE_LOG="$BRIDGE_LOG" FORGE_BRIDGE_BIN="$STUB" TMUX_SESSION=forge-x \
  "$FORGE" ask --slug p-x --stage coding --worker codex-a "delete the old table too?" --root "$R" >/dev/null 2>&1
ak=$(ls "$R"/.dev/attention/ask-*.json | head -1)
python3 -c 'import json,sys;e=json.load(open(sys.argv[1]));assert e["mode"]=="stage" and e["slug"]=="p-x" and e["stage"]=="coding" and e["worker"]=="codex-a" and "delete the old table" in e["question_snippet"]' "$ak" && ok "stage-mode ask fields" || bad "stage schema wrong"
{ grep -q -- 'callback .*--slug p-x .*--stage coding .*--status BLOCKED' "$BRIDGE_LOG" && grep -q -- '--quiet' "$BRIDGE_LOG"; } && ok "stage mode raised a quiet BLOCKED callback" || bad "callback args wrong: $(cat "$BRIDGE_LOG")"

echo "── forge ask: NO matching pending → session-scope fallback, warns, no callback ──"
new_root a3
BRIDGE_LOG="$WORK/bl3"; : > "$BRIDGE_LOG"
warn=$(BRIDGE_LOG="$BRIDGE_LOG" FORGE_BRIDGE_BIN="$STUB" TMUX_SESSION=forge-x \
  "$FORGE" ask --slug nope --stage coding --worker codex-a "q?" --root "$R" 2>&1 >/dev/null); rc=$?
[ "$rc" -eq 0 ] && ok "no-pending ask still exits 0 (never fails the worker)" || bad "no-pending ask nonzero"
echo "$warn" | grep -qi 'falling back to session-scope' && ok "warned on fallback" || bad "no fallback warning"
ak=$(ls "$R"/.dev/attention/ask-*.json | head -1)
python3 -c 'import json,sys;assert json.load(open(sys.argv[1]))["mode"]=="session-scope"' "$ak" && ok "fallback event mode=session-scope" || bad "did not downgrade"
[ ! -s "$BRIDGE_LOG" ] && ok "fallback fires no callback" || bad "callback fired without a pending"

echo "── forge ask: bridge callback error still exits 0, event still written ──"
new_root a4
mkdir -p "$R/.dev/proposals/p-x"
printf 'entries:\n  - timestamp: "2026-07-03T00:00:00Z"\n    stage: coding\n    to: codex-a\n    response: null\n' > "$R/.dev/proposals/p-x/forge-log.yml"
BRIDGE_LOG="$WORK/bl4"; : > "$BRIDGE_LOG"
BRIDGE_LOG="$BRIDGE_LOG" BRIDGE_RC=7 FORGE_BRIDGE_BIN="$STUB" TMUX_SESSION=forge-x \
  "$FORGE" ask --slug p-x --stage coding --worker codex-a "q?" --root "$R" >/dev/null 2>&1 \
  && ok "ask exits 0 despite a failing bridge callback" || bad "ask propagated bridge failure"
ls "$R"/.dev/attention/ask-*.json >/dev/null 2>&1 && ok "ask event written on bridge failure" || bad "no event on bridge failure"

echo "── dispatch --answers: archive + consume-once + answers_ask_id ──"
new_root ans1
mkdir -p "$R/.dev/proposals/p-x"
printf 'entries:\n  - timestamp: "2026-07-03T00:00:00Z"\n    stage: coding\n    to: codex-a\n    response: null\n' > "$R/.dev/proposals/p-x/forge-log.yml"
BRIDGE_LOG="$WORK/blA"; : > "$BRIDGE_LOG"
AID=$(BRIDGE_LOG="$BRIDGE_LOG" FORGE_BRIDGE_BIN="$STUB" TMUX_SESSION=forge-x \
  "$FORGE" ask --slug p-x --stage coding --worker codex-a "which table?" --root "$R" 2>/dev/null | awk '/^ASKED/{print $2}')
: > "$BRIDGE_LOG"
out=$(FORGE_TMUX_LIST="$TSV" FORGE_DISPATCH_DRY_RUN=1 FORGE_BRIDGE_BIN="$STUB" BRIDGE_LOG="$BRIDGE_LOG" \
  "$FORGE" dispatch @forge-x "use users" --answers "$AID" --allow-api-billing 2>&1)
{ test ! -f "$R/.dev/attention/$AID.json" && test -f "$R/.dev/attention/archive/$AID.json"; } && ok "ask archived (not deleted)" || bad "ask not archived: $out"
grep -q "callback-consume .*--slug p-x .*--stage coding .*--status BLOCKED" "$BRIDGE_LOG" && ok "consumed the BLOCKED callback once" || bad "no callback-consume: $(cat "$BRIDGE_LOG")"
ev=$(ls "$R"/.dev/attention/dispatch-*.json | head -1)
python3 -c 'import json,sys;assert json.load(open(sys.argv[1]))["answers_ask_id"]==sys.argv[2]' "$ev" "$AID" && ok "dispatch event answers_ask_id set" || bad "answers_ask_id not set"

echo "── dispatch --answers: double-answer fails loud, no re-consume, no inject ──"
: > "$BRIDGE_LOG"
out2=$(FORGE_TMUX_LIST="$TSV" FORGE_DISPATCH_DRY_RUN=1 FORGE_BRIDGE_BIN="$STUB" BRIDGE_LOG="$BRIDGE_LOG" \
  "$FORGE" dispatch @forge-x "again" --answers "$AID" --allow-api-billing 2>&1); rc2=$?
{ [ "$rc2" -ne 0 ] && echo "$out2" | grep -qi 'already answered'; } && ok "second --answers fails loud" || bad "double-answer not rejected (rc=$rc2)"
[ ! -s "$BRIDGE_LOG" ] && ok "no re-consume on the rejected double-answer" || bad "consume ran on double-answer"

echo "── dispatch --answers: session-mismatch refused, ask untouched ──"
new_root ans2
T2="$WORK/ans2.tsv"; printf 'forge-y\t%s\n' "$R" > "$T2"     # forge-y → this root
AID=$(FORGE_BRIDGE_BIN=/bin/true TMUX_SESSION=forge-x \
  "$FORGE" ask --session-scope "whose session?" --root "$R" 2>/dev/null | awk '/^ASKED/{print $2}')  # ask.session=forge-x
FORGE_TMUX_LIST="$T2" FORGE_DISPATCH_DRY_RUN=1 FORGE_BRIDGE_BIN=/bin/true \
  "$FORGE" dispatch @forge-y "x" --answers "$AID" --allow-api-billing >/dev/null 2>&1 \
  && bad "answered the wrong session" || ok "session-mismatch answer refused"
test -f "$R/.dev/attention/$AID.json" && ok "refused answer left the ask live (not archived)" || bad "ask archived on refusal"

echo "── dispatch --answers: unknown / traversal ask-id fails loud ──"
FORGE_TMUX_LIST="$TSV" FORGE_DISPATCH_DRY_RUN=1 FORGE_BRIDGE_BIN=/bin/true \
  "$FORGE" dispatch @forge-x "x" --answers ask-does-not-exist --allow-api-billing >/dev/null 2>&1 \
  && bad "unknown ask-id accepted" || ok "unknown ask-id fails loud"
FORGE_TMUX_LIST="$TSV" FORGE_DISPATCH_DRY_RUN=1 FORGE_BRIDGE_BIN=/bin/true \
  "$FORGE" dispatch @forge-x "x" --answers 'ask-../../etc/passwd' --allow-api-billing >/dev/null 2>&1 \
  && bad "path-traversal ask-id accepted" || ok "path-traversal ask-id rejected"

echo "── return-path: R1 stop persists payload keyed by dispatch_id ──"
new_root r1
python3 - "$R" <<'PY'
import json,os,sys
adir=os.path.join(sys.argv[1],".dev","attention"); os.makedirs(adir,exist_ok=True)
json.dump({"schema":"cc-attention/1","event":"userpromptsubmit","session":"forge-x",
           "dispatch_id":"cc-1","dispatch_ids":["cc-1"]},
          open(os.path.join(adir,"prompt.forge-x.json"),"w"))
PY
python3 -c 'import json;print(json.dumps({"last_assistant_message":"preamble sentence. "*40+"which table, users or orders?"}))' \
  | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" stop
P="$R/.dev/attention/payloads/response.cc-1.txt"
python3 - "$R" "$P" <<'PY' && ok "R1 payload + stop fields correct" || bad "R1 keying/fields wrong"
import json,os,stat,sys
root,p=sys.argv[1],sys.argv[2]
e=json.load(open(os.path.join(root,".dev","attention","stop.forge-x.json")))
assert e["response_paths"]==[".dev/attention/payloads/response.cc-1.txt"], e.get("response_paths")
assert e["response_dispatch_ids"]==["cc-1"], e.get("response_dispatch_ids")
assert e["looks_like_question"] is True and e["truncated"] is False
b=open(p).read(); assert len(b)>400 and b.rstrip().endswith("?"), (len(b),b[-20:])
assert stat.S_IMODE(os.stat(p).st_mode)==0o600, oct(os.stat(p).st_mode)
PY
test -f "$P" && ok "R1 payload file exists at the did-keyed path" || bad "R1 no payload file"

echo "── return-path: R2 question_snippet is tail-anchored ──"
new_root r2
python3 -c 'import json;print(json.dumps({"last_assistant_message":"This is a long declarative preamble that states many facts. "*10+"So, do you want option A or option B?"}))' \
  | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" stop
python3 - "$R" <<'PY' && ok "R2 question_snippet tail has the ?, head snippet does not" || bad "R2 tail/head wrong"
import json,os,sys
e=json.load(open(os.path.join(sys.argv[1],".dev","attention","stop.forge-x.json")))
qs=e["question_snippet"]; head=e["snippet"]
assert qs.rstrip().endswith("?") and "option A or option B" in qs, qs
assert "?" not in head, head          # head is the preamble, no question
PY

echo "── return-path: R3 payload + snippets redacted ──"
new_root r3
python3 -c 'import json;print(json.dumps({"last_assistant_message":"here is a key sk-abcdef1234567890 and a question?"}))' \
  | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" stop
if grep -rq 'sk-abcdef' "$R/.dev/attention/"; then bad "R3 secret leaked in payload/snippet"; else ok "R3 secret redacted everywhere"; fi

echo "── return-path: R4 FORGE_CC_RESPONSE_MAX byte-cut ──"
new_root r4
python3 -c 'import json;print(json.dumps({"last_assistant_message":"x"*200}))' \
  | FORGE_CC_RESPONSE_MAX=64 FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" stop
python3 - "$R" <<'PY' && ok "R4 truncated=true, full_bytes=200, payload<=64" || bad "R4 cap wrong"
import json,os,sys
adir=os.path.join(sys.argv[1],".dev","attention")
e=json.load(open(os.path.join(adir,"stop.forge-x.json")))
assert e["truncated"] is True and e["full_bytes"]==200, (e.get("truncated"),e.get("full_bytes"))
b=open(os.path.join(adir,"payloads","response.forge-x.txt"),"rb").read()
assert len(b)<=64, len(b)
PY

echo "── return-path: R5 coalesced dispatch_ids → payload per did ──"
new_root r5
python3 - "$R" <<'PY'
import json,os,sys
adir=os.path.join(sys.argv[1],".dev","attention"); os.makedirs(adir,exist_ok=True)
for d in ("cc-a","cc-b"):
    json.dump({"schema":"cc-dispatch/1","event":"dispatch","dispatch_id":d,"session":"forge-x","sender":"seat"},
              open(os.path.join(adir,f"dispatch-{d}.json"),"w"))
PY
printf '{"prompt":"do both [dispatch_id:cc-a] and [dispatch_id:cc-b]"}' \
  | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" userpromptsubmit
python3 -c 'import json;print(json.dumps({"last_assistant_message":"done both, ok?"}))' \
  | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" stop
python3 - "$R" <<'PY' && ok "R5 both dids captured + both payloads written" || bad "R5 coalescing wrong"
import json,os,sys
adir=os.path.join(sys.argv[1],".dev","attention")
pj=json.load(open(os.path.join(adir,"prompt.forge-x.json")))
assert pj["dispatch_ids"]==["cc-a","cc-b"], pj.get("dispatch_ids")
e=json.load(open(os.path.join(adir,"stop.forge-x.json")))
assert set(e["response_paths"])=={".dev/attention/payloads/response.cc-a.txt",".dev/attention/payloads/response.cc-b.txt"}, e["response_paths"]
assert os.path.exists(os.path.join(adir,"payloads","response.cc-a.txt"))
assert os.path.exists(os.path.join(adir,"payloads","response.cc-b.txt"))
PY

echo "── return-path: R6 no dids → response.<session>.txt fallback ──"
new_root r6
python3 -c 'import json;print(json.dumps({"last_assistant_message":"just chatting, no dispatch"}))' \
  | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" stop
python3 - "$R" <<'PY' && ok "R6 fallback payload + stop event still written" || bad "R6 fallback wrong"
import json,os,sys
adir=os.path.join(sys.argv[1],".dev","attention")
e=json.load(open(os.path.join(adir,"stop.forge-x.json")))
assert e["response_paths"]==[".dev/attention/payloads/response.forge-x.txt"], e["response_paths"]
assert e["response_dispatch_ids"]==[], e["response_dispatch_ids"]
assert os.path.exists(os.path.join(adir,"payloads","response.forge-x.txt"))
PY

echo "── return-path: R7 snippet head-anchored, looks_like_question intact ──"
new_root r7
python3 -c 'import json;print(json.dumps({"last_assistant_message":"HEADWORD marker at the very start then lots of filler. "*8+"and finally?"}))' \
  | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" stop
python3 - "$R" <<'PY' && ok "R7 snippet starts at head + still a question" || bad "R7 head anchor wrong"
import json,os,sys
e=json.load(open(os.path.join(sys.argv[1],".dev","attention","stop.forge-x.json")))
assert e["snippet"].startswith("HEADWORD marker at the very start"), e["snippet"][:40]
assert e["looks_like_question"] is True
PY

echo "── return-path: R8 unwritable payloads → fail-open, lifecycle intact ──"
new_root r8
mkdir -p "$R/.dev/attention/payloads"; chmod 500 "$R/.dev/attention/payloads"
python3 -c 'import json;print(json.dumps({"last_assistant_message":"answer that cannot be written?"}))' \
  | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" stop; rc=$?
chmod 700 "$R/.dev/attention/payloads"    # restore for cleanup
python3 - "$R" "$rc" <<'PY' && ok "R8 exit 0 + stop event written, no response_paths" || bad "R8 fail-open broken"
import json,os,sys
assert sys.argv[2]=="0", f"hook exit {sys.argv[2]}"
e=json.load(open(os.path.join(sys.argv[1],".dev","attention","stop.forge-x.json")))
assert e["looks_like_question"] is True            # lifecycle fields present
assert "response_paths" not in e, e.get("response_paths")   # payload block bailed out
PY

echo "── return-path: R9 forge reply reads persisted payloads ──"
new_root r9
a="$R/.dev/attention"; mkdir -p "$a/payloads"
printf 'the full worker answer, ending in a question?' > "$a/payloads/response.cc-1.txt"
python3 - "$a" <<'PY'
import json,os,sys
json.dump({"schema":"cc-attention/1","event":"stop","session":"forge-x","emitted_at":"2026-07-04T00:00:00Z",
           "snippet":"HEAD preview","looks_like_question":True,"response_paths":[".dev/attention/payloads/response.cc-1.txt"],
           "response_dispatch_ids":["cc-1"],"truncated":False,"full_bytes":45},
          open(os.path.join(sys.argv[1],"stop.forge-x.json"),"w"))
PY
FORGE_TMUX_LIST="$TSV" "$FORGE" reply @forge-x       | grep -q 'ending in a question?' && ok "R9 bare @session prints latest payload" || bad "R9 bare reply wrong"
FORGE_TMUX_LIST="$TSV" "$FORGE" reply @forge-x cc-1   | grep -q 'ending in a question?' && ok "R9 @session <did> reads did-keyed file" || bad "R9 did reply wrong"
FORGE_TMUX_LIST="$TSV" "$FORGE" reply @forge-x        | grep -q 'ends in a question' && ok "R9 header flags the question" || bad "R9 header missing question flag"
FORGE_TMUX_LIST="$TSV" "$FORGE" reply @forge-x --snippet | grep -q 'HEAD preview' && ok "R9 --snippet prints the head" || bad "R9 snippet wrong"
FORGE_TMUX_LIST="$TSV" "$FORGE" reply @forge-x --json | python3 -c 'import json,sys;j=json.load(sys.stdin);assert j["response_dispatch_ids"]==["cc-1"]' && ok "R9 --json emits the stop event" || bad "R9 json wrong"
FORGE_TMUX_LIST="$TSV" "$FORGE" reply @forge-x cc-nope >/dev/null 2>&1; [ $? -eq 3 ] && ok "R9 unknown did → exit 3" || bad "R9 unknown did not exit 3"
python3 - "$a" <<'PY'
import json,os,sys
e=json.load(open(os.path.join(sys.argv[1],"stop.forge-x.json"))); e["truncated"]=True; e["full_bytes"]=9999
json.dump(e,open(os.path.join(sys.argv[1],"stop.forge-x.json"),"w"))
PY
FORGE_TMUX_LIST="$TSV" "$FORGE" reply @forge-x | grep -q 'truncated; .* of 9999 bytes' && ok "R9 truncated event → note appended" || bad "R9 truncated note missing"

echo "── return-path: R10 dispatch --wait timeout ──"
new_root r10
BL="$WORK/bl-r10"; : > "$BL"
out=$(FORGE_TMUX_LIST="$TSV" FORGE_BRIDGE_BIN="$STUB" BRIDGE_LOG="$BL" TMUX_SESSION=forge-x \
      FORGE_DISPATCH_WAIT_TIMEOUT=1 FORGE_DISPATCH_WAIT_POLL_S=1 \
      "$FORGE" dispatch @forge-x "an open question?" --allow-api-billing --sender seat --wait 2>&1); rc=$?
[ "$rc" -eq 124 ] && ok "R10 --wait timeout → exit 124" || bad "R10 exit=$rc"
echo "$out" | grep -q 'forge reply @forge-x' && ok "R10 timeout prints the reply hint" || bad "R10 no reply hint"
echo "$out" | grep -qi 'absorbed' && ok "R10 timeout hint names absorption" || bad "R10 no absorption note"
[ -s "$BL" ] && ok "R10 dispatch was injected before waiting (bridge logged)" || bad "R10 not injected"
ls "$R"/.dev/attention/dispatch-*.json >/dev/null 2>&1 && ok "R10 dispatch event written (not lost on timeout)" || bad "R10 event lost"
# --timeout as a CLI arg (not the env default) also reaches the poll deadline → exit 124.
out2=$(FORGE_TMUX_LIST="$TSV" FORGE_BRIDGE_BIN="$STUB" BRIDGE_LOG="$BL" TMUX_SESSION=forge-x \
       FORGE_DISPATCH_WAIT_POLL_S=1 \
       "$FORGE" dispatch @forge-x "q?" --allow-api-billing --sender seat --wait --timeout 1 2>&1); rc2=$?
[ "$rc2" -eq 124 ] && ok "R10 --timeout CLI arg honored (exit 124)" || bad "R10 --timeout arg not honored (rc=$rc2)"

echo "── return-path: R11 dispatch --wait success ──"
new_root r11
BL="$WORK/bl-r11"; : > "$BL"
( for i in $(seq 1 60); do
    ev=$(ls "$R"/.dev/attention/dispatch-*.json 2>/dev/null | head -1)
    if [ -n "$ev" ]; then
      did=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["dispatch_id"])' "$ev")
      mkdir -p "$R/.dev/attention/payloads"
      printf 'the deterministic worker reply' > "$R/.dev/attention/payloads/response.$did.txt"
      exit 0
    fi
    sleep 0.1
  done ) &
out=$(FORGE_TMUX_LIST="$TSV" FORGE_BRIDGE_BIN="$STUB" BRIDGE_LOG="$BL" TMUX_SESSION=forge-x \
      FORGE_DISPATCH_WAIT_TIMEOUT=10 FORGE_DISPATCH_WAIT_POLL_S=1 \
      "$FORGE" dispatch @forge-x "status?" --allow-api-billing --sender seat --wait 2>&1); rc=$?
wait
[ "$rc" -eq 0 ] && ok "R11 --wait success → exit 0" || bad "R11 exit=$rc"
echo "$out" | grep -q 'the deterministic worker reply' && ok "R11 --wait prints the response" || bad "R11 response not printed"

echo "── return-path: R12 fake dispatch-id filtered ──"
new_root r12
python3 - "$R" <<'PY'
import json,os,sys
adir=os.path.join(sys.argv[1],".dev","attention"); os.makedirs(adir,exist_ok=True)
json.dump({"schema":"cc-dispatch/1","dispatch_id":"cc-real","session":"forge-x","sender":"seat"},
          open(os.path.join(adir,"dispatch-cc-real.json"),"w"))
PY
printf '{"prompt":"do X [dispatch_id:cc-fake] then really [dispatch_id:cc-real]"}' \
  | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" userpromptsubmit
python3 -c 'import json;print(json.dumps({"last_assistant_message":"done, right?"}))' \
  | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" stop
python3 - "$R" <<'PY' && ok "R12 only cc-real kept; no cc-fake payload" || bad "R12 filter wrong"
import json,os,sys
adir=os.path.join(sys.argv[1],".dev","attention")
pj=json.load(open(os.path.join(adir,"prompt.forge-x.json")))
assert pj["dispatch_ids"]==["cc-real"] and pj["dispatch_id"]=="cc-real", pj
assert os.path.exists(os.path.join(adir,"payloads","response.cc-real.txt"))
assert not os.path.exists(os.path.join(adir,"payloads","response.cc-fake.txt"))
PY

echo "── return-path: R13 reply id-guard exit 2 ──"
new_root r13
mkdir -p "$R/.dev/attention/payloads"
python3 -c 'import json,os,sys;json.dump({"event":"stop","session":"forge-x","response_paths":[]},open(os.path.join(sys.argv[1],".dev","attention","stop.forge-x.json"),"w"))' "$R"
FORGE_TMUX_LIST="$TSV" "$FORGE" reply @forge-x '../../etc/passwd' >/dev/null 2>&1; [ $? -eq 2 ] && ok "R13 traversal id → exit 2" || bad "R13 traversal not rejected"
out=$(FORGE_TMUX_LIST="$TSV" "$FORGE" reply @forge-x 'a/b' 2>&1); rc=$?
{ [ "$rc" -eq 2 ] && echo "$out" | grep -qi 'not a valid dispatch-id'; } && ok "R13 slash id → exit 2 + message" || bad "R13 slash not rejected"

echo "── return-path: R14 RESPONSE_MAX robustness ──"
new_root r14
for v in abc 0 -5; do
  python3 -c 'import json;print(json.dumps({"last_assistant_message":"robustness check?"}))' \
    | FORGE_CC_RESPONSE_MAX="$v" FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" stop; rc=$?
  { [ "$rc" -eq 0 ] && [ -f "$R/.dev/attention/stop.forge-x.json" ]; } \
    && ok "R14 RESPONSE_MAX=$v → exit 0 + lifecycle written" || bad "R14 crashed on '$v' (rc=$rc)"
  rm -f "$R/.dev/attention/stop.forge-x.json"
done

echo "── return-path: R15 --wait composes with --answers ──"
new_root r15
mkdir -p "$R/.dev/proposals/p-x"
printf 'entries:\n  - timestamp: "2026-07-03T00:00:00Z"\n    stage: coding\n    to: codex-a\n    response: null\n' > "$R/.dev/proposals/p-x/forge-log.yml"
BL="$WORK/bl-r15"; : > "$BL"
AID=$(FORGE_BRIDGE_BIN="$STUB" BRIDGE_LOG="$BL" TMUX_SESSION=forge-x \
  "$FORGE" ask --slug p-x --stage coding --worker codex-a "which env?" --root "$R" 2>/dev/null | awk '/^ASKED/{print $2}')
: > "$BL"
FORGE_TMUX_LIST="$TSV" FORGE_BRIDGE_BIN="$STUB" BRIDGE_LOG="$BL" TMUX_SESSION=forge-x \
  FORGE_DISPATCH_WAIT_TIMEOUT=1 FORGE_DISPATCH_WAIT_POLL_S=1 \
  "$FORGE" dispatch @forge-x "prod" --answers "$AID" --allow-api-billing --sender seat --wait >/dev/null 2>&1; rc=$?
[ "$rc" -eq 124 ] && ok "R15 --wait --answers → answer injects, then wait times out (124)" || bad "R15 compose exit=$rc"
test -f "$R/.dev/attention/archive/$AID.json" && ok "R15 --answers consumed the ask (archived) despite --wait" || bad "R15 ask not archived under --wait"

echo "── recover registry lockstep 1: writer schema literals ⊆ registry (R2) ──"
# A new "schema": "cc-…" literal in either writer file MUST gain a registry
# entry (the wperm.*-miss failure mode). Recovery's own output schemas are the
# only exemptions. Event-level drift is caught by lockstep 2 below.
"$FORGE" recover --print-registry > "$WORK/reg.json" 2>/dev/null
python3 - "$WORK/reg.json" "$ROOT/bin/forge" "$ROOT/bin/forge-cc-hook" <<'PY' && ok "every writer schema literal is registered" || bad "registry drift (new schema literal?)"
import json, re, sys
reg = json.load(open(sys.argv[1]))
registered = {e["schema"] for e in reg["records"]}
SELF = {"cc-recover-manifest/1", "cc-recover-report/1", "cc-recover-registry/1"}
lit = re.compile(r"""["']schema["']\s*:\s*["'](cc-[a-z-]+/\d+)["']""")
found = set()
for fp in sys.argv[2:]:
    found |= set(lit.findall(open(fp).read()))
missing = found - registered - SELF
assert not missing, "unregistered writer schemas: %s" % missing
PY

echo "── recover registry lockstep 2: driven writers classify (event-level, R-5) ──"
# Run the REAL writer entry points (forge dispatch/ask + every forge-cc-hook
# branch) into a fresh root, then require recover to classify every produced
# record — an unregistered *event* under a known schema fails here even though
# the schema-literal grep above cannot see it. (spawn-state is not CLI-drivable
# without tmux; its fixture lives in tests/forge-recover T2.)
new_root lk
FORGE_TMUX_LIST="$TSV" FORGE_DISPATCH_DRY_RUN=1 "$FORGE" dispatch @forge-x "lockstep probe" --allow-api-billing >/dev/null 2>&1
BL="$WORK/bl-lk"; : > "$BL"
FORGE_BRIDGE_BIN="$STUB" BRIDGE_LOG="$BL" TMUX_SESSION=forge-x "$FORGE" ask --session-scope "lockstep?" --root "$R" >/dev/null 2>&1
printf '{"prompt":"hi"}' | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" userpromptsubmit >/dev/null 2>&1
printf '{}'              | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" stop >/dev/null 2>&1
printf '{"message":"m"}' | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" notification >/dev/null 2>&1
printf '{"tool_name":"Bash","tool_input":{"command":"x"}}' | FORGE_CC_PANE_META="$(meta 1 "$R")" "$HOOK" permissionrequest >/dev/null 2>&1
printf '{"prompt":"hi"}' | FORGE_CC_PANE_META="$(meta 0 "$R")" "$HOOK" userpromptsubmit >/dev/null 2>&1
printf '{}'              | FORGE_CC_PANE_META="$(meta 0 "$R")" "$HOOK" stop >/dev/null 2>&1
printf '{"tool_name":"Bash","tool_input":{"command":"x"}}' | FORGE_CC_PANE_META="$(meta 0 "$R")" "$HOOK" permissionrequest >/dev/null 2>&1
nrec=$(ls "$R/.dev/attention/"*.json 2>/dev/null | wc -l | tr -d ' ')
out=$(FORGE_RECOVER_TMUX_STATUS=no-server FORGE_WATCH_TRIGGER=0 "$FORGE" recover --root "$R" --dry-run --json 2>/dev/null)
python3 - <<PY && ok "all $nrec driven-writer records classify (0 unknown)" || bad "driven-writer records unclassifiable"
import json
r = json.loads('''$out''')["roots"][0]
assert r["unknown"] == [], r["unknown"]
assert r["candidates"] >= 7, (r["candidates"], r["candidate_files"])
PY

echo "── gc depth pin (Risk R-2): recover archives outside gc reach ──"
new_root gcpin
mkdir -p "$R/.dev/attention/archive/recover-pin-01"
printf '{}' > "$R/.dev/attention/archive/recover-pin-01/x.json"
printf '{}' > "$R/.dev/attention/archive/flat.json"
touch -t 202601010000 "$R/.dev/attention/archive/recover-pin-01/x.json" "$R/.dev/attention/archive/flat.json"
"$FORGE" gc --root "$R" >/dev/null 2>&1
[ -f "$R/.dev/attention/archive/recover-pin-01/x.json" ] && ok "gc never reaches archive/<id>/ (depth-2)" || bad "gc swept a recover archive — R6 broken"
[ ! -f "$R/.dev/attention/archive/flat.json" ] && ok "gc depth-1 reach unchanged" || bad "gc depth-1 sweep regressed"

echo "── T-SEAT-DISPATCH-ROOTBIND: seat send carries cross-session flags + root binding ──"
new_root sb1
BRIDGE_LOG="$WORK/blsb"; : > "$BRIDGE_LOG"
FORGE_TMUX_LIST="$TSV" FORGE_BRIDGE_BIN="$STUB" BRIDGE_LOG="$BRIDGE_LOG" \
  "$FORGE" dispatch @forge-x "rootbind probe" --allow-api-billing >/dev/null 2>&1
grep -q -- 'send --target-session forge-x --cross-session claude' "$BRIDGE_LOG" \
  && ok "seat dispatch passes --target-session/--cross-session to send" \
  || bad "seat dispatch argv wrong: $(cat "$BRIDGE_LOG")"
grep -q 'TMUX_SESSION' "$BRIDGE_LOG" && bad "seat dispatch still leans on TMUX_SESSION" || ok "no TMUX_SESSION in the seat send path"
grep -q "PWD=$(cd "$R" && pwd -P)" "$BRIDGE_LOG" && ok "seat send is cd-bound to the target root (MAJOR-1)" || bad "seat send not root-bound: $(grep PWD= "$BRIDGE_LOG")"

echo "── T-SEAT-RELAY-FLAGS: ask relay callback carries the declared target ──"
new_root rf1
mkdir -p "$R/.dev/proposals/p-rf"
printf 'entries:\n  - timestamp: "2026-07-11T00:00:00Z"\n    stage: coding\n    to: codex-a\n    response: null\n' > "$R/.dev/proposals/p-rf/forge-log.yml"
BRIDGE_LOG="$WORK/blrf"; : > "$BRIDGE_LOG"
BRIDGE_LOG="$BRIDGE_LOG" FORGE_BRIDGE_BIN="$STUB" TMUX_SESSION=forge-x \
  "$FORGE" ask --slug p-rf --stage coding --worker codex-a "flags?" --root "$R" >/dev/null 2>&1
{ grep -q -- 'callback --target-session forge-x --cross-session' "$BRIDGE_LOG" \
  && grep -q -- '--status BLOCKED' "$BRIDGE_LOG" && grep -q -- '--quiet' "$BRIDGE_LOG"; } \
  && ok "relay callback declares --target-session/--cross-session (BLOCKED --quiet)" \
  || bad "relay callback argv wrong: $(cat "$BRIDGE_LOG")"
grep -v '^PWD=' "$BRIDGE_LOG" | grep -q 'TMUX_SESSION' && bad "relay still passes TMUX_SESSION" || ok "no TMUX_SESSION in the relay argv"

if command -v tmux >/dev/null 2>&1; then
  echo "── T-SEAT-RELAY-REPORTONLY: real-bridge out-of-tmux relay proceeds (report-only) ──"
  new_root rr1
  RELSESS="fccrel-$$"
  mkdir -p "$R/.claude" "$R/.dev/proposals/p-rr" "$R/.dev/forge-tmp/callbacks"
  printf 'name: rr1\n' > "$R/.claude/forge-project.yml"
  tmux new-session -d -s "$RELSESS" -x 200 -y 50 -c "$R"
  RELINC="$(tmux display-message -p -t "$RELSESS" '#{session_created}')"
  printf 'entries:\n  - timestamp: "2026-07-11T00:00:01Z"\n    stage: coding\n    to: codex-a\n    session: %s\n    incarnation: %s\n    response: null\n' "$RELSESS" "$RELINC" > "$R/.dev/proposals/p-rr/forge-log.yml"
  _i=0; while [ "$_i" -lt 4 ]; do tmux split-window -d -t "$RELSESS:0" -c "$R"; tmux select-layout -t "$RELSESS:0" tiled >/dev/null 2>&1; _i=$((_i+1)); done
  env -u TMUX -u TMUX_PANE FORGE_WATCH_TRIGGER=0 TMUX_SESSION="$RELSESS" \
    "$FORGE" ask --slug p-rr --stage coding --worker codex-a "report-only relay?" --root "$R" >/dev/null 2>&1
  RR_CB="$R/.dev/forge-tmp/callbacks/p-rr-coding.${RELSESS}.${RELINC}.callback"
  RR_OLD_CB="$R/.dev/forge-tmp/callbacks/p-rr-coding.${RELSESS}.callback"
  { [ -f "$RR_CB" ] \
    && [ ! -e "$RR_OLD_CB" ] \
    && grep -Fqx 'status: BLOCKED' "$RR_CB" \
    && grep -Fqx "session: ${RELSESS}" "$RR_CB" \
    && grep -Fqx "incarnation: ${RELINC}" "$RR_CB" \
    && grep -Fqx 'origin: ask' "$RR_CB" \
    && grep -Eq '^callback_id: .+$' "$RR_CB" \
    && grep -Fqx 'selected_pending_timestamp: "2026-07-11T00:00:01Z"' "$RR_CB" \
    && grep -Fqx '    response: null' "$R/.dev/proposals/p-rr/forge-log.yml"; } \
    && ok "out-of-tmux declared relay publishes incarnation-qualified ask callback (BLOCKED keeps pending open; legacy path absent)" \
    || bad "real-bridge relay refused or callback contract mismatch"
  tmux kill-session -t "$RELSESS" 2>/dev/null
else
  echo "  (skip T-SEAT-RELAY-REPORTONLY: no tmux)"
fi

echo "── T-LOCKSTEP: identity rule in lockstep across repo + installed prose copies ──"
for f in "$HOME/.claude/skills/forge-orchestrator/SKILL.md" \
         "$HOME/.claude/commands/forge.md" "$HOME/.claude/agents/forge-orchestrator.md" \
         "$ROOT/skills/forge-orchestrator/SKILL.md" \
         "$ROOT/commands/forge.md" "$ROOT/agents/forge-orchestrator.md"; do
  [ -f "$f" ] || continue
  grep -q 'export TMUX_SESSION' "$f" && bad "export TMUX_SESSION in $f" || ok "no export TMUX_SESSION: $(basename "$f")"
  grep -q 'forge-bridge identity' "$f" && ok "references forge-bridge identity: $(basename "$f")" || bad "missing forge-bridge identity: $f"
done

echo "── SwiftBar render: parked title/dropdown + enriched blocked (S1–S6) ──"
sb_render() {  # sb_render <board-json>  -> plugin stdout
    local d; d="$(mktemp -d)"; printf '#!/bin/sh\ncat <<'\''JSON'\''\n%s\nJSON\n' "$1" > "$d/forge"
    chmod +x "$d/forge"
    FORGE_BIN="$d/forge" bash "$ROOT/swiftbar/forge-board.5s.sh"
    rm -rf "$d"
}
# S1: parked-only board → title ⏸1, no ✓, no red !
J='{"schema":"cc-board/1","hot":[],"active":[],"parked":[{"slug":"p","stage":"coding","reason":"r","parked_at":"2026-07-01T00:00:00Z","uncommitted":false,"root":"/x","session":"forge-1"}],"episodes":[],"tasks":[]}'
T=$(sb_render "$J" | head -1)
echo "$T" | grep -q '⏸1' && ! echo "$T" | grep -q '✓' && ! echo "$T" | grep -q '!' \
  && ok "S1 parked-only title ⏸1, no ✓/!" || bad "S1 title wrong: $T"
# S2/S3: h_iso_age via a fixture with known parked_at ages (seconds/min/hour/day; malformed; future)
#   → assert the dropdown line's age token (Ns/Nm/Nh/Nd/?/0s)
# S4: parked dropdown line color=gray with slug/stage/reason
sb_render "$J" | grep -q '⏸ p/coding · parked .* · "r" | color=gray' && ok "S4 parked dropdown gray" || bad "S4 dropdown wrong"
# S5: enriched blocked hot row carries slug/stage/age/reason
JB='{"schema":"cc-board/1","hot":[{"condition":"ITEM-BLOCKED","slug":"p","stage":"coding","blocked_at":"2026-07-01T00:00:00Z","reason":"boom","acked":false,"session":"forge-1"}],"active":[],"parked":[],"episodes":[],"tasks":[]}'
sb_render "$JB" | grep -q '! ITEM-BLOCKED · p/coding .* · "boom" | color=red' && ok "S5 enriched blocked row" || bad "S5 blocked row wrong"
# S6: unseen>0 AND parked>0 → title has both 1! and ⏸1, no ✓
J6='{"schema":"cc-board/1","hot":[{"condition":"ITEM-BLOCKED","slug":"p","stage":"coding","acked":false}],"active":[],"parked":[{"slug":"q","stage":"qa","reason":"r","parked_at":"2026-07-01T00:00:00Z","root":"/x","session":"forge-1"}],"episodes":[],"tasks":[]}'
T6=$(sb_render "$J6" | head -1)
echo "$T6" | grep -q '1!' && echo "$T6" | grep -q '⏸1' && ! echo "$T6" | grep -q '✓' \
  && ok "S6 both 1! and ⏸1, no ✓" || bad "S6 title wrong: $T6"

echo "── forge parked: exit codes + filters + --resolve proxy (P-1) ──"
watch_stub() { local d; d="$(mktemp -d)"; printf '#!/bin/sh\n[ "$1" = status ] && cat <<'\''JSON'\''\n%s\nJSON\n' "$2" > "$d/forge-watch"; chmod +x "$d/forge-watch"; echo "$d"; }
J='{"parked":[{"slug":"p","stage":"coding","worker":"codex-a","session":"forge-1","uncommitted":true,"reason":"r","root":"'"$PWD"'"}]}'
D=$(watch_stub _ "$J")
out=$( FORGE_WATCH_BIN="$D/forge-watch" TMUX_SESSION=forge-1 "$ROOT/bin/forge" parked --root "$PWD" --session forge-1 2>&1 ); rc=$?
[ "$rc" -eq 10 ] && echo "$out" | grep -q 'worker=codex-a' && echo "$out" | grep -q 'session=forge-1' \
  && ok "P-1 exit 10 + worker/session fields" || bad "P-1 wrong: rc=$rc $out"
out=$( FORGE_WATCH_BIN="$D/forge-watch" "$ROOT/bin/forge" parked --root "$PWD" --session other 2>&1 ); rc=$?
[ "$rc" -eq 0 ] && ok "P-1 session filter → exit 0 (empty)" || bad "P-1 filter wrong: rc=$rc"
rm -rf "$D"
# P-1 resolve proxy: stub FORGE_BRIDGE_BIN, assert it is called with the note as one argv
D=$(mktemp -d); printf '#!/bin/sh\necho "$@" > "%s/called"\n' "$D" > "$D/forge-bridge"; chmod +x "$D/forge-bridge"
FORGE_BRIDGE_BIN="$D/forge-bridge" "$ROOT/bin/forge" parked --resolve p coding --note "a b c" >/dev/null 2>&1
grep -q 'park --resolve --slug p --stage coding --note a b c' "$D/called" && ok "P-1 --resolve proxies bridge (note one argv)" || bad "P-1 resolve proxy wrong: $(cat "$D/called")"
rm -rf "$D"

echo "── T-HYG-LOCKSTEP: bridge-owned hygiene prose in lockstep + emit whitelist ──"
BRIDGE_BIN="$ROOT/bin/forge-bridge"
for et in RESET HYGIENE_DECISION HYGIENE_ABANDON HYGIENE_BYPASSED RESET_UNAVAILABLE OBSERVE_ONLY; do
  grep -q "RESET|HYGIENE_DECISION|HYGIENE_ABANDON|HYGIENE_BYPASSED|RESET_UNAVAILABLE|OBSERVE_ONLY" "$BRIDGE_BIN" \
    && ok "emit whitelist declares $et" || bad "emit whitelist missing $et"
done
for f in "$ROOT/skills/forge-orchestrator/SKILL.md" "$ROOT/agents/forge-orchestrator.md" \
         "$HOME/.claude/skills/forge-orchestrator/SKILL.md" "$HOME/.claude/agents/forge-orchestrator.md"; do
  [ -f "$f" ] || continue
  for needle in "bridge owns context" "FORGE_WORKER_MIN_HEADROOM" "verify-decision" "finalize"; do
    grep -q "$needle" "$f" && ok "T-HYG-LOCKSTEP has '$needle': $(basename "$f")" \
      || bad "T-HYG-LOCKSTEP missing '$needle': $f"
  done
  grep -q "observed, never reset" "$f" && bad "T-HYG-LOCKSTEP obsolete 'observed, never reset' in $f" \
    || ok "T-HYG-LOCKSTEP obsolete reset prose absent: $(basename "$f")"
  grep -qE "≤ 20|<=20" "$f" && bad "T-HYG-LOCKSTEP obsolete route-away threshold in $f" \
    || ok "T-HYG-LOCKSTEP route-away threshold absent: $(basename "$f")"
done

echo "── T-HYG-INSTALL: clean-install seeds fail-closed reset-capability ──"
IH="$(mktemp -d)"
( cd "$ROOT" && HOME="$IH" bash install.sh ) >/dev/null 2>&1 || true
if [ -f "$IH/.config/forge/reset-capability.yml" ] \
   && grep -q 'proven: false' "$IH/.config/forge/reset-capability.yml" \
   && ! grep -q 'proven: true' "$IH/.config/forge/reset-capability.yml"; then
  ok "T-HYG-INSTALL clean install provisions the fail-closed capability seed"
else
  bad "T-HYG-INSTALL capability seed missing/not fail-closed in $IH/.config/forge/"
fi
rm -rf "$IH"

echo "═══ PASS: $PASS  FAIL: $FAIL ═══"; [ "$FAIL" -eq 0 ]
