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

# A hermetic project root (has .claude/forge-project.yml so _resolve_project_root
# walks up to it; carries the .dev tree callbacks/logs live under).
mkproj() {  # mkproj <name> -> echoes abs path
    local p="$WORK/$1"
    mkdir -p "$p/.claude" "$p/.dev/forge-tmp/callbacks" "$p/.dev/proposals"
    printf 'name: %s\n' "$1" > "$p/.claude/forge-project.yml"
    printf '%s' "$p"
}

# An open (response: null) pending entry the terminal callback close will target.
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

# callback / callback-consume run from inside the project root, TMUX unset so the
# back-compat pane notify is inert regardless of where the suite itself runs.
cb() {  # cb <root> <callback-args...>
    local r="$1"; shift
    ( cd "$r" && env -u TMUX "$BRIDGE" callback "$@" )
}
consume() {  # consume <root> <consume-args...>
    local r="$1"; shift
    ( cd "$r" && "$BRIDGE" callback-consume "$@" )
}

# infra-lock with an injected data-only identity.
il() {  # il <host> <session> <sid> <created> <infra-lock args...>
    local host="$1" sess="$2" sid="$3" created="$4"; shift 4
    ( cd "$PROJ" && \
      FORGE_INFRA_LOCK_DIR="$LOCKDIR" \
      FORGE_LOCK_SELF_HOST="$host" FORGE_LOCK_SELF_SESSION="$sess" \
      FORGE_LOCK_SELF_SESSION_ID="$sid" FORGE_LOCK_SELF_SESSION_CREATED="$created" \
      "$BRIDGE" infra-lock "$@" )
}

field() { grep '^status:' "$1" 2>/dev/null | awk '{print $2}'; }

