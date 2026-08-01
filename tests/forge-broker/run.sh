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
echo "broker tests: PASS"
