#!/bin/bash
# tests/forge-bridge/run.sh — identity core for bin/forge-bridge (session-pin hardening).
# Harness modeled on tests/forge-infra-lock/run.sh: hermetic mkR roots, real tmux for
# the identity-sensitive core (skips the tmux section cleanly when tmux is absent),
# PASS/FAIL counters, EXIT-trap cleanup. bash-3.2-safe.
#
# HERMETIC GUARANTEES:
#   * All project state lives under $WORK; sessions are uniquely-named fbid*-$$ and
#     killed in the EXIT trap. Real forge sessions are never touched.
#   * FORGE_WATCH_TRIGGER=0 everywhere so _emit_event never pokes the real board.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BRIDGE="$ROOT/bin/forge-bridge"
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); printf '  ok: %s\n' "$1"; }
bad(){ FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$1"; }

export FORGE_WATCH_TRIGGER=0
WORK="$(mktemp -d "${TMPDIR:-/tmp}/fbid.XXXXXX")"; WORK="$(cd "$WORK" && pwd -P)"
S1="fbid1-$$"; S2="fbid2-$$"; S3="fbid3-$$"; S4="fbid4-$$"; GS="fbguard-$$"
trap 'tmux kill-session -t "$S1" 2>/dev/null; tmux kill-session -t "$S2" 2>/dev/null; tmux kill-session -t "$S3" 2>/dev/null; tmux kill-session -t "$S4" 2>/dev/null; tmux kill-session -t "$GS" 2>/dev/null; rm -rf "$WORK"' EXIT

# ---- Pure-helper extraction (no main dispatch) ----
FNS="$WORK/fns.sh"
{
  grep -m1 '^_valid_session_name()' "$BRIDGE"
  sed -n '/^ownership_root()/,/^}$/p; /^_same_root_sessions()/,/^}$/p' "$BRIDGE"
} > "$FNS"
# shellcheck disable=SC1090
. "$FNS"

# mkR <name> — hermetic project root with forge-project.yml + expected_root pin.
mkR(){ local d="$WORK/$1"; mkdir -p "$d/.claude" "$d/.dev/proposals" "$d/.dev/forge-tmp/callbacks"; printf 'name: %s\nforge:\n  expected_root: %s\n' "$1" "$d" > "$d/.claude/forge-project.yml"; printf '%s' "$d"; }

echo "== ownership_root / _same_root_sessions (pure helpers) =="

rootA="$(mkR rootA)"; rootB="$(mkR rootB)"; rootC="$(mkR rootC)"; rootD="$(mkR rootD)"
mkdir -p "$rootA/sub/dir"

# T-OWN-1: subdir-created path resolves to the same ownership root as the root itself.
if [ "$(ownership_root "$rootA/sub/dir")" = "$(ownership_root "$rootA")" ] \
   && [ "$(ownership_root "$rootA")" = "$rootA" ]; then
    ok "T-OWN-1 subdir collapses to project root"
else
    bad "T-OWN-1 subdir collapses to project root (got: $(ownership_root "$rootA/sub/dir") vs $(ownership_root "$rootA"))"
fi

# T-OWN-2: symlink alias canonicalizes to the same root as the real path.
ln -s "$rootA" "$WORK/aliasA"
if [ "$(ownership_root "$WORK/aliasA")" = "$(ownership_root "$rootA")" ]; then
    ok "T-OWN-2 symlink alias collapses"
else
    bad "T-OWN-2 symlink alias collapses (got: $(ownership_root "$WORK/aliasA"))"
fi

# T-SRS-1 / T-SRS-2 via the FORGE_TMUX_LIST seam.
LIST="$WORK/tmuxlist"
printf 'sessA\t%s\nsessB\t%s/sub/dir\nother\t%s\n' "$rootA" "$rootA" "$rootB" > "$LIST"
srs_out="$(FORGE_TMUX_LIST="$LIST" _same_root_sessions "$rootA")"
if printf '%s\n' "$srs_out" | grep -qx 'sessA' && printf '%s\n' "$srs_out" | grep -qx 'sessB'; then
    ok "T-SRS-1 lists BOTH custom-named same-root sessions"
else
    bad "T-SRS-1 lists BOTH custom-named same-root sessions (got: $(printf '%s' "$srs_out" | tr '\n' ' '))"
fi
if printf '%s\n' "$srs_out" | grep -qx 'other'; then
    bad "T-SRS-2 unrelated-root session excluded"
else
    ok "T-SRS-2 unrelated-root session excluded"
fi

# ---- Sourced-gate test (hermetic, no tmux needed): fail-closed class ----
echo "== require_identity fail-closed (sourced) =="
GFNS="$WORK/gatefns.sh"
{
  echo '_emit_event(){ :; }'
  echo 'require_pane_count(){ return 0; }'
  echo 'SESSION=""'
  echo 'FORGE_REQUIRED_PANES=5'
  grep -m1 '^_valid_session_name()' "$BRIDGE"
  sed -n '/^ownership_root()/,/^}$/p; /^_same_root_sessions()/,/^}$/p; /^session_path_of()/,/^}$/p; /^_forge_identity()/,/^}$/p; /^_print_identity_block()/,/^}$/p; /^_identity_refuse()/,/^}$/p; /^require_identity()/,/^}$/p' "$BRIDGE"
} > "$GFNS"
gout="$(cd "$rootD" && bash -c ". '$GFNS'; require_identity bogus-cmd bogusclass" 2>&1)"; grc=$?
if [ "$grc" -ne 0 ] && printf '%s' "$gout" | grep -q "no identity class"; then
    ok "T-CLASS-FAILCLOSED unknown class refuses (fail-closed)"
else
    bad "T-CLASS-FAILCLOSED unknown class refuses (rc=$grc out=$gout)"
fi

# ---- Flag-parser usage errors (headless, real bridge) ----
echo "== global flag parser =="
"$BRIDGE" --target-session x read 0 >/dev/null 2>&1; [ $? -eq 2 ] \
    && ok "T-CLASS-USAGE-1 --target-session without --cross-session → exit 2" \
    || bad "T-CLASS-USAGE-1 --target-session without --cross-session → exit 2"
"$BRIDGE" --cross-session read 0 >/dev/null 2>&1; [ $? -eq 2 ] \
    && ok "T-CLASS-USAGE-2 --cross-session on non-send/callback → exit 2" \
    || bad "T-CLASS-USAGE-2 --cross-session on non-send/callback → exit 2"

# ---- Headless (no-tmux) resolution states ----
echo "== headless resolution =="
hout="$(cd "$rootD" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION "$BRIDGE" identity 2>&1)"; hrc=$?
if [ "$hrc" -eq 3 ] && printf '%s' "$hout" | grep -q 'identity_state=UNAVAILABLE'; then
    ok "T-ID-NOTMUX-0 zero same-root live → UNAVAILABLE, exit 3"
else
    bad "T-ID-NOTMUX-0 zero same-root live → UNAVAILABLE, exit 3 (rc=$hrc out=$hout)"
fi

# T-LEGACY-HEADER: headered advisory file → diagnostics read the first non-comment
# line; bare legacy files keep working; the value never routes (diagnostics-only).
mkdir -p "$rootD/.dev"
printf '# advisory only — identity is the host pane\nheadered-name\n' > "$rootD/.dev/.forge-session"
lh="$(cd "$rootD" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION "$BRIDGE" identity 2>&1)"
if printf '%s' "$lh" | grep -q 'legacy_file_session=headered-name' \
   && printf '%s' "$lh" | grep -q 'identity_state=UNAVAILABLE'; then
    ok "T-LEGACY-HEADER headered file read as diagnostics, never routes"
else
    bad "T-LEGACY-HEADER headered file read as diagnostics (out=$lh)"
fi
printf 'bare-name\n' > "$rootD/.dev/.forge-session"
lb="$(cd "$rootD" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION "$BRIDGE" identity 2>&1)"
printf '%s' "$lb" | grep -q 'legacy_file_session=bare-name' \
    && ok "T-LEGACY-HEADER bare legacy file still readable (migration no-op)" \
    || bad "T-LEGACY-HEADER bare legacy file still readable (out=$lb)"
rm -f "$rootD/.dev/.forge-session"

# ---- Real-tmux section ----
if ! command -v tmux >/dev/null 2>&1; then
    echo "SKIP: tmux unavailable — real-tmux identity tests skipped"
    echo
    printf 'forge-bridge: %d passed, %d failed\n' "$PASS" "$FAIL"
    [ "$FAIL" -eq 0 ]; exit $?
fi

echo "== real-tmux identity core =="
tmux new-session -d -s "$S1" -x 200 -y 50 -c "$rootA"
tmux split-window -d -t "$S1:0" -c "$rootA"
tmux new-session -d -s "$S2" -x 220 -y 50 -c "$rootA"
i=0; while [ $i -lt 4 ]; do tmux split-window -d -t "$S2:0" -c "$rootA"; tmux select-layout -t "$S2:0" tiled >/dev/null 2>&1; i=$((i+1)); done
S1P0="$(tmux display-message -p -t "$S1:0.0" '#{pane_id}')"
S2P0="$(tmux display-message -p -t "$S2:0.0" '#{pane_id}')"
sleep 1

# run_in_pane <pane-target> <name> <command string>  (command may not contain unescaped ")
run_in_pane(){
    local pane="$1" name="$2"; shift 2
    local o="$WORK/out.$name"
    : > "$o"
    tmux send-keys -t "$pane" "{ $* ; } > $o 2>&1; echo DONE_\$? >> $o" Enter
    local i=0
    while [ $i -lt 60 ]; do grep -q '^DONE_' "$o" 2>/dev/null && return 0; sleep 0.5; i=$((i+1)); done
    echo "TIMEOUT" >> "$o"; return 1
}
rc_of(){ sed -n 's/^DONE_//p' "$WORK/out.$1" | tail -1; }
out_of(){ cat "$WORK/out.$1"; }

# T-ID-INPANE
run_in_pane "$S1:0.0" inpane "FORGE_WATCH_TRIGGER=0 $BRIDGE identity"
if [ "$(rc_of inpane)" = "0" ] && out_of inpane | grep -q "identity_state=MATCH" \
   && out_of inpane | grep -q "host_session=$S1" && out_of inpane | grep -q "target_source=host"; then
    ok "T-ID-INPANE in-pane MATCH via host probe"
else
    bad "T-ID-INPANE in-pane MATCH via host probe ($(out_of inpane | tr '\n' ' '))"
fi

# T-ID-CMD-NOEVAL (reuses inpane output)
if out_of inpane | grep -q 'host_session=' && ! out_of inpane | grep -q 'export ' && ! out_of inpane | grep -q 'eval '; then
    ok "T-ID-CMD-NOEVAL descriptor only — no export/eval in output"
else
    bad "T-ID-CMD-NOEVAL descriptor only — no export/eval in output"
fi

# T-ID-ENVMATCH
run_in_pane "$S1:0.0" envmatch "TMUX_SESSION=$S1 FORGE_WATCH_TRIGGER=0 $BRIDGE identity"
[ "$(rc_of envmatch)" = "0" ] && out_of envmatch | grep -q "identity_state=MATCH" \
    && ok "T-ID-ENVMATCH stamp==host → MATCH" \
    || bad "T-ID-ENVMATCH stamp==host → MATCH ($(out_of envmatch | tr '\n' ' '))"

# T-ID-ENVMISMATCH (the incident replay: stale env stamp ≠ live host)
run_in_pane "$S1:0.0" envmis "TMUX_SESSION=$S2 FORGE_WATCH_TRIGGER=0 $BRIDGE identity"
if [ "$(rc_of envmis)" = "3" ] && out_of envmis | grep -q "identity_state=MISMATCH" \
   && out_of envmis | grep -q "target_session=$S1"; then
    ok "T-ID-ENVMISMATCH stale stamp → MISMATCH, target stays host"
else
    bad "T-ID-ENVMISMATCH stale stamp → MISMATCH, target stays host ($(out_of envmis | tr '\n' ' '))"
fi

# T-ID-LIVESTALE (MINOR-1: TMUX_PANE pointing at a live same-root OTHER session)
run_in_pane "$S1:0.0" livestale "TMUX_SESSION=$S1 TMUX_PANE=$S2P0 FORGE_WATCH_TRIGGER=0 $BRIDGE identity"
if [ "$(rc_of livestale)" = "3" ] && out_of livestale | grep -q "identity_state=MISMATCH" \
   && out_of livestale | grep -q "host_session=$S2"; then
    ok "T-ID-LIVESTALE live-stale TMUX_PANE + stamp → MISMATCH (env corroborator)"
else
    bad "T-ID-LIVESTALE live-stale TMUX_PANE + stamp → MISMATCH ($(out_of livestale | tr '\n' ' '))"
fi

# T-ID-WRONGCHECKOUT (host rooted rootA, cwd rootB)
run_in_pane "$S1:0.0" wrongco "( cd $rootB && FORGE_WATCH_TRIGGER=0 $BRIDGE identity )"
out_of wrongco | grep -q "identity_state=MISMATCH" \
    && ok "T-ID-WRONGCHECKOUT host-root ≠ cwd-root → MISMATCH" \
    || bad "T-ID-WRONGCHECKOUT host-root ≠ cwd-root → MISMATCH ($(out_of wrongco | tr '\n' ' '))"

# T-ID-UNAVAIL (dead/invalid TMUX_PANE)
run_in_pane "$S1:0.0" unavail "TMUX_PANE=%9999 FORGE_WATCH_TRIGGER=0 $BRIDGE identity"
[ "$(rc_of unavail)" = "3" ] && out_of unavail | grep -q "identity_state=UNAVAILABLE" \
    && ok "T-ID-UNAVAIL dead TMUX_PANE → UNAVAILABLE" \
    || bad "T-ID-UNAVAIL dead TMUX_PANE → UNAVAILABLE ($(out_of unavail | tr '\n' ' '))"

# T-ID-PROBE-TARGETED (active pane ≠ caller pane; probe must resolve the CALLER)
tmux select-pane -t "$S1:0.1"
run_in_pane "$S1:0.0" probetgt "FORGE_WATCH_TRIGGER=0 $BRIDGE identity"
tmux select-pane -t "$S1:0.0"
out_of probetgt | grep -q "host_pane=$S1P0" \
    && ok "T-ID-PROBE-TARGETED resolves the CALLER's pane, not the active pane" \
    || bad "T-ID-PROBE-TARGETED resolves the CALLER's pane ($(out_of probetgt | tr '\n' ' '))"

# T-ID-NOTMUX-1 (exactly one same-root live session)
tmux new-session -d -s "$S3" -x 120 -y 30 -c "$rootC"
sleep 0.5
n1out="$(cd "$rootC" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION "$BRIDGE" identity 2>&1)"; n1rc=$?
if [ "$n1rc" -eq 0 ] && printf '%s' "$n1out" | grep -q "target_source=unique-root-candidate" \
   && printf '%s' "$n1out" | grep -q "target_session=$S3"; then
    ok "T-ID-NOTMUX-1 single same-root candidate → MATCH"
else
    bad "T-ID-NOTMUX-1 single same-root candidate → MATCH (rc=$n1rc out=$n1out)"
fi

# T-CTX-SINGLE (headless, 1 same-root, real context file → renders, not the degrade)
printf 'pipeline: ctx-single-test\nnotes: []\n' > "$rootC/.dev/forge-context.$S3.yml"
ctx1="$(cd "$rootC" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION "$BRIDGE" context 2>&1)"; ctx1rc=$?
if [ "$ctx1rc" -eq 0 ] && ! printf '%s' "$ctx1" | grep -q "no active pipeline"; then
    ok "T-CTX-SINGLE headless single-session context renders"
else
    bad "T-CTX-SINGLE headless single-session context renders (rc=$ctx1rc out=$ctx1)"
fi

# Second same-root session → ambiguity cases
tmux new-session -d -s "$S4" -x 120 -y 30 -c "$rootC"
sleep 0.5

# T-ID-NOTMUX-2
n2out="$(cd "$rootC" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION "$BRIDGE" identity 2>&1)"; n2rc=$?
[ "$n2rc" -eq 3 ] && printf '%s' "$n2out" | grep -q "identity_state=AMBIGUOUS" \
    && ok "T-ID-NOTMUX-2 two same-root candidates → AMBIGUOUS, exit 3" \
    || bad "T-ID-NOTMUX-2 two same-root candidates → AMBIGUOUS (rc=$n2rc out=$n2out)"

# T-CTX-DEGRADE (headless, >1 same-root → degrades, exit 0)
ctx2="$(cd "$rootC" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION FORGE_IDENTITY_ENFORCE=1 "$BRIDGE" context 2>&1)"; ctx2rc=$?
if [ "$ctx2rc" -eq 0 ] && printf '%s' "$ctx2" | grep -q "no active pipeline"; then
    ok "T-CTX-DEGRADE headless ambiguous context degrades (no refuse, enforce on)"
else
    bad "T-CTX-DEGRADE headless ambiguous context degrades (rc=$ctx2rc out=$ctx2)"
fi

# T-BG-DETACHED (no TMUX_PANE, 2 same-root, enforce → host-session refuses)
bgd="$(cd "$rootC" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION FORGE_IDENTITY_ENFORCE=1 "$BRIDGE" add-note detached-note 2>&1)"; bgdrc=$?
[ "$bgdrc" -ne 0 ] && printf '%s' "$bgd" | grep -q "identity AMBIGUOUS" \
    && ok "T-BG-DETACHED detached ambiguous mutator refused under enforce" \
    || bad "T-BG-DETACHED detached ambiguous mutator refused under enforce (rc=$bgdrc out=$bgd)"

# ---- Mutator gating (report-only vs enforce) ----
echo "== mutator gating =="

# T-ENFORCE-OFF: in-pane MISMATCH (wrong checkout) + report-only → records, proceeds
run_in_pane "$S1:0.0" enfoff "( cd $rootB && FORGE_WATCH_TRIGGER=0 FORGE_IDENTITY_ENFORCE=0 $BRIDGE log --slug mm --stage coding --from claude --to codex-a --prompt p )"
if [ "$(rc_of enfoff)" = "0" ] && [ -f "$rootB/.dev/proposals/mm/forge-log.yml" ] \
   && grep -q "state=MISMATCH" "$rootB/.dev/forge-tmp/orchestrator-events.log" 2>/dev/null; then
    ok "T-ENFORCE-OFF report-only MISMATCH mutator proceeds + IDENTITY event recorded"
else
    bad "T-ENFORCE-OFF report-only MISMATCH mutator proceeds (rc=$(rc_of enfoff))"
fi

# T-MUT-LOG / T-ENFORCE-ON: same but enforce=1 → refused + identity-mismatch event
run_in_pane "$S1:0.0" enfon "( cd $rootB && FORGE_WATCH_TRIGGER=0 FORGE_IDENTITY_ENFORCE=1 $BRIDGE log --slug mm2 --stage coding --from claude --to codex-a --prompt p )"
if [ "$(rc_of enfon)" != "0" ] && [ ! -f "$rootB/.dev/proposals/mm2/forge-log.yml" ] \
   && out_of enfon | grep -q "identity MISMATCH" \
   && grep -q "identity-mismatch:" "$rootB/.dev/forge-tmp/orchestrator-events.log" 2>/dev/null; then
    ok "T-MUT-LOG/T-ENFORCE-ON enforce MISMATCH mutator refused + identity-mismatch event"
else
    bad "T-MUT-LOG/T-ENFORCE-ON enforce MISMATCH mutator refused (rc=$(rc_of enfon) out=$(out_of enfon | tr '\n' ' '))"
fi

# T-MUT-OK: in-pane MATCH log writes the pending with the host session stamped
run_in_pane "$S1:0.0" mutok "FORGE_WATCH_TRIGGER=0 $BRIDGE log --slug mutok --stage coding --from claude --to codex-a --prompt p"
if [ "$(rc_of mutok)" = "0" ] && grep -q "session: $S1" "$rootA/.dev/proposals/mutok/forge-log.yml" 2>/dev/null; then
    ok "T-MUT-OK in-pane MATCH log writes pending stamped with host session"
else
    bad "T-MUT-OK in-pane MATCH log writes pending (rc=$(rc_of mutok))"
fi

# T-BG-AGENT: a CHILD process of the pane (inherited TMUX_PANE) resolves the same host
printf 'pipeline: bg-agent-test\nnotes: []\n' > "$rootA/.dev/forge-context.$S1.yml"
run_in_pane "$S1:0.0" bgagent "bash -c 'FORGE_WATCH_TRIGGER=0 $BRIDGE add-note bg-agent-note-xyz'"
if [ "$(rc_of bgagent)" = "0" ] && grep -q "bg-agent-note-xyz" "$rootA/.dev/forge-context.$S1.yml" 2>/dev/null; then
    ok "T-BG-AGENT child process inherits TMUX_PANE → correct session context"
else
    bad "T-BG-AGENT child process inherits TMUX_PANE (rc=$(rc_of bgagent))"
fi

# ---- Dual-mode + cross-session validation ----
echo "== dual-mode / cross-session =="

# T-CLASS-DUAL-DEFAULT: no flags, headless, enforce → host-pane class refuses (UNAVAILABLE)
dd="$(cd "$rootD" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION FORGE_IDENTITY_ENFORCE=1 "$BRIDGE" send claude x 2>&1)"; ddrc=$?
[ "$ddrc" -ne 0 ] && printf '%s' "$dd" | grep -q "identity UNAVAILABLE" \
    && ok "T-CLASS-DUAL-DEFAULT flagless send stays host-pane (refused headless)" \
    || bad "T-CLASS-DUAL-DEFAULT flagless send stays host-pane (rc=$ddrc out=$dd)"

# T-CLASS-DUAL-SEND: flags upgrade to target-scoped — headless caller proceeds under enforce
ds="$(cd "$rootA" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION FORGE_WATCH_TRIGGER=0 FORGE_IDENTITY_ENFORCE=1 "$BRIDGE" send --target-session "$S2" --cross-session claude dual-ok-marker 2>&1)"; dsrc=$?
[ "$dsrc" -eq 0 ] \
    && ok "T-CLASS-DUAL-SEND validated flags upgrade send to target-scoped (proceeds)" \
    || bad "T-CLASS-DUAL-SEND validated flags upgrade send to target-scoped (rc=$dsrc out=$ds)"

# T-ID-CROSS / T-CROSS-VALID: in-pane declared cross-session send to a correctly-rooted target
run_in_pane "$S1:0.0" crossval "FORGE_WATCH_TRIGGER=0 FORGE_IDENTITY_ENFORCE=1 $BRIDGE send --target-session $S2 --cross-session claude cross-ok-marker"
sleep 1
if [ "$(rc_of crossval)" = "0" ] && tmux capture-pane -p -t "$S2:0.1" | grep -q "cross-ok-marker"; then
    ok "T-CROSS-VALID declared cross-session send lands in the target's pane 1"
else
    bad "T-CROSS-VALID declared cross-session send lands in target (rc=$(rc_of crossval))"
fi

# T-CROSS-WRONGROOT: FORGE_TMUX_LIST claims S2 is rooted elsewhere → root validation refuses
LISTW="$WORK/tmuxlist-wrong"; printf '%s\t%s\n' "$S2" "$rootB" > "$LISTW"
run_in_pane "$S1:0.0" crosswrong "FORGE_WATCH_TRIGGER=0 FORGE_IDENTITY_ENFORCE=1 FORGE_TMUX_LIST=$LISTW $BRIDGE send --target-session $S2 --cross-session claude nope"
if [ "$(rc_of crosswrong)" != "0" ] && out_of crosswrong | grep -q "rooted at"; then
    ok "T-CROSS-WRONGROOT target rooted elsewhere → refused (class-3 root validation)"
else
    bad "T-CROSS-WRONGROOT target rooted elsewhere → refused (rc=$(rc_of crosswrong) out=$(out_of crosswrong | tr '\n' ' '))"
fi

# ---- Callback / terminal-close isolation ----
echo "== callback / terminal-close =="

# pending_entry <root> <slug> <stage> <to> <ts> [session]
pending_entry(){
    local d="$1/.dev/proposals/$2"; mkdir -p "$d"
    if [ -n "${6:-}" ]; then
        cat > "$d/forge-log.yml" <<EOF
pipeline: $2
entries:
  - timestamp: "$5"
    stage: $3
    to: $4
    session: $6
    response: null
EOF
    else
        cat > "$d/forge-log.yml" <<EOF
pipeline: $2
entries:
  - timestamp: "$5"
    stage: $3
    to: $4
    response: null
EOF
    fi
}

# T-CB-HEADLESS-DONE: headless caller, zero live same-root, empty-session pending, enforce=1
pending_entry "$rootD" hdl coding codex-a "2026-07-11T00:00:00Z"
cbh="$(cd "$rootD" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION FORGE_IDENTITY_ENFORCE=1 "$BRIDGE" callback --slug hdl --stage coding --status DONE --worker codex-a --quiet 2>&1)"; cbhrc=$?
if [ "$cbhrc" -eq 0 ] && [ -f "$rootD/.dev/forge-tmp/callbacks/hdl-coding.callback" ] \
   && ! grep -q "response: null" "$rootD/.dev/proposals/hdl/forge-log.yml"; then
    ok "T-CB-HEADLESS-DONE headless callback proceeds under enforce (host-degrade)"
else
    bad "T-CB-HEADLESS-DONE headless callback proceeds under enforce (rc=$cbhrc out=$cbh)"
fi

# T-CLOSE-ISOLATION: two same-slug/stage/to pendings for S1 and S2; close from S1 pane
d="$rootA/.dev/proposals/xiso"; mkdir -p "$d"
cat > "$d/forge-log.yml" <<EOF
pipeline: xiso
entries:
  - timestamp: "2026-07-11T00:00:01Z"
    stage: coding
    to: codex-a
    session: $S1
    response: null
  - timestamp: "2026-07-11T00:00:02Z"
    stage: coding
    to: codex-a
    session: $S2
    response: null
EOF
run_in_pane "$S1:0.0" closeiso "FORGE_WATCH_TRIGGER=0 $BRIDGE callback --slug xiso --stage coding --status DONE --worker codex-a --quiet"
nulls=$(grep -c "response: null" "$d/forge-log.yml" 2>/dev/null)
if [ "$(rc_of closeiso)" = "0" ] && [ "$nulls" = "1" ] \
   && python3 -c "
import yaml,sys
d=yaml.safe_load(open('$d/forge-log.yml'))
e=[x for x in d['entries'] if x.get('session')=='$S2']
sys.exit(0 if e and e[0]['response'] is None else 1)
"; then
    ok "T-CLOSE-ISOLATION close from S1 leaves S2's coincident pending open"
else
    bad "T-CLOSE-ISOLATION close from S1 leaves S2's pending open (rc=$(rc_of closeiso) nulls=$nulls)"
fi

# T-CLOSE-HEADLESS-XSESSION-REFUSED: empty-caller close over pendings spanning 2 sessions
d2="$rootA/.dev/proposals/xhdl"; mkdir -p "$d2"
cat > "$d2/forge-log.yml" <<EOF
pipeline: xhdl
entries:
  - timestamp: "2026-07-11T00:00:03Z"
    stage: coding
    to: codex-a
    session: $S1
    response: null
  - timestamp: "2026-07-11T00:00:04Z"
    stage: coding
    to: codex-a
    session: $S2
    response: null
EOF
xh="$(cd "$rootA" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION FORGE_IDENTITY_ENFORCE=1 "$BRIDGE" callback --slug xhdl --stage coding --status DONE --worker codex-a --quiet 2>&1)"; xhrc=$?
xnulls=$(grep -c "response: null" "$d2/forge-log.yml" 2>/dev/null)
if [ "$xhrc" -ne 0 ] && [ "$xnulls" = "2" ]; then
    ok "T-CLOSE-HEADLESS-XSESSION-REFUSED multi-session close refused, nothing closed"
else
    bad "T-CLOSE-HEADLESS-XSESSION-REFUSED (rc=$xhrc nulls=$xnulls out=$xh)"
fi

# T-CB-NOTIFY-HOST: non-quiet self-callback notifies the HOST's pane 1 (target==host)
pending_entry "$rootA" ntfy coding codex-a "2026-07-11T00:00:05Z" "$S1"
run_in_pane "$S1:0.0" notify "FORGE_WATCH_TRIGGER=0 $BRIDGE callback --slug ntfy --stage coding --status DONE --worker codex-a --message notify-marker-xyz"
sleep 1
if [ "$(rc_of notify)" = "0" ] && tmux capture-pane -p -t "$S1:0.1" | grep -q "notify-marker-xyz"; then
    ok "T-CB-NOTIFY-HOST non-quiet callback notify lands in the host's pane 1"
else
    bad "T-CB-NOTIFY-HOST non-quiet callback notify lands in host pane 1 (rc=$(rc_of notify))"
fi

# ---- Diagnostics under bad identity (R7) ----
echo "== diagnostics (R7) =="

# T-PREFLIGHT-ID: preflight under MISMATCH still runs, prints descriptor + yaml field, exit 0
run_in_pane "$S1:0.0" pfid "TMUX_SESSION=$S2 FORGE_WATCH_TRIGGER=0 $BRIDGE preflight"
if [ "$(rc_of pfid)" = "0" ] && out_of pfid | grep -q "identity_state=MISMATCH" \
   && out_of pfid | grep -q "identity_state:     MISMATCH"; then
    ok "T-PREFLIGHT-ID preflight runs + carries identity under MISMATCH (exit 0)"
else
    bad "T-PREFLIGHT-ID preflight carries identity under MISMATCH (rc=$(rc_of pfid))"
fi

# T-HEALTH-MISMATCH: health under MISMATCH + enforce STILL RUNS and prints SUMMARY
run_in_pane "$S1:0.0" hlth "TMUX_SESSION=$S2 FORGE_WATCH_TRIGGER=0 FORGE_IDENTITY_ENFORCE=1 $BRIDGE health"
if out_of hlth | grep -q "^SUMMARY " && out_of hlth | grep -q "identity_state=MISMATCH"; then
    ok "T-HEALTH-MISMATCH health runs-and-prints under enforce MISMATCH (R7)"
else
    bad "T-HEALTH-MISMATCH health runs-and-prints under enforce MISMATCH ($(out_of hlth | tail -2 | tr '\n' ' '))"
fi

# T-CANON-PREFLIGHT: symlinked cwd → directory_state OK (symmetric canonicalization)
cp="$(cd "$WORK/aliasA" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION "$BRIDGE" preflight 2>&1)"
printf '%s' "$cp" | grep -q "directory_state:    OK" \
    && ok "T-CANON-PREFLIGHT symlinked checkout → directory_state OK" \
    || bad "T-CANON-PREFLIGHT symlinked checkout → directory_state OK ($(printf '%s' "$cp" | grep directory_state))"

# ---- Blocked-item all-boundary guard / supersede (P0 live debt) ----
echo "== blocked-item live guard / supersede (P0) =="
GROOT="$(mkR guard-root)"
GLOCKS="$GROOT/.dev/forge-tmp/guard-infra-locks"; mkdir -p "$GLOCKS"
tmux new-session -d -s "$GS" -x 220 -y 50 -c "$GROOT"
i=0; while [ "$i" -lt 4 ]; do tmux split-window -d -t "$GS:0" -c "$GROOT"; tmux select-layout -t "$GS:0" tiled >/dev/null 2>&1; i=$((i+1)); done
GINC="$(tmux display-message -p -t "$GS:0.0" '#{session_created}')"
GPROMPTS="$WORK/guard-prompts"; mkdir -p "$GPROMPTS"
printf 'P0 live guard prompt for {{slug}} at {{stage}} to {{worker}}\n' > "$GPROMPTS/adhoc.txt"

guard_wait_pane_ready(){
    local pane="$1" ready="$GROOT/.dev/forge-tmp/guard-pane-$1.ready" attempt=0 poll
    rm -f "$ready"
    while [ "$attempt" -lt 15 ]; do
        tmux send-keys -t "$GS:0.$pane" -l ": > \"$ready\"" 2>/dev/null || true
        tmux send-keys -t "$GS:0.$pane" Enter 2>/dev/null || true
        poll=0
        while [ "$poll" -lt 10 ]; do
            [ -f "$ready" ] && return 0
            sleep 0.1
            poll=$((poll+1))
        done
        attempt=$((attempt+1))
    done
    return 1
}
guard_pane=0
while [ "$guard_pane" -lt 5 ]; do
    guard_wait_pane_ready "$guard_pane" \
        || { bad "T-GUARD-PANE-READY-$guard_pane"; exit 1; }
    guard_pane=$((guard_pane+1))
done
rm -f "$GROOT"/.dev/forge-tmp/guard-pane-*.ready

guard_log(){
    local slug="$1" stage="$2" worker="$3" tag="$4"
    run_in_pane "$GS:0.0" "$tag-log" "( cd $GROOT && FORGE_WATCH_TRIGGER=0 $BRIDGE log --slug $slug --stage $stage --from claude --to $worker --prompt p0-$tag )"
    [ "$(rc_of "$tag-log")" = 0 ]
}
guard_block(){
    local slug="$1" stage="$2" worker="$3" pane="$4" tag="$5" origin="${6:-worker}"
    guard_log "$slug" "$stage" "$worker" "$tag" || return 1
    if [ "$origin" = ask ]; then
        run_in_pane "$GS:0.$pane" "$tag-cb" "( cd $GROOT && FORGE_WATCH_TRIGGER=0 FORGE_INTERNAL_ASK_ORIGIN=1 $BRIDGE callback --slug $slug --stage $stage --status BLOCKED --origin ask --worker $worker --message p0-$tag --quiet )"
    else
        run_in_pane "$GS:0.$pane" "$tag-cb" "( cd $GROOT && FORGE_WATCH_TRIGGER=0 $BRIDGE callback --slug $slug --stage $stage --status BLOCKED --worker $worker --message p0-$tag --quiet )"
    fi
    [ "$(rc_of "$tag-cb")" = 0 ]
}
guard_done(){
    local slug="$1" stage="$2" worker="$3" pane="$4" tag="$5"
    tmux send-keys -t "$GS:0.$pane" C-c 2>/dev/null || return 1
    sleep 0.2
    run_in_pane "$GS:0.$pane" "$tag-done" "( cd $GROOT && FORGE_WATCH_TRIGGER=0 $BRIDGE callback --slug $slug --stage $stage --status DONE --worker $worker --message p0-done --quiet )"
    [ "$(rc_of "$tag-done")" = 0 ]
}
guard_capture_has(){
    local pane="$1" needle="$2" captured
    captured="$(tmux capture-pane -p -S - -t "$GS:0.$pane" 2>/dev/null | tr -d '\n')" || return 1
    case "$captured" in *"$needle"*) return 0 ;; *) return 1 ;; esac
}
guard_assert_clean(){
    python3 - "$GROOT" <<'PY'
import pathlib, sys, yaml
root = pathlib.Path(sys.argv[1]); residue = []
for path in (root / '.dev/proposals').glob('*/forge-log.yml'):
    try: entries = (yaml.safe_load(path.read_text()) or {}).get('entries') or []
    except Exception as exc: residue.append('%s parse=%s' % (path, exc)); continue
    for entry in entries:
        if entry.get('response') is None: residue.append('%s pending=%s/%s' % (path, entry.get('stage'), entry.get('to')))
for path in (root / '.dev/forge-tmp/callbacks').glob('*.callback'):
    try: status = str((yaml.safe_load(path.read_text()) or {}).get('status') or '')
    except Exception as exc: residue.append('%s parse=%s' % (path, exc)); continue
    if status in ('BLOCKED', 'PARKED'): residue.append('%s status=%s' % (path, status))
if residue:
    print('GUARD FIXTURE RESIDUE: ' + ' | '.join(residue)); sys.exit(1)
PY
}
guard_require_clean(){
    local id="$1"
    if guard_assert_clean; then ok "$id"; else bad "$id"; return 1; fi
}

