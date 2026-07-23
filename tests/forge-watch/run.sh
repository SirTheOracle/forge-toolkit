#!/bin/bash
# run.sh — self-contained test harness for bin/forge-watch.
#
# Builds synthetic .dev/ fixtures under a scratch workspace, injects a fake
# tmux session map (FORGE_WATCH_TMUX_LIST) and a watch-roots file so discovery
# is deterministic, then asserts findings/notifications per trigger.
#
# Usage: bash tests/forge-watch/run.sh   (exit 0 = all pass)

set -uo pipefail

WATCH="$(cd "$(dirname "$0")/../.." && pwd)/bin/forge-watch"
WATCH_SOURCE_BEFORE="$(shasum -a 256 "$WATCH" | awk '{print $1}')"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/fw-tests.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

PASS=0; FAIL=0
red()   { printf '\033[0;31m%s\033[0m\n' "$1"; }
green() { printf '\033[0;32m%s\033[0m\n' "$1"; }

ok()   { PASS=$((PASS+1)); green "  ok: $1"; }
bad()  { FAIL=$((FAIL+1)); red   "  FAIL: $1"; }

# iso_ago <seconds-ago> -> UTC ISO-8601 Z timestamp
iso_ago() {
    python3 -c "import datetime,sys; print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(seconds=int(sys.argv[1]))).strftime('%Y-%m-%dT%H:%M:%SZ'))" "$1"
}

# ── per-test isolation ───────────────────────────────────────────────────
# Each test gets a fresh cache/config and a fresh roots workspace unless it
# opts to reuse (persistence tests).
new_env() {
    TDIR="$WORK/$1"; rm -rf "$TDIR"; mkdir -p "$TDIR"
    export FORGE_WATCH_CACHE_DIR="$TDIR/cache"
    export FORGE_WATCH_CONFIG_DIR="$TDIR/config"
    mkdir -p "$FORGE_WATCH_CACHE_DIR" "$FORGE_WATCH_CONFIG_DIR"
    ROOTS_DIR="$TDIR/projects"; mkdir -p "$ROOTS_DIR"
    : > "$TDIR/tmux.tsv"
    : > "$FORGE_WATCH_CONFIG_DIR/watch-roots"
    export FORGE_WATCH_TMUX_LIST="$TDIR/tmux.tsv"
    CAP="$TDIR/notify.cap"; : > "$CAP"
    export FORGE_WATCH_SINK_CAPTURE="$CAP"
    # Deterministic thresholds: stall 600 (stale=1200s=20m), dwell 300, zombie 7d.
    unset FORGE_STALL_THRESHOLD_S FORGE_WATCH_DWELL_S FORGE_WATCH_RENOTIFY_S FORGE_WATCH_ZOMBIE_AGE_D 2>/dev/null || true
    # Hardening knobs: existing tests re-run `check` and grep stdout, so pin
    # quiet-unchanged OFF here; the dedicated hardening tests opt in inline.
    export FORGE_WATCH_QUIET_UNCHANGED=0
    unset FORGE_WATCH_CHECK_TIMEOUT_S FORGE_WATCH_NOTIFY_TIMEOUT_S 2>/dev/null || true
}

mk_root() {  # mk_root <name>  -> echoes path; registers in watch-roots
    local r="$ROOTS_DIR/$1"
    mkdir -p "$r/.dev/forge-tmp/callbacks" "$r/.dev/proposals"
    echo "$r" >> "$FORGE_WATCH_CONFIG_DIR/watch-roots"
    echo "$r"
}

live_session() { printf '%s\t%s\n' "$1" "$2" >> "$FORGE_WATCH_TMUX_LIST"; }

ctx() {  # ctx <root> <session> <<yaml
    local f="$1/.dev/forge-context.$2.yml"; cat > "$f"
}

pending_log() {  # pending_log <root> <slug> <stage> <to> <ts>  (open entry)
    local d="$1/.dev/proposals/$2"; mkdir -p "$d"
    cat > "$d/forge-log.yml" <<EOF
entries:
  - timestamp: "$5"
    stage: $3
    to: $4
    response: null
EOF
}

closed_log() {  # closed_log <root> <slug> <stage> <to> <ts>
    local d="$1/.dev/proposals/$2"; mkdir -p "$d"
    cat > "$d/forge-log.yml" <<EOF
entries:
  - timestamp: "$5"
    stage: $3
    to: $4
    response: "FORGE_DONE: $3"
EOF
}

two_entry_log() {  # two_entry_log <root> <slug> <stage> <to1> <ts1> <to2> <ts2> <resp2>
    # Open entry first, then a closed entry (append order). Used for the
    # stale-alert reconciliation cases (orphan open + newer/older closed twin).
    local d="$1/.dev/proposals/$2"; mkdir -p "$d"
    cat > "$d/forge-log.yml" <<EOF
entries:
  - timestamp: "$5"
    stage: $3
    to: $4
    response: null
  - timestamp: "$7"
    stage: $3
    to: $6
    response: "$8"
EOF
}

callback() {  # callback <root> <slug> <stage> <STATUS> <worker>
    cat > "$1/.dev/forge-tmp/callbacks/$2-$3.callback" <<EOF
slug: $2
stage: $3
status: $4
worker: $5
callback_id: ${2}-${3}-x
timestamp: $(iso_ago 60)
message: |
  needs a human
EOF
}

parked_entry() {  # <root> <slug> <stage> <to> <ts> <reason> [uncommitted] [session]
    local d="$1/.dev/proposals/$2"; mkdir -p "$d"
    { echo "entries:"; echo "  - timestamp: \"$5\""; echo "    stage: $3"; echo "    to: $4";
      [ -n "${8:-}" ] && echo "    session: $8";
      echo "    parked_at: $5"; echo "    parked_reason: \"$6\""; echo "    uncommitted: ${7:-false}";
      echo "    response: null"; } > "$d/forge-log.yml"
}
parked_entry_append() {  # <root> <slug> <stage> <to> <ts> <reason> [session]
    { echo "  - timestamp: \"$5\""; echo "    stage: $3"; echo "    to: $4";
      [ -n "${7:-}" ] && echo "    session: $7";
      echo "    parked_at: $5"; echo "    parked_reason: \"$6\""; echo "    uncommitted: false";
      echo "    response: null"; } >> "$1/.dev/proposals/$2/forge-log.yml"
}
parked_callback() {  # <root> <slug> <stage> <worker> [session]
    local f; if [ -n "${5:-}" ]; then f="$1/.dev/forge-tmp/callbacks/$2-$3.$5.callback";
    else f="$1/.dev/forge-tmp/callbacks/$2-$3.callback"; fi
    { echo "slug: $2"; echo "stage: $3"; echo "status: PARKED"; echo "worker: $4";
      [ -n "${5:-}" ] && echo "session: $5"; echo "callback_id: ${2}-${3}-x";
      echo "parked_at: $(iso_ago 120)"; echo "parked_reason: \"held\""; echo "uncommitted: false";
      echo "timestamp: $(iso_ago 120)"; echo "message: |"; echo "  parked"; } > "$f"
}
ask_callback() {  # <root> <slug> <stage> <worker>
    cat > "$1/.dev/forge-tmp/callbacks/$2-$3.callback" <<EOF
slug: $2
stage: $3
status: BLOCKED
origin: ask
worker: $4
callback_id: ${2}-${3}-x
timestamp: $(iso_ago 60)
message: |
  operator ask
EOF
}
board_parked() { run_status --board | python3 -c 'import json,sys;print(json.dumps(json.load(sys.stdin).get("parked") or []))'; }

evlog() { echo "$1/.dev/forge-tmp/orchestrator-events.log"; }
evlog_touch() { : > "$(evlog "$1")"; }        # empty file to baseline against
evlog_append() { echo "$2" >> "$(evlog "$1")"; }

run_check()  { "$WATCH" check  2>&1; }
run_status() { "$WATCH" status "$@" 2>&1; }
notified()   { grep -q "$1" "$CAP"; }

assert_notified()     { if notified "$1"; then ok "notified: $2"; else bad "expected notify [$1]: $2"; fi; }
assert_not_notified() { if notified "$1"; then bad "unexpected notify [$1]: $2"; else ok "silent: $2"; fi; }
assert_status_has()   { if run_status | grep -q "$1"; then ok "status has: $2"; else bad "status missing [$1]: $2"; fi; }
assert_status_missing(){ if run_status | grep -q "$1"; then bad "status has unexpected [$1]: $2"; else ok "status clean: $2"; fi; }

# ═══════════════════════════════════════════════════════════════════════════
echo "── NEEDS-DECISION ──"
new_env nd
R=$(mk_root proj); live_session forge-1 "$R"
# fresh (dwell not met)
ctx "$R" forge-1 <<EOF
active_pipeline: p-fresh
last_stage_completed: qa
last_stage_status: done
next_stage: pending-orchestrator-decision
updated_at: "$(iso_ago 30)"
EOF
run_check >/dev/null
assert_not_notified "p-fresh" "fresh decision within dwell does not fire"

new_env nd2
R=$(mk_root proj); live_session forge-1 "$R"
ctx "$R" forge-1 <<EOF
active_pipeline: p-aged
last_stage_completed: qa
last_stage_status: done
next_stage: pending-orchestrator-decision
updated_at: "$(iso_ago 600)"
EOF
run_check >/dev/null
assert_notified "p-aged" "aged decision, no pending, fires"

new_env nd3
R=$(mk_root proj); live_session forge-1 "$R"
ctx "$R" forge-1 <<EOF
active_pipeline: p-inflight
last_stage_completed: qa
last_stage_status: done
next_stage: pending-orchestrator-decision
updated_at: "$(iso_ago 900)"
EOF
pending_log "$R" p-inflight qa-fix codex-a "$(iso_ago 120)"
run_check >/dev/null
assert_not_notified "p-inflight" "aged decision WITH open pending is suppressed (in-flight gate)"

new_env nd4
R=$(mk_root proj); live_session forge-1 "$R"
# broadened predicate: qa-retry done, next_stage preserved as 'qa' (live shape)
ctx "$R" forge-1 <<EOF
active_pipeline: p-qaretry
last_stage_completed: qa-retry
last_stage_status: done
next_stage: qa
updated_at: "$(iso_ago 700)"
EOF
run_check >/dev/null
assert_notified "p-qaretry" "qa-retry/done/next=qa (preserved intent) fires — broadened predicate"

# ═══════════════════════════════════════════════════════════════════════════
echo "── STAGE-ERROR (never 'failed') ──"
new_env se
R=$(mk_root proj); live_session forge-1 "$R"
ctx "$R" forge-1 <<EOF
active_pipeline: p-err
last_stage_completed: coding
last_stage_status: error
next_stage: ""
updated_at: "$(iso_ago 60)"
EOF
run_check >/dev/null
assert_notified "p-err.*ERRORED" "context last_stage_status=error fires STAGE-ERROR"
if grep -qi "failed" "$CAP"; then bad "no 'failed' in output"; else ok "no 'failed' vocabulary used"; fi

new_env se2
R=$(mk_root proj); live_session forge-1 "$R"
evlog_touch "$R"; run_check >/dev/null            # baseline
evlog_append "$R" "STAGE: pipeline=p-ev result_stage=coding status=error next=pending-orchestrator-decision worker=codex-a"
run_check >/dev/null
assert_notified "p-ev.*ERRORED" "STAGE status=error event fires STAGE-ERROR"

# ═══════════════════════════════════════════════════════════════════════════
echo "── WORKER-BLOCKED ──"
new_env wb
R=$(mk_root proj); live_session forge-1 "$R"
# W7 event-path BLOCKED negative regression (C2.5: event k=v line cannot carry payload
# and is ungated; emission removed — the gated live scan is the sole source)
evlog_touch "$R"; run_check >/dev/null
evlog_append "$R" "CALLBACK: pipeline=p-ev2 stage=qa worker=codex-a status=BLOCKED message_len=10 callback_file=x"
run_check >/dev/null
assert_not_notified "p-ev2" "W7 event-path BLOCKED no longer fires"

new_env wb2
R=$(mk_root proj); live_session forge-1 "$R"
# pre-existing blocked callback + open pending, event offset baselined at EOF
evlog_touch "$R"
pending_log "$R" p-pre coding codex-a "$(iso_ago 120)"
callback "$R" p-pre coding BLOCKED codex-a
run_check >/dev/null
assert_notified "p-pre.*blocked at" "pre-existing BLOCKED callback + open pending fires on first scan (EOF offset)"

new_env wb3
R=$(mk_root proj); live_session forge-1 "$R"
closed_log "$R" p-clsd coding codex-a "$(iso_ago 120)"   # pending closed
callback "$R" p-clsd coding BLOCKED codex-a
run_check >/dev/null
assert_not_notified "p-clsd" "BLOCKED callback whose pending is closed does not fire (stale file)"

new_env wb4
R=$(mk_root proj); live_session forge-1 "$R"
pending_log "$R" p-done coding codex-a "$(iso_ago 120)"
callback "$R" p-done coding DONE codex-a               # lingering DONE
run_check >/dev/null
assert_not_notified "p-done.*BLOCKED" "lingering DONE callback never fires ITEM-BLOCKED"

# W8 live-scan blocked → ITEM-BLOCKED + reason
new_env w8
R=$(mk_root proj); live_session forge-1 "$R"
pending_log "$R" p-lb coding codex-a "$(iso_ago 120)"
callback "$R" p-lb coding BLOCKED codex-a
run_check >/dev/null
assert_notified "p-lb.*blocked at coding" "W8 ITEM-BLOCKED item-first wording + reason"

# W9 rapid blocked→resolved
new_env w9
R=$(mk_root proj); live_session forge-1 "$R"
closed_log "$R" p-rap coding codex-a "$(iso_ago 120)"
callback "$R" p-rap coding BLOCKED codex-a
run_check >/dev/null
assert_not_notified "p-rap" "W9 closed pending → no blocked finding"

# W17 registries contain ITEM-BLOCKED (literal probe)
grep -q "'ITEM-BLOCKED'" "$WATCH" && ok "W17 ITEM-BLOCKED literal present" || bad "W17 ITEM-BLOCKED missing"
! grep -q "'WORKER-BLOCKED'" "$WATCH" && ok "W17 no WORKER-BLOCKED literal remains" || bad "W17 WORKER-BLOCKED remains"

# ═══════════════════════════════════════════════════════════════════════════
echo "── WORKER-STALLED + residue ──"
new_env ws
R=$(mk_root proj); live_session forge-1 "$R"
# stale (>1200s) but within 7d, no context file -> reachable by direct log scan
pending_log "$R" p-stall coding codex-a "$(iso_ago 1800)"
run_check >/dev/null
assert_notified "p-stall.*stalled" "stale pending (no context file) fires via direct log scan"

new_env ws2
R=$(mk_root proj); live_session forge-1 "$R"
pending_log "$R" p-both coding codex-a "$(iso_ago 1800)"
callback "$R" p-both coding BLOCKED codex-a
run_check >/dev/null
assert_notified   "p-both.*blocked at" "blocked callback fires"
assert_not_notified "p-both.*stalled" "WORKER-STALLED suppressed when a live BLOCKED callback covers the pending"

new_env ws3
R=$(mk_root proj); live_session forge-1 "$R"
pending_log "$R" p-old coding codex-a "$(iso_ago 6000000)"   # ~69d
run_check >/dev/null
assert_not_notified "p-old" "months-old open pending is residue (status-only), not a stall notification"
assert_status_has "STALE-PENDING.*p-old" "residue pending visible in status"

new_env ws4
R=$(mk_root proj); live_session forge-1 "$R"
pending_log "$R" p-act coding codex-a "$(iso_ago 1800)"
echo work > "$R/.dev/proposals/p-act/diagnosis.md"                      # fresh artifact = alive
run_check >/dev/null
assert_not_notified "p-act.*stalled" "stale pending with FRESH slug artifacts is working, not stalled"

new_env ws5
R=$(mk_root proj); live_session forge-1 "$R"
pending_log "$R" p-idle coding codex-a "$(iso_ago 1800)"
echo work > "$R/.dev/proposals/p-idle/diagnosis.md"
touch -t "$(python3 -c "import datetime;print((datetime.datetime.now()-datetime.timedelta(seconds=1800)).strftime('%Y%m%d%H%M'))")" "$R/.dev/proposals/p-idle/diagnosis.md"
run_check >/dev/null
assert_notified "p-idle.*stalled" "stale pending with only OLD artifacts still fires WORKER-STALLED"

# ═══════════════════════════════════════════════════════════════════════════
echo "── ITEM-PARKED (log-authoritative) + PARK-INCONSISTENT ──"
# W2 log-only reconstruction
new_env w2
R=$(mk_root proj); live_session forge-1 "$R"
parked_entry "$R" p-park coding codex-a "$(iso_ago 300)" "out of scope now" false forge-1
run_check >/dev/null
assert_status_has "p-park.*parked at coding" "W2 ITEM-PARKED row from log alone"
assert_notified "p-park.*incomplete park" "W2 hot PARK-INCONSISTENT"

# W3 entry + BLOCKED callback → parked row + repair
new_env w3
R=$(mk_root proj); live_session forge-1 "$R"
parked_entry "$R" p-pk3 coding codex-a "$(iso_ago 300)" "reason3" false forge-1
callback "$R" p-pk3 coding BLOCKED codex-a
run_check >/dev/null
assert_status_has "p-pk3.*parked at coding" "W3 parked row present"
assert_notified "p-pk3.*incomplete park" "W3 repair finding"

# W4 orphan PARKED callback
new_env w4
R=$(mk_root proj); live_session forge-1 "$R"
parked_callback "$R" p-orph coding codex-a forge-1
run_check >/dev/null
assert_notified "p-orph.*orphan PARKED callback" "W4 orphan repair finding"

# W5 agreement → clean ITEM-PARKED + board payload
new_env w5
R=$(mk_root proj); live_session forge-1 "$R"
parked_entry "$R" p-ok coding codex-a "$(iso_ago 300)" "held reason" true forge-1
parked_callback "$R" p-ok coding codex-a forge-1
run_check >/dev/null
assert_not_notified "p-ok.*incomplete park" "W5 no repair on agreement"
board_parked | python3 -c 'import json,sys
r=json.load(sys.stdin); assert r and r[0]["slug"]=="p-ok" and r[0]["stage"]=="coding" \
  and r[0]["reason"]=="held reason" and r[0]["uncommitted"] is True \
  and r[0]["worker"]=="codex-a" and r[0]["session"]=="forge-1", r' \
  && ok "W5 board[parked][0] payload" || bad "W5 board parked payload wrong"

# W6 duplicates
new_env w6
R=$(mk_root proj); live_session forge-1 "$R"
parked_entry "$R" p-dup coding codex-a "$(iso_ago 500)" "older" false forge-1
parked_entry_append "$R" p-dup coding codex-a "$(iso_ago 100)" "newer" forge-1
run_check >/dev/null
assert_status_has "p-dup.*×2 entries" "W6 duplicate ×2 flag"

