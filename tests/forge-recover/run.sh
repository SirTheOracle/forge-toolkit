#!/bin/bash
# Harness for `forge recover` (crash-recovery verb, final-plan.md P1–P11).
# Hermetic: fake tmux via FORGE_RECOVER_TMUX_LIST/STATUS (never a real server),
# scratch roots under mktemp, boot-state via FORGE_RECOVER_STATE_FILE.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FORGE="$ROOT/bin/forge"; WATCH="$ROOT/bin/forge-watch"
FORGE_SOURCE_BEFORE="$(shasum -a 256 "$FORGE" | awk '{print $1}')"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/frec.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); printf '  ok: %s\n' "$1"; }
bad(){ FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$1"; }
export FORGE_WATCH_TRIGGER=0 FORGE_RECOVER_VERIFY_SLEEP=0 FORGE_RECOVER_VERIFY_RETRIES=1

# rec <dir> <file> <json...>  — write an attention record fixture (mode 600)
rec(){ local d="$1" f="$2"; shift 2; printf '%s' "$*" > "$d/.dev/attention/$f"; chmod 600 "$d/.dev/attention/$f"; }
new_root(){ R="$WORK/$1"; mkdir -p "$R/.dev/attention/payloads"; }
# ghost records: one of every session-scoped type for session "ghost"
ghost_fixtures(){ local d="$1" s="${2:-ghost}" ts="${3:-2026-07-10T10:00:00Z}"
  rec "$d" "dispatch-cc-g1.json"      "{\"schema\":\"cc-dispatch/1\",\"event\":\"dispatch\",\"dispatch_id\":\"cc-g1\",\"session\":\"$s\",\"dispatched_at\":\"$ts\",\"payload_path\":\".dev/attention/payloads/cc-g1.txt\"}"
  rec "$d" "prompt.$s.json"           "{\"schema\":\"cc-attention/1\",\"event\":\"userpromptsubmit\",\"session\":\"$s\",\"emitted_at\":\"$ts\"}"
  rec "$d" "stop.$s.json"             "{\"schema\":\"cc-attention/1\",\"event\":\"stop\",\"session\":\"$s\",\"emitted_at\":\"$ts\",\"response_paths\":[\".dev/attention/payloads/response.cc-g1.txt\"]}"
  rec "$d" "perm.$s.abc123.json"      "{\"schema\":\"cc-attention/1\",\"event\":\"permissionrequest\",\"session\":\"$s\",\"emitted_at\":\"$ts\"}"
  rec "$d" "notify.$s.json"           "{\"schema\":\"cc-attention/1\",\"event\":\"notification\",\"session\":\"$s\",\"emitted_at\":\"$ts\"}"
  rec "$d" "ask-20260710T100000Z-aa11bb.json" "{\"schema\":\"cc-attention/1\",\"event\":\"ask\",\"session\":\"$s\",\"emitted_at\":\"$ts\",\"ask_id\":\"ask-20260710T100000Z-aa11bb\"}"
  rec "$d" "spawn-$s-needs-repair.json" "{\"schema\":\"cc-spawn/1\",\"event\":\"spawn-state\",\"session\":\"$s\",\"emitted_at\":\"$ts\"}"
  rec "$d" "wprompt.$s.p2.json"       "{\"schema\":\"cc-attention/1\",\"event\":\"userpromptsubmit\",\"session\":\"$s\",\"emitted_at\":\"$ts\"}"
  rec "$d" "wstop.$s.p2.task-1.a.json" "{\"schema\":\"cc-attention/1\",\"event\":\"stop\",\"session\":\"$s\",\"emitted_at\":\"$ts\",\"task_id\":\"task-1.a\"}"
  rec "$d" "wperm.$s.p4.beef00.json"  "{\"schema\":\"cc-attention/1\",\"event\":\"permissionrequest\",\"session\":\"$s\",\"emitted_at\":\"$ts\"}"
  printf 'payload' > "$d/.dev/attention/payloads/cc-g1.txt"
  printf 'resp'    > "$d/.dev/attention/payloads/response.cc-g1.txt"
  printf 'fall'    > "$d/.dev/attention/payloads/response.$s.txt"
  printf 'active_pipeline: slug-g\nlast_stage_completed: implementation\nlast_stage_status: done\nnext_stage: impl-review\nupdated_at: "%s"\n' "$ts" > "$d/.dev/forge-context.$s.yml"
}
recov(){ "$FORGE" recover "$@"; }

echo "── T1: --print-registry is valid JSON covering every writer schema ──"
REG="$WORK/reg.json"; recov --print-registry > "$REG" 2>/dev/null
python3 - "$REG" <<'PY' && ok "registry JSON + all writer schemas present" || bad "registry dump"
import json, sys
r = json.load(open(sys.argv[1]))
schemas = {e["schema"] for e in r["records"]}
need = {"cc-dispatch/1", "cc-attention/1", "cc-spawn/1", "cc-codex-register/1",
        "cc-registry/1", "cc-recover/1"}
assert need <= schemas, need - schemas
assert r["contexts"]["never_swept"] == ["forge-context.yml"]
PY

echo "── T2: every session record type classifies as a candidate (NO_SERVER) ──"
new_root t2; ghost_fixtures "$R"
rec "$R" "codex-register.json" '{"schema":"cc-codex-register/1","panes":{}}'
# headless codex automation records (EMPTY pane index): never candidates,
# never unknown — they terminate normally and age out via gc (live 2026-07-11)
rec "$R" "wstop.codex-t2.p.019f0000-aaaa.json" '{"schema":"cc-attention/1","event":"stop","session":"codex-t2","emitted_at":"2026-07-10T10:00:00Z"}'
rec "$R" "wprompt.codex-t2.p.json" '{"schema":"cc-attention/1","event":"userpromptsubmit","session":"codex-t2","emitted_at":"2026-07-10T10:00:00Z"}'
rec "$R" "recover-999.json" '{"schema":"cc-recover/1","event":"recover-candidates","boot_id":"999","emitted_at":"2026-07-10T10:00:00Z","candidates":1}'
mkdir -p "$R/.dev/attention/archive"
printf '{"schema":"cc-attention/1","event":"ask"}' > "$R/.dev/attention/archive/ask-old.json"
out=$(FORGE_RECOVER_TMUX_STATUS=no-server recov --root "$R" --dry-run --json); rc=$?
python3 - "$R" <<PY && ok "11 candidates (10 types + context), 0 unknown, exit 2" || bad "T2 classify: rc=$rc"
import json, sys
r = json.loads('''$out''')["roots"][0]
assert r["candidates"] == 11, r["candidates"]
assert r["unknown"] == [], r["unknown"]
assert r["needs_manual"] == []
PY
[ "$rc" -eq 2 ] && ok "dry-run with candidates exits 2" || bad "exit=$rc want 2"

echo "── T3: INSPECT_FAILED aborts with ZERO mutation (R-1 negative test) ──"
n_before=$(find "$R/.dev" -type f | wc -l | tr -d ' ')
FORGE_RECOVER_TMUX_STATUS=fail recov --root "$R" --apply --yes >/dev/null 2>&1; rc=$?
n_after=$(find "$R/.dev" -type f | wc -l | tr -d ' ')
[ "$rc" -eq 4 ] && ok "INSPECT_FAILED exits 4" || bad "exit=$rc want 4"
[ "$n_before" = "$n_after" ] && ok "no file touched on inspection failure" || bad "mutation on INSPECT_FAILED ($n_before -> $n_after)"

echo "── T4: same session name live at a DIFFERENT root → still recoverable here ──"
new_root t4a; ghost_fixtures "$R"; A="$R"
new_root t4b; B="$R"
printf 'ghost\t%s\t$9\t1000000000\n' "$B" > "$WORK/t4.tsv"
out=$(FORGE_RECOVER_TMUX_LIST="$WORK/t4.tsv" recov --root "$A" --dry-run --json)
c=$(python3 -c "import json;print(json.loads('''$out''')['roots'][0]['candidates'])")
[ "$c" -eq 11 ] && ok "(root,session) key: other-root ghost does not shield" || bad "candidates=$c want 11"

echo "── T5: name reused at same root — records straddle session_created ──"
new_root t5; ghost_fixtures "$R" ghost "2026-07-10T10:00:00Z"   # old incarnation
rec "$R" "prompt.ghost.new.json" '{"schema":"cc-attention/1","event":"userpromptsubmit","session":"ghost","emitted_at":"2026-07-11T12:00:00Z"}'
# live ghost created 2026-07-11T00:00:00Z (epoch 1783728000 > old ts, < new ts)
created=$(python3 -c "import datetime;print(int(datetime.datetime(2026,7,11,tzinfo=datetime.timezone.utc).timestamp()))")
printf 'ghost\t%s\t$7\t%s\n' "$R" "$created" > "$WORK/t5.tsv"
out=$(FORGE_RECOVER_TMUX_LIST="$WORK/t5.tsv" recov --root "$R" --dry-run --json)
python3 - <<PY && ok "pre-incarnation records candidates; post-incarnation retained" || bad "straddle"
import json
r = json.loads('''$out''')["roots"][0]
assert r["candidates"] == 11, r["candidates"]          # the 11 old ones
assert not any("prompt.ghost.new" in f for f in r["candidate_files"])
PY

echo "── T6: live same-name + unparseable timestamp → needs-manual, never archived ──"
new_root t6
rec "$R" "stop.ghost.json" '{"schema":"cc-attention/1","event":"stop","session":"ghost","emitted_at":"NOT-A-DATE"}'
printf 'ghost\t%s\t$7\t%s\n' "$R" "$created" > "$WORK/t6.tsv"
FORGE_RECOVER_TMUX_LIST="$WORK/t6.tsv" recov --root "$R" --apply --yes > "$WORK/t6.out" 2>&1
grep -q "MANUAL:" "$WORK/t6.out" && ok "reported needs-manual" || bad "no MANUAL line"
[ -f "$R/.dev/attention/stop.ghost.json" ] && ok "ambiguous record not moved" || bad "moved an ambiguous record"

echo "── T7: payload referenced by a retained live record must not move ──"
new_root t7; ghost_fixtures "$R"
# live session 'alive' stop references the same response payload as ghost's stop
rec "$R" "stop.alive.json" '{"schema":"cc-attention/1","event":"stop","session":"alive","emitted_at":"2026-07-11T10:00:00Z","response_paths":[".dev/attention/payloads/response.cc-g1.txt"]}'
printf 'orphan' > "$R/.dev/attention/payloads/response.cc-zz.txt"
printf 'alive\t%s\t$5\t1000000000\n' "$R" > "$WORK/t7.tsv"
FORGE_RECOVER_TMUX_LIST="$WORK/t7.tsv" recov --root "$R" --apply --yes > "$WORK/t7.out" 2>&1
[ -f "$R/.dev/attention/payloads/response.cc-g1.txt" ] && ok "shared payload retained (live owner)" || bad "shared payload moved"
[ ! -f "$R/.dev/attention/payloads/cc-g1.txt" ] && ok "exclusively-owned payload moved" || bad "owned payload not moved"
[ -f "$R/.dev/attention/payloads/response.cc-zz.txt" ] && ok "orphan payload left in place" || bad "orphan moved"
grep -q "orphan payload" "$WORK/t7.out" || grep -q "orphan payload" "$R"/.dev/attention/archive/*/MANIFEST.json && ok "orphan reported" || bad "orphan not reported"

echo "── T8: archive integrity — depth-2, manifest, modes, no .incomplete ──"
AD=$(ls -d "$R/.dev/attention/archive/recover-"* 2>/dev/null | head -1)
[ -n "$AD" ] && ok "archive dir minted under attention/archive/<id>/" || bad "no archive dir"
[ -f "$AD/MANIFEST.json" ] && ok "MANIFEST.json present" || bad "no manifest"
[ ! -f "$AD/.incomplete" ] && ok ".incomplete cleared after manifest" || bad ".incomplete residue"
m=$(stat -f '%Lp' "$AD/stop.ghost.json" 2>/dev/null)
[ "$m" = "600" ] && ok "file mode preserved (600)" || bad "mode=$m want 600"
python3 - "$AD/MANIFEST.json" <<'PY' && ok "manifest schema + sha256 + resume hint" || bad "manifest content"
import json, sys
m = json.load(open(sys.argv[1]))
assert m["schema"] == "cc-recover-manifest/1"
recs = {r["original"]: r for r in m["records"]}
ctx = [r for r in recs.values() if r["original"].endswith(".yml")][0]
assert ctx["slug"] == "slug-g" and ctx["next_stage"] == "impl-review"
assert "impl-review" in (ctx.get("resume") or "")
assert all(len(r["sha256"]) == 64 for r in m["records"])
PY

echo "── T9: pre-existing archive/*.json (ask/resolved-perm) untouched ──"
[ -f "$WORK/t2/.dev/attention/archive/ask-old.json" ] && ok "flat archive file untouched by earlier runs" || bad "flat archive file gone"

echo "── T10: second run is idempotent (0 candidates, exit 0) ──"
FORGE_RECOVER_TMUX_LIST="$WORK/t7.tsv" recov --root "$R" --dry-run >/dev/null 2>&1; rc=$?
[ "$rc" -eq 0 ] && ok "post-apply rescan clean" || bad "exit=$rc want 0"

echo "── T11: concurrent recovery — flock serializes, second run fails closed ──"
new_root t11; ghost_fixtures "$R"
LD="$WORK/lockdir"; mkdir -p "$LD"
python3 - "$LD" "$R" <<'PY' &
import fcntl, hashlib, os, sys, time
d, root = sys.argv[1], os.path.realpath(sys.argv[2])
p = os.path.join(d, "recover.%s.lock" % hashlib.sha256(root.encode()).hexdigest()[:8])
f = open(p, "w"); fcntl.flock(f, fcntl.LOCK_EX); time.sleep(3)
PY
HOLDER=$!
sleep 0.5
FORGE_RECOVER_LOCK_DIR="$LD" FORGE_RECOVER_TMUX_STATUS=no-server recov --root "$R" --apply --yes > "$WORK/t11.out" 2>&1; rc=$?
kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null
[ "$rc" -eq 3 ] && ok "lock-held apply exits 3 (partial failure)" || bad "exit=$rc want 3"
[ -f "$R/.dev/attention/stop.ghost.json" ] && ok "no file moved while lock held" || bad "moved under held lock"

echo "── T12: --force guard + targeted force ──"
recov --force --apply --yes >/dev/null 2>&1; rc=$?
[ "$rc" -eq 64 ] && ok "--force without --root/--session refused (64)" || bad "exit=$rc want 64"
new_root t12
rec "$R" "stop.ghost.json" '{"schema":"cc-attention/1","event":"stop","session":"ghost","emitted_at":"NOT-A-DATE"}'
printf 'ghost\t%s\t$7\t%s\n' "$R" "$created" > "$WORK/t12.tsv"
FORGE_RECOVER_TMUX_LIST="$WORK/t12.tsv" recov --root "$R" --session ghost --force --apply --yes >/dev/null 2>&1
[ ! -f "$R/.dev/attention/stop.ghost.json" ] && ok "targeted --force archives the ambiguous record" || bad "force did not move"
grep -q '"confidence": "forced"' "$R"/.dev/attention/archive/*/MANIFEST.json && ok "forced entry conspicuous in manifest" || bad "no forced marker"

echo "── T13: --session narrows the sweep ──"
new_root t13; ghost_fixtures "$R" ghost; ghost_fixtures "$R" wraith
FORGE_RECOVER_TMUX_STATUS=no-server recov --root "$R" --session wraith --apply --yes >/dev/null 2>&1
[ -f "$R/.dev/attention/stop.ghost.json" ] && ok "other session's records untouched" || bad "ghost swept under --session wraith"
[ ! -f "$R/.dev/attention/stop.wraith.json" ] && ok "target session archived" || bad "wraith not swept"

echo "── T14: context resume hint only for canonical stages ──"
new_root t14
printf 'active_pipeline: slug-w\nnext_stage: weird-stage\nupdated_at: "2026-07-10T10:00:00Z"\n' > "$R/.dev/forge-context.gone.yml"
printf 'status: legacy\n' > "$R/.dev/forge-context.yml"
FORGE_RECOVER_TMUX_STATUS=no-server recov --root "$R" --apply --yes >/dev/null 2>&1
grep -q "manual reconstruction required" "$R"/.dev/attention/archive/*/MANIFEST.json && ok "non-canonical next_stage → manual reconstruction" || bad "no manual-reconstruction entry"
[ -f "$R/.dev/forge-context.yml" ] && ok "bare legacy forge-context.yml never swept" || bad "legacy context swept"

echo "── T15: boot-state machine ──"
new_root t15; ghost_fixtures "$R"
SF="$WORK/boot.json"
bs(){ FORGE_RECOVER_STATE_FILE="$SF" FORGE_RECOVER_BOOT_ID="$1" recov --service --root "$R" >/dev/null 2>&1; }
FORGE_RECOVER_STATE_FILE="$SF" FORGE_RECOVER_BOOT_ID=b1 FORGE_RECOVER_TMUX_STATUS=no-server recov --service --root "$R" >/dev/null 2>&1
python3 -c "import json;s=json.load(open('$SF'));assert s['status']=='baseline' and s['boot_id']=='b1'" && ok "first install: baseline, no scan" || bad "baseline"
[ ! -f "$R/.dev/attention/recover-b1.json" ] && ok "no finding on baseline" || bad "baseline scanned"
FORGE_RECOVER_STATE_FILE="$SF" FORGE_RECOVER_BOOT_ID=b2 FORGE_RECOVER_TMUX_STATUS=fail recov --service --root "$R" >/dev/null 2>&1
python3 -c "import json;s=json.load(open('$SF'));assert s['boot_id']=='b1' and s['status']=='failed' and s['retry_count']==1" && ok "INSPECT_FAILED: boot id NOT advanced, retry counted" || bad "failed-scan state"
FORGE_RECOVER_STATE_FILE="$SF" FORGE_RECOVER_BOOT_ID=b2 FORGE_RECOVER_TMUX_STATUS=no-server recov --service --root "$R" >/dev/null 2>&1
python3 -c "import json;s=json.load(open('$SF'));assert s['boot_id']=='b2' and s['status']=='prompted'" && ok "retry with authoritative scan → prompted" || bad "retry state"
[ -f "$R/.dev/attention/recover-b2.json" ] && ok "durable finding written for new boot" || bad "no finding"
FORGE_RECOVER_STATE_FILE="$SF" FORGE_RECOVER_BOOT_ID=b2 FORGE_RECOVER_TMUX_STATUS=fail recov --service --root "$R" >/dev/null 2>&1; rc=$?
python3 -c "import json;s=json.load(open('$SF'));assert s['status']=='prompted'" && [ "$rc" -eq 0 ] && ok "same-boot repeat is a no-op even if tmux now fails" || bad "same-boot repeat"
[ -f "$R/.dev/attention/recover-b2.json" ] && ok "no response → finding persists (no auto-archive)" || bad "finding vanished"
FORGE_RECOVER_TMUX_STATUS=no-server recov --root "$R" --apply --yes >/dev/null 2>&1
[ ! -f "$R/.dev/attention/recover-b2.json" ] && ok "--apply supersedes the boot finding" || bad "boot finding not cleared"

echo "── T16: gc blindness — depth-2 archives exempt, depth-1 still swept ──"
new_root t16
AD="$R/.dev/attention/archive/recover-x-aa"; mkdir -p "$AD"
printf '{}' > "$AD/old.json"; printf '{}' > "$R/.dev/attention/archive/old-flat.json"
touch -t 202601010000 "$AD/old.json" "$R/.dev/attention/archive/old-flat.json"
"$FORGE" gc --root "$R" >/dev/null 2>&1
[ -f "$AD/old.json" ] && ok "aged file under archive/<id>/ survives gc" || bad "gc reached depth-2 archive"
[ ! -f "$R/.dev/attention/archive/old-flat.json" ] && ok "aged depth-1 archive file still swept (unchanged gc reach)" || bad "gc depth-1 behavior changed"

echo "── T17: forge-watch — RECOVER-PENDING renders; archived files invisible ──"
new_root t17; ghost_fixtures "$R"
FORGE_RECOVER_STATE_FILE="$WORK/boot17.json" FORGE_RECOVER_BOOT_ID=b9 FORGE_RECOVER_TMUX_STATUS=no-server recov --service --root "$R" >/dev/null 2>&1 || true
FORGE_RECOVER_STATE_FILE="$WORK/boot17.json" FORGE_RECOVER_BOOT_ID=b9x FORGE_RECOVER_TMUX_STATUS=no-server recov --service --root "$R" >/dev/null 2>&1
echo "$R" > "$WORK/t17.roots"; : > "$WORK/t17.tsv"
wout=$(FORGE_WATCH_ROOTS_FILE="$WORK/t17.roots" FORGE_WATCH_TMUX_LIST="$WORK/t17.tsv" FORGE_WATCH_CACHE_DIR="$WORK/t17c" FORGE_WATCH_CONFIG_DIR="$WORK/t17g" "$WATCH" status 2>/dev/null)
echo "$wout" | grep -q 'RECOVER-PENDING' && ok "RECOVER-PENDING finding rendered" || bad "no RECOVER-PENDING: $(echo "$wout" | head -3)"
FORGE_RECOVER_TMUX_STATUS=no-server recov --root "$R" --apply --yes >/dev/null 2>&1
wout=$(FORGE_WATCH_ROOTS_FILE="$WORK/t17.roots" FORGE_WATCH_TMUX_LIST="$WORK/t17.tsv" FORGE_WATCH_CACHE_DIR="$WORK/t17c" FORGE_WATCH_CONFIG_DIR="$WORK/t17g" "$WATCH" status 2>/dev/null)
echo "$wout" | grep -q 'ghost' && bad "archived session still renders findings" || ok "archived records invisible to the watcher"

echo "── T18: D3 residue — forge-log pendings + callbacks reported, never archived ──"
new_root t18; ghost_fixtures "$R"
mkdir -p "$R/.dev/proposals/p-1" "$R/.dev/forge-tmp/callbacks"
printf 'entries:\n  - timestamp: "2026-07-01T00:00:00Z"\n    stage: coding\n    response: null\n' > "$R/.dev/proposals/p-1/forge-log.yml"
printf 'cb' > "$R/.dev/forge-tmp/callbacks/x.callback"
out=$(FORGE_RECOVER_TMUX_STATUS=no-server recov --root "$R" --apply --yes 2>&1)
echo "$out" | grep -q "FOLLOW-UP" && ok "manual follow-ups reported" || bad "no follow-up lines"
[ -f "$R/.dev/proposals/p-1/forge-log.yml" ] && [ -f "$R/.dev/forge-tmp/callbacks/x.callback" ] && ok "residue not archived" || bad "residue touched"

echo "── T19: malformed JSON / unknown schema → untouched, reported ──"
new_root t19
printf 'not json' > "$R/.dev/attention/broken.json"
rec "$R" "mystery.json" '{"schema":"cc-future/9","event":"z","session":"ghost"}'
out=$(FORGE_RECOVER_TMUX_STATUS=no-server recov --root "$R" --apply --yes 2>&1); rc=$?
[ -f "$R/.dev/attention/broken.json" ] && [ -f "$R/.dev/attention/mystery.json" ] && ok "unknown/malformed left in place" || bad "unknown/malformed moved"
echo "$out" | grep -q "UNKNOWN" && ok "unknown reported" || bad "not reported"

echo "── T20: unreachable root is a per-root skip, not a global abort ──"
new_root t20; ghost_fixtures "$R"
out=$(FORGE_RECOVER_TMUX_STATUS=no-server recov --root "/nonexistent-root-xyz" --root "$R" --dry-run 2>&1); rc=$?
echo "$out" | grep -q "skipped" && ok "missing root skipped with warning" || bad "no skip line"
[ "$rc" -eq 2 ] && ok "other root still scanned (exit 2, candidates)" || bad "exit=$rc want 2"

echo "── T21: P13 recover four-quadrant + catch-all ──"
# ghost_fixtures seeds a dead-session candidate so the root is NON-clean — the FOLLOW-UP
# render only fires on a non-clean root (same convention as T18); the parked-quadrant
# classifier output is otherwise computed-but-unrendered on an all-clean root.
new_root recq; ghost_fixtures "$R"; mkdir -p "$R/.dev/proposals/q1" "$R/.dev/proposals/q2" "$R/.dev/proposals/q4" "$R/.dev/forge-tmp/callbacks"
printf 'entries:\n  - timestamp: "2026-07-01T00:00:00Z"\n    stage: coding\n    session: forge-1\n    parked_at: 2026-07-01T00:00:00Z\n    parked_reason: "x"\n    uncommitted: false\n    response: null\n' > "$R/.dev/proposals/q1/forge-log.yml"
printf 'slug: q1\nstage: coding\nstatus: PARKED\nworker: codex-a\nsession: forge-1\ncallback_id: q1-coding-x\ntimestamp: 2026-07-14T00:00:00Z\nmessage: |\n  p\n' > "$R/.dev/forge-tmp/callbacks/q1-coding.forge-1.callback"
printf 'entries:\n  - timestamp: "2026-07-01T00:00:00Z"\n    stage: coding\n    session: forge-1\n    parked_at: 2026-07-01T00:00:00Z\n    parked_reason: "y"\n    uncommitted: false\n    response: null\n' > "$R/.dev/proposals/q2/forge-log.yml"
printf 'slug: q2\nstage: coding\nstatus: BLOCKED\nworker: codex-a\nsession: forge-1\ncallback_id: q2-coding-x\ntimestamp: 2026-07-14T00:00:01Z\nmessage: |\n  b\n' > "$R/.dev/forge-tmp/callbacks/q2-coding.forge-1.callback"
printf 'slug: q3\nstage: coding\nstatus: PARKED\nworker: codex-a\nsession: forge-1\ncallback_id: q3-coding-x\ntimestamp: 2026-07-14T00:00:02Z\nmessage: |\n  p\n' > "$R/.dev/forge-tmp/callbacks/q3-coding.forge-1.callback"
printf 'entries:\n  - timestamp: "2026-07-01T00:00:00Z"\n    stage: coding\n    response: null\n' > "$R/.dev/proposals/q4/forge-log.yml"
printf 'slug: q5\nstage: coding\nstatus: BLOCKED\nworker: codex-a\nsession: forge-1\ncallback_id: q5-coding-x\ntimestamp: 2026-07-14T00:00:03Z\nmessage: |\n  bare-orphan\n' > "$R/.dev/forge-tmp/callbacks/q5-coding.forge-1.callback"
out=$(FORGE_RECOVER_TMUX_STATUS=no-server recov --root "$R" --dry-run 2>&1 || true)
echo "$out" | grep -q "FOLLOW-UP.*parked (deliberate) q1/coding" && ok "R1 quadrant1 deliberate" || bad "R1 missing"
echo "$out" | grep -q "FOLLOW-UP.*parked (incomplete transition) q2/coding" && ok "R2 quadrant2 incomplete" || bad "R2 missing"
echo "$out" | grep -q "FOLLOW-UP.*orphan PARKED callback q3/coding" && ok "R3 quadrant3 orphan" || bad "R3 missing"
echo "$out" | grep -q "FOLLOW-UP.*manual disposition" && ok "R4 quadrant4 manual disposition" || bad "R4 missing"
echo "$out" | grep -Eq "(q1-coding|q2-coding|q3-coding|q[123]/coding).*manual disposition" && bad "R1-3 leaked manual disposition" || ok "R1-3 no manual disposition"
echo "$out" | grep -q "FOLLOW-UP.*callback residue.*q5-coding" && ok "R5 bare BLOCKED orphan surfaced (catch-all)" || bad "R5 bare orphan dropped"

echo "── T22: P1-E recovery reports incarnation/ambiguity and mutates no callbacks ──"
new_root p1e22; ghost_fixtures "$R"; mkdir -p "$R/.dev/forge-tmp/callbacks"
cat > "$R/.dev/forge-tmp/callbacks/r22-exact-coding.forge-1.200.callback" <<'EOF'
slug: r22-exact
stage: coding
status: BLOCKED
worker: codex-a
session: forge-1
incarnation: 200
callback_id: r22-exact
timestamp: 2026-07-14T00:00:00Z
selected_pending_timestamp: "2026-07-14T00:00:00Z"
message: exact residue
EOF
cat > "$R/.dev/forge-tmp/callbacks/r22-amb-coding.forge-1.201.callback" <<'EOF'
slug: r22-amb
stage: coding
status: BLOCKED
worker: codex-a
session: forge-1
incarnation: 201
callback_id: r22-amb-exact
timestamp: 2026-07-14T00:00:01Z
message: ambiguous residue
EOF
cat > "$R/.dev/forge-tmp/callbacks/r22-amb-coding.forge-1.callback" <<'EOF'
slug: r22-amb
stage: coding
status: BLOCKED
worker: codex-a
session: forge-1
callback_id: r22-amb-session
timestamp: 2026-07-14T00:00:02Z
message: ambiguous legacy residue
EOF
cat > "$R/.dev/forge-tmp/callbacks/r22-mismatch-coding.wrong.callback" <<'EOF'
slug: r22-mismatch
stage: coding
status: BLOCKED
worker: codex-a
callback_id: r22-mismatch
timestamp: 2026-07-14T00:00:03Z
message: filename mismatch
EOF
cat > "$R/.dev/forge-tmp/callbacks/r22-dead-coding.forge-1.199.callback" <<'EOF'
slug: r22-dead
stage: coding
status: BLOCKED
worker: codex-a
session: forge-1
incarnation: 199
callback_id: r22-dead
timestamp: 2026-07-14T00:00:04Z
message: dead predecessor
EOF
cat > "$R/.dev/forge-tmp/callbacks/r22-foreign-coding.forge-foreign.300.callback" <<'EOF'
slug: r22-foreign
stage: coding
status: BLOCKED
worker: codex-a
session: forge-foreign
incarnation: 300
callback_id: r22-foreign
timestamp: 2026-07-14T00:00:05Z
message: foreign root
EOF
cat > "$R/.dev/forge-tmp/callbacks/r22-inc-only-coding.callback" <<'EOF'
slug: r22-inc-only
stage: coding
status: BLOCKED
worker: codex-a
incarnation: 404
callback_id: r22-inc-only
timestamp: 2026-07-14T00:00:06Z
message: invalid incarnation without session
EOF
printf '[unterminated\n' > "$R/.dev/forge-tmp/callbacks/r22-invalid-coding.callback"
printf 'forge-1\t%s\t$1\t200\nforge-foreign\t%s\t$2\t300\n' "$R" "$WORK/other-root" > "$WORK/t22-live.tsv"
before=$(find "$R/.dev/forge-tmp/callbacks" -type f -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256 | shasum -a 256)
out=$(FORGE_RECOVER_TMUX_LIST="$WORK/t22-live.tsv" recov --root "$R" --dry-run 2>&1 || true)
after=$(find "$R/.dev/forge-tmp/callbacks" -type f -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256 | shasum -a 256)
echo "$out" | grep -q 'incarnation callback residue r22-exact/coding' \
  && echo "$out" | grep -q 'ambiguous callback residue r22-amb/coding' \
  && echo "$out" | grep -q 'callback residue malformed-header.*r22-invalid-coding.callback' \
  && echo "$out" | grep -q 'callback residue header-filename-mismatch.*r22-mismatch-coding.wrong.callback' \
  && echo "$out" | grep -q 'r22-exact/coding.*classification=exact-current' \
  && echo "$out" | grep -q 'r22-dead/coding.*classification=dead-incarnation' \
  && echo "$out" | grep -q 'r22-foreign/coding.*classification=foreign-incarnation' \
  && echo "$out" | grep -q 'callback residue malformed-header.*r22-inc-only-coding.callback' \
  && [ "$before" = "$after" ] \
  && ok "T22 exact+ambiguous incarnation residue reported without mutation" || bad "T22 recovery classification/no-mutation"

# Post-all-source schema lockstep: this intentionally lives in Commit 2 after
# bridge, watcher, and recovery new_strings all exist.
python3 - "$ROOT" <<'PY' && ok "T23 callback schema known/required sets stay in reader lockstep" || bad "T23 callback schema lockstep"
import ast,pathlib,re,sys,yaml
root=pathlib.Path(sys.argv[1]); schema=yaml.safe_load((root/'orchestrator/schemas/callback.yml').read_text())
want={'known':set(schema['properties']),'required':set(schema['required'])}; found={}
pat=re.compile(r'# CALLBACK_SCHEMA_LOCKSTEP ([a-z-]+) (known|required)\n\s*(?:known|required)\s*=\s*(\{[^\n]+\})')
for rel in ('bin/forge-bridge','bin/forge-watch','bin/forge'):
    for reader,kind,literal in pat.findall((root/rel).read_text()):
        key=(reader,kind); assert key not in found,key; found[key]=set(ast.literal_eval(literal))
readers={'bridge-scanner','bridge-audit','forge-watch','forge-recovery'}
assert set(r for r,_ in found)==readers and len(found)==8,found.keys()
for (reader,kind),value in found.items(): assert value==want[kind],(reader,kind,value^want[kind])
PY

FORGE_SOURCE_AFTER="$(shasum -a 256 "$FORGE" | awk '{print $1}')"
[ "$FORGE_SOURCE_BEFORE" = "$FORGE_SOURCE_AFTER" ] \
  && ok "T24 recovery product source hash unchanged during P1-WC compatibility run" \
  || bad "T24 recovery product source hash changed"
echo "═══ PASS: $PASS  FAIL: $FAIL ═══"; [ "$FAIL" -eq 0 ]
