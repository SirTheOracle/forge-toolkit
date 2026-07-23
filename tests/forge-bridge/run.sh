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
trap 'tmux kill-session -t "$S1" 2>/dev/null; tmux kill-session -t "$S2" 2>/dev/null; tmux kill-session -t "$S3" 2>/dev/null; tmux kill-session -t "$S4" 2>/dev/null; tmux kill-session -t "$GS" 2>/dev/null; tmux kill-session -t "${HS:-none}" 2>/dev/null; rm -rf "$WORK"' EXIT

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

# pending_entry <root> <slug> <stage> <to> <ts> [session] [incarnation]
pending_entry(){
    local d="$1/.dev/proposals/$2"; mkdir -p "$d"
    {
        echo "pipeline: $2"
        echo "entries:"
        echo "  - timestamp: \"$5\""
        echo "    stage: $3"
        echo "    to: $4"
        [ -n "${6:-}" ] && echo "    session: $6"
        [ -n "${7:-}" ] && echo "    incarnation: $7"
        echo "    response: null"
    } > "$d/forge-log.yml"
}

# T-CB-HEADLESS-DONE: publication requires both target session and incarnation.
pending_entry "$rootD" hdl coding codex-a "2026-07-11T00:00:00Z"
hdl_before=$(shasum -a 256 "$rootD/.dev/proposals/hdl/forge-log.yml" | awk '{print $1}')
cbh="$(cd "$rootD" && env -u TMUX -u TMUX_PANE -u TMUX_SESSION FORGE_IDENTITY_ENFORCE=1 "$BRIDGE" callback --slug hdl --stage coding --status DONE --worker codex-a --quiet 2>&1)"; cbhrc=$?
hdl_after=$(shasum -a 256 "$rootD/.dev/proposals/hdl/forge-log.yml" | awk '{print $1}')
if [ "$cbhrc" -ne 0 ] && [ "$hdl_before" = "$hdl_after" ] \
   && [ -z "$(find "$rootD/.dev/forge-tmp/callbacks" -type f -name 'hdl-coding*.callback' -print -quit 2>/dev/null)" ]; then
    ok "T-CB-HEADLESS-DONE missing incarnation fails before callback/log mutation"
else
    bad "T-CB-HEADLESS-DONE missing incarnation fail-closed (rc=$cbhrc out=$cbh)"
fi

# T-CLOSE-ISOLATION: two same-slug/stage/to pendings for S1 and S2; close from S1 pane
d="$rootA/.dev/proposals/xiso"; mkdir -p "$d"
S1_CALLBACK_INC="$(tmux display-message -p -t "$S1:0.0" '#{session_created}')"
S2_CALLBACK_INC="$(tmux display-message -p -t "$S2:0.0" '#{session_created}')"
cat > "$d/forge-log.yml" <<EOF
pipeline: xiso
entries:
  - timestamp: "2026-07-11T00:00:01Z"
    stage: coding
    to: codex-a
    session: $S1
    incarnation: $S1_CALLBACK_INC
    response: null
  - timestamp: "2026-07-11T00:00:02Z"
    stage: coding
    to: codex-a
    session: $S2
    incarnation: $S2_CALLBACK_INC
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
pending_entry "$rootA" ntfy coding codex-a "2026-07-11T00:00:05Z" "$S1" "$S1_CALLBACK_INC"
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
B15_OK_CB="$GROOT/.dev/forge-tmp/callbacks/b15-ok-coding.$GS.$GINC.callback"
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

# stale-alert-lifecycle D1: the --supersede (FORGE_SUPERSEDED) close archives a
# resolved operator ask for the superseded (slug, stage) — the Round-3 supersede gap.
mkdir -p "$GROOT/.dev/attention/archive"
guard_block d1sup coding codex-a 2 d1sup || bad "D1-SUP setup"
cat > "$GROOT/.dev/attention/ask-d1sup.json" <<JSON
{"schema":"cc-attention/1","event":"ask","variant":"ask","session":"forge-1","root":"$GROOT","emitted_at":"2026-07-19T13:24:52Z","ask_id":"ask-d1sup","mode":"stage","slug":"d1sup","stage":"coding","worker":"codex-a","question_snippet":"drop or keep?"}
JSON
run_in_pane "$GS:0.0" d1sup-dispatch "( cd $GROOT && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS $BRIDGE dispatch --slug d1sup --stage adhoc --worker codex-b --supersede )"
if [ "$(rc_of d1sup-dispatch)" = 0 ] \
   && grep -q 'FORGE_SUPERSEDED' "$GROOT/.dev/proposals/d1sup/forge-log.yml" \
   && [ ! -f "$GROOT/.dev/attention/ask-d1sup.json" ] \
   && [ -f "$GROOT/.dev/attention/archive/ask-d1sup.json" ]; then
    ok "T-D1-SUPERSEDE-ARCHIVE ask archived on the FORGE_SUPERSEDED close"
else bad "T-D1-SUPERSEDE-ARCHIVE (ask not archived on supersede)"; fi
guard_done d1sup adhoc codex-b 3 d1sup-clean || bad "D1-SUP close replacement"
guard_require_clean "T-GUARD-HYGIENE-D1SUP" || exit 1

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
B15_FAIL_CB="$GROOT/.dev/forge-tmp/callbacks/b15-fail-coding.$GS.$GINC.callback"
cat > "$B15_FAIL_CB" <<EOF
slug: b15-fail
stage: coding
status: BLOCKED
worker: codex-a
session: $GS
incarnation: $GINC
origin:
callback_id: b15-fail-callback
timestamp: 2026-07-13T00:00:01Z
selected_pending_timestamp: "2026-07-13T00:00:00Z"
message: deterministic close failure
EOF
before_audit=$(grep -c 'SUPERSEDE_AUDIT.*b15-fail' "$GROOT/.dev/forge-tmp/orchestrator-events.log" 2>/dev/null || true)
before_audit=${before_audit:-0}
before_log=$(shasum -a 256 "$GROOT/.dev/proposals/b15-fail/forge-log.yml" | awk '{print $1}')
before_cb=$(shasum -a 256 "$B15_FAIL_CB" | awk '{print $1}')
run_in_pane "$GS:0.0" b15-fail-dispatch "( cd $GROOT && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS $BRIDGE dispatch --slug b15-fail --stage adhoc --worker codex-b --supersede )"
after_audit=$(grep -c 'SUPERSEDE_AUDIT.*b15-fail' "$GROOT/.dev/forge-tmp/orchestrator-events.log" 2>/dev/null || true)
after_audit=${after_audit:-0}
after_log=$(shasum -a 256 "$GROOT/.dev/proposals/b15-fail/forge-log.yml" | awk '{print $1}')
after_cb=$(shasum -a 256 "$B15_FAIL_CB" | awk '{print $1}')
if [ "$(rc_of b15-fail-dispatch)" != 0 ] && [ "$before_log" = "$after_log" ] \
   && [ "$before_cb" = "$after_cb" ] && [ "$before_audit" = "$after_audit" ] \
   && ! guard_capture_has 3 'codex-b-adhoc-b15-fail.txt' \
   && [ -z "$(find "$GROOT/.dev/forge-tmp/callbacks/archive" -type f -name 'b15-fail-*' -print -quit 2>/dev/null)" ]; then
    ok "T-GUARD-B15-SUPERSEDE-CLOSE-FAILURE"
else bad "T-GUARD-B15-SUPERSEDE-CLOSE-FAILURE"; fi
sed -i '' 's/^  - timestamp: 2026-07-13T00:00:00Z$/  - timestamp: "2026-07-13T00:00:00Z"/' "$GROOT/.dev/proposals/b15-fail/forge-log.yml"
run_in_pane "$GS:0.0" b15-fail-repair "( cd $GROOT && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS $BRIDGE dispatch --slug b15-fail --stage adhoc --worker codex-b --supersede )"
[ "$(rc_of b15-fail-repair)" = 0 ] && [ ! -f "$B15_FAIL_CB" ] || bad "B15 repair supersede"
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

# ---- stale-alert-lifecycle D1/D2 (headless log-response close) ----
echo "== stale-alert D1 ask-archival / D2 orphan-audit =="
mkS(){ local d="$WORK/$1"; mkdir -p "$d/.claude" "$d/.dev/proposals/$2" "$d/.dev/forge-tmp/callbacks" "$d/.dev/attention/archive"; printf 'name: %s\nforge:\n  expected_root: %s\n' "$1" "$d" > "$d/.claude/forge-project.yml"; printf '%s' "$d"; }
seed_ask(){ printf '{"schema":"cc-attention/1","event":"ask","variant":"ask","session":"forge-1","root":"%s","emitted_at":"2026-07-20T00:00:00Z","ask_id":"%s","mode":"stage","slug":"%s","stage":"%s","worker":"codex-a","question_snippet":"q"}\n' "$1" "$2" "$3" "$4" > "$1/.dev/attention/$2.json"; }

# D1: a terminal DONE close archives a resolved ask (no other open pending).
SD1="$(mkS sd1 s1)"
printf 'pipeline: s1\nentries:\n  - timestamp: "2026-07-20T00:00:00Z"\n    stage: coding\n    to: codex-a\n    response: null\n' > "$SD1/.dev/proposals/s1/forge-log.yml"
seed_ask "$SD1" ask-d1 s1 coding
( cd "$SD1" && FORGE_WATCH_TRIGGER=0 "$BRIDGE" log-response --slug s1 --response "FORGE_DONE: coding" --to codex-a --stage coding ) >/dev/null 2>&1
if [ ! -f "$SD1/.dev/attention/ask-d1.json" ] && [ -f "$SD1/.dev/attention/archive/ask-d1.json" ]; then
    ok "T-D1-ARCHIVE-ON-CLOSE resolved ask archived on terminal close"
else bad "T-D1-ARCHIVE-ON-CLOSE"; fi

# D1 gate-negative: a second open pending on the stage → ask NOT archived.
SD1N="$(mkS sd1n s1)"
printf 'pipeline: s1\nentries:\n  - timestamp: "2026-07-20T00:00:00Z"\n    stage: coding\n    to: codex-a\n    response: null\n  - timestamp: "2026-07-20T00:05:00Z"\n    stage: coding\n    to: codex-a\n    response: null\n' > "$SD1N/.dev/proposals/s1/forge-log.yml"
seed_ask "$SD1N" ask-d1n s1 coding
( cd "$SD1N" && FORGE_WATCH_TRIGGER=0 "$BRIDGE" log-response --slug s1 --response "FORGE_DONE: coding" --to codex-a --stage coding ) >/dev/null 2>&1
if [ -f "$SD1N/.dev/attention/ask-d1n.json" ] && [ ! -f "$SD1N/.dev/attention/archive/ask-d1n.json" ]; then
    ok "T-D1-GATE-NEGATIVE ask NOT archived while an open pending remains"
else bad "T-D1-GATE-NEGATIVE (ask archived despite open pending)"; fi

# D2: closing a twin with a DIFFERENT `to` emits WARN_ORPHAN_PENDING and leaves the
# cross-identity orphan OPEN (never auto-closed, P1-WC).
SD2="$(mkS sd2 s2)"
printf 'pipeline: s2\nentries:\n  - timestamp: "2026-07-19T16:17:29Z"\n    stage: fix-code\n    to: claude\n    response: null\n  - timestamp: "2026-07-19T16:19:50Z"\n    stage: fix-code\n    to: claude-sonnet\n    response: null\n' > "$SD2/.dev/proposals/s2/forge-log.yml"
: > "$SD2/.dev/forge-tmp/orchestrator-events.log"
( cd "$SD2" && FORGE_WATCH_TRIGGER=0 "$BRIDGE" log-response --slug s2 --response "FORGE_DONE: fix-code" --to claude-sonnet --stage fix-code ) >/dev/null 2>&1
n_orphan_open=$(grep -c 'response: null' "$SD2/.dev/proposals/s2/forge-log.yml")
if grep -q 'WARN_ORPHAN_PENDING: pipeline=s2 stage=fix-code .*orphan_ts=2026-07-19T16:17:29Z' "$SD2/.dev/forge-tmp/orchestrator-events.log" \
   && [ "$n_orphan_open" = 1 ]; then
    ok "T-D2-ORPHAN-AUDIT WARN_ORPHAN_PENDING emitted; cross-identity orphan left OPEN"
