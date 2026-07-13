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
# Menubar icon: anvil template image (alpha-only; macOS recolors per theme).
# Source: swiftbar/forge-icon.svg — regenerate with swiftbar/render-icon.swift
# (see the SVG header comment). Replaces the word "forge" in the title; the
# status text (N!, ⚙N, ✓, ⚠) stays beside it.
ICON="iVBORw0KGgoAAAANSUhEUgAAACQAAAAkCAYAAADhAJiYAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAACSgAwAEAAAAAQAAACQAAAAAQCQK+gAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAaxJREFUWAntV7tKBEEQPEVN1MzYw8gPUExUMPBXBCMjYwNjA0ND8RsMTU38AxMFY8MDwXcVO3s0vbc9zzsX2YJmHl1d09PXO3CDQQ+7AnO2O8i7BNaOYz5gfA+KmhJpC7ovsB9nnHPvT8DKyGRkUvTNHPs4sU5Cj3up2cynBrpkMsLLh3buJ+MVt2Gyj7KbunOfvZXQKW6/wjJMASNoXsTqXiNAfz2l1tSOxgEiSiWgdagdDf6cTzAtlrumZmurWO8QD76BlQY1qZ2EDUR9w3KrUsdTi5qtWGj1VI5nDCewNcc7xLjr5hzuYXdizanFeYWfmsVwCaX6thy51gjh6Jjx2uqhMWmWkz4hX7V9Ta3jdUVXQVhXJO5JtL45khQ7HyLgFvYFk00dMmcMY6lRBMtQ4esacrjFoQa1snEEBeugGB+1TOiemETenLSZuOfVCmlqzblCMuewRU9SH/CfwY4FT2sJVzX1EhoR1cYbBlpxhCT0qU7ljeWtldtcaq0GOaSHHhtR6RtFtDr32bMeQxgfN5Y85jMnlzHBD2Pss86ei/0nMnJJYejxDyvwC5DK7O/F7D/AAAAAAElFTkSuQmCC"
json="$("$FORGE_BIN" board --json 2>/dev/null)"
if [ -z "$json" ]; then
    echo "⚠ | templateImage=$ICON"; echo "---"; echo "board unavailable — is forge-watch installed? | color=red"; exit 0
fi
# JSON rides in an env var: `python3 - <<PY` takes its PROGRAM from stdin, so a pipe into the
# heredoc would be unreadable (stdin cannot carry both the script and the data).
FW_JSON="$json" FW_ICON="$ICON" python3 - <<'PY'
import os, json
ICON = os.environ.get("FW_ICON", "")
try:
    b = json.loads(os.environ.get("FW_JSON", ""))
except Exception:
    print(f"⚠ | templateImage={ICON}"); print("---"); print("board parse error | color=red"); raise SystemExit(0)

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

def h_iso_age(ts):
    # Parse %Y-%m-%dT%H:%M:%SZ against current UTC. malformed → '?'; future/negative
    # → '0s' (clock-skew fail-safe). Seconds/minutes/hours/days.
    import datetime
    if not ts or not isinstance(ts, str):
        return '?'
    try:
        dt = datetime.datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ').replace(
            tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
        return '?'
    s = int((datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds())
    if s < 0:
        s = 0
    if s >= 86400:
        return f"{s//86400}d"
    return f"{s//3600}h" if s >= 3600 else (f"{s//60}m" if s >= 60 else f"{s}s")

hot   = b.get("hot") or []
tasks = b.get("tasks") or []
parked = b.get("parked") or []             # additive cc-board/1 key (.get or [])
eps   = b.get("episodes") or []            # absent on an older forge-watch → today's render
# In-progress = worker-pane episodes only, by decision (final-plan D5): SESSION-WORKING
# (pane-1/operator seat) is deliberately NOT counted — the gear means worker progress.
inprog = sorted((e for e in eps if e.get("current") and e.get("state") == "in_progress"),
                key=lambda e: e.get("last_at", ""), reverse=True)
unseen = sum(1 for r in hot if not r.get("acked"))
stale = b.get("stale")
parts = []                                 # icon replaces the word "forge"
if unseen:
    parts.append(f"{unseen}!")
if inprog:
    parts.append(f"⚙{len(inprog)}")
if parked:
    parts.append(f"⏸{len(parked)}")        # between ⚙N and ✓; never part of `unseen`
# ✓ only when unseen, in-progress, AND parked are all empty — a parked-only board must
# never render "⏸1 ✓".
if not unseen and not inprog and not parked:
    parts.append("✓")
print(" ".join(parts) + (" ⚠" if stale else "") + f" | templateImage={ICON}")
print("---")
if stale:
    print(f"⚠ watcher stale ({b.get('heartbeat_age_s')}s) — run: forge-watch install | color=red")
# Dropdown shows only UNSEEN hot rows — acked ("I've seen it, deferring") items collapse to a
# dim count so acking visibly declutters. The full detail stays on `forge board`.
for r in hot:
    if r.get("acked"):
        continue
    who = esc(r.get("session") or r.get("label") or "")
    slug = r.get("slug"); stage = r.get("stage")
    if slug and stage:
        age = h_iso_age(r.get("blocked_at")) if r.get("blocked_at") else ""
        tail = f" · {age}" if age else ""
        reason = esc(r.get("reason") or "")[:40]
        rtail = f' · "{reason}"' if reason else ""
        print(f"! {esc(r.get('condition'))} · {esc(slug)}/{esc(stage)}{tail}{rtail} | color=red")
    else:
        print(f"! {esc(r.get('condition'))} · {who} | color=red")
acked = len(hot) - unseen
if acked:
    print(f"{acked} seen item(s) hidden — details: forge board | color=gray")
for r in parked[:3]:
    slug = esc(r.get("slug") or "")
    stage = esc(r.get("stage") or "")
    age = h_iso_age(r.get("parked_at"))
    unco = " · UNCOMMITTED" if r.get("uncommitted") else ""
    reason = esc(r.get("reason") or "")[:40]
    print(f'⏸ {slug}/{stage} · parked {age}{unco} · "{reason}" | color=gray')
if len(parked) > 3:
    print(f"+{len(parked) - 3} more parked · forge board | color=gray")
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
