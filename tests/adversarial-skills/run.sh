#!/bin/bash
# tests/adversarial-skills/run.sh — the four background-agent watchdog rules, asserted
# on every prose file that spawns a file-deliverable background agent.
#
# Style follows tests/forge-fix-runner/run.sh: hermetic, PASS/FAIL counters, bash-3.2
# safe, non-zero exit on any failure, python3 for the matching logic.
#
# WHY THIS SUITE EXISTS: four skills spawn background agents whose deliverable is a
# file, and nothing told the spawning lead to check that the file appears. A wedged
# agent was indistinguishable from a working one. Before this suite, ZERO test suites
# referenced any adversarial-* SKILL.md or its references/*.md — which is why the
# guidance existed in one skill, incomplete, and in none of the other three. Without
# the suite the fix has no regression coverage and rots the same way.
#
# THE FOUR RULES (each must be stated where the lead waits for the agent):
#   1. Poll for output      — verify bytes on disk, naming the deliverable path
#   2. Timeout budget       — a stated number per wait (5 min investigator/tester/
#                             critic, 8 min synthesizer/reconciler)
#   3. Ping once, with the caveat that NO REPLY DOES NOT MEAN DEAD
#   4. Distinct output path for any replacement — and this one must appear IN the
#      Error Handling / Error Recovery region, not merely somewhere in the file
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PASS=0; FAIL=0
ok(){  PASS=$((PASS+1)); printf '  ok: %s\n' "$1"; }
bad(){ FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$1"; }

echo "== 1. The four watchdog rules, on all eight prose files =="
# Every assertion runs over WHITESPACE-NORMALISED text. These are markdown paragraphs
# that soft-wrap mid-sentence, so a per-line grep would assert where the line break
# happened to fall rather than what the document says — the T-C1-ESTIMATE precedent in
# tests/forge-fix-runner/run.sh.
#
# Rule 4 is region-scoped on purpose. adversarial-proposal already carried the
# distinct-path idea as free-floating prose while its retry rows said only "Re-spawn
# C" — a lead reading the recovery table saw nothing about paths. Presence anywhere in
# the file is NOT the property under test; presence in the recovery region is.
while IFS= read -r line; do
  case "$line" in
    OK\|*)  ok  "${line#OK|}"  ;;
    BAD\|*) bad "${line#BAD|}" ;;
    *)      [ -n "$line" ] && printf '%s\n' "$line" ;;
  esac
