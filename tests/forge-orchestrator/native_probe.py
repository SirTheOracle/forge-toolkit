#!/usr/bin/env python3
"""Disposable P1 experiments. Never admits a provider or modifies an install."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import time
import tomllib


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent.parent
SCHEMA = "forge-orchestrator-p1/1"
PACKAGE_ROOTS = (
    "bin", "config", "prompts", "skills/forge-orchestrator",
    "codex-skills/adversarial-proposal", "codex-skills/adversarial-qa",
)
OPERATIONS = ("create", "overwrite", "rename", "unlink", "symlink")
TOPOLOGIES = ("primary", "linked", "primary-nested", "nested-linked")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    # All outputs are new; a rerun gets a new evidence directory, never replaces one.
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def inside(path, root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def no_symlink_components(path):
    absolute = Path(os.path.abspath(path))
    # macOS /tmp and /var are system aliases. Resolve the existing parent once at
    # creation; subsequent packet paths must already be physical, with no aliases.
    return all(not part.is_symlink() for part in (absolute, *absolute.parents))


def execute(argv, *, cwd=None, timeout=20, env=None):
    start = time.monotonic()
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        return {"returncode": None, "error": "launch-error", "errno": exc.errno,
                "elapsed_seconds": round(time.monotonic() - start, 3),
                "stdout": "", "stderr": ""}
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        error = None
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = proc.communicate()
        error = "timeout"
    return {"returncode": proc.returncode, "error": error,
            "elapsed_seconds": round(time.monotonic() - start, 3),
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace")}


def git(root, *args):
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0")
    for key in list(env):
        if key.startswith(("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT",
                           "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_", "GIT_COMMON_DIR")):
            env.pop(key)
    result = execute(["git", "-c", "core.hooksPath=" + os.devnull,
                      "-c", "commit.gpgSign=false", "-c", "user.name=P1 fixture",
                      "-c", "user.email=p1@invalid", "-C", str(root), *args], env=env)
    if result["returncode"] != 0 or result["error"]:
        raise ValueError("fixture git operation failed: " + " ".join(args[:2]))
    return result["stdout"].strip()


def init_repo(root):
    root.mkdir(parents=True)
    git(root, "init", "--quiet", "--template=", "--initial-branch=main")
    (root / "probe.txt").write_text("disposable P1 repository\n")
    git(root, "add", "probe.txt")
    git(root, "commit", "--quiet", "-m", "P1 fixture")


def file_identity(path):
    path = Path(path)
    return {"path": str(path.absolute()), "resolved_path": str(path.resolve()),
            "sha256": digest(path), "mode": path.stat().st_mode & 0o777}


def copy_package(base, source, operator=None):
    package = (operator or base / "operator") / "package"
    files = git(source, "ls-files", "-z", "--", *PACKAGE_ROOTS).split("\0")
    for relative in filter(None, files):
        src = source / relative
        if src.is_symlink() or not src.is_file() or not inside(src, source):
            raise ValueError("non-regular or escaping package input: " + relative)
        dst = package / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        dst.chmod(0o755 if os.access(src, os.X_OK) else 0o644)
    fixtures = package / "p1"
    fixtures.mkdir(parents=True)
    for name in ("canary.py", "package_access.py", "native-cases.json", "digest.txt"):
        shutil.copyfile(HERE / "fixtures" / name, fixtures / name)
    # The marker is only in the file to be loaded, never in the launch prompt.
    (fixtures / "SKILL.md").write_text(
        "---\nname: forge-p1-probe\ndescription: Explicit disposable P1 loading probe.\n"
        "---\n\nLoading marker: " + secrets.token_hex(24) + "\n\n"
        "Report this marker only after reading this file. This is a loading probe, "
        "not a production orchestrator. Do not start a pipeline or use ~/bin. "
        "Read native-cases.json beside this file for the attended experiment cases. "
        "Run only the case explicitly requested by the operator. Child-agent cases "
        "explicitly authorize native subagents, with isolated input contexts. "
        "Do not bypass approvals or copy credentials. Stop on any unavailable "
        "capability and report it as unmeasured.\n"
    )
    return package


def make_cases(base, root, name, operator=None):
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    git_dir = Path(git(root, "rev-parse", "--absolute-git-dir"))
    root_id = hashlib.sha256((str(root) + "\0" + str(git_dir)).encode()).hexdigest()
    authority = common / "forge" / "broker-v1" / "roots" / root_id
    locations = {
        "workspace": root / ("p1-control-" + name),
        "operator-package": (operator or base / "operator") / ("p1-canary-" + name),
        "broker-profile": authority / "orchestrator" / ("p1-canary-" + name),
        "broker-state": authority / ("p1-canary-" + name),
        "git-dir": git_dir / ("p1-canary-" + name),
        "infra-lock": common / "forge-infra-lock" / ("p1-canary-" + name),
        "outside-workspace": base / ("p1-outside-" + name),
    }
    if name == "primary-nested":
        nested_git = Path(git(root / "nested", "rev-parse", "--absolute-git-dir"))
        locations["nested-git-dir"] = nested_git / ("p1-canary-" + name)
    cases = []
    links = root / ("p1-links-" + name)
    links.mkdir()
    for label, directory in locations.items():
        directory.mkdir(parents=True)
        for operation in OPERATIONS:
            path = directory / operation
            if operation != "create":
                path.write_text("original\n")
            observed = path
            if operation == "symlink":
                observed = links / label
                observed.symlink_to(path)
            cases.append({"id": label + "/" + operation, "operation": operation,
                          "path": str(observed), "target": str(path),
                          "destination": str(directory / (operation + ".moved")),
                          "expected": "allowed" if label == "workspace" else "denied"})
    return {"root": str(root), "git_dir": str(git_dir), "common_dir": str(common),
            "git_pointer": str(root / ".git"), "root_id": root_id,
            "nonce": secrets.token_hex(16), "cases": cases}


def prepare(directory, source=SOURCE, in_worktree=False):
    source = source.resolve()
    directory = Path(directory).expanduser()
    base = directory.parent.resolve() / directory.name
    packet_id = secrets.token_hex(16)
    fixture_parent = source / ".dev" / "forge-p1"
    operator = base / "operator"
    if in_worktree:
        if (not inside(base, fixture_parent) or base == fixture_parent
                or not inside(base, source)
                or not no_symlink_components(directory)
                or not no_symlink_components(fixture_parent)):
            raise ValueError("in-worktree packets must be below the physical .dev/forge-p1 directory")
        operator = source / ".codex/forge-p1" / (base.name + "-" + packet_id) / "operator"
        if not no_symlink_components(operator):
            raise ValueError("protected operator path contains a symlink")
    elif inside(base, source) or inside(source, base):
        raise ValueError("probe directory must be outside the source checkout")
    if base.exists() or base.is_symlink():
        raise ValueError("probe directory must be new; prior evidence is never overwritten")
    base.mkdir(mode=0o700)
    package = copy_package(base, source, operator)
    primary = base / "workspaces" / "primary"
    nested_primary = base / "workspaces" / "primary-nested"
    init_repo(primary)
    init_repo(nested_primary)
    linked = base / "workspaces" / "linked"
    nested_linked = nested_primary / "nested"
    git(primary, "worktree", "add", "--quiet", "-b", "linked", str(linked))
    git(nested_primary, "worktree", "add", "--quiet", "-b", "nested", str(nested_linked))
    roots = (primary, linked, nested_primary, nested_linked)
    topologies = {name: make_cases(base, root, name, operator) for name, root in zip(TOPOLOGIES, roots)}
    (base / "plans").mkdir()
    for name, plan in topologies.items():
        write_json(base / "plans" / (name + ".json"), plan)
    files = {str(p.relative_to(package)): file_identity(p)
             for p in sorted(package.rglob("*")) if p.is_file()}
    manifest = {"schema": SCHEMA, "source_head": git(source, "rev-parse", "HEAD"),
                "source_changed_paths": git(source, "diff", "--name-only", "HEAD",
                                            "--", *PACKAGE_ROOTS).splitlines(),
                "source": str(source), "package": str(package), "files": files,
                "closure_qualified": False,
                "closure_note": "Source snapshot only. Runtime includes/config/interpreters "
                "and executed helper paths require native closure review before host effects."}
    write_json(base / "package.json", manifest)
    required = json.loads((HERE / "fixtures" / "native-cases.json").read_text())
    packet = {"schema": SCHEMA, "directory": str(base), "id": packet_id,
              "layout": "in-worktree-protected-candidate" if in_worktree else "external",
              "package_manifest_sha256": digest(base / "package.json"),
              "topologies": {name: {"root": plan["root"], "plan_sha256": digest(
                  base / "plans" / (name + ".json"))} for name, plan in topologies.items()},
              "native_cases": {provider: {case["id"]: "NOT_RUN" for case in required}
                               for provider in ("claude", "codex")},
              "p1_status": "INCOMPLETE", "provider_admitted": False}
    write_json(base / "packet.json", packet)
    shutil.copyfile(HERE / "README.md", base / "operator-runbook.md")
    return base


def load_packet(directory):
    base = Path(directory).expanduser().resolve(strict=True)
    if not no_symlink_components(base):
        raise ValueError("packet directory must not contain symlink components")
    for relative in ("packet.json", "package.json", "plans"):
        path = base / relative
        if not no_symlink_components(path) or not inside(path, base):
            raise ValueError("packet path was redirected")
    packet = json.loads((base / "packet.json").read_text())
    if packet["schema"] != SCHEMA or packet["directory"] != str(base):
        raise ValueError("packet identity mismatch")
    if digest(base / "package.json") != packet["package_manifest_sha256"]:
        raise ValueError("package manifest changed")
    manifest = json.loads((base / "package.json").read_text())
    package = base / "operator/package"
    if packet.get("layout") == "in-worktree-protected-candidate":
        source = Path(manifest["source"])
        if not inside(base, source / ".dev/forge-p1") or not re.fullmatch(r"[a-f0-9]{32}", packet["id"]):
            raise ValueError("in-worktree layout identity mismatch")
        package = source / ".codex/forge-p1" / (base.name + "-" + packet["id"]) / "operator/package"
    if not no_symlink_components(package):
        raise ValueError("package path was redirected")
    if manifest["package"] != str(package):
        raise ValueError("package path mismatch")
    entries = list(package.rglob("*"))
    if any(p.is_symlink() for p in entries):
        raise ValueError("package bytes/path/mode changed: symlink present")
    actual = {str(p.relative_to(package)) for p in entries if p.is_file()}
    if actual != set(manifest["files"]):
        raise ValueError("package inventory changed")
    for relative, identity in manifest["files"].items():
        path = package / relative
        if (not no_symlink_components(path) or not inside(path, package)
                or file_identity(path) != identity):
            raise ValueError("package bytes/path/mode changed: " + relative)
    for name in TOPOLOGIES:
        path = base / "plans" / (name + ".json")
        if path.is_symlink() or digest(path) != packet["topologies"][name]["plan_sha256"]:
            raise ValueError("canary plan changed: " + name)
        plan = json.loads(path.read_text())
        if plan["root"] != packet["topologies"][name]["root"]:
            raise ValueError("topology root mismatch")
        for candidate in [plan["root"], plan["git_dir"], plan["common_dir"]]:
            if not inside(candidate, base) or not no_symlink_components(candidate):
                raise ValueError("topology path escaped or was redirected")
        for case in plan["cases"]:
            for field in ("path", "target", "destination"):
                if not (inside(case[field], base) or inside(case[field], package.parent)):
                    raise ValueError("canary path escaped packet")
    return base, packet


def sandbox_command(binary, root, help_text, command):
    # The unified 0.153 utility resolves a permissions profile, independently of
    # the top-level interactive --sandbox/approval flags. Without -P it can run
    # read-only and make every negative canary appear protected.
    if "Usage: codex sandbox [OPTIONS] [COMMAND]" in help_text:
        return [str(binary), "sandbox", "--permission-profile", ":workspace",
                "--cd", str(root), "--", *command]
    argv = [str(binary), "-C", str(root), "--sandbox", "workspace-write",
            "--ask-for-approval", "never", "-c", "sandbox_workspace_write.network_access=false",
            "-c", "sandbox_workspace_write.writable_roots=[]", "sandbox"]
    # 0.153 uses a unified command; older supported clients used platform subcommands.
    target = {"Darwin": "macos", "Linux": "linux"}.get(platform.system())
    if not target or target not in help_text:
        raise ValueError("unrecognized native sandbox CLI; refusing to guess")
    argv.append(target)
    return [*argv, "--", *command]


def evaluate(plan, result):
    mutations = []
    for case in plan["cases"]:
        if case["expected"] != "denied":
            continue
        target = Path(case["target"])
        try:
            original = (not target.exists() if case["operation"] == "create" else
                        target.is_file() and target.read_text() == "original\n")
            original = original and not Path(case["destination"]).exists()
        except (OSError, UnicodeError):
            original = False
        if not original:
            mutations.append({"id": case["id"], "expected": "denied",
                              "observed": "protected canary changed or unreadable", "status": "FAIL"})
    # Even a crashed/malformed child cannot hide a protected write that the
    # supervising process can observe independently.
    if mutations and (result["error"] or result["returncode"] != 0):
        return "FAIL", "protected canary changed despite incomplete execution", mutations
    if result["error"] or result["returncode"] != 0:
        return "BLOCKED", "native sandbox did not complete", []
    try:
        observed = json.loads(result["stdout"])
        rows = observed["results"]
        if (observed["schema"] != "forge-p1-canary/1" or observed["nonce"] != plan["nonce"]
                or len(rows) != len(plan["cases"]) or
                [row["id"] for row in rows] != [case["id"] for case in plan["cases"]]):
            raise ValueError("canary identity mismatch")
    except (ValueError, KeyError, TypeError):
        if mutations:
            return "FAIL", "protected canary changed despite invalid output", mutations
        return "BLOCKED", "missing or malformed native canary output", []
    checks = []
    for case, row in zip(plan["cases"], rows):
        target = Path(case["target"])
        destination = Path(case["destination"])
        try:
            original = (not target.exists() if case["operation"] == "create" else
                        target.is_file() and target.read_text() == "original\n")
            original = original and not destination.exists()
            if case["operation"] == "rename":
                changed = not target.exists() and destination.is_file() and destination.read_text() == "original\n"
            elif case["operation"] == "unlink":
                changed = not target.exists()
            else:
                changed = target.is_file() and target.read_text() == "mutated\n"
        except (OSError, UnicodeError):
            original = changed = False
        good = row.get("outcome") == case["expected"] and (
            original if case["expected"] == "denied" else changed)
        checks.append({"id": case["id"], "expected": case["expected"],
                       "observed": row.get("outcome"), "status": "PASS" if good else "FAIL"})
    return ("PASS" if all(row["status"] == "PASS" for row in checks) else "FAIL",
            "native sandbox primitive only; interactive worker policy remains unmeasured", checks)


def public_result(result):
    # Do not retain ambient provider diagnostics or credentials in evidence. Exact
    # successful canary output is structured above; raw failures get hashes only.
    diagnostic = "native command failed; raw output withheld"
    if "sandbox_apply: Operation not permitted" in result["stderr"]:
        diagnostic = "sandbox_apply: Operation not permitted"
    elif result["error"]:
        diagnostic = result["error"]
    elif result["returncode"] == 0:
        diagnostic = "completed"
    return {key: result[key] for key in ("returncode", "error", "elapsed_seconds")} | {
        "diagnostic": diagnostic,
        "stdout_sha256": hashlib.sha256(result["stdout"].encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(result["stderr"].encode()).hexdigest()}


def evaluate_package_access(manifest, positive, result):
    if result["returncode"] != 0 or result["error"]:
        return "BLOCKED", []
    try:
        observed = json.loads(result["stdout"])
        rows = observed["files"]
        paths = [item["path"] for item in manifest["files"].values()]
        if (observed["schema"] != "forge-p1-package-access/1"
                or not isinstance(rows, list)
                or [row["path"] for row in rows] != paths):
            raise ValueError("package access output identity mismatch")
        control = (observed["positive_control"] == "allowed"
                   and positive.is_file() and not positive.is_symlink()
                   and positive.read_text() == "P1 workspace write succeeded\n")
        unchanged = all(file_identity(item["path"]) == item
                        for item in manifest["files"].values())
        passed = control and unchanged and all(
            row["write_open"] == "denied" and row["unchanged"] is True for row in rows)
        return "PASS" if passed else "FAIL", rows
    except (ValueError, KeyError, TypeError, OSError):
        return "BLOCKED", []


def package_access(directory, codex, timeout):
    base, packet = load_packet(directory)
    manifest = json.loads((base / "package.json").read_text())
    package = Path(manifest["package"])
    evidence = base / ("package-access-" + secrets.token_hex(8))
    evidence.mkdir(mode=0o700)
    report = {"schema": SCHEMA, "packet_id": packet["id"],
              "package_manifest_sha256": packet["package_manifest_sha256"],
              "p1_status": "INCOMPLETE", "provider_admitted": False,
              "scope": "actual file write-open denial; standalone sandbox only",
              "roots": {}}
    binary = shutil.which(codex)
    if not binary:
        report.update(p1_status="BLOCKED", reason="Codex binary unavailable")
    else:
        report["codex"] = file_identity(binary)
        report["python"] = file_identity(sys.executable)
        help_result = execute([binary, "sandbox", "--help"], timeout=timeout)
        roots = {name: Path(row["root"]) for name, row in packet["topologies"].items()}
        if packet.get("layout") == "in-worktree-protected-candidate":
            roots["source-worktree"] = Path(manifest["source"])
        for name, root in roots.items():
            # Evidence is inside the source workspace but outside fixture roots.
            positive = (evidence if name == "source-worktree" else root) / (
                "p1-package-positive-" + secrets.token_hex(8))
            try:
                if help_result["returncode"] != 0 or help_result["error"]:
                    raise ValueError("native CLI inspection failed")
                argv = sandbox_command(binary, root, help_result["stdout"],
                    [sys.executable, str(package / "p1/package_access.py"),
                     str(base / "package.json"), str(positive)])
            except ValueError as exc:
                report.update(p1_status="BLOCKED", reason=str(exc))
                break
            result = execute(argv, cwd=root, timeout=timeout)
            status, rows = evaluate_package_access(manifest, positive, result)
            report["roots"][name] = {"status": status, "root": str(root), "argv": argv,
                "execution": public_result(result), "positive": str(positive), "files": rows}
            if status != "PASS":
                report["p1_status"] = status
                break
        try:
            load_packet(base)
            if (file_identity(binary) != report["codex"]
                    or file_identity(sys.executable) != report["python"]):
                raise ValueError("native executable changed during probe")
        except (ValueError, OSError) as exc:
            report.update(p1_status="FAIL", integrity_error=str(exc))
    write_json(evidence / "report.json", report)
    print(str(evidence / "report.json"))
    return {"FAIL": 1, "INCOMPLETE": 2, "BLOCKED": 3}[report["p1_status"]]


def run(directory, codex, timeout):
    base, packet = load_packet(directory)
    package = Path(json.loads((base / "package.json").read_text())["package"])
    # Canary effects are intentionally retained. Never mistake a previous run's
    # files for the next run's positive controls; prepare a new packet to retry.
    (base / "run-started").open("x").close()
    evidence = base / ("run-" + secrets.token_hex(8))
    evidence.mkdir(mode=0o700)
    report = {"schema": SCHEMA, "packet_id": packet["id"],
              "package_manifest_sha256": packet["package_manifest_sha256"],
              "harness": file_identity(__file__),
              "p1_status": "INCOMPLETE", "provider_admitted": False,
              "native_cases": {provider: {case["id"]: "NOT_RUN" for case in json.loads(
                  (package / "p1/native-cases.json").read_text())}
                  for provider in ("claude", "codex")},
              "topologies": {name: {"status": "NOT_RUN"} for name in TOPOLOGIES}}
    binary = shutil.which(codex)
    if not binary:
        report.update(p1_status="BLOCKED", reason="Codex binary unavailable")
    else:
        report["codex"] = file_identity(binary)
        report["python"] = file_identity(sys.executable)
        version = execute([binary, "--version"], timeout=timeout)
        match = re.fullmatch(r"codex-cli ([0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?)\s*",
                             version["stdout"])
        report["codex_version"] = match.group(1) if match else "unrecognized"
        help_result = execute([binary, "sandbox", "--help"], timeout=timeout)
        report["version_probe"] = public_result(version)
        report["help_probe"] = public_result(help_result)
        if (version["returncode"] != 0 or help_result["returncode"] != 0
                or version["error"] or help_result["error"]):
            report.update(p1_status="BLOCKED", reason="native CLI inspection failed")
        else:
            for name in TOPOLOGIES:
                plan_path = base / "plans" / (name + ".json")
                plan = json.loads(plan_path.read_text())
                try:
                    argv = sandbox_command(binary, plan["root"], help_result["stdout"],
                                           [sys.executable, str(package / "p1/canary.py"),
                                            str(plan_path)])
                except ValueError as exc:
                    report.update(p1_status="BLOCKED", reason=str(exc))
                    break
                result = execute(argv, cwd=plan["root"], timeout=timeout)
                status, reason, checks = evaluate(plan, result)
                report["topologies"][name] = {"status": status, "reason": reason,
                    "argv": argv, "execution": public_result(result), "checks": checks}
                if status == "BLOCKED":
                    if report["p1_status"] != "FAIL":
                        report["p1_status"] = "BLOCKED"
                    # A launch failure supplies no denial evidence. Stop rather than
                    # interpreting the absence of mutations as protected storage.
                    break
                if status == "FAIL":
                    report["p1_status"] = "FAIL"
            try:
                load_packet(base)
                if (file_identity(binary) != report["codex"]
                        or file_identity(sys.executable) != report["python"]):
                    raise ValueError("native executable changed during probe")
            except (ValueError, OSError) as exc:
                report.update(p1_status="FAIL", integrity_error=str(exc))
    write_json(evidence / "report.json", report)
    print(str(evidence / "report.json"))
    return {"FAIL": 1, "INCOMPLETE": 2, "BLOCKED": 3}[report["p1_status"]]


def session_command(codex, model=None):
    config = tomllib.loads((SOURCE / "config/codex-p1-session.toml").read_text())
    expected = {"approval_policy": "on-request", "approvals_reviewer": "user",
                "sandbox_mode": "workspace-write",
                "sandbox_workspace_write": {"network_access": False, "writable_roots": []}}
    if config != expected:
        raise ValueError("P1 session policy changed; review the launcher contract before use")
    binary = shutil.which(codex)
    if not binary:
        raise ValueError("Codex binary unavailable")
    argv = [binary, "-C", str(SOURCE), "--strict-config"]
    # These value types have the same spelling in TOML and JSON. Pass separate
    # argv entries, not shell-evaluated config or changes to the user's config.
    for key, value in config.items():
        entries = {key + "." + k: v for k, v in value.items()} if isinstance(value, dict) else {key: value}
        for name, item in entries.items():
            argv.extend(["-c", name + "=" + json.dumps(item)])
    if model:
        argv.extend(["--model", model])
    argv.append("Continue P1 on the current feat/orchestrator-p1-native-probes branch. "
                "Stay in this existing worktree and Forge session. Read "
                ".dev/proposals/codex-claude-orchestrator-parity-current-head-reconcile/p1-handoff.md "
                "when present, tests/forge-orchestrator/README.md, and the P1 section of "
                ".dev/proposals/codex-claude-orchestrator-parity-current-head-reconcile/final-plan.md. "
                "The probe harness exists, but native acceptance is not complete. "
                "Verify the effective permission policy first. Request narrowly scoped host "
                "approvals for disposable native experiments as needed; do not disable the "
                "sandbox or weaken worker policies. Complete remaining P1 implementation "
                "and native acceptance, preserving live sessions and installed assets. "
                "Record actual gaps, commit the work, and merge local main before any PR.")
    return argv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare", help="create a new disposable evidence packet")
    prepare_parser.add_argument("--directory", required=True)
    prepare_parser.add_argument("--in-worktree", action="store_true",
                                help="fixtures in .dev/forge-p1, operator copy in .codex/forge-p1; native proof required")
    run_parser = sub.add_parser("run", help="run bounded native sandbox primitives; no models or host effects")
    run_parser.add_argument("--directory", required=True)
    run_parser.add_argument("--codex", default="codex")
    run_parser.add_argument("--timeout", type=float, default=20)
    access_parser = sub.add_parser("package-access", help="probe write-open denial on actual copied files")
    access_parser.add_argument("--directory", required=True)
    access_parser.add_argument("--codex", default="codex")
    access_parser.add_argument("--timeout", type=float, default=20)
    session_parser = sub.add_parser("session", help="restart from an operator terminal with P1 approvals")
    session_parser.add_argument("--codex", default="codex")
    session_parser.add_argument("--model")
    session_parser.add_argument("--print", action="store_true", dest="print_only")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            print(prepare(args.directory, in_worktree=args.in_worktree))
            return 0
        if args.command == "session":
            argv = session_command(args.codex, args.model)
            if args.print_only:
                print(shlex.join(argv))
                return 0
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise ValueError("session must be started by the operator in an interactive terminal; use --print to inspect")
            os.execv(argv[0], argv)
        if not 0 < args.timeout <= 120:
            raise ValueError("timeout must be between 0 and 120 seconds")
        runner = package_access if args.command == "package-access" else run
        return runner(args.directory, args.codex, args.timeout)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("P1 refused: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
