#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; B="$ROOT/bin/forge-broker"; W="$(mktemp -d)"
trap 'rm -rf "$W"' EXIT
R="$W/repo"; git init -q "$R"; git -C "$R" config user.name test; git -C "$R" config user.email test@local
echo one > "$R/a.txt"; git -C "$R" add a.txt; git -C "$R" commit -qm base
G="$(git -C "$R" rev-parse --absolute-git-dir)"; RID="$(python3 - "$R" "$G" <<'PY'
import hashlib,os,sys;print(hashlib.sha256((os.path.realpath(sys.argv[1])+'\0'+os.path.realpath(sys.argv[2])).encode()).hexdigest())
PY
)"; H="$(git -C "$R" rev-parse HEAD)"; BR="$(git -C "$R" symbolic-ref HEAD)"; D=delivery-test
P="$G/forge/broker-v1/roots/$RID"; mkdir -p "$P/deliveries"
python3 - "$P/deliveries/$D.json" "$R" "$RID" "$H" "$BR" <<'PY'
import datetime,json,sys
json.dump({'schema':'forge-delivery/1','state':'open','delivery_id':'delivery-test','session':'s','session_incarnation':'1','pane_index':2,'physical_code_root':sys.argv[2],'root_identity':sys.argv[3],'stage':'coding','capability_class':'commit','prompt_sha256':'p','branch_ref':sys.argv[5],'expected_head':sys.argv[4],'expires_at':(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')},open(sys.argv[1],'w'))
PY
request(){ python3 - "$1" "$R" "$RID" "$H" "$2" <<'PY'
import json,sys
json.dump({'schema':'forge-broker-request/1','operation_id':sys.argv[5],'operation':'commit','delivery_id':'delivery-test','session_incarnation':'1','physical_code_root':sys.argv[2],'root_identity':sys.argv[3],'stage':'coding','capability_class':'commit','prompt_sha256':'p','expected_head':sys.argv[4],'message':'broker commit\n','paths':['a.txt']},open(sys.argv[1],'w'))
PY
}
echo two > "$R/a.txt"; request "$W/ok.json" op-success1
out="$(FORGE_BROKER_TEST_ENABLE=1 "$B" once --root "$R" --request "$W/ok.json")"
printf '%s\n' "$out" | grep -q '"status": "ok"' || { printf '%s\n' "$out" >&2; exit 1; }
test "$(git -C "$R" diff --cached --name-only)" = ""
test "$(git -C "$R" show --format= --name-only HEAD)" = a.txt
# Replay returns the same commit and creates no second effect.
C1="$(git -C "$R" rev-list --count HEAD)"; FORGE_BROKER_TEST_ENABLE=1 "$B" once --root "$R" --request "$W/ok.json" >/dev/null; test "$C1" = "$(git -C "$R" rev-list --count HEAD)"
# A delivery is bound to its exact prompt digest, and a stale HEAD cannot commit.
sed -e 's/"prompt_sha256": "p"/"prompt_sha256": "different"/' -e 's/op-success1/op-mismatch1/' "$W/ok.json" > "$W/mismatch.json"
FORGE_BROKER_TEST_ENABLE=1 "$B" once --root "$R" --request "$W/mismatch.json" | grep -q DELIVERY_MISMATCH
request "$W/head-moved.json" op-headmv01
FORGE_BROKER_TEST_ENABLE=1 "$B" once --root "$R" --request "$W/head-moved.json" | grep -q HEAD_MOVED
# Exact path parser rejects traversal, absolute paths, metadata, magic pathspecs,
# symlinks, and duplicate case/Unicode aliases before invoking Git.
i=0
for bad in ../x /tmp/x .git/config :\(glob\)a; do
  i=$((i+1))
  sed -e "s#\"a.txt\"#\"$bad\"#" -e "s/op-success1/op-bad000$i/" "$W/ok.json" > "$W/bad.json"
  bad_out="$(FORGE_BROKER_TEST_ENABLE=1 "$B" once --root "$R" --request "$W/bad.json" 2>&1 || true)"
  printf '%s\n' "$bad_out" | grep -Eq 'PATH_|INVALID_PATH' || { printf '%s\n' "$bad_out" >&2; exit 1; }
done
touch "$W/outside"; ln -s "$W/outside" "$R/link"
sed -e 's/"a.txt"/"link"/' -e 's/op-success1/op-symlink1/' "$W/ok.json" > "$W/symlink.json"
FORGE_BROKER_TEST_ENABLE=1 "$B" once --root "$R" --request "$W/symlink.json" | grep -q SYMLINK_ESCAPE
python3 - "$W/ok.json" "$W/alias.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['operation_id']='op-alias001'; d['paths']=['a.txt','A.TXT']
json.dump(d,open(sys.argv[2],'w'))
PY
FORGE_BROKER_TEST_ENABLE=1 "$B" once --root "$R" --request "$W/alias.json" | grep -q PATH_ALIAS
python3 - "$W/ok.json" "$W/unicode-alias.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['operation_id']='op-uniAlias1'; d['paths']=['é.txt','e\u0301.txt']
json.dump(d,open(sys.argv[2],'w'))
PY
FORGE_BROKER_TEST_ENABLE=1 "$B" once --root "$R" --request "$W/unicode-alias.json" | grep -q PATH_ALIAS
# Clean-index gate and executable config gate.
echo staged > "$R/staged"; git -C "$R" add staged; request "$W/staged.json" op-staged1; FORGE_BROKER_TEST_ENABLE=1 "$B" once --root "$R" --request "$W/staged.json" | grep -q INDEX_NOT_CLEAN; git -C "$R" reset -q
git -C "$R" config filter.evil.clean '/usr/bin/touch should-not-run'; request "$W/filter.json" op-filter1; FORGE_BROKER_TEST_ENABLE=1 "$B" once --root "$R" --request "$W/filter.json" | grep -q UNSAFE_GIT_CONFIG; test ! -e "$R/should-not-run"
# Bounded PR publication uses only protected repo/number/binary metadata and is replay-safe.
mkdir -p "$R/.dev/reviews"; printf 'review body\n' > "$R/.dev/reviews/pr-7.md"
python3 - "$P/deliveries/delivery-publish.json" "$R" "$RID" "$H" "$BR" "$ROOT/tests/forge-broker/fake-gh" "$W/publish.json" <<'PY'
import datetime,hashlib,json,sys
delivery={'schema':'forge-delivery/1','state':'open','delivery_id':'delivery-publish','session':'s','session_incarnation':'1','pane_index':2,'physical_code_root':sys.argv[2],'root_identity':sys.argv[3],'stage':'pr-review','capability_class':'publish-pr-review','prompt_sha256':'pub','branch_ref':sys.argv[5],'expected_head':sys.argv[4],'github_repo':'owner/repo','pr_number':7,'gh_binary':sys.argv[6],'expires_at':(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}
json.dump(delivery,open(sys.argv[1],'w'))
body=open(sys.argv[2]+'/.dev/reviews/pr-7.md','rb').read()
request={'schema':'forge-broker-request/1','operation_id':'op-publish1','operation':'publish-pr-review','delivery_id':'delivery-publish','session_incarnation':'1','physical_code_root':sys.argv[2],'root_identity':sys.argv[3],'stage':'pr-review','capability_class':'publish-pr-review','prompt_sha256':'pub','artifact':'.dev/reviews/pr-7.md','artifact_sha256':hashlib.sha256(body).hexdigest()}
json.dump(request,open(sys.argv[7],'w'))
PY
publish_out="$(FORGE_BROKER_TEST_ENABLE=1 "$B" once --root "$R" --request "$W/publish.json")"
printf '%s\n' "$publish_out" | grep -q 'https://github.test/comment/123'
FORGE_BROKER_TEST_ENABLE=1 "$B" once --root "$R" --request "$W/publish.json" | grep -q '"status": "ok"'
# The daemon survives malformed untrusted input, refuses identity-changing reuse,
# reports health, and performs an authenticated drain/stop.
# An incomplete but parseable pid record is a recoverable cold-start condition;
# it must not surface as KeyError: 'pid' or force callers into --dry-run.
printf '{"schema":"forge-broker-pid/1"}\n' > "$P/pid.json"
status_rc=0; status_out="$(FORGE_BROKER_TEST_ENABLE=1 "$B" status --root "$R" --json 2>&1)" || status_rc=$?
[ "$status_rc" -eq 3 ] && printf '%s\n' "$status_out" | grep -q '"live": false'
FORGE_BROKER_TEST_ENABLE=1 "$B" start --root "$R" --session forge-test --incarnation 100 --mode contain >/dev/null
for _ in $(seq 1 100); do FORGE_BROKER_TEST_ENABLE=1 "$B" status --root "$R" --json > "$W/status.json" 2>/dev/null && break; sleep 0.05; done
grep -q '"live": true' "$W/status.json" || { sed -n '1,160p' "$P/broker.log" >&2; exit 1; }
printf '{not-json\n' > "$R/.dev/forge-broker/requests/malformed.request.json"
for _ in $(seq 1 100); do find "$R/.dev/forge-broker/requests" -name '*.error.json' -print -quit | grep -q . && break; sleep 0.05; done
find "$R/.dev/forge-broker/requests" -name '*.error.json' -print -quit | grep -q .
FORGE_BROKER_TEST_ENABLE=1 "$B" status --root "$R" --json | grep -q '"live": true'
# A syntactically valid .dev request is still denied as an authority source.
request "$R/.dev/forge-broker/requests/untrusted.request.json" op-untrust1
for _ in $(seq 1 100); do [ -f "$R/.dev/forge-broker/responses/op-untrust1.json" ] && break; sleep 0.05; done
grep -q HOST_CONTROL_REQUIRED "$R/.dev/forge-broker/responses/op-untrust1.json"
# Authenticated host submission can perform the exact-path operation.
git -C "$R" config --unset filter.evil.clean
H="$(git -C "$R" rev-parse HEAD)"
python3 - "$P/deliveries/$D.json" "$H" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['expected_head']=sys.argv[2]
json.dump(d,open(sys.argv[1],'w'))
PY
echo three > "$R/a.txt"; request "$W/host.json" op-hostctrl1
host_intent="$(python3 - "$W/host.json" <<'PY'
import json,sys
print(json.dumps({'action':'submit-operation','request':json.load(open(sys.argv[1]))}))
PY
)"
printf '%s\n' "$host_intent" | FORGE_BROKER_TEST_ENABLE=1 "$B" control --root "$R" | grep -q '"status": "ok"'
test "$(git -C "$R" show --format= --name-only HEAD)" = a.txt
reuse_out="$(FORGE_BROKER_TEST_ENABLE=1 "$B" start --root "$R" --session forge-test --incarnation 100 --mode enforce 2>&1 || true)"
printf '%s\n' "$reuse_out" | grep -q BROKER_IDENTITY_MISMATCH
FORGE_BROKER_TEST_ENABLE=1 "$B" stop --root "$R" | grep -q '"state": "draining"'
FORGE_BROKER_TEST_ENABLE=1 "$B" status --root "$R" --json > "$W/stopped.json" 2>/dev/null || true
grep -q '"state": "stopped"' "$W/stopped.json"
# Stale-incarnation reap: a daemon whose identity matches on everything except a
# verifiably dead session_incarnation is stopped and replaced in one `start`;
# any other mismatch, or a still-live recorded incarnation, still refuses.
FORGE_BROKER_TEST_ENABLE=1 "$B" start --root "$R" --session reap-test --incarnation 100 --mode contain >/dev/null
pid1="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["pid"])' "$P/pid.json")"
# R1: fixture unset => tmux reports nothing live => reap old daemon, spawn fresh.
FORGE_BROKER_TEST_ENABLE=1 "$B" start --root "$R" --session reap-test --incarnation 200 --mode contain >/dev/null
FORGE_BROKER_TEST_ENABLE=1 "$B" status --root "$R" --json > "$W/reap1.json"
grep -q '"session_incarnation": "200"' "$W/reap1.json"
grep -q '"live": true' "$W/reap1.json"
pid2="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["pid"])' "$P/pid.json")"
test "$pid1" != "$pid2"
for _ in $(seq 1 100); do kill -0 "$pid1" 2>/dev/null || break; sleep 0.05; done
! kill -0 "$pid1" 2>/dev/null
# R2: tmux already shows the REPLACEMENT incarnation under the same name (the
# production shape: forge-start creates the new session before invoking start).
FORGE_BROKER_TEST_TMUX_SESSIONS="reap-test=300" FORGE_BROKER_TEST_ENABLE=1 "$B" start --root "$R" --session reap-test --incarnation 300 --mode contain >/dev/null
FORGE_BROKER_TEST_ENABLE=1 "$B" status --root "$R" --json > "$W/reap2.json"
grep -q '"session_incarnation": "300"' "$W/reap2.json"
grep -q '"live": true' "$W/reap2.json"
pid3="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["pid"])' "$P/pid.json")"
test "$pid2" != "$pid3"
# R3: the RECORDED incarnation is still live in tmux => refuse, daemon untouched.
r3_out="$(FORGE_BROKER_TEST_TMUX_SESSIONS="reap-test=300" FORGE_BROKER_TEST_ENABLE=1 "$B" start --root "$R" --session reap-test --incarnation 400 --mode contain 2>&1 || true)"
printf '%s\n' "$r3_out" | grep -q BROKER_IDENTITY_MISMATCH
printf '%s\n' "$r3_out" | grep -q '"detail": "session_incarnation"'
# R4: a non-incarnation mismatch refuses even when the fixture says dead.
r4_out="$(FORGE_BROKER_TEST_ENABLE=1 "$B" start --root "$R" --session reap-test --incarnation 300 --mode enforce 2>&1 || true)"
printf '%s\n' "$r4_out" | grep -q '"detail": "mode"'
# R5: a multi-key mismatch including the incarnation refuses (singleton guard).
r5_out="$(FORGE_BROKER_TEST_ENABLE=1 "$B" start --root "$R" --session reap-test --incarnation 500 --mode enforce 2>&1 || true)"
printf '%s\n' "$r5_out" | grep -q '"detail": "session_incarnation,mode"'
# R6: a reap that cannot stop a still-live daemon re-raises instead of falling
# through to a doomed spawn (tampered token => unauthenticated control server).
tok="$(cat "$P/control.token")"
printf '%064d\n' 1 > "$P/control.token"
r6_out="$(FORGE_BROKER_TEST_ENABLE=1 "$B" start --root "$R" --session reap-test --incarnation 600 --mode contain 2>&1 || true)"
printf '%s\n' "$r6_out" | grep -q CONTROL_SERVER_UNAUTHENTICATED
printf '%s\n' "$tok" > "$P/control.token"
test "$pid3" = "$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["pid"])' "$P/pid.json")"
FORGE_BROKER_TEST_ENABLE=1 "$B" status --root "$R" --json | grep -q '"live": true'
FORGE_BROKER_TEST_ENABLE=1 "$B" stop --root "$R" >/dev/null
# Linked worktrees share a common Git directory but retain distinct root
# identities, protected delivery namespaces, indexes, and transaction journals.
WR="$W/linked"; git -C "$R" worktree add -q -b linked-branch "$WR"
WG="$(git -C "$WR" rev-parse --absolute-git-dir)"; WC="$(git -C "$WR" rev-parse --path-format=absolute --git-common-dir)"
WRID="$(python3 - "$WR" "$WG" <<'PY'
import hashlib,os,sys
print(hashlib.sha256((os.path.realpath(sys.argv[1])+'\0'+os.path.realpath(sys.argv[2])).encode()).hexdigest())
PY
)"; test "$WRID" != "$RID"
WH="$(git -C "$WR" rev-parse HEAD)"; WBR="$(git -C "$WR" symbolic-ref HEAD)"; WP="$WC/forge/broker-v1/roots/$WRID"
mkdir -p "$WP/deliveries"; echo linked > "$WR/linked.txt"
python3 - "$WP/deliveries/delivery-linked.json" "$WR" "$WRID" "$WH" "$WBR" "$W/linked.json" <<'PY'
import datetime,json,sys
d={'schema':'forge-delivery/1','state':'open','delivery_id':'delivery-linked','session':'linked','session_incarnation':'2','pane_index':2,'physical_code_root':sys.argv[2],'root_identity':sys.argv[3],'stage':'coding','capability_class':'commit','prompt_sha256':'linked','branch_ref':sys.argv[5],'expected_head':sys.argv[4],'expires_at':(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}
json.dump(d,open(sys.argv[1],'w'))
r={'schema':'forge-broker-request/1','operation_id':'op-linked01','operation':'commit','delivery_id':'delivery-linked','session_incarnation':'2','physical_code_root':sys.argv[2],'root_identity':sys.argv[3],'stage':'coding','capability_class':'commit','prompt_sha256':'linked','expected_head':sys.argv[4],'message':'linked commit\n','paths':['linked.txt']}
json.dump(r,open(sys.argv[6],'w'))
PY
FORGE_BROKER_TEST_ENABLE=1 "$B" once --root "$WR" --request "$W/linked.json" | grep -q '"status": "ok"'
test -f "$WG/forge/broker-v1/transactions/op-linked01/journal.json"
test ! -e "$G/forge/broker-v1/transactions/op-linked01/journal.json"
WH="$(git -C "$WR" rev-parse HEAD)"; echo post-cas > "$WR/post-cas.txt"
python3 - "$WP/deliveries/delivery-linked.json" "$W/linked.json" "$W/post-cas.json" "$WH" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['expected_head']=sys.argv[4]; json.dump(d,open(sys.argv[1],'w'))
r=json.load(open(sys.argv[2])); r.update(operation_id='op-postcas1',expected_head=sys.argv[4],message='post CAS\n',paths=['post-cas.txt']); json.dump(r,open(sys.argv[3],'w'))
PY
post_out="$(FORGE_BROKER_FAIL_AFTER_CAS=1 FORGE_BROKER_TEST_ENABLE=1 "$B" once --root "$WR" --request "$W/post-cas.json")"
printf '%s\n' "$post_out" | grep -q INJECTED_POST_CAS_FAILURE || { printf '%s\n' "$post_out" >&2; exit 1; }
git -C "$WR" diff --cached --quiet; git -C "$WR" diff --quiet
grep -q '"state":"committed-recovery"' "$WG/forge/broker-v1/transactions/op-postcas1/journal.json"
WH="$(git -C "$WR" rev-parse HEAD)"; echo crash > "$WR/crash.txt"
python3 - "$WP/deliveries/delivery-linked.json" "$W/linked.json" "$W/crash.json" "$WH" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); d['expected_head']=sys.argv[4]; json.dump(d,open(sys.argv[1],'w'))
r=json.load(open(sys.argv[2])); r.update(operation_id='op-crash001',expected_head=sys.argv[4],message='crash recovery\n',paths=['crash.txt']); json.dump(r,open(sys.argv[3],'w'))
PY
crash_rc=0; FORGE_BROKER_CRASH_AT=before-cas FORGE_BROKER_TEST_ENABLE=1 "$B" once --root "$WR" --request "$W/crash.json" >/dev/null 2>&1 || crash_rc=$?
test "$crash_rc" -eq 86; test "$(git -C "$WR" rev-parse HEAD)" = "$WH"
FORGE_BROKER_TEST_ENABLE=1 "$B" start --root "$WR" --session forge-linked --incarnation 200 --mode contain >/dev/null
git -C "$WR" diff --cached --quiet
grep -q '"state":"restored"' "$WG/forge/broker-v1/transactions/op-crash001/journal.json"
FORGE_BROKER_TEST_ENABLE=1 "$B" stop --root "$WR" >/dev/null

# Lifecycle queue tests are load-bearing: this is structural non-effect authority,
# not pane authentication. The worker-writable callback can only support accidental-
# divergence checks; dangerous actions must remain absent from the handler itself.
LQ="$R/.dev/forge-broker/lifecycle"; mkdir -p "$LQ/requests" "$LQ/responses"
FORGE_BROKER_TEST_ENABLE=1 "$B" start --root "$R" --session life --incarnation 1 --mode contain >/dev/null
FORGE_BROKER_TEST_ENABLE=1 "$B" status --root "$R" --json | grep -q '"lifecycle_queue": 1'
lq_head_before="$(git -C "$R" rev-parse HEAD)"
for action in submit-operation register-delivery attest-launch stop-broker query-result; do
  id="lci-$(printf '%032d' ${#action})"
  printf '{"schema":"forge-lifecycle-intent/1","intent_id":"%s","action":"%s","emitted_at":"2026-08-07T00:00:00Z"}\n' "$id" "$action" > "$LQ/requests/$id.intent.json"
  for _ in $(seq 1 100); do [ -f "$LQ/responses/$id.json" ] && break; sleep 0.05; done
  grep -q LIFECYCLE_ACTION_DENIED "$LQ/responses/$id.json"
done
test "$lq_head_before" = "$(git -C "$R" rev-parse HEAD)"
rm -f "$LQ/requests"/* "$LQ/responses"/*
fault_rc=0
printf '{"action":"active-delivery","session":"none","pane":"3"}\n' | FORGE_LIFECYCLE_TIMEOUT_S=0.1 FORGE_BROKER_TEST_CONNECT_FAULT=EPERM FORGE_BROKER_TEST_ENABLE=1 "$B" control --root "$R" >/dev/null 2>&1 || fault_rc=$?
[ "$fault_rc" -eq 3 ] || [ "$fault_rc" -eq 5 ]
[ -n "$(find "$LQ/requests" -name '*.intent.json' -print -quit)$(find "$LQ/responses" -name '*.json' -print -quit)" ]
rm -f "$LQ/requests"/* "$LQ/responses"/*
fault_rc=0
printf '{"action":"active-delivery","session":"none","pane":"3"}\n' | FORGE_BROKER_TEST_CONNECT_FAULT=ECONNREFUSED FORGE_BROKER_TEST_ENABLE=1 "$B" control --root "$R" >/dev/null 2>&1 || fault_rc=$?
[ "$fault_rc" -eq 4 ] && [ -z "$(find "$LQ/requests" -name '*.intent.json' -print -quit)" ]
# Post-connect ambiguity is CONTROL_UNREACHABLE and never replays into the queue.
rm -f "$LQ/requests"/* "$LQ/responses"/*
fault_rc=0
printf '{"action":"active-delivery","session":"none","pane":"3"}\n' | FORGE_BROKER_TEST_POST_CONNECT_FAULT=1 FORGE_BROKER_TEST_ENABLE=1 "$B" control --root "$R" >/dev/null 2>&1 || fault_rc=$?
[ "$fault_rc" -eq 4 ] && [ -z "$(find "$LQ/requests" -name '*.intent.json' -print -quit)" ]
# Capability skew does not queue against an old daemon.
cp "$P/pid.json" "$W/pid-capability.json"
python3 - "$P/pid.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d.pop('lifecycle_queue',None); json.dump(d,open(p,'w'))
PY
fault_rc=0
printf '{"action":"active-delivery","session":"none","pane":"3"}\n' | FORGE_BROKER_TEST_CONNECT_FAULT=EPERM FORGE_BROKER_TEST_ENABLE=1 "$B" control --root "$R" >/dev/null 2>&1 || fault_rc=$?
[ "$fault_rc" -eq 4 ] && [ -z "$(find "$LQ/requests" -name '*.intent.json' -print -quit)" ]
mv "$W/pid-capability.json" "$P/pid.json"
# Strict happy path, mismatch, expired host repair, and evidence-free operator reap.
mkdir -p "$P/active" "$R/.dev/proposals/lifecycle" "$R/.dev/forge-tmp/callbacks"
mk_delivery(){
  python3 - "$P" "$R" "$RID" "$1" "$2" "$3" <<'PY'
import datetime,json,os,sys
p,root,rid,did,expiry,pane=sys.argv[1:]; root=os.path.realpath(root)
d={'schema':'forge-delivery/1','state':'open','delivery_id':did,'slug':'lifecycle','stage':'adhoc',
   'worker':'codex-a','session':'life','session_incarnation':'1','pane_index':int(pane),
   'physical_code_root':root,'root_identity':rid,'capability_class':'workspace',
   'opened_at':'2026-08-07T00:00:00Z','expires_at':expiry}
json.dump(d,open(p+'/deliveries/'+did+'.json','w'))
json.dump(d,open(p+'/active/life.p'+pane+'.json','w'))
PY
}
future="$(python3 -c 'import datetime;print((datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))')"
past="2020-01-01T00:00:00Z"
printf 'entries:\n  - timestamp: "2026-08-07T00:00:00Z"\n    stage: adhoc\n    response: "FORGE_DONE: adhoc"\n' > "$R/.dev/proposals/lifecycle/forge-log.yml"
D2=delivery-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; mk_delivery "$D2" "$future" 3
CB="$(cd "$R/.dev/forge-tmp/callbacks" && pwd -P)/lifecycle-adhoc.life.1.callback"
printf 'slug: lifecycle\nstage: adhoc\nstatus: DONE\nworker: codex-a\nsession: life\nincarnation: 1\ndelivery_id: %s\nselected_pending_timestamp: "2026-08-07T00:00:00Z"\n' "$D2" > "$CB"
SHA="$(shasum -a 256 "$CB" | awk '{print $1}')"
python3 - "$D2" "$CB" "$SHA" <<'PY' | FORGE_BROKER_TEST_ENABLE=1 "$B" control --root "$R" | grep -q '"state": "completed"'
import json,sys
print(json.dumps({'action':'reconcile-delivery','delivery_id':sys.argv[1],'terminal':'completed',
                  'callback_path':sys.argv[2],'callback_sha256':sys.argv[3]}))
PY
D3=delivery-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb; mk_delivery "$D3" "$past" 4
sed "s/$D2/$D3/" "$CB" > "$CB.tmp"; mv "$CB.tmp" "$CB"; SHA="$(shasum -a 256 "$CB" | awk '{print $1}')"
python3 - "$D3" "$CB" "$SHA" <<'PY' | FORGE_BROKER_TEST_ENABLE=1 "$B" control --root "$R" --json | grep -q '"state": "completed"'
import json,sys
print(json.dumps({'action':'reconcile-delivery','delivery_id':sys.argv[1],'terminal':'completed',
                  'callback_path':sys.argv[2],'callback_sha256':sys.argv[3],'allow_expired':True}))
PY
D4=delivery-cccccccccccccccccccccccccccccccc; mk_delivery "$D4" "$past" 5
printf '{"action":"reap-delivery","session":"life","pane":"5","operator_command":"forge codex-broker reap"}\n' | FORGE_BROKER_TEST_ENABLE=1 "$B" control --root "$R" | grep -q '"state": "cancelled"'
grep -R -q DELIVERY_REAPED "$P/audit"

FORGE_BROKER_TEST_ENABLE=1 "$B" stop --root "$R" >/dev/null

# ─────────────────────────────────────────────────────────────────────────
# Phase C — gap register G-1/G-3/G-5/G-6 and the §11 defect regressions.
# Acceptance bar: every case below must FAIL against the unmodified source.
#
# Refusal assertions capture output AND rc before grepping: `control` exits
# non-zero on refusal, so piping it straight into grep dies under pipefail.
# ─────────────────────────────────────────────────────────────────────────
LQ2="$R/.dev/forge-broker/lifecycle"
say(){ echo "  ok: $1"; }
ctl(){ # ctl <want-rc> <want-substring> <label>  — request JSON on stdin
  local want_rc="$1" want="$2" label="$3" out rc=0
  out="$(FORGE_BROKER_TEST_ENABLE=1 "$B" control --root "$R" 2>&1)" || rc=$?
  [ "$rc" = "$want_rc" ] || { echo "FAIL $label: rc=$rc want $want_rc :: $out" >&2; exit 1; }
  grep -q -- "$want" <<<"$out" || { echo "FAIL $label: missing '$want' :: $out" >&2; exit 1; }
  say "$label"
}
qwait(){ for _ in $(seq 1 100); do [ -f "$1" ] && return 0; sleep 0.05; done; echo "FAIL: no response $1" >&2; exit 1; }

FORGE_BROKER_TEST_ENABLE=1 "$B" start --root "$R" --session life --incarnation 1 --mode contain >/dev/null
mk_delivery delivery-dddddddddddddddddddddddddddddddd "$future" 6

# ── G-1: the queue is a real transport, not just a refusal surface ──
# Discriminating positive: make PID/start-stamp validation unusable while the
# daemon stays alive and holds broker.lock. The old broker_pid_live() predicate
# exits 4 here; the lock-based predicate queues and receives the real payload.
cp "$P/pid.json" "$W/pid-lock-liveness.json"
python3 - "$P/pid.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d['pid_started']='unusable-client-start-stamp'; json.dump(d,open(p,'w'))
PY
audit_before="$(grep -Rl '"event":"LIFECYCLE_FALLBACK"' "$P/audit" 2>/dev/null | wc -l | tr -d ' ')"
responses_before="$(find "$LQ2/responses" -name '*.json' | wc -l | tr -d ' ')"
qout="$(printf '{"action":"active-delivery","session":"life","pane":"6"}\n' \
  | FORGE_BROKER_TEST_CONNECT_FAULT=EPERM FORGE_BROKER_TEST_ENABLE=1 "$B" control --root "$R")"
mv "$W/pid-lock-liveness.json" "$P/pid.json"
grep -q 'delivery-dddddddddddddddddddddddddddddddd' <<<"$qout"
audit_after="$(grep -Rl '"event":"LIFECYCLE_FALLBACK"' "$P/audit" 2>/dev/null | wc -l | tr -d ' ')"
responses_after="$(find "$LQ2/responses" -name '*.json' | wc -l | tr -d ' ')"
[ "$audit_after" -gt "$audit_before" ]
[ "$responses_after" -gt "$responses_before" ]
say "G-1 held lock routes despite unusable PID stamp and processes the real payload"
# Parity: the same request over TCP returns the same delivery id.
tout="$(printf '{"action":"active-delivery","session":"life","pane":"6"}\n' \
  | FORGE_BROKER_TEST_ENABLE=1 "$B" control --root "$R")"
grep -q 'delivery-dddddddddddddddddddddddddddddddd' <<<"$tout"
say "G-1 queue and TCP paths agree on the payload"

# A symlink at the protected lock path fails closed even while the daemon keeps
# the underlying inode locked. O_NOFOLLOW must prevent any queue intent.
rm -f "$LQ2/requests"/* "$LQ2/responses"/* 2>/dev/null || true
mv "$P/broker.lock" "$W/broker.lock.held"
ln -s "$W/broker.lock.held" "$P/broker.lock"
lock_rc=0
lock_out="$(printf '{"action":"active-delivery","session":"life","pane":"6"}\n' \
  | FORGE_BROKER_TEST_CONNECT_FAULT=EPERM FORGE_BROKER_TEST_ENABLE=1 \
    "$B" control --root "$R" 2>&1)" || lock_rc=$?
rm -f "$P/broker.lock"; mv "$W/broker.lock.held" "$P/broker.lock"
[ "$lock_rc" -eq 4 ] && grep -q CONTROL_UNREACHABLE <<<"$lock_out"
[ -z "$(find "$LQ2/requests" -name '*.intent.json' -print -quit)" ]
say "G-1 a symlinked live broker.lock fails closed without queueing"

# ── G-3: the lifecycle transport refuses what only the host may ask ──
printf '{"action":"reconcile-delivery","delivery_id":"delivery-dddddddddddddddddddddddddddddddd","terminal":"completed","callback_path":"/x","callback_sha256":"%064d","allow_expired":true}\n' 0 \
  | FORGE_BROKER_TEST_CONNECT_FAULT=EPERM FORGE_LIFECYCLE_TIMEOUT_S=6 \
    ctl 3 LIFECYCLE_FIELDS_DENIED "G-3 allow_expired refused on the lifecycle transport"
printf '{"action":"reconcile-delivery","delivery_id":"delivery-dddddddddddddddddddddddddddddddd","terminal":"completed","callback_path":"/x","callback_sha256":"%064d","surprise":1}\n' 0 \
  | FORGE_BROKER_TEST_CONNECT_FAULT=EPERM FORGE_LIFECYCLE_TIMEOUT_S=6 \
    ctl 3 LIFECYCLE_FIELDS_DENIED "G-3 unknown key refused on the lifecycle transport"
printf '{"action":"reconcile-delivery","delivery_id":"delivery-dddddddddddddddddddddddddddddddd","terminal":"cancelled","callback_path":"/x","callback_sha256":"%064d"}\n' 0 \
  | FORGE_BROKER_TEST_CONNECT_FAULT=EPERM FORGE_LIFECYCLE_TIMEOUT_S=6 \
    ctl 3 TERMINAL_STATE_INVALID "G-3 terminal:cancelled refused on the lifecycle transport"
# A non-lifecycle action under EPERM must not create an intent at all.
rm -f "$LQ2/requests"/* "$LQ2/responses"/* 2>/dev/null || true
printf '{"action":"submit-operation","operation_id":"op-nope"}\n' \
  | FORGE_BROKER_TEST_CONNECT_FAULT=EPERM ctl 4 CONTROL_UNREACHABLE "G-3 non-lifecycle EPERM does not queue"
[ -z "$(find "$LQ2/requests" -name '*.intent.json' -print -quit)" ]
say "G-3 no intent file was created for the denied non-lifecycle action"

# ── N-7: reap requires expiry, or an explicit force ──
mk_delivery delivery-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee "$future" 7
printf '{"action":"reap-delivery","session":"life","pane":"7","operator_command":"forge codex-broker reap"}\n' \
  | ctl 3 DELIVERY_NOT_EXPIRED "N-7 reap refuses a healthy unexpired delivery"
printf '{"action":"reap-delivery","session":"life","pane":"7","operator_command":"forge codex-broker reap --force","force":true}\n' \
  | ctl 0 '"state": "cancelled"' "N-7 reap --force cancels and is audited as forced"
# Audits are written with compact separators, so match without a space.
grep -R -q '"forced":true' "$P/audit"
grep -R -q '"expired":false' "$P/audit"
say "N-7 the audit records forced=true on an unexpired delivery"

# ── G-6 / G-6a: strict and permissive terminalizers disagree, by design ──
D6=delivery-ffffffffffffffffffffffffffffffff; mk_delivery "$D6" "$future" 8
python3 - "$P/deliveries/$D6.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d['capability_class']='commit'; json.dump(d,open(p,'w'))
PY
CB6="$(cd "$R/.dev/forge-tmp/callbacks" && pwd -P)/lifecycle-adhoc.life.1.callback"
printf 'slug: lifecycle\nstage: adhoc\nstatus: DONE\nworker: codex-a\nsession: life\nincarnation: 1\ndelivery_id: %s\nselected_pending_timestamp: "2026-08-07T00:00:00Z"\n' "$D6" > "$CB6"
SHA6="$(shasum -a 256 "$CB6" | awk '{print $1}')"
mkdir -p "$P/results"
python3 - "$D6" "$CB6" "$SHA6" <<'PY' | ctl 3 LIFECYCLE_DELIVERY_RESULT_NOT_OK "G-6 strict refuses a commit DONE with zero protected results"
import json,sys
print(json.dumps({'action':'reconcile-delivery','delivery_id':sys.argv[1],'terminal':'completed',
                  'callback_path':sys.argv[2],'callback_sha256':sys.argv[3]}))
PY
# G-6a: a result that exists but is not ok surfaces the SAME named code.
python3 - "$P/results/op-bad.json" "$D6" <<'PY'
import json,sys
json.dump({'delivery_id':sys.argv[2],'operation_id':'op-bad','status':'error'},open(sys.argv[1],'w'))
PY
python3 - "$D6" "$CB6" "$SHA6" <<'PY' | ctl 3 LIFECYCLE_DELIVERY_RESULT_NOT_OK "G-6a a present-but-error result surfaces the same code"
import json,sys
print(json.dumps({'action':'reconcile-delivery','delivery_id':sys.argv[1],'terminal':'completed',
                  'callback_path':sys.argv[2],'callback_sha256':sys.argv[3]}))
PY
# The permissive legacy terminalizer still accepts exactly what strict refused.
printf '{"action":"terminalize-delivery","delivery_id":"%s","terminal":"completed"}\n' "$D6" \
  | ctl 0 '"state": "completed"' "G-6 legacy terminalize-delivery still accepts it (asymmetry proven)"
rm -f "$P/results/op-bad.json"

# ── N-8: a truncated final reply is a stable refusal, not a traceback ──
printf '{"action":"active-delivery","session":"life","pane":"6"}\n' \
  | FORGE_BROKER_TEST_REPLY_TRUNCATE=1 ctl 4 CONTROL_REPLY_INVALID "N-8 truncated post-connect reply refuses with exit 4"
[ -z "$(find "$LQ2/requests" -name '*.intent.json' -print -quit)" ]
say "N-8 the truncated reply did not replay into the queue"

# ── N-2: replay repairs a half-finished transition ──
D2R=delivery-99999999999999999999999999999999; mk_delivery "$D2R" "$future" 9
CB2="$(cd "$R/.dev/forge-tmp/callbacks" && pwd -P)/lifecycle-adhoc.life.1.callback"
printf 'slug: lifecycle\nstage: adhoc\nstatus: DONE\nworker: codex-a\nsession: life\nincarnation: 1\ndelivery_id: %s\nselected_pending_timestamp: "2026-08-07T00:00:00Z"\n' "$D2R" > "$CB2"
SHA2="$(shasum -a 256 "$CB2" | awk '{print $1}')"
# Simulate the crash window: terminal record written, active/ never removed.
python3 - "$P/deliveries/$D2R.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d['state']='completed'; json.dump(d,open(p,'w'))
PY
test -f "$P/active/life.p9.json"
python3 - "$D2R" "$CB2" "$SHA2" <<'PY' | ctl 0 '"replayed": true' "N-2 replay of a partial transition reports replayed"
import json,sys
print(json.dumps({'action':'reconcile-delivery','delivery_id':sys.argv[1],'terminal':'completed',
                  'callback_path':sys.argv[2],'callback_sha256':sys.argv[3]}))
PY
[ ! -e "$P/active/life.p9.json" ]
say "N-2 replay REPAIRED the orphaned active record (was reported ok and left open)"

# ── N-4: valid non-object JSON gets a response instead of stranding ──
rm -f "$LQ2/requests"/* "$LQ2/responses"/* 2>/dev/null || true
printf '[1,2,3]\n' > "$LQ2/requests/lci-$(printf '%032d' 4).intent.json"
qwait "$LQ2/responses/lci-$(printf '%032d' 4).json"
grep -q LIFECYCLE_REQUEST_NOT_OBJECT "$LQ2/responses/lci-$(printf '%032d' 4).json"
say "N-4 non-object JSON is refused with a response, not an unhandled AttributeError"

# ── N-5 + N-1: an unclaimable entry is quarantined, symlinks are refused ──
rm -f "$LQ2/requests"/* "$LQ2/responses"/* 2>/dev/null || true
ln -s /etc/hosts "$LQ2/requests/lci-$(printf '%032d' 5).intent.json"
for _ in $(seq 1 100); do
  [ -n "$(find "$LQ2/requests" -name '*.rejected.*' -print -quit)" ] && break; sleep 0.05
done
[ -n "$(find "$LQ2/requests" -name '*.rejected.*' -print -quit)" ]
[ -z "$(find "$LQ2/requests" -name '*.intent.json' -print -quit)" ]
say "N-5 a symlinked intent is quarantined once instead of reprocessed forever"
find "$LQ2/requests" -name '*.rejected.*' -delete

# ── G-5: every queue path component is symlink-hardened (needs N-1) ──
# The daemon MUST stay live here: exclusive broker-lock contention is a
# precondition of the fallback, so against a stopped daemon every case
# short-circuits to exit 4 (CONTROL_UNREACHABLE) and never reaches
# queue_directory at all.
DECOY="$W/decoy"; mkdir -p "$DECOY"
g5(){ # g5 <relative-component-path> <label>
  local target="$R/$1" label="$2" saved="$W/saved-$(echo "$1" | tr / _)" out rc=0
  mv "$target" "$saved"; ln -s "$DECOY" "$target"
  out="$(printf '{"action":"active-delivery","session":"life","pane":"6"}\n' \
    | FORGE_BROKER_TEST_CONNECT_FAULT=EPERM FORGE_BROKER_TEST_ENABLE=1 \
      "$B" control --root "$R" 2>&1)" || rc=$?
  rm -f "$target"; mv "$saved" "$target"
  [ "$rc" != 0 ] || { echo "FAIL G-5 $label: symlinked component was accepted" >&2; exit 1; }
  grep -q LIFECYCLE_QUEUE_UNSAFE <<<"$out" || { echo "FAIL G-5 $label: $out" >&2; exit 1; }
  [ -z "$(ls -A "$DECOY")" ] || { echo "FAIL G-5 $label: decoy was written through" >&2; exit 1; }
  say "G-5 symlinked $label refused, decoy untouched"
}
g5 ".dev/forge-broker/lifecycle/requests" "requests"
g5 ".dev/forge-broker/lifecycle/responses" "responses"
g5 ".dev/forge-broker/lifecycle" "lifecycle"
g5 ".dev/forge-broker" "forge-broker"
g5 ".dev" ".dev"
rmdir "$DECOY"
FORGE_BROKER_TEST_ENABLE=1 "$B" stop --root "$R" >/dev/null

# A stale queue-advertising pid.json cannot authorize fallback after the daemon
# releases its exclusive lock. Unlocked, absent, and symlinked lock states all
# return exit 4 and create no intent.
dead_lock_refuses(){ # dead_lock_refuses <label>
  local label="$1" out rc=0
  rm -f "$LQ2/requests"/* "$LQ2/responses"/* 2>/dev/null || true
  out="$(printf '{"action":"active-delivery","session":"life","pane":"6"}\n' \
    | FORGE_BROKER_TEST_CONNECT_FAULT=EPERM FORGE_BROKER_TEST_ENABLE=1 \
      "$B" control --root "$R" 2>&1)" || rc=$?
  [ "$rc" -eq 4 ] || { echo "FAIL G-1 $label: rc=$rc want 4 :: $out" >&2; exit 1; }
  grep -q CONTROL_UNREACHABLE <<<"$out"
  [ -z "$(find "$LQ2/requests" -name '*.intent.json' -print -quit)" ]
  say "G-1 $label fails closed without queueing"
}
dead_lock_refuses "an unlocked stale broker.lock"
mv "$P/broker.lock" "$W/broker.lock.unlocked"
dead_lock_refuses "an absent broker.lock"
ln -s "$W/broker.lock.unlocked" "$P/broker.lock"
dead_lock_refuses "a symlinked stale broker.lock"
rm -f "$P/broker.lock"; mv "$W/broker.lock.unlocked" "$P/broker.lock"

# ── N-6: an orphan collision preserves both files ──
FORGE_BROKER_TEST_ENABLE=1 "$B" stop --root "$R" >/dev/null 2>&1 || true
mkdir -p "$LQ2/requests"; rm -f "$LQ2/requests"/* 2>/dev/null || true
ORIG="lci-$(printf '%032d' 6).intent.json"
printf '{"schema":"forge-lifecycle-intent/1","intent_id":"lci-%032d","action":"active-delivery","emitted_at":"2026-08-07T00:00:00Z"}\n' 6 > "$LQ2/requests/$ORIG"
printf '{"stale":"claim"}\n' > "$LQ2/requests/$ORIG.claimed.99999"
FORGE_BROKER_TEST_ENABLE=1 "$B" start --root "$R" --session life --incarnation 1 --mode contain >/dev/null
for _ in $(seq 1 100); do [ -n "$(find "$LQ2/requests" -name '*.orphan.*' -print -quit)" ] && break; sleep 0.05; done
[ -n "$(find "$LQ2/requests" -name '*.orphan.*' -print -quit)" ]
say "N-6 a colliding orphan is parked under a unique name, never overwritten"
FORGE_BROKER_TEST_ENABLE=1 "$B" stop --root "$R" >/dev/null

# ── G-1 timeout arm: exit EXACTLY 5, with the daemon alive but not servicing ──
rm -f "$LQ2/requests"/* "$LQ2/responses"/* 2>/dev/null || true
FORGE_BROKER_TEST_QUEUE_PAUSE=1 FORGE_BROKER_TEST_ENABLE=1 "$B" start --root "$R" --session life --incarnation 1 --mode contain >/dev/null
qrc=0
printf '{"action":"active-delivery","session":"life","pane":"6"}\n' \
  | FORGE_LIFECYCLE_TIMEOUT_S=1 FORGE_BROKER_TEST_CONNECT_FAULT=EPERM FORGE_BROKER_TEST_ENABLE=1 \
    "$B" control --root "$R" >/dev/null 2>&1 || qrc=$?
[ "$qrc" -eq 5 ] || { echo "FAIL G-1 timeout: rc=$qrc want exactly 5" >&2; exit 1; }
say "G-1 an unserviced queue times out at exit EXACTLY 5 (CONTROL_QUEUED)"
FORGE_BROKER_TEST_ENABLE=1 "$B" stop --root "$R" >/dev/null

echo "broker tests: PASS"
