#!/bin/bash
# Harness for bin/forge-start: --here compatibility and auto-worktree goldens,
# provisioning/broker atomicity, populate validation/trap/roles, and live proofs.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
START="$ROOT/bin/forge-start"
GOLD="$ROOT/tests/forge-start/golden-plain.log"
# Physicalize immediately: macOS $TMPDIR is /var/folders/... symlinked to
# /private/var/folders/.... forge-start resolves PHYSICAL_ROOT with `pwd -P`, so
# an unresolved WORK makes the --root arguments un-normalizable by sed.
WORK="$(mktemp -d "${TMPDIR:-/tmp}/fst.XXXXXX")"; WORK="$(cd "$WORK" && pwd -P)"
RSESS="fstrap-$$"; ROLESESS="fsrole-$$"; SSTAMP="fsstamp-$$"; PRELSESS="fsprel-$$"
trap 'tmux kill-session -t "$RSESS" 2>/dev/null; tmux kill-session -t "$ROLESESS" 2>/dev/null; tmux kill-session -t "$SSTAMP" 2>/dev/null; tmux kill-session -t "$PRELSESS" 2>/dev/null; rm -rf "$WORK"' EXIT
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); printf '  ok: %s\n' "$1"; }
bad(){ FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$1"; }

# Bridge stub (line-84 call is absolute-path; PATH cannot shadow it).
FB="$WORK/fake-bridge"; printf '#!/bin/bash\nexit 0\n' > "$FB"; chmod +x "$FB"
# C6: --here and --populate-existing now invoke `forge root-assets
# ensure-dev-exclusion` before any tmux state. Stub it and log the call.
# $HOME/bin/forge covers every case that does not set FORGE_BIN explicitly.
RALOG="$WORK/root-assets.calls"; : > "$RALOG"; export RALOG
mkdir -p "$WORK/h/bin"
RA="$WORK/h/bin/forge"
cat > "$RA" <<'SH'
#!/bin/bash
echo "$*" >> "${RALOG:?}"
[ "${RA_FAIL:-0}" = 1 ] && { echo "forge: stub refusal" >&2; exit 1; }
exit 0
SH
chmod +x "$RA"
DOCTORCALLS="$WORK/codex-doctor.calls"; : > "$DOCTORCALLS"
FORGE_CODEX_DOCTOR_BIN="$WORK/fake-codex-doctor"; export FORGE_CODEX_DOCTOR_BIN DOCTORCALLS
cat > "$FORGE_CODEX_DOCTOR_BIN" <<'SH'
#!/bin/bash
echo "$*" >> "${DOCTORCALLS:?}"
echo 'launch_ready=true'
SH
chmod +x "$FORGE_CODEX_DOCTOR_BIN"
FORGE_BROKER_BIN="$WORK/fake-broker"; export FORGE_BROKER_BIN
BRKLOG="$WORK/broker.log"; : > "$BRKLOG"
printf '#!/bin/bash\necho "$*" >> "%s"\nexit 0\n' "$BRKLOG" > "$FORGE_BROKER_BIN"; chmod +x "$FORGE_BROKER_BIN"

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
      *pane_left*) if [ "${FAKE_LAYOUT:-good}" = "bad" ]; then printf '0 0 0 199 200\n1 0 20 99 200\n2 0 35 99 200\n3 100 20 100 200\n4 100 36 100 200\n'
                   else printf '0 0 0 200 200\n1 0 20 99 200\n2 0 35 99 200\n3 100 20 100 200\n4 100 35 100 200\n'; fi ;;
      *) n="${FAKE_PANES:-5}"; i=0; while [ "$i" -lt "$n" ]; do echo "$i"; i=$((i+1)); done ;;
    esac ;;
  display-message) echo "${FAKE_DISP:-/tmp}" ;;
  show-environment) [ -n "${FAKE_ENV_STAMP:-}" ] && echo "TMUX_SESSION=${FAKE_ENV_STAMP}"; exit 0 ;;
  *) exit 0 ;;
esac
SH
chmod +x "$SHIM/tmux"