done <<EOF
$(python3 - "$ROOT" <<'PY'
import pathlib, re, sys

root = pathlib.Path(sys.argv[1])

# file -> deliverable paths the lead must name at its poll sites. At least one of
# these must appear within the text FOLLOWING a "poll for output" instruction: rule 1
# is "name the exact deliverable path being polled", not "mention polling".
FILES = [
    ("skills/adversarial-proposal/SKILL.md",
     ["proposal-A.md", "proposal-B.md", "proposal-C.md", "final-plan.md",
      "feedback-A.md", "reconciliation-notes.md"]),
    ("skills/adversarial-qa/SKILL.md",
     ["qa-report-A.md", "qa-report-B.md", "qa-synthesis.md", "feedback-A.md",
      "issues.md", "test-plan.md"]),
    ("skills/adversarial-implementation/SKILL.md",
     ["impl-A.md", "impl-B.md", "impl-C.md", "impl-feedback-A.md",
      "implementation.md"]),
    ("skills/adversarial-lite/SKILL.md",
     ["proposal-A.md", "proposal-B.md", "final-plan.md"]),
    ("skills/adversarial-proposal/references/subagent-fallback.md",
     ["proposal-A.md", "proposal-B.md", "proposal-C.md", "feedback-A.md",
      "final-plan.md"]),
    ("skills/adversarial-qa/references/subagent-fallback.md",
     ["qa-report-A.md", "qa-report-B.md", "qa-synthesis.md", "feedback-A.md",
      "issues.md", "test-plan.md"]),
    ("skills/adversarial-implementation/references/subagent-fallback.md",
     ["impl-A.md", "impl-B.md", "impl-C.md", "impl-feedback-A.md",
      "implementation.md"]),
    ("skills/adversarial-lite/references/workflow.md",
     ["proposal-A.md", "proposal-B.md", "final-plan.md"]),
]

# How far after a "poll for output" instruction the deliverable path may appear.
# Generous enough for a wrapped sentence or a small table row, tight enough that an
# unrelated filename elsewhere in the document cannot satisfy it.
WINDOW = 600

def flat(t):
    return re.sub(r"\s+", " ", t)

def error_region(text):
    """The Error Handling / Error Recovery section: its heading through the next
    top-level heading (or EOF). Returns '' when the document has no such section —
    which is itself a rule-4 failure, since there is then nowhere to wire it."""
    m = re.search(r"^## Error (?:Handling|Recovery)\b.*$", text, re.M)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest

failures = 0
for rel, deliverables in FILES:
    p = root / rel
    if not p.exists():
        print("BAD|%s: file does not exist" % rel)
        failures += 1
        continue
    raw = p.read_text()
    f = flat(raw)
    low = f.lower()

    # --- Rule 1: poll for output, naming the deliverable being polled.
    # Two conditions. The directive phrasing alone is not enough: the pre-fix
    # adversarial-proposal said "verify bytes on disk" with no path, so the lead had
    # nothing concrete to check.
    sites = [m.end() for m in re.finditer(r"poll for output", low)]
    if not sites:
        print("BAD|R1-POLL %s: no 'poll for output' instruction" % rel)
        failures += 1
    elif not any(any(d.lower() in low[s : s + WINDOW] for d in deliverables)
                 for s in sites):
        print("BAD|R1-POLL %s: polling is instructed but no deliverable path is "
              "named at any poll site" % rel)
        failures += 1
    else:
        print("OK|R1-POLL %s polls for bytes on disk and names the deliverable" % rel)

    if "bytes on disk" not in low:
        print("BAD|R1-BYTES %s: does not say the check is for bytes on disk" % rel)
        failures += 1
    else:
        print("OK|R1-BYTES %s states the check is bytes on disk" % rel)

    # --- Rule 2: a stated timeout budget, per wait. Both classes of wait exist in
    # every one of these documents (an investigator/tester/critic AND a synthesizer/
    # reconciler), so both numbers must be present. 5/8 is the adversarial-lite
    # calibration and is the precedent — see the lockstep check below.
    has5 = re.search(r"\b5[- ]minute|\b5 minutes\b", low) is not None
    has8 = re.search(r"\b8[- ]minute|\b8 minutes\b", low) is not None
    if has5 and has8:
        print("OK|R2-TIMEOUT %s states both budgets (5 min / 8 min)" % rel)
    else:
        missing = []
        if not has5: missing.append("5-minute")
        if not has8: missing.append("8-minute")
        print("BAD|R2-TIMEOUT %s: missing the %s budget" % (rel, " and ".join(missing)))
        failures += 1

    # --- Rule 3: ping once AND the caveat. TWO separate conditions, deliberately.
    # The caveat is the half that gets dropped, and it is the half that matters: a
    # lead that pings, hears nothing and concludes "dead" will spawn a replacement
    # onto the original's paths.
    pings = re.search(r"\bping\b", low) is not None
    caveat = "no reply does not mean dead" in low
    if pings and caveat:
        print("OK|R3-PING %s instructs one ping AND states no-reply-is-not-dead" % rel)
    else:
        missing = []
        if not pings: missing.append("the ping instruction")
        if not caveat: missing.append("the 'no reply does not mean dead' caveat")
        print("BAD|R3-PING %s: missing %s" % (rel, " and ".join(missing)))
        failures += 1

    # --- Rule 4: distinct replacement paths, IN the recovery region.
    region = flat(error_region(raw)).lower()
    if not region:
        print("BAD|R4-DISTINCT %s: no Error Handling/Recovery section to wire it "
              "into" % rel)
        failures += 1
    elif "distinct path" not in region:
        anywhere = "distinct path" in low
        why = ("only as free-floating prose outside the recovery region"
               if anywhere else "not at all")
        print("BAD|R4-DISTINCT %s: the distinct-replacement-path rule appears %s"
              % (rel, why))
        failures += 1
    else:
        print("OK|R4-DISTINCT %s wires distinct replacement paths into the recovery "
              "region" % rel)

# --- Lockstep: the plan's rule 2 says "where a skill already states a number, keep
# that number". adversarial-lite is the calibration source for 5/8; if someone
# renumbers it, every other file's budget silently stops matching its precedent.
lite = (root / "skills/adversarial-lite/SKILL.md").read_text()
lite_wf = (root / "skills/adversarial-lite/references/workflow.md").read_text()
if re.search(r"5 minutes per proposer", flat(lite)) and "8 minutes" in flat(lite) \
   and "5 minutes" in flat(lite_wf) and "8 minutes" in flat(lite_wf):
    print("OK|R2-LOCKSTEP adversarial-lite still carries its original 5/8 calibration")
else:
    print("BAD|R2-LOCKSTEP adversarial-lite's 5/8 calibration was renumbered")
    failures += 1

sys.exit(1 if failures else 0)
PY
)
EOF

echo "== 2. forge-coder coding-stage runaway guards (issue #52), on both copies =="
# WHY: a dispatched coding worker burned 1h25m on one turn — no commit, no callback,
# reading a file unrelated to its group — while remaining fully contract-compliant,
# because forge-coder's SKILL.md says how to proceed and never when to stop. The guards
# added for #52 are prose, and prose in this repo rots unless a test pins it: before
# this section NO suite referenced forge-coder at all, in either copy.
#
# Both copies are asserted INDEPENDENTLY (plan D2): under rollout=contain, commit-class
# stages route to the reviewed Claude lane, so a guard present only in the Codex mirror
# would be absent from the lane that actually commits.
#
# T7 is the load-bearing one. An earlier draft of the fix told the worker to "commit
# what is complete and continue" — mechanically impossible, because the broker binds one
# commit per delivery and a second attempt fails HEAD_MOVED, leaving the delivery open
# and blocking every later dispatch to that pane. T7 fails against that wording.
while IFS= read -r line; do
  case "$line" in
    OK\|*)  ok  "${line#OK|}"  ;;
    BAD\|*) bad "${line#BAD|}" ;;
    *)      [ -n "$line" ] && printf '%s\n' "$line" ;;
  esac