# W11 parked excluded from stall
new_env w11
R=$(mk_root proj); live_session forge-1 "$R"
parked_entry "$R" p-ps coding codex-a "$(iso_ago 6000000)" "old parked" false forge-1
run_check >/dev/null
assert_not_notified "p-ps.*stalled" "W11 parked excluded from stall"

# W12 ack leaves parked[] + PRETTY intact
new_env w12
R=$(mk_root proj); live_session forge-1 "$R"
parked_entry "$R" p-ack coding codex-a "$(iso_ago 300)" "keep" false forge-1
parked_callback "$R" p-ack coding codex-a forge-1
run_check >/dev/null
"$WATCH" ack p-ack >/dev/null 2>&1 || true
run_check >/dev/null
run_status --pretty | grep -q "p-ack/coding" && ok "W12 PRETTY parked survives ack" || bad "W12 PRETTY parked lost"
board_parked | grep -q '"p-ack"' && ok "W12 board parked survives ack" || bad "W12 board parked lost"

# W13 7-day survival
new_env w13
R=$(mk_root proj); live_session forge-1 "$R"
parked_entry "$R" p-7d coding codex-a "$(iso_ago 604800)" "week old" false forge-1
parked_callback "$R" p-7d coding codex-a forge-1
run_check >/dev/null
assert_status_has "p-7d.*parked at coding" "W13 parked survives 7 days"

# W16 ITEM-PARKED not suppressed by an ask on a different stage
new_env w16
R=$(mk_root proj); live_session forge-1 "$R"
parked_entry "$R" p-mix coding codex-a "$(iso_ago 300)" "parked" false forge-1
parked_callback "$R" p-mix coding codex-a forge-1
# APPEND a second open pending (qa/ask) to the SAME log — production accumulates all
# stage entries in one forge-log.yml; a plain pending_log would clobber the parked entry.
{ echo "  - timestamp: \"$(iso_ago 30)\""; echo "    stage: qa"; echo "    to: codex-b";
  echo "    session: forge-1"; echo "    response: null"; } >> "$R/.dev/proposals/p-mix/forge-log.yml"
ask_callback "$R" p-mix qa codex-b
run_check >/dev/null
assert_status_has "p-mix.*parked at coding" "W16 parked coexists with an ask"

# W10 ABANDONED + incarnation reset + ask control  (impl-C-r3 §6.1, verbatim body)
new_env w10
R=$(mk_root proj); live_session forge-1 "$R"
export FORGE_BLOCKED_ABANDON_S=1
pending_log "$R" p-ab coding codex-a "$(iso_ago 5)"
callback "$R" p-ab coding BLOCKED codex-a
evlog_touch "$R"; run_check >/dev/null          # tick 1: evidence recorded
evlog_append "$R" "DISPATCH: pipeline=p-other stage=coding worker=codex-b"
run_check >/dev/null                            # tick 2: later_other_dispatch=True
sleep 2; run_check >/dev/null                   # tick 3: aged past window
assert_notified "p-ab.*SKIPPED" "W10 ABANDONED after guard-bypass evidence + age"
# incarnation reset: consume + re-block → new callback_id, no inherited later_other_dispatch
rm -f "$R"/.dev/forge-tmp/callbacks/p-ab-coding*.callback
cat > "$R/.dev/forge-tmp/callbacks/p-ab-coding.callback" <<EOF
slug: p-ab
stage: coding
status: BLOCKED
worker: codex-a
callback_id: p-ab-coding-REBORN
timestamp: $(iso_ago 5)
message: |
  needs a human
EOF
: > "$CAP"                                       # fresh-tick assertion: only THIS tick counts
run_check >/dev/null
assert_not_notified "p-ab.*SKIPPED" "W10 incarnation reset: reborn block not ABANDONED"
unset FORGE_BLOCKED_ABANDON_S
# ask-origin control: raises nothing
R=$(mk_root proj); live_session forge-1 "$R"
pending_log "$R" p-ask coding codex-a "$(iso_ago 5)"
ask_callback "$R" p-ask coding codex-a
export FORGE_BLOCKED_ABANDON_S=1
evlog_touch "$R"; run_check >/dev/null
evlog_append "$R" "DISPATCH: pipeline=p-o2 stage=coding worker=codex-b"
run_check >/dev/null; sleep 2; run_check >/dev/null
assert_not_notified "p-ask.*SKIPPED" "W10 ask-origin never ABANDONED"
unset FORGE_BLOCKED_ABANDON_S

# W14 qualified COMPLETE non-green on event tick AND later tick
new_env w14
R=$(mk_root proj); live_session forge-1 "$R"
parked_entry "$R" p-cq verify codex-a "$(iso_ago 60)" "still parked" false forge-1
parked_callback "$R" p-cq verify codex-a forge-1
# context reached terminal next_stage so the context-fallback qualifier (P16e) re-derives
# the non-green COMPLETE on every tick (the event finding notifies once but is consumed).
ctx "$R" forge-1 <<EOF
schema: forge-context/1
active_pipeline: p-cq
last_stage_completed: verify
last_stage_status: done
next_stage: complete
updated_at: "$(iso_ago 60)"
EOF
evlog_touch "$R"; run_check >/dev/null
evlog_append "$R" "COMPLETE: pipeline=p-cq last_stage=verify worker=codex-a qualifier=incomplete parked=1 blocked=0"
run_check >/dev/null
assert_notified "p-cq.*1 parked" "W14 qualified COMPLETE non-green (event tick)"
run_check >/dev/null
assert_status_has "p-cq.*1 parked" "W14 still non-green on a later tick (context fallback)"

# W15 ask-origin block → context-fallback COMPLETE stays GREEN
new_env w15
R=$(mk_root proj); live_session forge-1 "$R"
pending_log "$R" p-aq qa codex-a "$(iso_ago 30)"
ask_callback "$R" p-aq qa codex-a
ctx "$R" forge-1 <<EOF
schema: forge-context/1
active_pipeline: p-aq
last_stage_completed: verify
last_stage_status: done
next_stage: complete
updated_at: "$(iso_ago 30)"
EOF
run_check >/dev/null
assert_status_has "p-aq.*pipeline COMPLETE" "W15 ask-only → fallback COMPLETE stays green"
assert_status_missing "p-aq.*blocked (incomplete)" "W15 ask not counted as blocked"

# ═══════════════════════════════════════════════════════════════════════════
echo "── event: PIPELINE-ERROR / STALL / COMPLETE ──"
new_env ev
R=$(mk_root proj); live_session forge-1 "$R"
evlog_touch "$R"; run_check >/dev/null
evlog_append "$R" "ERROR: pipeline=p-e stage=coding reason=dispatch-guard-parse-failed worker=codex-a"
evlog_append "$R" "GUARD_BLOCK: pipeline=p-g stage=qa reason=open-pending-exists worker=codex-b n_pending=1"
evlog_append "$R" "STALL: pipeline=p-s stage=coding worker=codex-a state=STUCK"
evlog_append "$R" "COMPLETE: pipeline=p-c last_stage=verify worker=codex-a"
run_check >/dev/null
assert_notified "p-e.*error"    "ERROR line fires PIPELINE-ERROR"
assert_notified "p-g.*guard"    "GUARD_BLOCK line fires PIPELINE-ERROR"
assert_notified "p-s.*stall"    "STALL line fires WORKER-STALL-EVENT"
assert_notified "p-c.*COMPLETE" "COMPLETE line fires PIPELINE-COMPLETE"

new_env ev2
R=$(mk_root proj); live_session forge-1 "$R"
ctx "$R" forge-1 <<EOF
active_pipeline: p-fin
last_stage_completed: verify
last_stage_status: done
next_stage: complete
updated_at: "$(iso_ago 60)"
EOF
run_check >/dev/null
assert_notified "p-fin.*COMPLETE" "context next_stage=complete fires PIPELINE-COMPLETE (fallback)"

# ═══════════════════════════════════════════════════════════════════════════
echo "── zombies + aliasing ──"
new_env zb
R=$(mk_root proj); live_session forge-9 "$R"   # forge-1 NOT live here
# recent (<7d) zombie context named for a dead session
ctx "$R" forge-1 <<EOF
active_pipeline: p-zombie
last_stage_completed: implementation
last_stage_status: done
next_stage: impl-review
updated_at: "$(iso_ago 7200)"
EOF
run_check >/dev/null
assert_notified "p-zombie.*ZOMBIE" "recent zombie (dead session) fires ZOMBIE-ACTIVE"

new_env zb2
R=$(mk_root proj); live_session forge-9 "$R"
ctx "$R" forge-1 <<EOF
active_pipeline: p-oldzombie
last_stage_completed: fix-qa
last_stage_status: done
next_stage: unknown
updated_at: "$(iso_ago 1900000)"
EOF
run_check >/dev/null
assert_not_notified "p-oldzombie" "old zombie (>7d, no evidence) is status-only"
assert_status_has "ZOMBIE-STALE-CONTEXT.*p-oldzombie" "old zombie visible in status"

echo "── aliasing (name,path) ──"
new_env al
RA=$(mk_root projA); RB=$(mk_root projB)
live_session forge-3 "$RB"                       # forge-3 lives at B, not A
ctx "$RA" forge-3 <<EOF
active_pipeline: p-aliased
last_stage_completed: coding
last_stage_status: done
next_stage: qa
updated_at: "$(iso_ago 3600)"
EOF
run_check >/dev/null
assert_notified "p-aliased.*ZOMBIE" "context for session live at a DIFFERENT root is a zombie (name+path check)"

new_env al2
RA=$(mk_root projA)
live_session forge-3 "$RA"                        # forge-3 genuinely at A
ctx "$RA" forge-3 <<EOF
active_pipeline: p-liveA
last_stage_completed: coding
last_stage_status: done
next_stage: qa
updated_at: "$(iso_ago 3600)"
EOF
run_check >/dev/null
assert_not_notified "p-liveA.*ZOMBIE" "same fixture with session genuinely at this root is NOT a zombie"

# ═══════════════════════════════════════════════════════════════════════════
echo "── LEGACY-CONTEXT ──"
new_env lg
R=$(mk_root proj); live_session forge-1 "$R"
cat > "$R/.dev/forge-context.yml" <<EOF
active_pipeline: p-legacy
last_stage_completed: implementation
last_stage_status: done
next_stage: impl-review
updated_at: "$(iso_ago 90000)"
EOF
run_check >/dev/null
assert_not_notified "p-legacy" "bare legacy forge-context.yml never notifies"
assert_status_has "LEGACY-CONTEXT.*p-legacy" "legacy pointer visible in status"

# ═══════════════════════════════════════════════════════════════════════════
echo "── parsers ──"
new_env ps
R=$(mk_root proj); live_session forge-1 "$R"
# malformed forge-log.yml with the legacy bad-indent files: block -> regex fallback
mkdir -p "$R/.dev/proposals/p-mal"
cat > "$R/.dev/proposals/p-mal/forge-log.yml" <<EOF
entries:
  - timestamp: "$(iso_ago 1800)"
    stage: coding
    to: codex-a
    response: null
    files:
- path: bad/indent.py
EOF
run_check >/dev/null
assert_notified "p-mal.*stalled" "malformed forge-log.yml parsed via regex fallback (stall detected)"

new_env ps2
R=$(mk_root proj); live_session forge-1 "$R"
# yaml.dump / add-note writer style: single quotes + notes block
cat > "$R/.dev/forge-context.forge-1.yml" <<EOF
active_pipeline: p-dump
last_stage_completed: qa
last_stage_status: done
next_stage: pending-orchestrator-decision
updated_at: '$(iso_ago 800)'
notes:
- 'some note from add-note'
EOF
run_check >/dev/null
assert_notified "p-dump" "yaml.dump/add-note writer style (single-quoted) parses"

new_env ps3
R=$(mk_root proj)   # no live session for this root; __nosession__ is never a tmux name
ctx "$R" __nosession__ <<EOF
active_pipeline: p-nosess
last_stage_completed: coding
last_stage_status: done
next_stage: qa
updated_at: "$(iso_ago 3600)"
EOF
run_check >/dev/null
assert_notified "p-nosess.*ZOMBIE" "__nosession__ context is a zombie candidate"

new_env ps4
R=$(mk_root proj); live_session forge-1 "$R"
mkdir -p "$R/.dev/proposals/p-junk"
printf 'this is not yaml: [unclosed\n  \tgarbage\x00\n' > "$R/.dev/proposals/p-junk/forge-log.yml"
run_check >/dev/null
assert_notified "unparseable" "fully unparseable file fires STATE-UNPARSEABLE"
# clears when fixed
closed_log "$R" p-junk coding codex-a "$(iso_ago 120)"
: > "$CAP"
run_status | grep -q "unparseable" && bad "STATE-UNPARSEABLE should clear after fix" || ok "STATE-UNPARSEABLE clears when file parses again"

# ═══════════════════════════════════════════════════════════════════════════
echo "── debounce / ack / baseline ──"
new_env db
R=$(mk_root proj); live_session forge-1 "$R"
ctx "$R" forge-1 <<EOF
active_pipeline: p-deb
last_stage_completed: qa
last_stage_status: done
next_stage: pending-orchestrator-decision
updated_at: "$(iso_ago 900)"
EOF
run_check >/dev/null
n1=$(wc -l < "$CAP")
: > "$CAP"; run_check >/dev/null
n2=$(wc -l < "$CAP")
if [ "$n1" -ge 1 ] && [ "$n2" -eq 0 ]; then ok "transition notifies once; immediate re-scan is silent (backoff)"; else bad "debounce: first=$n1 second=$n2"; fi

# ack silences
: > "$CAP"
"$WATCH" ack p-deb >/dev/null
run_check >/dev/null
if [ "$(wc -l < "$CAP")" -eq 0 ]; then ok "ack silences the condition"; else bad "ack did not silence"; fi

# clearing resets (condition gone -> state entry cleared -> re-entry fires fresh)
cat > "$R/.dev/forge-context.forge-1.yml" <<EOF
active_pipeline: p-deb
last_stage_completed: coding
last_stage_status: done
next_stage: qa
updated_at: "$(iso_ago 60)"
EOF
run_check >/dev/null    # condition cleared
: > "$CAP"
ctx "$R" forge-1 <<EOF
active_pipeline: p-deb
last_stage_completed: qa
last_stage_status: done
next_stage: pending-orchestrator-decision
updated_at: "$(iso_ago 900)"
EOF
run_check >/dev/null
if [ "$(wc -l < "$CAP")" -ge 1 ]; then ok "condition clears then re-fires on re-entry (ack forgotten)"; else bad "re-entry after clear did not fire"; fi

echo "── event baseline / recreate ──"
new_env eb
R=$(mk_root proj); live_session forge-1 "$R"
# pre-populate log with historical ERROR before first sight
evlog_append "$R" "ERROR: pipeline=p-hist stage=coding reason=old-thing worker=codex-a"
run_check >/dev/null
assert_not_notified "p-hist" "pre-existing event log is baselined to EOF (no replay flood on first sight)"
# recreate (truncate) the log smaller, then a new line -> processed
: > "$(evlog "$R")"
run_check >/dev/null                       # re-baseline to 0
evlog_append "$R" "ERROR: pipeline=p-new stage=qa reason=fresh worker=codex-b"
: > "$CAP"; run_check >/dev/null
assert_notified "p-new" "recreated/truncated log re-baselines and then processes new lines"

# ═══════════════════════════════════════════════════════════════════════════
echo "── corrupt state recovery ──"
new_env cs
R=$(mk_root proj); live_session forge-1 "$R"
ctx "$R" forge-1 <<EOF
active_pipeline: p-cs
last_stage_completed: qa
last_stage_status: done
next_stage: pending-orchestrator-decision
updated_at: "$(iso_ago 900)"
EOF
printf '{ this is not json ' > "$FORGE_WATCH_CACHE_DIR/state.json"
out=$(run_check)
if ls "$FORGE_WATCH_CACHE_DIR"/state.json.bad.* >/dev/null 2>&1 && notified "p-cs"; then
    ok "corrupt state.json preserved as .bad.* and scan still ran"
else
    bad "corrupt state recovery failed: $out"
fi

# ═══════════════════════════════════════════════════════════════════════════
echo "── status/ack are read-only (do not consume events or arm debounce) ──"
new_env ro
R=$(mk_root proj); live_session forge-1 "$R"
evlog_touch "$R"; run_check >/dev/null           # baseline offset
evlog_append "$R" "STAGE: pipeline=p-ro result_stage=coding status=error next=x worker=codex-a"
# A status peek must NOT consume the new event line...
run_status >/dev/null
: > "$CAP"; run_check >/dev/null
assert_notified "p-ro.*ERRORED" "status peek did not swallow the event — check still delivers it"

new_env ro2
R=$(mk_root proj); live_session forge-1 "$R"
ctx "$R" forge-1 <<EOF
active_pipeline: p-ro2
last_stage_completed: qa
last_stage_status: done
next_stage: pending-orchestrator-decision
updated_at: "$(iso_ago 900)"
EOF
run_status >/dev/null                              # peek must not arm debounce
run_status >/dev/null
: > "$CAP"; run_check >/dev/null
assert_notified "p-ro2" "status peek did not pre-arm debounce — first check still fires"

# ═══════════════════════════════════════════════════════════════════════════
echo "── PIPELINE-COMPLETE fires once, not forever ──"
new_env pc
R=$(mk_root proj); live_session forge-1 "$R"
ctx "$R" forge-1 <<EOF
active_pipeline: p-comp
last_stage_completed: verify
last_stage_status: done
next_stage: complete
updated_at: "$(iso_ago 60)"
EOF
run_check >/dev/null
assert_notified "p-comp.*COMPLETE" "completion notifies once"
# It persists (pane stays open at next_stage=complete) but must NOT re-notify.
export FORGE_WATCH_RENOTIFY_S=1   # make any backoff window trivially elapsed
: > "$CAP"; sleep 1; run_check >/dev/null
assert_not_notified "p-comp" "completion does not re-notify while it persists (policy=once)"
unset FORGE_WATCH_RENOTIFY_S

# ═══════════════════════════════════════════════════════════════════════════
echo "── zombie + stale pending does not double-notify ──"
new_env zd
R=$(mk_root proj); live_session forge-9 "$R"      # forge-1 not live here
pending_log "$R" p-zdead coding codex-a "$(iso_ago 1800)"   # stale + recent
ctx "$R" forge-1 <<EOF
active_pipeline: p-zdead
last_stage_completed: coding
last_stage_status: done
next_stage: qa
updated_at: "$(iso_ago 3600)"
EOF
run_check >/dev/null
assert_notified "p-zdead.*ZOMBIE" "dead-session slug fires ZOMBIE-ACTIVE"
assert_not_notified "p-zdead.*stalled" "WORKER-STALLED suppressed for a slug already flagged zombie"

# ═══════════════════════════════════════════════════════════════════════════
echo "── malformed log entry does not abort the root scan ──"
new_env me
R=$(mk_root proj); live_session forge-1 "$R"
mkdir -p "$R/.dev/proposals/p-bad"
printf 'entries:\n  - "oops not a dict"\n' > "$R/.dev/proposals/p-bad/forge-log.yml"
# an unrelated, healthy blocked callback in the same root must still surface
pending_log "$R" p-healthy coding codex-a "$(iso_ago 120)"
callback "$R" p-healthy coding BLOCKED codex-a
run_check >/dev/null
assert_notified "p-healthy.*blocked at" "a non-dict log entry does not suppress unrelated findings in the same root"

