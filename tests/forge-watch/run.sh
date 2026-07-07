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
wpromptf(){ # <root> <session> <pane> <ageSec> <task_id> [agent] [snippet]
  local a; a="$(attn "$1")"; cat > "$a/wprompt.$2.p$3.json" <<JSON
{"schema":"cc-attention/1","event":"userpromptsubmit","variant":"worker-prompt","session":"$2","root":"$1","pane_index":"$3","role":"worker","agent":"${6:-claude}","tmux_pane":"%$3","emitted_at":"$(iso_ago "$4")","task_id":"$5","dispatch_id":null,"dispatch_ids":[],"prompt_snippet":"${7:-typed work}","prompt_sha256":"abc"}
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
assert_status_missing "worker BLOCKED at coding" "WORKER-BLOCKED twin suppressed by the ask"

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

echo "── PANE-DONE: board row, no ring default; ring only with the knob ──"            # +4
new_env tpd
R=$(mk_root proj); live_session forge-1 "$R"
wstopf "$R" forge-1 0 30 ptask-1 claude "finished the widget"
assert_status_has "p0 — pane done" "wstop → PANE-DONE row"
run_status --board | python3 -c 'import json,sys;b=json.load(sys.stdin);r=[x for x in b["active"] if x["condition"]=="PANE-DONE"];assert r and r[0]["state"]=="done"' && ok "PANE-DONE is active/state=done in cc-board/1" || bad "board PANE-DONE wrong"
: > "$CAP"; run_check >/dev/null
[ "$(wc -l < "$CAP")" -eq 0 ] && ok "PANE-DONE does not ring by default (policy=never)" || bad "PANE-DONE rang without opt-in"
: > "$CAP"; FORGE_WATCH_NOTIFY_PANE_DONE=1 "$WATCH" check >/dev/null 2>&1
notified "pane done" && ok "PANE-DONE rings when FORGE_WATCH_NOTIFY_PANE_DONE=1" || bad "knob did not arm the ring"

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
PY

echo "── board-noise: old worker-done collapses to maintenance; latest in window ──"    # +3
new_env twin
R=$(mk_root proj); live_session forge-1 "$R"
export FORGE_WATCH_TASK_WINDOW_S=3600
wstopf "$R" forge-1 0 30   ptask-recent claude "recent turn"
wstopf "$R" forge-1 0 7200 ptask-old    claude "old turn"
assert_status_has "p0 — pane done" "recent worker-done surfaces as PANE-DONE"
run_status --board | python3 -c 'import json,sys;b=json.load(sys.stdin);ids={x["task_id"] for x in b["tasks"] if x["state"]=="done"};assert "ptask-recent" in ids and "ptask-old" not in ids, ids' && ok "tasks[] bounded to the window (old turn excluded)" || bad "window not applied"
run_status | grep -q "older worker-done" && ok "old worker-done collapses to a maintenance count" || bad "residue not collapsed"
unset FORGE_WATCH_TASK_WINDOW_S

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
run_status --board | python3 -c 'import json,sys;b=json.load(sys.stdin);assert b["schema"]=="cc-board/1" and b["hot"]==[] and b["tasks"]==[] and "maintenance" in b' && ok "empty everything → valid cc-board/1 (tasks[] present, no crash)" || bad "board degraded ungracefully"
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

echo "── SwiftBar plugin: renders counts + staleness from cc-board/1 JSON (hermetic) ──"   # +3
new_env tsb
PLUGIN="$(cd "$(dirname "$WATCH")/.." && pwd)/swiftbar/forge-board.5s.sh"
if [ -f "$PLUGIN" ]; then
  STUBF="$WORK/fakeforge"
  printf '#!/bin/bash\ncat <<JSON\n{"schema":"cc-board/1","hot":[{"condition":"NEEDS-ASK","session":"forge-1","acked":false}],"active":[],"tasks":[{"task_id":"cc-a"}],"maintenance":{"collapsed":true,"count":0,"rows":[]},"stale":false}\nJSON\n' > "$STUBF"; chmod +x "$STUBF"
  out=$(FORGE_BIN="$STUBF" bash "$PLUGIN")
  echo "$out" | head -1 | grep -q "forge 1!" && ok "SwiftBar menubar shows unseen hot count" || bad "swiftbar title wrong: $(echo "$out" | head -1)"
  echo "$out" | grep -q "1 task(s)" && ok "SwiftBar dropdown shows task count" || bad "swiftbar task count missing"
  STUBF2="$WORK/fakeforge2"
  printf '#!/bin/bash\ncat <<JSON\n{"schema":"cc-board/1","hot":[],"active":[],"tasks":[],"maintenance":{"collapsed":true,"count":0,"rows":[]},"stale":true,"heartbeat_age_s":300}\nJSON\n' > "$STUBF2"; chmod +x "$STUBF2"
  FORGE_BIN="$STUBF2" bash "$PLUGIN" | grep -q "watcher stale" && ok "SwiftBar self-reports watcher staleness" || bad "swiftbar staleness missing"
else
  echo "  (skip: plugin not found)"; ok "swiftbar plugin present"; ok "swiftbar task count (skipped)"; ok "swiftbar staleness (skipped)"
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

# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════"
green "PASS: $PASS"
[ "$FAIL" -gt 0 ] && red "FAIL: $FAIL" || green "FAIL: 0"
echo "═══════════════════════════════════════"
[ "$FAIL" -eq 0 ]
