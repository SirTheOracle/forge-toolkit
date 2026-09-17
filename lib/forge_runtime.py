"""Pinned runtime construction for protected candidate packages."""

import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig


SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
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


def inputs():
    """Bind staging identity to runtime inputs, not just the interpreter path."""
    tools = selected_tools()
    git_exec_path, helpers = git_helpers(tools["git"])
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
        if Path(resolved).stat().st_nlink != 1:
            # Some sealed-system tools have hardlink aliases. Pin independent
            # bytes instead of weakening the candidate no-hardlink invariant.
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
    git_exec_path, _ = git_helpers(tools["git"])
    metadata = {"schema": "forge-orchestrator-runtime/1", "python": interpreter,
                "stdlib": sysconfig.get_path("stdlib"), "tools": tools,
                "git_exec_path": str(git_exec_path),
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
    """Expose static closure gaps; never turn an inventory into native proof."""
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
    return {"status": "unsupported", "git_executable": metadata["tools"]["git"],
            "git_exec_path": metadata.get("git_exec_path"), "script_dependencies": scripts,
            "git_config_guard": str(Path(package) / "runtime/git_entry.py"),
            "blockers": ["Native subprocess closure has not been measured for this package.",
                         "The copied Git guard constrains repository config and helper selection; end-to-end native qualification remains required."]
                        + (["Script interpreter/import/include dependencies are not fully qualified."] if scripts else [])}


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