done <<EOF
$(python3 - "$ROOT" <<'PY'
import pathlib, re, sys

root = pathlib.Path(sys.argv[1])

COPIES = [
    "skills/forge-coder/SKILL.md",
    "codex-skills/forge-coder/SKILL.md",
]

def norm(t):
    """Whitespace-normalised, emphasis-stripped, lowercased. Same reason as section 1:
    these are soft-wrapped markdown paragraphs, so a per-line grep would assert where
    the line break fell. Markdown emphasis is stripped too, so **30 minutes** and
    30 minutes are the same claim."""
    return re.sub(r"\s+", " ", t.replace("*", "").replace("`", "")).lower()

def section(text, heading_re):
    """A '## ' section: its heading through the next top-level heading (or EOF).
    Returns '' when absent — itself a failure, since there is then nowhere to wire
    the guard in."""
    m = re.search(heading_re, text, re.M)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest

STOP_HEADING = r"^## When to Stop\b.*$"
ESC_HEADING = r"^## Escalation: forge ask\b.*$"

# Wording that routes a worker into the broker wedge (see header note). Checked over
# the WHOLE file, not just the stop section: it is equally wrong anywhere.
MIDGROUP_COMMIT = [
    r"commit what is complete",
    r"commit what is done",
    r"commit what you have (?:complete|done)",
    r"continue with a fresh turn",
]

failures = 0
sections = {}

for rel in COPIES:
    p = root / rel
    if not p.exists():
        print("BAD|%s: file does not exist" % rel)
        failures += 1
        continue
    raw = p.read_text()
    whole = norm(raw)
    stop = norm(section(raw, STOP_HEADING))
    esc = norm(section(raw, ESC_HEADING))
    sections[rel] = (section(raw, STOP_HEADING), section(raw, ESC_HEADING))

    if not stop:
        print("BAD|T1-CEILING %s: no 'When to Stop' section at all — every guard below "
              "has nowhere to live" % rel)
        failures += 6
        continue

    # --- T1: a turn-duration ceiling, stated with a NUMBER. "keep an eye on how long
    # you have been running" is not a ceiling; the number is the property under test.
    if "turn-duration ceiling" in stop and re.search(r"\b30[- ]minutes?\b", stop):
        print("OK|T1-CEILING %s states a turn-duration ceiling of 30 minutes" % rel)
    else:
        missing = []
        if "turn-duration ceiling" not in stop: missing.append("the ceiling itself")
        if not re.search(r"\b30[- ]minutes?\b", stop): missing.append("its numeric value")
        print("BAD|T1-CEILING %s: missing %s" % (rel, " and ".join(missing)))
        failures += 1

    # --- T2: hitting the ceiling must ESCALATE, and silent continuation must be named
    # and forbidden. Both halves: the runaway was silent, not merely slow.
    escalates = "forge ask" in stop and "blocked" in stop
    no_silence = re.search(r"(never|do not|don't) continue silently", stop) is not None
    if escalates and no_silence:
        print("OK|T2-ESCALATE %s requires forge ask/BLOCKED on the ceiling and forbids "
              "silent continuation" % rel)
    else:
        missing = []
        if not escalates: missing.append("the forge ask / BLOCKED escalation")
        if not no_silence: missing.append("the no-silent-continuation rule")
        print("BAD|T2-ESCALATE %s: missing %s" % (rel, " and ".join(missing)))
        failures += 1

    # --- T3: the scope guard binds EDITS, and reading is explicitly still allowed.
    # The permission half matters: the incident's tell was a legitimate read, and a
    # guard that forbade reading would fire on normal context-gathering (plan R3).
    names_list = "declared file list" in stop
    forbids_edit = re.search(r"(never|do not|don't) edit a file outside", stop) is not None
    allows_read = re.search(r"reading [^.]{0,80}allowed", stop) is not None
    if names_list and forbids_edit and allows_read:
        print("OK|T3-SCOPE %s guards edits outside the declared file list while still "
              "permitting reads" % rel)
    else:
        missing = []
        if not names_list: missing.append("the declared file list")
        if not forbids_edit: missing.append("the no-edit-outside rule")
        if not allows_read: missing.append("the reading-is-allowed carve-out")
        print("BAD|T3-SCOPE %s: missing %s" % (rel, " and ".join(missing)))
        failures += 1

    # --- T4: prefer recording partial progress over open-ended diagnosis — RECORD,
    # not commit — and name the concrete failure mode from the incident.
    records = re.search(r"record[^.]{0,80}known-incomplete", stop) is not None
    names_report = "coder-report.md" in stop
    not_commit = re.search(r"record, (do not|don't) commit", stop) is not None
    names_mode = "different commit group" in stop
    if records and names_report and not_commit and names_mode:
        print("OK|T4-PARTIAL %s prefers recorded (not committed) partial progress and "
              "names the out-of-scope diagnosis mode" % rel)
    else:
        missing = []
        if not records: missing.append("the record-and-stop instruction")
        if not names_report: missing.append("the coder-report.md destination")
        if not not_commit: missing.append("the record-do-not-commit distinction")
        if not names_mode: missing.append("the other-commit-group diagnosis example")
        print("BAD|T4-PARTIAL %s: missing %s" % (rel, " and ".join(missing)))
        failures += 1

    # --- T5: region-scoped on purpose. "Do NOT ask for things you can resolve and
    # note" is what made the runaway contract-compliant — a worker that is merely slow
    # or off-scope reads as resolve-and-note. The exception must be stated where that
    # line is, or the surrounding text keeps steering the wrong way.
    if not esc:
        print("BAD|T5-EXCEPTION %s: no 'Escalation: forge ask' section to carve the "
              "exception out of" % rel)
        failures += 1
    else:
        carve = re.search(r"exceptions? to the do-not-ask", esc) is not None
        cites_line = "resolve and note" in esc
        names_both = "turn-duration ceiling" in esc and "declared-file scope guard" in esc
        if carve and cites_line and names_both:
            print("OK|T5-EXCEPTION %s states both new triggers as explicit exceptions to "
                  "the resolve-and-note line" % rel)
        else:
            missing = []
            if not carve: missing.append("the stated exception")
            if not cites_line: missing.append("the resolve-and-note line it excepts")
            if not names_both: missing.append("one or both trigger names")
            print("BAD|T5-EXCEPTION %s: missing %s" % (rel, " and ".join(missing)))
            failures += 1

    # --- T7: the resulting contract must be NON-CONTRADICTORY. Two halves: no
    # mid-group-commit wording anywhere in the file, and explicit deference to the
    # rules the new text sits next to.
    offenders = [pat for pat in MIDGROUP_COMMIT if re.search(pat, whole)]
    defers = ("hard constraint 7" in stop and "hard constraint 4" in stop
              and "not committed" in stop)
    if not offenders and defers:
        print("OK|T7-NONCONTRA %s never instructs a mid-group commit and defers to Hard "
              "Constraints 7 and 4" % rel)
    else:
        why = []
        if offenders:
            why.append("instructs a mid-group commit (%s)" % ", ".join(offenders))
        if not defers:
            why.append("does not defer to Hard Constraint 7 / Hard Constraint 4 / the "
                       "files-modified-but-NOT-committed rule")
        print("BAD|T7-NONCONTRA %s: %s" % (rel, "; ".join(why)))
        failures += 1

# --- Lockstep, in the R2-LOCKSTEP idiom: the two copies must carry the SAME guard
# text. This is scoped to the NEW sections only — the copies' pre-existing 72-line
# drift is issue #65 and is deliberately not asserted here.
if len(sections) == len(COPIES):
    stops = set(norm(s) for s, _ in sections.values())
    escs = set(norm(e) for _, e in sections.values())
    if len(stops) == 1 and len(escs) == 1:
        print("OK|T-LOCKSTEP both forge-coder copies carry identical stop-condition and "
              "escalation-exception text")
    else:
        drifted = []
        if len(stops) != 1: drifted.append("the stop-condition section")
        if len(escs) != 1: drifted.append("the escalation exception")
        print("BAD|T-LOCKSTEP the two forge-coder copies have drifted in %s"
              % " and ".join(drifted))
        failures += 1

sys.exit(1 if failures else 0)
PY
)
EOF

