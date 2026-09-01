#!/usr/bin/env python3
"""Deterministic queue logic for the forge-fix-runner skill. TEMPLATE.

Pure functions operate on a list of issue dicts shaped like `gh issue list
--json number,title,labels` output. The CLI reads live from `gh` by default, or
from a JSON file (`--json FILE`) for tests / dry-run.

The actionable predicate here is the load-bearing safety contract: it is what
keeps the runner from re-queuing an issue that is already awaiting retest or
already in flight.

This is the toolkit TEMPLATE copy. Projects take a project-local copy under
`.claude/skills/forge-fix-runner/scripts/`; it is behaviourally identical
everywhere and only the PER-PROJECT CONFIG block below differs. When logic
changes here, propagate the change and leave each project's CONFIG block alone.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid

# NEVER import multiprocessing / concurrent.futures / logging.handlers here.
# test_queue.py does `import queue as q`, which resolves to THIS file only because
# sys.path[0] is the script directory; anything that transitively imports the stdlib
# `queue` fails with "cannot import name 'Empty' from 'queue'" and takes the whole
# suite with it. Measured, not assumed.

# ──────────────────────────────────────────────────────────────────────────
# PER-PROJECT CONFIG  — the only block that differs between projects.
# In the template DEFAULT_REPO is unset: a project copy must set it, or the
# caller must pass --repo. Never let this template guess a repo.
DEFAULT_REPO = "SirTheOracle/forge-toolkit"
TRIGGER_LABEL = "forge-fix"          # human-added triage signal; the runner never adds it
CLASSIFIER_PREFIX = "component:"     # forge-toolkit buckets by component, not service
BLOCKING_LABELS = {"needs-retest", "in-progress", "fix-pr-open", "operator-next"}

# Where this project's plan directories live under the PRIMARY root. Each project
# records its real value here.
PLANS_SUBDIR = ".dev/proposals/fix-plans"

# Readiness rungs (V2). One cheap probe per TOOLCHAIN this project uses. A queue's
# readiness runs every rung whose `match` fires against ANY required_tests command in
# that queue — a frontend-only first bucket proves nothing about a later backend one.
READINESS_RUNGS = {
    # forge-toolkit's suites are bash. No database, no server, no package manager.
    "bash": {"match": r"tests/[\w.-]+/run\.sh",
             "probe": "bash --version"},
}

# Patterns meaning "this worktree resolved the HARDCODED fallback, not the seeded
# value". NEGATIVE-ONLY on purpose: the correct seeded URL is project-specific, so a
# positive assertion would refuse a HEALTHY worktree. Empty here: forge-toolkit has
# no database, so there is no fallback to detect.
DB_FALLBACK_MARKERS: list[str] = []

# Commands PROVEN not to touch shared infra (no database, no fixed port). Anything
# not matching is infra-requiring. Adding a pattern is a deliberate, reviewed act.
# EVERY pattern is anchored to END OF STRING (V5): a `\b` terminator classifies
# `npm run test:run && pytest tests/...` as infra-free, which is precisely the
# wrong-False S6 itself names as the invariant-1 violation it must never produce.
# NO backend pytest command is ever infra-free: conftest.py derives the shared-DB URL
# at import and its session-scoped autouse fixture pg_terminate_backend()s every other
# connection to that database.
# The TEMPLATE ships EMPTY — a project earns entries by declaring them.
INFRA_FREE_TEST_PATTERNS: list[str] = [
    # forge-toolkit has no shared database and no fixed port. Each suite resolves the
    # binary under test from its OWN repo root, so a linked worktree is fully isolated
    # until merge. That makes these genuinely concurrent-safe, and is why fan-out here
    # approaches 1x rather than the 1.5-1.9x a shared-DB project pays.
    # Anchored to end of string: a trailing `&& something-else` is NOT infra-free.
    r"^\s*(bash\s+)?tests/[\w.-]+/run\.sh\s*$",
]
# ──────────────────────────────────────────────────────────────────────────

# ── Template-owned constants (NOT per-project) ────────────────────────────
MAX_SESSIONS = 3
QUEUE_IDS = ["S1", "S2", "S3"]           # S1 is always the initiator's queue
TIER_BASE = {"quick": 1, "full": 10}     # additive + tier-dominant
BIG_BUCKET_ISSUES = 10                   # past here the tier base stops dominating
IN_PROGRESS_LABEL = "in-progress"
PR_OPEN_LABEL = "fix-pr-open"
DEAL_SCHEMA = "forge-runner-deal/1"
CLAIM_SCHEMA = "forge-runner-claim/1"
SEAL_SCHEMA = "forge-runner-seal/1"
DEAL_FILE = "deal.json"
SEAL_FILE = "manifest.json"

# Exit codes — stable; the SKILL and the operator both key off them.
EXIT_LIVE_DEAL = 2
EXIT_ALREADY_CLAIMED = 3
EXIT_ROOT_CONFLICT = 4
EXIT_SEAL_MISMATCH = 5
EXIT_INVALID_PLAN = 6
EXIT_READINESS = 7

# Display noun derived from the classifier, so a project that buckets by
# "area:" prints "area" rather than a hardcoded "service".
CLASSIFIER_NOUN = CLASSIFIER_PREFIX.rstrip(":")

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SEVERITY_NONE = 99


def label_names(issue: dict) -> list[str]:
    return [lbl["name"] for lbl in issue.get("labels", [])]


def service_labels(issue: dict) -> list[str]:
    return [n for n in label_names(issue) if n.startswith(CLASSIFIER_PREFIX)]


def severity_of(issue: dict) -> str | None:
    sev = [n.split(":", 1)[1] for n in label_names(issue) if n.startswith("severity:")]
    return sev[0] if sev else None


def severity_rank(issue: dict) -> int:
    return SEVERITY_RANK.get(severity_of(issue) or "", SEVERITY_NONE)


def is_actionable(issue: dict) -> bool:
    """Open + trigger label + exactly one classifier label + no blocking label."""
    if issue.get("state", "OPEN").upper() != "OPEN":
        return False
    names = set(label_names(issue))
    if TRIGGER_LABEL not in names:
        return False
    if len(service_labels(issue)) != 1:
        return False
    if names & BLOCKING_LABELS:
        return False
    return True


def service_of(issue: dict) -> str:
    svc = service_labels(issue)
    return svc[0].split(":", 1)[1] if len(svc) == 1 else "?"


def tally(issues: list[dict]) -> dict[str, dict]:
    """Per-service counts over ACTIONABLE issues only."""
    out: dict[str, dict] = {}
    for issue in issues:
        if not is_actionable(issue):
            continue
        svc = service_of(issue)
        row = out.setdefault(
            svc, {"service": svc, "critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0}
        )
        sev = severity_of(issue)
        if sev in row:
            row[sev] += 1
        row["total"] += 1
    return out


def worst_first(issues: list[dict]) -> list[dict]:
    rows = list(tally(issues).values())
    rows.sort(key=lambda r: (-r["critical"], -r["high"], -r["medium"], -r["total"]))
    return rows


def queue_for(service: str, issues: list[dict]) -> list[dict]:
    """Actionable issues for one service, severity-ordered (then issue number)."""
    sel = [i for i in issues if is_actionable(i) and service_of(i) == service]
    sel.sort(key=lambda i: (severity_rank(i), i.get("number", 0)))
    return sel


# ── weight ────────────────────────────────────────────────────────────────
def bucket_weight(bucket: dict) -> int:
    """Additive and tier-dominant: a full bucket outweighs any realistic quick one."""
    tier = bucket.get("tier")
    if tier not in TIER_BASE:
        raise ValueError(f"{bucket.get('group_id')!r}: tier must be one of {sorted(TIER_BASE)}")
    return TIER_BASE[tier] + len(bucket.get("covered_issue_numbers") or [])


def weight_warnings(buckets: list[dict]) -> list[str]:
    """Warn — never silently invert — when a bucket is large enough that the issue
    count could outweigh the tier base. Observed n in the real artifact is 1-3."""
    return [
        f"WARN {b.get('group_id')}: covers {len(b.get('covered_issue_numbers') or [])} issues "
        f"(>= {BIG_BUCKET_ISSUES}) — the tier base no longer dominates the weight; "
        f"check the deal by hand"
        for b in buckets
        if len(b.get("covered_issue_numbers") or []) >= BIG_BUCKET_ISSUES
    ]


# ── V1: dependencies have no execution path ───────────────────────────────
def dependency_errors(buckets: list[dict]) -> list[str]:
    """A cross-bucket code dependency is not executable in this model.

    Every bucket branches from a freshly fetched origin/<base_branch> and no session
    waits for a merge, so a later bucket CANNOT see an earlier bucket's unmerged
    work. Queue locality supplies ORDERING, not ANCESTRY. Code-dependent or same-file
    work must be merged into ONE bucket at grouping time. The key itself is retained
    (always []) so the 39 legacy artifacts still parse.
    """
    bad = sorted(str(b.get("group_id")) for b in buckets if b.get("depends_on"))
    if not bad:
        return []
    return ["non-empty depends_on is not executable: " + ", ".join(bad) +
            ". A later bucket branches from origin/<base_branch>, which does not "
            "contain an earlier bucket's unmerged PR. Fix at GROUPING time by merging "
            "these into one bucket ('same files => same bucket', 'consumes a symbol "
            "another bucket creates => same bucket')."]


# ── V4: the ref is stored BARE; origin/ is added at exactly one place ──────
def normalize_base_branch(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("base_branch must be a non-empty string")
    v = value.strip()
    if v.startswith("origin/") or v.startswith("refs/"):
        raise ValueError(f"base_branch must be BARE (e.g. 'main'), got {v!r}. "
                         "'origin/' is added at use; storing it yields origin/origin/main.")
    return v


def base_remote_ref(bucket: dict, remote: str = "origin") -> str:
    """The ONE place `origin/` is ever prepended. Never store the result."""
    return f"{remote}/{normalize_base_branch(bucket.get('base_branch'))}"


# ── deal ──────────────────────────────────────────────────────────────────
def deal_buckets(buckets: list[dict], sessions: int = MAX_SESSIONS) -> dict[str, list[str]]:
    """Heaviest first, each to the currently-lightest queue, initiator breaks ties.

    Returns {queue_id: [group_id, ...]}. Deterministic under input shuffle: the sort
    key is (-weight, group_id) and the assignment key is (load, index), both total
    orders. n = min(3, len(buckets)) — never an empty queue an operator would open a
    tab for.
    """
    errs = dependency_errors(buckets)
    if errs:
        raise ValueError("; ".join(errs))
    ids = [str(b.get("group_id")) for b in buckets]
    dupes = sorted({g for g in ids if ids.count(g) > 1})
    if dupes:
        raise ValueError(f"duplicate group_id(s): {dupes}")
    if sessions < 1:
        raise ValueError("sessions must be >= 1")
    n = min(sessions, MAX_SESSIONS, len(buckets))
    queues: dict[str, list[str]] = {QUEUE_IDS[i]: [] for i in range(n)}
    if n == 0:
        return queues
    load = [0] * n
    for b in sorted(buckets, key=lambda b: (-bucket_weight(b), str(b.get("group_id")))):
        k = min(range(n), key=lambda i: (load[i], i))     # tie -> lowest index -> S1
        queues[QUEUE_IDS[k]].append(str(b["group_id"]))
        load[k] += bucket_weight(b)
    return queues


def queue_weights(buckets: list[dict], queues: dict[str, list[str]]) -> dict[str, int]:
    by_id = {str(b.get("group_id")): b for b in buckets}
    return {qid: sum(bucket_weight(by_id[g]) for g in members if g in by_id)
            for qid, members in queues.items()}


def verify_deal(deal: dict) -> list[str]:
    """Re-check an operator-edited deal. Every entry is a refusal, not a warning."""
    errs: list[str] = []
    buckets = deal.get("buckets") or []
    queues = deal.get("queues") or {}
    errs += dependency_errors(buckets)
    by_id: dict[str, dict] = {}
    for b in buckets:
        gid = str(b.get("group_id") or "")
        if not gid:
            errs.append("a bucket has no group_id"); continue
        if gid in by_id:
            errs.append(f"duplicate group_id {gid}"); continue
        by_id[gid] = b
        if b.get("tier") not in TIER_BASE:
            errs.append(f"{gid}: tier must be quick|full")
        if not (b.get("covered_issue_numbers") or []):
            errs.append(f"{gid}: covered_issue_numbers is empty")
        try:
            normalize_base_branch(b.get("base_branch"))
        except ValueError as ex:
            errs.append(f"{gid}: {ex}")
        if not re.fullmatch(r"[0-9a-f]{40}", str(b.get("base_sha") or "")):
            errs.append(f"{gid}: base_sha must be a 40-hex commit sha")
        if not str(b.get("packet") or "").startswith("packets/"):
            errs.append(f"{gid}: packet must be a path under packets/")
    if not queues:
        errs.append("the deal has no queues")
    if len(queues) > MAX_SESSIONS:
        errs.append(f"{len(queues)} queues exceeds the {MAX_SESSIONS}-session cap")
    for qid, members in queues.items():
        if qid not in QUEUE_IDS:
            errs.append(f"unknown queue id {qid}")
        if not members:
            errs.append(f"queue {qid} is empty — never print a tab command for an empty queue")
    assigned = [g for members in queues.values() for g in members]
    for gid in sorted(by_id):
        c = assigned.count(gid)
        if c == 0:
            errs.append(f"bucket {gid} is not assigned to any queue")
        elif c > 1:
            errs.append(f"bucket {gid} is assigned to {c} queues — a bucket is never split")
    for gid in sorted(set(assigned) - set(by_id)):
        errs.append(f"queue references unknown bucket {gid}")
    seen: dict[int, str] = {}
    for b in buckets:
        if b.get("approval_status") != "approved":
            continue
        for num in b.get("covered_issue_numbers") or []:
            if num in seen:
                errs.append(f"issue #{num} is covered by both {seen[num]} and {b.get('group_id')}")
            else:
                seen[num] = str(b.get("group_id"))
    return errs


# ── plan directory ────────────────────────────────────────────────────────
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_json_atomic(path: str, obj) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def primary_root(start: str = ".") -> str:
    """The PRIMARY working tree, resolved identically from any linked worktree.

    The same anchor the infra lock uses. The plan directory must NOT live in the
    initiator's worktree: that worktree is itself disposable and gets pruned.
    """
    out = subprocess.run(["git", "-C", start, "rev-parse", "--path-format=absolute",
                          "--git-common-dir"], capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        raise SystemExit(f"not inside a git worktree: {start}")
    return os.path.dirname(os.path.realpath(out.stdout.strip()))


def physical_root(start: str = ".") -> str:
    """realpath(git rev-parse --show-toplevel) — ownership_root() semantics.

    Never $PWD: two sessions in different subdirectories of ONE worktree must both
    resolve to the same physical root, or ROOT_CONFLICT never fires.
    """
    out = subprocess.run(["git", "-C", start, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        raise SystemExit(f"not inside a git worktree: {start}")
    return os.path.realpath(out.stdout.strip())


def plans_dir(root: str = ".", override: str | None = None) -> str:
    return override or os.path.join(primary_root(root), PLANS_SUBDIR)


def load_deal(plan_dir: str) -> dict:
    """deal.json is the CANONICAL mechanical source (V11); plan.md is the audit
    artifact. Execute mode, claim and verify all read this file, never the prose."""
    path = os.path.join(plan_dir, DEAL_FILE)
    if not os.path.exists(path):
        raise SystemExit(f"no {DEAL_FILE} in {plan_dir}")
    with open(path) as fh:
        deal = json.load(fh)
    if deal.get("schema") not in (None, DEAL_SCHEMA):
        raise SystemExit(f"{path}: unexpected schema {deal.get('schema')!r}")
    return deal


def buckets_for_queue(deal: dict, queue_id: str) -> list[dict]:
    members = (deal.get("queues") or {}).get(queue_id)
    if members is None:
        have = ", ".join(sorted(deal.get("queues") or {})) or "none"
        raise SystemExit(f"queue {queue_id} is not in this deal (have: {have})")
    by_id = {str(b.get("group_id")): b for b in (deal.get("buckets") or [])}
    missing = [g for g in members if g not in by_id]
    if missing:
        raise SystemExit(f"queue {queue_id} references unknown buckets: {', '.join(missing)}")
    return [by_id[g] for g in members]


# ── approval seal (V11) ───────────────────────────────────────────────────
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sealed_relpaths(plan_dir: str) -> list[str]:
    """plan.md + deal.json + every packet. claims/ and journal/ are MUTABLE and are
    deliberately outside the seal — that IS the narrow scope-guard exception."""
    rels = [n for n in ("plan.md", DEAL_FILE) if os.path.isfile(os.path.join(plan_dir, n))]
    pk = os.path.join(plan_dir, "packets")
    if os.path.isdir(pk):
        rels += ["packets/" + n for n in sorted(os.listdir(pk))
                 if os.path.isfile(os.path.join(pk, n))]
    return sorted(rels)


def seal_plan(plan_dir: str) -> dict:
    rec = {"schema": SEAL_SCHEMA, "sealed_at": _now(),
           "files": {rel: _sha256(os.path.join(plan_dir, rel))
                     for rel in sealed_relpaths(plan_dir)}}
    _write_json_atomic(os.path.join(plan_dir, SEAL_FILE), rec)
    return rec


def verify_seal(plan_dir: str) -> list[str]:
    """Empty list = the approved artifacts are provably unchanged."""
    path = os.path.join(plan_dir, SEAL_FILE)
    if not os.path.exists(path):
        return [f"{SEAL_FILE} is missing — this plan was never sealed at approval; "
                "an unsealed plan cannot be claimed"]
    with open(path) as fh:
        rec = json.load(fh)
    files = rec.get("files") or {}
    errs = []
    for rel, want in sorted(files.items()):
        full = os.path.join(plan_dir, rel)
        if not os.path.exists(full):
            errs.append(f"sealed file removed after approval: {rel}")
        elif _sha256(full) != want:
            errs.append(f"sealed file MODIFIED after approval: {rel}")
    for rel in sealed_relpaths(plan_dir):
        if rel not in files:
            errs.append(f"file added after approval: {rel}")
    return errs


# ── claims ────────────────────────────────────────────────────────────────
class ClaimRefused(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def tmux_sessions() -> dict[str, str]:
    """{session_name: incarnation}. Honours the FORGE_TMUX_LIST injection seam
    (`name<TAB>path<TAB>pid<TAB>incarnation`) that every forge suite already uses.
    Tests inject; production never does."""
    inject = os.environ.get("FORGE_TMUX_LIST", "")
    out: dict[str, str] = {}
    if inject:
        try:
            text = open(inject).read()
        except OSError:
            return out
        for line in text.splitlines():
            f = line.split("\t")
            if f and f[0]:
                out[f[0]] = f[3] if len(f) >= 4 else ""
        return out
    try:
        raw = subprocess.run(["tmux", "list-sessions", "-F",
                              "#{session_name}\t#{session_created}"],
                             capture_output=True, text=True, timeout=5)
    except Exception:
        return out
    if raw.returncode != 0:
        return out
    for line in raw.stdout.splitlines():
        f = line.split("\t")
        if len(f) >= 2:
            out[f[0]] = f[1]
    return out


def self_identity(root: str) -> dict:
    host = os.environ.get("FORGE_RUNNER_SELF_HOST") or socket.gethostname()
    session = os.environ.get("TMUX_SESSION", "")
    if not session:
        try:
            session = subprocess.run(["tmux", "display-message", "-p", "#{session_name}"],
                                     capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            session = ""
    return {"host": host, "tmux_session": session,
            "session_incarnation": tmux_sessions().get(session, ""),
            "root": physical_root(root), "pid": os.getpid()}


def claim_liveness(rec: dict, me: dict) -> str:
    """live | dead | foreign.

    A FOREIGN-host claim is NEVER stolen: its liveness is unverifiable from here,
    which is the same rule the infra lock applies to a foreign holder. Consequence to
    be honest about: a foreign claim can only be cleared by an operator, so
    `reconcile` REPORTS it rather than proposing it.
    """
    if rec.get("host") != me["host"]:
        return "foreign"
    sess = str(rec.get("tmux_session") or "")
    if not sess:
        return "dead"
    live = tmux_sessions()
    return "live" if (sess in live and
                      str(live[sess]) == str(rec.get("session_incarnation") or "")) else "dead"


class _PlanLock:
    """ONE lock for the whole plan directory: the root-conflict scan, the claim, the
    steal and the release all happen under it, and every predicate is re-validated
    inside it."""
    def __init__(self, plan_dir: str):
        d = os.path.join(plan_dir, "claims")
        os.makedirs(d, exist_ok=True)
        self.path = os.path.join(d, ".plan.lock")
        self.fh = None

    def __enter__(self):
        self.fh = open(self.path, "w")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()
        return False


def _read_claims(plan_dir: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    d = os.path.join(plan_dir, "claims")
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name)) as fh:
                out[name[:-5]] = json.load(fh)
        except Exception:
            continue
    return out


def claim_queue(plan_dir: str, queue_id: str, root: str) -> dict:
    """Claim one queue of a SEALED plan. Raises ClaimRefused on every refusal."""
    seal_errs = verify_seal(plan_dir)
    if seal_errs:
        raise ClaimRefused(EXIT_SEAL_MISMATCH,
                           "SEAL_MISMATCH: " + "; ".join(seal_errs) +
                           ". The approved plan changed after approval. Re-approve "
                           "deliberately or start a new deal; never execute a plan "
                           "you cannot prove.")
    deal = load_deal(plan_dir)
    buckets = buckets_for_queue(deal, queue_id)
    dep_errs = dependency_errors(buckets)
    if dep_errs:
        raise ClaimRefused(EXIT_INVALID_PLAN, "REFUSED: " + "; ".join(dep_errs))
    unapproved = [str(b.get("group_id")) for b in buckets if b.get("approval_status") != "approved"]
    if unapproved:
        raise ClaimRefused(EXIT_INVALID_PLAN,
                           f"REFUSED: queue {queue_id} contains unapproved bucket(s): "
                           f"{', '.join(unapproved)}")

    me = self_identity(root)
    with _PlanLock(plan_dir):
        claims = _read_claims(plan_dir)
        stolen = None
        for qid, rec in claims.items():
            state = claim_liveness(rec, me)
            if qid == queue_id:
                if state == "dead":
                    stolen = rec
                    continue
                same = (rec.get("host") == me["host"]
                        and rec.get("tmux_session") == me["tmux_session"]
                        and rec.get("session_incarnation") == me["session_incarnation"])
                if same:
                    return rec                       # reentrant: already ours
                raise ClaimRefused(
                    EXIT_ALREADY_CLAIMED,
                    f"ALREADY_CLAIMED: queue {queue_id} is held by "
                    f"{rec.get('tmux_session')}@{rec.get('host')} (incarnation "
                    f"{rec.get('session_incarnation')}, root {rec.get('root')}, since "
                    f"{rec.get('claimed_at')})"
                    + (" [foreign host — not stealable]" if state == "foreign" else ""))
            if state != "dead" and os.path.realpath(str(rec.get("root") or "")) == me["root"]:
                raise ClaimRefused(
                    EXIT_ROOT_CONFLICT,
                    f"ROOT_CONFLICT: queue {qid} of this plan is already claimed from "
                    f"this physical root ({me['root']}) by {rec.get('tmux_session')}. "
                    "One worktree per session (invariant 6) — open the second tab in "
                    "its own worktree.")
        rec = dict(me)
        rec.update({"schema": CLAIM_SCHEMA, "queue": queue_id, "plan_dir": plan_dir,
                    "claimed_at": _now(), "owner_token": uuid.uuid4().hex,
                    "stole_from": (stolen or {}).get("tmux_session")})
        # Atomic rename inside the plan-wide lock.
        d = os.path.join(plan_dir, "claims")
        tmp = os.path.join(d, f".{queue_id}.json.tmp.{os.getpid()}")
        with open(tmp, "w") as fh:
            json.dump(rec, fh, indent=2, sort_keys=True); fh.write("\n")
        os.replace(tmp, os.path.join(d, f"{queue_id}.json"))
        return rec


def release_queue(plan_dir: str, queue_id: str, root: str, owner_token: str | None) -> str:
    """Owner-token OR exact-identity release.

    V8 says "owner token required for release". Accepting an exact
    (host, tmux_session, session_incarnation) match as well is a DELIBERATE and
    equivalent-strength relaxation, not an oversight: a LIVE session's claim can never
    be stolen (the steal path requires a dead incarnation), so the only claim the
    identity tuple can ever match is one this very session still holds. Requiring an
    operator to carry a token string across a compaction is exactly the friction that
    gets worked around. Do not "tighten" this — it would break post-compaction release.

    What the token still buys, and why it is checked first: it stops a STOLEN
    predecessor from deleting its successor's claim and silently reopening the queue.
    """
    path = os.path.join(plan_dir, "claims", f"{queue_id}.json")
    me = self_identity(root)
    with _PlanLock(plan_dir):
        if not os.path.exists(path):
            return f"RELEASE_NOOP queue={queue_id} (no claim on file)"
        with open(path) as fh:
            rec = json.load(fh)
        by_token = bool(owner_token) and owner_token == rec.get("owner_token")
        by_identity = (rec.get("host") == me["host"]
                       and rec.get("tmux_session") == me["tmux_session"]
                       and rec.get("session_incarnation") == me["session_incarnation"])
        if not (by_token or by_identity):
            return (f"RELEASE_NOOP queue={queue_id} not-owner (held by "
                    f"{rec.get('tmux_session')}@{rec.get('host')}) — your claim was "
                    "stolen; a predecessor never deletes a successor's claim. Do NOT retry.")
        os.remove(path)
        return f"RELEASED queue={queue_id}"


# ── reconcile ─────────────────────────────────────────────────────────────
def _gh_json(argv: list[str]):
    """None means gh was unreachable. The caller MUST fail closed — an unreachable gh
    is never 'nothing found'."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except Exception:
        return None


