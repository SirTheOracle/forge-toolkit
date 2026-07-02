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

evlog() { echo "$1/.dev/forge-tmp/orchestrator-events.log"; }
evlog_touch() { : > "$(evlog "$1")"; }        # empty file to baseline against
evlog_append() { echo "$2" >> "$(evlog "$1")"; }

run_check()  { "$WATCH" check  2>&1; }
run_status() { "$WATCH" status 2>&1; }
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
evlog_touch "$R"; run_check >/dev/null
evlog_append "$R" "CALLBACK: pipeline=p-blk stage=qa worker=codex-a status=BLOCKED message_len=10 callback_file=x"
run_check >/dev/null
assert_notified "p-blk.*BLOCKED" "CALLBACK status=BLOCKED event fires"

new_env wb2
R=$(mk_root proj); live_session forge-1 "$R"
# pre-existing blocked callback + open pending, event offset baselined at EOF
evlog_touch "$R"
pending_log "$R" p-pre coding codex-a "$(iso_ago 120)"
callback "$R" p-pre coding BLOCKED codex-a
run_check >/dev/null
assert_notified "p-pre.*BLOCKED" "pre-existing BLOCKED callback + open pending fires on first scan (EOF offset)"

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
assert_not_notified "p-done.*BLOCKED" "lingering DONE callback never fires WORKER-BLOCKED"

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
assert_notified   "p-both.*BLOCKED" "blocked callback fires"
assert_not_notified "p-both.*stalled" "WORKER-STALLED suppressed when a live BLOCKED callback covers the pending"

new_env ws3
R=$(mk_root proj); live_session forge-1 "$R"
pending_log "$R" p-old coding codex-a "$(iso_ago 6000000)"   # ~69d
run_check >/dev/null
assert_not_notified "p-old" "months-old open pending is residue (status-only), not a stall notification"
assert_status_has "STALE-PENDING.*p-old" "residue pending visible in status"

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
assert_notified "p-healthy.*BLOCKED" "a non-dict log entry does not suppress unrelated findings in the same root"

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

# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════"
green "PASS: $PASS"
[ "$FAIL" -gt 0 ] && red "FAIL: $FAIL" || green "FAIL: 0"
echo "═══════════════════════════════════════"
[ "$FAIL" -eq 0 ]