else bad "T-D2-ORPHAN-AUDIT (audit=$(grep -c WARN_ORPHAN_PENDING "$SD2/.dev/forge-tmp/orchestrator-events.log") open=$n_orphan_open)"; fi

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

# T-REUSE-SUPERSEDE: a reborn name cannot supersede its predecessor incarnation
# and never reaches a different-named session's pending.
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
REUSE_CB="$rootA/.dev/forge-tmp/callbacks/rs1-adhoc.$S2.2222222.callback"
cat > "$REUSE_CB" <<EOF
slug: rs1
stage: adhoc
status: BLOCKED
worker: codex-a
session: $S2
incarnation: 2222222
callback_id: reuse-predecessor
timestamp: 2026-07-11T00:03:02Z
selected_pending_timestamp: "2026-07-11T00:03:00Z"
message: predecessor
EOF
REUSE_CB_BEFORE="$(shasum -a 256 "$REUSE_CB" | awk '{print $1}')"
run_in_pane "$S2:0.0" reusesup "FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$PDIR $BRIDGE dispatch --slug rs1 --stage adhoc --worker codex-a --supersede"
reuse_inc="$(tmux display-message -p -t "$S2:0.0" '#{session_created}')"
supok=$(python3 - "$drs/forge-log.yml" "$S1" "$S2" "$reuse_inc" <<'PY'
import sys,yaml
d=yaml.safe_load(open(sys.argv[1])); s1,s2,cur=sys.argv[2:5]
orphan=[e for e in d['entries'] if e.get('session')==s2 and str(e.get('incarnation',''))=='2222222']
other=[e for e in d['entries'] if e.get('session')==s1]
current=[e for e in d['entries'] if e.get('session')==s2 and str(e.get('incarnation',''))==cur]
print('OK' if orphan and orphan[0]['response'] is None and other and other[0]['response'] is None and len(current)==1 and current[0]['response'] is None else 'NO')
PY
)
if [ "$(rc_of reusesup)" = "0" ] && [ "$supok" = "OK" ] \
   && [ "$REUSE_CB_BEFORE" = "$(shasum -a 256 "$REUSE_CB" | awk '{print $1}')" ] \
   && [ -z "$(find "$rootA/.dev/forge-tmp/callbacks/archive" -name 'rs1-*' -print -quit 2>/dev/null)" ]; then
    ok "T-REUSE-SUPERSEDE predecessor and other session remain immutable"
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

# P1-E read-only audit matrix (fixture bytes must not change).
ED="$rootA/.dev/forge-tmp/callbacks"; mkdir -p "$ED"; ES="p1e-audit"; EST="coding"
S2INC="$(tmux display-message -p -t "$S2:0.0" '#{session_created}')"
write_ecb(){ local f="$1" sess="$2" inc="$3" extra="$4"; cat > "$ED/$f" <<EOF
slug: $ES
stage: $EST
status: BLOCKED
worker: codex-a
${sess:+session: $sess}
${inc:+incarnation: $inc}
origin:
callback_id: $f-id
timestamp: 2026-07-14T00:00:00Z
$extra
message: audit
EOF
}
write_ecb "$ES-$EST.$S2.$S2INC.callback" "$S2" "$S2INC" ""
dev_fingerprint(){ python3 - "$rootA/.dev" <<'PY'
import hashlib,os,sys
root=sys.argv[1]; h=hashlib.sha256()
for base,dirs,files in os.walk(root):
    dirs.sort(); files.sort()
    for name in dirs:
        h.update(b'D\0'+os.path.relpath(os.path.join(base,name),root).encode()+b'\0')
    for name in files:
        p=os.path.join(base,name); h.update(b'F\0'+os.path.relpath(p,root).encode()+b'\0')
        with open(p,'rb') as f:
            for chunk in iter(lambda:f.read(65536),b''): h.update(chunk)
print(h.hexdigest())
PY
}
before=$(dev_fingerprint)
run_in_pane "$S2:0.0" e-audit-exact "FORGE_WATCH_TRIGGER=0 $BRIDGE blocked-audit --root $rootA --json"
run_in_pane "$S2:0.0" e-audit-human "FORGE_WATCH_TRIGGER=0 $BRIDGE blocked-audit --root $rootA"
after=$(dev_fingerprint)
if [ "$(rc_of e-audit-exact)" = 0 ] && [ "$(rc_of e-audit-human)" = 0 ] \
  && out_of e-audit-exact | grep -q '"classification": "exact-current"' \
  && out_of e-audit-exact | grep -q '"zero_ambiguity": true' \
  && out_of e-audit-human | grep -q 'classification=exact-current.*suggest=manual identity disposition; do not use legacy mutators' \
  && out_of e-audit-human | grep -q 'MUTATES NOTHING' && [ "$before" = "$after" ]; then
  ok "T-P1E-AUDIT-BOTH-MODES-WHOLE-DEV-NOMUTATION"
else bad "T-P1E-AUDIT-BOTH-MODES-WHOLE-DEV-NOMUTATION"; fi
S1INC="$(tmux display-message -p -t "$S1:0.0" '#{session_created}')"
cat > "$ED/p1e-concurrent-coding.$S1.$S1INC.callback" <<EOF
slug: p1e-concurrent
stage: coding
status: BLOCKED
worker: codex-a
session: $S1
incarnation: $S1INC
callback_id: concurrent-other
timestamp: 2026-07-14T00:00:00Z
message: other live same-root owner
EOF
printf '%s\t%s\t$1\t%s\n%s\t%s\t$2\t%s\n' \
  "$S1" "$rootA" "$S1INC" "$S2" "$rootA" "$S2INC" > "$WORK/p1e-concurrent-live.tsv"
run_in_pane "$S2:0.0" e-audit-concurrent "FORGE_BLOCKED_AUDIT_TMUX_LIST=$WORK/p1e-concurrent-live.tsv FORGE_WATCH_TRIGGER=0 $BRIDGE blocked-audit --root $rootA --json"
out_of e-audit-concurrent | sed '/^DONE_/d' | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["summary"]["ambiguous"]==0; assert any(r.get("file","").startswith("p1e-concurrent-coding.") and r.get("classification")=="exact-current" for r in d["items"])' \
  && ok "T-P1E-AUDIT-CONCURRENT-SAME-ROOT" || bad "T-P1E-AUDIT-CONCURRENT-SAME-ROOT"
rm -f "$ED/p1e-concurrent-coding.$S1.$S1INC.callback"
write_ecb "$ES-$EST.$S2.callback" "$S2" "" ""
run_in_pane "$S2:0.0" e-audit-amb "FORGE_WATCH_TRIGGER=0 $BRIDGE blocked-audit --root $rootA --json"
out_of e-audit-amb | grep -q '"ambiguous": 2' && ok "T-P1E-AUDIT-CARDINALITY" || bad "T-P1E-AUDIT-CARDINALITY"
rm -f "$ED/$ES-$EST.$S2.callback" "$ED/$ES-$EST.$S2.$S2INC.callback"
write_ecb "$ES-$EST.$S2.111.callback" "$S2" 111 ""
write_ecb "$ES-$EST.$S2.222.callback" "$S2" 222 ""
write_ecb "$ES-$EST.dead-session.333.callback" "dead-session" 333 ""
S3INC="$(tmux display-message -p -t "$S3:0.0" '#{session_created}')"
write_ecb "$ES-$EST.$S3.$S3INC.callback" "$S3" "$S3INC" ""
printf '%s\t%s\t$2\t%s\n%s\t%s\t$3\t%s\n' "$S2" "$rootA" 222 "$S3" "$rootC" "$S3INC" > "$WORK/p1e-live.tsv"
FORGE_BLOCKED_AUDIT_TMUX_LIST="$WORK/p1e-live.tsv" run_in_pane "$S2:0.0" e-audit-rebirth "FORGE_BLOCKED_AUDIT_TMUX_LIST=$WORK/p1e-live.tsv FORGE_WATCH_TRIGGER=0 $BRIDGE blocked-audit --root $rootA --json"
out_of e-audit-rebirth | grep -q 'foreign-incarnation' && out_of e-audit-rebirth | grep -q 'dead-incarnation' \
  && ok "T-P1E-AUDIT-REBIRTH-FOREIGN-DEAD" || bad "T-P1E-AUDIT-REBIRTH-FOREIGN-DEAD"
rm -f "$ED/$ES-$EST."*.callback
write_ecb "$ES-$EST.wrong.callback" "$S2" "" ""
printf 'slug: %s\nstage: %s\nstatus: BLOCKED\nunknown_future: x\nmessage: x\n' "$ES" "$EST" > "$ED/$ES-$EST.callback"
printf 'slug: p1e-required\nstage: coding\nstatus: BLOCKED\nworker: codex-a\ntimestamp: 2026-07-14T00:00:00Z\nmessage: missing callback id\n' > "$ED/p1e-required-coding.callback"
printf 'slug: p1e-inc-only\nstage: coding\nstatus: BLOCKED\nworker: codex-a\nincarnation: 404\ncallback_id: inc-only\ntimestamp: 2026-07-14T00:00:00Z\nmessage: invalid shape\n' > "$ED/p1e-inc-only-coding.callback"
printf '[unterminated\n' > "$ED/p1e-malformed-coding.callback"
run_in_pane "$S2:0.0" e-audit-invalid "FORGE_WATCH_TRIGGER=0 $BRIDGE blocked-audit --root $rootA --json"
out_of e-audit-invalid | grep -q 'header-filename-mismatch' && out_of e-audit-invalid | grep -q 'unknown-header' \
  && out_of e-audit-invalid | grep -q 'malformed-header' \
  && out_of e-audit-invalid | grep -q 'incarnation-without-session' \
  && out_of e-audit-invalid | grep -q '"p1_wc_ready": false' \
  && ok "T-P1E-AUDIT-HEADER-NEGATIVES" || bad "T-P1E-AUDIT-HEADER-NEGATIVES"
rm -f "$ED/$ES-$EST"*.callback
rm -f "$ED/p1e-required-coding.callback" "$ED/p1e-inc-only-coding.callback" "$ED/p1e-malformed-coding.callback"

# Human guidance remains actionable only for shapes today's mutators can see.
write_ecb "$ES-$EST.$S2.callback" "$S2" "" ""
run_in_pane "$S2:0.0" e-audit-legacy-human "FORGE_WATCH_TRIGGER=0 $BRIDGE blocked-audit --root $rootA"
out_of e-audit-legacy-human | grep -q 'classification=session-only-legacy.*suggest=legacy-compatible: inspect; park / consume / supersede' \
  && ok "T-P1E-AUDIT-LEGACY-GUIDANCE" || bad "T-P1E-AUDIT-LEGACY-GUIDANCE"