guard_block b12-block coding codex-a 2 b12-block || bad "T-GUARD-B12 setup"
run_in_pane "$GS:0.0" b12-refuse "( cd $GROOT && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS $BRIDGE dispatch --slug b12-next --stage adhoc --worker codex-b )"
if [ "$(rc_of b12-refuse)" != 0 ] && out_of b12-refuse | grep -q 'HOOK BLOCKED: dispatch refused' \
   && [ ! -e "$GROOT/.dev/proposals/b12-next/forge-log.yml" ] \
   && ! guard_capture_has 3 'codex-b-adhoc-b12-next.txt' \
   && grep -q 'reason=unresolved-blocked-item' "$GROOT/.dev/forge-tmp/orchestrator-events.log"; then
    ok "T-GUARD-B12-CROSS-SLUG"
else bad "T-GUARD-B12-CROSS-SLUG"; fi
run_in_pane "$GS:0.0" b12-park "( cd $GROOT && FORGE_WATCH_TRIGGER=0 FORGE_INFRA_LOCK_DIR=$GLOCKS $BRIDGE park --slug b12-block --stage coding --reason p0-b12 )"
run_in_pane "$GS:0.0" b12-after-park "( cd $GROOT && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS $BRIDGE dispatch --slug b12-next --stage adhoc --worker codex-b )"
if [ "$(rc_of b12-park)" = 0 ] && [ "$(rc_of b12-after-park)" = 0 ] \
   && grep -q 'parked_at:' "$GROOT/.dev/proposals/b12-block/forge-log.yml" \
   && grep -q 'response: null' "$GROOT/.dev/proposals/b12-next/forge-log.yml" \
   && guard_capture_has 3 'codex-b-adhoc-b12-next.txt'; then
    ok "T-GUARD-B12-AFTER-PARK"
