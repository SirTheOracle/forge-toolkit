#!/bin/bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; SYNC="$ROOT/bin/forge-sync-main"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/fsm.XXXXXX")"; WORK="$(cd "$WORK" && pwd -P)"; trap 'rm -rf "$WORK"' EXIT
export HOME="$WORK/home"; mkdir -p "$HOME"
export GIT_CONFIG_NOSYSTEM=1 GIT_AUTHOR_NAME=T GIT_AUTHOR_EMAIL=t@e GIT_COMMITTER_NAME=T GIT_COMMITTER_EMAIL=t@e
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok: $1"; }; bad(){ FAIL=$((FAIL+1)); echo "  FAIL: $1"; }
has(){ case "$2" in *"$1"*) return 0 ;; *) return 1 ;; esac; }

# A bare origin plus a clone, both on $2 (default main). Every case gets its own
# tree so a refusal in one cannot leak state into the next.
repo(){
  NAME="$1"; BR="${2:-main}"; ORIGIN="$WORK/$NAME.git"; SRC="$WORK/$NAME"
  git init -q --bare -b "$BR" "$ORIGIN"
  git init -q -b "$BR" "$SRC"; git -C "$SRC" remote add origin "$ORIGIN"
  echo base > "$SRC/README"; git -C "$SRC" add README; git -C "$SRC" commit -qm base
  git -C "$SRC" push -qu origin "$BR"; git -C "$SRC" remote set-head origin -a >/dev/null 2>&1
}
# Advance the remote by one commit, without touching the clone.
remote_commit(){
  UP="$WORK/up-$RANDOM"; git clone -q "$1" "$UP"
  echo "$RANDOM" > "$UP/added"; git -C "$UP" add added; git -C "$UP" commit -qm "$2"
  git -C "$UP" push -q origin HEAD; rm -rf "$UP"
}

echo "== forge-sync-main =="

# ── in sync ──────────────────────────────────────────────────────────────────
repo insync
OUT="$("$SYNC" -C "$WORK/insync" 2>&1)"; RC=$?
[ "$RC" = 0 ] && ok "in-sync repo exits 0" || bad "in-sync repo exits 0 (rc=$RC)"
has "In sync: main == origin/main" "$OUT" && ok "in-sync repo says so" || bad "in-sync repo says so: $OUT"
has "Worktrees vs origin/main" "$OUT" && ok "drift table is printed" || bad "drift table is printed"

OUT="$("$SYNC" --check -C "$WORK/insync" 2>&1)"; RC=$?
[ "$RC" = 0 ] && ok "--check on an in-sync repo exits 0" || bad "--check on an in-sync repo exits 0 (rc=$RC)"

# ── behind: fast-forward ─────────────────────────────────────────────────────
repo behind; remote_commit "$WORK/behind.git" "remote work"
BEFORE="$(git -C "$WORK/behind" rev-parse main)"
OUT="$("$SYNC" -C "$WORK/behind" 2>&1)"; RC=$?
[ "$RC" = 0 ] && ok "behind repo exits 0" || bad "behind repo exits 0 (rc=$RC)"
has "Fast-forwarded main" "$OUT" && ok "behind repo reports the fast-forward" || bad "behind repo reports the fast-forward: $OUT"
[ "$(git -C "$WORK/behind" rev-parse main)" = "$(git -C "$WORK/behind" rev-parse origin/main)" ] \
  && ok "behind repo ends identical to origin/main" || bad "behind repo ends identical to origin/main"
[ "$(git -C "$WORK/behind" rev-parse main)" != "$BEFORE" ] && ok "the ref actually moved" || bad "the ref actually moved"
# A checked-out trunk must be updated with the working tree, not by moving the ref
# out from under it — a bare update-ref would leave `added` staged for deletion.
[ -f "$WORK/behind/added" ] && ok "the working tree carries the incoming file" || bad "the working tree carries the incoming file"
[ -z "$(git -C "$WORK/behind" status --porcelain=v1)" ] && ok "the trunk worktree is left clean" || bad "the trunk worktree is left clean"