def open_prs(repo: str):
    return _gh_json(["gh", "pr", "list", "--repo", repo, "--state", "open",
                     "--limit", "200", "--json", "number,url,body,title,headRefName"])


def pr_references(prs: list[dict], issue_number: int) -> list[int]:
    pat = re.compile(rf"#{issue_number}\b")
    return [p.get("number") for p in prs
            if pat.search(p.get("body") or "") or pat.search(p.get("title") or "")]


def local_branches(root: str) -> set[str]:
    out = subprocess.run(["git", "-C", root, "for-each-ref",
                          "--format=%(refname:short)", "refs/heads/"],
                         capture_output=True, text=True)
    return set(out.stdout.split()) if out.returncode == 0 else set()


def reconcile(issues: list[dict], prs: list[dict], plan_dirs: list[str], root: str) -> dict:
    """Two classes of residue, both OWNER-based:

      (a) an issue carrying `in-progress` that nothing owns — no open PR references
          it, no LIVE claim covers its bucket, and no local fix branch for it survives
          that a session could still be working on;
      (b) a claim file whose recorded session is dead (same host only).

    A live claim, a live tmux incarnation, an open referencing PR, or a surviving
    branch each INDEPENDENTLY protect the label. Elapsed time never does — a
    full-tier bucket can legitimately run for a day.
    """
    me = self_identity(root)
    branches = local_branches(root)
    protected: set[int] = set()
    dead_claims: list[dict] = []
    foreign_claims: list[dict] = []
    for plan_dir in plan_dirs:
        try:
            deal = load_deal(plan_dir)
        except SystemExit:
            continue
        by_id = {str(b.get("group_id")): b for b in (deal.get("buckets") or [])}
        for qid, rec in _read_claims(plan_dir).items():
            state = claim_liveness(rec, me)
            members = (deal.get("queues") or {}).get(qid) or []
            if state == "dead":
                dead_claims.append({"plan_dir": plan_dir, "queue": qid, "record": rec,
                                    "path": os.path.join(plan_dir, "claims", f"{qid}.json")})
                continue
            if state == "foreign":
                foreign_claims.append({"plan_dir": plan_dir, "queue": qid, "record": rec})
            for gid in members:
                protected.update(by_id.get(gid, {}).get("covered_issue_numbers") or [])
        for gid, b in by_id.items():
            if str(b.get("branch_name") or "") in branches:
                protected.update(b.get("covered_issue_numbers") or [])

    orphans = []
    for issue in issues:
        if issue.get("state", "OPEN").upper() != "OPEN":
            continue
        if IN_PROGRESS_LABEL not in set(label_names(issue)):
            continue
        num = issue.get("number")
        if num in protected or pr_references(prs, num):
            continue
        orphans.append({"number": num, "title": issue.get("title", ""),
                        "reason": "no open PR references it, no live claim covers it, "
                                  "and no fix branch for it survives"})
    return {"orphans": orphans, "dead_claims": dead_claims, "foreign_claims": foreign_claims}