else bad "T-GUARD-B12-AFTER-PARK"; fi
guard_done b12-next adhoc codex-b 3 b12-next-clean || bad "B12 close replacement"
run_in_pane "$GS:0.0" b12-resolve "( cd $GROOT && FORGE_WATCH_TRIGGER=0 $BRIDGE park --resolve --slug b12-block --stage coding --note p0-clean )"
guard_require_clean "T-GUARD-FIXTURE-HYGIENE-B12-A" || exit 1

guard_block b12-ask coding codex-a 2 b12-ask ask || bad "B12 ask setup"
run_in_pane "$GS:0.0" b12-ask-pass "( cd $GROOT && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS $BRIDGE dispatch --slug b12-ask-next --stage adhoc --worker codex-b )"
if [ "$(rc_of b12-ask-pass)" = 0 ] && guard_capture_has 3 'codex-b-adhoc-b12-ask-next.txt'; then
    ok "T-GUARD-B12-ASK-CONTROL"
else bad "T-GUARD-B12-ASK-CONTROL"; fi
guard_done b12-ask-next adhoc codex-b 3 b12-ask-next-clean || bad "B12 close ask dispatch"
guard_done b12-ask coding codex-a 2 b12-ask-clean || bad "B12 close ask"
guard_require_clean "T-GUARD-FIXTURE-HYGIENE-B12-ASK" || exit 1