# ── behind under --check: report, change nothing ─────────────────────────────
repo chk; remote_commit "$WORK/chk.git" "remote work"
BEFORE="$(git -C "$WORK/chk" rev-parse main)"
OUT="$("$SYNC" --check -C "$WORK/chk" 2>&1)"; RC=$?
[ "$RC" = 3 ] && ok "--check on a behind repo exits 3" || bad "--check on a behind repo exits 3 (rc=$RC)"
has "DRIFT" "$OUT" && ok "--check names the drift" || bad "--check names the drift: $OUT"
[ "$(git -C "$WORK/chk" rev-parse main)" = "$BEFORE" ] && ok "--check moved no ref" || bad "--check moved no ref"
# --check must fetch: comparing against a stale remote-tracking ref would report
# "in sync" for exactly the drift it exists to catch. Prove it by moving the
# remote and never fetching in the test itself.
repo stale; remote_commit "$WORK/stale.git" "remote work"
# --no-fetch runs FIRST and on its own repo: any earlier fetching invocation would
# have refreshed origin/main on disk and made this case indistinguishable.
OUT="$("$SYNC" --check --no-fetch -C "$WORK/stale" 2>&1)"; RC=$?
[ "$RC" = 0 ] && ok "--no-fetch compares against the refs on disk" || bad "--no-fetch compares against the refs on disk (rc=$RC)"
OUT="$("$SYNC" --check -C "$WORK/stale" 2>&1)"; RC=$?
[ "$RC" = 3 ] && ok "--check fetches, so it sees an unfetched remote commit" || bad "--check fetches, so it sees an unfetched remote commit (rc=$RC)"

# ── ahead: refuse ────────────────────────────────────────────────────────────
repo ahead
echo direct > "$WORK/ahead/direct"; git -C "$WORK/ahead" add direct
git -C "$WORK/ahead" commit -qm "direct commit that skipped review"
BEFORE="$(git -C "$WORK/ahead" rev-parse main)"
OUT="$("$SYNC" -C "$WORK/ahead" 2>&1)"; RC=$?
[ "$RC" = 3 ] && ok "ahead repo exits 3" || bad "ahead repo exits 3 (rc=$RC)"
has "REFUSED" "$OUT" && ok "ahead repo refuses" || bad "ahead repo refuses: $OUT"
has "never went through a pull request" "$OUT" && ok "the refusal names the cause" || bad "the refusal names the cause"
has "direct commit that skipped review" "$OUT" && ok "the refusal lists the offending commit" || bad "the refusal lists the offending commit"
has "gh pr create" "$OUT" && ok "the refusal prints a routing remedy" || bad "the refusal prints a routing remedy"
has "Nothing was changed" "$OUT" && ok "the refusal states it changed nothing" || bad "the refusal states it changed nothing"
[ "$(git -C "$WORK/ahead" rev-parse main)" = "$BEFORE" ] && ok "the refusal moved no ref" || bad "the refusal moved no ref"

# ── ahead AND behind: still refuse, never merge or rebase ────────────────────
repo both; remote_commit "$WORK/both.git" "remote work"
echo direct > "$WORK/both/direct"; git -C "$WORK/both" add direct; git -C "$WORK/both" commit -qm "local direct"
BEFORE="$(git -C "$WORK/both" rev-parse main)"
OUT="$("$SYNC" -C "$WORK/both" 2>&1)"; RC=$?
[ "$RC" = 3 ] && ok "diverged repo exits 3" || bad "diverged repo exits 3 (rc=$RC)"
[ "$(git -C "$WORK/both" rev-parse main)" = "$BEFORE" ] && ok "diverged repo is left untouched" || bad "diverged repo is left untouched"
[ "$(git -C "$WORK/both" rev-list --count main)" = 2 ] && ok "no merge commit was fabricated" || bad "no merge commit was fabricated"

