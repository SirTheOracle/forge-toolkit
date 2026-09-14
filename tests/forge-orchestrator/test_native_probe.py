#!/usr/bin/env python3
"""Hermetic harness tests; no native-provider capability claims."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("native_probe", HERE / "native_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-p1-test-")
        self.base = Path(self.tmp.name).resolve()
        self.source = self.base / "source"
        probe.init_repo(self.source)
        (self.source / "bin").mkdir()
        (self.source / "bin/forge").write_text("#!/bin/bash\nexit 0\n")
        (self.source / "bin/forge").chmod(0o755)
        probe.git(self.source, "add", "bin/forge")
        probe.git(self.source, "commit", "--quiet", "-m", "tracked helper")
        (self.source / "bin/forge.bak-private").write_text("not a package input")

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self):
        return probe.prepare(self.base / "packet", self.source)

    def test_prepare_is_new_external_and_non_destructive(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            probe.prepare(self.source / "bad", self.source)
        self.assertFalse((self.source / "bad").exists())
        packet = self.prepare()
        with self.assertRaisesRegex(ValueError, "must be new"):
            probe.prepare(packet, self.source)
        self.assertTrue((packet / "packet.json").exists())
        self.assertTrue((self.source / "bin/forge.bak-private").exists())

    def test_source_package_excludes_untracked_and_retains_identity(self):
        packet = self.prepare()
        base, data = probe.load_packet(packet)
        self.assertEqual(base, packet)
        self.assertEqual(data["p1_status"], "INCOMPLETE")
        self.assertFalse(data["provider_admitted"])
        files = json.loads((packet / "package.json").read_text())["files"]
        self.assertIn("bin/forge", files)
        self.assertNotIn("bin/forge.bak-private", files)
        self.assertEqual(files["bin/forge"]["mode"], 0o755)

    def test_tracked_symlink_is_refused(self):
        (self.source / "bin/link").symlink_to("forge")
        probe.git(self.source, "add", "bin/link")
        with self.assertRaisesRegex(ValueError, "non-regular"):
            self.prepare()

    def test_matrix_has_real_git_topologies_and_positive_controls(self):
        packet = self.prepare()
        plans = {name: json.loads((packet / "plans" / (name + ".json")).read_text())
                 for name in probe.TOPOLOGIES}
        self.assertEqual(plans["primary"]["common_dir"], plans["linked"]["common_dir"])
        self.assertNotEqual(plans["primary"]["root_id"], plans["linked"]["root_id"])
        self.assertTrue(Path(plans["linked"]["git_pointer"]).is_file())
        self.assertTrue(Path(plans["primary"]["git_pointer"]).is_dir())
        self.assertTrue(probe.inside(plans["nested-linked"]["root"], plans["primary-nested"]["root"]))
        for plan in plans.values():
            controls = [c for c in plan["cases"] if c["expected"] == "allowed"]
            self.assertEqual({c["operation"] for c in controls}, set(probe.OPERATIONS))
            self.assertTrue(all(probe.inside(c["target"], packet) for c in plan["cases"]))

    def test_package_content_mode_and_inventory_drift_refuse(self):
        packet = self.prepare()
        helper = packet / "operator/package/bin/forge"
        helper.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "bytes/path/mode"):
            probe.load_packet(packet)
        helper.chmod(0o755)
        helper.write_text("changed")
        with self.assertRaisesRegex(ValueError, "bytes/path/mode"):
            probe.load_packet(packet)
        extra = packet / "operator/package/extra"
        extra.write_text("extra")
        with self.assertRaisesRegex(ValueError, "inventory"):
            probe.load_packet(packet)

    def test_plan_change_refuses_before_execution(self):
        packet = self.prepare()
        (packet / "plans/primary.json").write_text("{}")
        with patch.object(probe, "execute") as execute:
            with self.assertRaisesRegex(ValueError, "canary plan changed"):
                probe.run(packet, "codex", 2)
            execute.assert_not_called()

    def test_package_symlink_redirection_refuses(self):
        packet = self.prepare()
        helper = packet / "operator/package/bin/forge"
        helper.unlink()
        helper.symlink_to(self.source / "bin/forge")
        with self.assertRaisesRegex(ValueError, "bytes/path/mode"):
            probe.load_packet(packet)

    def test_extra_broken_symlink_is_not_ignored(self):
        packet = self.prepare()
        (packet / "operator/package/broken").symlink_to("missing")
        with self.assertRaisesRegex(ValueError, "symlink"):
            probe.load_packet(packet)

    def test_sandbox_argv_retains_worker_policy_and_detects_cli_shape(self):
        command = probe.sandbox_command("/codex", self.base,
            "Usage: codex sandbox [OPTIONS] [COMMAND]...", ["/python", "canary.py"])
        self.assertEqual(command[-3:], ["--", "/python", "canary.py"])
        self.assertIn("sandbox_workspace_write.network_access=false", command)
        self.assertIn("sandbox_workspace_write.writable_roots=[]", command)
        self.assertNotIn("--strict-config", command)
        self.assertEqual(command[command.index("--ask-for-approval") + 1], "never")
        with patch.object(probe.platform, "system", return_value="Darwin"):
            old = probe.sandbox_command("/codex", self.base, "Commands: macos linux", ["/python"])
        self.assertEqual(old[-4:], ["sandbox", "macos", "--", "/python"])
        with self.assertRaisesRegex(ValueError, "unrecognized"):
            probe.sandbox_command("/codex", self.base, "unknown help", ["/python"])

    def test_sandbox_failure_and_empty_output_are_not_denials(self):
        packet = self.prepare()
        plan = json.loads((packet / "plans/primary.json").read_text())
        for result in ({"returncode": 71, "error": None, "stdout": ""},
                       {"returncode": 0, "error": None, "stdout": "{}"},
                       {"returncode": 0, "error": None, "stdout": "marker"}):
            status, _, checks = probe.evaluate(plan, result)
            self.assertEqual(status, "BLOCKED")
            self.assertEqual(checks, [])

    def test_unsandboxed_canary_detects_protected_writes(self):
        packet = self.prepare()
        plan_path = packet / "plans/primary.json"
        plan = json.loads(plan_path.read_text())
        result = probe.execute([sys.executable, str(HERE / "fixtures/canary.py"), str(plan_path)])
        status, _, checks = probe.evaluate(plan, result)
        self.assertEqual(status, "FAIL")
        self.assertTrue(all(c["status"] == "PASS" for c in checks if c["expected"] == "allowed"))
        self.assertTrue(all(c["status"] == "FAIL" for c in checks if c["expected"] == "denied"))

    def test_claimed_denial_does_not_hide_actual_mutation(self):
        packet = self.prepare()
        plan_path = packet / "plans/primary.json"
        plan = json.loads(plan_path.read_text())
        result = probe.execute([sys.executable, str(HERE / "fixtures/canary.py"), str(plan_path)])
        observed = json.loads(result["stdout"])
        for row, case in zip(observed["results"], plan["cases"]):
            row["outcome"] = case["expected"]
        result["stdout"] = json.dumps(observed)
        self.assertEqual(probe.evaluate(plan, result)[0], "FAIL")

    def test_crashed_or_malformed_child_cannot_hide_protected_write(self):
        packet = self.prepare()
        plan_path = packet / "plans/primary.json"
        plan = json.loads(plan_path.read_text())
        result = probe.execute([sys.executable, str(HERE / "fixtures/canary.py"), str(plan_path)])
        result["stdout"] = "invalid output"
        self.assertEqual(probe.evaluate(plan, result)[0], "FAIL")
        result["returncode"] = 1
        self.assertEqual(probe.evaluate(plan, result)[0], "FAIL")

    def test_primitive_success_still_does_not_prove_p1(self):
        packet = self.prepare()
        plan_path = packet / "plans/primary.json"
        plan = json.loads(plan_path.read_text())
        controls = dict(plan, cases=[c for c in plan["cases"] if c["expected"] == "allowed"])
        control_path = packet / "control.json"
        probe.write_json(control_path, controls)
        result = probe.execute([sys.executable, str(HERE / "fixtures/canary.py"), str(control_path)])
        observed = json.loads(result["stdout"])
        observed["results"] += [{"id": c["id"], "outcome": "denied"}
                                for c in plan["cases"] if c["expected"] == "denied"]
        result["stdout"] = json.dumps(observed)
        self.assertEqual(probe.evaluate(plan, result)[0], "PASS")
        self.assertFalse(json.loads((packet / "packet.json").read_text())["provider_admitted"])

    def test_missing_binary_retains_blocked_report_and_packet_is_single_use(self):
        packet = self.prepare()
        with patch.object(probe.shutil, "which", return_value=None):
            self.assertEqual(probe.run(packet, "missing", 2), 3)
        report = json.loads(next(packet.glob("run-*/report.json")).read_text())
        self.assertEqual(report["p1_status"], "BLOCKED")
        self.assertFalse(report["provider_admitted"])
        self.assertTrue(all(c["status"] == "NOT_RUN" for c in report["topologies"].values()))
        with self.assertRaises(FileExistsError):
            probe.run(packet, "missing", 2)

    def test_native_launch_failure_stops_matrix_and_retains_reason(self):
        packet = self.prepare()
        success = {"returncode": 0, "error": None, "elapsed_seconds": 0.01,
                   "stdout": "Usage: codex sandbox [OPTIONS] [COMMAND]...", "stderr": ""}
        denied = dict(success, returncode=71, stdout="", stderr="sandbox_apply: Operation not permitted")
        with patch.object(probe.shutil, "which", return_value=sys.executable), \
                patch.object(probe, "execute", side_effect=[success, success, denied]) as execute:
            self.assertEqual(probe.run(packet, "codex", 2), 3)
            self.assertEqual(execute.call_count, 3)
        report = json.loads(next(packet.glob("run-*/report.json")).read_text())
        self.assertEqual(report["topologies"]["primary"]["status"], "BLOCKED")
        self.assertEqual(report["topologies"]["linked"]["status"], "NOT_RUN")
        self.assertTrue(all(s == "NOT_RUN" for cases in report["native_cases"].values() for s in cases.values()))

    def test_diagnostics_do_not_copy_ambient_secrets(self):
        result = {"returncode": 1, "error": None, "elapsed_seconds": 0.01,
                  "stdout": "token=private-value", "stderr": "Authorization: Bearer secret-value"}
        public = json.dumps(probe.public_result(result))
        self.assertNotIn("private-value", public)
        self.assertNotIn("secret-value", public)

    def test_timeout_kills_process_group(self):
        result = probe.execute([sys.executable, "-c", "import time; time.sleep(20)"], timeout=0.05)
        self.assertEqual(result["error"], "timeout")
        self.assertLess(result["elapsed_seconds"], 3)

    def test_session_overrides_never_without_bypassing_worker_sandbox(self):
        with patch.object(probe.shutil, "which", return_value="/opt/homebrew/bin/codex"):
            argv = probe.session_command("codex", "chosen-model")
        self.assertEqual(argv[:4], ["/opt/homebrew/bin/codex", "-C", str(probe.SOURCE), "--strict-config"])
        config = dict(argv[i + 1].split("=", 1) for i, value in enumerate(argv) if value == "-c")
        self.assertEqual(json.loads(config["approval_policy"]), "on-request")
        self.assertEqual(json.loads(config["approvals_reviewer"]), "user")
        self.assertEqual(json.loads(config["sandbox_mode"]), "workspace-write")
        self.assertFalse(json.loads(config["sandbox_workspace_write.network_access"]))
        self.assertEqual(json.loads(config["sandbox_workspace_write.writable_roots"]), [])
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "chosen-model")
        self.assertIn("native acceptance is not complete", argv[-1])
        self.assertIn("p1-handoff.md", argv[-1])
        self.assertIn("Stay in this existing worktree and Forge session", argv[-1])

    def test_session_requires_a_terminal_and_has_inspect_only_mode(self):
        argv = [str(HERE / "native-probe.sh"), "session", "--codex", sys.executable]
        result = subprocess.run([*argv, "--print"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("approval_policy", result.stdout)
        result = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("interactive terminal", result.stderr)

    def test_session_refuses_changed_policy(self):
        config = self.base / "config"
        config.mkdir()
        (config / "codex-p1-session.toml").write_text('approval_policy = "never"\n')
        with patch.object(probe, "SOURCE", self.base):
            with self.assertRaisesRegex(ValueError, "policy changed"):
                probe.session_command("codex")

    def test_required_native_cases_have_valid_dependencies_and_both_providers(self):
        cases = json.loads((HERE / "fixtures/native-cases.json").read_text())
        ids = [c["id"] for c in cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 16)
        seen = {"sandbox-canaries"}
        for case in cases:
            self.assertTrue(set(case["requires"]).issubset(seen))
            seen.add(case["id"])
        packet = json.loads((self.prepare() / "packet.json").read_text())
        self.assertEqual(set(packet["native_cases"]), {"claude", "codex"})
        for provider in packet["native_cases"].values():
            self.assertEqual(set(provider), set(ids))
            self.assertEqual(set(provider.values()), {"NOT_RUN"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