guard_log b12-flight coding codex-a b12-flight || bad "B12 in-flight setup"
run_in_pane "$GS:0.0" b12-flight-pass "( cd $GROOT && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS $BRIDGE dispatch --slug b12-flight-next --stage adhoc --worker codex-b )"
if [ "$(rc_of b12-flight-pass)" = 0 ] && guard_capture_has 3 'codex-b-adhoc-b12-flight-next.txt'; then
    ok "T-GUARD-B12-INFLIGHT-CONTROL"
else bad "T-GUARD-B12-INFLIGHT-CONTROL"; fi
guard_done b12-flight-next adhoc codex-b 3 b12-flight-next-clean || bad "B12 close in-flight dispatch"
guard_done b12-flight coding codex-a 2 b12-flight-clean || bad "B12 close in-flight"
guard_require_clean "T-GUARD-FIXTURE-HYGIENE-B12-FLIGHT" || exit 1

guard_block b12-parked coding codex-a 2 b12-parked || bad "B12 parked setup"
run_in_pane "$GS:0.0" b12-parked-do "( cd $GROOT && FORGE_WATCH_TRIGGER=0 FORGE_INFRA_LOCK_DIR=$GLOCKS $BRIDGE park --slug b12-parked --stage coding --reason p0-parked )"
run_in_pane "$GS:0.0" b12-parked-advance "( cd $GROOT && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS $BRIDGE dispatch --slug b12-parked --stage adhoc --worker codex-b )"
if [ "$(rc_of b12-parked-do)" = 0 ] && [ "$(rc_of b12-parked-advance)" != 0 ] \
   && out_of b12-parked-advance | grep -q 'open pending' && ! guard_capture_has 3 'codex-b-adhoc-b12-parked.txt'; then
    ok "T-GUARD-B12-PARKED-ADVANCE"