rm -f "$ED/$ES-$EST.$S2.callback"
CLEANROOT="$WORK/p1e-clean-root"; mkdir -p "$CLEANROOT/.dev/forge-tmp/callbacks"
mkdir -p "$WORK/p1e-clean-home"
out=$(HOME="$WORK/p1e-clean-home" FORGE_WATCH_TRIGGER=0 "$BRIDGE" blocked-audit --root "$CLEANROOT" 2>&1); rc=$?
[ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q 'blocked-audit: clean (no residue)' \
  && ok "T-P1E-AUDIT-HUMAN-CLEAN" || bad "T-P1E-AUDIT-HUMAN-CLEAN"

# The active wait reader, not only the audit, rejects a constructed-path file
# missing one of worker/callback_id/timestamp/message before any status projection.
cat > "$ED/$ES-$EST.$S2.$S2INC.callback" <<EOF
slug: $ES
stage: $EST
status: BLOCKED
session: $S2
incarnation: $S2INC
callback_id: scan-required
timestamp: 2026-07-14T00:00:00Z
message: missing worker
EOF
run_in_pane "$S2:0.0" e-scan-required "FORGE_WATCH_TRIGGER=0 $BRIDGE wait --slug $ES --stage $EST --worker codex-a --timeout 1 --interval 0.1"
[ "$(rc_of e-scan-required)" = 1 ] && out_of e-scan-required | grep -q 'CALLBACK_HEADER_INVALID' \
  && ok "T-P1E-SCAN-REQUIRED-HEADERS" || bad "T-P1E-SCAN-REQUIRED-HEADERS"
rm -f "$ED/$ES-$EST.$S2.$S2INC.callback"

# The production wait path selects one valid exact candidate.
write_ecb "$ES-$EST.$S2.$S2INC.callback" "$S2" "$S2INC" ""
run_in_pane "$S2:0.0" e-scan-exact "FORGE_WATCH_TRIGGER=0 $BRIDGE wait --slug $ES --stage $EST --worker codex-a --timeout 3 --interval 0.1"
[ "$(rc_of e-scan-exact)" = 0 ] && out_of e-scan-exact | grep -q 'STATUS: BLOCKED' \
  && ok "T-P1E-WAIT-EXACT" || bad "T-P1E-WAIT-EXACT"

# A lock miss is a poll miss: release after one short acquisition ceiling and
# the same wait proceeds to select the exact callback.
LOCK="$rootA/.dev/forge-tmp/locks/lifecycle-${ES}--${EST}.lock"; mkdir -p "$(dirname "$LOCK")"
READY="$WORK/p1e-busy-release-ready"
python3 - "$LOCK" "$READY" <<'PY' &
import fcntl,sys,time
fd=open(sys.argv[1],'w'); fcntl.flock(fd,fcntl.LOCK_EX); open(sys.argv[2],'w').close(); time.sleep(.6)
PY
holder=$!; i=0; while [ ! -f "$READY" ] && [ "$i" -lt 40 ]; do sleep .05; i=$((i+1)); done
run_in_pane "$S2:0.0" e-wait-busy-release "FORGE_LIFECYCLE_LOCK_WAIT_S=0.1 FORGE_WATCH_TRIGGER=0 $BRIDGE wait --slug $ES --stage $EST --worker codex-a --timeout 3 --interval 0.1"
wait "$holder"; rm -f "$READY"
[ "$(rc_of e-wait-busy-release)" = 0 ] && out_of e-wait-busy-release | grep -q 'STATUS: BLOCKED' \
  && ! out_of e-wait-busy-release | grep -q 'LIFECYCLE_LOCK: busy' \
  && ok "T-P1E-WAIT-BUSY-THEN-RELEASE" || bad "T-P1E-WAIT-BUSY-THEN-RELEASE"

# If contention outlives the stage timeout, the existing timeout path still
# emits its structured block instead of returning an unstructured rc 1.
READY="$WORK/p1e-busy-timeout-ready"
python3 - "$LOCK" "$READY" <<'PY' &
import fcntl,sys,time
fd=open(sys.argv[1],'w'); fcntl.flock(fd,fcntl.LOCK_EX); open(sys.argv[2],'w').close(); time.sleep(2)
PY
holder=$!; i=0; while [ ! -f "$READY" ] && [ "$i" -lt 40 ]; do sleep .05; i=$((i+1)); done
run_in_pane "$S2:0.0" e-wait-busy-timeout "FORGE_LIFECYCLE_LOCK_WAIT_S=0.1 FORGE_WATCH_TRIGGER=0 $BRIDGE wait --slug $ES --stage $EST --worker codex-a --timeout 1 --interval 0.1"
wait "$holder"; rm -f "$READY"
[ "$(rc_of e-wait-busy-timeout)" = 0 ] && out_of e-wait-busy-timeout | grep -q 'STATUS: TIMEOUT' \
  && ! out_of e-wait-busy-timeout | grep -q 'LIFECYCLE_LOCK: busy' \
  && ok "T-P1E-WAIT-BUSY-STAGE-TIMEOUT" || bad "T-P1E-WAIT-BUSY-STAGE-TIMEOUT"

# Hold the physical lifecycle mutex, create a second plausible shape inside the
# transition, and release. wait must never observe the earlier sole-exact state;
# after release it deterministically reports ambiguity within a hard ceiling.
LOCK="$rootA/.dev/forge-tmp/locks/lifecycle-${ES}--${EST}.lock"; READY="$WORK/p1e-lock-ready"
mkdir -p "$(dirname "$LOCK")"
python3 - "$LOCK" "$READY" "$ED/$ES-$EST.$S2.$S2INC.callback" "$ED/$ES-$EST.$S2.callback" <<'PY' &
import fcntl,os,shutil,sys,time
fd=open(sys.argv[1],'w'); fcntl.flock(fd,fcntl.LOCK_EX); open(sys.argv[2],'w').close()
time.sleep(.5); shutil.copyfile(sys.argv[3],sys.argv[4])
raw=open(sys.argv[4]).read().replace('incarnation: '+os.environ.get('UNUSED','__never__')+'\n','')
# Remove the actual incarnation without knowing its value and preserve all other headers.
raw='\n'.join(x for x in raw.splitlines() if not x.startswith('incarnation:'))+'\n'
open(sys.argv[4],'w').write(raw); time.sleep(.5)
PY
holder=$!; i=0; while [ ! -f "$READY" ] && [ "$i" -lt 40 ]; do sleep .05; i=$((i+1)); done
run_in_pane "$S2:0.0" e-scan-transition "FORGE_LIFECYCLE_LOCK_WAIT_S=3 FORGE_WATCH_TRIGGER=0 $BRIDGE wait --slug $ES --stage $EST --worker codex-a --timeout 3 --interval 0.1"
wait "$holder"; rm -f "$READY"
[ "$(rc_of e-scan-transition)" = 1 ] && out_of e-scan-transition | grep -q 'CALLBACK_IDENTITY_AMBIGUOUS' \
  && ok "T-P1E-WAIT-LOCKED-TRANSITION" || bad "T-P1E-WAIT-LOCKED-TRANSITION"
rm -f "$ED/$ES-$EST.$S2.$S2INC.callback" "$ED/$ES-$EST.$S2.callback"

# Syntax, not scalar presence, is part of the wire contract.
write_ecb "$ES-$EST.$S2.$S2INC.callback" "$S2" "$S2INC" ""
sed -i '' 's/timestamp: 2026-07-14T00:00:00Z/timestamp: 2026-99-99T00:00:00Z/' "$ED/$ES-$EST.$S2.$S2INC.callback"
run_in_pane "$S2:0.0" e-scan-bad-time "FORGE_WATCH_TRIGGER=0 $BRIDGE wait --slug $ES --stage $EST --worker codex-a --timeout 1 --interval 0.1"
[ "$(rc_of e-scan-bad-time)" = 1 ] && out_of e-scan-bad-time | grep -q 'CALLBACK_HEADER_INVALID' \
  && ok "T-P1E-WAIT-TIMESTAMP-SYNTAX" || bad "T-P1E-WAIT-TIMESTAMP-SYNTAX"
rm -f "$ED/$ES-$EST.$S2.$S2INC.callback"

# Dependency failure is not mislabeled as corrupt callback data.
mkdir -p "$WORK/p1e-no-yaml"; printf 'raise ImportError("shadowed for test")\n' > "$WORK/p1e-no-yaml/yaml.py"
run_in_pane "$S2:0.0" e-scan-dependency "PYTHONPATH=$WORK/p1e-no-yaml FORGE_WATCH_TRIGGER=0 $BRIDGE wait --slug $ES --stage $EST --worker codex-a --timeout 1 --interval 0.1"
[ "$(rc_of e-scan-dependency)" = 1 ] && out_of e-scan-dependency | grep -q 'CALLBACK_SCANNER_DEPENDENCY: PyYAML missing' \
  && out_of e-scan-dependency | grep -q 'DETAIL: CALLBACK_SCANNER_DEPENDENCY' \
  && ok "T-P1E-SCAN-DEPENDENCY" || bad "T-P1E-SCAN-DEPENDENCY"

# P1-WC production writer: exact name, exact headers, and selected pending anchor.
WC_SLUG=p1wc-writer; WC_STAGE=coding
pending_entry "$rootA" "$WC_SLUG" "$WC_STAGE" codex-a "2026-07-15T00:00:00Z" "$S2" "$S2INC"
run_in_pane "$S2:0.0" p1wc-writer "FORGE_WATCH_TRIGGER=0 $BRIDGE callback --slug $WC_SLUG --stage $WC_STAGE --status BLOCKED --worker codex-a --message exact --quiet"
WC_CB="$rootA/.dev/forge-tmp/callbacks/$WC_SLUG-$WC_STAGE.$S2.$S2INC.callback"
if [ "$(rc_of p1wc-writer)" = 0 ] && [ -f "$WC_CB" ] \
   && grep -q "^session: $S2$" "$WC_CB" && grep -q "^incarnation: $S2INC$" "$WC_CB" \
   && grep -q '^selected_pending_timestamp: "2026-07-15T00:00:00Z"$' "$WC_CB"; then
    ok "T-P1WC-WRITER-EXACT"
else bad "T-P1WC-WRITER-EXACT"; fi

# A known actor cannot mutate a coincident legacy shape; both files remain byte-identical.
cp "$WC_CB" "$rootA/.dev/forge-tmp/callbacks/$WC_SLUG-$WC_STAGE.$S2.callback"
sed -i '' '/^incarnation:/d;/^selected_pending_timestamp:/d' "$rootA/.dev/forge-tmp/callbacks/$WC_SLUG-$WC_STAGE.$S2.callback"
wc_before=$(find "$rootA/.dev/forge-tmp/callbacks" -maxdepth 1 -name "$WC_SLUG-$WC_STAGE*.callback" -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256 | shasum -a 256)
run_in_pane "$S2:0.0" p1wc-amb "FORGE_WATCH_TRIGGER=0 $BRIDGE callback-consume --slug $WC_SLUG --stage $WC_STAGE --status BLOCKED"
wc_after=$(find "$rootA/.dev/forge-tmp/callbacks" -maxdepth 1 -name "$WC_SLUG-$WC_STAGE*.callback" -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256 | shasum -a 256)
[ "$(rc_of p1wc-amb)" != 0 ] && out_of p1wc-amb | grep -q 'CALLBACK_IDENTITY_AMBIGUOUS' && [ "$wc_before" = "$wc_after" ] \
  && ok "T-P1WC-MUTATION-AMBIGUOUS" || bad "T-P1WC-MUTATION-AMBIGUOUS"
rm -f "$rootA/.dev/forge-tmp/callbacks/$WC_SLUG-$WC_STAGE.$S2.callback"

# Exact current consume is a byte-preserving move.
wc_hash=$(shasum -a 256 "$WC_CB" | awk '{print $1}'); wc_id=$(sed -n 's/^callback_id: //p' "$WC_CB")
run_in_pane "$S2:0.0" p1wc-consume "FORGE_WATCH_TRIGGER=0 $BRIDGE callback-consume --slug $WC_SLUG --stage $WC_STAGE --status BLOCKED"
WC_ARCH="$rootA/.dev/forge-tmp/callbacks/archive/$WC_SLUG-$WC_STAGE.$wc_id.callback"
[ "$(rc_of p1wc-consume)" = 0 ] && [ "$wc_hash" = "$(shasum -a 256 "$WC_ARCH" | awk '{print $1}')" ] \
  && grep -q "^incarnation: $S2INC$" "$WC_ARCH" && ok "T-P1WC-CONSUME-BYTE-PRESERVE" || bad "T-P1WC-CONSUME-BYTE-PRESERVE"

# ---- Per-callback usage observation (Codex parity V2) ----
echo "== per-callback usage observation (Codex parity V2) =="
UFILE="$rootA/.dev/forge-usage.$S2.yml"
UINC="$(tmux display-message -p -t "$S2:0.0" '#{session_created}')"
UFIX="$WORK/usage-fixtures"; mkdir -p "$UFIX"
printf 'gpt-5.5 xhigh fast · Context 73%% left · ~/repo\n' > "$UFIX/codex-73.txt"
printf '\033[36mgpt-5.5 medium fast · Context 64%% left · ~/repo\033[0m\n' > "$UFIX/codex-ansi.txt"
printf 'gpt-5.5 xhigh fast · Context 88%% left · ~/old\ngpt-5.5 xhigh fast · Context 41%% left · ~/new\n' > "$UFIX/codex-last.txt"
printf 'gpt-5.5 xhigh fast · ~/repo · main\n' > "$UFIX/codex-missing.txt"
printf 'gpt-5.5 xhigh fast · Context 140%% left · ~/repo\n' > "$UFIX/codex-bad.txt"
printf 'Claude Opus ctx: 42k (81%%)\n' > "$UFIX/claude-opus.txt"
printf '\033[35mClaude Sonnet ctx: 7k (5%%)\033[0m\n' > "$UFIX/claude-sonnet.txt"
: > "$UFIX/empty.txt"

usage_callback(){
  local slug="$1" worker="$2" fixture="$3" status="${4:-BLOCKED}"
  pending_entry "$rootA" "$slug" coding "$worker" "2026-07-20T00:00:${5:-00}Z" "$S2" "$UINC"
  run_in_pane "$S2:0.0" "$slug" "FORGE_USAGE_FIXTURE=$fixture FORGE_WATCH_TRIGGER=0 $BRIDGE callback --slug $slug --stage coding --status $status --worker $worker --message usage-test --quiet"
}

# A real terminal callback survives a forced snapshot write failure. The usage
# path is a directory, so the observer's atomic replace fails inside its fail-open
# boundary while callback publication, log closure, status rendering, and rc stay healthy.
# Earlier callback tests may have created a snapshot through the existing observer;
# reset only this hermetic test root immediately before forcing the failure.
rm -f "$UFILE" "$UFILE.lock" "$UFILE.tmp"
rm -f "$rootA/.dev/forge-status.$S2.md"
mkdir "$UFILE"
usage_callback usage-observer-failure codex-a "$UFIX/codex-73.txt" DONE 01
uclose=$(python3 - "$rootA/.dev/proposals/usage-observer-failure/forge-log.yml" <<'PY'
import sys,yaml
entry=(yaml.safe_load(open(sys.argv[1])) or {})['entries'][0]
print('closed' if entry.get('response') is not None else 'open')
PY
)
if [ "$(rc_of usage-observer-failure)" = 0 ] \
   && [ -f "$rootA/.dev/forge-tmp/callbacks/usage-observer-failure-coding.$S2.$UINC.callback" ] \
   && [ "$uclose" = closed ] && [ -f "$rootA/.dev/forge-status.$S2.md" ] \
   && grep -q 'CALLBACK: pipeline=usage-observer-failure ' "$rootA/.dev/forge-tmp/orchestrator-events.log" \
   && grep -q 'USAGE: pipeline=usage-observer-failure ' "$rootA/.dev/forge-tmp/orchestrator-events.log"; then
  ok "T-USAGE-OBSERVER-FAILURE callback contract remains successful"
else
  bad "T-USAGE-OBSERVER-FAILURE callback contract changed (rc=$(rc_of usage-observer-failure) close=$uclose)"
fi
rmdir "$UFILE"
rm -f "$UFILE.tmp"

usage_callback usage-codex-success codex-a "$UFIX/codex-73.txt" BLOCKED 02
python3 - "$UFILE" <<'PY' \
  && ok "T-USAGE-CODEX-SUCCESS snapshot fields" || bad "T-USAGE-CODEX-SUCCESS snapshot fields"
import sys,yaml
r=(yaml.safe_load(open(sys.argv[1])) or {})['workers']['codex-a']
assert r['family']=='codex' and r['headroom']==73 and r['pct']==27
assert r['tokens'] is None and r['source']=='pane-footer' and r['confidence']=='high'
assert r['reason'] is None and r['raw'].endswith('Context 73% left · ~/repo')
PY
usage_event_count=$(grep -Ec 'USAGE: pipeline=usage-codex-success .*worker=codex-a family=codex pct=27 tokens=null headroom=73 source=pane-footer confidence=high' "$rootA/.dev/forge-tmp/orchestrator-events.log")
[ "$usage_event_count" = 1 ] \
  && ok "T-USAGE-CODEX-SUCCESS exactly one matching USAGE event" || bad "T-USAGE-CODEX-SUCCESS expected one matching USAGE event (got $usage_event_count)"
run_in_pane "$S2:0.0" usage-codex-read "FORGE_WATCH_TRIGGER=0 $BRIDGE usage codex-a"
out_of usage-codex-read | grep -q 'codex-a.*headroom=73.*confidence=high.*pct=27 tokens=None' \
  && ok "T-USAGE-CODEX-SUCCESS public reader uses existing numeric branch" \
  || bad "T-USAGE-CODEX-SUCCESS public reader did not show numeric record"

usage_callback usage-codex-ansi codex-b "$UFIX/codex-ansi.txt" BLOCKED 03
python3 - "$UFILE" <<'PY' \
  && ok "T-USAGE-CODEX-ANSI normalized footer parses" || bad "T-USAGE-CODEX-ANSI normalized footer parses"
import sys,yaml
r=yaml.safe_load(open(sys.argv[1]))['workers']['codex-b']
assert r['headroom']==64 and r['pct']==36 and r['confidence']=='high' and '\x1b' not in r['raw']
PY

usage_callback usage-codex-last codex-a "$UFIX/codex-last.txt" BLOCKED 04
python3 - "$UFILE" <<'PY' \
  && ok "T-USAGE-CODEX-LAST newest anchor wins" || bad "T-USAGE-CODEX-LAST newest anchor wins"
import sys,yaml
r=yaml.safe_load(open(sys.argv[1]))['workers']['codex-a']
assert r['headroom']==41 and r['pct']==59 and r['raw'].endswith('~/new')
PY

usage_callback usage-codex-capture-failed codex-b "$UFIX/empty.txt" BLOCKED 10
python3 - "$UFILE" <<'PY' \
  && ok "T-USAGE-CODEX-CAPTURE-FAILED empty capture degrades honestly" || bad "T-USAGE-CODEX-CAPTURE-FAILED empty capture did not degrade"
import sys,yaml
r=yaml.safe_load(open(sys.argv[1]))['workers']['codex-b']
assert r['headroom']=='unknown' and r['pct'] is None and r['tokens'] is None
assert r['confidence']=='none' and r['source']=='pane-footer'
assert r['reason']=='capture-failed' and r['raw'] is None
PY

usage_callback usage-codex-missing codex-b "$UFIX/codex-missing.txt" BLOCKED 05
python3 - "$UFILE" <<'PY' \
  && ok "T-USAGE-CODEX-MISSING degrades without callback failure" || bad "T-USAGE-CODEX-MISSING degrades without callback failure"
import sys,yaml
r=yaml.safe_load(open(sys.argv[1]))['workers']['codex-b']
assert r['headroom']=='unknown' and r['pct'] is None and r['tokens'] is None
assert r['confidence']=='none' and r['source']=='pane-footer'
assert r['reason']=='codex-context-anchor-missing'
PY
[ "$(rc_of usage-codex-missing)" = 0 ] || bad "T-USAGE-CODEX-MISSING callback returned nonzero"

usage_callback usage-codex-bad codex-a "$UFIX/codex-bad.txt" BLOCKED 06
python3 - "$UFILE" <<'PY' \
  && ok "T-USAGE-CODEX-BAD invalid percent cannot become routing headroom" || bad "T-USAGE-CODEX-BAD invalid percent cannot become routing headroom"
import sys,yaml
r=yaml.safe_load(open(sys.argv[1]))['workers']['codex-a']
assert r['headroom']=='unknown' and r['pct'] is None and r['tokens'] is None
assert r['confidence']=='none' and r['reason']=='bad-percent'
assert r['raw'].endswith('Context 140% left · ~/repo')
PY
run_in_pane "$S2:0.0" usage-bad-read "FORGE_WATCH_TRIGGER=0 $BRIDGE usage codex-a"
out_of usage-bad-read | grep -q 'headroom=unknown.*reason=bad-percent' \
  && ok "T-USAGE-CODEX-BAD public read stays unknown" || bad "T-USAGE-CODEX-BAD public read stays unknown"

usage_callback usage-claude-opus claude-opus "$UFIX/claude-opus.txt" BLOCKED 07
usage_callback usage-claude-sonnet claude-sonnet "$UFIX/claude-sonnet.txt" BLOCKED 08
python3 - "$UFILE" <<'PY' \
  && ok "T-USAGE-CLAUDE-NONREG Opus and Sonnet semantics pinned" || bad "T-USAGE-CLAUDE-NONREG Opus and Sonnet semantics pinned"
import sys,yaml
w=yaml.safe_load(open(sys.argv[1]))['workers']
o=w['claude-opus']; s=w['claude-sonnet']
assert (o['pct'],o['tokens'],o['headroom'],o['source'],o['confidence'])==(81,'42k',19,'pane-footer','high')
assert (s['pct'],s['tokens'],s['headroom'],s['source'],s['confidence'])==(5,'7k',95,'pane-footer','high')
PY

# A final Codex keyed upsert must preserve both Claude records and the other
# Codex worker's degraded record in the same session snapshot.
usage_callback usage-codex-merge codex-a "$UFIX/codex-73.txt" BLOCKED 09
python3 - "$UFILE" <<'PY' \
  && ok "T-USAGE-MULTI-WORKER keyed upsert preserves all workers" || bad "T-USAGE-MULTI-WORKER keyed upsert preserves all workers"
import sys,yaml
w=yaml.safe_load(open(sys.argv[1]))['workers']
assert set(('codex-a','codex-b','claude-opus','claude-sonnet')).issubset(w)
assert w['codex-a']['headroom']==73 and w['codex-b']['reason']=='codex-context-anchor-missing'
assert w['claude-opus']['headroom']==19 and w['claude-sonnet']['headroom']==95
PY

observer_body=$(sed -n '/^_observe_usage()/,/^}/p' "$BRIDGE" | sed '/^[[:space:]]*#/d')
if printf '%s\n' "$observer_body" | grep -Eq 'tmux[[:space:]]+send-keys|/clear|/new|/compact'; then
  bad "T-USAGE-READ-ONLY observer contains worker-facing mutation"
else
  ok "T-USAGE-READ-ONLY observer contains no worker-facing mutation"
fi

ACTIVE_USAGE_DOCS="$ROOT/docs/forge-operator-guide.md $ROOT/docs/forge-technical-reference.md $ROOT/skills/forge-orchestrator/SKILL.md $ROOT/agents/forge-orchestrator.md"
if grep -Eiq 'Codex is always unknown|Codex.*always.*unknown|codex-no-pane-usage|CLI exposes no usage in pane text|no pane-text usage signal' $ACTIVE_USAGE_DOCS; then
  bad "T-USAGE-DOC-OBSOLETE active docs retain obsolete Codex contract"
else
  ok "T-USAGE-DOC-OBSOLETE obsolete Codex contract removed"
fi
sed -n '/^4\. \*\*Usage awareness\*\*/,/^5\. \*\*If no one is available\*\*/p' "$ROOT/skills/forge-orchestrator/SKILL.md" > "$WORK/usage-skill.txt"
sed -n '/^4\. \*\*Usage awareness\*\*/,/^5\. \*\*If no one is available\*\*/p' "$ROOT/agents/forge-orchestrator.md" > "$WORK/usage-agent.txt"
if [ -s "$WORK/usage-skill.txt" ] && diff -q "$WORK/usage-skill.txt" "$WORK/usage-agent.txt" >/dev/null; then
  ok "T-USAGE-DOC-LOCKSTEP skill and agent Usage Awareness agree"
else
  bad "T-USAGE-DOC-LOCKSTEP skill and agent Usage Awareness drifted"
fi
grep -F 'A valid Codex `Context N% left` footer now supplies that numeric signal' "$ROOT/skills/forge-orchestrator/SKILL.md" > "$WORK/usage-route-skill.txt"
grep -F 'A valid Codex `Context N% left` footer now supplies that numeric signal' "$ROOT/agents/forge-orchestrator.md" > "$WORK/usage-route-agent.txt"
if [ -s "$WORK/usage-route-skill.txt" ] && diff -q "$WORK/usage-route-skill.txt" "$WORK/usage-route-agent.txt" >/dev/null; then
  ok "T-USAGE-DOC-ROUTE-LOCKSTEP skill and agent implementation routes agree"
else
  bad "T-USAGE-DOC-ROUTE-LOCKSTEP skill and agent implementation routes drifted"
fi

# ═════════ Worker-context-hygiene (worker-context-hygiene proposal) ═════════
# §0 harness: pure-helper extraction (FNS idiom), fixture seams, env defaults.
echo "── HYG §0: pure-helper extraction ──"
HFNS="$WORK/hfns.sh"
sed -n '/^_worker_family()/,/^}$/p; /^_hygiene_worker_ok()/,/^}$/p; /^_hygiene_valid_pct()/,/^}$/p; /^_hygiene_mode()/,/^}$/p; /^_hygiene_enforcing()/,/^}$/p; /^_hygiene_crash_at()/,/^}$/p; /^_worker_min_headroom()/,/^}$/p; /^_reset_proof_timeout()/,/^}$/p; /^_reset_automation_enabled()/,/^}$/p; /^_reset_baseline()/,/^}$/p; /^_reset_proof_probe()/,/^}$/p; /^_hygiene_file()/,/^}$/p; /^_hygiene_write()/,/^}$/p; /^_hygiene_decide()/,/^}$/p; /^_hygiene_current_gen()/,/^}$/p; /^_terminal_state()/,/^}$/p; /^_hygiene_journal_preflight()/,/^}$/p; /^_hygiene_finalization_field()/,/^}$/p; /^_hygiene_activation_blockers()/,/^}$/p; /^cmd_hygiene_gc()/,/^}$/p; /^_worker_lock_fd()/,/^}$/p; /^_worker_lock()/,/^}$/p; /^_worker_unlock()/,/^}$/p; /^_terminal_lock()/,/^}$/p; /^_terminal_unlock()/,/^}$/p; /^_hygiene_release_all()/,/^}$/p' "$BRIDGE" > "$HFNS"
grep -q '_reset_proof_probe' "$HFNS" && ok "HYG helper extraction non-empty" || bad "HYG helper extraction empty"
HFIX="$ROOT/tests/forge-bridge/fixtures"
# Shared env for every hygiene subshell: hermetic defaults + capability fixture.
hyg_env() {
  export FORGE_WORKER_HYGIENE_MODE="${1:-observe}"
  export FORGE_WORKER_AUTO_RESET_CLAUDE=1 FORGE_WORKER_AUTO_RESET_CODEX=1
  export FORGE_WORKER_HYGIENE_DEGRADED_CLAUDE=0 FORGE_WORKER_HYGIENE_DEGRADED_CODEX=0
  export FORGE_WORKER_RESET_PROOF_TIMEOUT_S=1 FORGE_WORKER_LOCK_WAIT_S=2
  export FORGE_HYGIENE_LOCK_WAIT_S=2 FORGE_WORKER_MIN_HEADROOM=75
  export FORGE_RESET_CAPABILITY_FILE="$HFIX/reset-capability.yml"
}

echo "── HYG §D: threshold/mode config errors + capability gate ──"
# Source into the MAIN shell (FNS idiom) so ok/bad counters propagate to the tally.
# shellcheck disable=SC1090
. "$HFNS"
hyg_env
  [ "$(FORGE_WORKER_MIN_HEADROOM=75 _worker_min_headroom claude-opus)" = 75 ] \
    && ok "T-HYG-CFG shared 75 resolves" || bad "T-HYG-CFG shared 75 wrong"
  [ "$(FORGE_WORKER_MIN_HEADROOM=75 FORGE_WORKER_MIN_HEADROOM_CODEX=60 _worker_min_headroom codex-a)" = 60 ] \
    && ok "T-HYG-CFG family override wins" || bad "T-HYG-CFG family override lost"
  [ "$(FORGE_WORKER_MIN_HEADROOM=75 _worker_min_headroom codex-b)" = 75 ] \
    && ok "T-HYG-CFG unset family inherits shared" || bad "T-HYG-CFG inherit broken"
  [ "$(FORGE_WORKER_MIN_HEADROOM=0 _worker_min_headroom claude-opus)" = 0 ] \
    && [ "$(FORGE_WORKER_MIN_HEADROOM=100 _worker_min_headroom claude-opus)" = 100 ] \
    && ok "T-HYG-CFG 0/100 boundary valid" || bad "T-HYG-CFG boundary rejected"
  o=$(FORGE_WORKER_MIN_HEADROOM=abc _worker_min_headroom claude-opus 2>&1); rc=$?
  [ "$rc" = 1 ] && echo "$o" | grep -q CONFIG_ERROR \
    && ok "T-HYG-CFG shared=abc CONFIG_ERROR rc1" || bad "T-HYG-CFG abc accepted: rc=$rc $o"
  o=$(FORGE_WORKER_MIN_HEADROOM=150 _worker_min_headroom claude-opus 2>&1); rc=$?
  [ "$rc" = 1 ] && ok "T-HYG-CFG shared=150 rejected" || bad "T-HYG-CFG 150 accepted"
  o=$(FORGE_WORKER_MIN_HEADROOM=75 FORGE_WORKER_MIN_HEADROOM_CLAUDE=xyz _worker_min_headroom claude-sonnet 2>&1); rc=$?
  [ "$rc" = 1 ] && echo "$o" | grep -q 'CONFIG_ERROR: FORGE_WORKER_MIN_HEADROOM_CLAUDE' \
    && ok "T-HYG-CFG family=xyz CONFIG_ERROR rc1" || bad "T-HYG-CFG family xyz accepted: $o"
  o=$(FORGE_WORKER_HYGIENE_MODE=enforc _hygiene_mode 2>&1); rc=$?
  [ "$rc" = 1 ] && echo "$o" | grep -q CONFIG_ERROR \
    && ok "T-HYG-MODE typo is a hard error (never silent observe)" || bad "T-HYG-MODE typo fell through: rc=$rc"
  [ "$(FORGE_WORKER_HYGIENE_MODE=enforce _hygiene_mode)" = enforce ] \
    && [ "$(FORGE_WORKER_HYGIENE_MODE=observe _hygiene_mode)" = observe ] \
    && ok "T-HYG-MODE valid modes echo" || bad "T-HYG-MODE valid mode broken"
  FORGE_WORKER_HYGIENE_MODE=enforc _hygiene_enforcing 2>/dev/null; rc=$?
  [ "$rc" = 2 ] && ok "T-HYG-MODE _hygiene_enforcing rc2 on invalid" || bad "T-HYG-MODE enforcing rc=$rc on invalid"
  # Capability gate (P4): fixture-driven, fail-closed.
  o=$(_reset_automation_enabled claude); rc=$?
  [ "$rc" = 0 ] && [ "$o" = enabled ] && ok "T-HYG-CAP claude proven+on => enabled" || bad "T-HYG-CAP claude: rc=$rc $o"
  o=$(_reset_automation_enabled codex); rc=$?
  [ "$rc" = 1 ] && echo "$o" | grep -q 'disabled:unproven' && ok "T-HYG-CAP codex unproven => disabled (family-disabled)" || bad "T-HYG-CAP codex: rc=$rc $o"
  o=$(FORGE_WORKER_AUTO_RESET_CLAUDE=0 _reset_automation_enabled claude); rc=$?
  [ "$rc" = 1 ] && [ "$o" = "disabled:operator-off" ] && ok "T-HYG-CAP operator-off wins" || bad "T-HYG-CAP operator-off: $o"
  o=$(FORGE_RESET_CAPABILITY_FILE=/nonexistent-cap.yml _reset_automation_enabled claude); rc=$?
  [ "$rc" = 1 ] && echo "$o" | grep -q 'no-fixture' && ok "T-HYG-CAP missing fixture fail-closed" || bad "T-HYG-CAP missing fixture: $o"

echo "── HYG §R: reset-proof probe (before/after fixture pairs) ──"
# Visible-only pin: neither capture in baseline/probe may use scrollback (-S).
sed -n '/^_reset_baseline()/,/^}$/p; /^_reset_proof_probe()/,/^}$/p' "$BRIDGE" | grep 'capture-pane' | grep -q -- '-S' \
  && bad "T-HYG-RESET-SCROLLBACK-ONLY capture uses -S (scrollback leaks into proof)" \
  || ok "T-HYG-RESET-SCROLLBACK-ONLY captures are visible-screen only (no -S)"
  probe_pair() {  # <name> <family> <before> <after> <expect-grep>
    local name="$1" fam="$2" b="$3" a="$4" want="$5" bl bfp bpres bsid bh o rc
    bl="$(FORGE_RESET_BASELINE_FIXTURE="$b" _reset_baseline s 0 "$fam")" || { bad "$name baseline failed"; return; }
    IFS=$'\t' read -r bfp bpres bsid bh <<< "$bl"
    o=$(FORGE_RESET_PROOF_FIXTURE="$a" _reset_proof_probe s 0 "$fam" "$bfp" "$bpres" "$bsid"); rc=$?
    if echo "$o" | grep -q "$want"; then ok "$name → $o"; else bad "$name wanted '$want' got rc=$rc '$o'"; fi
  }
  probe_pair "T-HYG-RESET-PAIR-PROVEN claude-opus" claude \
    "$HFIX/claude-opus-clear-before.txt" "$HFIX/claude-opus-clear-after.txt" '^PROVEN kind=post-baseline-anchor'
  probe_pair "T-HYG-RESET-PAIR-PROVEN claude-sonnet" claude \
    "$HFIX/claude-sonnet-clear-before.txt" "$HFIX/claude-sonnet-clear-after.txt" '^PROVEN kind=post-baseline-anchor'
  probe_pair "T-HYG-RESET-PAIR-PROVEN codex-a" codex \
    "$HFIX/codex-a-clear-before.txt" "$HFIX/codex-a-clear-after.txt" '^PROVEN kind=post-baseline-anchor'
  probe_pair "T-HYG-RESET-PAIR-PROVEN codex-b" codex \
    "$HFIX/codex-b-clear-before.txt" "$HFIX/codex-b-clear-after.txt" '^PROVEN kind=post-baseline-anchor'
  probe_pair "T-HYG-RESET-DEEP-BASELINE >120-line baseline" claude \
    "$HFIX/deep-conversation-before.txt" "$HFIX/claude-opus-clear-after.txt" '^PROVEN kind=post-baseline-anchor'
  probe_pair "T-HYG-RESET-IDLE-FAIL ignored clear (idle==idle)" claude \
    "$HFIX/ignored-clear-idle.txt" "$HFIX/ignored-clear-idle.txt" '^UNPROVEN:no-redraw'
  probe_pair "T-HYG-RESET-HISTORICAL-FAIL banner already visible" claude \
    "$HFIX/claude-opus-clear-after.txt" "$HFIX/claude-opus-clear-after.txt" 'anchor-was-present-pre-clear'
  # Scrollback-only anchor: the visible proof screen has NO banner (banner lives only in
  # scrollback, which the probe never captures) — redraw alone must not prove.
  printf '● cleared? screen redrew but no banner visible\n> \n' > "$WORK/scrollback-visible.txt"
  probe_pair "T-HYG-RESET-SCROLLBACK-ONLY visible-no-banner" claude \
    "$HFIX/claude-opus-clear-before.txt" "$WORK/scrollback-visible.txt" '^UNPROVEN:no-new-anchor'
  # Session-id change proof path.
  cat > "$WORK/cap-sid.yml" <<'YML'
version: 1
families:
  claude:
    proven: true
    new_conversation_anchor: ''
    session_id_capture: 'session: ([a-f0-9]+)'
YML
  printf 'working…\nsession: abc123\n> \n' > "$WORK/sid-before.txt"
  printf 'fresh…\nsession: def456\n> \n'   > "$WORK/sid-after.txt"
  FORGE_RESET_CAPABILITY_FILE="$WORK/cap-sid.yml" \
    probe_pair "T-HYG-RESET-SIDCHANGE" claude "$WORK/sid-before.txt" "$WORK/sid-after.txt" '^PROVEN kind=session-id-change'

echo "── HYG §D: decision precedence (hermetic journal fixtures) ──"
export DEV_DIR=".dev"
export ID_target_session="hygS" ID_target_incarnation="42"
HJF=".dev/forge-hygiene.hygS.42.yml"
hj() {  # hj <root> — write journal from stdin
  mkdir -p "$1/.dev"; cat > "$1/$HJF"
}
hj_obs() {  # hj_obs <root> <worker> <latest> <covers> <headroom> <confidence>
  hj "$1" <<EOF
version: 1
session: hygS
incarnation: 42
next_generation: 9
workers:
  $2:
    latest_generation: $3
    observation:
      state: known
      covers_generation: $4
      callback_id: cb1
      pending_timestamp: "2026-07-23T00:00:00Z"
      usage_record_hash: aaaa
      headroom: $5
      confidence: $6
      measured_at: "2020-01-01T00:00:00Z"
EOF
}
D="$WORK/hygD"; mkdir -p "$D/.dev"
hj_obs "$D" claude-opus 4 4 76 high
[ "$(_hygiene_decide "$D" claude-opus '' '' | awk '{print $1}')" = KEEP_OBSERVED ] \
  && ok "T-HYG-DEC 76 > 75 → KEEP_OBSERVED" || bad "T-HYG-DEC 76 wrong: $(_hygiene_decide "$D" claude-opus '' '')"
hj_obs "$D" claude-opus 4 4 75 high
[ "$(_hygiene_decide "$D" claude-opus '' '' | awk '{print $1}')" = RESET_THRESHOLD ] \
  && ok "T-HYG-DEC 75 <= 75 → RESET_THRESHOLD (inclusive)" || bad "T-HYG-DEC 75 wrong"
hj_obs "$D" claude-opus 4 4 74 high
[ "$(_hygiene_decide "$D" claude-opus '' '' | awk '{print $1}')" = RESET_THRESHOLD ] \
  && ok "T-HYG-DEC 74 → RESET_THRESHOLD" || bad "T-HYG-DEC 74 wrong"
hj_obs "$D" claude-opus 4 4 1 high
[ "$(FORGE_WORKER_MIN_HEADROOM=0 _hygiene_decide "$D" claude-opus '' '' | awk '{print $1}')" = KEEP_OBSERVED ] \
  && ok "T-HYG-DEC min=0 keeps headroom 1" || bad "T-HYG-DEC min=0 wrong"
hj_obs "$D" claude-opus 4 4 100 high
[ "$(FORGE_WORKER_MIN_HEADROOM=100 _hygiene_decide "$D" claude-opus '' '' | awk '{print $1}')" = RESET_THRESHOLD ] \
  && ok "T-HYG-DEC min=100 resets headroom 100" || bad "T-HYG-DEC min=100 wrong"
o=$(FORGE_WORKER_MIN_HEADROOM=abc _hygiene_decide "$D" claude-opus '' '' 2>/dev/null); rc=$?
[ "$rc" = 1 ] && echo "$o" | grep -q 'config-error' \
  && ok "T-HYG-DEC invalid threshold → rc1 config-error (mutates nothing)" || bad "T-HYG-DEC config-error wrong: rc=$rc $o"
# Identical decision logic for all four workers.
for hw in claude-opus codex-a codex-b claude-sonnet; do
  hj_obs "$D" "$hw" 4 4 76 high
  [ "$(_hygiene_decide "$D" "$hw" '' '' | awk '{print $1}')" = KEEP_OBSERVED ] \
    && ok "T-HYG-DEC identical policy for $hw" || bad "T-HYG-DEC $hw diverged"
done
rm -f "$D/$HJF"
[ "$(_hygiene_decide "$D" claude-opus '' '')" = "RESET_UNPROVEN no-journal" ] \
  && ok "T-HYG-DEC missing journal → RESET_UNPROVEN no-journal" || bad "T-HYG-DEC missing journal wrong"
printf '{unclosed: [\n' > "$D/$HJF"
[ "$(_hygiene_decide "$D" claude-opus '' '')" = "RESET_UNPROVEN malformed-journal" ] \
  && ok "T-HYG-DEC malformed journal → RESET_UNPROVEN" || bad "T-HYG-DEC malformed wrong"
hj_obs "$D" claude-opus 4 4 90 low
[ "$(_hygiene_decide "$D" claude-opus '' '')" = "RESET_UNPROVEN no-matching-coverage" ] \
  && ok "T-HYG-DEC confidence=low → RESET_UNPROVEN" || bad "T-HYG-DEC low confidence wrong"
hj_obs "$D" claude-opus 4 3 90 high
[ "$(_hygiene_decide "$D" claude-opus '' '')" = "RESET_UNPROVEN no-matching-coverage" ] \
  && ok "T-HYG-DEC stale generation coverage → RESET_UNPROVEN" || bad "T-HYG-DEC stale-gen wrong"
hj_obs "$D" claude-opus 4 4 90 high   # measured_at pinned to 2020 in hj_obs
[ "$(_hygiene_decide "$D" claude-opus '' '' | awk '{print $1}')" = KEEP_OBSERVED ] \
  && ok "T-HYG-DEC no age-only invalidation (2020 reading still valid)" || bad "T-HYG-DEC age TTL crept in"
hj "$D" <<'EOF'
version: 1
session: hygS
incarnation: 43
next_generation: 9
workers:
  claude-opus:
    latest_generation: 4
EOF
[ "$(_hygiene_decide "$D" claude-opus '' '')" = "RESET_UNPROVEN identity-mismatch" ] \
  && ok "T-HYG-DEC same-name rebirth (incarnation changed) → identity-mismatch" || bad "T-HYG-DEC rebirth wrong"
hj "$D" <<'EOF'
version: 1
session: otherS
incarnation: 42
next_generation: 9
workers:
  claude-opus:
    latest_generation: 4
EOF
[ "$(_hygiene_decide "$D" claude-opus '' '')" = "RESET_UNPROVEN identity-mismatch" ] \
  && ok "T-HYG-DEC foreign session → identity-mismatch" || bad "T-HYG-DEC foreign session wrong"
hj "$D" <<'EOF'
version: 1
session: hygS
incarnation: 42
next_generation: 9
workers:
  claude-opus:
    latest_generation: 4
    reset:
      id: reset-x
      covers_generation: 4
      proof_hash: beef
    observation:
      state: known
      covers_generation: 3
      headroom: 10
      confidence: high
EOF
[ "$(_hygiene_decide "$D" claude-opus '' '')" = "KEEP_RESET_PROVEN reset-covers-latest" ] \
  && ok "T-HYG-DEC reset beats older low observation (reset-wins)" || bad "T-HYG-DEC reset-wins wrong"
hj "$D" <<'EOF'
version: 1
session: hygS
incarnation: 42
next_generation: 9
workers:
  claude-opus:
    latest_generation: 5
    delivery:
      state: attempting
    reset:
      covers_generation: 4
      proof_hash: beef
EOF
[ "$(_hygiene_decide "$D" claude-opus '' '')" = "RESET_UNPROVEN attempting" ] \
  && ok "T-HYG-DEC attempting delivery → RESET_UNPROVEN" || bad "T-HYG-DEC attempting wrong"
hj "$D" <<'EOF'
version: 1
session: hygS
incarnation: 42
next_generation: 9
workers:
  claude-opus:
    latest_generation: 5
    reset:
      covers_generation: 4
      proof_hash: beef
    observation:
      state: known
      covers_generation: 4
      headroom: 90
      confidence: high
EOF
[ "$(_hygiene_decide "$D" claude-opus '' '')" = "RESET_UNPROVEN no-matching-coverage" ] \
  && ok "T-HYG-DEC later activity invalidates reset AND observation" || bad "T-HYG-DEC later-activity wrong"
hj "$D" <<'EOF'
version: 1
session: hygS
incarnation: 42
next_generation: 9
workers: {}
EOF
[ "$(_hygiene_decide "$D" claude-opus '' '')" = "RESET_UNPROVEN no-record" ] \
  && ok "T-HYG-DEC unknown worker → RESET_UNPROVEN no-record" || bad "T-HYG-DEC no-record wrong"

echo "── HYG §J: journal writer (atomic, identity, no-clobber) ──"
J="$WORK/hygJ"; mkdir -p "$J/.dev"
g=$(_hygiene_write "$J" next-gen ""); rc=$?
[ "$rc" = 0 ] && [ "$g" = 1 ] && ok "T-HYG-JOURNAL next-gen allocates 1" || bad "T-HYG-JOURNAL first alloc: rc=$rc g=$g"
g=$(_hygiene_write "$J" next-gen "")
[ "$g" = 2 ] && ok "T-HYG-JOURNAL next-gen advances high-water" || bad "T-HYG-JOURNAL second alloc: $g"
( _hygiene_write "$J" next-gen "" > "$WORK/g1.out" 2>/dev/null ) &
( _hygiene_write "$J" next-gen "" > "$WORK/g2.out" 2>/dev/null ) &
wait
g1=$(cat "$WORK/g1.out"); g2=$(cat "$WORK/g2.out")
[ -n "$g1" ] && [ -n "$g2" ] && [ "$g1" != "$g2" ] \
  && ok "T-HYG-JOURNAL concurrent next-gen → distinct generations ($g1/$g2)" \
  || bad "T-HYG-JOURNAL concurrent alloc collided: '$g1' '$g2'"
_hygiene_write "$J" delivery claude-opus "id=d1" "generation=3" "kind=dispatch" "slug=s" "stage=coding" "pending_timestamp=T1" \
  && grep -q 'state: attempting' "$J/$HJF" && ok "T-HYG-JOURNAL delivery persists attempting" || bad "T-HYG-JOURNAL delivery write failed"
_hygiene_write "$J" delivered claude-opus \
  && grep -q 'state: delivered' "$J/$HJF" && ok "T-HYG-JOURNAL delivered flips state" || bad "T-HYG-JOURNAL delivered failed"
_hygiene_write "$J" promote claude-opus "pending_timestamp=T1" \
  && grep -q 'state: callback-confirmed' "$J/$HJF" && ok "T-HYG-JOURNAL exact promote → callback-confirmed" || bad "T-HYG-JOURNAL promote failed"
_hygiene_write "$J" delivery claude-opus "id=d2" "generation=4" "kind=dispatch" "pending_timestamp=T2" >/dev/null
_hygiene_write "$J" promote claude-opus "pending_timestamp=WRONG"
grep -q 'state: attempting' "$J/$HJF" \
  && ok "T-HYG-JOURNAL mismatched promote does NOT confirm" || bad "T-HYG-JOURNAL mismatched promote confirmed"
JF2="$WORK/hygJ2"; mkdir -p "$JF2/.dev"
hj "$JF2" <<'EOF'
version: 1
session: foreignS
incarnation: 7
next_generation: 3
workers: {}
EOF
sha_before=$(shasum -a 256 "$JF2/$HJF" | awk '{print $1}')
_hygiene_write "$JF2" next-gen "" >/dev/null 2>&1; rc=$?
sha_after=$(shasum -a 256 "$JF2/$HJF" | awk '{print $1}')
[ "$rc" = 4 ] && [ "$sha_before" = "$sha_after" ] \
  && ok "T-HYG-JOURNAL foreign identity → rc4, no write" || bad "T-HYG-JOURNAL foreign identity: rc=$rc"
printf 'version: 1\nsession: hygS\n  bad-indent: {{{\n' > "$JF2/$HJF"
sha_before=$(shasum -a 256 "$JF2/$HJF" | awk '{print $1}')
_hygiene_write "$JF2" next-gen "" >/dev/null 2>&1; rc=$?
sha_after=$(shasum -a 256 "$JF2/$HJF" | awk '{print $1}')
[ "$rc" = 5 ] && [ "$sha_before" = "$sha_after" ] \
  && ok "T-HYG-JOURNAL-MALFORMED-NOCLOBBER parse-error → rc5 byte-identical" || bad "T-HYG-JOURNAL malformed clobbered: rc=$rc"
printf -- '- a\n- b\n' > "$JF2/$HJF"
sha_before=$(shasum -a 256 "$JF2/$HJF" | awk '{print $1}')
_hygiene_write "$JF2" next-gen "" >/dev/null 2>&1; rc=$?
sha_after=$(shasum -a 256 "$JF2/$HJF" | awk '{print $1}')
[ "$rc" = 5 ] && [ "$sha_before" = "$sha_after" ] \
  && ok "T-HYG-JOURNAL-MALFORMED-NOCLOBBER non-mapping → rc5 byte-identical" || bad "T-HYG-JOURNAL non-mapping clobbered: rc=$rc"
JF3="$WORK/hygJ3"; mkdir -p "$JF3/.dev/forge-tmp/hygiene-locks"
chmod u-w "$JF3/.dev"
_hygiene_write "$JF3" next-gen "" >/dev/null 2>&1; rc=$?
chmod u+w "$JF3/.dev"
[ "$rc" = 6 ] && [ -z "$(ls "$JF3/.dev"/forge-hygiene.*.tmp.* 2>/dev/null)" ] \
  && ok "T-HYG-JOURNAL-WRITE-FAIL unwritable dir → rc6, no partial tmp" || bad "T-HYG-JOURNAL write-fail: rc=$rc"
# Journal preflight (P0-3): side-effect-free, foreign/rebirth/malformed refuse.
_hygiene_journal_preflight "$J" && ok "T-HYG-PREFLIGHT own journal OK" || bad "T-HYG-PREFLIGHT own journal refused"
_hygiene_journal_preflight "$JF2" 2>/dev/null; rc=$?
[ "$rc" = 1 ] && ok "T-HYG-PREFLIGHT non-mapping journal refused" || bad "T-HYG-PREFLIGHT non-mapping accepted"
rm -f "$JF2/$HJF"
_hygiene_journal_preflight "$JF2" && ok "T-HYG-PREFLIGHT missing journal OK (unproven downstream)" || bad "T-HYG-PREFLIGHT missing refused"
hj "$JF2" <<'EOF'
version: 1
session: hygS
incarnation: 43
next_generation: 3
EOF
_hygiene_journal_preflight "$JF2" 2>/dev/null; rc=$?
[ "$rc" = 1 ] && ok "T-HYG-PREFLIGHT same-name rebirth refused" || bad "T-HYG-PREFLIGHT rebirth accepted"

echo "── HYG §G: hygiene-gc (conservative, dead-incarnation only) ──"
GCROOT="$WORK/hygGC"; mkdir -p "$GCROOT/.dev/forge-tmp/hygiene-locks"
require_identity(){ return 0; }
_resolve_project_root(){ printf '%s' "$GCROOT"; }
_same_root_sessions(){ printf 'live-sess\n'; }
cat > "$GCROOT/.dev/forge-hygiene.ghost.7.yml" <<'EOF'
version: 1
session: ghost
incarnation: 7
terminal:
  state: complete
EOF
touch -t 202001010000 "$GCROOT/.dev/forge-hygiene.ghost.7.yml"
touch "$GCROOT/.dev/forge-tmp/hygiene-locks/ghost.7.journal.lock"
cat > "$GCROOT/.dev/forge-hygiene.live-sess.1.yml" <<'EOF'
version: 1
session: live-sess
incarnation: 1
terminal:
  state: complete
EOF
touch -t 202001010000 "$GCROOT/.dev/forge-hygiene.live-sess.1.yml"
cat > "$GCROOT/.dev/forge-hygiene.ghost2.1.yml" <<'EOF'
version: 1
session: ghost2
incarnation: 1
terminal:
  state: terminal-cleanup
EOF
touch -t 202001010000 "$GCROOT/.dev/forge-hygiene.ghost2.1.yml"
printf '{bad yaml\n' > "$GCROOT/.dev/forge-hygiene.ghost3.1.yml"
touch -t 202001010000 "$GCROOT/.dev/forge-hygiene.ghost3.1.yml"
printf -- '- listy\n' > "$GCROOT/.dev/forge-hygiene.ghost4.1.yml"
touch -t 202001010000 "$GCROOT/.dev/forge-hygiene.ghost4.1.yml"
o=$(cmd_hygiene_gc --days xx 2>&1); rc=$?
[ "$rc" = 1 ] && echo "$o" | grep -q 'non-negative integer' \
  && ok "T-HYG-GC invalid --days rejected before deletion" || bad "T-HYG-GC bad days: rc=$rc"
o=$(cmd_hygiene_gc --days 30 --dry-run 2>&1)
echo "$o" | grep -q 'WOULD-REMOVE .*ghost\.7' && [ -f "$GCROOT/.dev/forge-hygiene.ghost.7.yml" ] \
  && ok "T-HYG-GC dry-run marks dead terminal journal, removes nothing" || bad "T-HYG-GC dry-run wrong: $o"
o=$(cmd_hygiene_gc --days 30 2>&1)
[ ! -f "$GCROOT/.dev/forge-hygiene.ghost.7.yml" ] && [ ! -f "$GCROOT/.dev/forge-tmp/hygiene-locks/ghost.7.journal.lock" ] \
  && ok "T-HYG-GC live run removes dead terminal journal + its locks" || bad "T-HYG-GC live removal failed: $o"
[ -f "$GCROOT/.dev/forge-hygiene.live-sess.1.yml" ] && echo "$o" | grep -q 'RETAIN(live=True.*live-sess' \
  && ok "T-HYG-GC live session RETAINED" || bad "T-HYG-GC live session removed"
[ -f "$GCROOT/.dev/forge-hygiene.ghost2.1.yml" ] \
  && ok "T-HYG-GC non-terminal (terminal-cleanup) RETAINED" || bad "T-HYG-GC terminal-cleanup removed"
[ -f "$GCROOT/.dev/forge-hygiene.ghost3.1.yml" ] && echo "$o" | grep -q 'RETAIN(malformed)' \
  && ok "T-HYG-GC malformed RETAINED" || bad "T-HYG-GC malformed removed"
[ -f "$GCROOT/.dev/forge-hygiene.ghost4.1.yml" ] && echo "$o" | grep -q 'RETAIN(non-mapping)' \
  && ok "T-HYG-GC non-mapping RETAINED" || bad "T-HYG-GC non-mapping removed"

echo "── HYG §L: worker boundary locks (fds 10-13) + terminal mutex (fd 7) ──"
HYGROOT="$WORK/hygL"; mkdir -p "$HYGROOT/.dev/forge-tmp/hygiene-locks"
_resolve_project_root(){ printf '%s' "$HYGROOT"; }
# Pane 1 / non-worker is unlockable.
[ -z "$(_worker_lock_fd claude)" ] && _worker_lock claude 2>/dev/null; rc=$?
[ "$rc" = 2 ] && ok "T-HYG-RESET-PANE1 non-worker lock → rc2" || bad "T-HYG-RESET-PANE1 rc=$rc"
# Acquire + release + immediate re-acquire (fd release within one process).
_worker_lock claude-opus && _worker_unlock claude-opus && _worker_lock claude-opus \
  && ok "T-HYG-LOCK-FD-RELEASE unlock frees the fd for immediate re-acquire" \
  || bad "T-HYG-LOCK-FD-RELEASE re-acquire failed"
_worker_unlock claude-opus
# Same-worker contention serializes: a foreign holder makes _worker_lock time out busy.
LOCKF="$HYGROOT/.dev/forge-tmp/hygiene-locks/hygS.42.claude-opus.lock"
python3 - "$LOCKF" <<'PY' &
import fcntl,sys,time
f=open(sys.argv[1],'w'); fcntl.flock(f,fcntl.LOCK_EX); time.sleep(4)
PY
HOLDPID=$!
sleep 0.7
FORGE_WORKER_LOCK_WAIT_S=1 _worker_lock claude-opus 2>/dev/null; rc=$?
[ "$rc" = 1 ] && ok "T-HYG-LOCK-SERIALIZE same worker busy → rc1 within wait" || { bad "T-HYG-LOCK-SERIALIZE rc=$rc (expected busy)"; _worker_unlock claude-opus; }
# Different workers are independent while claude-opus is held externally.
FORGE_WORKER_LOCK_WAIT_S=1 _worker_lock codex-a \
  && ok "T-HYG-LOCK-INDEPENDENT different worker acquires concurrently" \
  || bad "T-HYG-LOCK-INDEPENDENT codex-a blocked by claude-opus holder"
_worker_unlock codex-a
kill "$HOLDPID" 2>/dev/null; wait "$HOLDPID" 2>/dev/null
# Held lock survives a python heredoc child (fd inheritance is load-bearing).
_worker_lock codex-b
python3 - <<'PY'
print("heredoc child ran")
PY
FORGE_WL_PROBE="$HYGROOT/.dev/forge-tmp/hygiene-locks/hygS.42.codex-b.lock" python3 - <<'PY'
import fcntl,os,sys
f=open(os.environ["FORGE_WL_PROBE"],'w')
try:
    fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB); sys.exit(1)   # acquired => lock was dropped
