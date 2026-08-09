#!/bin/bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; FORGE="$ROOT/bin/forge"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/fwt.XXXXXX")"; WORK="$(cd "$WORK" && pwd -P)"; trap 'rm -rf "$WORK"' EXIT
# Physicalize WORK immediately: on macOS $TMPDIR is /var/folders/... which is a
# symlink to /private/var/folders/.... The engine resolves roots physically (by
# design — see the physical-parent rule), so every expected path built from an
# unresolved $WORK would mismatch the value the engine prints.
# PyYAML lives in the REAL user-site and Python derives that directory from HOME,
# so pin it via PYTHONPATH before overriding HOME — otherwise every forge YAML
# read AND every in-suite `python3 -c` assertion dies with ModuleNotFoundError.
# Same trap documented at tests/forge-cc/spawn.sh:10-16; exported globally here
# because this suite's own assertions need it, not just the forge subprocesses.
USERSITE="$(python3 -c 'import site; print(site.getusersitepackages())' 2>/dev/null || true)"
export HOME="$WORK/home" PYTHONPATH="$USERSITE" FORGE_WATCH_TRIGGER=0 FORGE_REGISTRY_FILE="$WORK/registry.yml"
export FORGE_WATCH_ROOTS_FILE="$WORK/watch-roots" FORGE_TMUX_LIST="$WORK/tmux.tsv"
mkdir -p "$HOME"; : > "$FORGE_WATCH_ROOTS_FILE"; : > "$FORGE_TMUX_LIST"
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok: $1"; }; bad(){ FAIL=$((FAIL+1)); echo "  FAIL: $1"; }
DIRENV="$WORK/direnv"; export FORGE_DIRENV_BIN="$DIRENV" DIRENV_LOG="$WORK/direnv.log"
printf '#!/bin/bash\necho "$*" >> "$DIRENV_LOG"\n[ "$1" != status ] || echo "Found RC allowed ${DIRENV_ALLOWED:-1}"\nexit "${DIRENV_RC:-0}"\n' > "$DIRENV"; chmod +x "$DIRENV"
repo(){
  SRC="$WORK/$1-src"; PARENT="$WORK/$1-wt"; ORIGIN="$WORK/$1.git"; mkdir -p "$SRC/.claude" "$SRC/.codex" "$PARENT"
  git -C "$SRC" init -q -b main; git -C "$SRC" config user.email t@e; git -C "$SRC" config user.name T
  printf '.dev/\n' > "$SRC/.gitignore"; echo base > "$SRC/README"; git -C "$SRC" add .gitignore README; git -C "$SRC" commit -qm base
  git init -q --bare "$ORIGIN"; git -C "$SRC" remote add origin "$ORIGIN"; git -C "$SRC" push -qu origin main
  git --git-dir="$ORIGIN" symbolic-ref HEAD refs/heads/main; git -C "$SRC" remote set-head origin -a >/dev/null
  printf 'forge:\n  worktree: {parent: "%s", prefix: proj, fetch: false}\n' "$PARENT" > "$SRC/.claude/forge-project.yml"
  printf '{"permissions":{"allow":["Bash(*)"]},"hooks":{"Stop":[{"hooks":[{"type":"command","command":"stale forge-cc-hook"}]}]}}\n' > "$SRC/.claude/settings.json"
  printf '{"permissions":{"allow":["Bash(*)"]}}\n' > "$SRC/.claude/settings.local.json"
  python3 -c 'import json,sys; json.dump({"hooks":json.load(open(sys.argv[1]))},open(sys.argv[2],"w"))' "$ROOT/config/codex-cc-hooks.json" "$SRC/.codex/hooks.json"
  echo 'export X=1' > "$SRC/.envrc"
  unset FORGE_WORKTREE_PARENT; WT="$PARENT/proj-s1"; : > "$DIRENV_LOG"
}
ensure(){ "$FORGE" worktree ensure --session "${1:-s1}" --from "$SRC" "${@:2}"; }
nw(){ git -C "$SRC" worktree list --porcelain | grep -c '^worktree ' || true; }
cfgpy(){ FORGE_CFG_EXPR="$1" FORGE_CFG="$SRC/.claude/forge-project.yml" python3 - <<'PY'
import os, yaml
fp=os.environ["FORGE_CFG"]
with open(fp) as f: data=yaml.safe_load(f) or {}
forge=data.setdefault("forge", {}); wt=forge.setdefault("worktree", {})
exec(os.environ["FORGE_CFG_EXPR"], {"os":os, "data":data, "forge":forge, "wt":wt})
with open(fp,"w") as f: yaml.safe_dump(data,f,sort_keys=False)
with open(fp) as f: assert isinstance(yaml.safe_load(f), dict)
PY
}

