#!/bin/bash
# Harness for bin/forge-start (Phase C): manual-path byte-identity (HC4 golden),
# --populate-existing validation/trap/roles, plus two real-tmux liveness proofs.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
START="$ROOT/bin/forge-start"
GOLD="$ROOT/tests/forge-start/golden-plain.log"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/fst.XXXXXX")"
RSESS="fstrap-$$"; ROLESESS="fsrole-$$"; SSTAMP="fsstamp-$$"; PRELSESS="fsprel-$$"
trap 'tmux kill-session -t "$RSESS" 2>/dev/null; tmux kill-session -t "$ROLESESS" 2>/dev/null; tmux kill-session -t "$SSTAMP" 2>/dev/null; tmux kill-session -t "$PRELSESS" 2>/dev/null; rm -rf "$WORK"' EXIT
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); printf '  ok: %s\n' "$1"; }
bad(){ FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$1"; }

# Bridge stub (line-84 call is absolute-path; PATH cannot shadow it).
FB="$WORK/fake-bridge"; printf '#!/bin/bash\nexit 0\n' > "$FB"; chmod +x "$FB"

# PATH-shadow recording tmux. has-session MUST be tunable: plain mode's auto-name
# loop needs nonzero (else it never picks forge-1); populate validation needs zero.
SHIM="$WORK/shim"; mkdir -p "$SHIM"
cat > "$SHIM/tmux" <<'SH'
#!/bin/bash
echo "$*" >> "${TMLOG:?}"
case "$1" in
  has-session) exit "${FAKE_HAS_RC:-1}" ;;
  list-panes)
    case "$*" in
      *pane_left*) if [ "${FAKE_LAYOUT:-good}" = "bad" ]; then printf '0 0\n1 5\n2 5\n3 5\n4 5\n'
                   else printf '0 0\n1 0\n2 5\n3 5\n4 5\n'; fi ;;
      *) n="${FAKE_PANES:-5}"; i=0; while [ "$i" -lt "$n" ]; do echo "$i"; i=$((i+1)); done ;;
    esac ;;
  display-message) echo "${FAKE_DISP:-/tmp}" ;;
  show-environment) [ -n "${FAKE_ENV_STAMP:-}" ] && echo "TMUX_SESSION=${FAKE_ENV_STAMP}"; exit 0 ;;
  *) exit 0 ;;
esac
SH
chmod +x "$SHIM/tmux"

echo "── T-START-IDENTITY: plain no-arg path byte-identical (HC4 golden) ──"
D="$(mktemp -d "${TMPDIR:-/tmp}/fstd.XXXXXX")"; D="$(cd "$D" && pwd)"   # macOS TMPDIR trailing slash → normalize or the sed below misses
TMLOG="$WORK/plain.log"; : > "$TMLOG"
( cd "$D" && TMLOG="$TMLOG" PATH="$SHIM:$PATH" FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" >/dev/null 2>&1 )
prc=$?
sed "s|$D|__DIR__|g" "$TMLOG" > "$WORK/plain.norm"
if diff -q "$GOLD" "$WORK/plain.norm" >/dev/null 2>&1; then
  ok "plain tmux call sequence identical to the pre-Phase-C golden"
else
  bad "plain path DRIFTED from golden:"; diff "$GOLD" "$WORK/plain.norm" | sed 's/^/    /'
fi
[ "$prc" -eq 0 ] && ok "T-START-PLAIN-EXITS-ZERO: plain run exits 0" || bad "plain run exited $prc"
grep 'send-keys' "$TMLOG" | grep -q 'FORGE_ROLE' && bad "plain launch strings carry FORGE_ROLE (byte drift)" || ok "plain launch strings unstamped"
CODEX_STATUS_OVERRIDE="-c 'tui.status_line=[\"model-with-reasoning\",\"context-remaining\",\"current-dir\"]'"
for _pane in 2 3; do
  _launch_line=$(grep "send-keys -t forge-1:.$_pane " "$TMLOG")
  _override_count=$(printf '%s\n' "$_launch_line" | grep -oF -- "$CODEX_STATUS_OVERRIDE" | wc -l | tr -d ' ')
  [ "$_override_count" = 1 ] \
    && ok "T-START-CODEX-STATUS-$_pane: override appears exactly once" \
    || bad "T-START-CODEX-STATUS-$_pane: override count=$_override_count line=$_launch_line"
  case "$_launch_line" in
    *'"context-remaining","current-dir"'*)
      ok "T-START-CODEX-ORDER-$_pane: context-remaining precedes current-dir" ;;
    *)
      bad "T-START-CODEX-ORDER-$_pane: routing field order drifted" ;;
  esac
