#!/bin/bash
# run.sh — self-contained test harness for the cross-worktree infra lock and the
# callback lifecycle it depends on, exercised against the repo's bin/forge-bridge.
#
# Recreates the (uncommitted, lost) 58-assertion scratchpad harness described in
# handoffs/handoff-2026-06-29-infra-lock-IMPLEMENTED.md as a durable, re-runnable
# suite. Three sections mirror the originals:
#   1. callback lifecycle  — cmd_callback terminal ordering (DONE/ERROR close-then-
#      publish, failed close aborts), BLOCKED keeps pending open, callback-consume.
#   2. infra-lock mutex     — acquire/release/status, steal on dead session, reentrancy,
#      stage-update, CONFLICT, foreign-host, corrupt→ESCALATE, injection-safety, env knobs.
#   3. acceptance           — subcommands wired, arg validation, help/defaults, doc/skill anchors.
#
# Style matches tests/forge-cc/run.sh and tests/forge-watch/run.sh: hermetic temp
# dirs, PASS/FAIL counters, nonzero exit on any failure.
#
# HERMETIC GUARANTEES:
#   * All lock/callback state lives under $WORK via FORGE_INFRA_LOCK_DIR and temp
#     project roots — never a real project root, never the real git common dir.
#   * Liveness tests create a UNIQUELY-NAMED throwaway tmux session and always kill
#     it in the EXIT trap. The live sessions forge-1..forge-4 are never touched.
#   * The acquiring identity is injected via data-only FORGE_LOCK_SELF_* seams so the
#     tests never depend on the caller's own tmux session; liveness still consults
#     REAL tmux (that is the behavior under test).
#
# Usage: bash tests/forge-infra-lock/run.sh   (exit 0 = all pass)

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BRIDGE="$ROOT/bin/forge-bridge"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/fil.XXXXXX")"
TSESS="filtest-$$-$RANDOM"                       # throwaway tmux session for liveness
trap 'tmux kill-session -t "$TSESS" 2>/dev/null; rm -rf "$WORK"' EXIT

PASS=0; FAIL=0
red()   { printf '\033[0;31m%s\033[0m\n' "$1"; }
green() { printf '\033[0;32m%s\033[0m\n' "$1"; }
ok()  { PASS=$((PASS+1)); green "  ok: $1"; }
bad() { FAIL=$((FAIL+1)); red   "  FAIL: $1"; }

[ -x "$BRIDGE" ] || { red "bin/forge-bridge not found/executable at $BRIDGE"; exit 1; }
command -v tmux >/dev/null 2>&1 || { red "tmux required for liveness tests"; exit 1; }

LOCKDIR="$WORK/lockanchor"; mkdir -p "$LOCKDIR"
WC_TEST_SESSION=forge-wc
WC_TEST_INCARNATION=777
WC_TMUX_LIST="$WORK/p1wc-tmux-list.tsv"
: > "$WC_TMUX_LIST"
export FORGE_TMUX_LIST="$WC_TMUX_LIST"

# Every hermetic root has one live exact identity in the shared topology seam.
mkproj() {  # mkproj <name> -> echoes abs path
    local p="$WORK/$1"
    mkdir -p "$p/.claude" "$p/.dev/forge-tmp/callbacks" "$p/.dev/proposals"
    printf 'name: %s\n' "$1" > "$p/.claude/forge-project.yml"
    if ! awk -F'\t' -v root="$p" '$2==root{found=1} END{exit !found}' "$WC_TMUX_LIST"; then
        printf '%s\t%s\t%s\t%s\n' "$WC_TEST_SESSION" "$p" "$$" "$WC_TEST_INCARNATION" >> "$WC_TMUX_LIST"
    fi
    printf '%s' "$p"
}

# An open pending. Identity is stamped by the publishing helper so tests that
# intentionally have no matching stage remain negative fixtures.
pending_entry() {  # pending_entry <root> <slug> <stage> <to> <ts>
    local d="$1/.dev/proposals/$2"; mkdir -p "$d"
    cat > "$d/forge-log.yml" <<EOF
pipeline: $2
entries:
  - timestamp: "$5"
    stage: $3
    to: $4
    response: null
EOF
}