repo create; out=$(ensure s1 --print-path 2>"$WORK/create.err"); rc=$?
[ "$rc" = 0 ] && [ "$out" = "$WT" ] && [ "$(printf '%s\n' "$out" | wc -l | tr -d ' ')" = 1 ] && [ "$(git -C "$WT" symbolic-ref --short HEAD)" = forge/s1 ] && [ "$(git -C "$WT" rev-parse HEAD)" = "$(git -C "$SRC" rev-parse origin/main)" ] && ok 'T-WT-CREATE / T-WT-STDOUT-EXACT' || bad create
echo keep >> "$WT/README"; git -C "$WT" commit -qam keep; out2=$(ensure s1 --print-path 2>"$WORK/reuse.err")
[ "$out2" = "$WT" ] && [ "$(nw)" = 2 ] && [ "$(git -C "$WT" log -1 --format=%s)" = keep ] && ok 'T-WT-IDEMPOTENT' || bad reattach
python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));assert d["schema"]=="cc-worktree/1" and d["session"]=="s1" and "source_ahead" in d' "$WT/.dev/forge-worktree.json" && ok 'T-WT-PROVENANCE' || bad provenance
[ "$("$FORGE" worktree path --session x --from "$SRC")" = "$PARENT/proj-x" ] && ok 'T-WT-PATH' || bad path
before=$(nw); group_ok=1; for n in 'bad name' '../x' . .. -x; do ensure "$n" >/dev/null 2>&1; [ $? = 2 ] || group_ok=0; done; [ "$group_ok" = 1 ] && [ "$(nw)" = "$before" ] && ok 'T-WT-NAME-GUARD' || bad name-guard
ensure dry --dry-run --print-path >/dev/null 2>&1; [ $? = 2 ] && ok 'T-WT-DRYRUN-EXCLUSIVE' || bad dry-exclusive
before=$(nw); plan=$(ensure dry --dry-run 2>/dev/null); [ $? = 0 ] && [ "$(nw)" = "$before" ] && echo "$plan" | grep -q 'PLAN worktree=' && ok 'T-WT-DRYRUN' || bad dry-run
group_ok=1; for argv in 'path --print-path' 'path --dry-run' 'path --allow-dirty-base' 'ensure --bogus'; do
  before=$(nw); out=$("$FORGE" worktree $argv --session flags --from "$SRC" 2>/dev/null); rc=$?
  [ "$rc" = 2 ] && [ -z "$out" ] && [ "$(nw)" = "$before" ] && [ ! -e "$PARENT/proj-flags" ] || group_ok=0