else bad "T-GUARD-B12-PARKED-ADVANCE"; fi
run_in_pane "$GS:0.0" b12-parked-clean "( cd $GROOT && FORGE_WATCH_TRIGGER=0 $BRIDGE park --resolve --slug b12-parked --stage coding --note p0-clean )"
guard_require_clean "T-GUARD-FIXTURE-HYGIENE-B12-PARKED" || exit 1

guard_block b13-hold coding codex-a 2 b13-hold || bad "B13 setup holder"
guard_log b13-send adhoc codex-b b13-send || bad "B13 setup logged send"
run_in_pane "$GS:0.0" b13-normal "( cd $GROOT && FORGE_WATCH_TRIGGER=0 $BRIDGE send codex-b B13_NORMAL )"
run_in_pane "$GS:0.0" b13-own "( cd $GROOT && FORGE_WATCH_TRIGGER=0 $BRIDGE send --force codex-a B13_OWN )"
run_in_pane "$GS:0.0" b13-other "( cd $GROOT && FORGE_WATCH_TRIGGER=0 $BRIDGE send --force codex-b B13_OTHER )"
if [ "$(rc_of b13-normal)" != 0 ] && out_of b13-normal | grep -q 'HOOK BLOCKED: send refused' \
   && ! guard_capture_has 3 B13_NORMAL \
   && [ "$(rc_of b13-own)" = 0 ] && guard_capture_has 2 B13_OWN \
   && [ "$(rc_of b13-other)" != 0 ] && out_of b13-other | grep -q 'HOOK BLOCKED: send refused' \
   && ! guard_capture_has 3 B13_OTHER; then
    ok "T-GUARD-B13-FORCE-MATRIX"
