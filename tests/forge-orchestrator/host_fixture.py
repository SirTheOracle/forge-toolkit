"""Disposable contract doubles. These never certify a production provider."""

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader("p2_host", str(ROOT / "bin/forge-orchestrator-host"))
spec = importlib.util.spec_from_loader(loader.name, loader)
host = importlib.util.module_from_spec(spec)
loader.exec_module(host)


class Fixture:
    def __init__(self, *, tmux=False):
        self.temp = tempfile.TemporaryDirectory(prefix="forge-p2-host-")
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "project"
        subprocess.run(["/usr/bin/git", "init", "-q", str(self.root)], check=True)
        self.ctx = host.Context(str(self.root))
        self.env = dict(os.environ, TMUX="", FORGE_WATCH_TRIGGER="0")
        self.socket = None
        if tmux:
            self.tmux = str(Path(shutil.which("tmux")).resolve())
            self.socket = "forge-p2-host-" + uuid.uuid4().hex[:12]
            subprocess.run([self.tmux, "-L", self.socket, "-f", "/dev/null", "new-session", "-d", "-s", "sentinel",
                            "-c", str(self.base), "/bin/bash", "--noprofile", "--norc"], check=True)
            result = subprocess.check_output([self.tmux, "-L", self.socket, "display-message", "-p", "-t", "sentinel:",
                                              "#{socket_path},#{pid},0"], text=True).strip()
            self.env["TMUX"] = result
        host.prepare_management(self.ctx)
        self.package = self.ctx.store.path / "packages" / "fixture"
        for name in ("bin", "lib", "assets"):
            (self.package / name).mkdir(parents=True)
        for name in ("forge-start", "forge-orchestrator-host", "forge-broker", "forge-bridge"):
            shutil.copy2(ROOT / "bin" / name, self.package / "bin" / name)
        shutil.copy2(ROOT / "lib/forge_orchestrator.py", self.package / "lib/forge_orchestrator.py")
        source = self.base / "provider.c"
        source.write_text('#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include <unistd.h>\n'
                          'int main(int argc,char **argv){if(argc>1 && !strcmp(argv[1],"--version")){'
                          'puts("fixture-provider/1");return 0;}if(getenv("P2_SERVER_POISON"))return 17;while(1)pause();}\n')
        self.provider = self.package / "bin/provider"
        subprocess.run(["/usr/bin/cc", "-o", str(self.provider), str(source)], check=True, capture_output=True)
        forge = self.package / "bin/forge"
        forge.write_text("#!/usr/bin/python3\nimport json,os,sys\nfrom pathlib import Path\n"
                         "cmd=sys.argv[1]\n"
                         "if cmd=='codex-doctor': print('launch_ready=true')\n"
                         "elif cmd=='codex-launch': os.execv(" + repr(str(self.provider)) + ",['provider','--worker',*sys.argv[2:]])\n"
                         "elif cmd=='orchestrator-hook':\n"
                         " p=Path(sys.argv[3])/'.claude/forge-project.yml'\n"
                         " d=json.loads(p.read_text()) if p.exists() else {}\n"
                         " print(json.dumps(d.get('forge',{}).get('control_center',{}).get('on_spawn',[])))\n"
                         "elif cmd=='orchestrator-pending':\n"
                         " p=Path(sys.argv[3])/'.dev/proposals'\n"
                         " rows=[json.loads(f.read_text()) for f in p.glob('*/forge-log.yml')]\n"
                         " print(json.dumps({'pending':any(not e.get('response') for r in rows for e in r.get('entries',[]))}))\n"
                         "elif cmd=='root-assets': pass\n"
                         "else: sys.exit(64)\n")
        forge.chmod(0o755)
        for name in ("package", "core", "adapter", "helpers"):
            (self.package / "assets" / name).write_text("contract fixture: " + name)
        paths = list(path for path in self.package.rglob("*") if path.is_file())
        paths += [Path("/bin/bash"), Path("/usr/bin/env"), Path(shutil.which("tmux")).resolve(), Path(sys.executable).resolve()]
        system = subprocess.check_output(["/usr/bin/python3", "-c", "import os,sys; print(os.path.realpath(sys.executable))"], text=True).strip()
        paths.append(Path(system))
        system_image = subprocess.check_output(["/usr/bin/python3", "-c",
            "import ctypes,os,sys; "
            "b=ctypes.create_string_buffer(4096); "
            "ctypes.CDLL('/usr/lib/libproc.dylib').proc_pidpath(os.getpid(),b,len(b)); "
            "print(os.fsdecode(b.value))"], text=True).strip() if sys.platform == "darwin" else system
        paths.append(Path(system_image).resolve())
        paths.append(Path(host.process(os.getpid())["executable"]))
        paths = sorted(set(paths))
        self.candidate = {
            "schema": "forge-orchestrator-candidate/1", "root": self.ctx.identity,
            "on_spawn": {"kind": "builtin", "argv": [str(self.package / "bin/forge-start"), "--populate-existing"],
                         "supported_providers": ["claude", "codex"], "transport_version": 1},
            "files": [host.file_identity(path) for path in paths],
            "inputs": {name: host.file_identity(self.package / "assets" / name) for name in ("package", "core", "adapter", "helpers")},
            "tools": {"forge": str(forge), "forge_start": str(self.package / "bin/forge-start"),
                      "host": str(self.package / "bin/forge-orchestrator-host"), "broker": str(self.package / "bin/forge-broker"),
                      "bridge": str(self.package / "bin/forge-bridge"), "tmux": str(Path(shutil.which("tmux")).resolve()),
                      "shell": "/bin/bash", "claude_worker": str(self.provider)},
            "providers": {provider: {"argv": [str(self.provider), "--fixture-provider", provider, "--root", "{root}", "--session", "{session}"],
                                      "executable": host.file_identity(self.provider), "version": "fixture-provider/1",
                                      "evidence": {"reports": ["f" * 64], "loading": "behavioral", "native_policy": "pass", "workflows": ["contract-fixture-only"]}}
                          for provider in ("claude", "codex")},
            "conformance": {}, "project_config_sha256": None,
        }
        self.candidate_path = self.base / "candidate.json"
        self.qualify_double()

    def qualify_double(self):
        """Test fixture, never an operator-facing qualification command."""
        report = {"schema": "forge-orchestrator-qualification/1", "root": self.ctx.identity,
                  "candidate_sha256": host.records.digest(host.candidate_body(self.candidate)),
                  "checks": {name: "pass" for name in ("native_policy", "management_entry", "hook_transport", "runtime_closure", "topology")},
                  "management_entry_sha256": host.file_identity(self.ctx.management_entry)["sha256"],
                  "management_sha256": host.records.digest(host.management_inputs(self.ctx)),
                  "topology_sha256": host.records.digest(host.topology(self.ctx)),
                  "supported_providers": sorted(self.candidate["providers"]), "fixture_only": True}
        directory = self.ctx.store.path / "reports"
        directory.mkdir(exist_ok=True)
        path = directory / (host.records.digest(report) + ".json")
        path.write_bytes(host.records.encoded(report))
        self.candidate["conformance"] = host.file_identity(path)
        self.candidate_path.write_bytes(host.records.encoded(self.candidate))

    def run(self, *args, entry=None, env=None):
        return subprocess.run([str(entry or ROOT / "bin/forge-orchestrator-host"), *args],
                              cwd=self.root, env=dict(self.env, **(env or {})), text=True, capture_output=True, timeout=150)

    def register(self, revision=0):
        result = self.run("orchestrator-profile", "register", "--root", str(self.root), "--from", str(self.candidate_path),
                          "--expect-revision", str(revision), entry=self.ctx.management_entry)
        if result.returncode:
            raise AssertionError(result.stderr)
        return json.loads(result.stdout)

    def close(self):
        try:
            if (self.ctx.protected / "pid.json").exists():
                self.run("stop", "--root", str(self.root), entry=self.package / "bin/forge-broker")
        finally:
            if self.socket:
                subprocess.run([self.tmux, "-L", self.socket, "kill-server"], capture_output=True)
            self.temp.cleanup()
