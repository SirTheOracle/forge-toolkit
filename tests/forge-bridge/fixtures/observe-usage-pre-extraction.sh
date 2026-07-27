# Verbatim _observe_usage from b1f5142, renamed _observe_usage_pre.
# Reference implementation for T-ACM-PARSER-EQUIV. DO NOT EDIT — if this needs to
# change, the parser is no longer equivalent and the test is telling you so.
_observe_usage_pre() {
    local worker="$1" slug="$2" stage="$3" worker_idx="$4" project_root="$5"

    # Guard 1: only the four worker panes (an orchestrator self-callback that
    # resolves to `claude` is silently skipped — no usage signal wanted there).
    case "$worker" in
        claude-opus|claude-sonnet|codex-a|codex-b) ;;
        *) return 0 ;;
    esac

    # Family from the canonical worker name (mirrors family() in cmd_health).
    local family
    case "$worker" in
        claude-*) family="claude" ;;
        codex-*)  family="codex"  ;;
        *)        return 0 ;;
    esac

    local usage_file="$project_root/$(_usage_file)"
    local measured_at; measured_at=$(timestamp)
    local fields=""

    # Capture the pane footer for either family (read-only) and parse its
    # provider-specific context anchor.
    # Test seam: FORGE_USAGE_FIXTURE points at a pre-captured pane file and
    # bypasses tmux entirely (used by T1 unit tests; never set in production).
    local raw_file="" fixture_mode=""
    if [ -n "${FORGE_USAGE_FIXTURE:-}" ] && [ -f "${FORGE_USAGE_FIXTURE}" ]; then
        raw_file="$FORGE_USAGE_FIXTURE"; fixture_mode=1
    else
        # cmd_callback ran require_identity, so ID_target_session is authoritative
        # (host for a worker self-callback; the declared target for a seat relay).
        case "$worker_idx" in
            ''|*[!0-9]*) fields="pct=null tokens=null headroom=unknown source=pane-footer confidence=none reason=bad-pane"
                         _emit_event USAGE "$slug" "stage=$stage worker=$worker family=$family $fields"
                         return 0 ;;
        esac
        local sess="${ID_target_session:-}"
        if [ -z "$sess" ]; then
            fields="pct=null tokens=null headroom=unknown source=pane-footer confidence=none reason=no-session"
            _emit_event USAGE "$slug" "stage=$stage worker=$worker family=$family $fields"
            return 0
        fi
        raw_file=$(mktemp -t forge-usage-XXXXXX 2>/dev/null) || raw_file=""
        if [ -n "$raw_file" ]; then
            tmux capture-pane -p -t "$sess:$WINDOW.$worker_idx" -S -12 > "$raw_file" 2>/dev/null || true
        fi
    fi

    # Parse + persist in one python pass; it prints the USAGE event fields (NOT
    # the raw footer line — raw lives only in the snapshot YAML, never in the
    # line-oriented event log) and always exits 0 with a well-formed fields line.
    fields=$(FORGE_U_FAMILY="$family" FORGE_U_WORKER="$worker" FORGE_U_STAGE="$stage" \
             FORGE_U_AT="$measured_at" FORGE_U_RAWFILE="${raw_file:-}" \
             python3 - "$usage_file" 2>/dev/null <<'PYEOF'
import os, sys, re, yaml, fcntl
usage_file = sys.argv[1]
fam   = os.environ.get("FORGE_U_FAMILY", "")
wk    = os.environ.get("FORGE_U_WORKER", "")
stage = os.environ.get("FORGE_U_STAGE", "")
at    = os.environ.get("FORGE_U_AT", "")
rawf  = os.environ.get("FORGE_U_RAWFILE", "")

def fields_line(d):
    def v(x):
        return "null" if x is None else x
    return ("pct=%s tokens=%s headroom=%s source=%s confidence=%s%s" % (
        v(d.get("pct")), v(d.get("tokens")), v(d.get("headroom")), v(d.get("source")),
        v(d.get("confidence")),
        (" reason=%s" % d["reason"]) if d.get("reason") else ""))

def upsert(path, worker, rec, updated):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    lp = path + ".lock"
    with open(lp, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            data = {}
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        data = yaml.safe_load(f) or {}
                except Exception:
                    data = {}
            if not isinstance(data, dict):
                data = {}
            workers = data.get("workers")
            if not isinstance(workers, dict):
                workers = {}
            workers[worker] = rec
            data["workers"] = workers
            data["updated"] = updated
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                yaml.safe_dump(data, f, default_flow_style=False, sort_keys=True)
            os.replace(tmp, path)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

# Defaults → unknown; refined on a successful family-specific parse.
rec = {"family": fam, "pct": None, "tokens": None, "headroom": "unknown",
       "raw": None, "source": "pane-footer", "confidence": "none",
       "reason": ("codex-context-anchor-missing" if fam == "codex" else "no-anchor"),
       "stage": stage, "measured_at": at}
try:
    raw = ""
    if rawf and os.path.exists(rawf):
        with open(rawf) as f:
            raw = f.read()
    if not raw.strip():
        rec["reason"] = "capture-failed"
    else:
        # Strip ANSI/OSC, then scan the last twelve non-empty lines newest-first.
        norm = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', raw)
        norm = re.sub(r'\x1b\][0-9;]*\x07', '', norm)
        lines = [line for line in norm.splitlines() if line.strip()][-12:]
        if fam == "claude":
            found = None
            for line in reversed(lines):
                mm = re.search(r'ctx:\s*(\d+)(k?)\s*\((\d+)%\)', line)
                if mm:
                    found = (mm, line.strip()); break
            if found:
                mm, line = found
                pct = int(mm.group(3))
                tokens = mm.group(1) + (mm.group(2) or "")
                headroom = max(0, min(100, 100 - pct))
                rec.update({"pct": pct, "tokens": tokens, "headroom": headroom,
                            "raw": line, "source": "pane-footer",
                            "confidence": ("high" if 0 <= pct <= 100 else "low"),
                            "reason": None})
        elif fam == "codex":
            found = None
            for line in reversed(lines):
                mm = re.search(r'\bContext\s+(\d{1,3})%\s+left\b', line)
                if mm:
                    found = (mm, line.strip()); break
            if found:
                mm, line = found
                remaining = int(mm.group(1))
                if 0 <= remaining <= 100:
                    rec.update({"pct": 100 - remaining, "tokens": None,
                                "headroom": remaining, "raw": line,
                                "source": "pane-footer", "confidence": "high",
                                "reason": None})
                else:
                    rec.update({"raw": line, "reason": "bad-percent"})
except Exception:
    rec = {"family": fam, "pct": None, "tokens": None, "headroom": "unknown",
           "raw": None, "source": "pane-footer", "confidence": "none",
           "reason": "observe-error", "stage": stage, "measured_at": at}

try:
    upsert(usage_file, wk, rec, at)
except Exception:
    pass

print(fields_line(rec))
PYEOF
)
    [ -z "$fixture_mode" ] && [ -n "$raw_file" ] && rm -f "$raw_file" 2>/dev/null
    [ -z "$fields" ] && fields="pct=null tokens=null headroom=unknown source=pane-footer confidence=none reason=observe-error"
    _emit_event USAGE "$slug" "stage=$stage worker=$worker family=$family $fields"
    return 0
}