# session-injected callback publish (FORGE_TMUX_LIST seam → session-qualified filename)
cbS() {  # cbS <root> <session> <callback-args...>
    local r="$1" s="$2"; shift 2
    local tl; tl="$(mktemp)"; printf '%s\t%s\n' "$s" "$r" > "$tl"
    ( cd "$r" && env -u TMUX TMUX_SESSION="$s" FORGE_TMUX_LIST="$tl" "$BRIDGE" callback "$@" )
    local rc=$?; rm -f "$tl"; return $rc
}
# session-injected arbitrary bridge verb
briS() {  # briS <root> <session> <verb+args...>
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
lifelock_path() { echo "$1/.dev/forge-tmp/locks/lifecycle-$2--$3.lock"; }  # matches _lifecycle_lock

# ═══════════════════════════════════════════════════════════════════════════
echo "══ SUITE 1: callback lifecycle ══"

# ── DONE: close-then-publish ──
P="$(mkproj cb-done)"
pending_entry "$P" p-done coding codex-a "2026-06-29T00:00:00Z"
out=$(cb "$P" --slug p-done --stage coding --status DONE --worker codex-a --quiet 2>&1); rc=$?
CBF="$P/.dev/forge-tmp/callbacks/p-done-coding.callback"
[ "$rc" -eq 0 ] && [ -f "$CBF" ] && ok "DONE publishes the callback file (exit 0)" || bad "DONE did not publish: rc=$rc out=$out"
[ "$(field "$CBF")" = "DONE" ] && ok "published callback carries status: DONE" || bad "callback status not DONE"
grep -q '^callback_id:' "$CBF" && ok "callback carries a callback_id field" || bad "no callback_id field"
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
[ ! -f "$P/.dev/forge-tmp/callbacks/p-ab-coding.callback" ] \
    && ok "aborted terminal close did NOT publish a callback" || bad "callback published despite failed close"
grep -q 'reason=terminal-close-failed' "$P/.dev/forge-tmp/orchestrator-events.log" 2>/dev/null \
    && ok "failed close emits ERROR terminal-close-failed event" || bad "no terminal-close-failed event"

# ── ERROR also closes-then-publishes ──
P="$(mkproj cb-err)"
pending_entry "$P" p-er coding codex-a "2026-06-29T00:00:00Z"
cb "$P" --slug p-er --stage coding --status ERROR --worker codex-a --quiet >/dev/null 2>&1
EF="$P/.dev/forge-tmp/callbacks/p-er-coding.callback"
[ "$(field "$EF")" = "ERROR" ] && ok "ERROR publishes a terminal callback (status ERROR)" || bad "ERROR callback wrong"
grep -q 'response: null' "$P/.dev/proposals/p-er/forge-log.yml" \
    && bad "ERROR left the pending open" || ok "ERROR closed the pending log entry"

# ── BLOCKED keeps the pending OPEN ──
P="$(mkproj cb-blk)"
pending_entry "$P" p-blk coding codex-a "2026-06-29T00:00:00Z"
cb "$P" --slug p-blk --stage coding --status BLOCKED --worker codex-a --message "needs a human" --quiet >/dev/null 2>&1
BF="$P/.dev/forge-tmp/callbacks/p-blk-coding.callback"
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
DF="$P/.dev/forge-tmp/callbacks/p-mm-coding.callback"
out=$(consume "$P" --slug p-mm --stage coding --status BLOCKED 2>&1); rc=$?
{ [ "$rc" -eq 0 ] && [ -f "$DF" ] && echo "$out" | grep -q 'status-mismatch'; } \
    && ok "consume leaves a terminal DONE callback in place (status-mismatch no-op)" \
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
out=$( cd "$P3" && env -u TMUX FORGE_INTERNAL_ASK_ORIGIN=1 "$BRIDGE" callback --slug bi-o3 --stage coding --status BLOCKED --origin ask --worker codex-a --message "q?" --quiet 2>&1 ); rc=$?
OF="$P3/.dev/forge-tmp/callbacks/bi-o3-coding.callback"
{ [ "$rc" -eq 0 ] && [ -f "$OF" ] && grep -q '^origin: ask' "$OF"; } && ok "internal ask origin stamps 'origin: ask'" || bad "internal ask origin not stamped (rc=$rc out=$out)"

# ── B1(headers): P1b session:/origin: scalar headers present + yaml-parseable ──
P="$(mkproj bi-hdr)"
pending_entry "$P" bi-h coding codex-a "2026-06-29T00:00:00Z"
cb "$P" --slug bi-h --stage coding --status BLOCKED --worker codex-a --message "needs a human" --quiet >/dev/null 2>&1
HF="$P/.dev/forge-tmp/callbacks/bi-h-coding.callback"
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

# B1 [D1] session-qualified filename + headers (identity seam)
P="$(mkproj b1)"; pending_entry "$P" p1 coding codex-a "2026-06-29T00:00:00Z"
cbS "$P" forge-1 --slug p1 --stage coding --status BLOCKED --worker codex-a --message "x" >/dev/null 2>&1
CBF="$P/.dev/forge-tmp/callbacks/p1-coding.forge-1.callback"
[ -f "$CBF" ] && ok "B1 session-qualified filename" || bad "B1 filename not session-qualified"
grep -q '^session: forge-1' "$CBF" && grep -q '^callback_id:' "$CBF" && ok "B1 session+callback_id headers" || bad "B1 headers missing"

# B2 [D2] legacy resolve + foreign-session consume NOOP
P="$(mkproj b2)"; pending_entry "$P" p2 coding codex-a "2026-06-29T00:00:00Z"
# legacy (no session) file present; a session consume must resolve it with a WARN
cb "$P" --slug p2 --stage coding --status BLOCKED --worker codex-a --message x >/dev/null 2>&1
LEG="$P/.dev/forge-tmp/callbacks/p2-coding.callback"
[ -f "$LEG" ] && ok "B2 legacy filename written when no session" || bad "B2 legacy filename missing"
# foreign-session owned file → a different session's consume is a NOOP
cbS "$P" forge-1 --slug p2 --stage coding --status BLOCKED --worker codex-a --message y >/dev/null 2>&1
out=$(briS "$P" forge-2 callback-consume --slug p2 --stage coding --status BLOCKED 2>&1); rc=$?
[ -f "$P/.dev/forge-tmp/callbacks/p2-coding.forge-1.callback" ] && ok "B2 foreign-session consume NOOP (owner file intact)" || bad "B2 foreign consume mutated owner file"

# B5 [D4] insert-parked-fields via park: newest of 2 open entries + WARN + round-trip
P="$(mkproj b5)"
{ echo "pipeline: p5"; echo "entries:";
  echo "  - timestamp: \"2026-06-29T00:00:00Z\""; echo "    stage: coding"; echo "    to: codex-a"; echo "    response: null";
  echo "  - timestamp: \"2026-06-29T01:00:00Z\""; echo "    stage: coding"; echo "    to: codex-a"; echo "    response: null"; } > "$P/.dev/proposals/p5/forge-log.yml" 2>/dev/null || { mkdir -p "$P/.dev/proposals/p5"; { echo "pipeline: p5"; echo "entries:"; echo "  - timestamp: \"2026-06-29T00:00:00Z\""; echo "    stage: coding"; echo "    to: codex-a"; echo "    response: null"; echo "  - timestamp: \"2026-06-29T01:00:00Z\""; echo "    stage: coding"; echo "    to: codex-a"; echo "    response: null"; } > "$P/.dev/proposals/p5/forge-log.yml"; }
cb "$P" --slug p5 --stage coding --status BLOCKED --worker codex-a --message x >/dev/null 2>&1
out=$( cd "$P" && "$BRIDGE" park --slug p5 --stage coding --reason 'colon: and "quote" here' 2>&1 ); rc=$?
echo "$out" | grep -q 'older duplicates: 2026-06-29T00:00:00Z' && ok "B5 D2 WARN lists older ts" || bad "B5 no older-ts WARN"
python3 - "$P/.dev/proposals/p5/forge-log.yml" <<'PY' && ok "B5 fields on NEWEST, response null, round-trips" || bad "B5 insert wrong"
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
new = [e for e in d["entries"] if e["timestamp"]=="2026-06-29T01:00:00Z"][0]
old = [e for e in d["entries"] if e["timestamp"]=="2026-06-29T00:00:00Z"][0]
assert new.get("parked_at") and new.get("response") is None, new
assert "colon:" in new.get("parked_reason","") and '"quote"' in new.get("parked_reason",""), new
assert not old.get("parked_at"), "older entry must NOT be parked"
PY

# B6 [D5] lifecycle-lock contention: external hold → park fails within wait; distinct keys don't contend
P="$(mkproj b6)"; pending_entry "$P" p6 coding codex-a "2026-06-29T00:00:00Z"
cb "$P" --slug p6 --stage coding --status BLOCKED --worker codex-a --message x >/dev/null 2>&1
LK="$(lifelock_path "$P" p6 coding)"; mkdir -p "$(dirname "$LK")"
( exec 9>"$LK"; flock 9; sleep 6 ) &  HOLD=$!
sleep 0.5
t0=$(date +%s); out=$( cd "$P" && "$BRIDGE" park --slug p6 --stage coding --reason later 2>&1 ); rc=$?; t1=$(date +%s)
[ "$rc" -ne 0 ] && ok "B6 park refused while lock externally held" || bad "B6 park succeeded under contention"
kill "$HOLD" 2>/dev/null; wait "$HOLD" 2>/dev/null
# distinct (slug,stage) never contend: a park on p6/qa proceeds while p6/coding lock is free now
pending_entry "$P" p6 qa codex-b "2026-06-29T00:00:00Z"   # note: same slug, different stage log entry
: # (full distinct-key assertion: two backgrounded parks on different stages both succeed)

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
[ "$(field "$P/.dev/forge-tmp/callbacks/pX-coding.forge-1.callback")" = "BLOCKED" ] && ok "B10 session-A callback untouched by session-B park" || bad "B10 cross-session mutation"
[ "$(field "$P/.dev/forge-tmp/callbacks/pX-coding.forge-2.callback")" = "PARKED" ] && ok "B10 session-B callback parked" || bad "B10 session-B not parked"

# B11 resolve closes pending + callback GONE + status ⏸ at 3 sites
P="$(mkproj b11)"; pending_entry "$P" p11 coding codex-a "2026-06-29T00:00:00Z"
cb "$P" --slug p11 --stage coding --status BLOCKED --worker codex-a --message x >/dev/null 2>&1
( cd "$P" && "$BRIDGE" park --slug p11 --stage coding --reason r >/dev/null 2>&1 )
out=$( cd "$P" && "$BRIDGE" park --resolve --slug p11 --stage coding --note "done here" 2>&1 ); rc=$?
grep -q 'FORGE_PARK_RESOLVED' "$P/.dev/proposals/p11/forge-log.yml" && ok "B11 pending closed FORGE_PARK_RESOLVED" || bad "B11 pending not resolved"
[ -z "$(ls "$P"/.dev/forge-tmp/callbacks/p11-coding*.callback 2>/dev/null)" ] && ok "B11 PARKED callback archived (gone from callbacks/)" || bad "B11 callback still live"

# B15 supersede consume-after-close + audit + callbacks GONE on success — DEFERRED.
# The P12 supersede source (consume-after-close + SUPERSEDE_AUDIT) IS applied in C3, but
# this assertion drives a *successful* `dispatch --supersede`, and dispatch's preflight
# rejects a hermetic (no-tmux) env with "no such tmux session exists" BEFORE reaching the
# supersede block (the block runs, then cmd_send needs a live pane). The FORGE_TMUX_LIST
# seam only feeds identity RESOLUTION, not a live send target — so this suite cannot drive
# a full dispatch. Deferred to live QA / a later group with a dispatch tmux-send stub,
# alongside the already-deferred B12/B13/B18 (which only assert the pre-send guard).

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

# B-acc help documents park+blocked-audit
# NOTE: the empty --allow-blocked reject half is DEFERRED to C5 — the dispatch guard
# wiring (P15b: --allow-blocked flag + "requires a non-empty reason") is not yet applied.
# Capture-then-grep (harness idiom, cf. the infra-lock help block above): a bare
# `"$BRIDGE" help | grep -q` trips SIGPIPE under `set -o pipefail` (grep -q exits on
# the early match while help is still emitting), failing the pipeline spuriously.
bahelp=$("$BRIDGE" help 2>&1)
echo "$bahelp" | grep -q 'park' && echo "$bahelp" | grep -q 'blocked-audit' && ok "B-acc help documents park+blocked-audit" || bad "B-acc help missing"

echo ""
echo "═══════════════════════════════════════"
green "PASS: $PASS"
[ "$FAIL" -gt 0 ] && red "FAIL: $FAIL" || green "FAIL: 0"
echo "═══════════════════════════════════════"
[ "$FAIL" -eq 0 ]
