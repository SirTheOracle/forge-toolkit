#!/bin/bash
# forge-board.5s.sh — SwiftBar/xbar plugin: the ambient always-visible Forge surface.
# Consumes ONLY `forge board --json` (cc-board/1) — NO detection of its own, so it cannot
# disagree with the watcher. Renders unseen(hot)/task counts in the menubar AND its OWN staleness
# from the heartbeat, so a dead watcher/menubar is self-evident (the failure a resident process
# must never hide). App-swappable: any consumer of this same JSON is equivalent — the plan
# hardcodes no app. SwiftBar itself is the launchd-supervised host. Filename "5s" sets the refresh.
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
hot   = b.get("hot") or []
tasks = b.get("tasks") or []
unseen = sum(1 for r in hot if not r.get("acked"))
stale = b.get("stale")
title = (f"forge {unseen}!" if unseen else "forge ✓") + (" ⚠" if stale else "")
print(title)
print("---")
if stale:
    print(f"⚠ watcher stale ({b.get('heartbeat_age_s')}s) — run: forge-watch install | color=red")
for r in hot:
    who = r.get("session") or r.get("label")
    print(f"! {r.get('condition')} · {who} | color=red")
print(f"{len(tasks)} task(s) in window")
print("Open board | bash=/bin/sh param1=-c param2='forge board' terminal=true")
PY