def apply_reconcile(repo: str, report: dict) -> None:
    for row in report["orphans"]:
        subprocess.run(["gh", "issue", "edit", str(row["number"]), "--repo", repo,
                        "--remove-label", IN_PROGRESS_LABEL], check=True)
        subprocess.run(["gh", "issue", "comment", str(row["number"]), "--repo", repo,
                        "--body", "<!-- forge-runner:reconcile -->\n"
                                  f"Removed `{IN_PROGRESS_LABEL}`: {row['reason']}. "
                                  "The issue is actionable again."], check=True)
    for row in report["dead_claims"]:
        os.remove(row["path"])
    # foreign_claims are REPORTED, never removed: their liveness is unverifiable from
    # this host, exactly as for a foreign infra-lock holder.


# ── live-deal guard ───────────────────────────────────────────────────────
def find_deals(pdir: str, service: str | None = None) -> list[tuple[str, dict]]:
    """EVERY deal under the plans directory (V9), not just the newest — after a
    --new-deal a newer INACTIVE deal would otherwise hide an older LIVE one."""
    out: list[tuple[str, dict]] = []
    if not os.path.isdir(pdir):
        return out
    for entry in sorted(os.listdir(pdir)):
        plan_dir = os.path.join(pdir, entry)
        if not os.path.isfile(os.path.join(plan_dir, DEAL_FILE)):
            continue
        try:
            deal = load_deal(plan_dir)
        except SystemExit:
            continue
        if service and (deal.get("service") or "") != service:
            continue
        out.append((plan_dir, deal))
    return out