done; [ "$group_ok" = 1 ] && ok 'T-WT-FLAG-MATRIX' || bad flag-matrix
repo malformed; printf 'forge: [broken\n' > "$SRC/.claude/forge-project.yml"; before=$(nw); ensure s1 >/dev/null 2>&1; [ $? = 2 ] && [ "$(nw)" = "$before" ] && ok 'T-WT-MALFORMED-CONFIG' || bad malformed-config
group_ok=1
for spec in 'root:list' 'forge:list' 'worktree:list' 'parent:list' 'prefix:int' 'branch_prefix:bool' 'base_ref:list' 'forge_base_ref:list' 'expected_root:int' 'fetch:string' 'seed:string' 'mode:list' 'required:string'; do
  repo "type-${spec%%:*}-$RANDOM"; kind=${spec#*:}
  case "$spec" in
    root:*) printf '%s\n' '- nope' > "$SRC/.claude/forge-project.yml" ;;
    forge:*) printf 'forge: []\n' > "$SRC/.claude/forge-project.yml" ;;
    worktree:*) printf 'forge: {worktree: []}\n' > "$SRC/.claude/forge-project.yml" ;;
    parent:*) cfgpy 'wt["parent"]=[]' ;;
    prefix:*) cfgpy 'wt["prefix"]=7' ;;
    branch_prefix:*) cfgpy 'wt["branch_prefix"]=True' ;;
    base_ref:*) cfgpy 'wt["base_ref"]=[]' ;;
    forge_base_ref:*) cfgpy 'forge["base_ref"]=[]' ;;
    expected_root:*) cfgpy 'forge["expected_root"]=7' ;;
    fetch:*) cfgpy 'wt["fetch"]="false"' ;;
    seed:*) cfgpy 'wt["seed"]="x"' ;;
    mode:*) cfgpy 'wt["seed"]=[{"path":"x","mode":[]}]' ;;
    required:*) cfgpy 'wt["seed"]=[{"path":"x","required":"yes"}]' ;;
  esac
  before=$(nw); ensure s1 >/dev/null 2>&1; [ $? = 2 ] && [ "$(nw)" = "$before" ] || group_ok=0
done
[ "$group_ok" = 1 ] && ok 'T-WT-CONFIG-TYPES' || bad config-types
repo emptyparent; cfgpy 'wt["parent"]=""'; before=$(nw); ensure s1 >/dev/null 2>&1; [ $? = 2 ] && [ "$(nw)" = "$before" ] && ok 'T-WT-PARENT-NONEMPTY' || bad parent-empty
group_ok=1; for bad_prefix in '' . .. a/b 'a\b'; do repo "prefix-$RANDOM"; BAD_PREFIX="$bad_prefix" cfgpy 'wt["prefix"]=os.environ["BAD_PREFIX"]'; before=$(nw); ensure s1 >/dev/null 2>&1; [ $? = 2 ] && [ "$(nw)" = "$before" ] || group_ok=0; done; [ "$group_ok" = 1 ] && ok 'T-WT-PREFIX-SAFE' || bad prefix-safe
repo badbranch; cfgpy 'wt["branch_prefix"]="bad.."'; before=$(nw); ensure s1 >/dev/null 2>&1; [ $? = 2 ] && [ "$(nw)" = "$before" ] && ok 'T-WT-BRANCH-NAME-VALIDATION' || bad branch-name
repo unknown; cfgpy 'wt["future_key"]={"nested":True}'; ensure s1 --print-path >/dev/null 2>&1; [ $? = 0 ] && ok 'T-WT-UNKNOWN-KEY-IGNORED' || bad unknown-key
repo physical; REALP="$WORK/real-parent"; LINKP="$WORK/link-parent"; mkdir -p "$REALP"; ln -s "$REALP" "$LINKP"; LINKP="$LINKP" cfgpy 'wt["parent"]=os.environ["LINKP"]'; out=$(ensure s1 --print-path 2>/dev/null); [ "$out" = "$REALP/proj-s1" ] && ok 'T-WT-PARENT-PHYSICAL' || bad physical-parent
repo mkparent; NEWP="$WORK/not-created/a/b"; NEWP="$NEWP" cfgpy 'wt["parent"]=os.environ["NEWP"]'; ensure s1 --print-path >/dev/null 2>&1; [ $? = 0 ] && [ -d "$NEWP" ] && ok 'T-WT-PARENT-CREATE' || bad parent-create
repo parentfail; printf x > "$WORK/parent-file"; BADP="$WORK/parent-file/child"; BADP="$BADP" cfgpy 'wt["parent"]=os.environ["BADP"]'; ensure s1 >/dev/null 2>"$WORK/parentfail.err"; [ $? = 4 ] && grep -q 'could not create worktree parent' "$WORK/parentfail.err" && ok 'T-WT-PARENT-FAIL' || bad parent-fail
repo conflict
mkdir -p "$PARENT/proj-empty"; ensure empty >/dev/null 2>&1; [ $? = 4 ] && [ -z "$(find "$PARENT/proj-empty" -mindepth 1 -print -quit)" ] && ok 'T-WT-DIR-CONFLICT-EMPTY' || bad empty-conflict
mkdir -p "$PARENT/proj-full"; echo x > "$PARENT/proj-full/x"; ensure full >/dev/null 2>&1; [ $? = 4 ] && [ -f "$PARENT/proj-full/x" ] && ok 'T-WT-DIR-CONFLICT-NONEMPTY' || bad full-conflict
repo missing; rm "$SRC/.claude/forge-project.yml"; before=$(nw); ensure x >/dev/null 2>&1; [ $? = 2 ] && [ "$(nw)" = "$before" ] && ok 'T-WT-NO-PROJECT-YML' || bad missing-yml

