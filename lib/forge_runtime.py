"""Pinned runtime construction for protected candidate packages."""

import importlib.metadata
import hashlib
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile


SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
GIT_HELPERS = ("git-remote-http", "git-remote-https", "git-upload-pack", "git-fetch-pack",
               "git-index-pack", "git-unpack-objects", "git-pack-objects", "git-rev-list")
TOOLS = ("bash", "sh", "zsh", "env", "git", "tmux", "gh", "rg", "awk", "basename", "cat", "cp", "date", "dirname", "grep",
         "head", "hostname", "mkdir", "mktemp", "mv", "pgrep", "ps", "readlink", "rm",
         "sed", "sleep", "sort", "tail", "tee", "tr", "wc", "cut", "find", "ls", "chmod", "cmp", "diff", "kill", "touch", "uname", "xargs")


def selected_tools():
    result = {}
    for name in TOOLS:
        selected = shutil.which(name, path=None if name in {"tmux", "gh", "rg"} else SYSTEM_PATH)
        if not selected:
            raise ValueError("missing candidate runtime command: " + name)
        if name == "git" and sys.platform == "darwin":
            # /usr/bin/git is an Apple developer-tool dispatcher, not the Git
            # executable. Bind the selected toolchain without ambient overrides.
            selected = subprocess.check_output(["/usr/bin/xcrun", "--find", "git"],
                env={"PATH": SYSTEM_PATH}, text=True, timeout=15).strip()
            if not Path(selected).is_absolute() or not Path(selected).is_file():
                raise ValueError("cannot resolve the native Git executable")
        result[name] = selected
    return result


def git_helpers(binary):
    directory = subprocess.check_output([str(binary), "--exec-path"], env={"PATH": SYSTEM_PATH},
                                        text=True, timeout=15).strip()
    path = Path(directory)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("Git helper directory is unavailable")
    return path, sorted(p.absolute() for p in path.rglob("*") if p.is_file() or p.is_symlink())


def provider_dependencies(binaries):
    """Explicit auxiliary executables used by the supported native CLI builds."""
    paths = []
    codex_host = Path(binaries["codex"]).parent / "codex-code-mode-host"
    if codex_host.exists():
        paths.append(codex_host)
    if sys.platform == "darwin":
        paths.append(Path("/usr/bin/sandbox-exec"))
    return sorted(paths)


def python_files(stdlib):
    return [path for path in Path(stdlib).rglob("*")
            if "site-packages" not in path.relative_to(stdlib).parts
            and path.is_file() and path.suffix in {".py", ".pyc", ".so", ".dylib", ".zip"}]


def sealed_system_path(path):
    """Root-owned file on a read-only (sealed system) volume."""
    info = os.stat(path)
    return info.st_uid == 0 and bool(os.statvfs(path).f_flag & os.ST_RDONLY)