arg_value() {  # <flag> followed by arguments
    local want="$1"; shift
    while [ $# -gt 0 ]; do
        if [ "$1" = "$want" ]; then printf '%s' "$2"; return 0; fi
        shift
    done
    return 1
}

stamp_pending_identity() {  # <root> <slug> <stage> <session> <incarnation>
    python3 - "$1/.dev/proposals/$2/forge-log.yml" "$3" "$4" "$5" <<'PY'
import re,sys
path,stage,session,incarnation=sys.argv[1:5]
try: raw=open(path).read()
except FileNotFoundError: sys.exit(0)
parts=re.split(r'(?=^  - timestamp:)',raw,flags=re.M); changed=False
for idx,block in enumerate(parts):
    if not re.search(r'^    stage: '+re.escape(stage)+r'\s*$',block,re.M) or not re.search(r'^    response: null\s*$',block,re.M): continue
    have_s=re.search(r'^    session: (.*)$',block,re.M); have_i=re.search(r'^    incarnation: (.*)$',block,re.M)
    if have_s and have_s.group(1).strip()!=session: continue
    if have_i and have_i.group(1).strip()!=incarnation: continue
    if have_s: block=re.sub(r'^    session: .*$',f'    session: {session}',block,flags=re.M)
    else: block=re.sub(r'(^    to: .*\n)',r'\1    session: '+session+'\n',block,count=1,flags=re.M)
    if have_i: block=re.sub(r'^    incarnation: .*$',f'    incarnation: {incarnation}',block,flags=re.M)
    else: block=re.sub(r'(^    session: .*\n)',r'\1    incarnation: '+incarnation+'\n',block,count=1,flags=re.M)
    parts[idx]=block; changed=True
if changed: open(path,'w').write(''.join(parts))
PY
}

cb() {  # cb <root> followed by callback arguments
    local r="$1"; shift
    cbS "$r" "$WC_TEST_SESSION" "$@"
}
consume() {  # consume <root> followed by consume arguments
    local r="$1"; shift
    briS "$r" "$WC_TEST_SESSION" callback-consume "$@"
}

# infra-lock with an injected data-only identity.
il() {  # il <host> <session> <sid> <created> followed by infra-lock arguments
    local host="$1" sess="$2" sid="$3" created="$4"; shift 4
    ( cd "$PROJ" && \
      FORGE_INFRA_LOCK_DIR="$LOCKDIR" \
      FORGE_LOCK_SELF_HOST="$host" FORGE_LOCK_SELF_SESSION="$sess" \
      FORGE_LOCK_SELF_SESSION_ID="$sid" FORGE_LOCK_SELF_SESSION_CREATED="$created" \
      "$BRIDGE" infra-lock "$@" )
}

field() { grep '^status:' "$1" 2>/dev/null | awk '{print $2}'; }
wc_callback_path() { printf '%s/.dev/forge-tmp/callbacks/%s-%s.%s.%s.callback' "$1" "$2" "$3" "${4:-$WC_TEST_SESSION}" "${5:-$WC_TEST_INCARNATION}"; }

cbS() {  # cbS <root> <session> followed by callback arguments
    local r="$1" s="$2"; shift 2
    local i="${FORGE_TEST_SESSION_INCARNATION:-$WC_TEST_INCARNATION}" slug stage
    slug="$(arg_value --slug "$@")"; stage="$(arg_value --stage "$@")"
    stamp_pending_identity "$r" "$slug" "$stage" "$s" "$i"
    local tl; tl="$(mktemp)"; printf '%s\t%s\t%s\t%s\n' "$s" "$r" "$$" "$i" > "$tl"
    ( cd "$r" && env -u TMUX TMUX_SESSION="$s" FORGE_TMUX_LIST="$tl" "$BRIDGE" callback "$@" )
    local rc=$?; rm -f "$tl"; return $rc
}
briS() {  # briS <root> <session> followed by a verb and arguments
    local r="$1" s="$2"; shift 2
    local i="${FORGE_TEST_SESSION_INCARNATION:-$WC_TEST_INCARNATION}"
    local tl; tl="$(mktemp)"; printf '%s\t%s\t%s\t%s\n' "$s" "$r" "$$" "$i" > "$tl"
    ( cd "$r" && env -u TMUX TMUX_SESSION="$s" FORGE_TMUX_LIST="$tl" "$BRIDGE" "$@" )
    local rc=$?; rm -f "$tl"; return $rc
}
briL() {  # all-legacy actor: session known, incarnation unavailable
    local r="$1" s="$2"; shift 2
    local tl; tl="$(mktemp)"; printf '%s\t%s\n' "$s" "$r" > "$tl"
    ( cd "$r" && env -u TMUX TMUX_SESSION="$s" FORGE_TMUX_LIST="$tl" "$BRIDGE" "$@" )
    local rc=$?; rm -f "$tl"; return $rc
}
parked_pending() {  # <root> <slug> <stage> <to> <ts> <reason> [session]
    local d="$1/.dev/proposals/$2"; mkdir -p "$d"
    { echo "pipeline: $2"; echo "entries:"; echo "  - timestamp: \"$5\""; echo "    stage: $3";
      echo "    to: $4"; [ -n "${7:-}" ] && echo "    session: $7";
      echo "    parked_at: $5"; echo "    parked_reason: \"$6\""; echo "    uncommitted: false";
      echo "    response: null"; } > "$d/forge-log.yml"
}
mk_stage_stub() { export FORGE_PROMPTS_DIR="$1/prompts"; mkdir -p "$FORGE_PROMPTS_DIR"; : > "$FORGE_PROMPTS_DIR/coding.md"; : > "$FORGE_PROMPTS_DIR/qa.md"; }
export FORGE_WATCH_TRIGGER=0  # hermetic suite: never poke the real board
lifelock_path() { echo "$1/.dev/forge-tmp/locks/lifecycle-$2--$3.lock"; }  # matches _lifecycle_lock

# One body serves short stress, the true product-default control, and full-suite B6.
# b6_case <unique-label> <wait-seconds-or-empty-for-product-default>
b6_case() {
    local label="$1" wait_s="$2" expected_wait="${2:-5}"
    local p slug="p6-$label" lk ready release hold i out rc t0 t1 elapsed_ok qaf caf infra parent_pid
    p="$(mkproj "b6-$label")"
    infra="$p/.dev/forge-tmp/b6-infra-locks"
    mkdir -p "$infra"
    pending_entry "$p" "$slug" coding codex-a "2026-06-29T00:00:00Z"
    cb "$p" --slug "$slug" --stage coding --status BLOCKED --worker codex-a --message x >/dev/null 2>&1
    lk="$(lifelock_path "$p" "$slug" coding)"
    ready="$p/.dev/forge-tmp/b6-holder.ready"
    release="$p/.dev/forge-tmp/b6-holder.release"
    parent_pid=$$
    mkdir -p "$(dirname "$lk")"
    (
        exec 9>"$lk"
        flock 9
        : > "$ready"
        while [ ! -f "$release" ] && kill -0 "$parent_pid" 2>/dev/null; do sleep 0.05; done
    ) &
    hold=$!

    i=0
    while [ ! -f "$ready" ] && [ "$i" -lt 100 ] && kill -0 "$hold" 2>/dev/null; do
        sleep 0.05
        i=$((i+1))
    done
    if [ ! -f "$ready" ] || ! kill -0 "$hold" 2>/dev/null; then
        bad "B6 $label holder died or did not become ready after flock"
        : > "$release"
        kill "$hold" 2>/dev/null
        wait "$hold" 2>/dev/null
        return 1
    fi
    ok "B6 $label holder reported post-flock readiness"

    t0=$(python3 -c 'import time; print(time.time())')
    if [ -n "$wait_s" ]; then
        out=$(cd "$p" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION \
            FORGE_INFRA_LOCK_DIR="$infra" FORGE_LIFECYCLE_LOCK_WAIT_S="$wait_s" \
            FORGE_LOCK_SELF_HOST=h FORGE_LOCK_SELF_SESSION="b6-$label" \
            FORGE_LOCK_SELF_SESSION_ID="b6-$label" FORGE_LOCK_SELF_SESSION_CREATED=1 \
            "$BRIDGE" park --slug "$slug" --stage coding --reason later 2>&1); rc=$?
    else
        out=$(cd "$p" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION -u FORGE_LIFECYCLE_LOCK_WAIT_S \
            FORGE_INFRA_LOCK_DIR="$infra" \
            FORGE_LOCK_SELF_HOST=h FORGE_LOCK_SELF_SESSION="b6-$label" \
            FORGE_LOCK_SELF_SESSION_ID="b6-$label" FORGE_LOCK_SELF_SESSION_CREATED=1 \
            "$BRIDGE" park --slug "$slug" --stage coding --reason later 2>&1); rc=$?
    fi
    t1=$(python3 -c 'import time; print(time.time())')
    elapsed_ok=$(python3 - "$t0" "$t1" "$expected_wait" <<'PY'
import sys
start, end, wait = map(float, sys.argv[1:])
elapsed = end - start
# The upper margin is a harness-sanity bound, not a lifecycle-lock semantic.
print("yes" if elapsed >= max(0.0, wait - 0.25) and elapsed < wait + 5.0 else "no")
PY
)
    [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q 'LIFECYCLE_LOCK: busy' \
        && ok "B6 $label park refused while coding lock held" \
        || bad "B6 $label contention refusal wrong: rc=$rc out=$out"
    [ "$elapsed_ok" = yes ] \
        && ok "B6 $label contention honored wait=$expected_wait" \
        || bad "B6 $label elapsed outside wait=$expected_wait bounds (start=$t0 end=$t1)"

    cat >> "$p/.dev/proposals/$slug/forge-log.yml" <<EOF
  - timestamp: "2026-06-29T01:00:00Z"
    stage: qa
    to: codex-b
    response: null
EOF
    cb "$p" --slug "$slug" --stage qa --status BLOCKED --worker codex-b --message qa-block >/dev/null 2>&1
    out=$(cd "$p" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION \
        FORGE_INFRA_LOCK_DIR="$infra" FORGE_LIFECYCLE_LOCK_WAIT_S=0.2 \
        FORGE_LOCK_SELF_HOST=h FORGE_LOCK_SELF_SESSION="b6-$label" \
        FORGE_LOCK_SELF_SESSION_ID="b6-$label" FORGE_LOCK_SELF_SESSION_CREATED=1 \
        "$BRIDGE" park --slug "$slug" --stage qa --reason distinct-key 2>&1); rc=$?
    qaf="$(wc_callback_path "$p" "$slug" qa)"
    caf="$(wc_callback_path "$p" "$slug" coding)"
    if [ "$rc" -eq 0 ] && [ "$(field "$qaf")" = PARKED ] \
       && [ "$(field "$caf")" = BLOCKED ] && kill -0 "$hold" 2>/dev/null \
       && python3 - "$p/.dev/proposals/$slug/forge-log.yml" <<'PY'
import sys, yaml
entries = yaml.safe_load(open(sys.argv[1]))['entries']
coding = [e for e in entries if e.get('stage') == 'coding']
qa = [e for e in entries if e.get('stage') == 'qa']
assert len(coding) == 1 and coding[0].get('response') is None and not coding[0].get('parked_at')
assert len(qa) == 1 and qa[0].get('response') is None and qa[0].get('parked_at')
PY
    then
        ok "B6 $label qa lifecycle succeeds while coding key remains held"
    else
        bad "B6 $label distinct-key lifecycle/state failed: rc=$rc out=$out"
    fi
    : > "$release"
    wait "$hold" 2>/dev/null
}

wc_matrix_fingerprint() { # <project> <slug>
    python3 - "$1" "$2" <<'PY'
import hashlib,os,sys
root,slug=sys.argv[1:3]; paths=[]
log=os.path.join(root,'.dev','proposals',slug,'forge-log.yml')
if os.path.isfile(log): paths.append(log)
cb=os.path.join(root,'.dev','forge-tmp','callbacks')
for base,_,files in os.walk(cb):
    for name in files:
        if name.startswith(slug+'-'): paths.append(os.path.join(base,name))
print('|'.join(os.path.relpath(p,root)+':'+hashlib.sha256(open(p,'rb').read()).hexdigest() for p in sorted(paths)))
PY
}
wc_matrix_replace() { python3 - "$1" "$2" "$3" <<'PY'
import sys
p,old,new=sys.argv[1:4]; raw=open(p).read(); assert raw.count(old)==1,(p,old,raw.count(old)); open(p,'w').write(raw.replace(old,new,1))
PY
}
wc_matrix_wait_file() { # <path>
    local path="$1" i=0
    while [ ! -e "$path" ] && [ "$i" -lt 500 ]; do sleep 0.01; i=$((i + 1)); done
    [ -e "$path" ]
}
wc_matrix_legacy_callback() { # <path> <slug> <session-or-empty> <selected-ts>
    { echo "slug: $2"; echo 'stage: coding'; echo 'status: BLOCKED'; echo 'worker: codex-a';
      [ -n "$3" ] && echo "session: $3"; echo "callback_id: legacy-$2"; echo 'timestamp: 2026-07-15T00:00:01Z';
      echo "selected_pending_timestamp: \"$4\""; echo 'message: legacy'; } > "$1"
}
wc_matrix_race() { # <dimension>
    local kind="$1" p slug="wc-race-$1" cb tl lk hready hrelease bready brelease holder consumer rc expected out
    p="$(mkproj "$slug")"; pending_entry "$p" "$slug" coding codex-a "2026-07-15T00:00:00Z"
    cb "$p" --slug "$slug" --stage coding --status BLOCKED --worker codex-a --message original >/dev/null 2>&1
    cb="$(wc_callback_path "$p" "$slug" coding)"; tl="$p/.matrix-tmux.tsv"
    printf '%s\t%s\t%s\t%s\n' "$WC_TEST_SESSION" "$p" "$$" "$WC_TEST_INCARNATION" > "$tl"
    lk="$(lifelock_path "$p" "$slug" coding)"; hready="$p/.hready"; hrelease="$p/.hrelease"; bready="$p/.bready"; brelease="$p/.brelease"
    ( exec 9>"$lk"; flock 9; : > "$hready"; wc_matrix_wait_file "$hrelease" ) & holder=$!
    wc_matrix_wait_file "$hready" || { : > "$hrelease"; kill "$holder" 2>/dev/null || true; wait "$holder" 2>/dev/null || true; bad "MATRIX $kind holder barrier timed out"; return; }
    ( cd "$p" && env -u TMUX TMUX_SESSION="$WC_TEST_SESSION" FORGE_TMUX_LIST="$tl" FORGE_LIFECYCLE_LOCK_WAIT_S=5 \
        FORGE_CALLBACK_PRELOCK_READY="$bready" FORGE_CALLBACK_PRELOCK_RELEASE="$brelease" \
        "$BRIDGE" callback-consume --slug "$slug" --stage coding --status BLOCKED >"$p/.matrix-out" 2>&1 ) & consumer=$!
    wc_matrix_wait_file "$bready" || { : > "$brelease"; : > "$hrelease"; kill "$consumer" "$holder" 2>/dev/null || true; wait "$consumer" 2>/dev/null || true; wait "$holder" 2>/dev/null || true; bad "MATRIX $kind consumer barrier timed out"; return; }
    case "$kind" in
      session) wc_matrix_replace "$cb" "session: $WC_TEST_SESSION" 'session: changed-session' ;;
      incarnation) wc_matrix_replace "$cb" "incarnation: $WC_TEST_INCARNATION" 'incarnation: 778' ;;
      callback_id) wc_matrix_replace "$cb" 'callback_id:' 'callback_id: changed-' ;;
      status) wc_matrix_replace "$cb" 'status: BLOCKED' 'status: PARKED' ;;
      selected_pending) wc_matrix_replace "$cb" 'selected_pending_timestamp: "2026-07-15T00:00:00Z"' 'selected_pending_timestamp: "2026-07-15T00:00:09Z"' ;;
      hash) printf '# hash-only-change\n' >> "$cb" ;;
      pending_timestamp) wc_matrix_replace "$p/.dev/proposals/$slug/forge-log.yml" 'timestamp: "2026-07-15T00:00:00Z"' 'timestamp: "2026-07-15T00:00:09Z"' ;;
      pending_status) wc_matrix_replace "$p/.dev/proposals/$slug/forge-log.yml" 'response: null' 'response: "external-close"' ;;
      candidate_session) wc_matrix_legacy_callback "$p/.dev/forge-tmp/callbacks/$slug-coding.$WC_TEST_SESSION.callback" "$slug" "$WC_TEST_SESSION" '2026-07-15T00:00:00Z' ;;
      candidate_unqualified) wc_matrix_legacy_callback "$p/.dev/forge-tmp/callbacks/$slug-coding.callback" "$slug" '' '2026-07-15T00:00:00Z' ;;
      candidate_remove) rm -f "$cb" ;;
      actor_incarnation) printf '%s\t%s\t%s\t778\n' "$WC_TEST_SESSION" "$p" "$$" > "$tl" ;;
    esac
    expected="$(wc_matrix_fingerprint "$p" "$slug")"; : > "$brelease"; sleep 0.05
    kill -0 "$consumer" 2>/dev/null || bad "MATRIX $kind did not wait behind held lock"
    : > "$hrelease"; wait "$holder"; wait "$consumer"; rc=$?; out="$(cat "$p/.matrix-out")"
    [ "$rc" -ne 0 ] && ! printf '%s' "$out" | grep -q 'LIFECYCLE_LOCK: busy' \
      && [ "$expected" = "$(wc_matrix_fingerprint "$p" "$slug")" ] \
      && ok "MATRIX lock-time $kind refusal is artifact-immutable" \
      || bad "MATRIX lock-time $kind failed rc=$rc out=$out"
}
wc_matrix_legacy_case() { # <label> <known|legacy> <session|unqualified> <success|refuse>
    local label="$1" actor="$2" shape="$3" expect="$4" p slug="wc-$1" ts=2026-07-15T01:00:00Z cbpath before out rc cbs=''
    p="$(mkproj "$slug")"; mkdir -p "$p/.dev/proposals/$slug"; [ "$shape" = session ] && cbs="$WC_TEST_SESSION"
    { echo "pipeline: $slug"; echo entries:; echo "  - timestamp: \"$ts\""; echo '    stage: coding'; echo '    to: codex-a';
      [ -n "$cbs" ] && echo "    session: $cbs"; echo '    response: null'; } > "$p/.dev/proposals/$slug/forge-log.yml"
    if [ -n "$cbs" ]; then cbpath="$p/.dev/forge-tmp/callbacks/$slug-coding.$cbs.callback"; else cbpath="$p/.dev/forge-tmp/callbacks/$slug-coding.callback"; fi
    wc_matrix_legacy_callback "$cbpath" "$slug" "$cbs" "$ts"; before="$(wc_matrix_fingerprint "$p" "$slug")"
    if [ "$actor" = known ]; then out="$(briS "$p" "$WC_TEST_SESSION" callback-consume --slug "$slug" --stage coding --status BLOCKED 2>&1)"; rc=$?;
    else out="$(briL "$p" "$WC_TEST_SESSION" callback-consume --slug "$slug" --stage coding --status BLOCKED 2>&1)"; rc=$?; fi
    if [ "$expect" = success ]; then
      [ "$rc" -eq 0 ] && [ -f "$p/.dev/forge-tmp/callbacks/archive/$slug-coding.legacy-$slug.callback" ] \
        && ok "MATRIX $label all-legacy success" || bad "MATRIX $label expected success: $out"
    else
      [ "$rc" -ne 0 ] && [ "$before" = "$(wc_matrix_fingerprint "$p" "$slug")" ] \
        && ok "MATRIX $label known actor refusal immutable" || bad "MATRIX $label mutated legacy: $out"
    fi
}
wc_matrix_exact_plus() { # <session|unqualified>
    local shape="$1" p slug="wc-amb-$1" legacy before out rc cbs=''
    p="$(mkproj "$slug")"; pending_entry "$p" "$slug" coding codex-a "2026-07-15T02:00:00Z"; cb "$p" --slug "$slug" --stage coding --status BLOCKED --worker codex-a --message exact >/dev/null 2>&1
    [ "$shape" = session ] && cbs="$WC_TEST_SESSION"
    if [ -n "$cbs" ]; then legacy="$p/.dev/forge-tmp/callbacks/$slug-coding.$cbs.callback"; else legacy="$p/.dev/forge-tmp/callbacks/$slug-coding.callback"; fi
    wc_matrix_legacy_callback "$legacy" "$slug" "$cbs" '2026-07-15T02:00:00Z'; before="$(wc_matrix_fingerprint "$p" "$slug")"
    out="$(consume "$p" --slug "$slug" --stage coding --status BLOCKED 2>&1)"; rc=$?
    [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q 'AMBIGUOUS' && [ "$before" = "$(wc_matrix_fingerprint "$p" "$slug")" ] \
      && ok "MATRIX exact+$shape ambiguity immutable" || bad "MATRIX exact+$shape ambiguity failed: $out"
}
wc_matrix_rebirth() { # <consume|park|resolve>
    local verb="$1" p slug="wc-rebirth-$1" cb tl before out rc status=BLOCKED
    [ "$verb" = resolve ] && status=PARKED; p="$(mkproj "$slug")"; mkdir -p "$p/.dev/proposals/$slug"
    { echo "pipeline: $slug"; echo entries:; echo '  - timestamp: "2026-07-15T03:00:00Z"'; echo '    stage: coding'; echo '    to: codex-a';
      echo "    session: $WC_TEST_SESSION"; echo '    incarnation: 776'; [ "$verb" = resolve ] && echo '    parked_at: "2026-07-15T03:00:01Z"'; echo '    response: null'; } > "$p/.dev/proposals/$slug/forge-log.yml"
    cb="$p/.dev/forge-tmp/callbacks/$slug-coding.$WC_TEST_SESSION.776.callback"
    { echo "slug: $slug"; echo 'stage: coding'; echo "status: $status"; echo 'worker: codex-a'; echo "session: $WC_TEST_SESSION"; echo 'incarnation: 776';
      echo "callback_id: rebirth-$verb"; echo 'timestamp: 2026-07-15T03:00:02Z'; echo 'selected_pending_timestamp: "2026-07-15T03:00:00Z"'; echo 'message: predecessor'; } > "$cb"
    tl="$p/.rebirth.tsv"; printf '%s\t%s\t%s\t%s\n' "$WC_TEST_SESSION" "$p" "$$" "$WC_TEST_INCARNATION" > "$tl"; before="$(wc_matrix_fingerprint "$p" "$slug")"
    case "$verb" in
      consume) out="$(cd "$p" && env -u TMUX TMUX_SESSION="$WC_TEST_SESSION" FORGE_TMUX_LIST="$tl" "$BRIDGE" callback-consume --slug "$slug" --stage coding --status BLOCKED 2>&1)"; rc=$? ;;
      park) out="$(cd "$p" && env -u TMUX TMUX_SESSION="$WC_TEST_SESSION" FORGE_TMUX_LIST="$tl" "$BRIDGE" park --slug "$slug" --stage coding --reason no 2>&1)"; rc=$? ;;
      resolve) out="$(cd "$p" && env -u TMUX TMUX_SESSION="$WC_TEST_SESSION" FORGE_TMUX_LIST="$tl" "$BRIDGE" park --resolve --slug "$slug" --stage coding --note no 2>&1)"; rc=$? ;;
    esac
    [ "$before" = "$(wc_matrix_fingerprint "$p" "$slug")" ] && ok "MATRIX rebirth $verb predecessor immutable" || bad "MATRIX rebirth $verb mutated predecessor: $out"
}
wc_matrix_empty_origin_delegate() {
    local p slug=wc-empty-origin cb id before out rc archive
    p="$(mkproj "$slug")"; pending_entry "$p" "$slug" coding codex-a "2026-07-15T04:00:00Z"; cb "$p" --slug "$slug" --stage coding --status BLOCKED --worker codex-a --message empty >/dev/null 2>&1
    cb="$(wc_callback_path "$p" "$slug" coding)"; id="$(sed -n 's/^callback_id: //p' "$cb")"
    ( cd "$p" && "$BRIDGE" park --slug "$slug" --stage coding --reason later >/dev/null 2>&1 )
    before="$(shasum -a 256 "$cb" | awk '{print $1}')"
    out="$(cd "$p" && "$BRIDGE" park --resolve --slug "$slug" --stage coding --note done 2>&1)"; rc=$?; archive="$p/.dev/forge-tmp/callbacks/archive/$slug-coding.$id.callback"
    [ "$rc" -eq 0 ] && [ -f "$archive" ] && [ "$before" = "$(shasum -a 256 "$archive" | awk '{print $1}')" ] \
      && ok 'MATRIX empty origin carries nonempty SHA through delegated archive' || bad "MATRIX empty-origin delegation failed: $out"
}
run_p1wc_matrix() {
    local k
    for k in session incarnation callback_id status selected_pending hash pending_timestamp pending_status candidate_session candidate_unqualified candidate_remove actor_incarnation; do wc_matrix_race "$k"; done
    wc_matrix_legacy_case known-session known session refuse; wc_matrix_legacy_case known-unqualified known unqualified refuse
    wc_matrix_legacy_case legacy-session legacy session success; wc_matrix_legacy_case legacy-unqualified legacy unqualified success
    wc_matrix_exact_plus session; wc_matrix_exact_plus unqualified
    wc_matrix_rebirth consume; wc_matrix_rebirth park; wc_matrix_rebirth resolve
    wc_matrix_empty_origin_delegate
}
if [ "${1:-}" = "--p1wc-matrix" ]; then
    run_p1wc_matrix
    green "PASS: $PASS"; [ "$FAIL" -gt 0 ] && red "FAIL: $FAIL" || green "FAIL: 0"
    [ "$FAIL" -eq 0 ]; exit $?