echo "== 3. Operator-constraint carriage (#77b) =="
while IFS= read -r line; do
  case "$line" in
    OK\|*)  ok  "${line#OK|}"  ;;
    BAD\|*) bad "${line#BAD|}" ;;
    *)      [ -n "$line" ] && printf '%s\n' "$line" ;;
  esac
done <<EOF
$(FORGE_TEST_CLAUDE_SKILLS_DIR="${FORGE_TEST_CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}" \
  python3 - "$ROOT" <<'PY'
import os, pathlib, re, subprocess, sys

root = pathlib.Path(sys.argv[1])
pdir = root / "prompts"
failures = 0

def _read_or_none(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None

def ok(msg):  print("OK|" + msg)
def bad(msg):
    global failures
    failures += 1
    print("BAD|" + msg)

INC = re.compile(r'<<<INCLUDE\s+(\S+?)\s*>>>')
prompts = sorted(pdir.glob("*.txt"))
stage_prompts = [p for p in prompts if not p.name.startswith("_")]
includes = {p.stem: set(INC.findall(p.read_text())) for p in prompts}

# ---- T-CC0 · deployment topology (P0/P35) --------------------------------------------
# A test that pins a tracked file enforces NOTHING unless the tracked file is the file that
# executes. Six deployed skills were stale copies while this suite was green.
#
# TWO QUESTIONS, DELIBERATELY SEPARATED, because conflating them is what makes this test
# either useless or permanently red:
#
#   TOPOLOGY (always FAILS)  — is this deployment LINKED to this repository at all?
#       fails on: absent; a dangling symlink; a symlink resolving outside every work tree
#       of this repository; a regular-file copy whose bytes match NO checkout of this
#       repository. That last is the unmanaged / hand-edited / abandoned deployment — the
#       only class nothing else in the toolkit can see.
#   FRESHNESS (ADVISORY by default) — is it THIS checkout's content?
#       Reported, counted, and never fatal unless FORGE_TEST_SKILL_STRICT=1.
#
# WHY FRESHNESS CANNOT BE THE DEFAULT FAILURE. Measured: all three symlinked deployments
# point at the PRIMARY checkout, which sits on its own branch — currently behind this
# base — so `skills/forge-fix-runner/SKILL.md` there differs from the copy here.
# `forge-fix-runner` is correctly symlinked AND serving stale bytes. A $ROOT-relative
# oracle would therefore give a different verdict depending on which checkout ran the
# suite and would go red for every skill whenever any worktree sits on a feature branch.
# A test that is red for reasons unrelated to the defect gets disabled, and a topology
# assertion that inherits "validates against the artifact rather than the reality" would be
# this issue's own failure inside the test written to prevent it.
#
# WHY "MATCHES SOME CHECKOUT" IS TOO WEAK TO BE THE ONLY RULE, also measured: this
# repository has seven work trees on various branches, and FOUR of the six known-stale
# copies match one of them. A bare repo-scoped content rule would downgrade
# `adversarial-proposal` — the load-bearing one, the entire reason P0 exists — to an
# advisory. Hence the strict lever: it is the mechanical gate for P0.
#
#   P0 GATE:  FORGE_TEST_SKILL_STRICT=1 bash tests/adversarial-skills/run.sh
#             run from the checkout being shipped. Every advisory becomes a failure.
#             The Definition of Done requires this to pass ONCE, on the merge commit.
#
# Content drift against THIS checkout is already reported by `./install.sh --check-drift`
# (it printed DIFFERS throughout the six-week drift; nobody ran it). This test's new value
# is the topology class, plus a gate that can be demanded in a checklist.
# §11 U13 states this boundary; do not quietly widen or narrow either half.
deployed_root = pathlib.Path(os.environ["FORGE_TEST_CLAUDE_SKILLS_DIR"])
strict = os.environ.get("FORGE_TEST_SKILL_STRICT") == "1"
inst = (root / "install.sh").read_text()
m = re.search(r'^SKILL_NAMES=\(([^)]*)\)', inst, re.S | re.M)
skill_names = set(re.findall(r'[A-Za-z][A-Za-z0-9_-]*', re.sub(r'#.*', '', m.group(1)))) if m else set()

def repo_worktrees(r):
    """Every work tree of THIS repository, resolved. Falls back to the single checkout if
    git is unavailable — which degrades the topology half to $ROOT-relative, and says so
    rather than degrading silently."""
    try:
        out = subprocess.run(["git", "-C", str(r), "worktree", "list", "--porcelain"],
                             capture_output=True, text=True, timeout=20)
        paths = [ln.split(" ", 1)[1].strip() for ln in out.stdout.splitlines()
                 if ln.startswith("worktree ")]
        return ([os.path.realpath(x) for x in paths] or [os.path.realpath(str(r))]), bool(paths)
    except Exception:
        return [os.path.realpath(str(r))], False

trees, enumerated = repo_worktrees(root)
if not skill_names:
    bad("T-CC0 install.sh SKILL_NAMES not found or empty")
elif not deployed_root.is_dir():
    ok("T-CC0 SKIPPED (no deployment root on this host)")
else:
    if not enumerated:
        print("  T-CC0 note: `git worktree list` unavailable — topology is being judged "
              "against this checkout alone, which can produce false failures.")
    bad_ones, stale = [], []
    for name in sorted(skill_names):
        srcd = root / "skills" / name
        if not srcd.is_dir():
            continue            # codex-only (proposal-reviewer); the installer skips it too
        dst = deployed_root / name
        if not dst.exists():
            bad_ones.append("%s: NOT DEPLOYED" % name)
            continue
        # The symlink may be on the directory OR on SKILL.md; both topologies exist here.
        link = dst if dst.is_symlink() else (dst / "SKILL.md")
        here = srcd / "SKILL.md"
        if link.is_symlink():
            target = os.path.realpath(str(link))
            if not os.path.exists(target):
                bad_ones.append("%s: symlink is DANGLING (%s)" % (name, target))
                continue
            if not any(target.startswith(w + os.sep) for w in trees):
                bad_ones.append("%s: symlink resolves OUTSIDE every work tree of this "
                                "repository (%s)" % (name, target))
                continue
            if here.is_file() and open(target, "rb").read() != here.read_bytes():
                stale.append("%s: correctly symlinked into a checkout of this repo, but "
                             "that checkout's bytes differ from this one — a symlink is "
                             "topology, not freshness" % name)
            continue
        dmd = dst / "SKILL.md"
        if not dmd.is_file():
            bad_ones.append("%s: deployed directory has no SKILL.md" % name)
            continue
        dbytes = dmd.read_bytes()
        if not any(_read_or_none(os.path.join(w, "skills", name, "SKILL.md")) == dbytes
                   for w in trees):
            bad_ones.append("%s: deployed SKILL.md is a COPY matching NO checkout of this "
                            "repository — unmanaged, and nothing links it back" % name)
        elif here.is_file() and dbytes != here.read_bytes():
            stale.append("%s: copy matches another checkout of this repo, not this one — "
                         "managed but STALE relative to the checkout under test" % name)
    for s in stale:
        print("  T-CC0 %s: %s" % ("STRICT" if strict else "advisory", s))
    if strict:
        bad_ones += stale
    if bad_ones:
        bad("T-CC0 deployment topology: " + "; ".join(bad_ones))
    else:
        ok("T-CC0 every deployed SKILL_NAMES entry is linked to this repository (%d work "
           "trees considered); %d stale-content %s"
           % (len(trees), len(stale), "failures" if strict else "advisories — run with "
              "FORGE_TEST_SKILL_STRICT=1 to gate on them"))

# ---- T-CC1 · both includes on every non-underscore prompt (P36) -----------------------
# The EXEMPT set FAILS SAFE: a stage nobody thinks about defaults to CARRYING the ledger
# (noise), never to omitting it (silence). Curation is only fatal when its default is
# silence. Each entry needs a WRITTEN REASON here, and this is the only place one lives.
EXEMPT = {
    "proposal": "orchestrator-local and NOT dispatchable — cmd_dispatch refuses "
                "--stage proposal outright, so this template renders no includes and an "
                "include here would never resolve. The frame-challenge clause reaches "
                "that stage through skills/adversarial-proposal/SKILL.md and the "
                "orchestrator's Spec Boundary section instead.",
}
miss = []
for p in stage_prompts:
    need = {"_operator_constraints", "_constraint_check"}
    have = includes[p.stem]
    if p.stem in EXEMPT:
        if need & have:
            miss.append("%s is EXEMPT but carries %s" % (p.name, sorted(need & have)))
        continue
    absent = sorted(need - have)
    if absent:
        miss.append("%s is missing %s" % (p.name, absent))
for name in sorted(set(EXEMPT) - {p.stem for p in stage_prompts}):
    miss.append("EXEMPT names '%s', which is not a stage prompt — a stale exemption is "
                "an exemption nobody re-justified" % name)
if miss:
    bad("T-CC1 include carriage: " + "; ".join(miss))
else:
    ok("T-CC1 every non-underscore prompt carries both operator-constraint includes, or "
       "is EXEMPT with a written reason (%d carriers, %d exempt)"
       % (len(stage_prompts) - len(EXEMPT), len(EXEMPT)))

# ---- T-CC2 · the prose include still says the load-bearing things (P37) ---------------
# The synthetic half fails CLOSED; the tracked prose half does NOT — a prompt missing
# _constraint_check still renders, just meaninglessly. This wording pin plus T-CC1 are
# the only things closing that asymmetry.
cc = (pdir / "_constraint_check.txt")
if not cc.is_file():
    bad("T-CC2 prompts/_constraint_check.txt does not exist")
else:
    body = " ".join(cc.read_text().split())
    need = [
        ("PASS", "the PASS verdict"),
        ("VIOLATED", "the VIOLATED verdict"),
        ("NOT-EXERCISED", "the NOT-EXERCISED verdict"),
        ("NOT-APPLICABLE", "the NOT-APPLICABLE verdict"),
        ("BLOCKING item regardless of what any upstream artifact says",
         "the clause that VIOLATED outranks any upstream artifact"),
        ("never `PASS`", "the executor never-PASS clause"),
        ("scope: user-visible", "the user-visible BLOCKING_ITEMS scoping"),
        ("REAL-PRODUCT observation", "the real-product-observation rule for user-visible PASS"),
        ("none_recorded_reason", "the empty-ledger clause"),
        ("Binds", "the binds-vs-asked_about clause"),
    ]
    gone = [why for lit, why in need if lit not in body]
    if gone:
        bad("T-CC2 _constraint_check.txt no longer states: " + "; ".join(gone))
    else:
        ok("T-CC2 _constraint_check.txt still states all four verdicts, that VIOLATED "
           "outranks upstream artifacts, and the executor never-PASS clause")

# ---- T-CC3 / T-CC4 · exact include sets (P38, P39) ------------------------------------
for inc, expected, tid in (
        ("_unchanged_flow_sweep", {"qa", "qa-retry", "fix-qa", "fix-qa-retry"}, "T-CC3"),
        ("_scope_diff_check", {"impl-review", "review", "fix-plan-review"}, "T-CC4")):
    actual = {s for s, v in includes.items() if inc in v}
    if actual != expected:
        bad("%s %s is included by %s, expected exactly %s"
            % (tid, inc, sorted(actual), sorted(expected)))
    else:
        ok("%s %s is included by exactly %s" % (tid, inc, sorted(expected)))

# ---- T-CC5 · every declared Input is somebody's declared Output (P40) -----------------
# REGION-SCOPED on the CONSUMER side only. Prompts name paths in prose and conditionally,
# and a noisy test gets disabled — so this accepts lower recall for near-zero false
# positives, exactly as the plan requires.
#
#   consumer side  a .dev path inside an `Inputs…:` heading region (heading line through
#                  the first blank line followed by a non-indented line).
#   producer side  a .dev path named ANYWHERE in a prompt OTHER than inside that prompt's
#                  own Inputs region, or anywhere in a skills/*/SKILL.md. Deliberately
#                  loose: producers declare their artifacts in an `Outputs:` block, in an
#                  inline `Output: write your review to …` sentence, AND in their DONE
#                  callback --message, and a heading-only producer side false-positives on
#                  repro.md, fix-review.md and fix-coder-report.md, which are all real.
#                  Excluding a prompt's OWN Inputs region is what stops review.txt from
#                  "producing" the very names it dangles on.
#
# PROTOTYPED against the tree before being designed in: it reports EXACTLY TWO errors
# today — review.txt's proposal-a.md and proposal-b.md — both Group A defects, and zero
# after Group A lands. Neither vacuous nor over-broad, measured rather than asserted.
DEVPATH = re.compile(r'(\.dev/[^\s"\'`,)]+\.(?:md|ya?ml))')

def input_regions(text):
    out = []
    for mm in re.finditer(r'(?m)^Inputs?\b[^\n]*:\s*$', text):
        rest = text[mm.start():]
        nxt = re.search(r'(?m)^\s*$\n(?=\S)', rest)
        out.append(rest[:nxt.start()] if nxt else rest)
    return out

per = {}
for p in prompts:
    t = p.read_text()
    ins = set()
    for r in input_regions(t):
        ins |= set(DEVPATH.findall(r))
    per[p.name] = (ins, set(DEVPATH.findall(t)) - ins)

skilltext = "\n".join(f.read_text() for f in sorted((root / "skills").rglob("SKILL.md")))
skill_named = {os.path.basename(x) for x in DEVPATH.findall(skilltext)}

# A FILE MAY NOT SATISFY ITS OWN INPUTS. Without this exclusion a prompt that names an
# artifact in its Inputs block AND mentions it once anywhere else in its own body would
# "produce" it, and the assertion would go vacuous for exactly the prompts most likely to
# reference something that does not exist. review.txt is the live example: it names
# constraints.yml in both its Inputs block and its SPEC FIDELITY section.
dangling = []
for n, (ins, _own) in per.items():
    produced = skill_named | {os.path.basename(x)
                              for m, (_i, outs) in per.items() if m != n for x in outs}
    for path in sorted(ins):
        if os.path.basename(path) not in produced:
            dangling.append("%s inputs '%s' — no OTHER prompt and no skill declares it as "
                            "an output" % (n, os.path.basename(path)))
dangling = sorted(set(dangling))
if dangling:
    bad("T-CC5 dangling artifact references: " + "; ".join(dangling))
else:
    ok("T-CC5 every .dev path named under an Inputs: heading is declared as an output by "
       "some OTHER prompt or by a skill (%d prompts checked)" % len(per))

# ---- T-CC6 · the shadow trap (P41) ---------------------------------------------------
syn = re.search(r'^SYNTHETIC_PROMPTS=\(([^)]*)\)', inst, re.S | re.M)
syn_set = set(re.findall(r'[A-Za-z_][A-Za-z0-9_-]*', re.sub(r'#.*', '', syn.group(1)))) if syn else set()
pn = re.search(r'^PROMPT_NAMES=\(([^)]*)\)', inst, re.S | re.M)
pn_set = set(re.findall(r'[A-Za-z_][A-Za-z0-9_-]*', re.sub(r'#.*', '', pn.group(1)))) if pn else set()
probs = []
if "_operator_constraints" not in syn_set:
    probs.append("_operator_constraints is NOT in SYNTHETIC_PROMPTS — 22 prompts would "
                 "fail to render")
if "_operator_constraints" in pn_set:
    probs.append("_operator_constraints is in PROMPT_NAMES — the installer would try to "
                 "ship a file for a synthetic")
if (pdir / "_operator_constraints.txt").exists():
    probs.append("prompts/_operator_constraints.txt EXISTS — it would shadow the "
                 "synthetic and substitute STALE, SLUG-BLIND text for live operator "
                 "constraints in every carrier prompt")
if "_preamble" not in syn_set:
    probs.append("_preamble is no longer in SYNTHETIC_PROMPTS")
if probs:
    bad("T-CC6 shadow trap: " + "; ".join(probs))
else:
    ok("T-CC6 _operator_constraints is synthetic, undeclared in PROMPT_NAMES, and has no "
       "shadowing file on disk")

# ---- T-CC7 · inventory bidirectionality, and the digest DEFERRAL as a tripwire (P42) --
# There is no DIGEST_NAMES array and no tracked digests/: digest vendoring is EXPLICITLY
# DEFERRED by the plan (F8 — nine fix stages have no template to vendor, so the honest
# task is "author nine and vendor sixteen", which is a project). Inventing a digest
# inventory here would either fail immediately or pass vacuously, and a vacuous assertion
# with a confident label is the failure this whole issue is about.
#
# So this asserts the state the plan DECLARES, as a live tripwire: while neither exists it
# passes and SAYS SO; the moment either appears, BOTH must exist and agree both ways.
shipped = {p.stem for p in prompts}
inv = []
for s in sorted(shipped - pn_set - syn_set):
    inv.append("prompts/%s.txt ships but is absent from PROMPT_NAMES" % s)
for s in sorted(pn_set - shipped):
    inv.append("PROMPT_NAMES declares '%s' but prompts/%s.txt does not exist" % (s, s))
has_dn = re.search(r'^DIGEST_NAMES=\(', inst, re.M) is not None
tracked_digests = sorted((root / "digests").glob("*.txt")) if (root / "digests").is_dir() else []
if has_dn != bool(tracked_digests):
    inv.append("digest vendoring is HALF done: DIGEST_NAMES=%s, tracked digests=%d. "
               "Vendor both or neither — a declared-but-absent digest inventory is the "
               "defect PROMPT_NAMES exists to prevent" % (has_dn, len(tracked_digests)))
if inv:
    bad("T-CC7 inventory: " + "; ".join(inv))
elif not has_dn and not tracked_digests:
    ok("T-CC7 PROMPT_NAMES agrees with prompts/ both ways; digest vendoring deferred "
       "(F8) — tripwire armed")
else:
    ok("T-CC7 PROMPT_NAMES and DIGEST_NAMES both agree with the repo, both ways")

# ---- T-CC8 · NOT_COVERED: on every non-underscore prompt (P43) ------------------------
nc = [p.name for p in stage_prompts if "NOT_COVERED:" not in p.read_text()]
if nc:
    bad("T-CC8 these stage prompts do not require a NOT_COVERED: line: " + ", ".join(sorted(nc)))
else:
    ok("T-CC8 all %d non-underscore prompts require a NOT_COVERED: line" % len(stage_prompts))
# and the contract line it rides on
bi = [p.name for p in stage_prompts if "BLOCKING_ITEMS:" not in p.read_text()]
if bi:
    bad("T-CC8 these stage prompts still have no BLOCKING_ITEMS: line: " + ", ".join(sorted(bi)))
else:
    ok("T-CC8 all %d non-underscore prompts carry the BLOCKING_ITEMS: contract line"
       % len(stage_prompts))

# ---- T-CC9 · qa / qa-retry lockstep, SECTION-SCOPED (P44) -----------------------------
# The two files were byte-identical, which is WHY the build lane's retry stage could not
# report whether the QA fix loop closed anything. qa-retry now carries a PRIOR FINDINGS
# RESOLUTION section that qa must NOT have, so byte-identity is the wrong assertion. The
# lockstep region is: identical include sets, and an identical contract tail from
# `CONFIDENCE:` to EOF. This change makes several synchronised edits across two
# hand-maintained files with nothing else detecting asymmetry.
qa, qr = pdir / "qa.txt", pdir / "qa-retry.txt"
if not (qa.is_file() and qr.is_file()):
    bad("T-CC9 qa.txt / qa-retry.txt missing")
else:
    probs = []
    if includes["qa"] != includes["qa-retry"]:
        probs.append("include sets differ: qa=%s qa-retry=%s"
                     % (sorted(includes["qa"]), sorted(includes["qa-retry"])))
    def tail(p):
        t = p.read_text()
        i = t.find("CONFIDENCE: HIGH/MEDIUM/LOW")
        return t[i:] if i >= 0 else None
    ta, tb = tail(qa), tail(qr)
    if ta is None or tb is None:
        probs.append("one of them no longer carries the CONFIDENCE contract line")
    elif ta != tb:
        probs.append("the contract tail from CONFIDENCE: to EOF has drifted")
    if "PRIOR FINDINGS RESOLUTION" not in qr.read_text():
        probs.append("qa-retry.txt lost its PRIOR FINDINGS RESOLUTION section — the "
                     "build lane's retry stage is back to being unable to report whether "
                     "the QA fix loop closed anything")
    if "PRIOR FINDINGS RESOLUTION" in qa.read_text():
        probs.append("qa.txt gained a PRIOR FINDINGS RESOLUTION section; there are no "
                     "prior findings on a first QA pass")
    if probs:
        bad("T-CC9 qa/qa-retry lockstep: " + "; ".join(probs))
    else:
        ok("T-CC9 qa.txt and qa-retry.txt agree in their lockstep sections, and differ "
           "only where they must")

sys.exit(1 if failures else 0)
PY
)
EOF

printf '\nPASS: %d\nFAIL: %d\n' "$PASS" "$FAIL"; [ "$FAIL" = 0 ]
