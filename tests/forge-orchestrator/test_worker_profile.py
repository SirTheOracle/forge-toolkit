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
            "CODEX_HOME": "/protected/auth/codex", "PATH": "/usr/bin:/bin", "LANG": "C"}


def env_of(argv):
    assert argv[:2] == ["/usr/bin/env", "-i"]
    pairs = [a for a in argv[2:] if re.match(r"^[A-Z_][A-Z0-9_]*=", a)]
    return dict(a.split("=", 1) for a in pairs)


class WorkerProfile(unittest.TestCase):
    def runtime(self):
        return SimpleNamespace(env=dict(ISOLATED), ctx=SimpleNamespace(root=Path("/root")),
                               tmux=lambda *a: SimpleNamespace(stdout="/sock,1,0\n"))

    def test_orchestrator_seat_keeps_isolated_namespace(self):
        env = env_of(host.Runtime.child_argv(self.runtime(), "s", ["codex"], pane="%0", role="orchestrator"))
        self.assertEqual(env["HOME"], ISOLATED["HOME"])
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], ISOLATED["CLAUDE_CONFIG_DIR"])
        self.assertEqual(env["CODEX_HOME"], ISOLATED["CODEX_HOME"])

    def test_workers_use_operator_profiles(self):
        env = env_of(host.Runtime.child_argv(self.runtime(), "s", ["claude"], pane="%1", role="worker",
                                             operator_profile=True))
        self.assertEqual(env["HOME"], pwd.getpwuid(os.getuid()).pw_dir)
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        self.assertNotIn("CODEX_HOME", env)
        self.assertEqual(env["FORGE_ROLE"], "worker")

    def test_every_worker_launch_opts_into_operator_profile(self):
        source = (TREE / "bin/forge-orchestrator-host").read_text()
        claude = source[source.index('rt.candidate["tools"]["claude_worker"]'):][:400]
        codex = source[source.index('"codex-launch", "--root"'):][:400]
        self.assertIn("operator_profile=True", claude)
        self.assertIn("operator_profile=True", codex)


if __name__ == "__main__":
    unittest.main()