def deal_live_issues(deal: dict, issues: list[dict]) -> list[int]:
    """Liveness is derived from GitHub with NO TTL: a deal is live iff any covered
    issue is still open AND still carries `in-progress`. This self-retires — step 6
    swaps `in-progress` for `fix-pr-open` at PR open, so the last bucket to ship
    retires the deal with no state transition and nothing to garbage-collect."""
    if deal.get("superseded_at"):
        return []
    covered = {n for b in (deal.get("buckets") or [])
               for n in (b.get("covered_issue_numbers") or [])}
    live = []
    for issue in issues:
        num = issue.get("number")
        if num in covered and issue.get("state", "OPEN").upper() == "OPEN" \
                and IN_PROGRESS_LABEL in set(label_names(issue)):
            live.append(num)
    return sorted(live)


def holds_live_claim(plan_dir: str, root: str) -> bool:
    """Owner-aware read path: the initiator legitimately re-runs `tally` after a
    compaction (Hard Rule 16). A guard that blocks legitimate re-orientation gets
    worked around, and worked-around guards die."""
    me = self_identity(root)
    for rec in _read_claims(plan_dir).values():
        if (rec.get("host") == me["host"]
                and rec.get("tmux_session") == me["tmux_session"]
                and rec.get("session_incarnation") == me["session_incarnation"]):
            return True
    return False


