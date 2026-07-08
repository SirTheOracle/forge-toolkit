#!/bin/bash
# forge-board.5s.sh — SwiftBar/xbar plugin: the ambient always-visible Forge surface.
# Consumes ONLY `forge board --json` (cc-board/1) — NO detection of its own, so it cannot
# disagree with the watcher. Renders unseen(hot)/in-progress/task counts in the menubar AND
# its OWN staleness from the heartbeat, so a dead watcher/menubar is self-evident (the failure
# a resident process must never hide). App-swappable: any consumer of this same JSON is
# equivalent — the plan hardcodes no app. SwiftBar itself is the launchd-supervised host.
# Filename "5s" sets the refresh.
# Install (operator-gated, DoD): symlink into the SwiftBar plugins folder, chmod +x.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/bin:$PATH"
FORGE_BIN="${FORGE_BIN:-forge}"
json="$("$FORGE_BIN" board --json 2>/dev/null)"
if [ -z "$json" ]; then
    echo "forge ⚠"; echo "---"; echo "board unavailable — is forge-watch installed? | color=red"; exit 0
fi
# JSON rides in an env var: `python3 - <<PY` takes its PROGRAM from stdin, so a pipe into the
# heredoc would be unreadable (stdin cannot carry both the script and the data).
FW_JSON="$json" python3 - <<'PY'
import os, json
try:
    b = json.loads(os.environ.get("FW_JSON", ""))
except Exception:
    print("forge ⚠"); print("---"); print("board parse error | color=red"); raise SystemExit(0)

def esc(s):
    # SwiftBar uses '|' to separate text from line metadata — a content pipe corrupts the
    # line. Collapse whitespace too (snippets arrive multi-line).
    return " ".join((s or "").split()).replace("|", "¦")

def h_age(s):
    # Clamp: clock skew can make quiet_s negative (the watcher computes NOW - last_at and
    # accepts future worker timestamps fail-safe); never render "quiet -50s".
    try:
        s = max(0, int(float(s or 0)))
    except (TypeError, ValueError):
        s = 0
    return f"{s//3600}h" if s >= 3600 else (f"{s//60}m" if s >= 60 else f"{s}s")

hot   = b.get("hot") or []
tasks = b.get("tasks") or []
eps   = b.get("episodes") or []            # absent on an older forge-watch → today's render
# In-progress = worker-pane episodes only, by decision (final-plan D5): SESSION-WORKING
# (pane-1/operator seat) is deliberately NOT counted — the gear means worker progress.
inprog = sorted((e for e in eps if e.get("current") and e.get("state") == "in_progress"),
                key=lambda e: e.get("last_at", ""), reverse=True)
unseen = sum(1 for r in hot if not r.get("acked"))
stale = b.get("stale")
parts = ["forge"]
if unseen:
    parts.append(f"{unseen}!")
if inprog:
    parts.append(f"⚙{len(inprog)}")
if not unseen and not inprog:
    parts.append("✓")
print(" ".join(parts) + (" ⚠" if stale else ""))
print("---")
if stale:
    print(f"⚠ watcher stale ({b.get('heartbeat_age_s')}s) — run: forge-watch install | color=red")
# Dropdown shows only UNSEEN hot rows — acked ("I've seen it, deferring") items collapse to a
# dim count so acking visibly declutters. The full detail stays on `forge board`.
for r in hot:
    if r.get("acked"):
        continue
    who = esc(r.get("session") or r.get("label") or "")
    print(f"! {esc(r.get('condition'))} · {who} | color=red")
acked = len(hot) - unseen
if acked:
    print(f"{acked} seen item(s) hidden — details: forge board | color=gray")
for e in inprog[:8]:
    who = esc(e.get("session") or e.get("label") or "?")
    pane = f" p{e['pane']}" if e.get("pane") not in (None, "") else ""
    act = "streaming" if e.get("mid_turn") else f"quiet {h_age(e.get('quiet_s'))}"
    seg = [f"⚙ {who}{pane}"]
    if e.get("label"):                     # skip the segment when absent — never "·  ·"
        seg.append(esc(e["label"]))
    seg += [f"{e.get('turn_count', 0)} turn(s)", act]
    line = " · ".join(seg)
    snip = esc(e.get("last_snippet") or "")[:40]
    if snip:
        line += f' · "{snip}"'
    print(f"{line} | color=orange")
if len(inprog) > 8:
    print(f"+{len(inprog) - 8} more in progress | color=gray")
pend = sum(1 for t in tasks if t.get("state") in ("queued", "accepted", "working"))
work = sum(1 for t in tasks if t.get("state") == "working")
if pend:
    print(f"{pend} pending ({work} working) · {len(tasks)} task(s) in window")
else:
    print(f"{len(tasks)} task(s) in window")
print("Open board | bash=/bin/sh param1=-c param2='forge board' terminal=true")
PY