echo "── T-START-IDENTITY: plain no-arg path byte-identical (HC4 golden) ──"
D="$(mktemp -d "${TMPDIR:-/tmp}/fstd.XXXXXX")"; D="$(cd "$D" && pwd -P)"   # normalize /var → /private/var so physical-root launch golden matches
git -C "$D" init -q
TMLOG="$WORK/plain.log"; : > "$TMLOG"
: > "$RALOG"
( cd "$D" && TMLOG="$TMLOG" PATH="$SHIM:$PATH" FORGE_BRIDGE_BIN="$FB" FORGE_BIN="$RA" HOME="$WORK/h" bash "$START" --here >/dev/null 2>&1 )
prc=$?
sed "s|$D|__DIR__|g" "$TMLOG" > "$WORK/plain.norm"
if diff -q "$GOLD" "$WORK/plain.norm" >/dev/null 2>&1; then
  ok "T-START-HERE-GOLDEN: --here matches the post-hoist compatibility golden"
else
  bad "plain path DRIFTED from golden:"; diff "$GOLD" "$WORK/plain.norm" | sed 's/^/    /'
fi
if [ "${REGEN:-0}" = 1 ]; then cp "$WORK/plain.norm" "$GOLD"; echo "  (golden REGENERATED)"; fi
[ "$prc" -eq 0 ] && ok "T-START-PLAIN-EXITS-ZERO: plain run exits 0" || bad "plain run exited $prc"
grep 'send-keys' "$TMLOG" | grep -q 'FORGE_ROLE' && bad "plain launch strings carry FORGE_ROLE (byte drift)" || ok "plain launch strings unstamped"
CODEX_STATUS_OVERRIDE="forge codex-launch"
grep -q 'forge codex-launch.*--root.*--session.*--pane 3.*--effort xhigh' "$TMLOG" && ok "Codex A uses centralized attested launch" || bad "Codex A launch contract missing"
grep -q 'forge codex-launch.*--pane 4.*--effort medium' "$TMLOG" && ok "Codex B uses centralized attested launch" || bad "Codex B launch contract missing"
for _pane in 3 4; do
  _launch_line=$(grep "send-keys -t forge-1:.$_pane " "$TMLOG")
  _override_count=$(printf '%s\n' "$_launch_line" | grep -oF -- "$CODEX_STATUS_OVERRIDE" | wc -l | tr -d ' ')
  [ "$_override_count" = 1 ] \
    && ok "T-START-CODEX-STATUS-$_pane: override appears exactly once" \
    || bad "T-START-CODEX-STATUS-$_pane: override count=$_override_count line=$_launch_line"
  case "$_launch_line" in
    *"--root "*"--session "*"--pane $_pane "*"--effort "*)
      ok "T-START-CODEX-ORDER-$_pane: root/session/pane/effort order is stable" ;;
    *)
      bad "T-START-CODEX-ORDER-$_pane: routing field order drifted" ;;
  esac
done
# C6 replaces the historical "--here never invokes FORGE_BIN" contract: --here
# must now install the toolkit-owned /.dev/ exclusion, because it never calls
# `worktree ensure` and an old root started only this way would never migrate.
# The tmux golden above is unchanged; only the invocation contract moved.
grep -qx "root-assets ensure-dev-exclusion --root $D" "$RALOG" \
  && ok "T-START-HERE-C6: --here installs the .dev/ exclusion" \
  || bad "T-START-HERE-C6: --here did not invoke root-assets (log: $(cat "$RALOG"))"
rm -rf "$D"

