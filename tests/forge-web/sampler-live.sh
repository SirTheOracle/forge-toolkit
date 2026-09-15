#!/bin/bash
# sampler-live.sh — the REAL-WATCHER experiment behind DoD 10.7.
#
# WHY THIS IS A SEPARATE SCRIPT AND NOT A PYTEST. Review R14: `watcher_spy.measure(...)` had
# no implementation, and a simulation cannot produce lock-occupancy or notification-latency
# numbers for the real binary. This drives the ACTUAL bin/forge-watch against an ISOLATED
# cache, an ISOLATED config dir and an EMPTY fixture root, so it measures the real thing while
# being incapable of touching the operator's state or acknowledging a real condition.
#
# It is DoD-only and is deliberately NOT registered in tests/run.sh — same as ack-live.sh —
# because it is non-hermetic (it runs the real watcher) and takes about two minutes.
#
# WHAT IT REPORTS, and what it does not:
#   - board scan duration p50/p95 and the rc distribution (0 vs 75);
#   - `forge-watch check` latency-to-a-COMPLETED-scan, with and without a competing board
#     sampler — this is the quantity the plan calls "notification delay", because a check
#     skipped on the lock cannot notify until some later tick completes a scan;
#   - measured web lock duty = (sum of board scan seconds) / wall time.
# It ASSERTS nothing about those numbers. They are inputs to tuning, and the plan's
# 1.94 s / 67 % figures are a REVIEWER'S SAMPLE from plan-review.md, not constants.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
WATCH="$ROOT/bin/forge-watch"
[ -x "$WATCH" ] || { echo "FAIL: $WATCH is not executable"; exit 1; }

TICKS="${SAMPLER_LIVE_TICKS:-30}"
TICK_S="${SAMPLER_LIVE_TICK_S:-2}"
SCANS="${SAMPLER_LIVE_SCANS:-20}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/forge-sampler-live.XXXXXX")"
cleanup() {
    [ -n "${BOARD_PID:-}" ] && kill "$BOARD_PID" 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

export FORGE_WATCH_CACHE_DIR="$WORK/cache"
export FORGE_WATCH_CONFIG_DIR="$WORK/config"
export FORGE_WATCH_NOTIFIER_BIN="$WORK/notifier"
export FORGE_WATCH_SINK_CAPTURE="$WORK/sink.tsv"
export FORGE_WATCH_TRIGGER=0
mkdir -p "$FORGE_WATCH_CACHE_DIR" "$FORGE_WATCH_CONFIG_DIR" "$WORK/fixture/.dev"

# An EMPTY fixture root: the watcher has real work to do (walk it, build state) but can find
# no condition, so nothing anywhere can be notified or acknowledged by this run.
printf '%s\n' "$WORK/fixture" > "$FORGE_WATCH_CONFIG_DIR/watch-roots"

# Recording notifier. Opportunistic: an empty fixture normally fires none, and a firing is
# recorded with a monotonic-enough stamp rather than assumed.
cat > "$FORGE_WATCH_NOTIFIER_BIN" <<'STUB'
#!/bin/bash
printf '%s\t%s\t%s\n' "$(python3 -c 'import time;print(time.time())')" "$1" "$2" \
    >> "${FORGE_WATCH_SINK_CAPTURE%.tsv}.notify.tsv"
STUB
chmod +x "$FORGE_WATCH_NOTIFIER_BIN"

now() { python3 -c 'import time;print("%.6f" % time.time())'; }

# ── Phase 1: board scan cost and rc distribution ────────────────────────────
echo "== phase 1: $SCANS board scans =="
: > "$WORK/scans.tsv"
for _i in $(seq 1 "$SCANS"); do
    t0="$(now)"
    "$WATCH" status --board >/dev/null 2>"$WORK/scan.err"
    rc=$?
    t1="$(now)"
    printf '%s\t%s\n' "$rc" "$(python3 -c "print('%.6f' % ($t1 - $t0))")" >> "$WORK/scans.tsv"
    sleep 0.2
done

# ── Phase 2: check latency WITHOUT a competing sampler ──────────────────────
run_ticks() {   # run_ticks <outfile>
    : > "$1"
    for _i in $(seq 1 "$TICKS"); do
        t0="$(now)"
        "$WATCH" check >/dev/null 2>"$WORK/tick.err"
        rc=$?
        t1="$(now)"
        skipped=0
        grep -q 'check skipped — state.lock held' "$WORK/tick.err" && skipped=1
        printf '%s\t%s\t%s\n' "$rc" "$skipped" \
            "$(python3 -c "print('%.6f' % ($t1 - $t0))")" >> "$1"
        sleep "$TICK_S"
    done
}
echo "== phase 2: $TICKS check ticks, no competing sampler =="
run_ticks "$WORK/ticks-solo.tsv"

# ── Phase 3: check latency WITH a competing board sampler ───────────────────
# The competing loop uses the SHIPPED cadence formula from sampler.py:
#   cooldown = min(60, max(5, 9 * ewma))
# seeded from the phase-1 p50, so this measures the sampler we actually ship rather than a
# worst case nobody will run.
P50="$(python3 - "$WORK/scans.tsv" <<'PY'
import statistics, sys
rows = [l.split('\t') for l in open(sys.argv[1]) if l.strip()]
ok = [float(d) for rc, d in rows if rc == '0']
print("%.4f" % (statistics.median(ok) if ok else 2.0))
PY
)"
COOLDOWN="$(python3 -c "print('%.2f' % min(60.0, max(5.0, 9.0 * $P50)))")"
echo "== phase 3: $TICKS check ticks, competing board sampler at ${COOLDOWN}s cooldown =="
(
    while :; do
        "$WATCH" status --board >/dev/null 2>&1
        sleep "$COOLDOWN"
    done
) & BOARD_PID=$!
run_ticks "$WORK/ticks-contended.tsv"
kill "$BOARD_PID" 2>/dev/null; BOARD_PID=""