except OSError:
    sys.exit(0)                                               # busy => still held
PY
rc=$?
[ "$rc" = 0 ] && ok "T-HYG-LOCK held lock survives heredoc child (fd inheritance)" || bad "T-HYG-LOCK dropped across heredoc"
_worker_unlock codex-b
# _worker_send_locked never re-acquires a worker lock (no-self-deadlock, source pin).
sed -n '/^_worker_send_locked()/,/^}$/p' "$BRIDGE" | grep -q '_worker_lock' \
  && bad "T-HYG-LOCK-NO-SELF-DEADLOCK _worker_send_locked acquires a lock" \
  || ok "T-HYG-LOCK-NO-SELF-DEADLOCK _worker_send_locked is lock-free (caller owns it)"
# Terminal mutex: acquire/release + busy under a foreign holder.
_terminal_lock && _terminal_unlock && ok "T-HYG-TERMINAL-MUTEX acquire/release" || bad "T-HYG-TERMINAL-MUTEX basic acquire failed"
TLOCKF="$HYGROOT/.dev/forge-tmp/hygiene-locks/hygS.42.terminal.lock"
python3 - "$TLOCKF" <<'PY' &
import fcntl,sys,time
f=open(sys.argv[1],'w'); fcntl.flock(f,fcntl.LOCK_EX); time.sleep(3)
PY
THOLD=$!
sleep 0.5
FORGE_TERMINAL_LOCK_WAIT_S=1 _terminal_lock 2>/dev/null; rc=$?
[ "$rc" = 1 ] && ok "T-HYG-TERMINAL-MUTEX busy under a concurrent holder" || { bad "T-HYG-TERMINAL-MUTEX rc=$rc"; _terminal_unlock; }
kill "$THOLD" 2>/dev/null; wait "$THOLD" 2>/dev/null
# _hygiene_release_all is idempotent and frees everything (safe on unheld fds).
_worker_lock claude-opus; _worker_lock codex-a; _terminal_lock
_hygiene_release_all; _hygiene_release_all
_worker_lock claude-opus && _worker_lock codex-a && _terminal_lock \
  && ok "T-HYG-RELEASE-ALL releases terminal + worker locks idempotently" \
  || bad "T-HYG-RELEASE-ALL left a lock held"