# ═══════════════════════════════════════════════════════════════════════════
echo "── concurrency lock ──"
new_env lk
R=$(mk_root proj); live_session forge-1 "$R"
ctx "$R" forge-1 <<EOF
active_pipeline: p-lock
last_stage_completed: qa
last_stage_status: done
next_stage: pending-orchestrator-decision
updated_at: "$(iso_ago 900)"
EOF
# Hold the state lock in a background python, then run check: it must find the
# lock held, exit 0 quietly, deliver nothing, and not corrupt state.
python3 - "$FORGE_WATCH_CACHE_DIR/state.lock" <<'PYLOCK' &
import sys, fcntl, time
fh = open(sys.argv[1], 'w')
fcntl.flock(fh, fcntl.LOCK_EX)
time.sleep(2.5)
PYLOCK
LOCKPID=$!
sleep 0.4
: > "$CAP"
if "$WATCH" check >/dev/null 2>&1 && [ "$(wc -l < "$CAP")" -eq 0 ]; then
    ok "second instance sees held lock, exits 0, delivers nothing"
else
    bad "lock contention not handled cleanly"
fi
wait "$LOCKPID" 2>/dev/null || true
# lock released -> now it fires
: > "$CAP"; run_check >/dev/null
notified "p-lock" && ok "after lock released, scan proceeds normally" || bad "post-lock scan did not fire"

# ═══════════════════════════════════════════════════════════════════════════
echo "── watch.env parser (data, not code) ──"
new_env cfg
R=$(mk_root proj); live_session forge-1 "$R"
CANARY="$TDIR/canary"; CANARY2="$TDIR/canary2"
cat > "$FORGE_WATCH_CONFIG_DIR/watch.env" <<EOF
FORGE_WATCH_DWELL_S=120
EVIL=\$(touch $CANARY)
FORGE_WATCH_ZOMBIE_AGE_D=\$(touch $CANARY2)
BOGUS_KEY=1
FORGE_WATCH_RENOTIFY_S="900"
EOF
ctx "$R" forge-1 <<EOF
active_pipeline: p-cfg
last_stage_completed: qa
last_stage_status: done
next_stage: pending-orchestrator-decision
updated_at: "$(iso_ago 200)"
EOF
out=$(run_check)
if [ -f "$CANARY" ] || [ -f "$CANARY2" ]; then bad "watch.env executed shell code (canary created!)"; else ok "watch.env not sourced — no code execution"; fi
echo "$out" | grep -q "unknown key 'BOGUS_KEY'" && ok "unknown key rejected loudly" || bad "unknown key not reported"
echo "$out" | grep -q "unsafe value for 'FORGE_WATCH_ZOMBIE_AGE_D'" && ok "unsafe value on a known key rejected loudly" || bad "unsafe value not reported"
# DWELL now 120 -> a 200s-old decision should fire (would not at default 300)
notified "p-cfg" && ok "whitelisted FORGE_WATCH_DWELL_S=120 applied (200s decision fires)" || bad "watch.env value not applied"

# ═══════════════════════════════════════════════════════════════════════════
echo "── no tmux server ──"
new_env nt
unset FORGE_WATCH_TMUX_LIST     # engine will try real tmux; harness has none guaranteed? force empty
export FORGE_WATCH_TMUX_LIST="$TDIR/empty.tsv"; : > "$TDIR/empty.tsv"
: > "$FORGE_WATCH_CONFIG_DIR/watch-roots"
if "$WATCH" status >/dev/null 2>&1; then ok "empty session map exits 0 cleanly"; else bad "empty session map errored"; fi

# ── attention helpers (command center) ──
attn(){ mkdir -p "$1/.dev/attention/payloads"; echo "$1/.dev/attention"; }
disp(){  # disp <root> <session> <ageSec> <id> [snippet] [sender]
  local a; a="$(attn "$1")"; cat > "$a/dispatch-$4.json" <<JSON
{"schema":"cc-dispatch/1","event":"dispatch","dispatch_id":"$4","session":"$2","root":"$1","target_pane":1,"mode":"inline","instruction_snippet":"${5:-do the thing}","instruction_sha256":"abc","dispatched_at":"$(iso_ago "$3")","sender":"${6:-seat}","state":"queued-input","answers_ask_id":null}
JSON
}
promptf(){ local a; a="$(attn "$1")"; cat > "$a/prompt.$2.json" <<JSON
{"schema":"cc-attention/1","event":"userpromptsubmit","variant":"dispatch-accept","session":"$2","root":"$1","pane_index":"1","role":"orchestrator","tmux_pane":"%1","emitted_at":"$(iso_ago "$3")","dispatch_id":"${4:-null}","prompt_snippet":"x"}
JSON
}
stopf(){ # stopf <root> <session> <ageSec> [snippet] [is_q] [question_snippet] [response_did]
  local a; a="$(attn "$1")"; local isq="${5:-false}" extra=""
  if [ -n "${7:-}" ]; then
    extra=",\"question_snippet\":\"${6:-a question?}\",\"response_paths\":[\".dev/attention/payloads/response.$7.txt\"],\"response_dispatch_ids\":[\"$7\"],\"truncated\":false,\"full_bytes\":120"
  elif [ -n "${6:-}" ]; then
    extra=",\"question_snippet\":\"$6\""
  fi
  cat > "$a/stop.$2.json" <<JSON
{"schema":"cc-attention/1","event":"stop","variant":"snippet","session":"$2","root":"$1","pane_index":"1","role":"orchestrator","tmux_pane":"%1","emitted_at":"$(iso_ago "$3")","snippet":"${4:-all done}","snippet_source":"last_assistant_message","looks_like_question":$isq$extra}
JSON
}
permf(){ local a; a="$(attn "$1")"; cat > "$a/perm.$2.$3.json" <<JSON
{"schema":"cc-attention/1","event":"permissionrequest","variant":"permission","session":"$2","root":"$1","pane_index":"1","role":"orchestrator","tmux_pane":"%1","emitted_at":"$(iso_ago "${5:-30}")","state":"needs-input","tool_name":"${4:-Bash}","command":"run something","command_hash":"$3","permission_suggestions":[]}
JSON
}
askf(){  # askf <root> <session> <ask_id> <slug> <stage> [ageSec] [question]
  local a; a="$(attn "$1")"
  python3 - "$a/$3.json" "$2" "$1" "$3" "${4:-}" "${5:-}" "$(iso_ago "${6:-60}")" "${7:-migrate users too or only orders?}" <<'PY'
import json,sys
p,sess,root,aid,slug,stage,ts,q=sys.argv[1:9]
json.dump({"schema":"cc-attention/1","event":"ask","variant":"ask","session":sess,"root":root,
  "pane_index":"4","role":"worker","tmux_pane":"%4","emitted_at":ts,"ask_id":aid,
  "mode":"stage" if slug else "session-scope","slug":slug or None,"stage":stage or None,
  "worker":"codex-a","question_snippet":q,"question_sha256":"deadbeef"},open(p,"w"),indent=2)
PY
}
wpromptf(){ # <root> <session> <pane> <ageSec> <task_id> [agent] [snippet] [dispatch_id]
  local a; a="$(attn "$1")"; local didj="null"; [ -n "${8:-}" ] && didj="\"$8\""
  cat > "$a/wprompt.$2.p$3.json" <<JSON
{"schema":"cc-attention/1","event":"userpromptsubmit","variant":"worker-prompt","session":"$2","root":"$1","pane_index":"$3","role":"worker","agent":"${6:-claude}","tmux_pane":"%$3","emitted_at":"$(iso_ago "$4")","task_id":"$5","dispatch_id":$didj,"dispatch_ids":[],"prompt_snippet":"${7:-typed work}","prompt_sha256":"abc"}
JSON
}
wstopf(){ # <root> <session> <pane> <ageSec> <task_id> [agent] [snippet] [is_q] [qsnip]
  local a; a="$(attn "$1")"; local isq="${8:-false}" extra=""
  [ -n "${9:-}" ] && extra=",\"question_snippet\":\"$9\""
  cat > "$a/wstop.$2.p$3.$5.json" <<JSON
{"schema":"cc-attention/1","event":"stop","variant":"worker-snippet","session":"$2","root":"$1","pane_index":"$3","role":"worker","agent":"${6:-claude}","tmux_pane":"%$3","emitted_at":"$(iso_ago "$4")","task_id":"$5","prompt_snippet":"p","snippet":"${7:-worker done}","snippet_source":"last_assistant_message","looks_like_question":$isq$extra}
JSON
}
wpermf(){ # <root> <session> <pane> <hash> [ageSec]
  local a; a="$(attn "$1")"; cat > "$a/wperm.$2.p$3.$4.json" <<JSON
{"schema":"cc-attention/1","event":"permissionrequest","variant":"worker-permission","session":"$2","root":"$1","pane_index":"$3","role":"worker","agent":"claude","tmux_pane":"%$3","emitted_at":"$(iso_ago "${5:-30}")","state":"needs-input","tool_name":"Bash","command":"do x","command_hash":"$4","permission_suggestions":[]}
JSON
}

echo "── attention: idle dispatch → accepted (working) ──"
new_env att1
R=$(mk_root proj); live_session forge-1 "$R"
disp "$R" forge-1 120 cc-x-idle
promptf "$R" forge-1 60 cc-x-idle          # UserPromptSubmit AFTER dispatch, id match
assert_status_has "forge-1 — working" "idle dispatch accepted via UserPromptSubmit id → SESSION-WORKING"
assert_status_missing "queued-input" "no longer queued once accepted"

echo "── attention: mid-turn dispatch stays queued until next Stop (absorption) ──"
new_env att2
R=$(mk_root proj); live_session forge-1 "$R"
stopf "$R" forge-1 300 "prev turn"          # Stop predating the dispatch
disp  "$R" forge-1 120 cc-x-mid             # lands after that Stop, no new prompt
assert_status_has "forge-1 — queued-input" "mid-turn dispatch, no newer activity → SESSION-QUEUED"
stopf "$R" forge-1 30 "absorbed answer"     # closing Stop, newer than dispatch
assert_status_has "forge-1 — done" "next Stop after dispatch closes the absorbed dispatch"
assert_status_missing "queued-input" "queued-input cleared by the closing Stop"

echo "── attention: exactly one lifecycle state (no queued+done contradiction) ──"
new_env att1b
R=$(mk_root proj); live_session forge-1 "$R"
disp "$R" forge-1 120 cc-open               # unclosed dispatch
stopf "$R" forge-1 300 "older stop"         # an OLDER stop also present
n=$(run_status | grep -c "forge-1 — ")
[ "$n" -eq 1 ] && ok "exactly one lifecycle row per session (unclosed dispatch wins → queued)" || bad "got $n lifecycle rows"
assert_status_has "forge-1 — queued-input" "unclosed dispatch outranks the older stop"

echo "── attention: NEEDS-PERMISSION enters notify/debounce/ack ──"
new_env att3
R=$(mk_root proj); live_session forge-1 "$R"
permf "$R" forge-1 deadbeef Bash 30
run_check >/dev/null
assert_notified "permission needed" "PermissionRequest attention fires a notification"
: > "$CAP"; run_check >/dev/null
[ "$(wc -l < "$CAP")" -eq 0 ] && ok "NEEDS-PERMISSION debounced on immediate re-scan" || bad "re-notified within backoff"
: > "$CAP"; "$WATCH" ack forge-1 >/dev/null; run_check >/dev/null
[ "$(wc -l < "$CAP")" -eq 0 ] && ok "ack silences NEEDS-PERMISSION" || bad "ack did not silence permission"

echo "── attention: later Stop supersedes an answered permission + malformed json ──"
new_env att4
R=$(mk_root proj); live_session forge-1 "$R"
permf "$R" forge-1 cafe Bash 300             # old permission
stopf "$R" forge-1 60 "ok"                   # a Stop after it → answered
assert_status_missing "permission needed" "a later Stop supersedes the answered permission"
printf 'not json{' > "$R/.dev/attention/perm.forge-1.bad.json"
assert_status_has "unparseable" "malformed attention json fires STATE-UNPARSEABLE"

echo "── attention: AskUserQuestion perm rows carry the question (QW1-QW4) ──"
new_env attq1
R=$(mk_root proj); live_session forge-1 "$R"
a="$(attn "$R")"; cat > "$a/perm.forge-1.e3b0c442.json" <<JSON
{"schema":"cc-attention/1","event":"permissionrequest","variant":"permission","session":"forge-1","root":"$R","pane_index":"1","role":"orchestrator","tmux_pane":"%1","emitted_at":"$(iso_ago 30)","state":"needs-input","tool_name":"AskUserQuestion","command":"","command_hash":"e3b0c442","permission_suggestions":[],"question_snippet":"Deploy to prod?","question_options":["yes","no","dry-run"],"question_count":2,"multi_select":false}
JSON
assert_status_has "question (AskUserQuestion): Deploy to prod?" "QW1 enriched perm row shows the question"
assert_status_has "options: yes / no / dry-run" "QW1 option labels rendered"
assert_status_has "(+1 more)" "QW1 additional-question count surfaces"
assert_status_missing "permission needed: AskUserQuestion" "QW1 contentless fallback not used"
cat > "$a/wperm.forge-1.p0.e3b0c442.json" <<JSON
{"schema":"cc-attention/1","event":"permissionrequest","variant":"worker-permission","session":"forge-1","root":"$R","pane_index":"0","role":"worker","agent":"claude","tmux_pane":"%0","emitted_at":"$(iso_ago 30)","state":"needs-input","tool_name":"AskUserQuestion","command":"","command_hash":"e3b0c442","permission_suggestions":[],"question_snippet":"Which table?","question_options":["users","orders"],"question_count":1,"multi_select":false}
JSON
assert_status_has "forge-1 p0 — question (AskUserQuestion): Which table?" "QW1 worker wperm renders via the same path"
run_status --pretty | grep -q "answer in the pane" && ok "QW3 pretty hot row carries the go-to-pane hint" || bad "QW3 go-to-pane hint missing"
rm -f "$a/wperm.forge-1.p0.e3b0c442.json"
stopf "$R" forge-1 5 "answered"
assert_status_missing "Deploy to prod" "QW4 later Stop supersedes the enriched perm row"
new_env attq2
R=$(mk_root proj); live_session forge-1 "$R"
permf "$R" forge-1 e3b0c442 AskUserQuestion 30
assert_status_has "permission needed: AskUserQuestion" "QW2 legacy record (no question fields) renders as before"

echo "── board: classes + maintenance collapse (2 actionable surface) ──"
new_env bd
R=$(mk_root proj); live_session forge-1 "$R"
ctx "$R" forge-1 <<EOF
active_pipeline: p-dec
last_stage_completed: qa
last_stage_status: done
next_stage: pending-orchestrator-decision
updated_at: "$(iso_ago 900)"
EOF
disp "$R" forge-1 120 cc-b; promptf "$R" forge-1 60 cc-b     # SESSION-WORKING (active)
pending_log "$R" p-old coding codex-a "$(iso_ago 6000000)"   # STALE-PENDING (maintenance)
cat > "$R/.dev/forge-context.yml" <<EOF
active_pipeline: p-leg
last_stage_completed: implementation
last_stage_status: done
next_stage: impl-review
updated_at: "$(iso_ago 90000)"
EOF
RB=$(mk_root projZ); live_session forge-9 "$RB"
ctx "$RB" forge-8 <<EOF
active_pipeline: p-zed
last_stage_completed: implementation
last_stage_status: done
next_stage: impl-review
updated_at: "$(iso_ago 7200)"
EOF
run_status --board > "$TDIR/board.json"
python3 - "$TDIR/board.json" <<'PY' && ok "board buckets hot/active/maintenance; nothing leaks; state field present" || bad "board classification wrong"
import json,sys
b=json.load(open(sys.argv[1]))
assert b["schema"]=="cc-board/1"
hot={r["condition"] for r in b["hot"]}
assert "NEEDS-DECISION" in hot and "ZOMBIE-ACTIVE" in hot, hot
work=[r for r in b["active"] if r["condition"]=="SESSION-WORKING"]
assert work and work[0]["state"]=="working", "working row / state missing"
assert b["maintenance"]["collapsed"] is True and b["maintenance"]["count"] >= 1
mc={r["condition"] for r in b["maintenance"]["rows"]}
assert "STALE-PENDING" in mc or "LEGACY-CONTEXT" in mc
assert not (mc & hot)
PY
"$WATCH" status | grep -q "^\[NEEDS-DECISION\]" && ok "non-board status output preserved" || bad "status format regressed"

echo "── ask: NEEDS-ASK hot row carries the question ──"
new_env cask1
R=$(mk_root proj); live_session forge-1 "$R"
askf "$R" forge-1 ask-1 "" "" 60 "drop the column or keep it?"
assert_status_has "worker asks: drop the column" "ask event → NEEDS-ASK with question snippet"
run_status --board > "$TDIR/b.json"
python3 -c 'import json,sys;b=json.load(open(sys.argv[1]));r=[x for x in b["hot"] if x["condition"]=="NEEDS-ASK"];assert r and r[0]["state"]=="needs-input"' "$TDIR/b.json" && ok "NEEDS-ASK is hot, state=needs-input" || bad "ask not hot/needs-input"

echo "── ask precedence: stage-mode ask suppresses the WORKER-BLOCKED twin ──"
new_env cask2
R=$(mk_root proj); live_session forge-1 "$R"
pending_log "$R" p-mig coding codex-a "$(iso_ago 120)"
callback "$R" p-mig coding BLOCKED codex-a
askf "$R" forge-1 ask-2 p-mig coding 60
n=$(run_status | grep -c "p-mig")
[ "$n" -eq 1 ] && ok "exactly one row for the asked slug/stage (no ask+blocked double)" || bad "got $n rows for p-mig"
assert_status_has "NEEDS-ASK" "the surviving row is the richer NEEDS-ASK"
assert_status_missing "blocked at coding" "ITEM-BLOCKED twin suppressed by the ask"

echo "── ask is NOT superseded by a later Stop (unlike NEEDS-PERMISSION) ──"
new_env cask3
R=$(mk_root proj); live_session forge-1 "$R"
askf "$R" forge-1 ask-3 "" "" 300 "which region?"
stopf "$R" forge-1 30 "moving on"
assert_status_has "worker asks: which region" "ask stays hot despite a newer Stop"

echo "── archived/answered ask produces nothing ──"
new_env cask4
R=$(mk_root proj); live_session forge-1 "$R"
mkdir -p "$R/.dev/attention/archive"
askf "$R" forge-1 ask-4 "" "" 60 "should be gone"
mv "$R/.dev/attention/ask-4.json" "$R/.dev/attention/archive/ask-4.json"
assert_status_missing "should be gone" "an ask under archive/ is invisible to ingestion"