def _print_deal_progress(plan_dir: str, deal: dict, issues: list[dict]) -> None:
    by_num = {i.get("number"): i for i in issues}
    claims = _read_claims(plan_dir)
    by_id = {str(b.get("group_id")): b for b in (deal.get("buckets") or [])}
    print(f"deal: {plan_dir}")
    for qid in sorted(deal.get("queues") or {}):
        claim = claims.get(qid)
        held = f"claimed by {claim.get('tmux_session')}" if claim else "unclaimed"
        members = deal["queues"][qid]
        print(f"  {qid} ({held}) — {len(members)} bucket(s)")
        for gid in members:
            b = by_id.get(gid, {})
            states = []
            for n in b.get("covered_issue_numbers") or []:
                issue = by_num.get(n, {}) or {}
                lbl = set(label_names(issue))
                st = issue.get("state", "?").upper()
                states.append(f"#{n}:" + ("closed" if st == "CLOSED"
                                          else PR_OPEN_LABEL if PR_OPEN_LABEL in lbl
                                          else IN_PROGRESS_LABEL if IN_PROGRESS_LABEL in lbl
                                          else "open"))
            print(f"    {gid} [{b.get('tier')}] {' '.join(states)}")


def guard_live_deal(args, issues) -> None:
    """Refuse to TRIAGE while a deal for this repo is live. READS are never refused.

    Only the triage path (plain tally/select, no --deal-status, no live claim)
    refuses.
    """
    root = getattr(args, "root", None) or "."
    # FAIL CLOSED FIRST. `issues is None` means gh was unreachable, and triage on an
    # unknown queue is exactly what invariant 4 forbids — whether or not a deal
    # happens to exist on disk. Checking this AFTER the deal scan let the ordinary
    # first-run state (gh down, no deals yet) fall through to worst_first(None).
    if issues is None:
        print("REFUSED: gh is unreachable, so neither the actionable queue nor deal "
              "liveness can be determined. Failing closed. Retry when gh works; if a "
              "deal exists and you mean to replace it, re-run with --new-deal.",
              file=sys.stderr)
        sys.exit(EXIT_LIVE_DEAL)
    try:
        pdir = plans_dir(root, getattr(args, "plans_dir", None))
    except SystemExit:
        return                       # not in a git worktree: there are no deals to guard
    deals = find_deals(pdir)
    if not deals:
        return
    live = [(pd, d, nums) for pd, d in deals
            for nums in [deal_live_issues(d, issues)] if nums]
    if not live:
        return
    if getattr(args, "new_deal", False):
        for pd, _d, nums in live:
            print(f"NEW_DEAL_OVERRIDE superseding {pd} (still-live issues: "
                  f"{', '.join('#%d' % n for n in nums)})", file=sys.stderr)
            _write_json_atomic(os.path.join(pd, "superseded.json"),
                               {"superseded_at": _now(), "superseded_by": "--new-deal",
                                "live_issues_at_supersede": nums,
                                "disposition": "operator declared a new deal for this repo"})
        return
    if getattr(args, "deal_status", False) or any(holds_live_claim(pd, root) for pd, _d, _n in live):
        for pd, d, _nums in live:
            _print_deal_progress(pd, d, issues)
        sys.exit(0)
    print("REFUSED: a live deal exists for this repo — triage would produce a second, "
          "different grouping of the same queue (invariant: exactly one session triages).",
          file=sys.stderr)
    for pd, _d, nums in live:
        print(f"  live deal: {pd}", file=sys.stderr)
        print(f"    still in-progress: {', '.join('#%d' % n for n in nums)}", file=sys.stderr)
    print("  You are probably an ASSISTING tab. Do this instead:", file=sys.stderr)
    print("    /forge-fix-runner execute <ABS-PLAN-DIR> <QUEUE-ID>", file=sys.stderr)
    print("  To see progress instead of triaging:   queue.py tally --deal-status", file=sys.stderr)
    print("  To find genuinely orphaned labels:     queue.py reconcile --dry-run", file=sys.stderr)
    print("  To start a NEW deal deliberately:      queue.py tally --new-deal", file=sys.stderr)
    sys.exit(EXIT_LIVE_DEAL)


# ── packets (V10) ─────────────────────────────────────────────────────────
PACKET_REQUIRED_KEYS = ("group_id", "service", "tier", "slug", "branch_name",
                        "queue_id", "plan_dir", "base_branch", "base_sha",
                        "covered_issue_numbers", "close_keywords", "required_tests",
                        "verification_targets", "drop_conditions")


