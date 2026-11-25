"""Worker panes run on the operator's own profiles, like legacy forge-start.

Only the orchestrator seat uses the isolated native namespace; the four worker
panes must share the operator's normal Claude/Codex subscription login.
"""

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import pwd
import re
from types import SimpleNamespace
import unittest

TREE = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader("host_worker_profile", str(TREE / "bin/forge-orchestrator-host"))
spec = importlib.util.spec_from_loader(loader.name, loader)
host = importlib.util.module_from_spec(spec)
loader.exec_module(host)

ISOLATED = {"HOME": "/protected/home", "CLAUDE_CONFIG_DIR": "/protected/auth/claude",
            "CODEX_HOME": "/protected/auth/codex", "PATH": "/pkg/runtime/bin", "LANG": "C", "LC_ALL": "C",
            "GIT_CONFIG_GLOBAL": "/dev/null"}
OPERATOR = ("PATH=/opt/homebrew/bin:/usr/bin:/bin\nLANG=en_US.UTF-8\nSSH_AUTH_SOCK=/tmp/agent\n"
            "TMUX=/stale,1,0\n-REMOVED\nSHLVL=3\n")


def env_of(argv):
    assert argv[:2] == ["/usr/bin/env", "-i"]
    pairs = [a for a in argv[2:] if re.match(r"^[A-Z_][A-Z0-9_]*=", a)]
    return dict(a.split("=", 1) for a in pairs)


class WorkerProfile(unittest.TestCase):
    def runtime(self):
        def tmux(*args, check=True):
            if args[:2] == ("show-environment", "-g"):
                return SimpleNamespace(returncode=0, stdout=OPERATOR)
            return SimpleNamespace(returncode=0, stdout="/sock,1,0\n")
        rt = SimpleNamespace(env=dict(ISOLATED, FORGE_BRIDGE_BIN="/pkg/bin/forge-bridge"),
                             ctx=SimpleNamespace(root=Path("/root")), tmux=tmux)
        rt.operator_env = lambda: host.Runtime.operator_env(rt)
        return rt

    def test_orchestrator_seat_keeps_isolated_namespace(self):
        env = env_of(host.Runtime.child_argv(self.runtime(), "s", ["codex"], pane="%0", role="orchestrator"))
        self.assertEqual(env["HOME"], ISOLATED["HOME"])
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], ISOLATED["CLAUDE_CONFIG_DIR"])
        self.assertEqual(env["CODEX_HOME"], ISOLATED["CODEX_HOME"])
        self.assertNotIn("LC_ALL", env)
        self.assertEqual(env["LANG"], "en_US.UTF-8")

    def test_workers_use_operator_profiles(self):
        env = env_of(host.Runtime.child_argv(self.runtime(), "s", ["claude"], pane="%1", role="worker",
                                             operator_profile=True))
        self.assertEqual(env["HOME"], pwd.getpwuid(os.getuid()).pw_dir)
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        self.assertNotIn("CODEX_HOME", env)
        self.assertEqual(env["FORGE_ROLE"], "worker")
        # Operator PATH (keychain via /usr/bin/security), UTF-8 locale, real git config.
        self.assertEqual(env["PATH"], "/opt/homebrew/bin:/usr/bin:/bin")
        self.assertEqual(env["LANG"], "en_US.UTF-8")
        self.assertNotIn("LC_ALL", env)
        self.assertNotIn("GIT_CONFIG_GLOBAL", env)
        self.assertEqual(env["SSH_AUTH_SOCK"], "/tmp/agent")
        self.assertEqual(env["TMUX"], "/sock,1,0")
        self.assertNotIn("SHLVL", env)
        self.assertNotIn("REMOVED", env)
        self.assertEqual(env["FORGE_BRIDGE_BIN"], "/pkg/bin/forge-bridge")

    def test_every_worker_launch_opts_into_operator_profile(self):
        source = (TREE / "bin/forge-orchestrator-host").read_text()
        claude = source[source.index('rt.candidate["tools"]["claude_worker"]'):][:400]
        codex = source[source.index('"codex-launch", "--root"'):][:400]
        # The packaged forge wrapper re-pins the isolated environment; workers run its body.
        self.assertIn('worker_forge(', source[source.index('"codex-launch", "--root"') - 200:][:200])
        self.assertIn("operator_profile=True", claude)
        self.assertIn("operator_profile=True", codex)

    def test_worker_forge_runs_packaged_body(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            forge = Path(tmp) / "forge"
            forge.write_text("#!/bin/sh\n")
            self.assertEqual(host.worker_forge(str(forge)), [str(forge)])
            (Path(tmp) / ".forge-body").write_text("#!/bin/bash\n")
            self.assertEqual(host.worker_forge(str(forge)), ["/bin/bash", str(Path(tmp) / ".forge-body")])


if __name__ == "__main__":
    unittest.main()