# ── dirty trunk worktree: refuse rather than clobber ─────────────────────────
repo dirty; remote_commit "$WORK/dirty.git" "remote work"
echo scratch >> "$WORK/dirty/README"
BEFORE="$(git -C "$WORK/dirty" rev-parse main)"
OUT="$("$SYNC" -C "$WORK/dirty" 2>&1)"; RC=$?
[ "$RC" = 3 ] && ok "dirty trunk exits 3" || bad "dirty trunk exits 3 (rc=$RC)"
has "uncommitted changes" "$OUT" && ok "dirty trunk refusal names the reason" || bad "dirty trunk refusal names the reason: $OUT"
[ "$(git -C "$WORK/dirty" rev-parse main)" = "$BEFORE" ] && ok "dirty trunk moved no ref" || bad "dirty trunk moved no ref"
has scratch "$(cat "$WORK/dirty/README")" && ok "dirty trunk kept the local edit" || bad "dirty trunk kept the local edit"
# Untracked files alone are not a reason to refuse: every forge root carries some.
repo untracked; remote_commit "$WORK/untracked.git" "remote work"
echo junk > "$WORK/untracked/scratch.txt"
"$SYNC" -C "$WORK/untracked" >/dev/null 2>&1; RC=$?
[ "$RC" = 0 ] && ok "untracked files do not block the sync" || bad "untracked files do not block the sync (rc=$RC)"

# ── trunk checked out nowhere: move the ref directly ─────────────────────────
repo detach; remote_commit "$WORK/detach.git" "remote work"
git -C "$WORK/detach" checkout -q -b side
OUT="$("$SYNC" -C "$WORK/detach" 2>&1)"; RC=$?
[ "$RC" = 0 ] && ok "unchecked-out trunk exits 0" || bad "unchecked-out trunk exits 0 (rc=$RC)"
[ "$(git -C "$WORK/detach" rev-parse main)" = "$(git -C "$WORK/detach" rev-parse origin/main)" ] \
  && ok "unchecked-out trunk is fast-forwarded" || bad "unchecked-out trunk is fast-forwarded"
[ "$(git -C "$WORK/detach" rev-parse --abbrev-ref HEAD)" = side ] && ok "the current branch is not switched" || bad "the current branch is not switched"

# ── drift table: linked worktrees and the unpushed-trunk flag ────────────────
repo table
git -C "$WORK/table" worktree add -q -b feat "$WORK/table-feat" >/dev/null 2>&1
echo f > "$WORK/table-feat/f"; git -C "$WORK/table-feat" add f; git -C "$WORK/table-feat" commit -qm feat
OUT="$("$SYNC" -C "$WORK/table" 2>&1)"
has "table-feat" "$OUT" && ok "the table lists linked worktrees" || bad "the table lists linked worktrees: $OUT"
has "feat" "$OUT" && ok "the table names each branch" || bad "the table names each branch"
has "UNPUSHED-TRUNK" "$OUT" && bad "a feature branch ahead is not flagged as unpushed trunk" || ok "a feature branch ahead is not flagged as unpushed trunk"

# ── non-main trunk, taken from the remote's own HEAD ─────────────────────────
repo master master; remote_commit "$WORK/master.git" "remote work"
OUT="$("$SYNC" -C "$WORK/master" 2>&1)"; RC=$?
[ "$RC" = 0 ] && ok "a master-trunk repo exits 0" || bad "a master-trunk repo exits 0 (rc=$RC)"
has "origin/master" "$OUT" && ok "the trunk name comes from the remote HEAD" || bad "the trunk name comes from the remote HEAD: $OUT"

# ── argument and environment errors are loud ────────────────────────────────
OUT="$("$SYNC" -C "$WORK" 2>&1)"; RC=$?
[ "$RC" = 1 ] && ok "a non-repository exits 1" || bad "a non-repository exits 1 (rc=$RC)"
OUT="$("$SYNC" --nope -C "$WORK/insync" 2>&1)"; RC=$?
[ "$RC" = 1 ] && ok "an unknown flag exits 1" || bad "an unknown flag exits 1 (rc=$RC)"
has "unknown argument" "$OUT" && ok "an unknown flag says which" || bad "an unknown flag says which"
git init -q -b main "$WORK/noremote"
OUT="$("$SYNC" -C "$WORK/noremote" 2>&1)"; RC=$?
[ "$RC" = 1 ] && ok "a repo with no remote exits 1" || bad "a repo with no remote exits 1 (rc=$RC)"
OUT="$("$SYNC" --help 2>&1)"; RC=$?
[ "$RC" = 0 ] && has "forge-sync-main" "$OUT" && ok "--help prints usage" || bad "--help prints usage"

echo "═══════════════════════════════════════"
echo "PASS: $PASS"; echo "FAIL: $FAIL"
echo "═══════════════════════════════════════"
[ "$FAIL" = 0 ]