fi

if [ "${1:-}" = "--b6-stress" ]; then
    iterations="${2:-25}"
    case "$iterations" in *[!0-9]*|'') red "--b6-stress requires a positive integer"; exit 2 ;; esac
    [ "$iterations" -gt 0 ] || { red "--b6-stress requires a positive integer"; exit 2; }
    n=1
    while [ "$n" -le "$iterations" ]; do b6_case "stress-$n" 0.1; n=$((n+1)); done
    b6_case default-control ""
    echo ""
    green "PASS: $PASS"
    [ "$FAIL" -gt 0 ] && red "FAIL: $FAIL" || green "FAIL: 0"
    [ "$FAIL" -eq 0 ]
    exit $?
fi

# ═══════════════════════════════════════════════════════════════════════════
echo "══ SUITE 1: callback lifecycle ══"

# ── DONE: close-then-publish ──
P="$(mkproj cb-done)"
pending_entry "$P" p-done coding codex-a "2026-06-29T00:00:00Z"
out=$(cb "$P" --slug p-done --stage coding --status DONE --worker codex-a --quiet 2>&1); rc=$?
CBF="$(wc_callback_path "$P" p-done coding)"
[ "$rc" -eq 0 ] && [ -f "$CBF" ] && ok "DONE publishes the exact callback file (exit 0)" || bad "DONE did not publish: rc=$rc out=$out"
[ "$(field "$CBF")" = "DONE" ] && ok "published callback carries status: DONE" || bad "callback status not DONE"
grep -q '^callback_id:' "$CBF" && grep -q "^incarnation: $WC_TEST_INCARNATION$" "$CBF" \
  && ok "callback carries callback_id+incarnation" || bad "callback identity fields missing"
grep -q 'response: null' "$P/.dev/proposals/p-done/forge-log.yml" \
    && bad "pending still open after DONE (close did not run)" \
    || ok "DONE closed the pending log entry first (response no longer null)"
ls "$P/.dev/forge-tmp/callbacks/".*.tmp.* >/dev/null 2>&1 \
    && bad "temp callback file leaked" || ok "no .tmp callback residue after atomic publish"

# ── failed close aborts publish (no matching open pending) ──
P="$(mkproj cb-abort)"
pending_entry "$P" p-ab qa codex-a "2026-06-29T00:00:00Z"   # pending is qa, we DONE 'coding'
out=$(cb "$P" --slug p-ab --stage coding --status DONE --worker codex-a --quiet 2>&1); rc=$?
[ "$rc" -ne 0 ] && ok "DONE with no matching open pending fails loud (nonzero)" || bad "failed close returned 0"
[ -z "$(find "$P/.dev/forge-tmp/callbacks" -maxdepth 1 -name 'p-ab-coding*.callback' -print -quit)" ] \
    && ok "aborted terminal close did NOT publish a callback" || bad "callback published despite failed close"
grep -q 'reason=terminal-close-failed' "$P/.dev/forge-tmp/orchestrator-events.log" 2>/dev/null \
    && ok "failed close emits ERROR terminal-close-failed event" || bad "no terminal-close-failed event"

# ── ERROR also closes-then-publishes ──
P="$(mkproj cb-err)"
pending_entry "$P" p-er coding codex-a "2026-06-29T00:00:00Z"
cb "$P" --slug p-er --stage coding --status ERROR --worker codex-a --quiet >/dev/null 2>&1
EF="$(wc_callback_path "$P" p-er coding)"
[ "$(field "$EF")" = "ERROR" ] && ok "ERROR publishes a terminal callback (status ERROR)" || bad "ERROR callback wrong"
grep -q 'response: null' "$P/.dev/proposals/p-er/forge-log.yml" \
    && bad "ERROR left the pending open" || ok "ERROR closed the pending log entry"

# ── BLOCKED keeps the pending OPEN ──
P="$(mkproj cb-blk)"
pending_entry "$P" p-blk coding codex-a "2026-06-29T00:00:00Z"
cb "$P" --slug p-blk --stage coding --status BLOCKED --worker codex-a --message "needs a human" --quiet >/dev/null 2>&1
BF="$(wc_callback_path "$P" p-blk coding)"
[ "$(field "$BF")" = "BLOCKED" ] && ok "BLOCKED publishes the callback (status BLOCKED)" || bad "BLOCKED callback wrong"
grep -q 'response: null' "$P/.dev/proposals/p-blk/forge-log.yml" \
    && ok "BLOCKED keeps the dispatch pending OPEN (response still null)" || bad "BLOCKED closed the pending"
grep -q '^callback_id:' "$BF" && ok "BLOCKED callback carries a callback_id" || bad "BLOCKED callback missing callback_id"

# ── callback-consume archives the BLOCKED callback ──
CBID=$(grep '^callback_id:' "$BF" | awk '{print $2}')
out=$(consume "$P" --slug p-blk --stage coding --status BLOCKED 2>&1); rc=$?
ARCH="$P/.dev/forge-tmp/callbacks/archive/p-blk-coding.${CBID}.callback"
{ [ "$rc" -eq 0 ] && [ ! -f "$BF" ] && [ -f "$ARCH" ]; } \
    && ok "consume archives the canonical BLOCKED callback (canonical gone → archive/)" \
    || bad "consume did not archive: rc=$rc out=$out"
[ "$(field "$ARCH")" = "BLOCKED" ] && ok "archived callback preserves status BLOCKED" || bad "archived status wrong"

