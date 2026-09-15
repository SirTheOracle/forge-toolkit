"""Closed orchestrator asset inventory and provider routes, shared by install/bridge."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

TREE = Path(__file__).resolve().parent.parent
INCLUDE = re.compile(r"<<<INCLUDE\s+(\S+)\s*>>>")
TOKEN = re.compile(r"[A-Za-z0-9_-]+\Z")


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check", "route"))
    parser.add_argument("--tree", type=Path, default=TREE)
    parser.add_argument("--stage")
    parser.add_argument("--worker")
    args = parser.parse_args()
    try:
        if args.action == "check":
            errors = inventory(args.tree)
            print("\n".join(errors) if errors else "Asset inventory complete")
            return bool(errors)
        provider = "codex" if args.worker.startswith("codex") else "claude"
        print(route(args.stage, args.worker, tree=args.tree,
                    prompts=os.environ.get("FORGE_PROMPTS_DIR"), digests=os.environ.get("FORGE_DIGESTS_DIR"),
                    skill_root=os.environ.get("FORGE_" + provider.upper() + "_SKILLS_DIR")))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print("ASSET_REQUIRED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