echo "── provisioning and broker atomicity ──"
STARTSRC="$WORK/start-source"; WTD="$WORK/fixed-worktree"; mkdir -p "$STARTSRC/.dev" "$WTD/.dev"; git -C "$STARTSRC" init -q; git -C "$WTD" init -q
FORGECALLS="$WORK/forge.calls"; : > "$FORGECALLS"; WF="$WORK/fake-forge"; printf '#!/bin/bash\necho "$*" >> "%s"\necho "%s"\n' "$FORGECALLS" "$WTD" > "$WF"; chmod +x "$WF"
TMLOG="$WORK/wt.log"; : > "$TMLOG"; : > "$DOCTORCALLS"
( cd "$STARTSRC" && TMLOG="$TMLOG" PATH="$SHIM:$PATH" FORGE_BIN="$WF" FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" worktree >/dev/null 2>&1 ); rc=$?
sed "s|$WTD|__WT__|g" "$TMLOG" > "$WORK/wt.norm"
[ "$rc" = 0 ] && diff -q "$ROOT/tests/forge-start/golden-worktree.log" "$WORK/wt.norm" >/dev/null \
  && ok "T-START-WT-GOLDEN: all cwd/root args use provisioned path" || bad "worktree golden drift"
grep -qF "worktree ensure --session worktree --from $STARTSRC --print-path" "$FORGECALLS" && ! grep -qF "$STARTSRC" "$TMLOG" && grep -qF -- "--root $WTD" "$BRKLOG" && ! grep -qF "$STARTSRC" "$BRKLOG" \
  && ok "T-START-WT-ROOT-COLLAPSE" || bad "source root leaked past provisioning"
grep -qxF "codex-doctor $WTD" "$DOCTORCALLS" \
  && ok "T-START-CODEX-PREFLIGHT-ROOT: doctor checks the provisioned root" \
  || bad "Codex doctor did not check provisioned root: $(cat "$DOCTORCALLS")"

# A fail-closed Codex version/readiness refusal must occur before new-session,
# broker start, any split, or any CLI launch. The read-only explicit-name
# has-session probe is allowed and creates no tmux state.
BADDOCTOR="$WORK/bad-codex-doctor"
cat > "$BADDOCTOR" <<'SH'
#!/bin/bash
cat <<'EOF'
codex_version=9.9.9
supported_interactive_versions=0.147.0
version_supported=false
launch_ready=false
EOF
SH
chmod +x "$BADDOCTOR"
TMLOG="$WORK/codex-preflight-fail.log"; : > "$TMLOG"; : > "$BRKLOG"
out=$(cd "$WTD" && TMLOG="$TMLOG" PATH="$SHIM:$PATH" FORGE_CODEX_DOCTOR_BIN="$BADDOCTOR" FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" --here unsupported 2>&1); rc=$?
[ "$rc" = 3 ] && echo "$out" | grep -q 'Codex readiness preflight failed before tmux layout' \
  && echo "$out" | grep -q '^codex_version=9.9.9$' \
  && ! grep -qE '^(new-session|split-window|send-keys|kill-session) ' "$TMLOG" \
  && [ ! -s "$BRKLOG" ] \
  && ok "T-START-CODEX-PREFLIGHT-ATOMIC: unsupported Codex leaves zero tmux/broker state" \
  || bad "Codex preflight was not atomic (rc=$rc tmux=$(cat "$TMLOG") broker=$(cat "$BRKLOG") out=$out)"

# One chronological stream proves broker ownership is established after the
# incarnation read and before the first split; separate logs cannot prove this.
ORDERBROKER="$WORK/order-broker"; printf '#!/bin/bash\necho "BROKER $*" >> "$TMLOG"\n' > "$ORDERBROKER"; chmod +x "$ORDERBROKER"
TMLOG="$WORK/order.log"; : > "$TMLOG"
( cd "$STARTSRC" && TMLOG="$TMLOG" PATH="$SHIM:$PATH" FORGE_BIN="$WF" FORGE_BROKER_BIN="$ORDERBROKER" FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" ordered >/dev/null 2>&1 ); rc=$?
python3 - "$TMLOG" <<'PY' && ok "T-START-BROKER-ORDER" || bad "broker ordering"
import sys
lines=open(sys.argv[1]).read().splitlines()
pos=lambda needle: next(i for i,x in enumerate(lines) if needle in x)
assert pos("new-session") < pos("display-message") < pos("BROKER start") < pos("split-window")
PY

TMLOG="$WORK/provfail.log"; : > "$TMLOG"
( cd "$STARTSRC" && TMLOG="$TMLOG" PATH="$SHIM:$PATH" FORGE_START_PROVISION_FAIL=1 FORGE_BIN="$WF" HOME="$WORK/h" bash "$START" pf >/dev/null 2>&1 ); rc=$?
# The contract is "zero tmux STATE", not "zero tmux calls": for an explicit name
# forge-start probes `has-session` BEFORE provisioning (by design — it is line 1
# of golden-worktree.log, and it replaces the auto-name loop's probe rather than
# adding a second HC4 call). A read-only probe creates nothing; assert that no
# state-creating call was made instead of asserting an empty log.
[ "$rc" != 0 ] && ! grep -q '^new-session' "$TMLOG" && ok "T-START-PROVISION-FAIL-ATOMIC" || bad "provision failure touched tmux"
group_ok=1; for erc in 2 4; do
  EF="$WORK/forge-$erc"; printf '#!/bin/bash\nexit %s\n' "$erc" > "$EF"; chmod +x "$EF"; TMLOG="$WORK/pass-$erc.log"; : > "$TMLOG"
  ( cd "$STARTSRC" && TMLOG="$TMLOG" PATH="$SHIM:$PATH" FORGE_BIN="$EF" HOME="$WORK/h" bash "$START" "p$erc" >/dev/null 2>&1 ); rc=$?
  [ "$rc" = "$erc" ] && ! grep -q '^new-session' "$TMLOG" || group_ok=0
done
[ "$group_ok" = 1 ] && ok "T-START-EXIT-PASSTHROUGH" || bad "exit passthrough"
TMLOG="$WORK/dup.log"; : > "$TMLOG"; out=$(cd "$STARTSRC" && TMLOG="$TMLOG" PATH="$SHIM:$PATH" FAKE_HAS_RC=0 FORGE_BIN="$WORK/never-forge" bash "$START" dupe 2>&1); rc=$?
[ "$rc" = 2 ] && echo "$out" | grep -q 'attach with' && ! grep -q 'new-session' "$TMLOG" && ok "T-START-DUP-SESSION" || bad "duplicate session"

BF="$WORK/fail-broker"; printf '#!/bin/bash\nexit 1\n' > "$BF"; chmod +x "$BF"
echo prior > "$WTD/.dev/.forge-session"; TMLOG="$WORK/bfplain.log"; : > "$TMLOG"
out=$(cd "$WTD" && TMLOG="$TMLOG" PATH="$SHIM:$PATH" FAKE_PANES=1 FORGE_BROKER_BIN="$BF" FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" --here broken 2>&1); rc=$?
[ "$rc" = 3 ] && [ "$(grep -c '^new-session ' "$TMLOG")" = 1 ] && [ "$(grep -c '^kill-session ' "$TMLOG")" = 1 ] && grep -q 'kill-session' "$TMLOG" && ! grep -q 'split-window\|send-keys' "$TMLOG" && [ "$(cat "$WTD/.dev/.forge-session")" = prior ] \
  && ok "T-START-BROKERFAIL-PLAIN" || bad "plain broker failure"
TMLOG="$WORK/bfpop.log"; : > "$TMLOG"
out=$(TMLOG="$TMLOG" PATH="$SHIM:$PATH" FAKE_HAS_RC=0 FAKE_PANES=1 FAKE_DISP="$WTD" FORGE_BROKER_BIN="$BF" FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" --populate-existing popfail 2>&1); rc=$?
[ "$rc" != 0 ] && ! grep -q 'kill-session\|split-window' "$TMLOG" && echo "$out" | grep -q 'broker start failed' && ! echo "$out" | grep -q 'failed mid-layout' \
  && ok "T-START-BROKERFAIL-POP" || bad "populate broker failure"
TMLOG="$WORK/noprov.log"; : > "$TMLOG"
TMLOG="$TMLOG" PATH="$SHIM:$PATH" FAKE_HAS_RC=0 FAKE_PANES=1 FAKE_DISP="$WTD" FORGE_BIN="$RA" FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" --populate-existing noprov >/dev/null 2>&1
[ $? = 0 ] && ok "T-START-POPULATE-NO-PROVISION" || bad "populate invoked provisioning"

echo "── T-START-POP-VALIDATE: populate refuses a non-1-pane session ──"
TMLOG="$WORK/val.log"; : > "$TMLOG"
out=$( TMLOG="$TMLOG" PATH="$SHIM:$PATH" FAKE_HAS_RC=0 FAKE_PANES=2 FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" --populate-existing scratch 2>&1 ); rc=$?
{ [ "$rc" -eq 2 ] && echo "$out" | grep -q 'requires a 1-pane session'; } && ok "2-pane session refused, exit 2" || bad "validate (rc=$rc): $out"
grep -q 'new-session' "$TMLOG" && bad "populate created a session" || ok "populate never runs new-session"
grep -q 'kill-session' "$TMLOG" && bad "populate ran kill-session" || ok "no kill-session on refusal"

echo "── T-START-PLAIN-LAYOUTFAIL: plain layout failure stops broker before kill (R7) ──"
D2="$(mktemp -d "${TMPDIR:-/tmp}/fstd.XXXXXX")"; D2="$(cd "$D2" && pwd -P)"
git -C "$D2" init -q
TMLOG="$WORK/plainfail.log"; : > "$TMLOG"; : > "$BRKLOG"
( cd "$D2" && TMLOG="$TMLOG" PATH="$SHIM:$PATH" FAKE_LAYOUT=bad FORGE_BRIDGE_BIN="$FB" FORGE_BIN="$RA" HOME="$WORK/h" bash "$START" --here >/dev/null 2>&1 )
rc=$?
[ "$rc" -ne 0 ] && ok "plain layout failure exits nonzero" || bad "plain layout failure exited 0"
sed -n '1p' "$BRKLOG" | grep -q '^start --root ' && ok "broker start recorded before the failure" || bad "broker start missing from log: $(cat "$BRKLOG")"
sed -n '2p' "$BRKLOG" | grep -q '^stop --root .* --timeout 5$' && ok "layout failure stopped the broker before kill-session" || bad "no broker stop on plain layout failure: $(cat "$BRKLOG")"
grep -q 'kill-session' "$TMLOG" && ok "plain layout failure still kills the session" || bad "kill-session missing from plain layout failure"
if grep -qE 'set-environment .* FORGE_LAYOUT|set-option -p .* @forge-worker' "$TMLOG"; then
  bad "T-STAMP-NONE plain failure wrote layout receipts"
else
  ok "T-STAMP-NONE plain failure wrote no layout receipts"
fi
rm -rf "$D2"

echo "── T-START-POP-TRAP: layout failure → partial-split report, no kill, prior file kept ──"
POPROOT="$WORK/poproot"; mkdir -p "$POPROOT/.dev"; git -C "$POPROOT" init -q
echo "prior-session" > "$POPROOT/.dev/.forge-session"
TMLOG="$WORK/trap.log"; : > "$TMLOG"; : > "$BRKLOG"
out=$( TMLOG="$TMLOG" PATH="$SHIM:$PATH" FAKE_HAS_RC=0 FAKE_PANES=1 FAKE_LAYOUT=bad FAKE_DISP="$POPROOT" FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" --populate-existing scratch 2>&1 ); rc=$?
[ "$rc" -ne 0 ] && ok "layout failure exits nonzero" || bad "layout failure exited 0"
echo "$out" | grep -q 'partial-split' && ok "trap reports partial-split state" || bad "no partial-split report: $out"
grep -q 'kill-session' "$TMLOG" && bad "trap ran kill-session" || ok "trap never kills the session"
grep -q '^stop ' "$BRKLOG" && bad "populate layout failure stopped the broker (session survives — broker must too)" || ok "populate layout failure leaves the broker running (R8)"
if grep -qE 'set-environment .* FORGE_LAYOUT|set-option -p .* @forge-worker' "$TMLOG"; then
  bad "T-STAMP-NONE populate failure wrote layout receipts"
else
  ok "T-STAMP-NONE populate failure wrote no layout receipts"
fi
[ "$(grep -v '^#' "$POPROOT/.dev/.forge-session" | grep -m1 .)" = "prior-session" ] && ok "prior .forge-session preserved (first non-comment line)" || bad ".forge-session clobbered"

echo "── T-START-POP-ROLES: per-pane FORGE_ROLE stamps in populate launch strings ──"
TMLOG="$WORK/roles.log"; : > "$TMLOG"
out=$( TMLOG="$TMLOG" PATH="$SHIM:$PATH" FAKE_HAS_RC=0 FAKE_PANES=1 FAKE_DISP="$POPROOT" FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" --populate-existing psess 2>&1 ); rc=$?
[ "$rc" -eq 0 ] && ok "healthy populate exits 0" || bad "populate (rc=$rc): $out"
grep 'send-keys -t psess:.0' "$TMLOG" | grep -q 'FORGE_ROLE=orchestrator claude' && ok "pane 0 stamped orchestrator" || bad "pane 0 stamp missing"
grep 'send-keys -t psess:.1' "$TMLOG" | grep -q 'FORGE_ROLE=worker claude' && ok "pane 1 stamped worker" || bad "pane 1 stamp missing"
grep 'send-keys -t psess:.2' "$TMLOG" | grep -q 'FORGE_ROLE=worker claude' && ok "pane 2 stamped worker" || bad "pane 2 stamp missing"
grep 'send-keys -t psess:.3\|send-keys -t psess:.4' "$TMLOG" | grep -q 'FORGE_ROLE' && bad "codex panes stamped (should not be)" || ok "codex panes 3/4 unstamped"
grep -q 'set-environment -t psess FORGE_LAYOUT 2' "$TMLOG" && ok "T-STAMP-OK session generation receipt written" || bad "T-STAMP-OK session receipt missing"
for _receipt in ':.0 @forge-worker claude' ':.1 @forge-worker claude-opus' ':.2 @forge-worker claude-sonnet' ':.3 @forge-worker codex-a' ':.4 @forge-worker codex-b'; do
  grep -q "set-option -p -t psess$_receipt" "$TMLOG" || bad "T-STAMP-OK missing $_receipt"
done
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
  DL="$WORK/livetrap"; mkdir -p "$DL/.dev"; git -C "$DL" init -q
  echo "prior-session" > "$DL/.dev/.forge-session"
  tmux new-session -d -s "$RSESS" -c "$DL"
  out=$( cd "$DL" && FORGE_BRIDGE_BIN="$FB" FORGE_BIN="$ROOT/bin/forge" FORGE_START_FAIL_AFTER=3 bash "$START" --populate-existing "$RSESS" 2>&1 ); rc=$?
  [ "$rc" -ne 0 ] && ok "live injected failure exits nonzero" || bad "live failure exited 0"
  tmux has-session -t "$RSESS" 2>/dev/null && ok "session still ALIVE after failure" || bad "session was killed"
  lp=$(tmux list-panes -t "$RSESS" -F '#{pane_index}' 2>/dev/null | grep -c .)
  [ "$lp" = 1 ] && ok "panes torn back down to 1 (only this run's panes removed)" || bad "pane count after trap: $lp"
  [ "$(grep -v '^#' "$DL/.dev/.forge-session" | grep -m1 .)" = "prior-session" ] && ok "live: prior .forge-session intact" || bad "live: .forge-session touched"
  tmux kill-session -t "$RSESS" 2>/dev/null

  echo "── T-START-POP-ROLES-LIVE: FORGE_ROLE reaches the launched child process ──"
  DR="$WORK/liverole"; mkdir -p "$DR/.dev" "$WORK/rolebin" "$WORK/roles"; git -C "$DR" init -q
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
  ( cd "$DR" && FORGE_BRIDGE_BIN="$FB" FORGE_BIN="$ROOT/bin/forge" bash "$START" --populate-existing "$ROLESESS" >/dev/null 2>&1 ) || true
  for _i in $(seq 1 15); do
    [ -f "$WORK/roles/0" ] && [ -f "$WORK/roles/1" ] && [ -f "$WORK/roles/2" ] && break
    sleep 1
  done
  _geo=$(tmux list-panes -t "$ROLESESS" -F '#{pane_index} #{pane_left} #{pane_top} #{pane_width} #{window_width}')
  printf '%s\n' "$_geo" | awk '{L[$1]=$2;T[$1]=$3;W[$1]=$4;WW=$5} END {exit !(L[0]==0 && T[0]==0 && W[0]==WW && L[1]==0 && L[2]==0 && L[3]>0 && L[3]==L[4] && T[1]==T[3] && T[2]==T[4] && T[0]<T[1] && T[1]<T[2])}' \
    && ok "T-GEO real tmux banner over aligned 2x2 grid" || bad "T-GEO wrong: $_geo"
  [ "$(cat "$WORK/roles/0" 2>/dev/null)" = "orchestrator" ] && ok "pane 0 child saw FORGE_ROLE=orchestrator" || bad "pane 0 role: '$(cat "$WORK/roles/0" 2>/dev/null)'"
  { [ "$(cat "$WORK/roles/1" 2>/dev/null)" = "worker" ] && [ "$(cat "$WORK/roles/2" 2>/dev/null)" = "worker" ]; } \
    && ok "panes 1/2 children saw FORGE_ROLE=worker" || bad "worker roles: 1='$(cat "$WORK/roles/1" 2>/dev/null)' 2='$(cat "$WORK/roles/2" 2>/dev/null)'"
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
  DP="$WORK/liverelaunch"; mkdir -p "$DP/.dev"; git -C "$DP" init -q
  ( cd "$DP" && tmux new-session -d -s "$PRELSESS" -c "$DP" -e "HOME=$FH2" -e "PATH=$WORK/rolebin:$PATH" )
  ( cd "$DP" && FORGE_BRIDGE_BIN="$FB" FORGE_BIN="$ROOT/bin/forge" bash "$START" --populate-existing "$PRELSESS" >/dev/null 2>&1 ) || true
  _stamp_live="$(tmux show-environment -t "$PRELSESS" TMUX_SESSION 2>/dev/null | sed -n 's/^TMUX_SESSION=//p')"
  [ "$_stamp_live" = "$PRELSESS" ] && ok "unstamped populate installed the session env stamp" || bad "populate stamp missing: '$_stamp_live'"
  tmux has-session -t "$PRELSESS" 2>/dev/null && ok "session survived the pane-0 relaunch" || bad "session died during relaunch"
  _plp=$(tmux list-panes -t "$PRELSESS" -F '#{pane_index}' 2>/dev/null | grep -c .)
  [ "$_plp" = 5 ] && ok "populate completed the 5-pane split after relaunch" || bad "pane count after relaunch populate: $_plp"
  tmux kill-session -t "$PRELSESS" 2>/dev/null
else
  echo "  (skip live blocks: no tmux)"
fi

echo "── T-DEV-MIGRATE: --here / populate install the .dev exclusion (C6) ──"
# These two paths never call `worktree ensure`, so an old root started only this
# way would never migrate. The step must run BEFORE any tmux state exists.

# V16a: the env form, not just the literal flag.
MIG="$WORK/mig-env"; mkdir -p "$MIG/.dev"; git -C "$MIG" init -q; MIG="$(cd "$MIG" && pwd -P)"
TMLOG="$WORK/mig1.log"; : > "$TMLOG"; : > "$RALOG"
( cd "$MIG" && TMLOG="$TMLOG" PATH="$SHIM:$PATH" FORGE_START_WORKTREE=0 FORGE_BIN="$RA" \
    FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" migenv >/dev/null 2>&1 )
grep -qx "root-assets ensure-dev-exclusion --root $MIG" "$RALOG" \
  && ok 'T-DEV-V16 FORGE_START_WORKTREE=0 installs the exclusion' || bad "T-DEV-V16 env form skipped it (log: $(cat "$RALOG"))"

# V16b: refusal leaves NO tmux state — not one new-session, not one split.
MIG2="$WORK/mig-refuse"; mkdir -p "$MIG2/.dev"; git -C "$MIG2" init -q; MIG2="$(cd "$MIG2" && pwd -P)"
TMLOG="$WORK/mig2.log"; : > "$TMLOG"; : > "$RALOG"
out=$( cd "$MIG2" && TMLOG="$TMLOG" PATH="$SHIM:$PATH" RA_FAIL=1 FORGE_BIN="$RA" \
    FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" --here migref 2>&1 ); rc=$?
{ [ "$rc" = 3 ] && ! grep -qE '^(new-session|split-window|send-keys) ' "$TMLOG"; } \
  && ok 'T-DEV-V16 refusal precedes any tmux state' || bad "T-DEV-V16 rc=$rc tmux log: $(cat "$TMLOG")"
printf '%s' "$out" | grep -q 'exclusion could not be established' \
  && ok 'T-DEV-V16 refusal names the cause' || bad "T-DEV-V16 diagnostic: $out"

# V17: --populate-existing is a migration entry point too (cmd_spawn re-registers
# first, but a direct invocation does not). Pane 0 must survive the refusal.
TMLOG="$WORK/mig3.log"; : > "$TMLOG"; : > "$RALOG"
out=$( TMLOG="$TMLOG" PATH="$SHIM:$PATH" FAKE_HAS_RC=0 FAKE_PANES=1 FAKE_DISP="$WTD" RA_FAIL=1 \
    FORGE_BIN="$RA" FORGE_BRIDGE_BIN="$FB" HOME="$WORK/h" bash "$START" --populate-existing migpop 2>&1 ); rc=$?
[ "$rc" != 0 ] && ok 'T-DEV-V17 populate refuses when the exclusion fails' || bad "T-DEV-V17 rc=$rc"
! grep -q '^kill-session ' "$TMLOG" && ok 'T-DEV-V17 populate refusal never kills the session' || bad 'T-DEV-V17 killed the session'
! grep -qE '^(split-window|send-keys) ' "$TMLOG" && ok 'T-DEV-V17 no panes added before the refusal' || bad "T-DEV-V17 split before refusal: $(cat "$TMLOG")"

echo
echo "═══════════════════════════════════════"
printf 'PASS: %d\nFAIL: %d\n' "$PASS" "$FAIL"
echo "═══════════════════════════════════════"
[ "$FAIL" -eq 0 ]