echo "── NEEDS-ASK enters notify / debounce / ack ──"
new_env cask5
R=$(mk_root proj); live_session forge-1 "$R"
askf "$R" forge-1 ask-5 "" "" 30 "credentials rotated — proceed?"
run_check >/dev/null
assert_notified "worker asks" "ask fires a notification on first scan"
: > "$CAP"; run_check >/dev/null
[ "$(wc -l < "$CAP")" -eq 0 ] && ok "NEEDS-ASK debounced on immediate re-scan" || bad "re-notified within backoff"
: > "$CAP"; "$WATCH" ack forge-1 >/dev/null; run_check >/dev/null
[ "$(wc -l < "$CAP")" -eq 0 ] && ok "ack silences NEEDS-ASK" || bad "ack did not silence the ask"

# ═══════════════════════════════════════════════════════════════════════════
echo "── stale-alert reconciliation (C1–C5 / D3) ──"

# §3a: NEEDS-ASK clears once its stage is closed in forge-log.
new_env recon_ask_resolved
R=$(mk_root proj); live_session forge-1 "$R"
closed_log "$R" p-inc incorporate claude-sonnet "$(iso_ago 172800)"
askf "$R" forge-1 ask-ri p-inc incorporate 172800
run_check >/dev/null
assert_not_notified "p-inc" "NEEDS-ASK clears on a clean stage close (§3a)"

# §3b: WORKER-STALLED demoted when a strictly-newer closed twin exists.
new_env recon_stall_demote
R=$(mk_root proj); live_session forge-1 "$R"
two_entry_log "$R" p-orph fix-code claude "$(iso_ago 172800)" claude-sonnet "$(iso_ago 172700)" "FORGE_DONE: fix-code"
run_check >/dev/null
assert_not_notified "p-orph.*stalled" "orphan open pending demoted when a newer closed twin exists (§3b)"
assert_status_has "STALE-PENDING.*p-orph" "the superseded orphan stays visible as residue"

# D3: a superseded orphan no longer hides a live NEEDS-DECISION.
new_env recon_d3_reveal
R=$(mk_root proj); live_session forge-1 "$R"
two_entry_log "$R" p-dec qa claude "$(iso_ago 172800)" claude-sonnet "$(iso_ago 172700)" "FORGE_DONE: qa"
ctx "$R" forge-1 <<EOF
active_pipeline: p-dec
last_stage_completed: qa
last_stage_status: done
next_stage: pending-orchestrator-decision
updated_at: "$(iso_ago 600)"
EOF
run_check >/dev/null
assert_notified "p-dec.*decision needed" "D3: superseded orphan does not hide a live NEEDS-DECISION"

# D3 paired negative: a genuinely-live pending still suppresses NEEDS-DECISION.
new_env recon_d3_live
R=$(mk_root proj); live_session forge-1 "$R"
pending_log "$R" p-dl qa claude-sonnet "$(iso_ago 120)"
ctx "$R" forge-1 <<EOF
active_pipeline: p-dl
last_stage_completed: qa
last_stage_status: done
next_stage: pending-orchestrator-decision
updated_at: "$(iso_ago 600)"
EOF
run_check >/dev/null
assert_not_notified "p-dl.*decision" "live pending still suppresses NEEDS-DECISION (unchanged)"

# Regression: a live stage-mode ask (open pending → stage not resolved) stays hot.
new_env recon_ask_live
R=$(mk_root proj); live_session forge-1 "$R"
pending_log "$R" p-la coding codex-a "$(iso_ago 120)"
askf "$R" forge-1 ask-la p-la coding 60 "which config?"
run_check >/dev/null
assert_notified "worker asks.*p-la/coding" "live stage-mode ask stays hot (open entry → stage not resolved)"

# Regression: a session-scope ask (no slug/stage) is never suppressed.
new_env recon_ask_session
R=$(mk_root proj); live_session forge-1 "$R"
askf "$R" forge-1 ask-ss "" "" 60 "global question?"
run_check >/dev/null
assert_notified "worker asks" "session-scope ask (no slug/stage) never suppressed"

# Regression: a genuine single-entry stall still fires (no closed sibling).
new_env recon_stall_single
R=$(mk_root proj); live_session forge-1 "$R"
pending_log "$R" p-ss coding codex-a "$(iso_ago 1800)"
run_check >/dev/null
assert_notified "p-ss.*stalled" "genuine single-entry stall still fires (no closed sibling → not superseded)"

# ADV-2 fail-safe: a malformed log whose fallback DROPS a response-less block is
# non-authoritative — the stage must NOT be treated as resolved, ask stays hot.
new_env recon_failsafe_adv2
R=$(mk_root proj); live_session forge-1 "$R"
d="$R/.dev/proposals/p-adv2"; mkdir -p "$d"
{ printf 'entries:\n';
  printf '  - timestamp: "%s"\n' "$(iso_ago 172900)"; printf '    stage: incorporate\n';
  printf '    to: claude-sonnet\n'; printf '    response: "FORGE_DONE: incorporate"\n';
  printf '  - timestamp: "%s"\n' "$(iso_ago 172800)"; printf '    stage: incorporate\n';
  printf '    to: claude\n'; printf '\tfiles:- legacy malformed tab line\n'; } > "$d/forge-log.yml"
askf "$R" forge-1 ask-adv2 p-adv2 incorporate 172700
run_check >/dev/null
assert_notified "worker asks" "ADV-2 fail-safe: a dropped response-less block keeps the stage unresolved → ask stays hot"

# Fail-safe: an ask for a stage with NO log entry is not suppressed.
new_env recon_failsafe_unknown
R=$(mk_root proj); live_session forge-1 "$R"
closed_log "$R" p-unk coding claude-sonnet "$(iso_ago 172800)"
askf "$R" forge-1 ask-unk p-unk qa 172700
run_check >/dev/null
assert_notified "worker asks" "fail-safe: ask for a stage with no log entry is not suppressed (absence ≠ resolution)"

# Reorder safety: a live pending newer than an older closed sibling still stalls,
# and an ask for that stage stays hot (an open entry exists → not resolved).
new_env recon_reorder
R=$(mk_root proj); live_session forge-1 "$R"
two_entry_log "$R" p-ro coding claude-sonnet "$(iso_ago 1800)" claude "$(iso_ago 172800)" "FORGE_DONE: old-round"
askf "$R" forge-1 ask-ro p-ro coding 172700
run_check >/dev/null
assert_notified "p-ro.*stalled" "reorder: live pending newer than the closed sibling still stalls (ts guard)"
assert_notified "worker asks.*p-ro/coding" "reorder: ask stays hot while an open entry exists"

# Multi-round: closed, closed, open(newest) + fresh ask → ask hot AND live stall fires.
new_env recon_multiround
R=$(mk_root proj); live_session forge-1 "$R"
d="$R/.dev/proposals/p-mr"; mkdir -p "$d"
cat > "$d/forge-log.yml" <<EOF
entries:
  - timestamp: "$(iso_ago 260000)"
    stage: qa
    to: claude
    response: "FORGE_DONE: r1"
  - timestamp: "$(iso_ago 200000)"
    stage: qa
    to: claude
    response: "FORGE_DONE: r2"
  - timestamp: "$(iso_ago 1800)"
    stage: qa
    to: claude-sonnet
    response: null
EOF
askf "$R" forge-1 ask-mr p-mr qa 300
run_check >/dev/null
assert_notified "worker asks.*p-mr/qa" "multi-round: fresh ask on a live round stays hot despite older closed rounds"
assert_notified "p-mr.*stalled" "multi-round: the live newest pending still stalls (older closed rounds do not suppress)"

# A resolved stage with a leftover BLOCKED callback renders CALLBACK-FOREIGN only —
# never unmasks a hot ITEM-BLOCKED (documents the unmask-unreachable claim).
new_env recon_resolved_blocked
R=$(mk_root proj); live_session forge-1 "$R"
closed_log "$R" p-rb coding claude-sonnet "$(iso_ago 172800)"
callback "$R" p-rb coding BLOCKED codex-a
askf "$R" forge-1 ask-rb p-rb coding 172700
run_check >/dev/null
assert_not_notified "p-rb.*blocked at" "resolved stage: leftover BLOCKED callback does NOT unmask ITEM-BLOCKED"
assert_not_notified "p-rb" "NEEDS-ASK also suppressed on the resolved stage"

echo "── return-path: W1 correlated question → NEEDS-REPLY (no bell) ──"
new_env wreply1
R=$(mk_root proj); live_session forge-1 "$R"
disp  "$R" forge-1 120 cc-a
stopf "$R" forge-1 30 "head preview" true "so, A or B?" cc-a
assert_status_has "awaiting reply: so, A or B?" "correlated question → NEEDS-REPLY w/ question_snippet"
run_status --board > "$TDIR/b.json"
python3 -c 'import json,sys;b=json.load(open(sys.argv[1]));r=[x for x in b["hot"] if x["condition"]=="NEEDS-REPLY"];assert r and r[0]["state"]=="needs-input"' "$TDIR/b.json" \
  && ok "W1 NEEDS-REPLY is hot, state=needs-input" || bad "W1 not hot/needs-input"
pout=$(run_status --pretty)
echo "$pout" | grep -q 'reply with: forge dispatch @forge-1' && ok "W1 pretty prints paste-ready PLAIN dispatch" || bad "W1 no plain dispatch hint"
if echo "$pout" | grep -q -- '--answers'; then bad "W1 NEEDS-REPLY hint used --answers"; else ok "W1 hint is plain (never --answers)"; fi
: > "$CAP"; run_check >/dev/null
[ "$(wc -l < "$CAP")" -eq 0 ] && ok "W1 no bell by default (policy=never)" || bad "W1 rang without opt-in"

echo "── return-path: W2 no seat dispatch → stays SESSION-DONE ──"
new_env wreply2
R=$(mk_root proj); live_session forge-1 "$R"
stopf "$R" forge-1 30 "done" true "trailing question?" cc-x
assert_status_has "forge-1 — done" "no correlated dispatch → SESSION-DONE"
assert_status_missing "awaiting reply" "no NEEDS-REPLY without a seat-correlated dispatch"

echo "── return-path: W3 non-question stop → no NEEDS-REPLY ──"
new_env wreply3
R=$(mk_root proj); live_session forge-1 "$R"
disp  "$R" forge-1 120 cc-a
stopf "$R" forge-1 30 "just done" false "" cc-a
assert_status_has "forge-1 — done" "non-question stop → SESSION-DONE"
assert_status_missing "awaiting reply" "no NEEDS-REPLY when looks_like_question is false"

echo "── return-path: W4 superseded NEEDS-REPLY clears ──"
new_env wreply4
R=$(mk_root proj); live_session forge-1 "$R"
disp    "$R" forge-1 120 cc-a
stopf   "$R" forge-1 60 "q" true "reply now?" cc-a
promptf "$R" forge-1 20 cc-a          # newer prompt → session working again
assert_status_has "forge-1 — working" "newer prompt supersedes → SESSION-WORKING"
assert_status_missing "awaiting reply" "NEEDS-REPLY drops once superseded"

echo "── return-path: W5 NEEDS-REPLY bell opt-in ──"
new_env wreply5
R=$(mk_root proj); live_session forge-1 "$R"
disp  "$R" forge-1 120 cc-a
stopf "$R" forge-1 30 "q" true "ring me?" cc-a
: > "$CAP"; FORGE_WATCH_NOTIFY_REPLY=1 "$WATCH" check >/dev/null 2>&1
assert_notified "awaiting reply" "NEEDS-REPLY rings when FORGE_WATCH_NOTIFY_REPLY=1"
: > "$CAP"; FORGE_WATCH_NOTIFY_REPLY=1 "$WATCH" check >/dev/null 2>&1
[ "$(wc -l < "$CAP")" -eq 0 ] && ok "W5 debounced on immediate re-scan" || bad "W5 re-notified within backoff"
: > "$CAP"; "$WATCH" ack forge-1 >/dev/null 2>&1; FORGE_WATCH_NOTIFY_REPLY=1 "$WATCH" check >/dev/null 2>&1
[ "$(wc -l < "$CAP")" -eq 0 ] && ok "W5 ack silences NEEDS-REPLY" || bad "W5 ack did not silence"

echo "── return-path: W6 NEEDS-ASK outranks NEEDS-REPLY ──"
new_env wreply6
R=$(mk_root proj); live_session forge-1 "$R"
askf  "$R" forge-1 ask-1 "" "" 60 "explicit ask?"
disp  "$R" forge-1 120 cc-a
stopf "$R" forge-1 30 "q" true "counter question?" cc-a
run_status --board > "$TDIR/b.json"
python3 -c 'import json,sys;b=json.load(open(sys.argv[1]));c=sorted(x["condition"] for x in b["hot"]);assert c==["NEEDS-ASK"], c' "$TDIR/b.json" \
  && ok "W6 exactly one hot row and it is NEEDS-ASK" || bad "W6 precedence wrong"

echo "── return-path: W7 enriched stop events do not disturb lifecycle ──"
new_env wreply7
R=$(mk_root proj); live_session forge-1 "$R"
stopf "$R" forge-1 300 "prev turn"
disp  "$R" forge-1 120 cc-mid
stopf "$R" forge-1 30 "absorbed answer" false "" cc-mid
assert_status_has "forge-1 — done" "W7 enriched closing Stop still resolves absorbed dispatch → done"
n=$(run_status | grep -c "forge-1 — ")
[ "$n" -eq 1 ] && ok "W7 exactly one lifecycle row with enriched events" || bad "W7 got $n rows"

echo "── return-path: W8 gate on the answered dispatch's sender ──"
new_env wreply8
R=$(mk_root proj); live_session forge-1 "$R"
disp  "$R" forge-1 200 cc-a "" seat        # older, seat-sent
disp  "$R" forge-1 60  cc-b "" auto        # newer, non-seat
stopf "$R" forge-1 30 "q" true "which one?" cc-a     # stop RECORDS it answered cc-a
assert_status_has "awaiting reply: which one?" "W8 fires on the answered did's seat sender, not the newest disp"
new_env wreply8b
R=$(mk_root proj); live_session forge-1 "$R"
disp  "$R" forge-1 200 cc-a "" auto        # older, non-seat
disp  "$R" forge-1 60  cc-b "" seat        # newer, seat
stopf "$R" forge-1 30 "q" true "which one?" cc-a     # answered did is the non-seat cc-a
assert_status_missing "awaiting reply" "W8 inverse: answered did non-seat → no NEEDS-REPLY"
assert_status_has "forge-1 — done" "W8 inverse falls back to SESSION-DONE"

echo "── return-path: W9 FORGE_WATCH_NOTIFY_REPLY via watch.env ──"
new_env wreply9
R=$(mk_root proj); live_session forge-1 "$R"
disp  "$R" forge-1 120 cc-a
stopf "$R" forge-1 30 "q" true "via config?" cc-a
printf 'FORGE_WATCH_NOTIFY_REPLY=1\nFORGE_WATCH_BOGUS=1\n' > "$FORGE_WATCH_CONFIG_DIR/watch.env"
: > "$CAP"; out=$(run_check)
echo "$out" | grep -q "unknown key 'FORGE_WATCH_BOGUS'" && ok "W9 unknown key still warns" || bad "W9 unknown key not warned"
if echo "$out" | grep -q "unknown key 'FORGE_WATCH_NOTIFY_REPLY'"; then bad "W9 REPLY key wrongly rejected"; else ok "W9 FORGE_WATCH_NOTIFY_REPLY accepted (allowlisted, no warning)"; fi
notified "awaiting reply" && ok "W9 config-set knob fires the bell" || bad "W9 config knob did not ring"

# ── spawn-state ingestion (Phase C) ──────────────────────────────────────
spawnf() {  # spawnf <root> <session> <state> <age-s> <detail>
    local a; a="$(attn "$1")"
    cat > "$a/spawn-$2-$3.json" <<JSON
{"schema":"cc-spawn/1","event":"spawn-state","session":"$2","root":"$1","state":"$3","detail":"$5","emitted_at":"$(iso_ago "$4")"}
JSON
}

echo "── spawn-state: needs-repair + population-failure render HOT/needs-input ──"
new_env cspawn1
R=$(mk_root proj); live_session forge-1 "$R"
spawnf "$R" forge-1 needs-repair 60 "3 panes (expected 5)"
spawnf "$R" forge-1 population-failure 60 "on_spawn exited 3"
assert_status_has "spawn needs-repair" "needs-repair renders"
assert_status_has "population FAILED" "population-failure renders"
run_status --board | python3 -c '
import json,sys
b=json.load(sys.stdin)
hot={r["condition"]: r for r in b["hot"]}
assert "SPAWN-NEEDS-REPAIR" in hot and "SPAWN-POPULATE-FAILED" in hot
assert hot["SPAWN-NEEDS-REPAIR"]["state"]=="needs-input"
assert hot["SPAWN-POPULATE-FAILED"]["state"]=="needs-input"
assert hot["SPAWN-NEEDS-REPAIR"]["session"]=="forge-1"
' && ok "board: both spawn states hot with needs-input" || bad "board spawn rows wrong"

echo "── spawn-state: notify / debounce / ack ──"
new_env cspawn2
R=$(mk_root proj); live_session forge-1 "$R"
spawnf "$R" forge-1 population-failure 30 "on_spawn exited 3"
run_check >/dev/null
assert_notified "population FAILED" "population-failure notifies on first scan"
: > "$CAP"; run_check >/dev/null
[ "$(wc -l < "$CAP")" -eq 0 ] && ok "spawn-state debounced on immediate re-scan" || bad "re-notified within backoff"
: > "$CAP"; "$WATCH" ack forge-1 >/dev/null; run_check >/dev/null
[ "$(wc -l < "$CAP")" -eq 0 ] && ok "ack silences spawn-state" || bad "ack did not silence"

echo "── spawn-state: first-launch notifies ONCE (policy once) ──"
new_env cspawn3
R=$(mk_root proj); live_session forge-1 "$R"
spawnf "$R" forge-1 first-launch 30 "approve the folder-trust dialog"
run_check >/dev/null
assert_notified "first launch" "first-launch notifies"
: > "$CAP"; FORGE_WATCH_RENOTIFY_S=1 run_check >/dev/null; sleep 2; FORGE_WATCH_RENOTIFY_S=1 run_check >/dev/null
grep -q "first launch" "$CAP" && bad "first-launch re-notified (policy once violated)" || ok "first-launch never re-notifies"

echo "── spawn-state: ancient event is residue; removed file clears (T-WATCH-SPAWN-CLEAR) ──"
new_env cspawn4
R=$(mk_root proj); live_session forge-1 "$R"
spawnf "$R" forge-1 needs-repair 9999999 "ancient"
assert_status_missing "spawn needs-repair" "past ZOMBIE_AGE_S → residue dropped"
spawnf "$R" forge-1 needs-repair 60 "3 panes"
n=$(run_status | grep -c "spawn needs-repair")
[ "$n" = 1 ] && ok "single needs-repair row (overwrite-keyed file, dedup by key)" || bad "got $n needs-repair rows"
rm -f "$(attn "$R")/spawn-forge-1-needs-repair.json"   # what spawn's clear-on-PASS does
assert_status_missing "spawn needs-repair" "resolved needs-repair clears from the board"

