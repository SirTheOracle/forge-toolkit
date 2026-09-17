"""Opt-in native sandbox checks. No model calls or production qualification writes."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from host_fixture import Fixture, ROOT, host
from native_probe import sandbox_command


@unittest.skipUnless(os.environ.get("FORGE_ORCHESTRATOR_NATIVE_MANAGEMENT") == "1",
                     "opt-in native Codex sandbox measurement")
class ManagementSandboxTests(unittest.TestCase):
    def measure(self, root, ctx, candidate_path, env):
        binary = shutil.which("codex")
        self.assertIsNotNone(binary)
        help_text = subprocess.check_output([binary, "sandbox", "--help"], text=True)
        # Qualified launchers do not inherit ambient writable temp roots.
        # The primary repository must not sit inside a granted TMPDIR.
        env = {key: value for key, value in env.items() if key != "TMPDIR"}

        def run(argv):
            return subprocess.run(sandbox_command(binary, root, help_text, argv), cwd=root,
                                  env=env, text=True, capture_output=True, timeout=45)

        before = ctx.store.profile()
        ordinary = root / "ordinary-edit.txt"
        positive = run([sys.executable, "-I", "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('allowed')", str(ordinary)])
        self.assertEqual(positive.returncode, 0, positive.stderr)
        self.assertEqual(ordinary.read_text(), "allowed")
        for entry in (ctx.management_entry, ROOT / "bin/forge"):
            result = run([str(entry), "orchestrator-profile", "probe", "--root", str(root)])
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual(ctx.store.profile(), before)
            self.assertTrue("Operation not permitted" in result.stderr or "Permission denied" in result.stderr
                            or "OPERATOR_ENTRY_REQUIRED" in result.stderr, result.stderr)
            self.assertEqual(list(ctx.management.glob(".probe-*")), [])
        for entry, action in ((ctx.management_entry, "disable"), (ROOT / "bin/forge", "disable"),
                              (ctx.management_entry, "register"), (ROOT / "bin/forge", "register")):
            argv = [str(entry), "orchestrator-profile", action, "--root", str(root), "--expect-revision", "1"]
            if action == "register":
                argv += ["--from", str(candidate_path)]
            result = run(argv)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual(ctx.store.profile(), before, result.stderr)
            self.assertTrue("Operation not permitted" in result.stderr or "Permission denied" in result.stderr
                            or "OPERATOR_ENTRY_REQUIRED" in result.stderr, result.stderr)
        target = ctx.store.path / "profile.json"
        script = "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('{}')"
        result = run([sys.executable, "-I", "-c", script, str(target)])
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(ctx.store.profile(), before)

    def test_primary_management_denial_with_positive_workspace_control(self):
        f = Fixture()
        self.addCleanup(f.close)
        f.register()
        self.measure(f.root, f.ctx, f.candidate_path, f.env)

    def test_linked_management_denial_with_positive_workspace_control(self):
        f = Fixture()
        self.addCleanup(f.close)
        subprocess.run(["git", "-C", str(f.root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "--allow-empty", "-qm", "fixture"], check=True)
        linked = f.base / "linked"
        subprocess.run(["git", "-C", str(f.root), "worktree", "add", "-qb", "linked", str(linked)], check=True)
        ctx = host.Context(str(linked))
        host.prepare_management(ctx)
        f.ctx, f.root = ctx, linked
        f.candidate["root"] = ctx.identity
        f.qualify_double()
        f.register()
        self.measure(linked, ctx, f.candidate_path, f.env)


if __name__ == "__main__":
    unittest.main()
