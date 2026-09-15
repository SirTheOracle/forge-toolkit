"""Installed-path diagnostics never manufacture an admission from metadata."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="forge-p2-status-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "project"
        subprocess.run(["/usr/bin/git", "init", "-q", str(self.root)], check=True)

    def run_status(self, command=None, **extra_env):
        return subprocess.run([str(command or ROOT / "bin/forge"), "orchestrator-status", "--root", str(self.root)],
                              cwd=self.root, env=dict(os.environ, TMUX="", **extra_env),
                              text=True, capture_output=True, timeout=10)

    def test_absent_is_readonly_and_unverified(self):
        result = self.run_status()
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["state"], "absent")
        self.assertEqual(value["role"], "orchestrator")
        self.assertIsNone(value["provider"])
        self.assertEqual(value["admission"], "unverified")
        self.assertEqual(value["root"]["physical_root"], str(self.root))
        self.assertFalse((self.root / ".git/forge").exists())
        self.assertFalse((self.root / ".dev").exists())

    def test_symlinked_caller_resolves_toolkit_library_not_workspace_shadow(self):
        caller = self.base / "forge"
        caller.symlink_to(ROOT / "bin/forge")
        (self.root / "forge_orchestrator.py").write_text("raise RuntimeError('workspace shadow executed')\n")
        (self.root / "lib").mkdir()
        (self.root / "lib/forge_orchestrator.py").write_text("raise RuntimeError('workspace lib executed')\n")
        result = self.run_status(caller, PYTHONPATH=str(self.root), FORGE_BROKER_BIN="/nonexistent")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "absent")

    def test_missing_copied_library_fails_closed(self):
        toolkit = self.base / "toolkit"
        (toolkit / "bin").mkdir(parents=True)
        for name in ("forge", "forge-broker"):
            shutil.copy2(ROOT / "bin" / name, toolkit / "bin" / name)
        result = self.run_status(toolkit / "bin/forge")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ORCHESTRATOR_LIBRARY_UNAVAILABLE", result.stdout + result.stderr)
        self.assertFalse((self.root / ".git/forge").exists())

    def test_bridge_reads_live_root_without_audit_or_record_writes(self):
        tools = self.base / "tools"
        tools.mkdir()
        tmux = tools / "tmux"
        tmux.write_text("#!/bin/bash\n[ \"$1\" = display-message ] || exit 64\n"
                        "printf '%%0\\tfixture\\t%s\\t1\\t$1\\n' \"$TEST_ROOT\"\n")
        tmux.chmod(0o755)
        env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"],
                   TEST_ROOT=str(self.root), TMUX="/tmp/p2-fixture,123,0", TMUX_PANE="%0", TMUX_SESSION="fixture")
        result = subprocess.run([str(ROOT / "bin/forge-bridge"), "orchestrator-status"], cwd=self.root,
                                env=env, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["root"]["physical_root"], str(self.root))
        self.assertFalse((self.root / ".dev").exists())
        self.assertFalse((self.root / ".git/forge").exists())


if __name__ == "__main__":
    unittest.main()