else bad "T-GUARD-B13-FORCE-MATRIX"; fi
run_in_pane "$GS:0.0" b13-bypass "( cd $GROOT && FORGE_WATCH_TRIGGER=0 $BRIDGE send --allow-blocked p0-b13 codex-b B13_BYPASS )"
run_in_pane "$GS:0.0" b13-again "( cd $GROOT && FORGE_WATCH_TRIGGER=0 $BRIDGE send codex-b B13_AGAIN )"
if [ "$(rc_of b13-bypass)" = 0 ] && guard_capture_has 3 B13_BYPASS \
   && grep -Eq 'GUARD_BLOCK: pipeline=multi stage=\? boundary=send reason=allow-blocked-bypass n=1 .*bypassed=b13-hold.*allow_reason=p0-b13' "$GROOT/.dev/forge-tmp/orchestrator-events.log" \
   && [ "$(rc_of b13-again)" != 0 ] && out_of b13-again | grep -q 'HOOK BLOCKED: send refused' \
   && ! guard_capture_has 3 B13_AGAIN; then
    ok "T-GUARD-B13-BYPASS-ONE-SHOT"
else bad "T-GUARD-B13-BYPASS-ONE-SHOT"; fi
guard_done b13-hold coding codex-a 2 b13-hold-clean || bad "B13 close holder"
guard_done b13-send adhoc codex-b 3 b13-send-clean || bad "B13 close logged send"
guard_require_clean "T-GUARD-FIXTURE-HYGIENE-B13" || exit 1

