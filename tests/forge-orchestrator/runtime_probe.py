"""P1 fixture runtime construction, not a production installer or admission API."""

import importlib.metadata
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig


SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
TOOLS = ("bash", "sh", "env", "git", "tmux", "awk", "cat", "date", "dirname", "grep",
         "head", "hostname", "mkdir", "mv", "pgrep", "ps", "readlink", "rm",
         "sed", "sleep", "sort", "tail", "tee", "tr", "wc")


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
    shutil.copyfile(Path(__file__).parent / "fixtures/python_entry.py", runtime / "python_entry.py")
    interpreter = str(Path(sys.executable).resolve(strict=True))
    launch = [interpreter, "-I", "-S", "-B", str(runtime / "python_entry.py")]
    wrapper = runtime / "bin/python3"
    wrapper.write_text("#!/bin/sh\nexec " + shlex.join(launch) + ' "$@"\n')
    wrapper.chmod(0o755)
    tools = {}
    for name in TOOLS:
        selected = shutil.which(name, path=SYSTEM_PATH)
        if name == "tmux":
            selected = shutil.which(name)
        if not selected:
            raise ValueError("missing P1 runtime command: " + name)
        resolved = str(Path(selected).resolve(strict=True))
        tools[name] = resolved
        stub = runtime / "bin" / name
        stub.write_text("#!/bin/sh\nexec " + shlex.quote(resolved) + ' "$@"\n')
        stub.chmod(0o755)
    metadata = {"schema": "forge-p1-runtime/1", "python": interpreter,
                "stdlib": sysconfig.get_path("stdlib"), "tools": tools,
                "python_flags": ["-I", "-S", "-B"],
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
    for key, name in (("FORGE_BRIDGE_BIN", "forge-bridge"), ("FORGE_FORGE_BIN", "forge"),
                      ("FORGE_BROKER_BIN", "forge-broker"), ("FORGE_WATCH_BIN", "forge-watch"),
                      ("FORGE_START_BIN", "forge-start")):
        env[key] = str(package / "bin" / name)
    for key, relative in (("FORGE_PROMPTS_DIR", "prompts"),
                          ("FORGE_IDLE_PROMPTS_FILE", "config/idle-prompts.yml"),
                          ("FORGE_RESET_CAPABILITY_FILE", "config/reset-capability.yml"),
                          ("FORGE_DIGESTS_DIR", "p1")):
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
    # Inventory the importable distribution, not user site-packages or caches.
    for path in stdlib.rglob("*"):
        relative = path.relative_to(stdlib)
        if any(part in {"site-packages", "__pycache__"} for part in relative.parts):
            continue
        if path.is_file() and path.suffix in {".py", ".so", ".dylib", ".zip"}:
            files.add(path.resolve(strict=True))
    return sorted(files)


def native_dependencies(files):
    """Resolve Mach-O linked inputs; unresolved install names fail qualification."""
    pending, seen, cached, unresolved = list(files), set(), set(), set()
    magic = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe",
             b"\xca\xfe\xba\xbf", b"\xce\xfa\xed\xfe"}
    while pending:
        path = Path(pending.pop()).resolve(strict=True)
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
            elif dependency.resolve() not in seen:
                pending.append(dependency)
    return {"files": sorted(seen), "os_cached_libraries": sorted(cached),
            "unresolved": sorted(unresolved)}