repo branch; git -C "$SRC" branch forge/free origin/main; ensure free --print-path >/dev/null 2>"$WORK/free.err"; [ $? = 0 ] && grep -q 'the cut is its tip' "$WORK/free.err" && ok 'T-WT-BRANCH-FREE' || bad free-branch
git -C "$SRC" branch forge/busy origin/main; git -C "$SRC" worktree add "$WORK/owner" forge/busy >/dev/null; ensure busy >/dev/null 2>"$WORK/busy.err"; [ $? = 4 ] && grep -q "$WORK/owner" "$WORK/busy.err" && ok 'T-WT-BRANCH-INUSE' || bad busy-branch
repo base; git -C "$SRC" branch explicit origin/main; cfgpy 'wt["base_ref"]="explicit"'; ensure explicit --print-path >/dev/null 2>&1; [ "$(git -C "$PARENT/proj-explicit" rev-parse HEAD)" = "$(git -C "$SRC" rev-parse explicit)" ] && ok 'T-WT-BASE-CHAIN explicit' || bad explicit-base
# Removing origin is NOT enough to reach the HEAD rung: the ladder's last rung is
# the LOCAL `main` branch, which the fixture still has. Rename it away so every
# named rung misses and the warned HEAD fallback is actually exercised.
repo ladder; ensure ohead --print-path >/dev/null 2>&1; git -C "$SRC" remote set-head origin -d; ensure omain --print-path >/dev/null 2>&1; git -C "$SRC" remote remove origin; git -C "$SRC" branch -m main trunk; ensure local --print-path >/dev/null 2>"$WORK/head.err"; grep -q 'falling back to HEAD' "$WORK/head.err" && ok 'T-WT-BASE-CHAIN remaining ladder' || bad base-ladder
repo fetchfail; cfgpy 'wt["fetch"]=True'; git -C "$SRC" remote set-url origin "$WORK/does-not-exist.git"; ensure s1 --print-path >/dev/null 2>"$WORK/fetch.err"; grep -Eq 'fetch origin failed; using local origin/main @ [0-9a-f]+ \(committer date 20[0-9]{2}-' "$WORK/fetch.err" && ok 'T-WT-FETCH-FAIL-DATE' || bad fetch-failure-date
repo fetchfresh; cfgpy 'wt["fetch"]=True'; CLONE="$WORK/fetch-clone"; git clone -q "$ORIGIN" "$CLONE"; git -C "$CLONE" config user.email t@e; git -C "$CLONE" config user.name T; echo remote >> "$CLONE/README"; git -C "$CLONE" commit -qam remote; git -C "$CLONE" push -q origin main; REMOTE_SHA=$(git -C "$CLONE" rev-parse HEAD); echo local >> "$SRC/README"; git -C "$SRC" commit -qam local; out=$(ensure s1 --print-path 2>/dev/null); python3 - "$out/.dev/forge-worktree.json" "$REMOTE_SHA" <<'PY' && ok 'T-WT-FETCH-FRESH / POST-FETCH-AHEAD' || bad fetch-fresh
import json,sys
d=json.load(open(sys.argv[1])); assert d["base_sha"]==sys.argv[2] and d["source_ahead"]==1
PY
[ "$(git -C "$out" rev-parse HEAD)" = "$REMOTE_SHA" ] || bad fetch-cut-sha

