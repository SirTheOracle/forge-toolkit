"""Local opt-in release contract; all provider processes are non-model doubles."""

import copy
import json
from pathlib import Path
import subprocess
import unittest

from host_fixture import Fixture, ROOT, host


class LocalReleaseTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(tmux=True)
        self.addCleanup(self.f.close)
        self.f.qualify_double(local_release=True)

    def register(self, *flags, provider="codex", entry=None):
        args = ["orchestrator-profile", "register", "--root", str(self.f.root),
                "--from", str(self.f.candidate_path), "--expect-revision", "0", *flags]
        if provider:
            args.extend(["--provider", provider])
        return self.f.run(*args, entry=entry or self.f.ctx.management_entry)

    def test_registration_requires_explicit_acceptance_provider_and_operator_entry(self):
        result = self.register()
        self.assertIn("LOCAL_VALIDATION_ACCEPTANCE_REQUIRED", result.stderr)
        result = self.register("--accept-local-validation", provider=None)
        self.assertIn("PROVIDER_REQUIRED", result.stderr)
        for entry in (ROOT / "bin/forge", ROOT / "bin/forge-orchestrator-host"):
            result = self.register("--accept-local-validation", entry=entry)
            self.assertIn("OPERATOR_ENTRY_REQUIRED", result.stderr)
        self.assertIsNone(self.f.ctx.store.profile())

    def test_first_registration_leaves_unbound_legacy_session_unchanged(self):
        f = self.f
        subprocess.run([f.tmux, "-L", f.socket, "new-session", "-d", "-s", "legacy",
                        "-c", str(f.root), "/bin/bash", "--noprofile", "--norc"], check=True)
        command = [f.tmux, "-L", f.socket, "list-panes", "-t", "legacy", "-F", "#{pane_id}"]
        before = subprocess.check_output(command, text=True)
        result = self.register("--accept-local-validation")
        self.assertIn("HANDOFF_REQUIRED", result.stderr)
        self.assertIsNone(f.ctx.store.profile())
        self.assertIsNone(f.ctx.store.current())
        self.assertEqual(subprocess.check_output(command, text=True), before)

    def test_both_profiles_default_claude_and_keep_unverified_grade_through_disable(self):
        original = copy.deepcopy(self.f.candidate)
        for provider in ("claude", "codex"):
            self.f.candidate = copy.deepcopy(original)
            self.f.candidate["providers"] = {provider: original["providers"][provider]}
            self.f.candidate["on_spawn"]["supported_providers"] = [provider]
            self.f.qualify_double(local_release=True)
            registered = self.register("--accept-local-validation", provider=provider)
            self.assertEqual(registered.returncode, 0, registered.stderr)
        for provider in ("claude", "codex"):
            result = self.f.run("start", "--root", str(self.f.root), "--provider", provider,
                                "--session", "local-" + provider, "--no-attach", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["validation"], "local")
        result = self.f.run("start", "--root", str(self.f.root), "--session", "local-claude", "--no-attach")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["provider"], "claude")
        self.assertEqual(json.loads(result.stdout)["validation"], "local")
        current = self.f.ctx.store.current()
        self.assertEqual(current["record"]["evidence"]["native_policy"], "unverified")
        result = self.f.run("rollout", "--root", str(self.f.root), "--provider", "claude", "--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout)
        self.assertEqual(status["scope"], "attended-local-release")
        self.assertEqual(status["native_qualification"], "not-run")
        self.assertEqual(status["full_workflow_parity"], "not-established")
        result = self.f.run("orchestrator-profile", "disable", "--root", str(self.f.root),
                            "--provider", "claude", "--expect-revision", "1", entry=self.f.ctx.management_entry)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.f.run("guard", "--root", str(self.f.root), "--session", "local-claude")
        self.assertIn("PROFILE_DISABLED", result.stderr)
        self.assertEqual(self.f.ctx.store.current(), current)
        self.assertTrue(self.f.ctx.store.profile("codex")["profile"]["enabled"])

    def test_explicit_codex_launch_and_ensure_preserve_local_grade_without_hot_switch(self):
        registered = self.register("--accept-local-validation")
        self.assertEqual(registered.returncode, 0, registered.stderr)
        args = ("start", "--root", str(self.f.root), "--session", "local-codex", "--no-attach")
        result = self.f.run(*args, "--provider", "codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["validation"], "local")
        current = self.f.ctx.store.current()
        ensured = self.f.run(*args)
        self.assertEqual(ensured.returncode, 0, ensured.stderr)
        self.assertEqual(json.loads(ensured.stdout)["provider"], "codex")
        self.assertEqual(json.loads(ensured.stdout)["validation"], "local")
        refused = self.f.run(*args, "--provider", "claude")
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(self.f.ctx.store.current(), current)

    def test_local_evidence_cannot_claim_native_conformance(self):
        candidate = self.f.candidate
        path = Path(candidate["conformance"]["path"])
        report = json.loads(path.read_text())
        report["schema"] = "forge-orchestrator-qualification/1"
        report["checks"].update(native_policy="pass", management_entry="pass")
        path.write_text(json.dumps(report))
        candidate["conformance"] = host.file_identity(path)
        with self.assertRaisesRegex(host.Refusal, "validation grade differs"):
            host.validate_candidate(self.f.ctx, candidate)


if __name__ == "__main__":
    unittest.main()