# ── consume no-op when nothing to consume ──
out=$(consume "$P" --slug p-nope --stage coding --status BLOCKED 2>&1); rc=$?
{ [ "$rc" -eq 0 ] && echo "$out" | grep -q 'NOOP'; } \
    && ok "consume is a structured no-op when no canonical callback exists (exit 0)" \
    || bad "consume no-op wrong: rc=$rc out=$out"

# ── consume leaves a terminal (DONE) callback in place (status mismatch) ──
P="$(mkproj cb-mismatch)"
pending_entry "$P" p-mm coding codex-a "2026-06-29T00:00:00Z"
cb "$P" --slug p-mm --stage coding --status DONE --worker codex-a --quiet >/dev/null 2>&1
DF="$(wc_callback_path "$P" p-mm coding)"
out=$(consume "$P" --slug p-mm --stage coding --status BLOCKED 2>&1); rc=$?
{ [ "$rc" -eq 0 ] && [ -f "$DF" ] && echo "$out" | grep -q 'reason=status-mismatch'; } \
    && ok "consume preserves a terminal DONE callback as a structured no-op" \
    || bad "consume clobbered/mishandled a terminal callback: rc=$rc out=$out"

# ── consume rejects a terminal --status ──
out=$(consume "$P" --slug p-mm --stage coding --status DONE 2>&1); rc=$?
[ "$rc" -ne 0 ] && ok "consume refuses a terminal --status (only BLOCKED consumable)" || bad "consume accepted --status DONE"

# ═══════════════════════════════════════════════════════════════════════════
echo "══ SUITE 2: infra-lock mutex ══"

PROJ="$(mkproj il-proj)"
HOST="testhost"

# The one REAL throwaway tmux session — its (session_id, session_created) is the
# only identity `tmux list-sessions` will report as live.
tmux new-session -d -s "$TSESS" 2>/dev/null
A_SID=$(tmux display-message -t "$TSESS" -p '#{session_id}')
A_CREATED=$(tmux display-message -t "$TSESS" -p '#{session_created}')
[ -n "$A_SID" ] && [ -n "$A_CREATED" ] && ok "created a real throwaway tmux session for liveness ($A_SID)" \
    || bad "could not create/read throwaway tmux session"

# ── acquire on a free lock ──
out=$(il "$HOST" forge-A "$A_SID" "$A_CREATED" acquire --slug alpha --stage coding); rc=$?
{ [ "$rc" -eq 0 ] && echo "$out" | grep -q 'ACQUIRED.*action=acquired'; } \
    && ok "acquire on a FREE lock → ACQUIRED (action=acquired)" || bad "free acquire wrong: rc=$rc out=$out"
[ -f "$LOCKDIR/infra.holder" ] && ok "holder sidecar written under FORGE_INFRA_LOCK_DIR" || bad "no holder sidecar in anchor"

# ── status reports HELD_LIVE for the real live holder ──
out=$(il "$HOST" forge-A "$A_SID" "$A_CREATED" status)
echo "$out" | grep -q 'HELD live by alpha' && ok "status → HELD live for the real live holder" || bad "status not HELD_LIVE: $out"

# ── reentrancy (same session, same slug+stage) ──
out=$(il "$HOST" forge-A "$A_SID" "$A_CREATED" acquire --slug alpha --stage coding); rc=$?
{ [ "$rc" -eq 0 ] && echo "$out" | grep -q 'ALREADY_HELD'; } \
    && ok "re-acquire same session/slug/stage → ALREADY_HELD (reentrant, exit 0)" || bad "reentrancy wrong: rc=$rc out=$out"

# ── stage-update (same session/slug, new stage) ──
out=$(il "$HOST" forge-A "$A_SID" "$A_CREATED" acquire --slug alpha --stage qa); rc=$?
{ [ "$rc" -eq 0 ] && echo "$out" | grep -q 'action=stage_update'; } \
    && ok "same session/slug, new stage → ACQUIRED action=stage_update" || bad "stage-update wrong: rc=$rc out=$out"
il "$HOST" forge-A "$A_SID" "$A_CREATED" status | grep -q 'stage qa' \
    && ok "status reflects the updated stage (qa)" || bad "stage not updated in status"

# ── CONFLICT: same live session, different slug ──
out=$(il "$HOST" forge-A "$A_SID" "$A_CREATED" acquire --slug beta --stage coding 2>&1); rc=$?
{ [ "$rc" -eq 3 ] && echo "$out" | grep -q 'CONFLICT'; } \
    && ok "same live session, different slug → CONFLICT (exit 3)" || bad "conflict wrong: rc=$rc out=$out"

# ── a DIFFERENT session is blocked by the live holder, then times out ──
out=$(il "$HOST" forge-B sessB-notreal 222 acquire --slug beta --stage coding --timeout 0 --interval 1 2>&1); rc=$?
[ "$rc" -eq 2 ] && ok "live holder blocks a different session → TIMEOUT (exit 2)" || bad "expected timeout(2), got rc=$rc"
echo "$out" | grep -q 'liveness       = live' \
    && ok "timeout dump shows the holder is liveness=live" || bad "timeout dump missing live liveness: $out"

# ── release: wrong slug is a no-op (not the owner's slug) ──
out=$(il "$HOST" forge-A "$A_SID" "$A_CREATED" release --slug beta 2>&1)
echo "$out" | grep -q 'RELEASE_NOOP' && ok "release with a non-held slug → RELEASE_NOOP" || bad "wrong-slug release not noop: $out"

# ── release: a different session cannot release the holder ──
out=$(il "$HOST" forge-B sessB-notreal 222 release --slug alpha 2>&1)
echo "$out" | grep -q 'RELEASE_NOOP' && ok "foreign session release → RELEASE_NOOP (not owner)" || bad "not-owner release not noop: $out"

# ── full-key release succeeds even after a stage-update (release omits stage) ──
out=$(il "$HOST" forge-A "$A_SID" "$A_CREATED" release --slug alpha 2>&1)
echo "$out" | grep -q 'INFRA_LOCK: RELEASED' && ok "owner release → RELEASED (stage-updated holder releases cleanly)" || bad "owner release failed: $out"
il "$HOST" forge-A "$A_SID" "$A_CREATED" status | grep -q 'FREE' && ok "status → FREE after release" || bad "not free after release"

# ── idempotent release when free ──
out=$(il "$HOST" forge-A "$A_SID" "$A_CREATED" release --slug alpha 2>&1)
echo "$out" | grep -q 'RELEASE_NOOP.*not-held' && ok "release when already free → RELEASE_NOOP not-held (idempotent)" || bad "idempotent release wrong: $out"

# ── steal on a DEAD session holder ──
il "$HOST" forge-dead dead-sess-gone 333 acquire --slug gamma --stage coding >/dev/null 2>&1   # holder is not a live tmux session
out=$(il "$HOST" forge-B sessB-notreal 222 acquire --slug gamma --stage coding); rc=$?
{ [ "$rc" -eq 0 ] && echo "$out" | grep -q 'action=steal'; } \
    && ok "dead-session holder is stolen by the next acquirer (action=steal)" || bad "dead steal wrong: rc=$rc out=$out"
il "$HOST" forge-B sessB-notreal 222 release --slug gamma >/dev/null 2>&1

# ── steal on server-restart session-id reuse (same id, different creation time) ──
il "$HOST" forge-reuse "$A_SID" "$((A_CREATED-9999))" acquire --slug delta --stage coding >/dev/null 2>&1
out=$(il "$HOST" forge-B sessB-notreal 222 acquire --slug delta --stage coding); rc=$?
{ [ "$rc" -eq 0 ] && echo "$out" | grep -q 'action=steal'; } \
    && ok "id reuse with a different session_created → stolen (server-restart case)" || bad "id-reuse steal wrong: rc=$rc out=$out"
il "$HOST" forge-B sessB-notreal 222 release --slug delta >/dev/null 2>&1

# ── foreign-host holder: waits then times out; status shows foreign ──
il otherhost forge-far far-sid 444 acquire --slug epsilon --stage coding >/dev/null 2>&1
out=$(il "$HOST" forge-B sessB-notreal 222 acquire --slug epsilon --stage coding --timeout 0 2>&1); rc=$?
[ "$rc" -eq 2 ] && ok "foreign-host holder blocks then TIMEOUT (exit 2)" || bad "foreign wait wrong: rc=$rc"
echo "$out" | grep -q 'liveness       = foreign-host' && ok "timeout dump marks liveness=foreign-host" || bad "foreign liveness missing: $out"
il "$HOST" forge-B sessB-notreal 222 status | grep -q 'HELD foreign-host' && ok "status → HELD foreign-host" || bad "status not foreign"

# ── foreign holder release is a no-op; --force clears it ──
out=$(il "$HOST" forge-B sessB-notreal 222 release --slug epsilon 2>&1)
echo "$out" | grep -q 'RELEASE_NOOP' && ok "releasing a foreign-host holder → RELEASE_NOOP" || bad "foreign release not noop: $out"
out=$(il "$HOST" forge-B sessB-notreal 222 release --slug epsilon --force 2>&1)
echo "$out" | grep -q 'FORCE_RELEASED' && ok "release --force clears any holder → FORCE_RELEASED" || bad "force release failed: $out"
il "$HOST" forge-B sessB-notreal 222 status | grep -q 'FREE' && ok "status → FREE after force-release" || bad "not free after force"

# ── corrupt sidecar → ESCALATE, never a traceback ──
printf ': : not a mapping [unclosed\n' > "$LOCKDIR/infra.holder"
out=$(il "$HOST" forge-A "$A_SID" "$A_CREATED" status 2>&1)
{ echo "$out" | grep -q 'ESCALATE' && ! echo "$out" | grep -qi 'Traceback'; } \
    && ok "corrupt sidecar → status ESCALATE with no Python traceback" || bad "corrupt status wrong: $out"
out=$(il "$HOST" forge-A "$A_SID" "$A_CREATED" acquire --slug zeta --stage coding 2>&1); rc=$?
{ [ "$rc" -eq 4 ] && echo "$out" | grep -q 'ESCALATE' && ! echo "$out" | grep -qi 'Traceback'; } \
    && ok "corrupt sidecar → acquire ESCALATE (exit 4), no traceback" || bad "corrupt acquire wrong: rc=$rc out=$out"
rm -f "$LOCKDIR/infra.holder"

# ── injection-safe session_id (data only, never eval'd) ──
CANARY="$WORK/PWNED"; rm -f "$CANARY"
il "$HOST" forge-inj "\$(touch $CANARY)" 555 acquire --slug eta --stage coding >/dev/null 2>&1
[ ! -e "$CANARY" ] && ok "shell-metachar session_id is NOT evaluated (no canary file)" || bad "injection: canary created!"
grep -qF '$(touch' "$LOCKDIR/infra.holder" && ok "the metachar session_id is stored verbatim in the sidecar" || bad "session_id not stored literally"
il "$HOST" forge-inj "\$(touch $CANARY)" 555 release --slug eta >/dev/null 2>&1

# ── env knob: FORGE_INFRA_LOCK_TIMEOUT_S default is honored (no --timeout flag) ──
il otherhost forge-far far-sid 444 acquire --slug theta --stage coding >/dev/null 2>&1
rc=$(cd "$PROJ" && FORGE_INFRA_LOCK_DIR="$LOCKDIR" FORGE_INFRA_LOCK_TIMEOUT_S=0 \
     FORGE_LOCK_SELF_HOST="$HOST" FORGE_LOCK_SELF_SESSION_ID=sessB-notreal FORGE_LOCK_SELF_SESSION_CREATED=222 \
     "$BRIDGE" infra-lock acquire --slug theta --stage coding >/dev/null 2>&1; echo $?)