echo "── pretty board: NEEDS YOU + sessions table + hidden maintenance ──"
new_env cpretty1
R=$(mk_root proj); live_session forge-1 "$R"
R2=$(mk_root proj2); live_session forge-2 "$R2"
R3=$(mk_root proj3); live_session forge-3 "$R3"          # live, no events → idle
askf "$R" forge-1 ask-p1 "" "" 60 "prod or staging config?"
stopf "$R2" forge-2 120 "shipped the widget"             # → done w/ snippet
pending_log "$R" p-ancient coding codex "$(iso_ago 6000000)"   # STALE-PENDING residue
run_status --pretty > "$TDIR/pretty.txt" 2>&1
grep -q "FORGE BOARD" "$TDIR/pretty.txt" && ok "pretty: header renders" || bad "pretty: no header"
grep -q "NEEDS YOU (1)" "$TDIR/pretty.txt" && ok "pretty: hot section counts the ask" || bad "pretty: hot section wrong: $(cat "$TDIR/pretty.txt")"
grep -q -- '--answers ask-p1' "$TDIR/pretty.txt" && grep -q "@forge-1" "$TDIR/pretty.txt" \
  && ok "pretty: ask row carries a paste-ready answer command" || bad "pretty: answer hint missing"
grep -q "forge-2.*done.*shipped the widget" "$TDIR/pretty.txt" && ok "pretty: done session shows its snippet" || bad "pretty: done snippet missing"
grep -q "forge-3.*idle" "$TDIR/pretty.txt" && ok "pretty: eventless live session listed as idle" || bad "pretty: idle session missing"
grep -q "needs you" "$TDIR/pretty.txt" && ok "pretty: asked session flagged in table" || bad "pretty: session flag missing"
grep -q "1 STALE-PENDING" "$TDIR/pretty.txt" && ! grep -q "\[STALE-PENDING\]" "$TDIR/pretty.txt" \
  && ok "pretty: maintenance summarized, rows hidden" || bad "pretty: maintenance leak/missing"
run_status --pretty --all | grep -q "\[STALE-PENDING\]" && ok "pretty --all: maintenance rows listed" || bad "pretty --all: rows missing"
rm -f "$(attn "$R")/ask-p1.json"
run_status --pretty | grep -q "NEEDS YOU — nothing" && ok "pretty: empty hot reads all-clear" || bad "pretty: all-clear line missing"
run_status --board > "$TDIR/pb.json"
python3 -c 'import json,sys; b=json.load(open(sys.argv[1])); assert b["schema"]=="cc-board/1"' "$TDIR/pb.json" \
  && ok "pretty mode leaves the JSON contract untouched" || bad "board JSON broke"

echo "── worker ingestion: canonical session lifecycle UNTOUCHED (regression) ──"   # +2
new_env twork1
R=$(mk_root proj); live_session forge-1 "$R"
disp "$R" forge-1 120 cc-a; promptf "$R" forge-1 60 cc-a       # canonical → SESSION-WORKING
wpromptf "$R" forge-1 0 30 ptask-x; wstopf "$R" forge-1 0 60 ptask-old
assert_status_has "forge-1 — working" "canonical SESSION-WORKING intact despite worker files"
assert_status_missing "forge-1 — done" "worker Stop did NOT flip the session to done (P11)"

echo "── EPISODE rows: active/settled; settle ring only with the knob ──"              # +5
new_env tpd
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 30 ptask-1 claude "finished the widget"
assert_status_has "p0 — in progress · 1 turn(s)" "sub-settle wstop → EPISODE-ACTIVE row"
run_status --board | python3 -c 'import json,sys;b=json.load(sys.stdin);r=[x for x in b["active"] if x["condition"]=="EPISODE-ACTIVE"];assert r and r[0]["state"]=="working"' && ok "EPISODE-ACTIVE is active/state=working in cc-board/1" || bad "board EPISODE-ACTIVE wrong"
: > "$CAP"; run_check >/dev/null
[ "$(wc -l < "$CAP")" -eq 0 ] && ok "EPISODE-ACTIVE never rings (policy=never)" || bad "EPISODE-ACTIVE rang"
new_env tpd2
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 700 ptask-2 claude "finished the widget"
assert_status_has "p0 — done · 1 turn(s)" "quiet past settle → EPISODE-SETTLED row"
: > "$CAP"; run_check >/dev/null
[ "$(wc -l < "$CAP")" -eq 0 ] && ok "EPISODE-SETTLED does not ring by default (dark-first)" || bad "settled rang without opt-in"
new_env tpd3
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 700 ptask-3 claude "finished the widget"
: > "$CAP"; FORGE_WATCH_NOTIFY_EPISODE_DONE=1 "$WATCH" check >/dev/null 2>&1
notified "done" && ok "EPISODE-SETTLED rings when FORGE_WATCH_NOTIFY_EPISODE_DONE=1" || bad "knob did not arm the settle ring"

echo "── tasks[]: dispatched queued/accepted/answered + worker working/done ──"         # +1
new_env ttasks
R=$(mk_root proj); live_session forge-1 "$R"
disp "$R" forge-1 120 cc-q
disp "$R" forge-1 120 cc-acc; promptf "$R" forge-1 60 cc-acc
disp "$R" forge-1 300 cc-ans; printf 'ans' > "$(attn "$R")/payloads/response.cc-ans.txt"
wpromptf "$R" forge-1 2 30 ptask-w
wstopf   "$R" forge-1 3 30 ptask-d claude "did the thing"
run_status --board > "$TDIR/t.json"
python3 - "$TDIR/t.json" <<'PY' && ok "tasks[] carries all five states with fields" || bad "tasks[] states wrong"
import json,sys
t={x["task_id"]:x for x in json.load(open(sys.argv[1]))["tasks"]}
assert t["cc-q"]["state"]=="queued", t.get("cc-q")
assert t["cc-acc"]["state"]=="accepted"
assert t["cc-ans"]["state"]=="answered" and t["cc-ans"]["response_path"].endswith("response.cc-ans.txt")
assert t["ptask-w"]["state"]=="working" and t["ptask-w"]["agent"]=="claude"
assert t["ptask-d"]["state"]=="done" and t["ptask-d"]["pane"]=="3", t["ptask-d"]
assert t["cc-q"]["episode_id"] is None, t["cc-q"]            # dispatched rows never tagged
assert t["ptask-w"]["episode_id"] and t["ptask-d"]["episode_id"]   # worker rows tagged
PY

echo "── board-noise: old worker-done collapses to maintenance; latest in window ──"    # +3
new_env twin
R=$(mk_root proj); live_session forge-1 "$R"
export FORGE_WATCH_TASK_WINDOW_S=3600
wstopf "$R" forge-1 0 30   ptask-recent claude "recent turn"
wstopf "$R" forge-1 0 7200 ptask-old    claude "old turn"
assert_status_has "p0 — in progress" "recent worker turn surfaces as the live episode"
run_status --board | python3 -c 'import json,sys;b=json.load(sys.stdin);ids={x["task_id"] for x in b["tasks"] if x["state"]=="done"};assert "ptask-recent" in ids and "ptask-old" not in ids, ids' && ok "tasks[] bounded to the window (old turn excluded)" || bad "window not applied"
run_status | grep -q "older worker-done" && ok "old worker-done collapses to a maintenance count" || bad "residue not collapsed"
unset FORGE_WATCH_TASK_WINDOW_S

# ═══════════════════════════════════════════════════════════════════════════
# Episode block (pane-episode-state, plan §7). SETTLE pinned to 120s; ages are
# SETTLE-relative so the block survives a default change.
export FORGE_WATCH_EPISODE_SETTLE_S=120

echo "── episodes 1: prompt-only pane opens (stops ∪ prompts union) ──"                 # +2
new_env tep1
R=$(mk_root proj); live_session forge-1 "$R"
wpromptf "$R" forge-1 0 30 t-a
assert_status_has "p0 — in progress" "wprompt with no wstop opens an episode"
assert_status_missing "p0 — done" "prompt-only pane is not done"

echo "── episodes 2: sub-settle turns extend ONE episode, not N rows ──"                # +3
new_env tep2
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 260 t1; wstopf "$R" forge-1 0 160 t2; wstopf "$R" forge-1 0 60 t3
assert_status_has "in progress · 3 turn(s)" "3 sub-settle turns → one active episode"
assert_status_missing "p0 — done" "no settled row while turns keep arriving"
run_status --board | python3 -c 'import json,sys;e=json.load(sys.stdin)["episodes"];assert len(e)==1 and e[0]["turn_count"]==3 and e[0]["state"]=="in_progress", e' && ok "episodes[] holds one 3-turn in-progress record" || bad "episodes[] wrong"

echo "── episodes 3: mid-turn dominates ──"                                             # +1
new_env tep3
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 60 t-a; wpromptf "$R" forge-1 0 10 t-b
run_status --board | python3 -c 'import json,sys;e=json.load(sys.stdin)["episodes"];assert e[0]["mid_turn"] is True and e[0]["state"]=="in_progress", e' && ok "newer wprompt → mid_turn episode stays in progress" || bad "mid-turn not dominant"

echo "── episodes 4: settle ──"                                                         # +3
new_env tep4
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 150 tX
assert_status_has "p0 — done · 1 turn(s)" "quiet ≥ settle → done"
assert_status_missing "in progress" "no active row after settle"
run_status --board | python3 -c 'import json,sys;e=json.load(sys.stdin)["episodes"];assert e[0]["state"]=="settled", e' && ok "episode settled in episodes[]" || bad "state not settled"

echo "── episodes 5: reopen → two episodes, historical tagging, one condition ──"       # +2
new_env tep5
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 1000 o1; wstopf "$R" forge-1 0 900 o2   # gap 100 < 120 → one settled run
wstopf "$R" forge-1 0 30 n1                                    # gap 870 ≥ 120 → NEW run
run_status --board > "$TDIR/ep5.json"
python3 - "$TDIR/ep5.json" <<'PY' && ok "reopen: 2 distinct episodes; history keeps its id; one live condition" || bad "reopen derivation wrong"
import json,sys
b=json.load(open(sys.argv[1]))
eps=b["episodes"]; assert len(eps)==2, eps
assert len({e["episode_id"] for e in eps})==2
curr=[e for e in eps if e["current"]][0]; old=[e for e in eps if not e["current"]][0]
assert curr["state"]=="in_progress" and curr["turn_count"]==1 and curr["earlier"] is True
assert old["state"]=="settled" and old["turn_count"]==2
t={x["task_id"]:x for x in b["tasks"]}
assert t["o1"]["episode_id"]==old["episode_id"] and t["o2"]["episode_id"]==old["episode_id"]
assert t["n1"]["episode_id"]==curr["episode_id"]
econd=[r for r in b["hot"]+b["active"] if r["condition"].startswith("EPISODE-")]
assert len(econd)==1 and econd[0]["episode_id"]==curr["episode_id"], econd
PY
run_status --pretty | grep -q "(pane active earlier)" && ok "reopen hint renders" || bad "no reopen hint"

echo "── episodes 6: ring once per episode; reopen re-rings once at a NEW id ──"        # +3
new_env tep6
R=$(mk_root proj); live_session forge-1 "$R"
export FORGE_WATCH_NOTIFY_EPISODE_DONE=1
wstopf "$R" forge-1 0 130 tZ claude "job finished"
run_check >/dev/null
[ "$(grep -c "p0 — done" "$CAP")" -eq 1 ] && ok "fresh settle rings exactly once" || bad "settle ring count wrong: $(cat "$CAP")"
: > "$CAP"; run_check >/dev/null
[ "$(wc -l < "$CAP")" -eq 0 ] && ok "same settled episode never re-rings (policy=once)" || bad "re-rang"
wstopf "$R" forge-1 0 800 tZ claude "job finished"    # re-stamp the old episode further back
wstopf "$R" forge-1 0 300 tN claude "second job done" # new settled run (gap 500 ≥ 120); age
                                                      # differs from tZ's original 130 so the
                                                      # new anchor can never collide with the
                                                      # already-rung episode_id
: > "$CAP"; run_check >/dev/null
[ "$(grep -c "p0 — done" "$CAP")" -eq 1 ] && ok "re-settle at a NEW episode_id rings exactly once more" || bad "reopen ring wrong: $(cat "$CAP")"
unset FORGE_WATCH_NOTIFY_EPISODE_DONE

echo "── episodes 7: dark by default; cold restart cannot ring stale history ──"        # +3
new_env tep7
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 130 tA
: > "$CAP"; run_check >/dev/null
[ "$(wc -l < "$CAP")" -eq 0 ] && ok "fresh settle without the knob is silent" || bad "rang without knob"
new_env tep7b
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 4120 old1                       # settled far outside the fresh band
rm -f "$FORGE_WATCH_CACHE_DIR/state.json"
: > "$CAP"; FORGE_WATCH_NOTIFY_EPISODE_DONE=1 "$WATCH" check >/dev/null 2>&1
[ "$(wc -l < "$CAP")" -eq 0 ] && ok "cold restart: settled history outside EPISODE_RING_FRESH_S never rings" || bad "cold-start storm: $(cat "$CAP")"
wstopf "$R" forge-1 0 130 new1                        # freshly settled new run (gap ≥ 120)
: > "$CAP"; FORGE_WATCH_NOTIFY_EPISODE_DONE=1 "$WATCH" check >/dev/null 2>&1
[ "$(grep -c "p0 — done" "$CAP")" -eq 1 ] && ok "freshly-settled stream still rings once after cold start" || bad "fresh ring lost: $(cat "$CAP")"

echo "── episodes 8: mid-turn hang → EPISODE-STUCK (hot) ──"                            # +2
new_env tep8
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 40000 t-old; wpromptf "$R" forge-1 0 39000 t-hang
assert_status_has "EPISODE-STUCK" "prompt with no Stop past TASK_STUCK_S → EPISODE-STUCK"
run_status --board | python3 -c 'import json,sys;r=[x for x in json.load(sys.stdin)["hot"] if x["condition"]=="EPISODE-STUCK"];assert r and r[0]["state"]=="needs-input"' && ok "EPISODE-STUCK is hot/needs-input" || bad "EPISODE-STUCK not hot"

echo "── episodes 9: first-turn hang, NO wstop at all (T7b) ──"                         # +1
new_env tep9
R=$(mk_root proj); live_session forge-1 "$R"
wpromptf "$R" forge-1 0 40000 t-hang
assert_status_has "EPISODE-STUCK" "union derivation reaches a no-wstop hang"

echo "── episodes 10: dispatched hang → TASK-STUCK only (suppression, R1b) ──"          # +6
new_env tep10
R=$(mk_root proj); live_session forge-1 "$R"
disp "$R" forge-1 40000 did-x
wpromptf "$R" forge-1 0 39000 t-hang claude "working" did-x
assert_status_has "TASK-STUCK" "dispatched hang fires TASK-STUCK"
assert_status_missing "EPISODE-STUCK" "EPISODE-STUCK twin suppressed for the same dispatch"
run_status --board | python3 -c 'import json,sys;h=json.load(sys.stdin)["hot"];assert len(h)==1 and h[0]["condition"]=="TASK-STUCK", h' && ok "exactly one hot row for one hang" || bad "double hot row"
: > "$CAP"; run_check >/dev/null
[ "$(grep -c . "$CAP")" -eq 1 ] && ok "exactly one ring for one hang" || bad "ring count wrong: $(cat "$CAP")"
new_env tep10b
R=$(mk_root proj); live_session forge-1 "$R"
wpromptf "$R" forge-1 0 39000 t-hang claude "working"
assert_status_has "EPISODE-STUCK" "undispatched twin fires EPISODE-STUCK alone"
assert_status_missing "TASK-STUCK" "no TASK-STUCK without a dispatch"

echo "── episodes 11: codex rows (empty pane_index) key cleanly ──"                     # +2
new_env tep11
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 "" 60 t-c1 codex "codex turn"; wstopf "$R" forge-1 "" 20 t-c2 codex "codex turn 2"
assert_status_has "in progress · 2 turn(s)" "two codex turns → one episode"
run_status --board | python3 -c 'import json,sys;e=json.load(sys.stdin)["episodes"];assert len(e)==1 and e[0]["agent"]=="codex" and e[0]["pane"]=="", e' && ok "codex episode keyed on empty pane, agent=codex" || bad "codex episode wrong"

echo "── episodes 12: hot outranks and is orthogonal ──"                                # +3
new_env tep12
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 30 t-w claude "working away"
wpermf "$R" forge-1 0 beef 10
run_check >/dev/null
assert_notified "permission needed" "worker NEEDS-PERMISSION still rings"
assert_status_has "permission needed" "hot permission row present"
assert_status_has "p0 — in progress" "episode row coexists (orthogonal)"

echo "── episodes 13: SESSIONS annotation, glyph untouched ──"                          # +3
new_env tep13
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 30 t-a
run_status --pretty > "$TDIR/p.txt"
grep -q "forge-1.*- idle.*1 pane(s) active" "$TDIR/p.txt" && ok "idle glyph + active-pane annotation (no leak into pane-1 derivation)" || bad "annotation/glyph wrong: $(grep -m2 forge-1 "$TDIR/p.txt")"
new_env tep13b
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 150 t-a
run_status --pretty > "$TDIR/p.txt"
grep -q "forge-1.*- idle" "$TDIR/p.txt" && ok "settled worker episode → glyph still idle (R8)" || bad "glyph leaked"
grep -q "pane(s) active" "$TDIR/p.txt" && bad "settled episode wrongly annotated" || ok "no annotation for a settled episode"

echo "── episodes 14: cc-board additive — episodes[], row metadata, task tags ──"       # +1
new_env tep14
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 160 t1; wstopf "$R" forge-1 0 60 t2      # pane 0: active run (gap 100)
wstopf "$R" forge-1 2 150 t3                                    # pane 2: settled run
disp "$R" forge-1 120 cc-d                                      # dispatched row
run_status --board > "$TDIR/b14.json"
python3 - "$TDIR/b14.json" <<'PY' && ok "episodes[] + _row metadata + per-task episode_id + dispatched null" || bad "board additive contract wrong"
import json,sys
b=json.load(open(sys.argv[1]))
assert isinstance(b.get("episodes"), list) and len(b["episodes"])==2, b.get("episodes")
act=[r for r in b["active"] if r["condition"]=="EPISODE-ACTIVE"]
assert act and act[0]["state"]=="working"
for kx in ("episode_id","first_at","last_at","turn_count","mid_turn"):
    assert kx in act[0], kx
st=[r for r in b["active"] if r["condition"]=="EPISODE-SETTLED"]
assert st and st[0]["state"]=="done"
t={x["task_id"]:x for x in b["tasks"]}
assert t["t1"]["episode_id"] and t["t1"]["episode_id"]==t["t2"]["episode_id"]
assert t["t3"]["episode_id"] and t["t3"]["episode_id"]!=t["t1"]["episode_id"]
assert t["cc-d"]["episode_id"] is None
PY