def parse_packet(path: str) -> dict:
    """A packet is YAML front matter (`---` fenced) followed by the verbatim prose
    problem statement. The front matter is the machine-checkable contract; a free-text
    scan cannot gate invariant 1 — the verbatim `gh issue view` bodies below the fence
    routinely contain tables and `#N` references that satisfy any loose pattern."""
    import yaml  # lazy: `tally` must not require PyYAML
    text = open(path).read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise ValueError("packet has no `---` YAML front-matter block")
    data = yaml.safe_load(m.group(1))
    if not isinstance(data, dict):
        raise ValueError("packet front matter is not a mapping")
    return data


def check_packets(plan_dir: str) -> list[str]:
    errs: list[str] = []
    deal = load_deal(plan_dir)
    packets_root = os.path.realpath(os.path.join(plan_dir, "packets"))
    approved = [b for b in (deal.get("buckets") or []) if b.get("approval_status") == "approved"]
    if not approved:
        errs.append(f"no approved buckets in {DEAL_FILE}")
    queue_of = {g: qid for qid, members in (deal.get("queues") or {}).items() for g in members}
    seen_issues: dict[int, str] = {}
    for b in approved:
        gid = str(b.get("group_id"))
        if b.get("depends_on"):
            errs.append(f"{gid}: non-empty depends_on is not executable — merge the buckets")
        rel = str(b.get("packet") or "")
        full = os.path.join(plan_dir, rel)
        if not rel.startswith("packets/"):
            errs.append(f"{gid}: packet path {rel!r} is not under packets/"); continue
        real = os.path.realpath(full)
        if os.path.islink(full) or os.path.commonpath([real, packets_root]) != packets_root:
            errs.append(f"{gid}: packet escapes packets/ (symlink or traversal): {rel}"); continue
        if not os.path.isfile(full):
            errs.append(f"{gid}: packet missing: {rel}"); continue
        try:
            pk = parse_packet(full)
        except Exception as ex:
            errs.append(f"{gid}: {ex}"); continue
        for key in PACKET_REQUIRED_KEYS:
            if key not in pk:
                errs.append(f"{gid}: packet is missing required key {key!r}")
        for key in ("group_id", "tier", "slug", "branch_name", "base_branch",
                    "base_sha", "service"):
            if key in pk and str(pk.get(key)) != str(b.get(key)):
                errs.append(f"{gid}: packet {key}={pk.get(key)!r} != {DEAL_FILE} {b.get(key)!r}")
        if pk.get("queue_id") != queue_of.get(gid):
            errs.append(f"{gid}: packet queue_id={pk.get('queue_id')!r} != dealt queue "
                        f"{queue_of.get(gid)!r}")
        covered = set(b.get("covered_issue_numbers") or [])
        pk_covered = set(pk.get("covered_issue_numbers") or [])
        if covered != pk_covered:          # EXACT set equality, both directions
            errs.append(f"{gid}: packet covers {sorted(pk_covered)} but {DEAL_FILE} says "
                        f"{sorted(covered)}")
        # THE gating assertion for invariant 1: one verification row per covered issue,
        # and no row for an issue this bucket does not cover.
        rows = pk.get("verification_targets") or []
        row_issues = {r.get("issue") for r in rows if isinstance(r, dict)}
        for num in sorted(covered - row_issues):
            errs.append(f"{gid}: issue #{num} is covered but has NO verification row — it "
                        f"could never be verified, so `Closes #{num}` could never be earned")
        for num in sorted(row_issues - covered):
            errs.append(f"{gid}: verification row for #{num}, which this bucket does not cover")
        for r in rows:
            if not isinstance(r, dict):
                errs.append(f"{gid}: a verification_targets entry is not a mapping"); continue
            for key in ("issue", "coded_id", "symptom", "check", "evidence"):
                if not r.get(key):
                    errs.append(f"{gid}: verification row {r.get('issue')} lacks {key!r}")
        for num in sorted({int(n) for n in re.findall(r"#(\d+)", str(pk.get("close_keywords") or ""))}
                          - covered):
            errs.append(f"{gid}: close_keywords names #{num}, which this bucket does not cover")
        for num in covered:
            if num in seen_issues:
                errs.append(f"issue #{num} is covered by both {seen_issues[num]} and {gid}")
            else:
                seen_issues[num] = gid
        if not (pk.get("required_tests") or []):
            errs.append(f"{gid}: required_tests is empty — nothing would gate the PR")
        if gid not in queue_of:
            errs.append(f"{gid}: approved but assigned to no queue in {DEAL_FILE}")
    return errs


# ── readiness (V2) ────────────────────────────────────────────────────────
# The CALLER runs this AFTER claiming and UNDER the infra lock. Its commands touch the
# shared database, so running it before the claim and outside the lock performs the
# exact cross-session collision this feature exists to prevent.
def readiness_commands(deal: dict, queue_id: str) -> list[str]:
    """The deduplicated union of required_tests over EVERY bucket in the queue, in
    order. Printed by `--list` so the operator sees what the queue will run."""
    seen, out = set(), []
    for b in buckets_for_queue(deal, queue_id):
        for cmd in b.get("required_tests") or []:
            if cmd not in seen:
                seen.add(cmd); out.append(cmd)
    return out


def queue_toolchains(deal: dict, queue_id: str) -> list[str]:
    """Distinct READINESS_RUNGS keys used ANYWHERE in this queue. One cheap command
    from the first bucket proves nothing: a frontend-only first bucket says nothing
    about a later backend one."""
    cmds = readiness_commands(deal, queue_id)
    return [name for name, rung in READINESS_RUNGS.items()
            if any(re.search(rung["match"], c) for c in cmds)]


def run_readiness(deal: dict, queue_id: str, root: str,
                  db_probe: str | None = None, import_probe: str | None = None) -> list[str]:
    """Returns a list of failures (empty = ready)."""
    failures: list[str] = []
    for name in queue_toolchains(deal, queue_id):
        probe = READINESS_RUNGS[name]["probe"]
        r = subprocess.run(probe, shell=True, capture_output=True, text=True)
        if r.returncode != 0:
            tail = ((r.stderr or r.stdout).strip().splitlines() or [""])[-1]
            failures.append(
                f"{name}: `{probe}` exited {r.returncode}: {tail}\n"
                "  Remediation: declare the missing path under forge.worktree.seed in "
                ".claude/forge-project.yml and re-provision the worktree.")
    if db_probe:
        # The plan assumed a seedless worktree cannot start pytest. It CAN: the app
        # config carries a hardcoded DATABASE_URL default, so an unseeded worktree
        # resolves a DIFFERENT database rather than failing. Assert the RESOLVED value.
        # NEGATIVE-ONLY: the correct value is project-specific, and a positive
        # assertion (e.g. requiring `_test` in the URL) would refuse a HEALTHY
        # worktree, because conftest derives the `_test` name by .replace() at import.
        r = subprocess.run(db_probe, shell=True, capture_output=True, text=True)
        if r.returncode != 0:
            failures.append(f"database: `{db_probe}` exited {r.returncode}")
        else:
            for pat in DB_FALLBACK_MARKERS:
                if re.search(pat, r.stdout):
                    failures.append(
                        f"database: resolved {r.stdout.strip()!r}, which matches the "
                        f"hardcoded-fallback marker {pat!r}. The env file is missing or "
                        "unreadable in this worktree; verification here would target the "
                        "WRONG database. Re-provision with forge.worktree.seed.")
                    break
    if import_probe:
        # A symlinked venv carries an editable-install .pth pointing at the PRIMARY
        # checkout's source. Under pytest the worktree wins; under alembic, uvicorn or
        # bare `python -c` it does not. A green run against the primary's code is a
        # wrong `Closes #N`, so PROVE the provenance rather than assuming it.
        # THE PROBE COMMAND MATTERS: it must reproduce what pytest does — see
        # PROJECT.md. A bare `import app` resolves to the PRIMARY even on a correctly
        # seeded worktree (measured), which would fail the healthy case.
        r = subprocess.run(import_probe, shell=True, capture_output=True, text=True)
        resolved = r.stdout.strip()
        if r.returncode != 0 or not resolved:
            failures.append(f"import provenance: `{import_probe}` exited {r.returncode}")
        elif not os.path.realpath(resolved).startswith(os.path.realpath(root) + os.sep):
            failures.append(
                f"import provenance: the module under test resolves to {resolved!r}, which is "
                f"OUTSIDE this worktree ({root}). A symlinked venv carries an editable-install "
                ".pth pointing at the primary checkout. Tests here would verify code this "
                "session never changed. Add PYTHONPATH=src to this project's rungs, or "
                "bootstrap a per-worktree venv (R12).")
    return failures