def implicit_inputs(stdlib=None):
    """Include absence: adding an earlier Python zip or global startup file drifts.

    Verification passes the package's recorded stdlib, so the result describes the
    packaged interpreter, not whichever Python runs the check (forge-start uses
    /usr/bin/python3 while staging may run under another python3).
    """
    stdlib = Path(stdlib or sysconfig.get_path("stdlib")).resolve(strict=True)
    match = re.fullmatch(r"python(\d+)\.(\d+)", stdlib.name)
    if not match:
        raise ValueError("unrecognized stdlib directory: " + str(stdlib))
    etc = Path("/private/etc" if sys.platform == "darwin" else "/etc")
    paths = [stdlib.parent / ("python" + match.group(1) + match.group(2) + ".zip"),
             etc / "zshenv", etc / "zsh/zshenv", etc / "codex"]
    paths.append(Path("/Library/Application Support/ClaudeCode") if sys.platform == "darwin" else etc / "claude-code")
    result = []
    for start in paths:
        pending = [start]
        while pending:
            path = pending.pop()
            if path.is_symlink():
                raise ValueError("aliased native startup input: " + str(path))
            if path.is_dir():
                result.append({"path": str(path), "kind": "directory"})
                pending.extend(sorted(path.iterdir(), reverse=True))
            elif path.is_file():
                result.append({"path": str(path), "kind": "file",
                               "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            elif path.exists():
                raise ValueError("nonregular native startup input: " + str(path))
            else:
                result.append({"path": str(path), "kind": "absent"})
    return sorted(result, key=lambda item: item["path"])


def inputs():
    """Bind staging identity to runtime inputs, not just the interpreter path."""
    tools = selected_tools()
    git_exec_path, _ = git_helpers(tools["git"])
    helpers = [git_exec_path / name for name in GIT_HELPERS]
    yaml = Path(importlib.metadata.distribution("PyYAML").locate_file("yaml"))
    if yaml.is_symlink():
        raise ValueError("symlinked PyYAML source")
    vendor = sorted(yaml.rglob("*.py"))
    if any(path.is_symlink() for path in yaml.rglob("*")):
        raise ValueError("symlinked PyYAML input")
    dependencies = native_dependencies([sys.executable, *tools.values(),
                                        *python_files(sysconfig.get_path("stdlib")), *vendor, *helpers])
    if dependencies["unresolved"]:
        raise ValueError("unresolved runtime inputs: " + repr(dependencies["unresolved"]))
    return {"tools": tools, "git_exec_path": str(git_exec_path), "git_helpers": list(map(str, helpers)),
            "implicit_inputs": implicit_inputs(),
            "aliases": dependencies["aliases"],
            "files": [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                      for path in dependencies["files"]],
            "os_cached_libraries": dependencies["os_cached_libraries"]}


def os_identity():
    info = os.uname()
    result = {name: getattr(info, name) for name in ("sysname", "release", "version", "machine")}
    if sys.platform == "darwin":
        build = Path("/System/Library/CoreServices/SystemVersion.plist")
        result["system_version_sha256"] = hashlib.sha256(build.read_bytes()).hexdigest()
    return result


def stage(package, *, yaml_source=None):
    package = Path(package)
    runtime = package / "runtime"
    runtime.mkdir()
    (runtime / "bin").mkdir()
    (runtime / "vendor").mkdir()
    yaml_source = Path(yaml_source or importlib.metadata.distribution("PyYAML").locate_file("yaml"))
    if yaml_source.is_symlink() or not (yaml_source / "__init__.py").is_file():
        raise ValueError("PyYAML source must be a regular package")
    for src in sorted(yaml_source.rglob("*")):
        if src.is_symlink():
            raise ValueError("PyYAML source contains a symlink")
        # Pure Python avoids an optional native extension and its extra closure.
        if src.is_file() and src.suffix == ".py":
            dst = runtime / "vendor/yaml" / src.relative_to(yaml_source)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    shutil.copyfile(Path(__file__).parent / "forge_python_entry.py", runtime / "python_entry.py")
    shutil.copyfile(Path(__file__).parent / "forge_git_entry.py", runtime / "git_entry.py")
    interpreter = str(Path(sys.executable).resolve(strict=True))
    launch = [interpreter, "-I", "-S", "-B", str(runtime / "python_entry.py")]
    wrapper = runtime / "bin/python3"
    wrapper.write_text("#!/bin/sh\nexec " + shlex.join(launch) + ' "$@"\n')
    wrapper.chmod(0o755)
    # Only the SHA-256 form used by copied helpers is supported. Keep hashing in
    # the audited Python closure instead of adding Perl and its import paths.
    shutil.copyfile(Path(__file__).parent / "forge_sha256sum.py", runtime / "sha256sum.py")
    sha = runtime / "bin/shasum"
    sha.write_text("#!/bin/sh\nexec " + shlex.join([*launch, str(runtime / "sha256sum.py")]) + ' "$@"\n')
    sha.chmod(0o755)
    tools = {}
    for name, selected in selected_tools().items():
        resolved = str(Path(selected).resolve(strict=True))
        if Path(resolved).stat().st_nlink != 1 and not sealed_system_path(resolved):
            # Hard-linked tools off the sealed volume get independent pinned bytes.
            # Sealed-system tools must run in place: macOS launch constraints
            # SIGKILL a copied Apple platform binary (grep, rm, pgrep, readlink).
            copied = runtime / "native" / name
            copied.parent.mkdir(exist_ok=True)
            shutil.copyfile(resolved, copied)
            copied.chmod(0o500)
            resolved = str(copied)
        tools[name] = resolved
        stub = runtime / "bin" / name
        command = shlex.join([*launch, str(runtime / "git_entry.py")]) if name == "git" else shlex.quote(resolved)
        stub.write_text("#!/bin/sh\nexec " + command + ' "$@"\n')
        stub.chmod(0o755)
    original_git_exec_path, _ = git_helpers(tools["git"])
    git_exec_path = runtime / "git-core"
    git_exec_path.mkdir()
    # The guard permits builtins and HTTP(S) fetch only. Do not expose unrelated
    # Perl/shell helpers, even if the upstream Git installation ships them.
    for name in GIT_HELPERS:
        source = original_git_exec_path / name
        with source.open("rb") as stream:
            if stream.read(2) == b"#!":
                raise ValueError("scripted Git helper is not supported: " + name)
        target = git_exec_path / name
        shutil.copyfile(source, target)
        target.chmod(0o500)
    metadata = {"schema": "forge-orchestrator-runtime/1", "python": interpreter,
                "stdlib": sysconfig.get_path("stdlib"), "tools": tools,
                "git_exec_path": str(git_exec_path),
                "implicit_inputs": implicit_inputs(),
                "python_flags": ["-I", "-S", "-B"],
                "shims": {"shasum": "isolated Python; only -a 256 and at most one input"},
                "source_yaml": str(yaml_source), "qualified": False,
                "note": "Staged only; dependency integrity and denial require measured evidence."}
    (runtime / "runtime.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return metadata


def environment(package, home, *, tmux=None, pane=None):
    package, home = Path(package), Path(home)
    # No inherited shell startup, Python path, dyld, Git config, Forge authority,
    # credentials, test seams or external helper overrides enter copied helpers.
    env = {"PATH": str(package / "runtime/bin"), "HOME": str(home),
           "LC_ALL": "C", "LANG": "C", "TERM": "xterm-256color",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
           "FORGE_WATCH_TRIGGER": "0"}
    env.update(FORGE_ASSETS_REQUIRED="1", FORGE_CONSTRAINTS_MODE="enforce",
               FORGE_CLAUDE_SKILLS_DIR=str(package / "skills"),
               FORGE_CODEX_SKILLS_DIR=str(package / "codex-skills"),
               FORGE_BIN=str(package / "bin/forge"),
               FORGE_CODEX_DOCTOR_BIN=str(package / "bin/forge"),
               CODEX_HOME=str(home / ".codex"), CLAUDE_CONFIG_DIR=str(home / ".claude"),
               DISABLE_AUTOUPDATER="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    for key in ("USER", "LOGNAME"):
        if key in os.environ:
            env[key] = os.environ[key]
    for key, name in (("FORGE_BRIDGE_BIN", "forge-bridge"), ("FORGE_FORGE_BIN", "forge"),
                      ("FORGE_BROKER_BIN", "forge-broker"), ("FORGE_WATCH_BIN", "forge-watch"),
                      ("FORGE_START_BIN", "forge-start")):
        env[key] = str(package / "bin" / name)
    for key, relative in (("FORGE_PROMPTS_DIR", "prompts"),
                          ("FORGE_IDLE_PROMPTS_FILE", "config/idle-prompts.yml"),
                          ("FORGE_RESET_CAPABILITY_FILE", "config/reset-capability.yml"),
                          ("FORGE_DIGESTS_DIR", "digests")):
        env[key] = str(package / relative)
    if tmux is not None:
        env["TMUX"] = tmux
    if pane is not None:
        env["TMUX_PANE"] = pane
    return env


def dependency_files(package):
    metadata = json.loads((Path(package) / "runtime/runtime.json").read_text())
    files = {Path(metadata["python"]), *(Path(p) for p in metadata["tools"].values())}
    stdlib = Path(metadata["stdlib"])
    # Bytecode can execute even with -B; include it, excluding user site-packages.
    # Preserve logical names too: a new alias to an existing file changes imports.
    files.update(path.absolute() for path in python_files(stdlib))
    if "git_exec_path" in metadata:
        files.update(p.absolute() for p in Path(metadata["git_exec_path"]).rglob("*") if p.is_file() or p.is_symlink())
    return sorted(files)


def audit(package, receipt):
    """Check the reachable executable set; measurement is a separate step."""
    metadata = json.loads((Path(package) / "runtime/runtime.json").read_text())
    executables = {Path(p) for p in metadata["tools"].values()}
    executables.update(Path(p) for p in receipt["worker_binaries"].values())
    if "git_exec_path" in metadata:
        executables.update(p for p in Path(metadata["git_exec_path"]).rglob("*") if p.is_file())
    scripts = []
    for path in sorted(executables):
        with path.open("rb") as stream:
            if stream.read(2) == b"#!":
                scripts.append(str(path))
    blockers = []
    implicit = metadata.get("implicit_inputs")
    if implicit != implicit_inputs(metadata.get("stdlib")):
        blockers.append("Implicit startup inputs changed.")
    if any(item["kind"] != "absent" for item in implicit or []):
        blockers.append("Custom global startup/archive/policy inputs require a separate closure audit.")
    if scripts:
        blockers.append("Script interpreter/import/include dependencies are not qualified.")
    helper_dir = Path(metadata.get("git_exec_path", "/nonexistent"))
    if helper_dir != Path(package) / "runtime/git-core" or {p.name for p in helper_dir.iterdir()} != set(GIT_HELPERS):
        blockers.append("Git helper directory is not the closed copied helper set.")
    if metadata.get("python_flags") != ["-I", "-S", "-B"]:
        blockers.append("Python startup is not isolated.")
    return {"status": "unsupported" if blockers else "not-run", "static_status": "fail" if blockers else "pass",
            "git_executable": metadata["tools"]["git"],
            "git_exec_path": metadata.get("git_exec_path"), "script_dependencies": scripts,
            "git_config_guard": str(Path(package) / "runtime/git_entry.py"),
            "blockers": blockers}


def measure(package, receipt, directory):
    """Run actual copied control entries under hostile startup/override inputs."""
    package = Path(package)
    result = audit(package, receipt)
    observations = []
    if result["static_status"] != "pass":
        return result
    try:
        root = receipt["root"]["physical_root"]
        with tempfile.TemporaryDirectory(prefix=".forge-runtime-", dir=root) as temporary:
            attack = Path(temporary)
            sentinel = attack / "executed"
            payload = "#!/bin/sh\nprintf injected > " + shlex.quote(str(sentinel)) + "\n"
            evil = attack / "override"
            evil.write_text(payload)
            evil.chmod(0o700)
            for name in (".bashrc", ".bash_profile", ".zshenv", ".profile"):
                (attack / name).write_text(payload)
            (attack / "sitecustomize.py").write_text("open(" + repr(str(sentinel)) + ",'w').write('python')\n")
            (attack / ".gitconfig").write_text("[include]\npath = /nonexistent/runtime-probe\n")
            env = dict(receipt["environment"], HOME=str(attack), PATH=str(attack), BASH_ENV=str(evil),
                       ENV=str(evil), ZDOTDIR=str(attack), PYTHONPATH=str(attack),
                       GIT_CONFIG_GLOBAL=str(attack / ".gitconfig"),
                       GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.fsmonitor", GIT_CONFIG_VALUE_0=str(evil),
                       FORGE_BROKER_BIN=str(evil), FORGE_BRIDGE_BIN=str(evil), FORGE_FORGE_BIN=str(evil))
            cases = [
                ("isolated-python", [str(package / "runtime/bin/python3"), "-c",
                  "import json,sys,yaml; print(json.dumps({'isolated':sys.flags.isolated,'no_site':sys.flags.no_site,"
                  "'no_bytecode':sys.flags.dont_write_bytecode,'yaml':yaml.__file__}))"]),
                ("guarded-git", [str(package / "runtime/bin/git"), "-C", root, "status", "--porcelain"]),
                ("copied-router", [str(package / "bin/forge"), "orchestrator-profile", "inspect", "--root", root]),
                ("copied-host", [str(package / "bin/forge-orchestrator-host"), "orchestrator-profile", "inspect", "--root", root]),
                ("copied-broker", [str(package / "bin/forge-broker"), "status", "--root", root, "--json"])]
            for label, argv in cases:
                cp = subprocess.run(argv, cwd=root, env=env, text=True, capture_output=True, timeout=30)
                passed = cp.returncode == 0 and not sentinel.exists()
                output = cp.stdout
                if label == "copied-broker":
                    value = json.loads(cp.stdout)
                    passed = (not sentinel.exists() and
                              ((cp.returncode == 0 and value.get("live") is True)
                               or (cp.returncode == 3 and value.get("live") is False)))
                    output = json.dumps({key: value.get(key) for key in ("state", "live", "runtime_state")})
                if label == "isolated-python" and passed:
                    value = json.loads(cp.stdout)
                    passed = value == {"isolated": 1, "no_site": 1, "no_bytecode": 1,
                                       "yaml": str(package / "runtime/vendor/yaml/__init__.py")}
                observations.append({"case": label, "pass": passed, "exit_code": cp.returncode,
                                     "stdout": output, "stdout_sha256": hashlib.sha256(cp.stdout.encode()).hexdigest(),
                                     "stderr": cp.stderr})
        result.update(status="pass" if all(row["pass"] for row in observations) else "fail",
                      observations=observations,
                      scope="Copied control startup and overrides; provider execution is separately required by native checks.")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result.update(status="fail", error=str(exc), observations=observations)
    with (Path(directory) / "runtime-probe.json").open("x") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
    return result


def native_dependencies(files):
    """Resolve Mach-O linked inputs; unresolved install names fail qualification."""
    pending, seen, cached, unresolved = list(files), set(), set(), set()
    aliases = {}
    magic = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe",
             b"\xca\xfe\xba\xbf", b"\xce\xfa\xed\xfe"}
    while pending:
        raw = Path(pending.pop()).absolute()
        for component in (*reversed(raw.parents), raw):
            if component.is_symlink():
                aliases[str(component)] = os.readlink(component)
        path = raw.resolve(strict=True)
        if path in seen:
            continue
        seen.add(path)
        with path.open("rb") as stream:
            if stream.read(4) not in magic:
                continue
        result = subprocess.run(["/usr/bin/otool", "-L", str(path)], check=True,
                                text=True, capture_output=True)
        for line in result.stdout.splitlines():
            if not line.startswith("\t") or " (compatibility version " not in line:
                continue
            name = line.strip().split(" (compatibility version ", 1)[0]
            if name.startswith(("/usr/lib/", "/System/Library/")):
                cached.add(name)
                continue
            if name.startswith("@loader_path/"):
                dependency = path.parent / name.removeprefix("@loader_path/")
            elif name.startswith("/"):
                dependency = Path(name)
            else:
                unresolved.add(str(path) + ":" + name)
                continue
            if not dependency.is_file():
                unresolved.add(str(path) + ":" + name)
            else:
                pending.append(dependency)
    return {"files": sorted(seen), "os_cached_libraries": sorted(cached),
            "unresolved": sorted(unresolved), "aliases": aliases}
