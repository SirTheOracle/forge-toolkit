#!/usr/bin/env python3
"""Deterministic queue logic for the forge-fix-runner skill.

Pure functions operate on a list of issue dicts shaped like `gh issue list
--json number,title,labels` output. The CLI reads live from `gh` by default, or
from a JSON file (`--json FILE`) for tests / dry-run.

The actionable predicate here is the load-bearing safety contract: it is what
keeps the runner from re-queuing an issue that is already awaiting retest or
already in flight (e.g. issue #81, which carries both forge-fix and needs-retest).
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys

# Labels that make an issue NON-actionable even when forge-fix is present.
BLOCKING_LABELS = {"needs-retest", "in-progress", "fix-pr-open"}
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SEVERITY_NONE = 99


def label_names(issue: dict) -> list[str]:
    return [lbl["name"] for lbl in issue.get("labels", [])]


def service_labels(issue: dict) -> list[str]:
    return [n for n in label_names(issue) if n.startswith("service:")]


def severity_of(issue: dict) -> str | None:
    sev = [n.split(":", 1)[1] for n in label_names(issue) if n.startswith("severity:")]
    return sev[0] if sev else None


def severity_rank(issue: dict) -> int:
    return SEVERITY_RANK.get(severity_of(issue) or "", SEVERITY_NONE)


def is_actionable(issue: dict) -> bool:
    """Open + forge-fix + exactly one service:* + no blocking label."""
    if issue.get("state", "OPEN").upper() != "OPEN":
        return False
    names = set(label_names(issue))
    if "forge-fix" not in names:
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


DEFAULT_REPO = "SirTheOracle/goparent-ai"


def _load(args) -> list[dict]:
    if args.json:
        with open(args.json) as fh:
            return json.load(fh)
    cmd = [
        "gh", "issue", "list", "--repo", args.repo, "--label", "forge-fix",
        "--state", "open", "--limit", "300", "--json", "number,title,labels,state",
    ]
    return json.loads(subprocess.check_output(cmd, text=True))


def _cmd_tally(args):
    issues = _load(args)
    rows = worst_first(issues)
    if args.format == "json":
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("No actionable forge-fix issues.")
        return
    print(f"{'service':<12}{'crit':>5}{'high':>5}{'med':>5}{'total':>7}")
    for r in rows:
        print(f"{r['service']:<12}{r['critical']:>5}{r['high']:>5}{r['medium']:>5}{r['total']:>7}")
    print(f"\nNext service (worst-first): {rows[0]['service']}  ({rows[0]['critical']} criticals)")


def _cmd_select(args):
    issues = _load(args)
    q = queue_for(args.service, issues)
    if args.format == "json":
        print(json.dumps([{"number": i["number"], "severity": severity_of(i),
                           "title": i.get("title", "")} for i in q], indent=2))
        return
    if not q:
        print(f"No actionable issues for service:{args.service}")
        return
    for i in q:
        print(f"#{i['number']}\t{severity_of(i) or '-':<8}\t{i.get('title','')[:70]}")


def main(argv=None):
    p = argparse.ArgumentParser(description="forge-fix-runner queue logic")
    p.add_argument("--json", help="read issues from a JSON file instead of gh (tests/dry-run)")
    p.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repo (owner/name) for gh")
    p.add_argument("--format", choices=["table", "json"], default="table")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tally", help="worst-first service ordering over actionable issues")
    s = sub.add_parser("select", help="actionable queue for one service, severity-ordered")
    s.add_argument("--service", required=True)
    args = p.parse_args(argv)
    {"tally": _cmd_tally, "select": _cmd_select}[args.cmd](args)


if __name__ == "__main__":
    main()