def infra_required(required_tests, forced: bool = False) -> bool:
    """True unless EVERY command matches this project's end-anchored infra-free
    allowlist.

    The scout does NOT decide. It supplies `required_tests` (which it already does)
    and may force True — NEVER False. An empty list is True: a bucket with nothing to
    run has proven nothing about what it touches.
    """
    if forced:
        return True
    cmds = list(required_tests or [])
    if not cmds:
        return True
    return not all(
        any(re.match(pat, c) for pat in INFRA_FREE_TEST_PATTERNS)
        for c in cmds
    )


def _load(args) -> list[dict]:
    if args.json:
        with open(args.json) as fh:
            return json.load(fh)
    if not args.repo:
        sys.exit(
            "no repo configured: set DEFAULT_REPO in the PER-PROJECT CONFIG block "
            "of this queue.py, or pass --repo owner/name.\n"
            "(You are probably running the toolkit TEMPLATE copy instead of the "
            "project-local one under .claude/skills/forge-fix-runner/.)"
        )
    cmd = [
        "gh", "issue", "list", "--repo", args.repo, "--label", TRIGGER_LABEL,
        "--state", "open", "--limit", "300", "--json", "number,title,labels,state",
    ]
    rows = _gh_json(cmd)
    if rows is None:
        # None is NOT an empty queue. The live-deal guard must be able to tell the
        # difference so it can fail closed rather than silently permit triage.
        if getattr(args, "allow_gh_failure", False):
            return None
        sys.exit("gh is unreachable (or returned unparseable JSON) — refusing to act "
                 "on an unknown queue state.")
    return rows


def _cmd_tally(args):
    args.allow_gh_failure = True     # so guard_live_deal can fail CLOSED on None
    issues = _load(args)
    guard_live_deal(args, issues)    # exits non-zero when issues is None
    if issues is None:               # belt and braces: never iterate None
        sys.exit("gh is unreachable — refusing to act on an unknown queue state.")
    rows = worst_first(issues)
    if args.format == "json":
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print(f"No actionable {TRIGGER_LABEL} issues.")
        return
    print(f"{CLASSIFIER_NOUN:<12}{'crit':>5}{'high':>5}{'med':>5}{'total':>7}")
    for r in rows:
        print(f"{r['service']:<12}{r['critical']:>5}{r['high']:>5}{r['medium']:>5}{r['total']:>7}")
    print(f"\nNext {CLASSIFIER_NOUN} (worst-first): {rows[0]['service']}"
          f"  ({rows[0]['critical']} criticals)")


def _cmd_select(args):
    args.allow_gh_failure = True     # so guard_live_deal can fail CLOSED on None
    issues = _load(args)
    guard_live_deal(args, issues)    # exits non-zero when issues is None
    if issues is None:               # belt and braces: never iterate None
        sys.exit("gh is unreachable — refusing to act on an unknown queue state.")
    q = queue_for(args.service, issues)
    if args.format == "json":
        print(json.dumps([{"number": i["number"], "severity": severity_of(i),
                           "title": i.get("title", "")} for i in q], indent=2))
        return
    if not q:
        print(f"No actionable issues for {CLASSIFIER_PREFIX}{args.service}")
        return
    for i in q:
        print(f"#{i['number']}\t{severity_of(i) or '-':<8}\t{i.get('title','')[:70]}")


def _cmd_deal(args):
    with open(args.plan) as fh:
        deal = json.load(fh)
    buckets = [b for b in (deal.get("buckets") or []) if b.get("approval_status") != "dropped"]
    if args.verify:
        errs = verify_deal(deal)
        for e in errs:
            print(f"INVALID: {e}", file=sys.stderr)
        if errs:
            sys.exit(EXIT_INVALID_PLAN)
        print(f"OK: {len(buckets)} bucket(s) across {len(deal.get('queues') or {})} queue(s)")
        return
    for w in weight_warnings(buckets):
        print(w, file=sys.stderr)
    queues = deal_buckets(buckets, args.sessions)
    by_id = {str(b["group_id"]): b for b in buckets}
    asked = min(args.sessions, MAX_SESSIONS)
    if len(queues) < asked:
        print(f"NOTE: {len(buckets)} bucket(s) deal into {len(queues)} queue(s), not "
              f"{asked} — no tab is opened for an empty queue.")
    if args.format == "json":
        print(json.dumps(queues, indent=2)); return
    # Per-queue WEIGHT totals alongside bucket counts: this is what makes a
    # single-bucket S3 read as a heavy bucket rather than as a bug. (There are no
    # "units" — chains are rejected, not collapsed.)
    weights = queue_weights(buckets, queues)
    print(f"{'queue':<7}{'buckets':>9}{'weight':>8}   members")
    for qid in QUEUE_IDS:
        if qid not in queues:
            continue
        detail = "  ".join(f"{g}[{by_id[g]['tier']}/{bucket_weight(by_id[g])}"
                           f"/{'infra' if infra_required(by_id[g].get('required_tests')) else 'free'}]"
                           for g in queues[qid])
        print(f"{qid:<7}{len(queues[qid]):>9}{weights[qid]:>8}   {detail}")
    if args.write:
        deal["queues"] = queues
        for g, b in by_id.items():
            b["weight"] = bucket_weight(b)
            b["queue"] = next(q for q, m in queues.items() if g in m)
            b["infra_required"] = infra_required(b.get("required_tests"))
        _write_json_atomic(args.plan, deal)
        print(f"wrote queues into {args.plan}")


def _cmd_seal(args):
    rec = seal_plan(args.plan_dir)
    print(f"SEALED {len(rec['files'])} file(s) at {rec['sealed_at']}")
    for rel in sorted(rec["files"]):
        print(f"  {rec['files'][rel][:12]}  {rel}")


def _cmd_verify_seal(args):
    errs = verify_seal(args.plan_dir)
    for e in errs:
        print(f"SEAL_MISMATCH: {e}", file=sys.stderr)
    if errs:
        sys.exit(EXIT_SEAL_MISMATCH)
    print("SEAL_OK")


