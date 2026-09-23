"""Closed orchestrator asset inventory and provider routes, shared by install/bridge."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile

TREE = Path(__file__).resolve().parent.parent
INCLUDE = re.compile(r"<<<INCLUDE\s+(\S+)\s*>>>")
TOKEN = re.compile(r"[A-Za-z0-9_-]+\Z")
WORKERS = ("claude-opus", "claude-sonnet", "codex-a", "codex-b")
HIGH = ("codex-a", "claude-opus")
THROUGHPUT = ("codex-b", "claude-sonnet")


def capability_lane(stage, worker, rollout, *, tree=TREE):
    cfg = json.loads((tree / "config/codex-forge-runtime.json").read_text())
    if rollout not in cfg["rollouts"]:
        raise ValueError("rollout-unknown")
    if stage not in cfg["stage_capabilities"]:
        raise ValueError("capability-unknown")
    cap = cfg["stage_capabilities"][stage]
    allowed = cfg["contain_codex_capabilities"]
    if worker.startswith("codex"):
        if rollout == "enforce":
            allowed = cfg["enforce_codex_capabilities"]
        elif rollout == "publish-enabled":
            allowed = cfg["publish_codex_capabilities"]
        lane = "codex" if cap in allowed else "reviewed-host"
        if cap not in cfg["contain_codex_capabilities"] and not cfg["codex"].get("private_codex_home_default", False):
            lane = "reviewed-host"
    else:
        lane = "claude" if cap in allowed else "reviewed-host"
    return {"capability_class": cap, "lane": lane, "rollout": rollout}


def latest_qa(root, slug):
    import yaml
    if not slug or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*", slug):
        raise ValueError("verify requires a valid --slug")
    path = Path(root) / ".dev/proposals" / slug / "forge-log.yml"
    try:
        doc = yaml.safe_load(regular(path).read_text())
    except yaml.YAMLError as exc:
        raise ValueError("invalid QA history") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
        raise ValueError("invalid QA history")
    rows = []
    for entry in doc["entries"]:
        if not isinstance(entry, dict):
            raise ValueError("invalid QA history entry")
        if entry.get("stage") in ("qa", "qa-retry"):
            if not entry.get("timestamp"):
                raise ValueError("QA history has no timestamp")
            try:
                stamp = datetime.fromisoformat(str(entry["timestamp"]).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("invalid QA timestamp") from exc
            if stamp.tzinfo is None:
                raise ValueError("QA timestamp has no timezone")
            rows.append((stamp.astimezone(timezone.utc), entry))
    if not rows:
        raise ValueError("verify requires prior QA history")
    rows.sort(key=lambda row: row[0])
    if len(rows) > 1 and rows[-1][0] == rows[-2][0]:
        raise ValueError("ambiguous latest QA history")
    row = rows[-1][1]
    if not row.get("response"):
        raise ValueError("latest QA is still pending")
    if not isinstance(row.get("to"), str):
        raise ValueError("latest QA worker is unknown")
    worker = {"opus": "claude-opus", "sonnet": "claude-sonnet", "codex": "codex-a",
              "orchestrator": "claude"}.get(row.get("to"), row.get("to"))
    if worker not in (*WORKERS, "claude"):
        raise ValueError("latest QA worker is unknown")
    return worker


def select_route(stage, *, tree=TREE, rollout="contain", worker=None, assigned=None,
                 unavailable=(), qa_worker=None):
    """Read-only selection. Availability is an observation, never dispatch authority."""
    spec = manifest(tree)
    if stage not in spec["stages"]:
        raise ValueError("stage has no advertised asset contract: " + stage)
    if any(w not in WORKERS for w in (*unavailable, *([worker] if worker else []),
                                     *([assigned] if assigned else []))):
        raise ValueError("unknown worker")
    if worker and assigned and worker != assigned:
        raise ValueError("assigned worker cannot be substituted")
    if stage == "proposal":
        if worker or assigned:
            raise ValueError("proposal is local, not worker-dispatched")
        return {"stage": stage, "status": "local-prerequisites", "selected_worker": None,
                "requested_worker": None, "reason": "native-isolated-proposal-required",
                "required": ["native-children", "isolated-proposal", "local-log-close", "digest"]}
    high = stage.startswith("fix-") or stage in ("review", "incorporate", "implementation", "impl-review", "verify", "proposal-lite")
    tier = "high" if high else "throughput"
    if stage.startswith("fix-") or stage in ("incorporate", "impl-review"):
        preferred = ("claude-opus", "codex-a")
    elif high:
        preferred = HIGH
    elif stage in ("coding", "coding-fix", "qa-fix"):
        preferred = ("claude-sonnet", "codex-b")
    else:
        preferred = THROUGHPUT
    # A contract without a capability class cannot be advertised as executable.
    capability_lane(stage, preferred[0], rollout, tree=tree)
    if stage == "verify" and qa_worker is None:
        raise ValueError("verify requires latest QA worker")
    eligible, rejected = [], {}
    lanes = {}
    for candidate in WORKERS:
        provider = candidate.split("-")[0]
        reason = None
        if candidate not in preferred:
            reason = "reasoning-tier"
        elif provider not in spec["stages"][stage]["providers"]:
            reason = "worker-skill-unsupported"
        else:
            lanes[candidate] = capability_lane(stage, candidate, rollout, tree=tree)
            if provider == "codex" and lanes[candidate]["lane"] == "reviewed-host":
                reason = "reviewed-host-required"
            elif stage == "verify" and candidate == qa_worker:
                reason = "latest-qa-worker"
        if reason:
            rejected[candidate] = reason
        else:
            eligible.append(candidate)
    requested = worker or assigned or preferred[0]
    result = {"stage": stage, "tier": tier, "requested_worker": requested,
              "selected_worker": None, "eligible_workers": [w for w in preferred if w in eligible],
              "rejected_workers": rejected, "rollout": rollout}
    if worker or assigned:
        if requested not in eligible:
            return dict(result, status="refused", reason=rejected[requested])
        if requested in unavailable:
            return dict(result, status="wait", reason="assigned-worker-unavailable" if assigned else "requested-worker-unavailable")
        selected = requested
        reason = "assigned" if assigned else "explicit"
    else:
        selected = next((w for w in preferred if w in eligible and w not in unavailable), None)
        if selected is None:
            if stage in ("qa", "qa-retry") and "codex-b" in unavailable:
                return dict(result, status="local-prerequisites", reason="no-eligible-throughput-worker",
                            required=["native-children", "native-live-qa", "infra-lock", "local-log-close", "digest"])
            return dict(result, status="wait", reason="no-eligible-worker-available")
        reason = "default" if selected == requested else rejected.get(requested, "default-worker-unavailable")
    return dict(result, status="selected", selected_worker=selected, reason=reason, **lanes[selected])


def manifest(tree=TREE):
    return json.loads((tree / "config/orchestrator-assets.json").read_text())


def regular(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f"symlinked asset: {path}")
    if not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError(f"missing or non-regular asset: {path}")
    return path


def template(path, prompts, spec):
    text = regular(path).read_text()
    for name in INCLUDE.findall(text):
        if not TOKEN.fullmatch(name):
            raise ValueError(f"unsafe include: {name}")
        if name in spec["synthetic"]:
            if (prompts / (name + ".txt")).exists():
                raise ValueError(f"shadowed synthetic include: {name}")
            continue
        included = regular(prompts / (name + ".txt")).read_text()
        if INCLUDE.search(included):
            raise ValueError(f"nested include unsupported: {name}")
    return text


def inventory(tree=TREE):
    spec = manifest(tree)
    errors = []
    closure = json.loads((tree / "config/orchestrator-files.json").read_text())
    expected = set(closure["files"])
    actual = {str(p.relative_to(tree)) for base in closure["directories"] for p in (tree / base).rglob("*")
              if (p.is_file() or p.is_symlink()) and "__pycache__" not in p.parts and p.name != ".DS_Store"}
    errors += ["undeclared source asset: " + name for name in sorted(actual - expected)]
    for name in sorted(expected):
        try:
            regular(tree / name)
        except ValueError as exc:
            errors.append(str(exc))
    required = list(spec["required"])
    for provider, names in spec["providers"].items():
        base = "skills" if provider == "claude" else "codex-skills"
        required += [f"{base}/{name}/SKILL.md" for name in names]
        for path in sorted((tree / base).iterdir()):
            if path.is_dir() and path.name not in names:
                errors.append(f"undeclared provider skill: {base}/{path.name}")
    required += [f"digests/{stage}.txt" for stage in spec["stages"]]
    required += [f"prompts/{stage}.txt" for stage in spec["stages"]]
    for name in sorted(set(required)):
        try:
            regular(tree / name)
        except ValueError as exc:
            errors.append(str(exc))
    for path in sorted((tree / "digests").glob("*.txt")):
        if path.stem not in spec["stages"]:
            errors.append(f"undeclared digest: {path.name}")
    for base in ("prompts", "digests"):
        for path in sorted((tree / base).glob("*.txt")):
            try:
                if path.stem in spec["synthetic"] + spec["quarantined"]:
                    raise ValueError(f"forbidden tracked template: {path}")
                template(path, tree / "prompts", spec)
            except ValueError as exc:
                errors.append(str(exc))
    for stage, route in spec["stages"].items():
        for provider in route["providers"]:
            if route["skill"] and route["skill"] not in spec["providers"][provider]:
                errors.append(f"undeclared worker skill: {stage}/{provider}/{route['skill']}")
    return errors


def route(stage, worker, *, tree=TREE, prompts=None, digests=None, skill_root=None):
    spec = manifest(tree)
    if not TOKEN.fullmatch(stage):
        raise ValueError("invalid stage token")
    if stage not in spec["stages"]:
        raise ValueError(f"stage has no advertised asset contract: {stage}")
    provider = "codex" if worker.startswith("codex") else "claude" if worker.startswith("claude") else None
    entry = spec["stages"][stage]
    if provider not in entry["providers"]:
        raise ValueError(f"unsupported worker route: {stage}/{worker}")
    prompts = Path(prompts or tree / "prompts")
    digests = Path(digests or tree / "digests")
    template(prompts / (stage + ".txt"), prompts, spec)
    template(digests / (stage + ".txt"), prompts, spec)
    skill = ""
    if entry["skill"]:
        root = Path(skill_root) if skill_root else Path.home() / (".codex" if provider == "codex" else ".claude") / "skills"
        skill = str(regular(root / entry["skill"] / "SKILL.md"))
    return skill


def snapshot_findings(root, slug, stage):
    if not slug or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*", slug):
        raise ValueError("invalid slug")
    name = {"qa-retry": "issues.md", "fix-qa-retry": "fix-issues.md"}.get(stage)
    if name is None:
        raise ValueError("not a QA retry stage")
    source = regular(Path(root) / ".dev/qa" / slug / name)
    data = source.read_bytes()
    if not data.strip():
        raise ValueError("prior findings are empty")
    target = source.with_name("prior-" + name)
    if target.exists() or target.is_symlink():
        regular(target)
    fd, temporary = tempfile.mkstemp(prefix=".prior-", dir=source.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check", "route", "select", "lane", "snapshot-findings"))
    parser.add_argument("--tree", type=Path, default=TREE)
    parser.add_argument("--stage")
    parser.add_argument("--worker")
    parser.add_argument("--assigned-worker")
    parser.add_argument("--unavailable", action="append", default=[])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--slug")
    args = parser.parse_args()
    if args.action != "check" and not args.stage:
        parser.error("--stage is required")
    if args.action == "route" and not args.worker:
        parser.error("--worker is required")
    try:
        if args.action == "check":
            errors = inventory(args.tree)
            print("\n".join(errors) if errors else "Asset inventory complete")
            return bool(errors)
        if args.action == "snapshot-findings":
            print(snapshot_findings(args.root.resolve(), args.slug, args.stage))
            return 0
        if args.action in ("select", "lane"):
            rollout = os.environ.get("FORGE_CODEX_ROLLOUT", "contain")
            if args.action == "lane":
                for key, value in capability_lane(args.stage, args.worker or "", rollout, tree=args.tree).items():
                    print(f"{key}={value}")
                return 0
            result = select_route(args.stage, tree=args.tree, rollout=rollout, worker=args.worker,
                                  assigned=args.assigned_worker, unavailable=args.unavailable,
                                  qa_worker=latest_qa(args.root.resolve(), args.slug) if args.stage == "verify" else None)
            print(json.dumps(result, sort_keys=True))
            return 0 if result["status"] == "selected" else 3
        provider = "codex" if args.worker.startswith("codex") else "claude"
        print(route(args.stage, args.worker, tree=args.tree,
                    prompts=os.environ.get("FORGE_PROMPTS_DIR"), digests=os.environ.get("FORGE_DIGESTS_DIR"),
                    skill_root=os.environ.get("FORGE_" + provider.upper() + "_SKILLS_DIR")))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(("ROUTE_REFUSED: " if args.action in ("select", "lane") else "ASSET_REQUIRED: ") + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
