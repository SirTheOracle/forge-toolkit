#!/bin/bash
# tests/forge-fix-runner/run.sh — Hard Rule 23 derived-set lockstep + fix-runner
# protocol anchors + the V1 branch-ancestry proof.
#
# Style follows tests/forge-worktree/run.sh: hermetic temp dir, PASS/FAIL counters,
# non-zero exit on any failure, bash-3.2 safe.
#
# WHY THIS SUITE EXISTS: fix-code/fix-qa/fix-qa-retry sat outside the locked set
# unnoticed because the set is written down in THIRTEEN places and derived in none.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ffr.XXXXXX")"; WORK="$(cd "$WORK" && pwd -P)"
trap 'rm -rf "$WORK"' EXIT
PASS=0; FAIL=0
ok(){  PASS=$((PASS+1)); printf '  ok: %s\n' "$1"; }
bad(){ FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$1"; }

RUNNER="$ROOT/skills/forge-fix-runner/SKILL.md"
BRIDGE="$ROOT/bin/forge-bridge"

echo "== 1. Hard Rule 23 derived-set lockstep (13 live sites, 5 files) =="
# The thirteen sites, for the record:
#   1-4  skills/forge-orchestrator/SKILL.md  Rule 23 title, "five infra-touching",
#        locked enumeration, reasoning enumeration
#   5    skills/forge-orchestrator/SKILL.md  "The five reasoning stages are ... scope."
#   6    bin/forge-bridge                    infra-lock header comment
#   7    bin/forge-bridge                    infra-lock help text
#   8-10 docs/forge-technical-reference.md   command table, Hard-Rule table, "Infra-lock coverage"
#   11-13 docs/forge-operator-guide.md       "Infra-lock discipline", recovery prose,
#        "Multi-Worktree Concurrency"
# DELIBERATELY EXCLUDED: skills/forge-orchestrator/SKILL.md's DATED changelog line
# (history, not spec) and handoffs/*.md.
"$BRIDGE" help > "$WORK/help.txt" 2>&1
python3 - "$ROOT" "$WORK/help.txt" <<'PY' && ok "R23-LOCKSTEP every live enumeration matches stage_capabilities" || bad "R23-LOCKSTEP (see above)"
import json, pathlib, re, sys
root = pathlib.Path(sys.argv[1]); helptext = pathlib.Path(sys.argv[2]).read_text()
caps = json.loads((root / "config/codex-forge-runtime.json").read_text())["stage_capabilities"]
locked = {s for s, c in caps.items() if c in ("commit", "live-qa")}
free   = {s for s, c in caps.items() if c == "workspace"} | {"proposal"}
errs = []
def stages(t): return set(re.findall(r'`([a-z][a-z0-9-]*)`', t))

sk = (root / "skills/forge-orchestrator/SKILL.md").read_text()

# --- Sites 1-4: the Rule 23 block. Anchored to the BLOCK, never the file: the dated
# changelog line near the end of the SKILL is HISTORY and must not be rewritten.
blk = re.search(r'^23\. \*\*Cross-worktree infra lock.*?(?=\n---\n)', sk, re.S | re.M)
if not blk:
    errs.append("Rule 23 block not found")
else:
    b = blk.group(0)
    m = re.search(r'\*\*Infra stages \(locked\)\*\*(.*?)\n\s*- \*\*Reasoning', b, re.S)
    if not m: errs.append("locked enumeration not found in the Rule 23 block")
    else:
        got = stages(m.group(1)) - {"commit", "live-qa", "stage_capabilities"}
        if got != locked: errs.append("locked %s != config %s" % (sorted(got), sorted(locked)))
    m = re.search(r'\*\*Reasoning stages \(NEVER locked\)\*\*(.*?)\n\s*- `commit-review`', b, re.S)
    if not m: errs.append("never-locked enumeration not found in the Rule 23 block")
    else:
        got = stages(m.group(1)) - {"workspace", "commit", "live-qa", "infra-lock"}
        if got != free: errs.append("never-locked %s != workspace+proposal %s" % (sorted(got), sorted(free)))
    for needed, why in (("stage_capabilities", "does not cite the derivation source"),
                        ("shared test DATABASE", "does not name the contended resource"),
                        ("INFRA_LOCK_REQUIRED", "does not state that acquisition is enforced"),
                        ("fix-verify", "does not document the fix-verify lock label")):
        if needed not in b: errs.append("Rule 23 %s" % why)
    if "five infra" in b: errs.append("Rule 23 heading still says 'five infra'")

# --- Site 5: only the DATED changelog line may still say "five".
for ln in sk.splitlines():
    if ("five reasoning stages" in ln or "five infra stages" in ln) and not ln.strip().startswith("2026-"):
        errs.append("stale 'five …' outside the dated changelog: %s" % ln.strip()[:80])

# --- Sites 6-7: bridge header comment + help output.
br = (root / "bin/forge-bridge").read_text()
for label, blob in (("header comment", br), ("help output", helptext)):
    for s in sorted(locked):
        if s not in blob: errs.append("bin/forge-bridge %s omits %s" % (label, s))
# NOTE: the loop above is a whole-FILE presence check for the bridge. Its teeth are
# the negative regex below: fix-code/fix-qa/fix-qa-retry appear ZERO times in
# bin/forge-bridge today, so the presence check has bite now — but if a future edit
# mentions them elsewhere it silently retires, and this regex is what still fails.
if re.search(r'\{coding, ?qa, ?qa-fix, ?qa-retry, ?verify\}', br):
    errs.append("bin/forge-bridge still carries a five-stage brace enumeration")

# --- Sites 8-13: the two shipped docs. TWO COMPLEMENTARY RULES — do not delete
# either as "redundant". The enumeration rule catches a stale locked set; the
# "five infra" scan catches stale prose that names no stages at all.
#
# The enumeration rule runs over PARAGRAPH-NORMALISED text, not raw lines. Markdown
# soft-wraps split several of these enumerations mid-list (`fix-code` falls on the
# NEXT line), so a per-line scan made the rule an accident of where the line break
# happened to fall: it fired on correct, wrapped enumerations and stayed silent on
# others purely by luck of formatting.
#
# It matches a CONTIGUOUS comma-separated run of the five original stages, then
# asserts the three fix stages follow it. Merely co-occurring stage names are not an
# enumeration: the "Fresh-code guarantee" section names `qa`/`qa-retry`/`verify` and
# `coding` in separate clauses about RESTART-ON-ENTRY, which has nothing to do with
# the locked set and must not be rewritten.
# Teeth are unchanged: any real five-stage enumeration not followed by the fix stages
# still fails — proven by injecting one.
FIVE = re.compile(r'`coding`,\s*`qa`,\s*`qa-fix`,\s*`qa-retry`,\s*`verify`')
for rel in ("docs/forge-technical-reference.md", "docs/forge-operator-guide.md"):
    text = (root / rel).read_text()
    flat = re.sub(r'\s+', ' ', text)
    for m in FIVE.finditer(flat):
        tail = flat[m.end():m.end() + 60]
        if "`fix-code`" not in tail:
            errs.append("%s: stale enumeration: %s" % (rel, flat[m.start():m.end() + 40]))
    if "Five infra" in text or "five infra" in text:
        errs.append("%s: still says 'five infra'" % rel)

# --- COUPLING: prose that agrees with the config while the GUARD enforces a
# different set is the exact failure mode S1 exists to close. S1's halves must ship
# together, so assert the code half exists whenever the prose half does.
if "_stage_capability_class" not in br or "INFRA_LOCK_REQUIRED" not in br:
    errs.append("bin/forge-bridge: the runtime ownership guard is absent — S1's halves were split")
if not re.search(r'case "\$_cap_class" in\s*\n\s*commit\|live-qa\)', br):
    errs.append("bin/forge-bridge: the guard does not key off commit|live-qa")
if "_infra_lock_owned_by_me" not in br:
    errs.append("bin/forge-bridge: the ownership predicate is absent")

for e in errs: print("  R23-LOCKSTEP: " + e, file=sys.stderr)
sys.exit(1 if errs else 0)
PY

echo "== 2. fix-runner protocol anchors =="
grep -q 'infra-lock acquire --slug <slug> --stage fix-code' "$RUNNER" \
  && ok "T-FR-SHAPEA step 4 wraps fix-code in Shape A" || bad "T-FR-SHAPEA missing"
grep -q 'infra-lock acquire --slug <slug> --stage fix-verify' "$RUNNER" \
  && ok "T-FR-SHAPEB step 5 acquires the fix-verify lock" || bad "T-FR-SHAPEB missing"
grep -q 'infra-lock release --slug <slug> --stage fix-verify' "$RUNNER" \
  && ok "T-FR-SHAPEB-REL step 5 releases the fix-verify lock" || bad "T-FR-SHAPEB-REL missing"
grep -q 'success path AND on the failure path' "$RUNNER" \
  && ok "T-FR-SHAPEB-BOTH release on both paths is stated" || bad "T-FR-SHAPEB-BOTH missing"
grep -q 'never bypass `LANE_REQUIRED`' "$RUNNER" \
  && ok "T-FR-LANE the lane gate survived the Shape A edit" || bad "T-FR-LANE lost the lane gate"

printf '\nPASS: %d\nFAIL: %d\n' "$PASS" "$FAIL"; [ "$FAIL" = 0 ]