# ── Report ──────────────────────────────────────────────────────────────────
python3 - "$WORK" "$COOLDOWN" <<'PY'
import os, statistics, sys

work, cooldown = sys.argv[1], float(sys.argv[2])


def rows(name):
    path = os.path.join(work, name)
    if not os.path.exists(path):
        return []
    return [l.rstrip('\n').split('\t') for l in open(path) if l.strip()]


scans = rows('scans.tsv')
ok = sorted(float(d) for rc, d in scans if rc == '0')
busy = sum(1 for rc, _ in scans if rc == '75')


def pct(values, q):
    if not values:
        return float('nan')
    index = min(len(values) - 1, int(round(q * (len(values) - 1))))
    return values[index]


print("\n--- board scans (real forge-watch, isolated cache, empty fixture root) ---")
print("attempts=%d  rc0=%d  rc75(busy)=%d  busy_rate=%.2f"
      % (len(scans), len(ok), busy, (busy / len(scans)) if scans else 0.0))
print("scan seconds: p50=%.3f  p95=%.3f  mean=%.3f"
      % (pct(ok, .50), pct(ok, .95), statistics.mean(ok) if ok else float('nan')))

for label, name in (("no sampler", 'ticks-solo.tsv'), ("with sampler", 'ticks-contended.tsv')):
    tick = rows(name)
    if not tick:
        continue
    skipped = sum(1 for _rc, s, _d in tick if s == '1')
    durations = sorted(float(d) for _rc, s, d in tick if s == '0')
    # latency-to-a-completed-scan: a skipped tick's work waits for the next completing one.
    latency, pending = [], 0.0
    for _rc, s, d in tick:
        if s == '1':
            pending += float(d) + cooldown * 0
        else:
            latency.append(pending + float(d))
            pending = 0.0
    latency.sort()
    print("\n--- check ticks (%s) ---" % label)
    print("ticks=%d  skipped_on_lock=%d  skip_rate=%.2f"
          % (len(tick), skipped, skipped / len(tick)))
    print("completed-scan seconds: p50=%.3f  p95=%.3f" % (pct(durations, .50), pct(durations, .95)))
    print("latency to a COMPLETED scan: p50=%.3f  p95=%.3f" % (pct(latency, .50), pct(latency, .95)))

total_scan = sum(ok)
wall = total_scan + cooldown * max(1, len(ok))
print("\n--- measured web lock duty ---")
print("sum(successful board scan seconds)=%.2f  modelled wall=%.2f  duty=%.3f"
      % (total_scan, wall, total_scan / wall if wall else 0.0))
print("cooldown used = min(60, max(5, 9 * p50)) = %.2fs" % cooldown)
print("\nNOTE: the 1.94 s / 67%% figures in final-plan.md are plan-review.md's sample, not")
print("      these numbers and not a constant. Record BOTH in the coder report.")
PY

echo
echo "sampler-live: DONE (measurements only; nothing was asserted, acknowledged or notified)"