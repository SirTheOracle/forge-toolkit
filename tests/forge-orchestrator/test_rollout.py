"""Local rollout contracts; fixture evidence cannot certify provider models."""

import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from host_fixture import Fixture, ROOT, host
import acceptance


class AcceptanceReportTests(unittest.TestCase):
    def test_skill_suite_cannot_hide_a_python_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "python3"
            fake.write_text("#!/bin/sh\nexit 23\n")
            fake.chmod(0o700)
            result = subprocess.run(["/bin/bash", str(ROOT / "tests/adversarial-skills/run.sh")],
                                    env=dict(os.environ, PATH=directory + ":/usr/bin:/bin"),
                                    text=True, capture_output=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout.count("Python checks failed (exit 23)"), 3)

    def test_local_success_never_mints_native_or_issue_qualification(self):
        snapshot = {"sha256": "a" * 64}
        suites = {name: {"status": "pass"} for name in acceptance.SUITES}
        report = acceptance.finish_report(snapshot, snapshot, suites)
        self.assertEqual(report["status"], "pass")
        self.assertFalse(report["admission_evidence"])
        self.assertEqual(report["full_workflow_parity"], "not-established")
        self.assertTrue(all(row["status"] == "not-run" for row in report["native"].values()))
        self.assertTrue(all(row["original_defect_disposition"] == "not-requalified"
                            for row in report["affected_paths"].values()))

    def test_source_drift_failure_skip_or_missing_suite_cannot_pass(self):
        snapshot = {"sha256": "a" * 64}
        suites = {name: {"status": "pass"} for name in acceptance.SUITES}
        self.assertEqual(acceptance.finish_report(snapshot, {"sha256": "b" * 64}, suites)["status"], "fail")
        for state in ("fail", "interrupted", "not-run", None):
            changed = copy.deepcopy(suites)
            if state is None:
                del changed[acceptance.SUITES[0]]
            else:
                changed[acceptance.SUITES[0]]["status"] = state
            self.assertEqual(acceptance.finish_report(snapshot, snapshot, changed)["status"], "fail")


class RolloutTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(tmux=True)
        self.addCleanup(self.f.close)

    def inspect(self, *args):
        return subprocess.run([str(ROOT / "bin/forge"), "orchestrator-rollout", "--root", str(self.f.root), *args],
                              env=self.f.env, text=True, capture_output=True, timeout=20)

    def test_absent_profile_is_diagnostic_not_a_registration(self):
        before = sorted(str(p) for p in self.f.ctx.protected.rglob("*"))
        for flags, expected in (((), 0), (("--check",), 3)):
            result = self.inspect(*flags)
            self.assertEqual(result.returncode, expected, result.stderr)
            report = json.loads(result.stdout)
            self.assertFalse(report["eligible"])
            self.assertEqual(report["reason"]["code"], "ORCHESTRATOR_NOT_QUALIFIED")
            self.assertEqual(report["default_provider"], "claude")
            self.assertEqual(report["full_workflow_parity"], "not-established")
        self.assertEqual(before, sorted(str(p) for p in self.f.ctx.protected.rglob("*")))
        self.assertIsNone(self.f.ctx.store.profile())

    def test_exact_profile_inputs_are_reported_without_claiming_full_parity(self):
        profile = self.f.register()
        for provider in ("claude", "codex"):
            result = self.inspect("--provider", provider, "--check")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = json.loads(result.stdout)
            self.assertTrue(report["eligible"])
            self.assertEqual(report["boundary"], "new-session")
            self.assertEqual(report["profile"]["sha256"], profile["sha256"])
            self.assertEqual(report["evidence"]["inputs"], self.f.candidate["inputs"])
            self.assertEqual(report["evidence"]["conformance"], self.f.candidate["conformance"])
            self.assertEqual(report["evidence"]["provider"]["evidence"]["workflows"], ["contract-fixture-only"])
            self.assertEqual(report["full_workflow_parity"], "not-established")
        self.assertEqual(self.f.ctx.store.profile(), profile)
        self.assertIsNone(self.f.ctx.store.current())

    def test_local_acceptance_report_cannot_register_a_profile(self):
        self.f.candidate_path.write_text(json.dumps(acceptance.finish_report(
            {"sha256": "a" * 64}, {"sha256": "a" * 64},
            {name: {"status": "pass"} for name in acceptance.SUITES})))
        result = self.f.run("orchestrator-profile", "register", "--root", str(self.f.root),
                            "--from", str(self.f.candidate_path), "--expect-revision", "0",
                            entry=self.f.ctx.management_entry)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("INVALID_PROFILE", result.stderr)
        self.assertIsNone(self.f.ctx.store.profile())

    def test_drift_and_disable_remain_readonly_refusals(self):
        profile = self.f.register()
        path = self.f.package / "assets/core"
        path.write_text("changed")
        result = self.inspect("--check")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(json.loads(result.stdout)["reason"]["code"], "INPUT_CHANGED")
        self.assertEqual(self.f.ctx.store.profile(), profile)
        disabled = self.f.run("orchestrator-profile", "disable", "--root", str(self.f.root),
                              "--expect-revision", "1", entry=self.f.ctx.management_entry)
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        result = self.inspect("--check")
        self.assertEqual(json.loads(result.stdout)["reason"]["code"], "PROFILE_DISABLED")
        self.assertEqual(self.f.ctx.store.profile_snapshot(profile["sha256"]), profile["profile"])

    def test_invalid_provider_never_falls_back(self):
        result = self.inspect("--provider", "other")
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(self.f.ctx.store.profile())


if __name__ == "__main__":
    unittest.main()