guard_block b15-ok coding codex-a 2 b15-ok || bad "B15 success setup"
B15_OK_CB="$GROOT/.dev/forge-tmp/callbacks/b15-ok-coding.$GS.callback"
B15_OK_ID="$(sed -n 's/^callback_id: //p' "$B15_OK_CB")"
run_in_pane "$GS:0.0" b15-ok-dispatch "( cd $GROOT && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS $BRIDGE dispatch --slug b15-ok --stage adhoc --worker codex-b --supersede )"
if [ "$(rc_of b15-ok-dispatch)" = 0 ] && [ ! -f "$B15_OK_CB" ] \
   && grep -q 'FORGE_SUPERSEDED' "$GROOT/.dev/proposals/b15-ok/forge-log.yml" \
   && grep -Eq "SUPERSEDE_AUDIT: pipeline=b15-ok stage=coding prior_callback_id=$B15_OK_ID prior_status=BLOCKED .*actor=$GS" "$GROOT/.dev/forge-tmp/orchestrator-events.log" \
   && guard_capture_has 3 'codex-b-adhoc-b15-ok.txt'; then
    ok "T-GUARD-B15-SUPERSEDE-SUCCESS"
else bad "T-GUARD-B15-SUPERSEDE-SUCCESS"; fi
guard_done b15-ok adhoc codex-b 3 b15-ok-clean || bad "B15 close success replacement"
guard_require_clean "T-GUARD-FIXTURE-HYGIENE-B15-SUCCESS" || exit 1

mkdir -p "$GROOT/.dev/proposals/b15-fail"
cat > "$GROOT/.dev/proposals/b15-fail/forge-log.yml" <<EOF
pipeline: b15-fail
entries:
  - timestamp: 2026-07-13T00:00:00Z
    stage: coding
    to: codex-a
    session: $GS
    incarnation: $GINC
    response: null
EOF
B15_FAIL_CB="$GROOT/.dev/forge-tmp/callbacks/b15-fail-coding.$GS.callback"
cat > "$B15_FAIL_CB" <<EOF
slug: b15-fail
stage: coding
status: BLOCKED
worker: codex-a
session: $GS
origin:
callback_id: b15-fail-callback
timestamp: 2026-07-13T00:00:01Z
message: deterministic close failure
EOF
before_audit=$(grep -c 'SUPERSEDE_AUDIT.*b15-fail' "$GROOT/.dev/forge-tmp/orchestrator-events.log" 2>/dev/null || true)
before_audit=${before_audit:-0}
run_in_pane "$GS:0.0" b15-fail-dispatch "( cd $GROOT && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS $BRIDGE dispatch --slug b15-fail --stage adhoc --worker codex-b --supersede )"
after_audit=$(grep -c 'SUPERSEDE_AUDIT.*b15-fail' "$GROOT/.dev/forge-tmp/orchestrator-events.log" 2>/dev/null || true)
after_audit=${after_audit:-0}
if [ "$(rc_of b15-fail-dispatch)" = 0 ] && out_of b15-fail-dispatch | grep -q 'partial close failure' \
   && guard_capture_has 3 'codex-b-adhoc-b15-fail.txt' && [ -f "$B15_FAIL_CB" ] \
   && [ "$(grep -c 'response: null' "$GROOT/.dev/proposals/b15-fail/forge-log.yml")" -ge 2 ] \
   && [ "$before_audit" = "$after_audit" ] \
   && [ -z "$(find "$GROOT/.dev/forge-tmp/callbacks/archive" -type f -name 'b15-fail-*' -print -quit 2>/dev/null)" ]; then
    ok "T-GUARD-B15-SUPERSEDE-CLOSE-FAILURE"
else bad "T-GUARD-B15-SUPERSEDE-CLOSE-FAILURE"; fi
guard_done b15-fail adhoc codex-b 3 b15-fail-replacement-clean || bad "B15 close delivered replacement"
sed -i '' 's/^  - timestamp: 2026-07-13T00:00:00Z$/  - timestamp: "2026-07-13T00:00:00Z"/' "$GROOT/.dev/proposals/b15-fail/forge-log.yml"
run_in_pane "$GS:0.0" b15-fail-repair "( cd $GROOT && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS $BRIDGE dispatch --slug b15-fail --stage adhoc --worker codex-b --supersede )"
[ "$(rc_of b15-fail-repair)" = 0 ] || bad "B15 repair supersede"
guard_done b15-fail adhoc codex-b 3 b15-fail-repair-clean || bad "B15 close repair replacement"
guard_require_clean "T-GUARD-FIXTURE-HYGIENE-B15-FAILURE" || exit 1

guard_block b17-hold coding codex-a 2 b17-hold || bad "B17 setup"
run_in_pane "$GS:0.0" b17-dispatch "( cd $GROOT && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS $BRIDGE dispatch --slug b17-next --stage adhoc --worker codex-b --allow-blocked p0-b17 )"
if [ "$(rc_of b17-dispatch)" = 0 ] && guard_capture_has 3 'codex-b-adhoc-b17-next.txt' \
   && grep -Eq 'GUARD_BLOCK: pipeline=multi stage=\? boundary=dispatch reason=allow-blocked-bypass n=1 .*bypassed=b17-hold.*allow_reason=p0-b17' "$GROOT/.dev/forge-tmp/orchestrator-events.log" \
   && ! out_of b17-dispatch | grep -q 'HOOK BLOCKED: send refused'; then
    ok "T-GUARD-B17-INTERNAL-DELIVERY"
else bad "T-GUARD-B17-INTERNAL-DELIVERY"; fi
guard_done b17-next adhoc codex-b 3 b17-next-clean || bad "B17 close dispatch"
guard_done b17-hold coding codex-a 2 b17-hold-clean || bad "B17 close holder"
guard_require_clean "T-GUARD-FIXTURE-HYGIENE-B17" || exit 1