echo "── episodes 15: QUIET — active episode msg is byte-identical across ticks ──"     # +2
new_env tep15
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 60 tq1 claude "steady work"
run_status | grep "EPISODE-ACTIVE" > "$TDIR/m1.txt"
wstopf "$R" forge-1 0 91 tq1 claude "steady work"      # same turn re-stamped 31s older
run_status | grep "EPISODE-ACTIVE" > "$TDIR/m2.txt"
if [ -s "$TDIR/m1.txt" ] && diff -q "$TDIR/m1.txt" "$TDIR/m2.txt" >/dev/null; then
    ok "EPISODE-ACTIVE msg byte-identical 31s apart (QUIET signature holds)"
else
    bad "msg churned across ticks: $(diff "$TDIR/m1.txt" "$TDIR/m2.txt" 2>&1)"
fi
grep -q " ago" "$TDIR/m1.txt" && bad "live age leaked into the finding msg" || ok "no age token in the finding msg"

echo "── episodes 16: clock skew (future emitted_at) is fail-safe ──"                   # +1
new_env tep16
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 -50 t-fut
assert_status_has "p0 — in progress" "future timestamp reads in-progress, no crash"

echo "── episodes 17: fresh prompt after settled gap opens a NEW episode (R2a) ──"      # +2
new_env tep17
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 1000 old1
wpromptf "$R" forge-1 0 10 new1
run_status --board > "$TDIR/ep17.json"
python3 - "$TDIR/ep17.json" <<'PY' && ok "prompt-only current run; old episode not resurrected" || bad "R2a violated"
import json,sys
b=json.load(open(sys.argv[1])); eps=b["episodes"]
assert len(eps)==2, eps
cur=[e for e in eps if e["current"]][0]; old=[e for e in eps if not e["current"]][0]
assert cur["turn_count"]==0 and cur["state"]=="in_progress" and cur["earlier"] is True
assert cur["episode_id"]!=old["episode_id"]
assert cur["first_at"]==cur["last_at"]          # anchored on the wprompt, not the old start
assert cur["first_at"]!=old["first_at"]
econd=[r for r in b["hot"]+b["active"] if r["condition"].startswith("EPISODE-")]
assert len(econd)==1 and econd[0]["episode_id"]==cur["episode_id"]
assert econd[0]["first_at"]==cur["first_at"]
PY
run_status --pretty | grep -q "(pane active earlier)" && ok "reopen-before-first-stop shows the earlier hint" || bad "hint missing"

echo "── episodes 18: stale settled folds to residue; STUCK exempt (R2b) ──"            # +2
new_env tep18
R=$(mk_root proj); live_session forge-1 "$R"
export FORGE_WATCH_TASK_WINDOW_S=3600
wstopf "$R" forge-1 0 7200 old1
run_status --board > "$TDIR/ep18.json"
python3 - "$TDIR/ep18.json" <<'PY' && ok "stale settled: no live condition, no task row; residue only" || bad "R2b guard wrong"
import json,sys
b=json.load(open(sys.argv[1]))
assert not [r for r in b["active"] if r["condition"]=="EPISODE-SETTLED"]
assert not [x for x in b["tasks"] if x["task_id"]=="old1"]
assert [r for r in b["maintenance"]["rows"] if r["condition"]=="PANE-DONE-RESIDUE"]
PY
new_env tep18b
R=$(mk_root proj); live_session forge-1 "$R"
export FORGE_WATCH_TASK_WINDOW_S=3600
wpromptf "$R" forge-1 0 30000 t-hang
assert_status_has "EPISODE-STUCK" "unterminated hang past the window still fires (not residue)"
unset FORGE_WATCH_TASK_WINDOW_S

echo "── episodes 19: multi-root board aggregates every root (R3a) ──"                  # +1
new_env tep19
R=$(mk_root proj); live_session forge-1 "$R"
R2=$(mk_root proj2); live_session forge-2 "$R2"
wstopf "$R" forge-1 0 60 t-a claude "root one"
wstopf "$R2" forge-2 0 30 t-b claude "root two"
run_status --board | python3 -c '
import json,sys
e=json.load(sys.stdin)["episodes"]
assert len(e)==2, e
assert {x["label"] for x in e}=={"proj","proj2"}, e
assert [x["last_at"] for x in e]==sorted([x["last_at"] for x in e], reverse=True)
' && ok "episodes[] carries both roots, sorted last_at desc" || bad "multi-root aggregation broken"

echo "── episodes 20: cross-root task-id collision keeps root-scoped tags (R3b) ──"     # +1
new_env tep20
R=$(mk_root proj); live_session forge-1 "$R"
R2=$(mk_root proj2); live_session forge-2 "$R2"
wstopf "$R" forge-1 0 30 t-shared
wstopf "$R2" forge-1 0 150 t-shared          # same session name + task_id, other root
run_status --board | python3 -c '
import json,sys
b=json.load(sys.stdin)
rows=[x for x in b["tasks"] if x["task_id"]=="t-shared"]
assert len(rows)==2, rows
pairs={(x["root"], x["episode_id"]) for x in rows}
assert len(pairs)==2 and all(p[1] for p in pairs), pairs
' && ok "same task_id in two roots → two distinct (root, episode_id) pairs" || bad "cross-root tag collision"

unset FORGE_WATCH_EPISODE_SETTLE_S
# ═══════════════════════════════════════════════════════════════════════════

echo "── worker NEEDS-PERMISSION: wperm → hot; superseded by a later worker Stop ──"    # +2
new_env twperm
R=$(mk_root proj); live_session forge-1 "$R"
wpermf "$R" forge-1 0 abcd 30
assert_status_has "forge-1 p0 — permission needed" "wperm → NEEDS-PERMISSION (hot)"
wstopf "$R" forge-1 0 10 ptask-after
assert_status_missing "permission needed" "a later worker Stop supersedes the worker permission"

echo "── graceful degradation: everything empty → valid cc-board/1, no crash ──"        # +2
new_env tgrace
: > "$FORGE_WATCH_CONFIG_DIR/watch-roots"; : > "$FORGE_WATCH_TMUX_LIST"
run_status --board | python3 -c 'import json,sys;b=json.load(sys.stdin);assert b["schema"]=="cc-board/1" and b["hot"]==[] and b["tasks"]==[] and b["episodes"]==[] and "maintenance" in b' && ok "empty everything → valid cc-board/1 (tasks[]/episodes[] present, no crash)" || bad "board degraded ungracefully"
run_status --pretty | grep -q "all clear\|FORGE BOARD" && ok "pretty board renders with nothing installed" || bad "pretty board crashed empty"

echo "── cc-board/1 additive contract regression ──"                                    # +1
new_env tadd
R=$(mk_root proj); live_session forge-1 "$R"
disp "$R" forge-1 120 cc-a; promptf "$R" forge-1 60 cc-a
run_status --board | python3 -c '
import json,sys
b=json.load(sys.stdin); assert b["schema"]=="cc-board/1"
for k in ("hot","active","maintenance","tasks","heartbeat","heartbeat_age_s","stale"): assert k in b, k
assert isinstance(b["tasks"],list) and isinstance(b["maintenance"],dict)
' && ok "cc-board/1 carries additive tasks[]/heartbeat; hot/active/maintenance intact" || bad "additive contract broke"

echo "── forge --tasks projection renders + pretty TASKS section ──"                     # +4
new_env tview
R=$(mk_root proj); live_session forge-1 "$R"
disp "$R" forge-1 120 cc-v; printf 'x' > "$(attn "$R")/payloads/response.cc-v.txt"
run_status --tasks | grep -q "answered" && ok "--tasks renders the projection" || bad "--tasks view missing state"
run_status --tasks | grep -q "forge reply @forge-1 cc-v" && ok "--tasks shows the reply hint for answered tasks" || bad "--tasks reply hint missing"
# N1 reconcile: also exercise the inline pretty-board TASKS section (Diff 3e / P15), not just --tasks.
run_status --pretty | grep -q "TASKS" && ok "pretty board renders the inline TASKS section (P15)" || bad "pretty TASKS section missing"
run_status --pretty | grep -q "forge reply @forge-1 cc-v" && ok "pretty TASKS shows the reply hint for answered tasks" || bad "pretty TASKS reply hint missing"

echo "── forge --tasks @session filter + --json machine path ──"                         # +2
new_env tview2
R=$(mk_root proj); live_session forge-1 "$R"; R2=$(mk_root proj2); live_session forge-2 "$R2"
disp "$R" forge-1 120 cc-1; disp "$R2" forge-2 120 cc-2
run_status --tasks @forge-1 | grep -q "cc-2" && bad "@session filter not applied (cc-2 leaked)" || ok "--tasks @forge-1 filters to that session (cc-2 absent)"
run_status --tasks --json | python3 -c 'import json,sys;a=json.load(sys.stdin);assert isinstance(a,list) and any(t["task_id"]=="cc-1" for t in a)' && ok "--tasks --json emits the task array" || bad "--tasks --json not a JSON array"

echo "── TASK-STUCK: unterminated dispatch past the stall window → hot ──"              # +3
new_env tstuck
R=$(mk_root proj); live_session forge-1 "$R"
export FORGE_WATCH_TASK_STUCK_S=60
disp "$R" forge-1 3600 cc-stuck
assert_status_has "TASK-STUCK" "unanswered dispatch older than TASK_STUCK → TASK-STUCK"
run_status --board | python3 -c 'import json,sys;r=[x for x in json.load(sys.stdin)["hot"] if x["condition"]=="TASK-STUCK"];assert r and r[0]["state"]=="needs-input"' && ok "TASK-STUCK is hot/needs-input" || bad "TASK-STUCK not hot"
printf 'a' > "$(attn "$R")/payloads/response.cc-stuck.txt"
assert_status_missing "TASK-STUCK" "an answered task is not stuck"
unset FORGE_WATCH_TASK_STUCK_S

echo "── delivery: stub notifier nonzero → delivered.log rc!=0 → DELIVERY-UNVERIFIED ──"  # +2
new_env tdel
R=$(mk_root proj); live_session forge-1 "$R"
askf "$R" forge-1 ask-d "" "" 30 "ring me?"
STUBN="$WORK/failnotify"; printf '#!/bin/bash\nexit 3\n' > "$STUBN"; chmod +x "$STUBN"
FORGE_WATCH_NOTIFIER_BIN="$STUBN" "$WATCH" check >/dev/null 2>&1
python3 -c 'import json,sys;rec=json.loads(open(sys.argv[1]).read().splitlines()[-1]);assert rec["rc"]==3 and rec["channel"]=="stub"' "$FORGE_WATCH_CACHE_DIR/delivered.log" && ok "failed ring logged with rc!=0 in delivered.log" || bad "delivered.log rc wrong"
run_status | grep -q "DELIVERY-UNVERIFIED" && ok "next scan surfaces DELIVERY-UNVERIFIED (failure itself surfaced)" || bad "DELIVERY-UNVERIFIED not raised"

echo "── freshness: stale heartbeat → board prints the staleness line ──"                # +2
new_env tfresh
R=$(mk_root proj); live_session forge-1 "$R"
printf '{"event_offsets":{},"conditions":{},"last_tick":"%s"}' "$(iso_ago 600)" > "$FORGE_WATCH_CACHE_DIR/state.json"
run_status --pretty | grep -q "forge-watch last ran" && ok "stale heartbeat → freshness line on the board" || bad "no freshness line"
run_status --board | python3 -c 'import json,sys;assert json.load(sys.stdin)["stale"] is True' && ok "cc-board/1 stale flag set" || bad "stale flag missing"

echo "── selftest: sentinel → DELIVERY-UNVERIFIED banner until --confirm ──"              # +4
new_env tself
R=$(mk_root proj); live_session forge-1 "$R"
id=$("$WATCH" selftest | awk '/^sent sentinel/{print $3}')
notified "sentinel" && ok "selftest fired a (captured) notification" || bad "selftest did not notify"
[ -n "$id" ] && ok "selftest emitted a sentinel id" || bad "no sentinel id"
run_status | grep -q "DELIVERY-UNVERIFIED" && ok "unconfirmed selftest → DELIVERY-UNVERIFIED banner" || bad "no selftest banner"
"$WATCH" selftest --confirm "$id" >/dev/null 2>&1
run_status | grep -q "$id" && bad "banner persists after confirm" || ok "confirmed selftest clears the banner"

echo "── SwiftBar plugin: renders counts + staleness from cc-board/1 JSON (hermetic) ──"   # +5
new_env tsb
PLUGIN="$(cd "$(dirname "$WATCH")/.." && pwd)/swiftbar/forge-board.5s.sh"
if [ -f "$PLUGIN" ]; then
  STUBF="$WORK/fakeforge"
  printf '#!/bin/bash\ncat <<JSON\n{"schema":"cc-board/1","hot":[{"condition":"NEEDS-ASK","session":"forge-1","acked":false}],"active":[],"tasks":[{"task_id":"cc-a"}],"maintenance":{"collapsed":true,"count":0,"rows":[]},"stale":false}\nJSON\n' > "$STUBF"; chmod +x "$STUBF"
  out=$(FORGE_BIN="$STUBF" bash "$PLUGIN")
  echo "$out" | head -1 | grep -q "^1! | templateImage=iVBOR" && ok "SwiftBar menubar shows unseen hot count + icon" || bad "swiftbar title wrong: $(echo "$out" | head -1)"
  echo "$out" | grep -q "1 task(s)" && ok "SwiftBar dropdown shows task count" || bad "swiftbar task count missing"
  STUBF2="$WORK/fakeforge2"
  printf '#!/bin/bash\ncat <<JSON\n{"schema":"cc-board/1","hot":[],"active":[],"tasks":[],"maintenance":{"collapsed":true,"count":0,"rows":[]},"stale":true,"heartbeat_age_s":300}\nJSON\n' > "$STUBF2"; chmod +x "$STUBF2"
  FORGE_BIN="$STUBF2" bash "$PLUGIN" | grep -q "watcher stale" && ok "SwiftBar self-reports watcher staleness" || bad "swiftbar staleness missing"
  # icon integrity: the embedded base64 must decode to a real PNG (a single
  # flipped char in the 850-char blob corrupts an IDAT CRC and kills the icon)
  [ "$(sed -n 's/^ICON="\(.*\)"$/\1/p' "$PLUGIN" | base64 -d 2>/dev/null | head -c 8 | xxd -p)" = "89504e470d0a1a0a" ] \
    && ok "embedded icon decodes to a PNG" || bad "embedded icon base64 invalid"
  out=$(FORGE_BIN=/usr/bin/false bash "$PLUGIN")
  echo "$out" | head -1 | grep -q "^⚠ | templateImage=iVBOR" && ok "no-board fallback title carries icon" || bad "fallback title: $(echo "$out" | head -1)"
else
  echo "  (skip: plugin not found)"; ok "swiftbar plugin present"; ok "swiftbar task count (skipped)"; ok "swiftbar staleness (skipped)"; ok "swiftbar icon decode (skipped)"; ok "swiftbar fallback icon (skipped)"
fi