repo seed; mkdir -p "$SRC/.claude/skills/foo" "$SRC/.claude/commands"; echo s > "$SRC/.claude/skills/foo/SKILL.md"; echo c > "$SRC/.claude/commands/bar.md"; ensure s1 --print-path >/dev/null 2>&1
[ -f "$WT/.claude/skills/foo/SKILL.md" ] && [ -f "$WT/.claude/commands/bar.md" ] && [ -f "$WT/.envrc" ] && [ -L "$WT/.codex/hooks.json" ] && [ -f "$WT/.claude/settings.local.json" ] && [ -z "$(git -C "$WT" status --porcelain -uno)" ] && ok 'T-WT-SEED-DEPTH1 / T-WT-SEED-ENVRC / T-WT-SEED-UNTRACKED-OK / T-WT-SEED-SKIP-PRESENT' || bad implicit-seeds
python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));assert d["permissions"] and "stale forge-cc-hook" not in open(sys.argv[1]).read()' "$WT/.claude/settings.json" && ok 'T-WT-SEED-SETTINGS' || bad canonical-merge
python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));assert d["codex_trust"]=="symlinked"' "$WT/.dev/forge-worktree.json" && ok 'T-WT-CODEX-SYMLINK' || bad codex-symlink
repo codexfresh; printf '{"hooks":{}}\n' > "$SRC/.codex/hooks.json"; ensure s1 --print-path >/dev/null 2>"$WORK/codexfresh.err"; [ ! -L "$WT/.codex/hooks.json" ] && python3 -c 'import json,sys;assert json.load(open(sys.argv[1]))["codex_trust"]=="unverified"' "$WT/.dev/forge-worktree.json" && grep -q unverified "$WORK/codexfresh.err" && ok 'T-WT-CODEX-FRESHER' || bad codex-fresher
repo codexcopy; cfgpy 'wt["seed"]=[{"path":".codex/hooks.json","mode":"copy","required":True}]'; ensure s1 --print-path >/dev/null 2>"$WORK/codexcopy.err"; [ ! -L "$WT/.codex/hooks.json" ] && python3 -c 'import json,sys;assert json.load(open(sys.argv[1]))["codex_trust"]=="unverified"' "$WT/.dev/forge-worktree.json" && grep -q 'declared copy mode' "$WORK/codexcopy.err" && ok 'T-WT-CODEX-DECLARED-COPY' || bad codex-declared-copy
repo posttrip; git -C "$SRC" add .claude/settings.json; git -C "$SRC" commit -qm tracked-settings; git -C "$SRC" push -q origin main; ensure s1 >/dev/null 2>"$WORK/posttrip.err"; [ $? = 4 ] && grep -q 'after canonical bootstrap' "$WORK/posttrip.err" && grep -q '.claude/settings.json' "$WORK/posttrip.err" && ok 'T-WT-POST-BOOTSTRAP-TRIPWIRE' || bad post-bootstrap-tripwire
repo expected; printf '# keep\nforge:\n  expected_root: "%s" # inline\n  worktree: {parent: "%s", prefix: proj, fetch: false}\n' "$SRC" "$PARENT" > "$SRC/.claude/forge-project.yml"; ensure s1 --print-path >/dev/null 2>&1; grep -q "# keep" "$WT/.claude/forge-project.yml" && grep -q "expected_root: \"$WT\" # inline" "$WT/.claude/forge-project.yml" && ok 'T-WT-EXPECTED-ROOT-REWRITE' || bad rewrite
repo passthru; cp "$SRC/.claude/forge-project.yml" "$WORK/orig"; ensure s1 --print-path >/dev/null 2>&1; cmp -s "$WORK/orig" "$WT/.claude/forge-project.yml" && ok 'T-WT-EXPECTED-ROOT-PASSTHRU' || bad passthru
repo refuse; FORGE_CFG_SOURCE="$SRC" cfgpy 'forge["expected_root"]=os.environ["FORGE_CFG_SOURCE"]'; git -C "$SRC" add .claude/forge-project.yml; git -C "$SRC" commit -qm cfg; ensure s1 >/dev/null 2>"$WORK/refuse.err"; [ $? = 2 ] && grep -q 'delete.*expected_root.*set it to' "$WORK/refuse.err" && ok 'T-WT-EXPECTED-ROOT-REFUSE' || bad expected-refuse
repo modes; mkdir -p "$SRC/backend/.venv"; echo env > "$SRC/backend/.env"; cfgpy 'wt["seed"]=[{"path":"backend/.venv","mode":"symlink","required":True},{"path":"backend/.env","mode":"copy","required":True}]'; ensure s1 --print-path >/dev/null 2>&1; [ -L "$WT/backend/.venv" ] && [ -f "$WT/backend/.env" ] && [ ! -L "$WT/backend/.env" ] && ok 'T-WT-SEED-MODES' || bad modes
repo required; cfgpy 'wt["seed"]=[{"path":"missing","required":True}]'; ensure s1 >/dev/null 2>&1; [ $? = 4 ] && ok 'T-WT-SEED-REQUIRED true' || bad required
repo optional; cfgpy 'wt["seed"]=[{"path":"missing","required":False}]'; ensure s1 --print-path >/dev/null 2>"$WORK/optional.err"; [ $? = 0 ] && grep -q optional "$WORK/optional.err" && ok 'T-WT-SEED-REQUIRED false' || bad optional
group_ok=1; for q in /abs ../x .git/x .dev/x 'back\\slash'; do repo "u-$RANDOM"; export FORGE_SEED_PATH="$q"; cfgpy 'wt["seed"]=[{"path":os.environ["FORGE_SEED_PATH"]}]'; before=$(nw); ensure s1 >/dev/null 2>&1; [ $? = 2 ] && [ "$(nw)" = "$before" ] || group_ok=0; done; unset FORGE_SEED_PATH; [ "$group_ok" = 1 ] && ok 'T-WT-SEED-MODES unsafe' || bad unsafe-seeds
# Two shapes, one scan: --dry-run enumerates every unseeded path; a real provision
# emits ONE grouped summary line. Assert both, so a future change cannot silently
# restore the 74-line flood or drop the advisory altogether.
repo advisory; mkdir -p "$SRC/backend/.venv"; echo 'backend/.venv/' >> "$SRC/.gitignore"; git -C "$SRC" commit -qam ignore-venv
ensure s1 --dry-run >/dev/null 2>"$WORK/advisory-verbose.err"; grep -q 'backend/.venv.*not seeded' "$WORK/advisory-verbose.err" && ok 'T-WT-ADVISORY-SCAN verbose' || bad advisory-verbose
ensure s1 --print-path >/dev/null 2>"$WORK/advisory.err"; grep -qE 'gitignored path\(s\) not seeded \(.*backend' "$WORK/advisory.err" && [ "$(grep -c 'not seeded' "$WORK/advisory.err")" = 1 ] && ok 'T-WT-ADVISORY-SCAN summary' || bad advisory