def _cmd_claim(args):
    try:
        rec = claim_queue(args.plan_dir, args.queue, args.root)
    except ClaimRefused as ex:
        print(str(ex), file=sys.stderr)
        sys.exit(ex.code)
    if rec.get("stole_from"):
        print(f"STOLE the stale claim of {rec['stole_from']} (its incarnation is no longer live)")
    print(f"CLAIMED queue={rec['queue']} root={rec['root']} session={rec['tmux_session']} "
          f"owner_token={rec['owner_token']}")


def _cmd_release(args):
    print(release_queue(args.plan_dir, args.queue, args.root, args.owner_token))


def _cmd_readiness(args):
    deal = load_deal(args.plan_dir)
    if args.list:
        for name in queue_toolchains(deal, args.queue):
            print(f"rung  {name}\t{READINESS_RUNGS[name]['probe']}")
        for cmd in readiness_commands(deal, args.queue):
            print(f"test  {cmd}")
        return
    failures = run_readiness(deal, args.queue, args.root,
                             db_probe=args.db_probe, import_probe=args.import_probe)
    for f in failures:
        print(f"NOT_READY: {f}", file=sys.stderr)
    if failures:
        print("Release the infra lock and the claim, then STOP. Never execute a queue "
              "you cannot verify.", file=sys.stderr)
        sys.exit(EXIT_READINESS)
    print(f"READY queue={args.queue} toolchains={','.join(queue_toolchains(deal, args.queue)) or 'none'}")


def _cmd_reconcile(args):
    args.allow_gh_failure = False
    issues = _load(args)
    prs = open_prs(args.repo)
    if prs is None:
        sys.exit("gh is unreachable — refusing to reconcile against an unknown PR state.")
    dirs = args.plan_dir or [pd for pd, _d in find_deals(plans_dir(args.root, args.plans_dir))]
    report = reconcile(issues, prs, dirs, args.root)
    for row in report["orphans"]:
        print(f"ORPHAN #{row['number']}  {row['title'][:60]}  ({row['reason']})")
    for row in report["dead_claims"]:
        rec = row["record"]
        print(f"DEAD_CLAIM {row['queue']} in {row['plan_dir']} (session "
              f"{rec.get('tmux_session')} incarnation {rec.get('session_incarnation')} is "
              f"gone; claimed {rec.get('claimed_at')})")
    for row in report["foreign_claims"]:
        rec = row["record"]
        print(f"FOREIGN_CLAIM {row['queue']} in {row['plan_dir']} (host "
              f"{rec.get('host')}; liveness unverifiable from here — operator decision, "
              f"never auto-removed)")
    if not (report["orphans"] or report["dead_claims"]):
        print("nothing to reconcile")
        return
    if not args.apply:
        print("\n(dry run — nothing changed. Re-run with --apply to act. Status is a "
              "human-owned control plane; this never mutates by itself.)")
        return
    apply_reconcile(args.repo, report)
    print("applied")


def _cmd_packet_check(args):
    errs = check_packets(args.plan_dir)
    for e in errs:
        print(f"PACKET_INVALID: {e}", file=sys.stderr)
    if errs:
        sys.exit(EXIT_INVALID_PLAN)
    print("PACKETS_OK")


def main(argv=None):
    p = argparse.ArgumentParser(description="forge-fix-runner queue logic")
    p.add_argument("--json", help="read issues from a JSON file instead of gh (tests/dry-run)")
    p.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repo (owner/name) for gh")
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.add_argument("--root", default=".", help="a path inside the git worktree to resolve from")
    p.add_argument("--plans-dir", dest="plans_dir",
                   help=f"override the plan-directory root (default: <PRIMARY_ROOT>/{PLANS_SUBDIR})")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("tally", help=f"worst-first {CLASSIFIER_NOUN} ordering over actionable issues")
    s = sub.add_parser("select", help=f"actionable queue for one {CLASSIFIER_NOUN}, severity-ordered")
    s.add_argument("--service", required=True,
                   help=f"the {CLASSIFIER_NOUN} value (bare, without the '{CLASSIFIER_PREFIX}' prefix)")
    for sp in (t, s):
        sp.add_argument("--deal-status", dest="deal_status", action="store_true",
                        help="print deal + queue progress instead of triaging (never refuses)")
        sp.add_argument("--new-deal", dest="new_deal", action="store_true",
                        help="audited override: supersede every live deal for this repo")

    d = sub.add_parser("deal", help="deal approved buckets into <=3 session queues")
    d.add_argument("--plan", required=True, help=f"path to {DEAL_FILE}")
    d.add_argument("--sessions", type=int, default=MAX_SESSIONS)
    d.add_argument("--verify", action="store_true",
                   help="re-check an operator-edited deal instead of computing one")
    d.add_argument("--write", action="store_true", help=f"write the computed queues back into {DEAL_FILE}")

    sl = sub.add_parser("seal", help="hash plan.md + deal.json + packets/ at approval (V11)")
    sl.add_argument("--plan-dir", dest="plan_dir", required=True)
    vs = sub.add_parser("verify-seal", help="prove the approved artifacts are unchanged")
    vs.add_argument("--plan-dir", dest="plan_dir", required=True)

    c = sub.add_parser("claim", help="claim one queue of a sealed plan for this session")
    c.add_argument("--plan-dir", dest="plan_dir", required=True)
    c.add_argument("--queue", required=True, choices=QUEUE_IDS)

    r = sub.add_parser("release", help="release this session's claim on a drained queue")
    r.add_argument("--plan-dir", dest="plan_dir", required=True)
    r.add_argument("--queue", required=True, choices=QUEUE_IDS)
    r.add_argument("--owner-token", dest="owner_token")

    rd = sub.add_parser("readiness", help="probe every toolchain used in one queue (V2)")
    rd.add_argument("--plan-dir", dest="plan_dir", required=True)
    rd.add_argument("--queue", required=True, choices=QUEUE_IDS)
    rd.add_argument("--list", action="store_true", help="print the probes and tests without running them")
    rd.add_argument("--db-probe", dest="db_probe",
                    help="a command printing the RESOLVED database URL")
    rd.add_argument("--import-probe", dest="import_probe",
                    help="a command printing the resolved path of the module under test")

    rc = sub.add_parser("reconcile",
                        help="find (and with --apply, remove) orphaned in-progress labels and dead claims")
    rc.add_argument("--apply", action="store_true", help="mutate; without it this is a dry run")
    rc.add_argument("--plan-dir", dest="plan_dir", action="append", default=[],
                    help="restrict to one plan dir (repeatable); default: every deal for this repo")

    pc = sub.add_parser("packet-check", help="validate every approved bucket's packet")
    pc.add_argument("--plan-dir", dest="plan_dir", required=True)

    args = p.parse_args(argv)
    handlers = {
        "tally": _cmd_tally, "select": _cmd_select, "deal": _cmd_deal,
        "seal": _cmd_seal, "verify-seal": _cmd_verify_seal,
        "claim": _cmd_claim, "release": _cmd_release, "readiness": _cmd_readiness,
        "reconcile": _cmd_reconcile, "packet-check": _cmd_packet_check,
    }
    if args.cmd not in handlers:        # fail loudly; never a bare KeyError
        sys.exit(f"internal: subcommand {args.cmd!r} is declared but not wired into main()")
    handlers[args.cmd](args)


if __name__ == "__main__":
    main()