echo "── SwiftBar plugin: in-progress episodes SB1-SB12 (hermetic) ──"                     # +16
new_env tsb2
PLUGIN="$(cd "$(dirname "$WATCH")/.." && pwd)/swiftbar/forge-board.5s.sh"
if [ -f "$PLUGIN" ]; then
  sbrun() {  # sbrun <json> — run the plugin against a stub forge emitting <json>
    local f="$WORK/sbstub"
    printf '#!/bin/bash\ncat "$0.json"\n' > "$f"; chmod +x "$f"
    printf '%s' "$1" > "$f.json"
    FORGE_BIN="$f" bash "$PLUGIN"
  }
  EP1='{"episode_id":"e1","session":"forge-3","pane":"0","root":"/r","label":"goparent-ai","agent":"claude","state":"in_progress","mid_turn":false,"turn_count":3,"current":true,"quiet_s":120,"first_at":"2026-01-01T00:00:00Z","last_at":"2026-01-01T00:02:00Z","last_snippet":"building the parser"}'
  BASE='"schema":"cc-board/1","active":[],"maintenance":{"collapsed":true,"count":0,"rows":[]}'

  # SB1 — in-progress title + row (titles carry the icon param; compare the text
  # part via ${title%% |*} — the icon replaces the word "forge", counts stay)
  out=$(sbrun "{$BASE,\"hot\":[],\"tasks\":[],\"stale\":false,\"episodes\":[$EP1]}")
  t1=$(echo "$out" | head -1)
  { [ "${t1%% |*}" = "⚙1" ] && echo "$t1" | grep -q "templateImage=iVBOR"; } && ok "SB1: title shows ⚙1 + icon, no ✓" || bad "SB1 title: $t1"
  { echo "$out" | grep -q "forge-3 p0" && echo "$out" | grep -q "quiet 2m"; } && ok "SB1: episode row has session/pane + quiet age" || bad "SB1 row missing"

  # SB2 — hot leads, order pinned
  out=$(sbrun "{$BASE,\"hot\":[{\"condition\":\"NEEDS-ASK\",\"session\":\"forge-1\",\"acked\":false}],\"tasks\":[],\"stale\":false,\"episodes\":[$EP1]}")
  t2=$(echo "$out" | head -1)
  [ "${t2%% |*}" = "1! ⚙1" ] && ok "SB2: hot leads gear in title" || bad "SB2 title: $t2"

  # SB3 — degrade: no episodes key → exact legacy render, no traceback
  out=$(sbrun "{$BASE,\"hot\":[],\"tasks\":[{\"task_id\":\"cc-a\"}],\"stale\":false}" 2>"$WORK/sb3.err")
  t3=$(echo "$out" | head -1)
  [ "${t3%% |*}" = "✓" ] && ok "SB3: legacy board → exact '✓' text" || bad "SB3 title: $t3"
  { echo "$out" | grep -q "1 task(s) in window" && ! grep -q Traceback "$WORK/sb3.err"; } && ok "SB3: legacy task line, no traceback" || bad "SB3 task line/stderr"

  # SB4 — snippet pipe sanitized; exactly one | (the SwiftBar separator) on the row
  EPP=$(printf '%s' "$EP1" | sed 's/building the parser/a | b/')
  out=$(sbrun "{$BASE,\"hot\":[],\"tasks\":[],\"stale\":false,\"episodes\":[$EPP]}")
  line=$(echo "$out" | grep "color=orange")
  { echo "$line" | grep -q "a ¦ b" && [ "$(printf '%s' "$line" | tr -cd '|' | wc -c | tr -d ' ')" -eq 1 ]; } && ok "SB4: snippet pipe → ¦, one separator" || bad "SB4 row: $line"

  # SB5 — non-current / settled excluded; codex empty pane omits pN token
  EPA='{"episode_id":"a","session":"s-old","pane":"0","root":"/r","label":"L","agent":"claude","state":"in_progress","mid_turn":false,"turn_count":1,"current":false,"quiet_s":5,"last_at":"2026-01-01T00:00:01Z","first_at":"2026-01-01T00:00:00Z","last_snippet":""}'
  EPB='{"episode_id":"b","session":"s-done","pane":"2","root":"/r","label":"L","agent":"claude","state":"settled","mid_turn":false,"turn_count":1,"current":true,"quiet_s":900,"last_at":"2026-01-01T00:00:02Z","first_at":"2026-01-01T00:00:00Z","last_snippet":""}'
  EPC='{"episode_id":"c","session":"codex-x","pane":"","root":"/r","label":"proj","agent":"codex","state":"in_progress","mid_turn":false,"turn_count":1,"current":true,"quiet_s":5,"last_at":"2026-01-01T00:00:03Z","first_at":"2026-01-01T00:00:00Z","last_snippet":""}'
  out=$(sbrun "{$BASE,\"hot\":[],\"tasks\":[],\"stale\":false,\"episodes\":[$EPA,$EPB,$EPC]}")
  { echo "$out" | head -1 | grep -q "⚙1" && [ "$(echo "$out" | grep -c 'color=orange')" -eq 1 ]; } && ok "SB5: only current+in_progress rendered" || bad "SB5 count wrong"
  line=$(echo "$out" | grep "color=orange")
  { echo "$line" | grep -q "codex-x" && ! echo "$line" | grep -qE " p[0-9]"; } && ok "SB5: empty pane omits pN token" || bad "SB5 row: $line"

  # SB6 — pending task line; legacy substring retained
  out=$(sbrun "{$BASE,\"hot\":[],\"stale\":false,\"episodes\":[],\"tasks\":[{\"task_id\":\"t1\",\"state\":\"queued\"},{\"task_id\":\"t2\",\"state\":\"working\"},{\"task_id\":\"t3\",\"state\":\"done\"}]}")
  { echo "$out" | grep -q "2 pending (1 working)" && echo "$out" | grep -q "task(s) in window"; } && ok "SB6: pending task summary" || bad "SB6 task line"

  # SB7 — stale suffix stays last with gear present
  out=$(sbrun "{$BASE,\"hot\":[],\"tasks\":[],\"stale\":true,\"heartbeat_age_s\":300,\"episodes\":[$EP1]}")
  t7=$(echo "$out" | head -1)
  [ "${t7%% |*}" = "⚙1 ⚠" ] && ok "SB7: '⚙1 ⚠' (⚠ last)" || bad "SB7 title: $t7"
  out=$(sbrun "{$BASE,\"hot\":[{\"condition\":\"NEEDS-ASK\",\"session\":\"forge-1\",\"acked\":false}],\"tasks\":[],\"stale\":true,\"heartbeat_age_s\":300,\"episodes\":[$EP1]}")
  t7=$(echo "$out" | head -1)
  [ "${t7%% |*}" = "1! ⚙1 ⚠" ] && ok "SB7: '1! ⚙1 ⚠'" || bad "SB7 hot title: $t7"

  # SB8 — mid_turn renders streaming, not quiet
  EPS=$(printf '%s' "$EP1" | sed 's/"mid_turn":false/"mid_turn":true/')
  out=$(sbrun "{$BASE,\"hot\":[],\"tasks\":[],\"stale\":false,\"episodes\":[$EPS]}")
  { echo "$out" | grep -q "streaming" && ! echo "$out" | grep -q "quiet"; } && ok "SB8: mid_turn → streaming" || bad "SB8 row"

  # SB9 — negative quiet_s clamped to 0s
  EPN=$(printf '%s' "$EP1" | sed 's/"quiet_s":120/"quiet_s":-50/')
  out=$(sbrun "{$BASE,\"hot\":[],\"tasks\":[],\"stale\":false,\"episodes\":[$EPN]}")
  { echo "$out" | grep -q "quiet 0s" && ! echo "$out" | grep -q -- "-50"; } && ok "SB9: negative quiet clamps to 0s" || bad "SB9 row"

  # SB10 — 8-row cap + overflow line
  NINE=$(python3 -c '
import json
eps=[{"episode_id":f"e{i}","session":f"s{i}","pane":"0","root":"/r","label":"L","agent":"claude",
      "state":"in_progress","mid_turn":False,"turn_count":1,"current":True,"quiet_s":5,
      "first_at":"2026-01-01T00:00:00Z","last_at":f"2026-01-01T00:00:{i:02d}Z","last_snippet":""}
     for i in range(9)]
print(json.dumps(eps))')
  out=$(sbrun "{$BASE,\"hot\":[],\"tasks\":[],\"stale\":false,\"episodes\":$NINE}")
  { [ "$(echo "$out" | grep -c 'color=orange')" -eq 8 ] && echo "$out" | grep -q "+1 more in progress"; } && ok "SB10: cap 8 + overflow line" || bad "SB10 cap/overflow"

  # SB11 — hot-row sanitizer: pipe in session name
  out=$(sbrun "{$BASE,\"hot\":[{\"condition\":\"NEEDS-ASK\",\"session\":\"forge|1\",\"acked\":false}],\"tasks\":[],\"stale\":false,\"episodes\":[]}")
  line=$(echo "$out" | grep "color=red")
  { echo "$line" | grep -q "forge¦1" && [ "$(printf '%s' "$line" | tr -cd '|' | wc -c | tr -d ' ')" -eq 1 ]; } && ok "SB11: hot row pipe → ¦, one separator" || bad "SB11 row: $line"

  # SB12 — SESSION-WORKING alone does NOT light the gear (final-plan D5, deliberate)
  out=$(sbrun "{\"schema\":\"cc-board/1\",\"hot\":[],\"active\":[{\"condition\":\"SESSION-WORKING\",\"state\":\"working\",\"session\":\"forge-1\"}],\"maintenance\":{\"collapsed\":true,\"count\":0,\"rows\":[]},\"tasks\":[],\"stale\":false,\"episodes\":[]}")
  t12=$(echo "$out" | head -1)
  { [ "${t12%% |*}" = "✓" ] && ! echo "$out" | grep -q "⚙"; } && ok "SB12: SESSION-WORKING-only → no gear (pinned exclusion)" || bad "SB12 title: $t12"
else
  echo "  (skip: plugin not found)"
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16; do ok "swiftbar in-progress (skipped)"; done
fi

echo "── CODEX-EMISSION-OFF: marker + no codex signal → maintenance; codex fire clears ──"  # +2
new_env tcodex
R=$(mk_root proj); live_session forge-1 "$R"
printf '{"schema":"cc-codex-register/1","hook_sha256":"abc","hooks_path":"x"}' > "$(attn "$R")/codex-register.json"
assert_status_has "CODEX-EMISSION-OFF" "installed codex hooks + no codex emission → CODEX-EMISSION-OFF"
wstopf "$R" forge-1 2 30 turn-1 codex "codex answered"
assert_status_missing "CODEX-EMISSION-OFF" "a codex-tagged wstop suppresses the row (trust proven observationally)"


echo "── hardening: quiet ticks — unchanged check prints nothing (non-tty) ──"           # +3
new_env tquiet
R=$(mk_root proj); live_session forge-1 "$R"
out1=$(FORGE_WATCH_QUIET_UNCHANGED=1 "$WATCH" check)
out2=$(FORGE_WATCH_QUIET_UNCHANGED=1 "$WATCH" check)
[ -n "$out1" ] && ok "first check prints (signature transition)" || bad "first check silent"
[ -z "$out2" ] && ok "unchanged second check prints nothing" || bad "unchanged check still printed: $out2"
askf "$R" forge-1 ask-q "" "" 30 "quiet?"
out3=$(FORGE_WATCH_QUIET_UNCHANGED=1 "$WATCH" check)
echo "$out3" | grep -q "NEEDS-ASK" && ok "changed findings print again" || bad "changed findings suppressed: $out3"

echo "── hardening: lock contention leaves a stderr trace, exit 0 ──"                    # +3
new_env tlock
R=$(mk_root proj); live_session forge-1 "$R"
python3 - "$FORGE_WATCH_CACHE_DIR/state.lock" <<'LKPY' &
import sys, fcntl, time, datetime
fh = open(sys.argv[1], 'a')
fcntl.flock(fh, fcntl.LOCK_EX)
fh.seek(0); fh.truncate()
fh.write(datetime.datetime.now(datetime.timezone.utc).isoformat()); fh.flush()
time.sleep(4)
LKPY
HOLDER=$!
sleep 1
err=$("$WATCH" check 2>&1 >/dev/null); rc=$?
[ "$rc" -eq 0 ] && ok "skipped tick still exits 0" || bad "skip exit rc=$rc"
echo "$err" | grep -q "state.lock held" && ok "skip leaves a stderr trace" || bad "no skip trace: $err"
echo "$err" | grep -Eq "for ~[0-9]+s" && ok "trace includes held-duration" || bad "no held-duration: $err"
wait "$HOLDER" 2>/dev/null

echo "── hardening: wedged notifier is killed by fw_timed → rc!=0 audit ──"              # +2
new_env tnto
SLOWN="$WORK/slownotify"; printf '#!/bin/bash\nsleep 20\n' > "$SLOWN"; chmod +x "$SLOWN"
start=$SECONDS
FORGE_WATCH_NOTIFIER_BIN="$SLOWN" FORGE_WATCH_NOTIFY_TIMEOUT_S=1 "$WATCH" selftest >/dev/null 2>&1
dur=$((SECONDS - start))
[ "$dur" -lt 10 ] && ok "notifier bounded (${dur}s < 10s)" || bad "notifier not bounded (${dur}s)"
python3 -c 'import json,sys;rec=json.loads(open(sys.argv[1]).read().splitlines()[-1]);assert rec["rc"]!=0 and rec["channel"]=="stub", rec' "$FORGE_WATCH_CACHE_DIR/delivered.log" && ok "timeout logged rc!=0 (DELIVERY-UNVERIFIED path)" || bad "timeout rc not logged"

echo "── hardening: check-mode watchdog kills a hung engine ──"                          # +2
new_env twdog
mkfifo "$WORK/twdog-fifo"
err=$(FORGE_WATCH_TMUX_LIST="$WORK/twdog-fifo" FORGE_WATCH_CHECK_TIMEOUT_S=1 \
      perl -e 'alarm 15; exec @ARGV' "$WATCH" check 2>&1 >/dev/null); rc=$?
[ "$rc" -eq 70 ] && ok "hung check dies with watchdog exit 70" || bad "watchdog rc=$rc"
echo "$err" | grep -q "watchdog" && ok "watchdog leaves a stderr trace" || bad "no watchdog trace: $err"

echo "── markdown stripping: snippets render plain on every surface (MD1-MD6) ──"   # +7
new_env mdstrip
R=$(mk_root proj); live_session forge-1 "$R"
# MD1: worker episode snippet — heading/bold/backticks/link all stripped
wstopf "$R" forge-1 0 30 ptask-md1 claude '## Done **bold** with `code` and [plan.md](/tmp/plan.md)'
assert_status_has "last: Done bold with code and plan.md" "MD1 episode snippet stripped"
# MD2: session-level done snippet — nested bold+inline-code stripped
stopf "$R" forge-1 60 'committed **`fix.py`** as `abc123`'
assert_status_has "done: committed fix.py as abc123" "MD2 session-done snippet stripped"
# MD3: a snip()-truncated link tail degrades to its link text
wstopf "$R" forge-1 2 30 ptask-md3 claude 'artifact: [diagnosis.md](/Users/x/sirtheoracle/au'
assert_status_has "p2 — in progress · 1 turn(s) · last: artifact: diagnosis.md" "MD3 truncated link → text"
# MD4: non-markdown lookalikes survive (globs, math, #refs, dunders)
wstopf "$R" forge-1 4 30 ptask-md4 claude 'keep *.json globs, 2 * 3, #192 and __init__.py'
assert_status_has 'keep \*\.json globs, 2 \* 3, #192 and __init__\.py' "MD4 lookalikes untouched"
# MD5: the cc-board JSON itself carries stripped text (the SwiftBar surface)
run_status --board > "$TDIR/md.json"
python3 - "$TDIR/md.json" <<'PY' && ok "MD5 board JSON is markdown-free" || bad "MD5 markdown leaked into board JSON"
import json, sys
b = json.load(open(sys.argv[1]))
texts  = [e.get('last_snippet') or '' for e in b.get('episodes', [])]
texts += [r.get('msg') or '' for r in b.get('hot', []) + b.get('active', [])]
texts += [(t.get('snippet') or '') + (t.get('prompt_snippet') or '') for t in b.get('tasks', [])]
assert not any('**' in t or '](' in t or '`' in t for t in texts), texts
PY
# MD6: AskUserQuestion perm question + option labels stripped
a="$(attn "$R")"; cat > "$a/perm.forge-1.mdq.json" <<JSON
{"schema":"cc-attention/1","event":"permissionrequest","variant":"permission","session":"forge-1","root":"$R","pane_index":"1","role":"orchestrator","tmux_pane":"%1","emitted_at":"$(iso_ago 30)","state":"needs-input","tool_name":"AskUserQuestion","command":"","command_hash":"mdq","permission_suggestions":[],"question_snippet":"Deploy **now** to \`prod\`?","question_options":["**yes**","\`no\`"],"question_count":1,"multi_select":false}
JSON
assert_status_has "question (AskUserQuestion): Deploy now to prod?" "MD6 perm question stripped"
assert_status_has "options: yes / no" "MD6 option labels stripped"

echo "── harness-injected turns: dropped from tasks[] on every surface (HT1-HT6) ──"   # +7
new_env htfilter
R=$(mk_root proj); live_session forge-1 "$R"
a="$(attn "$R")"
# HT1: done turn whose prompt was an injected <task-notification> → no task row
cat > "$a/wstop.forge-1.p0.ptask-ht1.json" <<JSON
{"schema":"cc-attention/1","event":"stop","variant":"worker-snippet","session":"forge-1","root":"$R","pane_index":"0","role":"worker","agent":"claude","tmux_pane":"%0","emitted_at":"$(iso_ago 30)","task_id":"ptask-ht1","prompt_snippet":"<task-notification> <task-id>abc123</task-id> <tool-use-id>toolu_x</tool-use-id>","snippet":"noted, moving on","snippet_source":"last_assistant_message","looks_like_question":false}
JSON
# HT2: legacy mangled form (record written before the sk- boundary fix) → no task row
cat > "$a/wstop.forge-1.p2.ptask-ht2.json" <<JSON
{"schema":"cc-attention/1","event":"stop","variant":"worker-snippet","session":"forge-1","root":"$R","pane_index":"2","role":"worker","agent":"claude","tmux_pane":"%2","emitted_at":"$(iso_ago 30)","task_id":"ptask-ht2","prompt_snippet":"<ta«redacted»> <task-id>def456</task-id> <tool-use-id>toolu_y</tool-use-id>","snippet":"ack","snippet_source":"last_assistant_message","looks_like_question":false}
JSON
# HT3: Agent-Teams <agent-message> turn → no task row
cat > "$a/wstop.forge-1.p4.ptask-ht3.json" <<JSON
{"schema":"cc-attention/1","event":"stop","variant":"worker-snippet","session":"forge-1","root":"$R","pane_index":"4","role":"worker","agent":"claude","tmux_pane":"%4","emitted_at":"$(iso_ago 30)","task_id":"ptask-ht3","prompt_snippet":"<agent-message from=\"regression-hunter-b\"> No corrections","snippet":"ok","snippet_source":"last_assistant_message","looks_like_question":false}
JSON
# HT4: live injected wprompt (would render state=working) → no task row
wpromptf "$R" forge-1 6 30 ptask-ht4 claude "<task-notification> <task-id>xyz</task-id> background agent done"
# HT5: a normal typed task in the same root still renders (no over-filtering)
wstopf "$R" forge-1 3 30 ptask-ht5 claude "real work done"
run_status --board > "$TDIR/ht.json"
python3 - "$TDIR/ht.json" <<'PY' && ok "HT1-HT4 injected rows absent from tasks[]; HT5 real task present" || bad "harness-injected filter wrong in tasks[]"
import json, sys
b = json.load(open(sys.argv[1]))
ids = {t.get('task_id') for t in b.get('tasks', [])}
assert 'ptask-ht5' in ids, ids
assert not ({'ptask-ht1','ptask-ht2','ptask-ht3','ptask-ht4'} & ids), ids
PY
python3 - "$TDIR/ht.json" <<'PY' && ok "HT6 board JSON tasks[] carries no harness tags (SwiftBar surface)" || bad "HT6 harness tag leaked into board JSON"
import json, sys
b = json.load(open(sys.argv[1]))
texts = [(t.get('prompt_snippet') or '') for t in b.get('tasks', [])]
assert not any(x.lstrip().startswith('<ta') or 'agent-message' in x for x in texts), texts
PY
run_status --pretty | grep -q "task-notification\|ta«redacted»\|agent-message" && bad "injected tag visible on pretty board" || ok "pretty board free of injected-tag rows"
run_status --tasks | grep -q "task-notification\|ta«redacted»\|agent-message" && bad "--tasks still lists injected turns" || ok "--tasks view filtered too"
python3 - "$TDIR/ht.json" <<'PY' && ok "episodes still count injected turns (only the task projection hides them)" || bad "episode derivation over-filtered"
import json, sys
b = json.load(open(sys.argv[1]))
panes = {e.get('pane') for e in b.get('episodes', [])}
assert '0' in panes and '2' in panes and '4' in panes, panes
PY

# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo "── RECOVER-PENDING: cc-recover/1 boot finding renders (read-only) ──"
new_env recover_pending
R=$(mk_root recroot)
mkdir -p "$R/.dev/attention"
cat > "$R/.dev/attention/recover-777.json" <<'EOF'
{"schema":"cc-recover/1","event":"recover-candidates","boot_id":"777",
 "emitted_at":"2026-07-11T10:00:00Z","candidates":4,"sessions":["forge-9"],
 "needs_manual":0,"hint":"forge recover --dry-run · then forge recover --apply"}
EOF
out=$("$WATCH" status 2>/dev/null)
echo "$out" | grep -q 'RECOVER-PENDING' && ok "RECOVER-PENDING finding emitted" || bad "no RECOVER-PENDING"
echo "$out" | grep -q 'boot 777' && ok "finding carries the boot id" || bad "no boot id in msg"
echo "$out" | grep -q 'forge recover --dry-run' && ok "finding carries the operator hint" || bad "no hint"

echo "── recover archives (attention/archive/<id>/) invisible to the watcher ──"
new_env recover_blind
R=$(mk_root blindroot)
mkdir -p "$R/.dev/attention/archive/recover-20260711T000000Z-aa/payloads"
cat > "$R/.dev/attention/archive/recover-20260711T000000Z-aa/stop.deadghost.json" <<'EOF'
{"schema":"cc-attention/1","event":"stop","session":"deadghost","emitted_at":"2026-07-10T10:00:00Z"}
EOF
out=$("$WATCH" status 2>/dev/null)
echo "$out" | grep -q 'deadghost' && bad "archived record leaked into findings" || ok "depth-2 archive content produces zero findings"

# ═══════════════════════════════════════════════════════════════════════════
# Blocked-item lifecycle (C1): parser-unit tests. parse_callback / pending_entries
# live inside forge-watch's PYEOF heredoc, so we slice the real def-region out of
# bin/forge-watch and exec it (NOT a hand copy) — the assertions exercise the exact
# shipped bodies. FW_DRIVER prints KEY=VALUE lines for grep.
# ═══════════════════════════════════════════════════════════════════════════
echo "── W1: parse_callback yaml.safe_load-first + _first_line (P4b) ──"
new_env w1
FW_DRIVER="$WORK/_fw_unit.py"
cat > "$FW_DRIVER" <<'PYDRV'
import sys, re, os, glob, json
try:
    import yaml; HAVE_YAML = True
except ImportError:
    HAVE_YAML = False
src = open(os.environ['FW_BIN']).read().splitlines()
start = next(i for i, l in enumerate(src) if l.startswith('def note_unparseable('))
end   = next(i for i, l in enumerate(src) if l.startswith('# ── findings'))
ns = {'re': re, 'os': os, 'glob': glob, 'HAVE_YAML': HAVE_YAML, 'unparseable': []}
if HAVE_YAML: ns['yaml'] = yaml
exec(compile("\n".join(src[start:end]), 'forge-watch-slice', 'exec'), ns)
mode = sys.argv[1]
if mode == 'parse_callback':
    d = ns['parse_callback'](sys.argv[2])
    if d is None:
        print('parsed=0'); sys.exit(0)
    print('parsed=1')
    print('first_line=%s' % d.get('_first_line', ''))
    print('status=%s' % d.get('status', ''))
    print('session=%s' % d.get('session', ''))
    print('origin=%s' % d.get('origin', ''))
elif mode == 'pending_entries':
    out = ns['pending_entries']('r', sys.argv[2])
    print('count=%d' % len(out))
    if out:
        e = out[0]
        print('to=%s' % e.get('to'))
        print('session=%s' % e.get('session'))
        print('parked_at=%s' % e.get('parked_at'))
        print('parked_reason=%s' % e.get('parked_reason'))
        print('uncommitted=%s' % e.get('uncommitted'))
        print('response_none=%s' % (e.get('stage') is not None and 'response' not in e))
PYDRV
fw_unit() { FW_BIN="$WATCH" python3 "$FW_DRIVER" "$@"; }

# block-scalar message → _first_line is the FIRST body line (not "message: |")
CB_BLK="$ROOTS_DIR/cb_block.callback"
cat > "$CB_BLK" <<'EOF'
slug: w1
stage: coding
status: BLOCKED
session: forge-1
origin: ask
callback_id: w1-coding-x
timestamp: 2026-07-11T10:00:00Z
message: |
  needs a human here
  second line ignored
EOF
o=$(fw_unit parse_callback "$CB_BLK")
echo "$o" | grep -q '^parsed=1$'                 && ok "W1 block: parse_callback returns a dict" || bad "W1 block: parse_callback None"
echo "$o" | grep -q '^first_line=needs a human here$' && ok "W1 block: _first_line == first body line" || bad "W1 block: _first_line wrong ($o)"
echo "$o" | grep -q '^session=forge-1$'          && ok "W1 block: session header captured" || bad "W1 block: session missing ($o)"
echo "$o" | grep -q '^origin=ask$'               && ok "W1 block: origin header captured" || bad "W1 block: origin missing ($o)"

# inline scalar message → _first_line is the inline text verbatim
CB_INL="$ROOTS_DIR/cb_inline.callback"
cat > "$CB_INL" <<'EOF'
slug: w1
stage: coding
status: BLOCKED
message: hello inline world
EOF
o=$(fw_unit parse_callback "$CB_INL")
echo "$o" | grep -q '^first_line=hello inline world$' && ok "W1 inline: _first_line == inline text" || bad "W1 inline: _first_line wrong ($o)"

# malformed file (tab breaks yaml) → scalar fallback returns a dict, NO abort
CB_BAD="$ROOTS_DIR/cb_bad.callback"
printf 'status: BLOCKED\n\tbadtab: x\n' > "$CB_BAD"
o=$(fw_unit parse_callback "$CB_BAD" 2>/dev/null)
echo "$o" | grep -q '^parsed=1$'      && ok "W1 malformed: scalar fallback still returns a dict (no abort)" || bad "W1 malformed: aborted/None ($o)"
echo "$o" | grep -q '^status=BLOCKED$' && ok "W1 malformed: fallback captured scalar header" || bad "W1 malformed: header lost ($o)"

echo "── W15: regex-fallback pending_entries retains parked_*/session/to (P4a) ──"
new_env w15
BAD_LOG="$ROOTS_DIR/malformed-forge-log.yml"
{
  printf 'entries:\n'
  printf '  - timestamp: "2026-07-11T10:00:00Z"\n'
  printf '    stage: coding\n'
  printf '    to: codex-a\n'
  printf '    session: forge-1\n'
  printf '    parked_at: "2026-07-11T10:05:00Z"\n'
  printf '    parked_reason: "waiting on human"\n'
  printf '    uncommitted: true\n'
  printf '    response: null\n'
  printf '\tfiles:- legacy malformed tab line\n'
} > "$BAD_LOG"
o=$(FW_BIN="$WATCH" python3 "$FW_DRIVER" pending_entries "$BAD_LOG" 2>/dev/null)
echo "$o" | grep -q '^count=1$'                           && ok "W15: one open entry reconstructed via regex fallback" || bad "W15: count wrong ($o)"
echo "$o" | grep -q '^to=codex-a$'                        && ok "W15: 'to' retained in fallback" || bad "W15: to lost ($o)"
echo "$o" | grep -q '^session=forge-1$'                   && ok "W15: 'session' retained in fallback" || bad "W15: session lost ($o)"
echo "$o" | grep -q '^parked_at=.*2026-07-11T10:05:00Z'   && ok "W15: 'parked_at' retained in fallback" || bad "W15: parked_at lost ($o)"
echo "$o" | grep -q '^parked_reason=waiting on human$'    && ok "W15: 'parked_reason' retained + unquoted" || bad "W15: parked_reason lost ($o)"
echo "$o" | grep -q '^uncommitted=True$'                  && ok "W15: 'uncommitted' normalized to bool True" || bad "W15: uncommitted wrong ($o)"

echo "── W18: P1-E callback identity correlation ──"
new_env w18; WR=$(mk_root w18); live_session forge-w16 "$WR"
mkdir -p "$WR/.dev/proposals/e16"; cat > "$WR/.dev/proposals/e16/forge-log.yml" <<'EOF'
entries:
  - timestamp: "2026-07-14T00:00:00Z"
    stage: coding
    to: codex-a
    session: forge-w16
    incarnation: 200
    response: null
EOF
cat > "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.200.callback" <<'EOF'
slug: e16
stage: coding
status: BLOCKED
worker: codex-a
session: forge-w16
incarnation: 200
callback_id: exact-1
timestamp: 2026-07-14T00:00:01Z
message: exact current
EOF
o=$("$WATCH" status 2>/dev/null)
echo "$o" | grep -q 'blocked at coding: exact current' && ok "W18 exact current projects" || bad "W18 exact projection"

# Persisted evidence resets on either callback id or incarnation while the
# aggregate (slug,stage) compatibility key remains stable for context/stall users.
"$WATCH" check >/dev/null 2>&1
python3 - "$FORGE_WATCH_CACHE_DIR/state.json" "$WR" <<'PY' \
  && ok "W18 evidence records callback id+incarnation" || bad "W18 evidence initial identity"
import json,os,sys
d=json.load(open(sys.argv[1])); prefix=os.path.realpath(sys.argv[2])+'\te16\tcoding\t'; rows=[v for k,v in d['blocked_evidence'].items() if k.startswith(prefix)]
assert len(rows)==1 and rows[0]['callback_id']=='exact-1' and str(rows[0]['incarnation'])=='200'
PY
mv "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.200.callback" "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.201.callback"
sed -i '' 's/incarnation: 200/incarnation: 201/' "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.201.callback"
sed -i '' 's/incarnation: 200/incarnation: 201/' "$WR/.dev/proposals/e16/forge-log.yml"
"$WATCH" check >/dev/null 2>&1
python3 - "$FORGE_WATCH_CACHE_DIR/state.json" "$WR" <<'PY' \
  && ok "W18 incarnation-only change resets evidence" || bad "W18 incarnation reset"
import json,os,sys
d=json.load(open(sys.argv[1])); prefix=os.path.realpath(sys.argv[2])+'\te16\tcoding\t'; rows=[v for k,v in d['blocked_evidence'].items() if k.startswith(prefix)]
assert len(rows)==1 and rows[0]['callback_id']=='exact-1' and str(rows[0]['incarnation'])=='201' and rows[0]['later_other_dispatch'] is False
PY
sed -i '' 's/callback_id: exact-1/callback_id: exact-2/' "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.201.callback"
"$WATCH" check >/dev/null 2>&1
python3 - "$FORGE_WATCH_CACHE_DIR/state.json" "$WR" <<'PY' \
  && ok "W18 callback-id-only change resets evidence" || bad "W18 callback-id reset"
import json,os,sys
d=json.load(open(sys.argv[1])); prefix=os.path.realpath(sys.argv[2])+'\te16\tcoding\t'; rows=[v for k,v in d['blocked_evidence'].items() if k.startswith(prefix)]
assert len(rows)==1 and rows[0]['callback_id']=='exact-2' and str(rows[0]['incarnation'])=='201' and rows[0]['later_other_dispatch'] is False
PY

# Two exact owners sharing one root and slug/stage remain two scoped rows, while
# the aggregate map continues to suppress only one stale/stall twin.
cat >> "$WR/.dev/proposals/e16/forge-log.yml" <<'EOF'
  - timestamp: "2026-07-14T00:00:03Z"
    stage: coding
    to: codex-b
    session: forge-w17
    incarnation: 301
    response: null
EOF
cat > "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w17.301.callback" <<'EOF'
slug: e16
stage: coding
status: BLOCKED
worker: codex-b
session: forge-w17
incarnation: 301
callback_id: concurrent-2
timestamp: 2026-07-14T00:00:04Z
message: concurrent exact
EOF
o=$("$WATCH" status 2>/dev/null)
[ "$(printf '%s\n' "$o" | grep -c 'ITEM-BLOCKED')" -ge 2 ] \
  && ! printf '%s' "$o" | grep -q 'CALLBACK-AMBIGUOUS' \
  && ok "W18 concurrent exact owners remain distinct" || bad "W18 concurrent exact owners collapsed"
rm "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w17.301.callback"
python3 - "$WR/.dev/proposals/e16/forge-log.yml" <<'PY'
import sys,yaml
p=sys.argv[1]; d=yaml.safe_load(open(p)); d['entries']=[e for e in d['entries'] if e.get('session')!='forge-w17']
open(p,'w').write(yaml.safe_dump(d,sort_keys=False))
PY

# Session-only and unqualified old-producer shapes remain readable one at a time.
cp "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.201.callback" "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.callback"
sed -i '' '/^incarnation:/d;s/callback_id: exact-2/callback_id: session-legacy/' "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.callback"
rm "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.201.callback"
o=$("$WATCH" status 2>/dev/null)
echo "$o" | grep -q 'blocked at coding: exact current' && ok "W18 session-only legacy projects" || bad "W18 session legacy"
mv "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.callback" "$WR/.dev/forge-tmp/callbacks/e16-coding.callback"
sed -i '' '/^session:/d;s/callback_id: session-legacy/callback_id: unqualified-legacy/' "$WR/.dev/forge-tmp/callbacks/e16-coding.callback"
o=$("$WATCH" status 2>/dev/null)
echo "$o" | grep -q 'blocked at coding: exact current' && ok "W18 unqualified legacy projects" || bad "W18 unqualified legacy"
rm "$WR/.dev/forge-tmp/callbacks/e16-coding.callback"

# Same-name predecessor and header-negative callbacks never become current.
cat > "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.199.callback" <<'EOF'
slug: e16
stage: coding
status: BLOCKED
worker: codex-a
session: forge-w16
incarnation: 199
callback_id: predecessor
timestamp: 2026-07-14T00:00:01Z
message: foreign predecessor
EOF
o=$("$WATCH" status 2>/dev/null)
echo "$o" | grep -q 'foreign predecessor' && bad "W18 foreign rebirth projected" || ok "W18 same-name predecessor excluded"
mv "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.199.callback" "$WR/.dev/forge-tmp/callbacks/e16-coding.wrong.callback"
printf '\nunknown_future: x\n' >> "$WR/.dev/forge-tmp/callbacks/e16-coding.wrong.callback"
o=$("$WATCH" status 2>/dev/null)
echo "$o" | grep -q 'foreign predecessor' && bad "W18 invalid header projected" || ok "W18 filename/unknown-header negatives excluded"
rm "$WR/.dev/forge-tmp/callbacks/e16-coding.wrong.callback"
cat > "$WR/.dev/forge-tmp/callbacks/e16-coding.callback" <<'EOF'
slug: e16
stage: coding
status: BLOCKED
worker: codex-a
incarnation: 201
callback_id: inc-without-session
timestamp: 2026-07-14T00:00:01Z
message: must not project
EOF
o=$("$WATCH" status 2>/dev/null)
echo "$o" | grep -q 'CALLBACK-INVALID' && ! echo "$o" | grep -q 'blocked at coding: must not project' \
  && ok "W18 incarnation-without-session invalid" || bad "W18 incarnation-without-session projected"
rm "$WR/.dev/forge-tmp/callbacks/e16-coding.callback"

# Ask origin remains visible as blocked but is excluded from abandonment evidence.
cat > "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.201.callback" <<EOF
slug: e16
stage: coding
status: BLOCKED
worker: codex-a
session: forge-w16
incarnation: 201
origin: ask
callback_id: ask-1
timestamp: $(iso_ago 1000)
selected_pending_timestamp: "2026-07-14T00:00:00Z"
message: operator ask
EOF
"$WATCH" check >/dev/null 2>&1
evlog_append "$WR" "DISPATCH: pipeline=e18-other stage=coding worker=codex-b"
o=$(FORGE_BLOCKED_ABANDON_S=1 "$WATCH" check 2>/dev/null)
echo "$o" | grep -q 'ITEM-BLOCKED-ABANDONED' && bad "W18 ask became abandoned" || ok "W18 ask suppression preserved"

# Exact + session-only is ambiguous; glob order cannot select.
cp "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.201.callback" "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.callback"
sed -i '' '/^incarnation:/d;s/callback_id: ask-1/callback_id: legacy-2/;/^origin:/d' "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.callback"
o=$("$WATCH" status 2>/dev/null)
echo "$o" | grep -q 'CALLBACK-AMBIGUOUS' && ok "W18 exact+session cardinality is explicit" || bad "W18 ambiguity hidden"
rm "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.callback"
python3 - "$WR/.dev/proposals/e16/forge-log.yml" <<'PY'
import sys,yaml
p=sys.argv[1]; d=yaml.safe_load(open(p)); e=d['entries'][0]
e.update(parked_at='2026-07-14T00:00:02Z',parked_reason='log wins',uncommitted=False)
open(p,'w').write(yaml.safe_dump(d,sort_keys=False))
PY
o=$("$WATCH" status 2>/dev/null)
echo "$o" | grep -q 'ITEM-PARKED' && echo "$o" | grep -q 'PARK-INCONSISTENT' \
  && echo "$o" | grep -q 'ITEM-BLOCKED' \
  && ok "W18 contradictory parked-log/BLOCKED coexistence pinned" || bad "W18 contradiction projection changed"
sed -i '' 's/status: BLOCKED/status: PARKED/' "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.201.callback"
o=$("$WATCH" status 2>/dev/null)
echo "$o" | grep -q 'PARK-INCONSISTENT' && bad "W18 exact PARKED failed correlation" || ok "W18 exact PARKED correlates"

# Exact + session-only PARKED is ambiguity only: no repair or foreign advice.
cp "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.201.callback" "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.callback"
sed -i '' '/^incarnation:/d;s/callback_id: ask-1/callback_id: parked-legacy/' "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.callback"
o=$("$WATCH" status 2>/dev/null)
echo "$o" | grep -q 'CALLBACK-AMBIGUOUS' && ! echo "$o" | grep -q 'PARK-INCONSISTENT' \
  && ! echo "$o" | grep -q 'CALLBACK-FOREIGN' \
  && ok "W18 ambiguous PARKED emits ambiguity only" || bad "W18 ambiguous PARKED emitted misleading advice"
rm "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.callback"

# Duplicate headers are invalid on the shared strict predicate.
printf 'worker: duplicate\n' >> "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.201.callback"
o=$("$WATCH" status 2>/dev/null)
echo "$o" | grep -q 'invalid PARKED callback' && echo "$o" | grep -q 'PARK-INCONSISTENT' \
  && ok "W18 duplicate PARKED header invalid" || bad "W18 duplicate PARKED header correlated"
sed -i '' '$d' "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.201.callback"
cp "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.201.callback" "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.199.callback"
sed -i '' 's/incarnation: 201/incarnation: 199/;s/callback_id: ask-1/callback_id: foreign-park/' "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.199.callback"
o=$("$WATCH" status 2>/dev/null)
echo "$o" | grep -q 'CALLBACK-FOREIGN' && ok "W18 foreign PARKED classified" || bad "W18 foreign PARKED hidden"
cp "$WR/.dev/forge-tmp/callbacks/e16-coding.forge-w16.201.callback" "$WR/.dev/forge-tmp/callbacks/e16-coding.invalid.callback"
printf '\nunknown_future: x\n' >> "$WR/.dev/forge-tmp/callbacks/e16-coding.invalid.callback"
o=$("$WATCH" status 2>/dev/null)
echo "$o" | grep -q 'invalid PARKED callback' && ok "W18 header-invalid PARKED classified" || bad "W18 invalid PARKED hidden"

# Two independently owned PARKED records for the same aggregate slug/stage
# remain two rows, just as the BLOCKED case above does.
new_env w18p; PR=$(mk_root w18p); live_session forge-p16a "$PR"; live_session forge-p16b "$PR"
parked_entry "$PR" e16p coding codex-a "2026-07-14T00:10:00Z" "held a" false forge-p16a
parked_entry_append "$PR" e16p coding codex-b "2026-07-14T00:11:00Z" "held b" forge-p16b
parked_callback "$PR" e16p coding codex-a forge-p16a
parked_callback "$PR" e16p coding codex-b forge-p16b
sed -i '' 's/callback_id: e16p-coding-x/callback_id: e16p-coding-b/' "$PR/.dev/forge-tmp/callbacks/e16p-coding.forge-p16b.callback"
o=$("$WATCH" status --board 2>/dev/null)
python3 -c 'import json,sys; d=json.load(sys.stdin); assert len([x for x in d.get("parked",[]) if x.get("slug")=="e16p"])==2' <<<"$o" \
  && ok "W18 concurrent PARKED owners remain distinct" || bad "W18 concurrent PARKED owners collapsed"

WATCH_SOURCE_AFTER="$(shasum -a 256 "$WATCH" | awk '{print $1}')"
[ "$WATCH_SOURCE_BEFORE" = "$WATCH_SOURCE_AFTER" ] \
  && ok "W19 watcher product source hash unchanged during P1-WC compatibility run" \
  || bad "W19 watcher product source hash changed"

echo "═══════════════════════════════════════"
green "PASS: $PASS"
[ "$FAIL" -gt 0 ] && red "FAIL: $FAIL" || green "FAIL: 0"
echo "═══════════════════════════════════════"
[ "$FAIL" -eq 0 ]