# NO-OWNER branch: FORGE_TMUX_LIST is empty here, so no session owns SRC and the
# message offers `commit`. The no-stash assertion is scoped to THIS branch only —
# the live-owner branch below deliberately contains the word in a prohibition.
# See §1.1: do not "fix" the owner message to satisfy a global no-stash grep.
repo dirty; echo x >> "$SRC/README"; before=$(nw); ensure s1 >/dev/null 2>"$WORK/dirty.err"; [ $? = 2 ] && [ "$(nw)" = "$before" ] && grep -q README "$WORK/dirty.err" && ! grep -q stash "$WORK/dirty.err" && ok 'T-WT-DIRTY-REFUSE' || bad dirty-refuse
# LIVE-OWNER branch: must name the session, offer ONLY --allow-dirty-base, and
# WARN against stashing. Asserting the warning is present stops a future edit
# from deleting it to satisfy a misread of manual gate 8.
printf 'owner\t%s\n' "$SRC" > "$FORGE_TMUX_LIST"; ensure owned >/dev/null 2>"$WORK/owner.err"; [ $? = 2 ] && grep -q "Session 'owner'.*--allow-dirty-base" "$WORK/owner.err" && grep -q 'do not stash' "$WORK/owner.err" && ! grep -q 'commit the work' "$WORK/owner.err" && ok 'T-WT-DIRTY-OWNER' || bad dirty-owner; : > "$FORGE_TMUX_LIST"
TMUXPROD="$WORK/tmux-prod"; printf '#!/bin/bash\nprintf "prod-owner\\t%%s\\n" "$FORGE_TMUX_ROOT"\n' > "$TMUXPROD"; chmod +x "$TMUXPROD"; FORGE_TMUX_LIST= FORGE_TMUX_BIN="$TMUXPROD" FORGE_TMUX_ROOT="$SRC" ensure prod >/dev/null 2>"$WORK/prod-owner.err"; [ $? = 2 ] && grep -q "Session 'prod-owner'" "$WORK/prod-owner.err" && ok 'T-WT-DIRTY-OWNER-PRODUCTION' || bad production-owner
repo generated; mkdir -p "$SRC/frontend/public"; echo base > "$SRC/frontend/public/.wp-blog-cache.json"; git -C "$SRC" add frontend/public/.wp-blog-cache.json; git -C "$SRC" commit -qm generated; echo changed >> "$SRC/frontend/public/.wp-blog-cache.json"; ensure s1 >/dev/null 2>"$WORK/generated.err"; [ $? = 2 ] && grep -q 'tracked file your build rewrites should be gitignored' "$WORK/generated.err" && ok 'T-WT-MACHINE-GENERATED-REMEDY' || bad generated-remedy
repo dirtyoverride; echo x >> "$SRC/README"
out=$(ensure allowed --allow-dirty-base --print-path 2>/dev/null); python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));assert d["allow_dirty_base"] and d["tracked_dirty"]' "$out/.dev/forge-worktree.json" && [ -f "$out/.dev/attention/spawn-allowed-worktree-dirty-source.json" ] && ok 'T-WT-DIRTY-OVERRIDE' || bad dirty-override
ensure allowed --print-path >/dev/null 2>"$WORK/rd.err"; [ $? = 0 ] && grep -q reattaching "$WORK/rd.err" && ok 'T-WT-DIRTY-REATTACH' || bad dirty-reattach
repo untracked; echo x > "$SRC/scratch"; out=$(ensure s1 --print-path 2>"$WORK/untracked.err"); [ $? = 0 ] && grep -q untracked "$WORK/untracked.err" && [ ! -f "$out/.dev/attention/spawn-s1-worktree-dirty-source.json" ] && ok 'T-WT-UNTRACKED-OK' || bad untracked
repo ahead; git -C "$SRC" checkout -qb local; for n in 1 2; do echo "$n" >> "$SRC/README"; git -C "$SRC" commit -qam "a$n"; done; out=$(ensure s1 --print-path 2>/dev/null); python3 -c 'import json,sys;assert json.load(open(sys.argv[1]))["source_ahead"]==2' "$out/.dev/forge-worktree.json" && ok 'T-WT-DIVERGENCE' || bad ahead
repo shared; printf 'schema: cc-registry/1\nrepos: {id: {alias: source, path: "%s", repo_id: id}}\n' "$SRC" > "$FORGE_REGISTRY_FILE"; echo sentinel > "$FORGE_WATCH_ROOTS_FILE"; cp "$FORGE_REGISTRY_FILE" "$WORK/r0"; cp "$FORGE_WATCH_ROOTS_FILE" "$WORK/w0"; out=$(ensure s1 --print-path 2>/dev/null)
cmp -s "$FORGE_REGISTRY_FILE" "$WORK/r0" && ok 'T-WT-NO-REGISTRY' || bad registry; cmp -s "$FORGE_WATCH_ROOTS_FILE" "$WORK/w0" && ! grep -qF "$out" "$FORGE_WATCH_ROOTS_FILE" && ok 'T-WT-NO-WATCHROOTS' || bad watch-roots
mkdir -p "$out/.dev/attention"; printf '{"schema":"cc-spawn/1","event":"spawn-state","session":"crashed","root":"%s","state":"needs-repair","detail":"crash residue","emitted_at":"%s"}\n' "$out" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$out/.dev/attention/spawn-crashed-needs-repair.json"
FORGE_RECOVER_TMUX_STATUS=no-server "$FORGE" recover --dry-run --json > "$WORK/recover" 2>/dev/null
python3 - "$WORK/recover" "$out" <<'PY' && ok 'T-WT-RECOVER-ROOTS' || bad recover
import json,sys
d=json.load(open(sys.argv[1])); rows=[r for r in d["roots"] if r["root"]==sys.argv[2]]
assert len(rows)==1 and rows[0]["candidates"] >= 1 and rows[0]["unknown"] == []
PY
repo denv; out=$(DIRENV_ALLOWED=0 ensure s1 --print-path 2>"$WORK/denv.err"); grep -q "allow $out" "$DIRENV_LOG" && ok 'T-WT-DIRENV allowed' || bad direnv-allowed
repo defer; out=$(ensure s1 --print-path 2>"$WORK/defer.err"); ! grep -q '^allow ' "$DIRENV_LOG" && grep -q 'direnv allow' "$WORK/defer.err" && ok 'T-WT-DIRENV deferred' || bad direnv-deferred
repo nobin; out=$(FORGE_DIRENV_BIN="$WORK/no-direnv" ensure s1 --print-path 2>"$WORK/nobin.err"); python3 -c 'import json,sys;assert json.load(open(sys.argv[1]))["direnv"]=="deferred"' "$out/.dev/forge-worktree.json" && grep -q "direnv allow '$out'" "$WORK/nobin.err" && ok 'T-WT-DIRENV binary-absent-is-deferred' || bad direnv-no-binary
repo absent; rm "$SRC/.envrc"; out=$(ensure s1 --print-path 2>/dev/null); ! grep -q '^allow ' "$DIRENV_LOG" && python3 -c 'import json,sys;assert json.load(open(sys.argv[1]))["direnv"]=="absent"' "$out/.dev/forge-worktree.json" && ok 'T-WT-DIRENV absent' || bad direnv-absent
repo dfail; out=$(DIRENV_ALLOWED=0 DIRENV_RC=7 ensure s1 --print-path 2>"$WORK/dfail.err"); [ $? = 0 ] && grep -q 'direnv allow failed' "$WORK/dfail.err" && ok 'T-WT-DIRENV nonfatal' || bad direnv-failure
printf '\nPASS: %d\nFAIL: %d\n' "$PASS" "$FAIL"; [ "$FAIL" = 0 ]