_hygiene_release_all

echo "── HYG §C: crash-conservative delivery seams (real tmux, fresh root) ──"
if command -v tmux >/dev/null 2>&1; then
  HS="fbhygc-$$"
  HC="$(mkR hygc-root)"
  tmux new-session -d -s "$HS" -x 220 -y 50 -c "$HC"
  i=0; while [ "$i" -lt 4 ]; do tmux split-window -d -t "$HS:0" -c "$HC"; tmux select-layout -t "$HS:0" tiled >/dev/null 2>&1; i=$((i+1)); done
  HINC="$(tmux display-message -p -t "$HS:0.0" '#{session_created}')"
  hyg_pane_ready(){
    local pane="$1" ready="$HC/.dev/forge-tmp/hyg-pane-$1.ready" attempt=0 poll
    rm -f "$ready"
    while [ "$attempt" -lt 15 ]; do
      tmux send-keys -t "$HS:0.$pane" -l ": > \"$ready\"" 2>/dev/null || true
      tmux send-keys -t "$HS:0.$pane" Enter 2>/dev/null || true
      poll=0
      while [ "$poll" -lt 10 ]; do
        [ -f "$ready" ] && return 0
        sleep 0.1; poll=$((poll+1))
      done
      attempt=$((attempt+1))
    done
    return 1
  }
  hp=0; hyg_ready_ok=1
  while [ "$hp" -lt 5 ]; do
    hyg_pane_ready "$hp" || { bad "T-HYG-PANE-READY-$hp"; hyg_ready_ok=0; }
    hp=$((hp+1))
  done
  rm -f "$HC"/.dev/forge-tmp/hyg-pane-*.ready
  hyg_cap_has(){
    local pane="$1" needle="$2" captured
    captured="$(tmux capture-pane -p -S - -t "$HS:0.$pane" 2>/dev/null | tr -d '\n')" || return 1
    case "$captured" in *"$needle"*) return 0 ;; *) return 1 ;; esac
  }
  JHC="$HC/.dev/forge-hygiene.$HS.$HINC.yml"
  jdel(){  # jdel <worker> <field> — delivery field from the §C journal
    python3 - "$JHC" "$1" "$2" <<'PY'
import sys,yaml,os
try: d=(yaml.safe_load(open(sys.argv[1])) or {}) if os.path.exists(sys.argv[1]) else {}
except Exception: d={}
w=(d.get("workers") or {}).get(sys.argv[2]) or {}
print((w.get("delivery") or {}).get(sys.argv[3]) or "")
PY
  }
  hdec(){ ID_target_session="$HS" ID_target_incarnation="$HINC" _hygiene_decide "$HC" "$1" '' ''; }
  # Seed: a clean dispatch to codex-b proves the un-crashed path and creates the journal.
  run_in_pane "$HS:0.0" hygc-seed "( cd $HC && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS $BRIDGE dispatch --slug hygc0 --stage adhoc --worker codex-b )"
  [ "$(rc_of hygc-seed)" = 0 ] && [ "$(jdel codex-b state)" = delivered ] \
    && ok "T-HYG-CRASH seed dispatch delivers + journal records delivered" \
    || bad "T-HYG-CRASH seed: rc=$(rc_of hygc-seed) state=$(jdel codex-b state)"
  tmux send-keys -t "$HS:0.3" C-c 2>/dev/null; sleep 0.3
  run_in_pane "$HS:0.3" hygc-seed-done "( cd $HC && FORGE_WATCH_TRIGGER=0 $BRIDGE callback --slug hygc0 --stage adhoc --status DONE --worker codex-b --message d --quiet )"
  [ "$(rc_of hygc-seed-done)" = 0 ] || bad "T-HYG-CRASH seed close failed"
  # pending seam: crash after the pending log, before any journal delivery record.
  run_in_pane "$HS:0.0" hygc-pending "( cd $HC && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS FORGE_HYGIENE_TEST=1 FORGE_HYGIENE_CRASH_AT=pending $BRIDGE dispatch --slug hygc1 --stage adhoc --worker codex-a )"
  if [ "$(rc_of hygc-pending)" = 99 ] \
     && grep -q 'response: null' "$HC/.dev/proposals/hygc1/forge-log.yml" \
     && [ -z "$(jdel codex-a state)" ] \
     && [ "$(hdec codex-a)" = "RESET_UNPROVEN no-record" ] \
     && ! hyg_cap_has 2 'adhoc-hygc1.txt'; then
    ok "T-HYG-CRASH-PENDING open pending + no record + no keystroke → RESET_UNPROVEN no-record"
  else
    bad "T-HYG-CRASH-PENDING rc=$(rc_of hygc-pending) state='$(jdel codex-a state)' dec='$(hdec codex-a)'"
  fi
  tmux send-keys -t "$HS:0.2" C-c 2>/dev/null; sleep 0.3
  run_in_pane "$HS:0.2" hygc1-close "( cd $HC && FORGE_WATCH_TRIGGER=0 $BRIDGE callback --slug hygc1 --stage adhoc --status DONE --worker codex-a --message d --quiet )"
  [ "$(rc_of hygc1-close)" = 0 ] || bad "T-HYG-CRASH hygc1 close failed"
  # activity seam: attempting persisted, crash BEFORE any keystroke.
  run_in_pane "$HS:0.0" hygc-act "( cd $HC && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS FORGE_HYGIENE_TEST=1 FORGE_HYGIENE_CRASH_AT=activity $BRIDGE dispatch --slug hygc2 --stage adhoc --worker codex-a )"
  if [ "$(rc_of hygc-act)" = 99 ] \
     && [ "$(jdel codex-a state)" = attempting ] && [ "$(jdel codex-a kind)" = dispatch ] \
     && [ "$(hdec codex-a)" = "RESET_UNPROVEN attempting" ] \
     && ! hyg_cap_has 2 'adhoc-hygc2.txt'; then
    ok "T-HYG-CRASH-ACTIVITY attempting persisted, NO keystroke → RESET_UNPROVEN attempting"
  else
    bad "T-HYG-CRASH-ACTIVITY rc=$(rc_of hygc-act) state='$(jdel codex-a state)' dec='$(hdec codex-a)'"
  fi
  # Convergence: close the crashed pending, re-dispatch clean → delivered.
  tmux send-keys -t "$HS:0.2" C-c 2>/dev/null; sleep 0.3
  run_in_pane "$HS:0.2" hygc2-close "( cd $HC && FORGE_WATCH_TRIGGER=0 $BRIDGE callback --slug hygc2 --stage adhoc --status DONE --worker codex-a --message d --quiet )"
  run_in_pane "$HS:0.0" hygc2-retry "( cd $HC && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS $BRIDGE dispatch --slug hygc2b --stage adhoc --worker codex-a )"
  [ "$(rc_of hygc2-retry)" = 0 ] && [ "$(jdel codex-a state)" = delivered ] \
    && ok "T-HYG-CRASH-ACTIVITY retry converges to delivered" \
    || bad "T-HYG-CRASH-ACTIVITY retry rc=$(rc_of hygc2-retry) state=$(jdel codex-a state)"
  tmux send-keys -t "$HS:0.2" C-c 2>/dev/null; sleep 0.3
  run_in_pane "$HS:0.2" hygc2b-close "( cd $HC && FORGE_WATCH_TRIGGER=0 $BRIDGE callback --slug hygc2b --stage adhoc --status DONE --worker codex-a --message d --quiet )"
  # send-text seam: text typed, Enter never sent → stays attempting.
  run_in_pane "$HS:0.0" hygc-text "( cd $HC && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS FORGE_HYGIENE_TEST=1 FORGE_HYGIENE_CRASH_AT=send-text $BRIDGE dispatch --slug hygc3 --stage adhoc --worker codex-b )"
  if [ "$(rc_of hygc-text)" = 99 ] && [ "$(jdel codex-b state)" = attempting ] \
     && hyg_cap_has 3 'adhoc-hygc3.txt'; then
    ok "T-HYG-CRASH-SEND-TEXT text typed, no Enter → stays attempting"
  else
    bad "T-HYG-CRASH-SEND-TEXT rc=$(rc_of hygc-text) state='$(jdel codex-b state)'"
  fi
  tmux send-keys -t "$HS:0.3" C-c 2>/dev/null; sleep 0.3
  run_in_pane "$HS:0.3" hygc3-close "( cd $HC && FORGE_WATCH_TRIGGER=0 $BRIDGE callback --slug hygc3 --stage adhoc --status DONE --worker codex-b --message d --quiet )"
  # send-enter seam: Enter delivered but crash before the delivered write → attempting.
  run_in_pane "$HS:0.0" hygc-enter "( cd $HC && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS FORGE_HYGIENE_TEST=1 FORGE_HYGIENE_CRASH_AT=send-enter $BRIDGE dispatch --slug hygc4 --stage adhoc --worker codex-b )"
  [ "$(rc_of hygc-enter)" = 99 ] && [ "$(jdel codex-b state)" = attempting ] \
    && ok "T-HYG-CRASH-SEND-ENTER post-Enter pre-delivered → stays attempting (callback must confirm)" \
    || bad "T-HYG-CRASH-SEND-ENTER rc=$(rc_of hygc-enter) state='$(jdel codex-b state)'"
  tmux send-keys -t "$HS:0.3" C-c 2>/dev/null; sleep 0.3
  run_in_pane "$HS:0.3" hygc4-close "( cd $HC && FORGE_WATCH_TRIGGER=0 $BRIDGE callback --slug hygc4 --stage adhoc --status DONE --worker codex-b --message d --quiet )"
  # delivered seam: crash AFTER the delivered write — benign.
  run_in_pane "$HS:0.0" hygc-del "( cd $HC && FORGE_WATCH_TRIGGER=0 FORGE_PROMPTS_DIR=$GPROMPTS FORGE_HYGIENE_TEST=1 FORGE_HYGIENE_CRASH_AT=delivered $BRIDGE dispatch --slug hygc5 --stage adhoc --worker codex-a )"
  [ "$(rc_of hygc-del)" = 99 ] && [ "$(jdel codex-a state)" = delivered ] \
    && ok "T-HYG-CRASH-DELIVERED post-delivered crash is benign" \
    || bad "T-HYG-CRASH-DELIVERED rc=$(rc_of hygc-del) state='$(jdel codex-a state)'"
  tmux send-keys -t "$HS:0.2" C-c 2>/dev/null; sleep 0.3
  run_in_pane "$HS:0.2" hygc5-close "( cd $HC && FORGE_WATCH_TRIGGER=0 $BRIDGE callback --slug hygc5 --stage adhoc --status DONE --worker codex-a --message d --quiet )"
  # Ordinary (logged, non-force) public send: activity seam → attempting kind=send, no keystroke.
  run_in_pane "$HS:0.0" hygc-slog "( cd $HC && FORGE_WATCH_TRIGGER=0 $BRIDGE log --slug hygc6 --stage adhoc --from claude --to codex-a --prompt p )"
  run_in_pane "$HS:0.0" hygc-send "( cd $HC && FORGE_WATCH_TRIGGER=0 FORGE_HYGIENE_TEST=1 FORGE_HYGIENE_CRASH_AT=activity $BRIDGE send codex-a HYGC6_SEND_MARKER )"
  if [ "$(rc_of hygc-send)" = 99 ] && [ "$(jdel codex-a state)" = attempting ] \
     && [ "$(jdel codex-a kind)" = send ] && ! hyg_cap_has 2 HYGC6_SEND_MARKER; then
    ok "T-HYG-CRASH ordinary send activity seam → attempting kind=send, no keystroke"
  else
    bad "T-HYG-CRASH ordinary send: rc=$(rc_of hygc-send) state='$(jdel codex-a state)' kind='$(jdel codex-a kind)'"
  fi
  tmux send-keys -t "$HS:0.2" C-c 2>/dev/null; sleep 0.3
  run_in_pane "$HS:0.2" hygc6-close "( cd $HC && FORGE_WATCH_TRIGGER=0 $BRIDGE callback --slug hygc6 --stage adhoc --status DONE --worker codex-a --message d --quiet )"
  # send --force continuation: crash at send-enter → attempting kind=send-force, generation advanced.
  run_in_pane "$HS:0.0" hygc-flog "( cd $HC && FORGE_WATCH_TRIGGER=0 $BRIDGE log --slug hygc7 --stage adhoc --from claude --to codex-a --prompt p )"
  run_in_pane "$HS:0.2" hygc-fblk "( cd $HC && FORGE_WATCH_TRIGGER=0 $BRIDGE callback --slug hygc7 --stage adhoc --status BLOCKED --worker codex-a --message stuck --quiet )"
  gen_before="$(ID_target_session=$HS ID_target_incarnation=$HINC _hygiene_current_gen "$HC" codex-a)"
  run_in_pane "$HS:0.0" hygc-force "( cd $HC && FORGE_WATCH_TRIGGER=0 FORGE_HYGIENE_TEST=1 FORGE_HYGIENE_CRASH_AT=send-enter $BRIDGE send --force codex-a HYGC7_FORCE_CONTINUATION )"
  gen_after="$(ID_target_session=$HS ID_target_incarnation=$HINC _hygiene_current_gen "$HC" codex-a)"
  if [ "$(rc_of hygc-force)" = 99 ] && [ "$(jdel codex-a kind)" = send-force ] \
     && [ "$(jdel codex-a state)" = attempting ] && [ "$gen_after" != "$gen_before" ]; then
    ok "T-HYG-CRASH send --force advances generation, stays attempting at send-enter seam"
  else
    bad "T-HYG-CRASH send --force: rc=$(rc_of hygc-force) kind='$(jdel codex-a kind)' gen $gen_before→$gen_after"
  fi
  tmux send-keys -t "$HS:0.2" C-c 2>/dev/null; sleep 0.3
  run_in_pane "$HS:0.2" hygc7-close "( cd $HC && FORGE_WATCH_TRIGGER=0 $BRIDGE callback --slug hygc7 --stage adhoc --status DONE --worker codex-a --message d --quiet )"
else
  tmux kill-session -t "$HS" 2>/dev/null
  echo "  (skip HYG §C: tmux unavailable)"
fi

echo
printf 'forge-bridge: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