[ "$rc" -eq 2 ] && ok "FORGE_INFRA_LOCK_TIMEOUT_S=0 makes acquire time out immediately (env knob honored)" || bad "env timeout knob ignored: rc=$rc"
# --timeout flag overrides the (large) env ceiling
rc=$(cd "$PROJ" && FORGE_INFRA_LOCK_DIR="$LOCKDIR" FORGE_INFRA_LOCK_TIMEOUT_S=9999 \
     FORGE_LOCK_SELF_HOST="$HOST" FORGE_LOCK_SELF_SESSION_ID=sessB-notreal FORGE_LOCK_SELF_SESSION_CREATED=222 \
     "$BRIDGE" infra-lock acquire --slug theta --stage coding --timeout 0 >/dev/null 2>&1; echo $?)
[ "$rc" -eq 2 ] && ok "--timeout flag overrides the FORGE_INFRA_LOCK_TIMEOUT_S env ceiling" || bad "--timeout did not override env: rc=$rc"
il "$HOST" forge-B sessB-notreal 222 release --slug theta --force >/dev/null 2>&1

# ═══════════════════════════════════════════════════════════════════════════
echo "══ SUITE 3: acceptance ══"

# ── subcommands are wired into the dispatch + help ──
help=$("$BRIDGE" help 2>&1)
echo "$help" | grep -q 'infra-lock' && ok "help documents the infra-lock subcommand" || bad "help missing infra-lock"
echo "$help" | grep -q 'callback-consume' && ok "help documents callback-consume" || bad "help missing callback-consume"
echo "$help" | grep -q '1800' && ok "help states the 1800s default acquire ceiling" || bad "help missing 1800 default"
echo "$help" | grep -q '15s poll' && ok "help states the 15s default poll interval" || bad "help missing 15s poll"

# ── infra-lock is live end to end (fresh anchor → FREE) ──
FRESH="$WORK/freshlock"
out=$(cd "$PROJ" && FORGE_INFRA_LOCK_DIR="$FRESH" \
      FORGE_LOCK_SELF_HOST=h FORGE_LOCK_SELF_SESSION_ID=s FORGE_LOCK_SELF_SESSION_CREATED=1 \
      "$BRIDGE" infra-lock status 2>&1)
echo "$out" | grep -q 'FREE' && ok "infra-lock status on a fresh anchor → FREE (wired end-to-end)" || bad "fresh status wrong: $out"

# ── callback-consume is live end to end (no callback → NOOP exit 0) ──
P="$(mkproj acc-consume)"
out=$(consume "$P" --slug nothing --stage coding --status BLOCKED 2>&1); rc=$?
{ [ "$rc" -eq 0 ] && echo "$out" | grep -q 'NOOP'; } && ok "callback-consume wired (no callback → NOOP exit 0)" || bad "consume not wired: rc=$rc"

# ── argument validation ──
( cd "$PROJ" && FORGE_INFRA_LOCK_DIR="$LOCKDIR" FORGE_LOCK_SELF_HOST=h FORGE_LOCK_SELF_SESSION_ID=s FORGE_LOCK_SELF_SESSION_CREATED=1 \
  "$BRIDGE" infra-lock >/dev/null 2>&1 ) && bad "infra-lock with no action accepted" || ok "infra-lock with no action → usage error"
( cd "$PROJ" && FORGE_INFRA_LOCK_DIR="$LOCKDIR" FORGE_LOCK_SELF_HOST=h FORGE_LOCK_SELF_SESSION_ID=s FORGE_LOCK_SELF_SESSION_CREATED=1 \
  "$BRIDGE" infra-lock acquire --slug only >/dev/null 2>&1 ) && bad "acquire without --stage accepted" || ok "acquire without --stage → error"
( cd "$PROJ" && FORGE_INFRA_LOCK_DIR="$LOCKDIR" FORGE_LOCK_SELF_HOST=h FORGE_LOCK_SELF_SESSION_ID=s FORGE_LOCK_SELF_SESSION_CREATED=1 \
  "$BRIDGE" infra-lock release >/dev/null 2>&1 ) && bad "release without --slug accepted" || ok "release without --slug → error"
cb "$P" --slug x --stage coding --status BOGUS --worker codex-a --quiet >/dev/null 2>&1 \
    && bad "callback accepted an invalid --status" || ok "callback rejects an invalid --status"
consume "$P" --slug x >/dev/null 2>&1 && bad "callback-consume accepted a missing --stage" || ok "callback-consume requires --slug and --stage"

# ── merged docs/skill anchors (read-only greps against the repo as merged) ──
SK="$ROOT/skills/forge-orchestrator/SKILL.md"
grep -q 'Rule 23' "$SK" 2>/dev/null && ok "orchestrator SKILL.md carries Hard Rule 23 (terminality/lock)" || bad "Rule 23 absent from SKILL.md"
grep -q 'infra-lock acquire' "$SK" 2>/dev/null && ok "SKILL.md wraps infra stages with infra-lock acquire" || bad "SKILL.md has no infra-lock acquire wrapping"
grep -q 'callback-consume' "$SK" 2>/dev/null && ok "SKILL.md references callback-consume in the BLOCKED flow" || bad "SKILL.md missing callback-consume"
grep -qi 'Cross-Worktree Infra Lock' "$ROOT/docs/forge-technical-reference.md" 2>/dev/null \
    && ok "technical reference documents the Cross-Worktree Infra Lock" || bad "tech ref missing infra-lock section"
grep -qi 'Multi-Worktree Concurrency' "$ROOT/docs/forge-operator-guide.md" 2>/dev/null \
    && ok "operator guide documents Multi-Worktree Concurrency" || bad "operator guide missing concurrency section"

# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo "── recover ∘ infra-lock composition (final-plan Divergence 3 / R-6) ──"
# A HELD_LIVE holder for a session recovery's tmux seam calls dead must fail
# closed: the record is skipped/reported, never archived. Exercises the REAL
# `forge-bridge infra-lock status` output against the REAL `forge recover`
# parser in one process chain (no isolated stubs — the R-6 coupling test).
FORGE_CLI="$ROOT/bin/forge"
CR="$WORK/comp-root"; mkdir -p "$CR/.dev/attention"
tmux has-session -t "$TSESS" 2>/dev/null || tmux new-session -d -s "$TSESS" -c "$WORK" 2>/dev/null
CID=$(tmux list-sessions -F '#{session_name}|#{session_id}|#{session_created}' 2>/dev/null | awk -F'|' -v s="$TSESS" '$1==s{print $2"|"$3}')
if [ -n "$CID" ]; then
    CSID="${CID%%|*}"; CSCREATED="${CID##*|}"
    CANCHOR="$WORK/comp-anchor"; mkdir -p "$CANCHOR"
    FORGE_INFRA_LOCK_DIR="$CANCHOR" FORGE_LOCK_SELF_HOST="$(hostname)" \
      FORGE_LOCK_SELF_SESSION="$TSESS" FORGE_LOCK_SELF_SESSION_ID="$CSID" \
      FORGE_LOCK_SELF_SESSION_CREATED="$CSCREATED" \
      "$BRIDGE" infra-lock acquire --slug comp-x --stage coding >/dev/null 2>&1
    printf '{"schema":"cc-attention/1","event":"stop","session":"%s","emitted_at":"2026-07-10T10:00:00Z"}' "$TSESS" \
      > "$CR/.dev/attention/stop.$TSESS.json"
    printf '{"schema":"cc-attention/1","event":"stop","session":"trulydead","emitted_at":"2026-07-10T10:00:00Z"}' \
      > "$CR/.dev/attention/stop.trulydead.json"
    # recovery's tmux seam: EMPTY server view → both sessions look dead; only
    # the infra-lock corroboration can save $TSESS.
    FORGE_INFRA_LOCK_DIR="$CANCHOR" FORGE_LOCK_SELF_HOST="$(hostname)" \
      FORGE_LOCK_SELF_SESSION="$TSESS" FORGE_LOCK_SELF_SESSION_ID="$CSID" \
      FORGE_LOCK_SELF_SESSION_CREATED="$CSCREATED" \
      FORGE_BRIDGE_BIN="$BRIDGE" FORGE_RECOVER_TMUX_STATUS=no-server \
      FORGE_WATCH_TRIGGER=0 FORGE_RECOVER_VERIFY_RETRIES=1 FORGE_RECOVER_VERIFY_SLEEP=0 \
      "$FORGE_CLI" recover --root "$CR" --apply --yes >/dev/null 2>&1
    [ -f "$CR/.dev/attention/stop.$TSESS.json" ] \
      && ok "HELD_LIVE contradiction skips the held session's record" \
      || bad "record archived despite live infra-lock holder"
    [ ! -f "$CR/.dev/attention/stop.trulydead.json" ] \
      && ok "uncontradicted dead record still archived" \
      || bad "dead record not archived"
    grep -q "infra-lock HELD_LIVE contradicts" "$CR"/.dev/attention/archive/*/MANIFEST.json 2>/dev/null \
      && ok "contradiction skip recorded in manifest" \
      || bad "no contradiction entry in manifest"
    FORGE_INFRA_LOCK_DIR="$CANCHOR" FORGE_LOCK_SELF_HOST="$(hostname)" \
      FORGE_LOCK_SELF_SESSION="$TSESS" FORGE_LOCK_SELF_SESSION_ID="$CSID" \
      FORGE_LOCK_SELF_SESSION_CREATED="$CSCREATED" \
      "$BRIDGE" infra-lock release --slug comp-x >/dev/null 2>&1
else
    bad "could not create throwaway tmux session for composition test"
fi

# ═══════════════════════════════════════════════════════════════════════════
echo "══ SUITE 4: blocked-item lifecycle (C1 — identity/origin contract) ══"
# NOTE (C1 scope): B1(filename)/B2(legacy-resolve+foreign-session) require the
# FORGE_TMUX_LIST identity seam to make _resolve_session return a name; B4
# (_callback_ask_origin) and B5 (_insert_parked_fields_by_ts) have no CLI driver
# until the guard/park verbs land — they are authored in C5/C3 respectively. The
# C1-feasible slice below covers P1a (origin contract) and P1b (session/origin
# headers + yaml round-trip), the headline identity surface.

# ── B3: --origin contract (defined value set + internal-only 'ask') ──
P="$(mkproj bi-origin)"
pending_entry "$P" bi-o coding codex-a "2026-06-29T00:00:00Z"
out=$(cb "$P" --slug bi-o --stage coding --status BLOCKED --origin foo --worker codex-a --quiet 2>&1); rc=$?
[ "$rc" -ne 0 ] && ok "callback --origin foo rejected (defined value set)" || bad "--origin foo accepted (rc=$rc)"
pending_entry "$P" bi-o2 coding codex-a "2026-06-29T00:00:00Z"
out=$(cb "$P" --slug bi-o2 --stage coding --status BLOCKED --origin ask --worker codex-a --quiet 2>&1); rc=$?
[ "$rc" -ne 0 ] && ok "callback --origin ask without internal token rejected" || bad "--origin ask accepted without token"
P3="$(mkproj bi-origin3)"
pending_entry "$P3" bi-o3 coding codex-a "2026-06-29T00:00:00Z"
out=$(FORGE_INTERNAL_ASK_ORIGIN=1 cb "$P3" --slug bi-o3 --stage coding --status BLOCKED --origin ask --worker codex-a --message "q?" --quiet 2>&1); rc=$?
OF="$(wc_callback_path "$P3" bi-o3 coding)"
{ [ "$rc" -eq 0 ] && [ -f "$OF" ] && grep -q '^origin: ask' "$OF"; } && ok "internal ask origin stamps 'origin: ask'" || bad "internal ask origin not stamped (rc=$rc out=$out)"

# ── B1(headers): P1b session:/origin: scalar headers present + yaml-parseable ──
P="$(mkproj bi-hdr)"
pending_entry "$P" bi-h coding codex-a "2026-06-29T00:00:00Z"
cb "$P" --slug bi-h --stage coding --status BLOCKED --worker codex-a --message "needs a human" --quiet >/dev/null 2>&1
HF="$(wc_callback_path "$P" bi-h coding)"
grep -q '^session:' "$HF" && ok "callback carries a session: header (P1b)" || bad "no session: header"
grep -q '^origin:'  "$HF" && ok "callback carries an origin: header (P1b)"  || bad "no origin: header"
grep -q '^callback_id:' "$HF" && ok "callback carries callback_id (identity)" || bad "no callback_id"
python3 -c "import yaml,sys; d=yaml.safe_load(open('$HF')); sys.exit(0 if isinstance(d,dict) and 'session' in d and 'origin' in d else 1)" \
    && ok "callback body round-trips yaml.safe_load with session/origin keys" || bad "callback not yaml-parseable with new headers"

echo "══ SUITE 4: blocked-item lifecycle ══"
# park internally calls `cmd_infra_lock release`, which needs a resolvable lock anchor
# (the seat runs inside a git repo; the hermetic test projects are not repos). Point it
# at a dedicated dir — the same FORGE_INFRA_LOCK_DIR seam the il() helper uses — so
# release resolves to a RELEASE_NOOP not-held instead of erroring on git-dir resolution.
export FORGE_INFRA_LOCK_DIR="$WORK/suite4-locks"; mkdir -p "$FORGE_INFRA_LOCK_DIR"

# B1: new production writer emits exact session+incarnation identity.
P="$(mkproj b1)"; pending_entry "$P" p1 coding codex-a "2026-06-29T00:00:00Z"
cbS "$P" forge-1 --slug p1 --stage coding --status BLOCKED --worker codex-a --message "x" >/dev/null 2>&1
CBF="$(wc_callback_path "$P" p1 coding forge-1 "$WC_TEST_INCARNATION")"
[ -f "$CBF" ] && ok "B1 exact filename" || bad "B1 filename not exact"
grep -q '^session: forge-1' "$CBF" && grep -q "^incarnation: $WC_TEST_INCARNATION$" "$CBF" \
  && grep -q '^selected_pending_timestamp:' "$CBF" && ok "B1 exact ownership headers" || bad "B1 headers missing"

# B2: all-legacy actor/callback/pending remains compatible only without incarnation.
P="$(mkproj b2)"; mkdir -p "$P/.dev/proposals/p2"
cat > "$P/.dev/proposals/p2/forge-log.yml" <<'EOF'
pipeline: p2
entries:
  - timestamp: "2026-06-29T00:00:00Z"
    stage: coding
    to: codex-a
    session: forge-legacy
    response: null
EOF
LEG="$P/.dev/forge-tmp/callbacks/p2-coding.forge-legacy.callback"
cat > "$LEG" <<'EOF'
slug: p2
stage: coding
status: BLOCKED
worker: codex-a
session: forge-legacy
callback_id: legacy-b2
timestamp: 2026-06-29T00:00:01Z
message: old producer
EOF
out=$(briL "$P" forge-legacy callback-consume --slug p2 --stage coding --status BLOCKED 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ -f "$P/.dev/forge-tmp/callbacks/archive/p2-coding.legacy-b2.callback" ] \
  && ok "B2 all-legacy triple remains consumable" || bad "B2 all-legacy compatibility failed: $out"

# A different exact session cannot consume the exact owner callback.
P="$(mkproj b2-foreign)"; pending_entry "$P" p2f coding codex-a "2026-06-29T00:00:00Z"
cbS "$P" forge-1 --slug p2f --stage coding --status BLOCKED --worker codex-a --message y >/dev/null 2>&1
FOREIGN="$(wc_callback_path "$P" p2f coding forge-1 "$WC_TEST_INCARNATION")"
before=$(shasum -a 256 "$FOREIGN" | awk '{print $1}')
out=$(briS "$P" forge-2 callback-consume --slug p2f --stage coding --status BLOCKED 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ "$before" = "$(shasum -a 256 "$FOREIGN" | awk '{print $1}')" ] \
  && ok "B2 foreign exact consume NOOP" || bad "B2 foreign consume mutated owner file"

# B4 [D3/P14] precise ask matcher via blocked-audit
P="$(mkproj b4)"; mkdir -p "$P/.dev/attention"
pending_entry "$P" p4 coding codex-a "2026-06-29T00:00:00Z"
cb "$P" --slug p4 --stage coding --status BLOCKED --worker codex-a --message x >/dev/null 2>&1  # worker, no ask
out=$( cd "$P" && "$BRIDGE" blocked-audit --json 2>&1 )
echo "$out" | grep -q '"origin": "worker"' && ok "B4 worker cb → origin worker (no ask record)" || bad "B4 misclassified worker"
printf '{"event":"ask","slug":"p4","stage":"coding","mode":"stage"}' > "$P/.dev/attention/ask-1.json"
out=$( cd "$P" && "$BRIDGE" blocked-audit --json 2>&1 )
echo "$out" | grep -q '"origin": "ask"' && ok "B4 matching live ask → origin ask" || bad "B4 ask not inferred"
# stale/different-stage ask must NOT match
rm -f "$P/.dev/attention/ask-1.json"; printf '{"event":"ask","slug":"p4","stage":"qa","mode":"stage"}' > "$P/.dev/attention/ask-2.json"
out=$( cd "$P" && "$BRIDGE" blocked-audit --json 2>&1 )
echo "$out" | grep -q '"origin": "worker"' && ok "B4 different-stage ask does not match" || bad "B4 stale ask matched"

# B5 [D4] insert-parked-fields via park: newest of 2 open entries + WARN + round-trip
P="$(mkproj b5)"
mkdir -p "$P/.dev/proposals/p5"
cat > "$P/.dev/proposals/p5/forge-log.yml" <<'EOF'
pipeline: p5
entries:
  - timestamp: "2026-06-29T00:00:00Z"
    stage: coding
    to: codex-a
    response: null
  - timestamp: "2026-06-29T01:00:00Z"
    stage: coding
    to: codex-a
    response: null
EOF
python3 - "$P/.dev/proposals/p5/forge-log.yml" "$WC_TEST_SESSION" "$WC_TEST_INCARNATION" <<'PY'
import sys
path,session,incarnation=sys.argv[1:4]
raw=open(path).read()
raw=raw.replace('    to: codex-a\n    response: null', '    to: codex-a\n    session: '+session+'\n    incarnation: '+incarnation+'\n    response: null')
open(path,'w').write(raw)
PY
before=$(shasum -a 256 "$P/.dev/proposals/p5/forge-log.yml" | awk '{print $1}')
out=$(cb "$P" --slug p5 --stage coding --status BLOCKED --worker codex-a --message x 2>&1); rc=$?
after=$(shasum -a 256 "$P/.dev/proposals/p5/forge-log.yml" | awk '{print $1}')
[ "$rc" -ne 0 ] && echo "$out" | grep -q 'CALLBACK_IDENTITY_AMBIGUOUS' \
  && [ "$before" = "$after" ] && [ -z "$(find "$P/.dev/forge-tmp/callbacks" -name 'p5-coding*.callback' -print -quit)" ] \
  && ok "B5 duplicate exact pending publication fails closed" || bad "B5 duplicate pending was selected"

# B6 [D5] deterministic lifecycle-lock contention + distinct-key non-contention.
b6_case full-suite ""

# B7 park basic + re-park idempotent
P="$(mkproj b7)"; pending_entry "$P" p-pk coding codex-a "2026-06-29T00:00:00Z"
cb "$P" --slug p-pk --stage coding --status BLOCKED --worker codex-a --message boom >/dev/null 2>&1
out=$( cd "$P" && "$BRIDGE" park --slug p-pk --stage coding --reason "out of scope" 2>&1 ); rc=$?
CBF=$(ls "$P"/.dev/forge-tmp/callbacks/p-pk-coding*.callback 2>/dev/null | head -1)
[ "$(field "$CBF")" = "PARKED" ] && ok "B7 callback flipped to PARKED" || bad "B7 not PARKED: $out"
grep -q 'parked_at:' "$P/.dev/proposals/p-pk/forge-log.yml" && ok "B7 parked_at on entry" || bad "B7 no parked_at"
grep -q 'response: null' "$P/.dev/proposals/p-pk/forge-log.yml" && ok "B7 pending stays open" || bad "B7 pending closed"
out=$( cd "$P" && "$BRIDGE" park --slug p-pk --stage coding --reason again 2>&1 ); rc=$?
[ "$rc" -eq 0 ] && echo "$out" | grep -q 'PARK NOOP' && ok "B7 re-park idempotent NOOP" || bad "B7 re-park not NOOP"
# ask-origin refused; no-callback NOOP; no-pending loud fail
P2="$(mkproj b7b)"; pending_entry "$P2" p-ask coding codex-a "2026-06-29T00:00:00Z"
mkdir -p "$P2/.dev/attention"; cb "$P2" --slug p-ask --stage coding --status BLOCKED --worker codex-a --message x >/dev/null 2>&1
printf '{"event":"ask","slug":"p-ask","stage":"coding","mode":"stage"}' > "$P2/.dev/attention/ask-1.json"
out=$( cd "$P2" && "$BRIDGE" park --slug p-ask --stage coding --reason no 2>&1 ); rc=$?
[ "$rc" -ne 0 ] && echo "$out" | grep -qi 'REFUSED' && ok "B7 ask-origin park refused" || bad "B7 ask park not refused"
P3="$(mkproj b7c)"; pending_entry "$P3" p-nc coding codex-a "2026-06-29T00:00:00Z"
out=$( cd "$P3" && "$BRIDGE" park --slug p-nc --stage coding --reason x 2>&1 ); rc=$?
[ "$rc" -eq 0 ] && echo "$out" | grep -q 'PARK NOOP' && ok "B7 no-callback NOOP exit 0" || bad "B7 no-callback not NOOP"

# B8 lock_release_state classes (reasoning-tier not-held → OK)
P="$(mkproj b8)"; pending_entry "$P" p-rt review claude-opus "2026-06-29T00:00:00Z"
cb "$P" --slug p-rt --stage review --status BLOCKED --worker claude-opus --message x >/dev/null 2>&1
out=$( cd "$P" && "$BRIDGE" park --slug p-rt --stage review --reason later 2>&1 ); rc=$?
[ "$rc" -eq 0 ] && echo "$out" | grep -Eq 'lock_release_state=(RELEASE_NOOP_NOT_HELD|RELEASED)' \
  && ok "B8 not-held/released → PARK OK exit 0" || bad "B8 reasoning-tier park wrong: rc=$rc $out"

# B9 [D6] concurrency: park vs consume — revalidation refuses the loser
P="$(mkproj b9)"; pending_entry "$P" p9 coding codex-a "2026-06-29T00:00:00Z"
cb "$P" --slug p9 --stage coding --status BLOCKED --worker codex-a --message x >/dev/null 2>&1
# Hold the lock, consume+archive the callback under it, release; then park must revalidate-fail
LK="$(lifelock_path "$P" p9 coding)"; mkdir -p "$(dirname "$LK")"
( cd "$P" && "$BRIDGE" callback-consume --slug p9 --stage coding --status BLOCKED >/dev/null 2>&1 )
out=$( cd "$P" && "$BRIDGE" park --slug p9 --stage coding --reason late 2>&1 ); rc=$?
[ "$rc" -ne 0 ] && ok "B9 park after consume → NOOP/refusal (no lost update)" || { echo "$out" | grep -q 'PARK NOOP' && ok "B9 park NOOP after consume" || bad "B9 park raced a consumed callback"; }

# B10 [D1] two sessions, one root, identical slug/stage → isolation
P="$(mkproj b10)"
parked_pending "$P" pX coding codex-a "2026-06-29T00:00:00Z" "sA" forge-1
# append a second open entry for session forge-2 at the same slug/stage
{ echo "  - timestamp: \"2026-06-29T02:00:00Z\""; echo "    stage: coding"; echo "    to: codex-b"; echo "    session: forge-2"; echo "    response: null"; } >> "$P/.dev/proposals/pX/forge-log.yml"
cbS "$P" forge-1 --slug pX --stage coding --status BLOCKED --worker codex-a --message a >/dev/null 2>&1
cbS "$P" forge-2 --slug pX --stage coding --status BLOCKED --worker codex-b --message b >/dev/null 2>&1
briS "$P" forge-2 park --slug pX --stage coding --reason "B parks" >/dev/null 2>&1
# session-1's callback must remain BLOCKED (untouched)
[ "$(field "$(wc_callback_path "$P" pX coding forge-1 "$WC_TEST_INCARNATION")")" = "BLOCKED" ] && ok "B10 session-A callback untouched by session-B park" || bad "B10 cross-session mutation"
[ "$(field "$(wc_callback_path "$P" pX coding forge-2 "$WC_TEST_INCARNATION")")" = "PARKED" ] && ok "B10 session-B callback parked" || bad "B10 session-B not parked"

# B11 resolve closes pending + callback GONE + status ⏸ at 3 sites
P="$(mkproj b11)"; pending_entry "$P" p11 coding codex-a "2026-06-29T00:00:00Z"
cb "$P" --slug p11 --stage coding --status BLOCKED --worker codex-a --message x >/dev/null 2>&1
( cd "$P" && "$BRIDGE" park --slug p11 --stage coding --reason r >/dev/null 2>&1 )
out=$( cd "$P" && "$BRIDGE" park --resolve --slug p11 --stage coding --note "done here" 2>&1 ); rc=$?
grep -q 'FORGE_PARK_RESOLVED' "$P/.dev/proposals/p11/forge-log.yml" && ok "B11 pending closed FORGE_PARK_RESOLVED" || bad "B11 pending not resolved"
[ -z "$(ls "$P"/.dev/forge-tmp/callbacks/p11-coding*.callback 2>/dev/null)" ] && ok "B11 PARKED callback archived (gone from callbacks/)" || bad "B11 callback still live"

# B14 [P16] cmd_emit COMPLETE auto-qualifier + guard fail-safe on a malformed sibling log.
# emit is ungated (no live session), so it exercises _completion_unresolved →
# _unresolved_blocked_items hermetically. The malformed pbad/forge-log.yml MUST be skipped
# (yaml.safe_load wrapped in try/except), never abort the count with a traceback.
P="$(mkproj b14)"
mkdir -p "$P/.dev/proposals/p14"
{ echo "pipeline: p14"; echo "entries:"; echo "  - timestamp: \"2026-06-29T00:00:00Z\"";
  echo "    stage: coding"; echo "    to: codex-a"; echo "    parked_at: 2026-06-29T00:00:00Z";
  echo "    parked_reason: \"parked\""; echo "    uncommitted: false"; echo "    response: null"; } > "$P/.dev/proposals/p14/forge-log.yml"
mkdir -p "$P/.dev/proposals/pbad"
printf 'pipeline: pbad\nentries:\n  - timestamp: "x\n    stage: [unterminated\n  : : {{{\n' > "$P/.dev/proposals/pbad/forge-log.yml"
out=$( cd "$P" && FORGE_WORKER_HYGIENE_MODE=observe "$BRIDGE" emit COMPLETE --slug p14 2>&1 ); rc=$?
echo "$out" | grep -qi 'Traceback' && bad "B14 guard tracebacked on a malformed log" || ok "B14 guard fail-safe on malformed log (no traceback)"
grep -q 'qualifier=incomplete parked=1' "$P/.dev/forge-tmp/orchestrator-events.log" 2>/dev/null \
  && ok "B14 cmd_emit COMPLETE auto-qualified parked=1" || bad "B14 no qualifier: $out $(cat "$P/.dev/forge-tmp/orchestrator-events.log" 2>/dev/null)"

# T-HYG-EMIT-DELEGATE-ENFORCE (worker-context-hygiene §9b companion): the SAME command under
# enforce is delegated — with no exact verify decision it refuses and appends nothing new.
ev_before=$(grep -c '^COMPLETE: ' "$P/.dev/forge-tmp/orchestrator-events.log" 2>/dev/null | tr -d ' ')
out=$( cd "$P" && FORGE_WATCH_TRIGGER=0 FORGE_WORKER_HYGIENE_MODE=enforce "$BRIDGE" emit COMPLETE --slug p14 2>&1 ); rc=$?
ev_after=$(grep -c '^COMPLETE: ' "$P/.dev/forge-tmp/orchestrator-events.log" 2>/dev/null | tr -d ' ')
if [ "$rc" -ne 0 ] && echo "$out" | grep -q 'no exact verify decision' && [ "$ev_before" = "$ev_after" ]; then
  ok "T-HYG-EMIT-DELEGATE-ENFORCE enforce refuses the bare COMPLETE (B14 observe behavior preserved)"
else
  bad "T-HYG-EMIT-DELEGATE-ENFORCE rc=$rc out=$out"
fi

# B12/B13/B15/B17/B18 execute end to end in tests/forge-bridge/run.sh:
#   T-GUARD-B12-CROSS-SLUG, T-GUARD-B12-AFTER-PARK,
#   T-GUARD-B12-ASK-CONTROL, T-GUARD-B12-INFLIGHT-CONTROL,
#   T-GUARD-B12-PARKED-ADVANCE, T-GUARD-B13-FORCE-MATRIX,
#   T-GUARD-B13-BYPASS-ONE-SHOT, T-GUARD-B15-SUPERSEDE-SUCCESS,
#   T-GUARD-B15-SUPERSEDE-CLOSE-FAILURE, T-GUARD-B17-INTERNAL-DELIVERY,
#   T-GUARD-B18-FORCE-FILTER, T-GUARD-B18-OWN-CONTINUE,
#   T-GUARD-FIXTURE-HYGIENE-FINAL.

# B16 crash-safety: parked record + BLOCKED callback coexist → re-park self-heals
P="$(mkproj b16)"; parked_pending "$P" p16 coding codex-a "2026-06-29T00:00:00Z" "parked" ""
cb "$P" --slug p16 --stage coding --status BLOCKED --worker codex-a --message x >/dev/null 2>&1  # record present, callback still BLOCKED
out=$( cd "$P" && "$BRIDGE" park --slug p16 --stage coding --reason r 2>&1 ); rc=$?
[ "$(field "$(ls "$P"/.dev/forge-tmp/callbacks/p16-coding*.callback|head -1)")" = "PARKED" ] && ok "B16 re-park self-heals BLOCKED→PARKED" || bad "B16 re-park did not heal"

# M1 migration classifications + zero mutations
P="$(mkproj m1)"; mkdir -p "$P/.dev/attention"
pending_entry "$P" pm coding codex-a "2026-06-29T00:00:00Z"
cb "$P" --slug pm --stage coding --status BLOCKED --worker codex-a --message x >/dev/null 2>&1        # legacy worker
printf '{"event":"ask","slug":"pa","stage":"qa","mode":"stage"}' > "$P/.dev/attention/ask-1.json"
mkdir -p "$P/.dev/proposals/pa"; printf 'slug: pa\nstage: qa\nstatus: BLOCKED\nmessage: |\n  a\n' > "$P/.dev/forge-tmp/callbacks/pa-qa.callback"
printf 'entries:\n  - timestamp: "t1"\n    stage: qa\n    response: null\n' > "$P/.dev/proposals/pa/forge-log.yml"
before=$(cd "$P" && find .dev -type f | sort | xargs shasum 2>/dev/null | shasum)
out=$( cd "$P" && "$BRIDGE" blocked-audit --json 2>&1 )
after=$(cd "$P" && find .dev -type f | sort | xargs shasum 2>/dev/null | shasum)
echo "$out" | grep -q '"origin": "worker"' && echo "$out" | grep -q '"origin": "ask"' && ok "M1 worker+ask classified" || bad "M1 classification wrong"
[ "$before" = "$after" ] && ok "M1 blocked-audit mutated nothing" || bad "M1 mutated the tree"

# B-acc help documents park+blocked-audit + empty --allow-blocked reject
# Capture-then-grep (harness idiom, cf. the infra-lock help block above): a bare
# `"$BRIDGE" help | grep -q` trips SIGPIPE under `set -o pipefail` (grep -q exits on
# the early match while help is still emitting), failing the pipeline spuriously.
bahelp=$("$BRIDGE" help 2>&1)
echo "$bahelp" | grep -q 'park' && echo "$bahelp" | grep -q 'blocked-audit' && ok "B-acc help documents park+blocked-audit" || bad "B-acc help missing"
# empty --allow-blocked rejected at arg-parse (before any identity/tmux need)
P="$(mkproj bacc)"; pending_entry "$P" p-b coding codex-a "2026-06-29T00:00:00Z"
out=$( cd "$P" && env -u TMUX FORGE_WORKER_HYGIENE_MODE=observe "$BRIDGE" dispatch --slug p-o --stage coding --worker codex-b --allow-blocked 2>&1 ); rc=$?
[ "$rc" -ne 0 ] && echo "$out" | grep -qi 'requires a non-empty reason' && ok "B-acc empty --allow-blocked rejected" || bad "B-acc empty reason accepted"

# P1-E writer freeze + archive preservation.
SCHEMA="$ROOT/orchestrator/schemas/callback.yml"
python3 - "$SCHEMA" <<'PY' && ok "E1 schema validates three shapes and rejects boundary negatives" || bad "E1 schema"
import copy,datetime,re,sys,yaml
d=yaml.safe_load(open(sys.argv[1])); assert len(d['oneOf'])==3
assert 'selected_pending_timestamp' in d['properties']
assert d['properties']['timestamp']['description'].startswith('Callback publication')
base=dict(slug='e1',stage='coding',status='BLOCKED',worker='codex-a',callback_id='id-1',
          timestamp='2026-07-14T00:00:00Z',message='m')
positives=[dict(base,session='s',incarnation='7'),dict(base,session='s'),dict(base)]
negatives=[]
x=copy.deepcopy(base); x.pop('worker'); negatives.append(x)
x=dict(base,unknown_future='x'); negatives.append(x)
x=dict(base,session='',incarnation='7'); negatives.append(x)
x=dict(base,timestamp='2026-99-99T00:00:00Z'); negatives.append(x)
known=set(d['properties']); required=set(d['required'])
def semantic_timestamp(v):
    ts=v.get('timestamp')
    if not isinstance(ts,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',ts): return False
    try: datetime.datetime.strptime(ts,'%Y-%m-%dT%H:%M:%SZ')
    except ValueError: return False
    return True
def semantic_shape(v):
    s=str(v.get('session') or ''); i=str(v.get('incarnation') or '')
    return bool((s and i) or (s and not i) or (not s and not i))
try:
    from jsonschema import Draft202012Validator,FormatChecker
except ImportError:
    # The repository has no Python dependency manifest. Exercise the exact
    # contract dependency-free; environments that provide jsonschema also run
    # the authoritative Draft 2020-12 engine below.
    def valid(v):
        if set(v)-known or not required.issubset(v): return False
        return semantic_timestamp(v) and semantic_shape(v)
else:
    validator=Draft202012Validator(d,format_checker=FormatChecker())
    def valid(v): return validator.is_valid(v) and semantic_timestamp(v) and semantic_shape(v)
assert all(map(valid,positives)) and not any(map(valid,negatives))
PY
# The resolver body is a hard compatibility surface: compare it byte-for-byte
# with the certified floor, including comments and its four-argument signature.
python3 - "$ROOT" <<'PY' && ok "E3 four-argument resolver is byte-identical to certified floor" || bad "E3 resolver drift"
import hashlib,pathlib,sys
new=pathlib.Path(sys.argv[1],'bin/forge-bridge').read_text()
def block(s):
    start=s.index('# Resolve the callback file for (slug,stage) for the ACTING session.')
    end=s.index('\n}',s.index('_resolve_callback_file()',start))+2
    return s[start:end]
# SHA-256 of the exact resolver/comment block at certified floor a558c52b.
assert hashlib.sha256(block(new).encode()).hexdigest()=='e3aa76d6b4c9fc87bfc8de0c30ffd96194ac3013211b4c31838d143e05c67b50'
PY
P="$(mkproj p1wc-writer)"; pending_entry "$P" p1wc-writer coding codex-a "2026-07-14T00:00:00Z"
cbS "$P" forge-p1e --slug p1wc-writer --stage coding --status BLOCKED --worker codex-a --message exact >/dev/null 2>&1
WF="$(wc_callback_path "$P" p1wc-writer coding forge-p1e "$WC_TEST_INCARNATION")"
[ -f "$WF" ] && grep -q '^session: forge-p1e$' "$WF" \
  && grep -q "^incarnation: $WC_TEST_INCARNATION$" "$WF" \
  && grep -q '^selected_pending_timestamp: "2026-07-14T00:00:00Z"$' "$WF" \
  && ok "E8 writer emits exact callback identity" || bad "E8 writer exact floor"
WID=$(sed -n 's/^callback_id: //p' "$WF"); BEFORE=$(shasum -a 256 "$WF" | awk '{print $1}')
briS "$P" forge-p1e callback-consume --slug p1wc-writer --stage coding --status BLOCKED >/dev/null 2>&1
AF="$P/.dev/forge-tmp/callbacks/archive/p1wc-writer-coding.${WID}.callback"
AFTER=$(shasum -a 256 "$AF" | awk '{print $1}')
[ "$BEFORE" = "$AFTER" ] && grep -q "^incarnation: $WC_TEST_INCARNATION$" "$AF" \
  && ok "E4 archive move preserves every callback byte/header" || bad "E4 archive preservation"
grep -q 'lifecycle-${slug}--${stage}.lock' "$BRIDGE" \
  && ok "E3 physical lifecycle mutex key unchanged" || bad "E3 mutex key changed"

# Ordinary and nested mutation callers remain on the unchanged four-argument
# resolver. A short ceiling makes any accidental second-fd self-conflict loud.
P="$(mkproj p1e-lock-callers)"; pending_entry "$P" p1e-ordinary coding codex-a "2026-07-14T00:01:00Z"
cb "$P" --slug p1e-ordinary --stage coding --status BLOCKED --worker codex-a --message ordinary >/dev/null 2>&1
out=$(cd "$P" && FORGE_LIFECYCLE_LOCK_WAIT_S=0.2 "$BRIDGE" callback-consume --slug p1e-ordinary --stage coding --status BLOCKED 2>&1); rc=$?
[ "$rc" -eq 0 ] && ! printf '%s' "$out" | grep -q 'LIFECYCLE_LOCK: busy' \
  && ok "E3 ordinary consume has no nested-lock regression" || bad "E3 ordinary consume lock regression: $out"
pending_entry "$P" p1e-nested coding codex-a "2026-07-14T00:02:00Z"
cb "$P" --slug p1e-nested --stage coding --status BLOCKED --worker codex-a --message nested >/dev/null 2>&1
(cd "$P" && "$BRIDGE" park --slug p1e-nested --stage coding --reason nested >/dev/null 2>&1)
out=$(cd "$P" && FORGE_LIFECYCLE_LOCK_WAIT_S=0.2 "$BRIDGE" park --resolve --slug p1e-nested --stage coding --note done 2>&1); rc=$?
[ "$rc" -eq 0 ] && ! printf '%s' "$out" | grep -q 'LIFECYCLE_LOCK: busy' \
  && ok "E3 nested park-resolve consume reuses caller lock" || bad "E3 nested --_no_lock regression: $out"

# P1-WC makes the exact current callback mutable under its matching lifecycle lock.
P="$(mkproj p1wc-exact-current)"
mkdir -p "$P/.dev/proposals/p1wc-exact-current"
cat > "$P/.dev/proposals/p1wc-exact-current/forge-log.yml" <<'EOF'
pipeline: p1wc-exact-current
entries:
  - timestamp: "2026-07-14T00:03:00Z"
    stage: coding
    to: codex-a
    session: forge-p1e
    incarnation: 777
    response: null
EOF
EF="$P/.dev/forge-tmp/callbacks/p1wc-exact-current-coding.forge-p1e.777.callback"
cat > "$EF" <<'EOF'
slug: p1wc-exact-current
stage: coding
status: BLOCKED
worker: codex-a
session: forge-p1e
incarnation: 777
callback_id: exact-current
timestamp: 2026-07-14T00:03:01Z
selected_pending_timestamp: "2026-07-14T00:03:00Z"
message: exact current mutator fixture
EOF
EB=$(shasum -a 256 "$EF" | awk '{print $1}')
out=$(FORGE_LIFECYCLE_LOCK_WAIT_S=0.2 briS "$P" forge-p1e callback-consume --slug p1wc-exact-current --stage coding --status BLOCKED 2>&1); rc=$?
AF="$P/.dev/forge-tmp/callbacks/archive/p1wc-exact-current-coding.exact-current.callback"
[ "$rc" -eq 0 ] && [ -f "$AF" ] && [ "$EB" = "$(shasum -a 256 "$AF" | awk '{print $1}')" ] \
  && ok "E8 exact current callback is byte-preserving mutation input" || bad "E8 exact current mutation failed: $out"

wc_artifact_fingerprint() { # <project> <slug>
    python3 - "$1" "$2" <<'PY'
import hashlib,os,sys
root,slug=sys.argv[1:3]; rows=[]
log=os.path.join(root,'.dev','proposals',slug,'forge-log.yml')
paths=[log] if os.path.isfile(log) else []
cb=os.path.join(root,'.dev','forge-tmp','callbacks')
for base,_,files in os.walk(cb):
    for name in files:
        if name.startswith(slug+'-'): paths.append(os.path.join(base,name))
for path in sorted(paths):
    rows.append(os.path.relpath(path,root)+':'+hashlib.sha256(open(path,'rb').read()).hexdigest())
print('|'.join(rows))
PY
}

# PARKED log authority permits resolution when the owned parked pending has no callback.
P="$(mkproj p1wc-log-authority)"; mkdir -p "$P/.dev/proposals/logonly"
cat > "$P/.dev/proposals/logonly/forge-log.yml" <<EOF
pipeline: logonly
entries:
  - timestamp: "2026-07-14T00:03:30Z"
    stage: coding
    to: codex-a
    session: $WC_TEST_SESSION
    incarnation: $WC_TEST_INCARNATION
    parked_at: "2026-07-14T00:03:31Z"
    parked_reason: "log is authoritative"
    response: null
EOF
out="$(briS "$P" "$WC_TEST_SESSION" park --resolve --slug logonly --stage coding --note done 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && grep -q 'response: "FORGE_PARK_RESOLVED:' "$P/.dev/proposals/logonly/forge-log.yml" \
  && ok "WC PARKED resolution is log-authoritative without callback" \
  || bad "WC log-authoritative resolve failed: $out"

# P1-WC private delegation flags never cross the public command router.
P="$(mkproj p1wc-private-flags)"; PF="$(wc_callback_path "$P" private coding)"
cat > "$PF" <<EOF
slug: private
stage: coding
status: BLOCKED
worker: codex-a
session: $WC_TEST_SESSION
incarnation: $WC_TEST_INCARNATION
callback_id: private-id
timestamp: 2026-07-14T00:04:00Z
selected_pending_timestamp: "2026-07-14T00:03:59Z"
message: no matching pending
EOF
PB="$(shasum -a 256 "$PF" | awk '{print $1}')"; PSET="$(wc_artifact_fingerprint "$P" private)"
fabricated="$PF|BLOCKED|private-id|2026-07-14T00:03:59Z|$WC_TEST_SESSION|$WC_TEST_INCARNATION||$PB"
out="$(briS "$P" "$WC_TEST_SESSION" callback-consume --_no_lock --_closed-selection "$fabricated" --slug private --stage coding --status BLOCKED 2>&1)"; rc=$?
[ "$rc" -eq 2 ] && echo "$out" | grep -q 'private callback-consume delegation' \
  && [ "$PSET" = "$(wc_artifact_fingerprint "$P" private)" ] \
  && ok "WC private delegation flags cannot bypass ownership selection" \
  || bad "WC public private-flag bypass changed callback: $out"

# Actor incarnation is re-read after waiting for the physical lifecycle lock.
P="$(mkproj p1wc-incarnation-race)"; pending_entry "$P" race coding codex-a "2026-07-14T00:05:00Z"
cb "$P" --slug race --stage coding --status BLOCKED --worker codex-a --message race >/dev/null 2>&1
RF="$(wc_callback_path "$P" race coding)"; RB="$(wc_artifact_fingerprint "$P" race)"
TL="$P/.race-tmux.tsv"; printf '%s\t%s\t%s\t%s\n' "$WC_TEST_SESSION" "$P" "$$" "$WC_TEST_INCARNATION" > "$TL"
LK="$(lifelock_path "$P" race coding)"; HREADY="$P/.holder-ready"; HRELEASE="$P/.holder-release"; BREADY="$P/.barrier-ready"; BRELEASE="$P/.barrier-release"
wc_race_wait_file() { local path="$1" i=0; while [ ! -f "$path" ] && [ "$i" -lt 500 ]; do sleep 0.02; i=$((i + 1)); done; [ -f "$path" ]; }
( exec 9>"$LK"; flock 9; : > "$HREADY"; wc_race_wait_file "$HRELEASE" ) & holder=$!
if ! wc_race_wait_file "$HREADY"; then
    : > "$HRELEASE"; kill "$holder" 2>/dev/null || true; wait "$holder" 2>/dev/null || true
    bad "WC lifecycle-lock holder barrier timed out"
else
    ( cd "$P" && env -u TMUX TMUX_SESSION="$WC_TEST_SESSION" FORGE_TMUX_LIST="$TL" FORGE_LIFECYCLE_LOCK_WAIT_S=5 \
        FORGE_CALLBACK_PRELOCK_READY="$BREADY" FORGE_CALLBACK_PRELOCK_RELEASE="$BRELEASE" \
        "$BRIDGE" callback-consume --slug race --stage coding --status BLOCKED >"$P/.race-out" 2>&1 ) & consumer=$!
    if ! wc_race_wait_file "$BREADY"; then
        : > "$BRELEASE"; : > "$HRELEASE"; kill "$consumer" "$holder" 2>/dev/null || true
        wait "$consumer" 2>/dev/null || true; wait "$holder" 2>/dev/null || true
        bad "WC callback pre-lock barrier timed out"
    else
        printf '%s\t%s\t%s\t%s\n' "$WC_TEST_SESSION" "$P" "$$" "$((WC_TEST_INCARNATION + 1))" > "$TL"
        : > "$BRELEASE"; sleep 0.1; kill -0 "$consumer" 2>/dev/null || bad "WC consumer did not wait on held lifecycle lock"
        : > "$HRELEASE"; wait "$holder"; wait "$consumer"; rc=$?
        [ "$rc" -ne 0 ] && ! grep -q 'LIFECYCLE_LOCK: busy' "$P/.race-out" \
          && [ "$RB" = "$(wc_artifact_fingerprint "$P" race)" ] \
          && ok "WC under-lock incarnation refresh rejects same-name rebirth" \
          || bad "WC rebirth during lock wait mutated predecessor: $(cat "$P/.race-out")"
    fi
fi

echo ""
echo "═══════════════════════════════════════"
green "PASS: $PASS"
[ "$FAIL" -gt 0 ] && red "FAIL: $FAIL" || green "FAIL: 0"
echo "═══════════════════════════════════════"
[ "$FAIL" -eq 0 ]