guard_block z-b18-a coding codex-a 2 b18-a || bad "B18 setup A"
guard_block a-b18-b coding codex-b 3 b18-b || bad "B18 setup B"
run_in_pane "$GS:0.0" b18-force-a "( cd $GROOT && FORGE_WATCH_TRIGGER=0 $BRIDGE send --force codex-a B18_FORCE_A )"
run_in_pane "$GS:0.0" b18-force-b "( cd $GROOT && FORGE_WATCH_TRIGGER=0 $BRIDGE send --force codex-b B18_FORCE_B )"
b18_a_out="$(out_of b18-force-a)"; b18_b_out="$(out_of b18-force-b)"
if [ "$(rc_of b18-force-a)" != 0 ] && [ "$(rc_of b18-force-b)" != 0 ] \
   && printf '%s' "$b18_a_out" | grep -q 'a-b18-b/coding' && ! printf '%s' "$b18_a_out" | grep -q 'z-b18-a/coding' \
   && printf '%s' "$b18_b_out" | grep -q 'z-b18-a/coding' && ! printf '%s' "$b18_b_out" | grep -q 'a-b18-b/coding' \
   && ! guard_capture_has 2 B18_FORCE_A && ! guard_capture_has 3 B18_FORCE_B; then
    ok "T-GUARD-B18-FORCE-FILTER"
else bad "T-GUARD-B18-FORCE-FILTER"; fi
guard_done a-b18-b coding codex-b 3 b18-b-clean || bad "B18 close B"
run_in_pane "$GS:0.0" b18-own "( cd $GROOT && FORGE_WATCH_TRIGGER=0 $BRIDGE send --force codex-a B18_AFTER_B )"
if [ "$(rc_of b18-own)" = 0 ] && guard_capture_has 2 B18_AFTER_B; then
    ok "T-GUARD-B18-OWN-CONTINUE"
else bad "T-GUARD-B18-OWN-CONTINUE"; fi
guard_done z-b18-a coding codex-a 2 b18-a-clean || bad "B18 close A"
guard_require_clean "T-GUARD-FIXTURE-HYGIENE-B18" || exit 1

guard_require_clean "T-GUARD-FIXTURE-HYGIENE-FINAL" || exit 1
tmux kill-session -t "$GS" 2>/dev/null

# ---- Name-reuse / incarnation (R10, CG-6) ----
echo "== name-reuse / incarnation (R10) =="

# Hermetic stage-prompt template (dispatch resolves FORGE_PROMPTS_DIR/<stage>.txt).
PDIR="$WORK/prompts"; mkdir -p "$PDIR"
printf 'adhoc test prompt for {{slug}}\n' > "$PDIR/adhoc.txt"

# T-REUSE-BLOCK: a dead predecessor's orphan (same name, DIFFERENT incarnation) must
# not block a reborn session's dispatch.
drb="$rootA/.dev/proposals/rb1"; mkdir -p "$drb"
cat > "$drb/forge-log.yml" <<EOF
pipeline: rb1
entries:
  - timestamp: "2026-07-11T00:01:00Z"
    stage: adhoc
    to: codex-a
    session: $S2
    incarnation: 1111111
    prompt: old
    response: null
    files: []
EOF
run_in_pane "$S2:0.0" reuseblock "FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$PDIR $BRIDGE dispatch --slug rb1 --stage adhoc --worker codex-a"
if [ "$(rc_of reuseblock)" = "0" ] && ! out_of reuseblock | grep -q "HOOK BLOCKED"; then
    ok "T-REUSE-BLOCK reborn session not blocked by dead incarnation's orphan"
else
    bad "T-REUSE-BLOCK reborn session not blocked (rc=$(rc_of reuseblock) out=$(out_of reuseblock | head -3 | tr '\n' ' '))"
fi

# T-REUSE-LEGACY: a legacy pending (no incarnation field) still blocks by name.
drl="$rootA/.dev/proposals/rl1"; mkdir -p "$drl"
cat > "$drl/forge-log.yml" <<EOF
pipeline: rl1
entries:
  - timestamp: "2026-07-11T00:02:00Z"
    stage: adhoc
    to: codex-a
    session: $S2
    prompt: legacy
    response: null
    files: []
EOF
run_in_pane "$S2:0.0" reuselegacy "FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$PDIR $BRIDGE dispatch --slug rl1 --stage adhoc --worker codex-a"
if [ "$(rc_of reuselegacy)" != "0" ] && out_of reuselegacy | grep -q "open pending"; then
    ok "T-REUSE-LEGACY legacy no-incarnation pending still blocks by name"
else
    bad "T-REUSE-LEGACY legacy pending still blocks (rc=$(rc_of reuselegacy))"
fi

# T-REUSE-SUPERSEDE: --supersede closes the caller-session orphan regardless of
# incarnation, but NEVER a different-named session's pending.
drs="$rootA/.dev/proposals/rs1"; mkdir -p "$drs"
cat > "$drs/forge-log.yml" <<EOF
pipeline: rs1
entries:
  - timestamp: "2026-07-11T00:03:00Z"
    stage: adhoc
    to: codex-a
    session: $S2
    incarnation: 2222222
    prompt: orphan
    response: null
    files: []
  - timestamp: "2026-07-11T00:03:01Z"
    stage: adhoc
    to: codex-a
    session: $S1
    prompt: other-session
    response: null
    files: []
EOF
run_in_pane "$S2:0.0" reusesup "FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$PDIR $BRIDGE dispatch --slug rs1 --stage adhoc --worker codex-a --supersede"
supok=$(python3 - "$drs/forge-log.yml" "$S1" "$S2" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
s1, s2 = sys.argv[2], sys.argv[3]
orphan = [e for e in d['entries'] if e.get('session') == s2 and str(e.get('incarnation','')) == '2222222']
other  = [e for e in d['entries'] if e.get('session') == s1]
print('OK' if orphan and orphan[0]['response'] and 'FORGE_SUPERSEDED' in str(orphan[0]['response'])
      and other and other[0]['response'] is None else 'NO')
PY
)
if [ "$(rc_of reusesup)" = "0" ] && [ "$supok" = "OK" ]; then
    ok "T-REUSE-SUPERSEDE closes own-name orphan cross-incarnation, other session untouched"
else
    bad "T-REUSE-SUPERSEDE (rc=$(rc_of reusesup) supok=$supok)"
fi

# T-REUSE-ROUNDTRIP: to:claude local entries carry incarnation; YAML + renderer tolerate it.
run_in_pane "$S2:0.0" reusert "FORGE_WATCH_TRIGGER=0 $BRIDGE log --slug rt1 --stage adhoc --from claude --to claude --prompt p"
rtinc=$(grep -A2 "to: claude" "$rootA/.dev/proposals/rt1/forge-log.yml" 2>/dev/null | sed -n 's/^ *incarnation: //p' | head -1)
if [ "$(rc_of reusert)" = "0" ] && [ -n "$rtinc" ] \
   && python3 -c "import yaml,sys; yaml.safe_load(open('$rootA/.dev/proposals/rt1/forge-log.yml')); yaml.safe_load(open('$rootA/.dev/forge-log.yml'))" 2>/dev/null; then
    ok "T-REUSE-ROUNDTRIP to:claude entry carries incarnation; logs round-trip as YAML"
else
    bad "T-REUSE-ROUNDTRIP (rc=$(rc_of reusert) inc='$rtinc')"
fi
run_in_pane "$S2:0.0" reusestatus "FORGE_WATCH_TRIGGER=0 $BRIDGE status"
[ "$(rc_of reusestatus)" = "0" ] \
    && ok "T-REUSE-ROUNDTRIP status renderer tolerates the incarnation field" \
    || bad "T-REUSE-ROUNDTRIP status renderer (rc=$(rc_of reusestatus))"

echo
printf 'forge-bridge: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