done
rm -rf "$D"

echo "── T-START-POP-VALIDATE: populate refuses a non-1-pane session ──"
TMLOG="$WORK/val.log"; : > "$TMLOG"
out=$( TMLOG="$TMLOG" PATH="$SHIM:$PATH" FAKE_HAS_RC=0 FAKE_PANES=2 FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" --populate-existing scratch 2>&1 ); rc=$?
{ [ "$rc" -eq 2 ] && echo "$out" | grep -q 'requires a 1-pane session'; } && ok "2-pane session refused, exit 2" || bad "validate (rc=$rc): $out"
grep -q 'new-session' "$TMLOG" && bad "populate created a session" || ok "populate never runs new-session"
grep -q 'kill-session' "$TMLOG" && bad "populate ran kill-session" || ok "no kill-session on refusal"

echo "── T-START-POP-TRAP: layout failure → partial-split report, no kill, prior file kept ──"
POPROOT="$WORK/poproot"; mkdir -p "$POPROOT/.dev"
echo "prior-session" > "$POPROOT/.dev/.forge-session"
TMLOG="$WORK/trap.log"; : > "$TMLOG"
out=$( TMLOG="$TMLOG" PATH="$SHIM:$PATH" FAKE_HAS_RC=0 FAKE_PANES=1 FAKE_LAYOUT=bad FAKE_DISP="$POPROOT" FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" --populate-existing scratch 2>&1 ); rc=$?
[ "$rc" -ne 0 ] && ok "layout failure exits nonzero" || bad "layout failure exited 0"
echo "$out" | grep -q 'partial-split' && ok "trap reports partial-split state" || bad "no partial-split report: $out"
grep -q 'kill-session' "$TMLOG" && bad "trap ran kill-session" || ok "trap never kills the session"
[ "$(grep -v '^#' "$POPROOT/.dev/.forge-session" | grep -m1 .)" = "prior-session" ] && ok "prior .forge-session preserved (first non-comment line)" || bad ".forge-session clobbered"

echo "── T-START-POP-ROLES: per-pane FORGE_ROLE stamps in populate launch strings ──"
TMLOG="$WORK/roles.log"; : > "$TMLOG"
out=$( TMLOG="$TMLOG" PATH="$SHIM:$PATH" FAKE_HAS_RC=0 FAKE_PANES=1 FAKE_DISP="$POPROOT" FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" --populate-existing psess 2>&1 ); rc=$?
[ "$rc" -eq 0 ] && ok "healthy populate exits 0" || bad "populate (rc=$rc): $out"
grep 'send-keys -t psess:.1' "$TMLOG" | grep -q 'FORGE_ROLE=orchestrator claude' && ok "pane 1 stamped orchestrator" || bad "pane 1 stamp missing"
grep 'send-keys -t psess:.0' "$TMLOG" | grep -q 'FORGE_ROLE=worker claude' && ok "pane 0 stamped worker" || bad "pane 0 stamp missing"
grep 'send-keys -t psess:.4' "$TMLOG" | grep -q 'FORGE_ROLE=worker claude' && ok "pane 4 stamped worker" || bad "pane 4 stamp missing"
grep 'send-keys -t psess:.2\|send-keys -t psess:.3' "$TMLOG" | grep -q 'FORGE_ROLE' && bad "codex panes stamped (should not be)" || ok "codex panes 2/3 unstamped"
[ "$(grep -v '^#' "$POPROOT/.dev/.forge-session" | grep -m1 .)" = "psess" ] && ok "populate wrote .forge-session with the session name" || bad ".forge-session not written"
grep -q '^# advisory only' "$POPROOT/.dev/.forge-session" && ok "T-LEGACY-HEADER: writer prepends the advisory header" || bad "writer missing advisory header"
ls "$POPROOT/.dev/".forge-session.tmp.* >/dev/null 2>&1 && bad "atomic-write temp residue left behind" || ok "no .forge-session temp residue (atomic rename)"
grep -q 'set-environment -t psess TMUX_SESSION psess' "$WORK/roles.log" && ok "T-START-POP-RELAUNCH(shim): unstamped populate sets the session env stamp" || bad "unstamped populate did not set-environment"
grep -q 'respawn-pane -k -t psess:.0' "$WORK/roles.log" && ok "T-START-POP-RELAUNCH(shim): unstamped populate relaunches pane 0" || bad "unstamped populate did not respawn pane 0"

echo "── T-START-POP-RELAUNCH(shim, stamped): already-stamped populate is a no-op ──"
TMLOG="$WORK/roles2.log"; : > "$TMLOG"
out=$( TMLOG="$TMLOG" PATH="$SHIM:$PATH" FAKE_HAS_RC=0 FAKE_PANES=1 FAKE_ENV_STAMP=psess2 FAKE_DISP="$POPROOT" FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" --populate-existing psess2 2>&1 ); rc=$?
[ "$rc" -eq 0 ] && ok "stamped populate exits 0" || bad "stamped populate (rc=$rc): $out"
grep -q 'respawn-pane' "$TMLOG" && bad "stamped populate respawned pane 0 (should be no-op)" || ok "stamped populate never respawns pane 0 (idempotent)"

if command -v tmux >/dev/null 2>&1; then
  echo "── T-START-POP-TRAP-LIVE: real tmux, injected failure, session survives ──"
  DL="$WORK/livetrap"; mkdir -p "$DL/.dev"
  echo "prior-session" > "$DL/.dev/.forge-session"
  tmux new-session -d -s "$RSESS" -c "$DL"
  out=$( cd "$DL" && FORGE_BRIDGE_BIN="$FB" FORGE_START_FAIL_AFTER=3 bash "$START" --populate-existing "$RSESS" 2>&1 ); rc=$?
  [ "$rc" -ne 0 ] && ok "live injected failure exits nonzero" || bad "live failure exited 0"
  tmux has-session -t "$RSESS" 2>/dev/null && ok "session still ALIVE after failure" || bad "session was killed"
  lp=$(tmux list-panes -t "$RSESS" -F '#{pane_index}' 2>/dev/null | grep -c .)
  [ "$lp" = 1 ] && ok "panes torn back down to 1 (only this run's panes removed)" || bad "pane count after trap: $lp"
  [ "$(grep -v '^#' "$DL/.dev/.forge-session" | grep -m1 .)" = "prior-session" ] && ok "live: prior .forge-session intact" || bad "live: .forge-session touched"
  tmux kill-session -t "$RSESS" 2>/dev/null

  echo "── T-START-POP-ROLES-LIVE: FORGE_ROLE reaches the launched child process ──"
  DR="$WORK/liverole"; mkdir -p "$DR/.dev" "$WORK/rolebin" "$WORK/roles"
  for c in claude codex; do
    cat > "$WORK/rolebin/$c" <<SH
#!/bin/bash
# -pt \$TMUX_PANE: an untargeted display-message resolves to the session's
# ACTIVE pane, not the caller — panes would clobber each other's files.
idx="\$(tmux display-message -pt "\${TMUX_PANE:?}" '#{pane_index}' 2>/dev/null)"
echo "\${FORGE_ROLE:-none}" > "$WORK/roles/\$idx"
exec sleep 30
SH
    chmod +x "$WORK/rolebin/$c"
  done
  # PATH must reach the PANE shells: with a live tmux server, panes inherit the
  # SERVER env, and the pane's login zsh then REBUILDS PATH (path_helper + user
  # rc). Deterministic route: fake HOME whose rc files pin PATH stub-first,
  # injected via session env (-e) — the same mechanism spawn's birth stamps use.
  FH2="$WORK/panehome"; mkdir -p "$FH2"
  printf 'export PATH="%s:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"\n' "$WORK/rolebin" > "$FH2/.zprofile"
  cp "$FH2/.zprofile" "$FH2/.zshrc"
  ( cd "$DR" && tmux new-session -d -s "$ROLESESS" -c "$DR" -e "HOME=$FH2" -e "PATH=$WORK/rolebin:$PATH" -e "TMUX_SESSION=$ROLESESS" )
  ( cd "$DR" && FORGE_BRIDGE_BIN="$FB" bash "$START" --populate-existing "$ROLESESS" >/dev/null 2>&1 ) || true
  for _i in $(seq 1 15); do
    [ -f "$WORK/roles/0" ] && [ -f "$WORK/roles/1" ] && [ -f "$WORK/roles/4" ] && break
    sleep 1
  done
  [ "$(cat "$WORK/roles/1" 2>/dev/null)" = "orchestrator" ] && ok "pane 1 child saw FORGE_ROLE=orchestrator" || bad "pane 1 role: '$(cat "$WORK/roles/1" 2>/dev/null)'"
  { [ "$(cat "$WORK/roles/0" 2>/dev/null)" = "worker" ] && [ "$(cat "$WORK/roles/4" 2>/dev/null)" = "worker" ]; } \
    && ok "panes 0/4 children saw FORGE_ROLE=worker" || bad "worker roles: 0='$(cat "$WORK/roles/0" 2>/dev/null)' 4='$(cat "$WORK/roles/4" 2>/dev/null)'"
  tmux kill-session -t "$ROLESESS" 2>/dev/null

  echo "── T-START-STAMP-LIVE: -e birth stamp reaches a real pane child (R8) ──"
  # A real plain-mode run would type live claude/codex launch strings into panes,
  # so this mirrors the production new-session line the HC4 golden now pins
  # (new-session … -e TMUX_SESSION=<name> -e FORGE_ROOT=<dir>) and proves a real
  # child shell inherits the stamp — tmux show-environment alone is insufficient.
  DS="$WORK/livestamp"; mkdir -p "$DS"
  tmux new-session -d -s "$SSTAMP" -c "$DS" -e "TMUX_SESSION=$SSTAMP" -e "FORGE_ROOT=$DS"
  tmux send-keys -t "$SSTAMP:0.0" "printf '%s' \"\$TMUX_SESSION\" > $WORK/stamp-probe" Enter
  for _i in $(seq 1 10); do [ -s "$WORK/stamp-probe" ] && break; sleep 1; done
  [ "$(cat "$WORK/stamp-probe" 2>/dev/null)" = "$SSTAMP" ] \
    && ok "real pane child inherited TMUX_SESSION from the -e birth stamp" \
    || bad "pane child TMUX_SESSION='$(cat "$WORK/stamp-probe" 2>/dev/null)' (want $SSTAMP)"
  tmux kill-session -t "$SSTAMP" 2>/dev/null

  echo "── T-START-POP-RELAUNCH-LIVE: unstamped populate stamps + relaunches pane 0 ──"
  DP="$WORK/liverelaunch"; mkdir -p "$DP/.dev"
  ( cd "$DP" && tmux new-session -d -s "$PRELSESS" -c "$DP" -e "HOME=$FH2" -e "PATH=$WORK/rolebin:$PATH" )
  ( cd "$DP" && FORGE_BRIDGE_BIN="$FB" bash "$START" --populate-existing "$PRELSESS" >/dev/null 2>&1 ) || true
  _stamp_live="$(tmux show-environment -t "$PRELSESS" TMUX_SESSION 2>/dev/null | sed -n 's/^TMUX_SESSION=//p')"
  [ "$_stamp_live" = "$PRELSESS" ] && ok "unstamped populate installed the session env stamp" || bad "populate stamp missing: '$_stamp_live'"
  tmux has-session -t "$PRELSESS" 2>/dev/null && ok "session survived the pane-0 relaunch" || bad "session died during relaunch"
  _plp=$(tmux list-panes -t "$PRELSESS" -F '#{pane_index}' 2>/dev/null | grep -c .)
  [ "$_plp" = 5 ] && ok "populate completed the 5-pane split after relaunch" || bad "pane count after relaunch populate: $_plp"
  tmux kill-session -t "$PRELSESS" 2>/dev/null
else
  echo "  (skip live blocks: no tmux)"
fi

echo
echo "═══════════════════════════════════════"
printf 'PASS: %d\nFAIL: %d\n' "$PASS" "$FAIL"
echo "═══════════════════════════════════════"
[ "$FAIL" -eq 0 ]
