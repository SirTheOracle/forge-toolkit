#!/usr/bin/env python3
"""Run the finite local release checks. This never produces admission evidence."""

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

TREE = Path(__file__).resolve().parents[2]
SUITES = ("forge-orchestrator", "forge-bridge", "forge-broker", "adversarial-skills",
          "forge-fix-runner", "forge-start", "forge-recover", "forge-infra-lock")
NATIVE_LIMITS = ("integrated-attended-preview", "claude-build", "codex-build", "claude-fix", "codex-fix",
                 "isolated-local-proposal", "native-live-qa-fallback", "unattended-stall-recovery")
AFFECTED_PATHS = {
    "55": ("reset/send/pending ordering", "forge-bridge"),
    "57": ("delivery-bound terminal callback", "forge-bridge"),
    "80": ("new/untracked commit accounting", "forge-broker"),
    "49": ("evidenced no-op terminal result", "forge-broker"),
    "74": ("unattended stall/dead-worker recovery", "forge-recover"),
    "42/61": ("supersede/incarnation recovery", "forge-bridge"),
    "62": ("same-second stale ownership refusal", "forge-orchestrator"),
    "73": ("unknown legacy ownership refusal", "forge-bridge"),
    "56": ("legacy callback compatibility is not freshness", "forge-bridge"),
    "83/84/85/86/87/89": ("assets, retry findings, inventory and observation", "forge-orchestrator"),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def identity(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": digest(path.read_bytes())}


def snapshot():
    declared = json.loads((TREE / "config/orchestrator-files.json").read_text())
    names = set(declared["files"]) | {"install.sh", "README.md", "docs/forge-operator-guide.md"}
    # Include new test files before their first commit, but not generated reports
    # or phase closeout prose, which are outputs rather than executable inputs.
    names.update(str(p.relative_to(TREE)) for p in (TREE / "tests").rglob("*")
                 if p.is_file() and p.suffix in {".py", ".sh", ".json", ".txt", ".yml", ".log"}
                 and "__pycache__" not in p.parts and "evidence" not in p.parts)
    files = {name: digest((TREE / name).read_bytes()) for name in sorted(names)}
    tools = {name: identity(path) if path else None for name, path in
             (("python", sys.executable), ("bash", "/bin/bash"), ("git", shutil.which("git")),
              ("tmux", shutil.which("tmux")), ("codex", shutil.which("codex")), ("claude", shutil.which("claude")))}
    return {"files": files, "tools": tools,
            "sha256": digest(json.dumps({"files": files, "tools": tools}, sort_keys=True).encode())}


def run_suite(name, directory, env):
    command = ["/bin/bash", str(TREE / "tests" / name / "run.sh")]
    log = directory / (name + ".log")
    started = time.monotonic()
    status, returncode = "fail", None
    with log.open("xb") as output:
        child = subprocess.Popen(command, cwd=TREE, env=env, stdout=output, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        try:
            returncode = child.wait(timeout=1800)
            status = "pass" if returncode == 0 else "fail"
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            status = "interrupted"
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
    return {"command": command, "status": status, "exit_code": returncode,
            "seconds": round(time.monotonic() - started, 2), "log": identity(log)}


def finish_report(before, after, results):
    stable = before == after
    complete = set(results) == set(SUITES) and all(row["status"] == "pass" for row in results.values())
    return {"schema": "forge-orchestrator-local-acceptance/1", "scope": "local-contracts-only",
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source": before, "source_stable": stable, "after_sha256": after["sha256"],
            "status": "pass" if stable and complete else "fail", "suites": results,
            "admission_evidence": False, "full_workflow_parity": "not-established",
            "native": {name: {"status": "not-run", "reason": "local-only, no model calls authorized"}
                       for name in NATIVE_LIMITS},
            "affected_paths": {issue: {"path": path, "related_suite": suite,
                                      "local_suite_status": results.get(suite, {}).get("status", "not-run"),
                                      "original_defect_disposition": "not-requalified"}
                               for issue, (path, suite) in AFFECTED_PATHS.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="new report directory under this worktree's .dev")
    args = parser.parse_args()
    directory = Path(args.output).resolve()
    if TREE / ".dev" not in directory.parents:
        parser.error("output must stay under this worktree's .dev")
    directory.mkdir(parents=True, exist_ok=False)
    before = snapshot()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=TREE, text=True).strip()
    env = dict(os.environ, FORGE_ORCHESTRATOR_NATIVE_MANAGEMENT="0", FORGE_START_ATTACH="0", FORGE_WATCH_TRIGGER="0")
    results = {}
    for name in SUITES:
        print("Running " + name, flush=True)
        results[name] = run_suite(name, directory, env)
        print(name + ": " + results[name]["status"], flush=True)
        if results[name]["status"] == "interrupted":
            break
    report = finish_report(before, snapshot(), results)
    report["starting_head"] = head
    with (directory / "report.json").open("x") as output:
        json.dump(report, output, indent=2, sort_keys=True)
        output.write("\n")
    print(str(directory / "report.json"), flush=True)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
