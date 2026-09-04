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

printf '\nPASS: %d\nFAIL: %d\n' "$PASS" "$FAIL"; [ "$FAIL" = 0 ]
