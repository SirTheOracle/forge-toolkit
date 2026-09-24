"""Root-scoped registration/CAS tests, not production runtime qualification."""

import copy
import json
import os
from pathlib import Path
import subprocess
import unittest

from host_fixture import Fixture, ROOT, host


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture(tmux=True)
        self.addCleanup(self.fixture.close)

    def test_operator_entry_register_disable_retains_snapshots(self):
        f = self.fixture
        first = f.register()
        self.assertEqual(first["profile"]["revision"], 1)
        disabled = f.run("orchestrator-profile", "disable", "--root", str(f.root), "--expect-revision", "1",
                         entry=f.ctx.management_entry)
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        self.assertFalse(json.loads(disabled.stdout)["profile"]["enabled"])
        self.assertEqual(f.ctx.store.profile_snapshot(first["sha256"]), first["profile"])
        route = f.run("route", "--root", str(f.root), "--provider", "codex")
        self.assertIn("PROFILE_DISABLED", route.stderr)

    def test_single_provider_candidates_coexist_without_replacing_claude(self):
        f = self.fixture
        original = copy.deepcopy(f.candidate)
        registered = {}
        for provider in ("claude", "codex"):
            f.candidate = copy.deepcopy(original)
            f.candidate["providers"] = {provider: original["providers"][provider]}
            f.candidate["on_spawn"]["supported_providers"] = [provider]
            f.qualify_double()
            result = f.run("orchestrator-profile", "register", "--provider", provider,
                           "--root", str(f.root), "--from", str(f.candidate_path),
                           "--expect-revision", "0", entry=f.ctx.management_entry)
            self.assertEqual(result.returncode, 0, result.stderr)
            registered[provider] = json.loads(result.stdout)
        self.assertEqual(f.ctx.store.profile("claude"), registered["claude"])
        self.assertEqual(f.ctx.store.profile("codex"), registered["codex"])
        for provider in ("claude", "codex"):
            result = f.run("route", "--root", str(f.root), "--provider", provider)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = f.run("route", "--root", str(f.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = f.run("orchestrator-profile", "disable", "--provider", "codex", "--root", str(f.root),
                       "--expect-revision", "1", entry=f.ctx.management_entry)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(f.ctx.store.profile("claude"), registered["claude"])


    def test_management_probe_never_publishes_or_changes_profile(self):
        f = self.fixture
        for registered in (False, True):
            if registered:
                f.register()
            before = f.ctx.store.profile()
            result = f.run("orchestrator-profile", "probe", "--root", str(f.root), entry=f.ctx.management_entry)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"management_write": True, "profile_changed": False})
            self.assertEqual(f.ctx.store.profile(), before)
            self.assertEqual(list(f.ctx.management.glob(".probe-*")), [])
            denied = f.run("orchestrator-profile", "probe", "--root", str(f.root), entry=ROOT / "bin/forge")
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("OPERATOR_ENTRY_REQUIRED", denied.stderr)

    def test_generic_forge_and_source_helper_cannot_register_even_with_role_stamp(self):
        f = self.fixture
        args = ("orchestrator-profile", "register", "--root", str(f.root), "--from", str(f.candidate_path), "--expect-revision", "0")
        for entry in (ROOT / "bin/forge", ROOT / "bin/forge-orchestrator-host"):
            result = f.run(*args, entry=entry, env={"FORGE_ROLE": "operator", "FORGE_ORCHESTRATOR_OPERATOR": "1"})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("OPERATOR_ENTRY_REQUIRED", result.stderr)
        self.assertIsNone(f.ctx.store.profile())

    def test_stale_revision_does_not_change_profile(self):
        f = self.fixture
        first = f.register()
        result = f.run("orchestrator-profile", "disable", "--root", str(f.root), "--expect-revision", "0",
                       entry=f.ctx.management_entry)
        self.assertIn("PROFILE_REVISION_CONFLICT", result.stderr)
        self.assertEqual(f.ctx.store.profile(), first)

    def test_pending_reader_distinguishes_terminal_open_and_unsafe_logs(self):
        f = self.fixture
        path = f.root / ".dev/proposals/example/forge-log.yml"
        path.parent.mkdir(parents=True)
        def inspect():
            return subprocess.run([str(ROOT / "bin/forge"), "orchestrator-pending", "--root", str(f.root)],
                                  env=f.env, capture_output=True, text=True, timeout=10)
        for response, expected in ((None, True), ("FORGE_DONE", False)):
            path.write_text(json.dumps({"entries": [{"response": response}]}))
            result = inspect()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"pending": expected})
        path.write_text('{"entries":[{"response":true}]}')
        self.assertNotEqual(inspect().returncode, 0)
        path.unlink()
        path.symlink_to(f.root / "absent-log")
        self.assertNotEqual(inspect().returncode, 0)

    def test_changed_helper_invalidates_new_admission(self):
        f = self.fixture
        f.register()
        (f.package / "assets/core").write_text("changed")
        result = f.run("route", "--root", str(f.root), "--provider", "codex")
        self.assertIn("INPUT_CHANGED", result.stderr)

    def test_project_claim_and_wrong_report_cannot_grant_support(self):
        f = self.fixture
        bad = copy.deepcopy(f.candidate)
        bad["on_spawn"]["argv"].append("--extra")
        f.candidate_path.write_text(json.dumps(bad))
        result = f.run("orchestrator-profile", "register", "--root", str(f.root), "--from", str(f.candidate_path),
                       "--expect-revision", "0", entry=f.ctx.management_entry)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(f.ctx.store.profile())

    def test_workspace_qualification_report_refuses(self):
        f = self.fixture
        local = f.root / "report.json"
        local.write_bytes(Path(f.candidate["conformance"]["path"]).read_bytes())
        f.candidate["conformance"] = host.file_identity(local)
        f.candidate_path.write_text(json.dumps(f.candidate))
        result = f.run("orchestrator-profile", "register", "--root", str(f.root), "--from", str(f.candidate_path),
                       "--expect-revision", "0", entry=f.ctx.management_entry)
        self.assertIn("QUALIFICATION_UNPROTECTED", result.stderr)

    def test_changed_management_closure_refuses(self):
        f = self.fixture
        (f.ctx.management / "lib/forge_orchestrator.py").chmod(0o700)
        (f.ctx.management / "lib/forge_orchestrator.py").write_text((f.ctx.management / "lib/forge_orchestrator.py").read_text() + "\n# changed\n")
        result = f.run("orchestrator-profile", "register", "--root", str(f.root), "--from", str(f.candidate_path),
                       "--expect-revision", "0", entry=f.ctx.management_entry)
        self.assertIn("INPUT_CHANGED", result.stderr)


if __name__ == "__main__":
    unittest.main()
