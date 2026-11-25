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

from host_probe import Fixture


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

    def test_in_worktree_packet_requires_protected_candidate_path(self):
        parent = self.source / ".dev/forge-p1"
        parent.mkdir(parents=True)
        packet = probe.prepare(parent / "trial", self.source, in_worktree=True)
        _, data = probe.load_packet(packet)
        self.assertEqual(data["layout"], "in-worktree-protected-candidate")
        self.assertTrue(probe.inside(packet, self.source))
        manifest = json.loads((packet / "package.json").read_text())
        package = Path(manifest["package"])
        self.assertTrue(probe.inside(package, self.source / ".codex/forge-p1"))
        self.assertFalse(probe.inside(package, packet))
        self.assertIn("p1/package_access.py", manifest["files"])
        with self.assertRaisesRegex(ValueError, "physical .dev"):
            probe.prepare(self.source / "ordinary", self.source, in_worktree=True)
        alias = self.source / ".dev/forge-p1/alias"
        alias.symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "physical .dev"):
            probe.prepare(alias / "escape", self.source, in_worktree=True)
        local_alias = parent / "local-alias"
        local_alias.symlink_to(parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "physical .dev"):
            probe.prepare(local_alias / "escape", self.source, in_worktree=True)

    def test_in_worktree_package_redirection_refuses(self):
        parent = self.source / ".dev/forge-p1"
        parent.mkdir(parents=True)
        packet = probe.prepare(parent / "trial", self.source, in_worktree=True)
        manifest = json.loads((packet / "package.json").read_text())
        package = Path(manifest["package"])
        moved = package.with_name("moved")
        package.rename(moved)
        package.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "redirected"):
            probe.load_packet(packet)

    def test_uncontained_package_access_fails_without_changing_bytes(self):
        packet = self.prepare()
        manifest = json.loads((packet / "package.json").read_text())
        positive = packet / "positive"
        result = probe.execute([sys.executable, str(HERE / "fixtures/package_access.py"),
                                str(packet / "package.json"), str(positive)])
        status, rows = probe.evaluate_package_access(manifest, positive, result)
        self.assertEqual(status, "FAIL")
        self.assertTrue(all(row["write_open"] == "allowed" and row["unchanged"] for row in rows))
        probe.load_packet(packet)

    def test_package_access_requires_exact_rows_and_real_positive_control(self):
        packet = self.prepare()
        manifest = json.loads((packet / "package.json").read_text())
        positive = packet / "positive"
        observed = {"schema": "forge-p1-package-access/1", "positive_control": "allowed",
                    "files": [{"path": item["path"], "write_open": "denied", "unchanged": True}
                              for item in manifest["files"].values()]}
        result = {"returncode": 0, "error": None, "stdout": json.dumps(observed)}
        self.assertEqual(probe.evaluate_package_access(manifest, positive, result)[0], "FAIL")
        positive.write_text("P1 workspace write succeeded\n")
        self.assertEqual(probe.evaluate_package_access(manifest, positive, result)[0], "PASS")
        observed["files"] = observed["files"][:-1]
        result["stdout"] = json.dumps(observed)
        self.assertEqual(probe.evaluate_package_access(manifest, positive, result)[0], "BLOCKED")

    def test_package_access_launch_failure_and_tampering_are_not_passes(self):
        packet = self.prepare()
        manifest = json.loads((packet / "package.json").read_text())
        result = {"returncode": 71, "error": None, "stdout": ""}
        self.assertEqual(probe.evaluate_package_access(manifest, packet / "positive", result)[0], "BLOCKED")
        with patch.object(probe.shutil, "which", return_value=None):
            self.assertEqual(probe.package_access(packet, "missing", 2), 3)
        report = json.loads(next(packet.glob("package-access-*/report.json")).read_text())
        self.assertFalse(report["provider_admitted"])
        self.assertEqual(report["p1_status"], "BLOCKED")
        (packet / "operator/package/bin/forge").write_text("changed")
        with patch.object(probe, "execute") as execute:
            with self.assertRaisesRegex(ValueError, "bytes/path/mode"):
                probe.package_access(packet, "codex", 2)
            execute.assert_not_called()

    def test_tracked_symlink_is_refused(self):
        (self.source / "bin/link").symlink_to("forge")
        probe.git(self.source, "add", "bin/link")
        with self.assertRaisesRegex(ValueError, "non-regular"):
            self.prepare()

    def runtime_fixture(self):
        package = self.base / "runtime-package"
        package.mkdir()
        vendor = self.base / "yaml"
        vendor.mkdir()
        (vendor / "__init__.py").write_text("ORIGIN = 'protected-copy'\n")
        with patch.object(probe.runtime_probe, "TOOLS", ()):
            probe.runtime_probe.stage(package, yaml_source=vendor)
        return package

    def test_runtime_ignores_project_pythonpath_and_startup_shadow(self):
        package = self.runtime_fixture()
        bad = self.source / "yaml.py"
        bad.write_text("raise RuntimeError('project shadow executed')\n")
        (self.source / "sitecustomize.py").write_text("raise RuntimeError('startup executed')\n")
        import os
        env = dict(os.environ, PYTHONPATH=str(self.source), PYTHONHOME=str(self.source))
        result = probe.execute([str(package / "runtime/bin/python3"), "-c",
                                "import yaml,sys; print(yaml.ORIGIN); print(sys.flags.isolated)"],
                               cwd=self.source, env=env)
        self.assertEqual(result["returncode"], 0, result["stderr"])
        self.assertEqual(result["stdout"], "protected-copy\n1\n")
        self.assertFalse(list(package.rglob("*.pyc")))

    def test_runtime_supports_stdin_and_protected_scripts_only(self):
        package = self.runtime_fixture()
        script = package / "check.py"
        script.write_text("import sys,yaml; print(yaml.ORIGIN, sys.argv[1])\n")
        launcher = str(package / "runtime/bin/python3")
        result = probe.execute([launcher, str(script), "argument"], cwd=self.source)
        self.assertEqual(result["stdout"], "protected-copy argument\n", result["stderr"])
        external = self.source / "untrusted.py"
        external.write_text("raise RuntimeError('should not execute')\n")
        result = probe.execute([launcher, str(external)], cwd=self.source)
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("outside its protected package", result["stderr"])
        stdin = subprocess.run([launcher, "-", "argument"], input=script.read_text(),
                               text=True, capture_output=True, cwd=self.source)
        self.assertEqual(stdin.stdout, "protected-copy argument\n", stdin.stderr)
        result = probe.execute([sys.executable, str(package / "runtime/python_entry.py"), "-c", "pass"])
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("requires -I -S -B", result["stderr"])

    def test_runtime_sha256_matches_native_for_file_and_stdin(self):
        package = self.runtime_fixture()
        launcher = str(package / "runtime/bin/shasum")
        data = self.base / "input with spaces"
        data.write_bytes(b"P1 fixture\x00\xff\n")
        for args, stdin in (([str(data)], None), ([], data.read_bytes()), (["-"], data.read_bytes())):
            expected = subprocess.run(["/usr/bin/shasum", "-a", "256", *args],
                                      input=stdin, capture_output=True, check=True)
            actual = subprocess.run([launcher, "-a", "256", *args], input=stdin,
                                    capture_output=True, cwd=self.source)
            self.assertEqual(actual.returncode, 0, actual.stderr)
            self.assertEqual(actual.stdout, expected.stdout)

    def test_runtime_sha256_refuses_unsupported_modes(self):
        package = self.runtime_fixture()
        launcher = str(package / "runtime/bin/shasum")
        for args in ([], ["-a", "1"], ["-c", "checksums"], ["-a", "256", "one", "two"]):
            actual = probe.execute([launcher, *args], cwd=self.source)
            self.assertNotEqual(actual["returncode"], 0)
            self.assertIn("supports only -a 256", actual["stderr"])

    def test_runtime_environment_does_not_inherit_authority_or_startup(self):
        package = self.runtime_fixture()
        with patch.dict(probe.os.environ, {"FORGE_BROKER_TEST_ENABLE": "1", "BASH_ENV": "bad",
                                           "PYTHONPATH": "bad", "FORGE_FORGE_BIN": "bad"}):
            env = probe.runtime_probe.environment(package, self.base / "home", tmux="fixture-socket", pane="%1")
        self.assertNotIn("FORGE_BROKER_TEST_ENABLE", env)
        self.assertNotIn("BASH_ENV", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertEqual(env["FORGE_FORGE_BIN"], str(package / "bin/forge"))
        self.assertEqual(env["PATH"], str(package / "runtime/bin"))
        self.assertEqual(env["TMUX_PANE"], "%1")

    def test_runtime_refuses_vendor_symlinks_and_unsupported_python_forms(self):
        package = self.runtime_fixture()
        launcher = str(package / "runtime/bin/python3")
        result = probe.execute([launcher, "-m", "site"])
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("unsupported", result["stderr"])
        other = self.base / "other-package"
        other.mkdir()
        (self.base / "yaml/link.py").symlink_to(self.base / "yaml/__init__.py")
        with self.assertRaisesRegex(ValueError, "symlink"):
            probe.runtime_probe.stage(other, yaml_source=self.base / "yaml")

    def test_native_worker_requires_one_successful_exact_tool_trace(self):
        command = "checked-command --argument"
        call = {"exit_code": 0, "command": "/bin/zsh -lc 'checked-command --argument'"}
        self.assertTrue(probe.command_was_traced([call], command))
        for calls in ([], [call, call], [{"exit_code": 1, "command": command}],
                      [{"exit_code": 0, "command": "'bad quoting"}],
                      [{"exit_code": 0, "command": "echo 'checked-command --argument'"}],
                      [{"exit_code": 0, "command": "/bin/zsh -lc 'checked-command --argument' extra"}],
                      [{"exit_code": 0, "command": "other-command"}]):
            self.assertFalse(probe.command_was_traced(calls, command))

    def test_host_fixture_never_uses_default_socket(self):
        fixture = Fixture.__new__(Fixture)
        fixture.probe, fixture.base, fixture.env = probe, self.base, {}
        fixture.tmux, fixture.socket = "/pinned/tmux", "forge-p1-owned"
        fixture.created = True
        answer = {"returncode": 0, "error": None, "stdout": "", "stderr": ""}
        with patch.object(probe, "execute", return_value=answer) as execute:
            fixture.stop()
            fixture.stop()
        execute.assert_called_once_with(
            ["/pinned/tmux", "-L", "forge-p1-owned", "-f", "/dev/null", "kill-server"],
            cwd=self.base, env={})

    def test_host_fixture_commands_use_unique_outputs_after_resume(self):
        fixture = Fixture.__new__(Fixture)
        fixture.sessions = {"primary": {"name": "p1-owned-primary", "root": str(self.base)}}
        fixture.package, fixture.env, fixture.nonce = self.base, {}, "a1234567"
        fixture.commands = []
        with patch.object(fixture, "call") as call, \
                patch("host_probe.time.monotonic", side_effect=[0, 31, 0, 31]), \
                patch("host_probe.secrets.token_hex", side_effect=["one", "two"]):
            fixture.command("primary", "forge-bridge", ["identity"])
            fixture.commands = []
            fixture.command("primary", "forge-bridge", ["identity"])
        commands = [c.args[-1] for c in call.call_args_list if "-l" in c.args]
        self.assertIn("p1-host-a1234567-one.out", commands[0])
        self.assertIn("p1-host-a1234567-two.out", commands[1])
        self.assertTrue(all('TMUX="$TMUX" TMUX_PANE="$TMUX_PANE"' in cmd for cmd in commands))

    def test_runtime_audit_retains_failed_topology_and_continues_independent_roots(self):
        def stage(package):
            (package / "runtime").mkdir()
            probe.write_json(package / "runtime/runtime.json", {})

        with patch.object(probe.runtime_probe, "stage", side_effect=stage):
            packet = probe.prepare(self.base / "packet", self.source, with_runtime=True)

        def execute(argv, **kwargs):
            result = {"returncode": 0, "error": None, "stderr": "", "elapsed_seconds": 0.01,
                      "stdout": "Usage: codex sandbox [OPTIONS] [COMMAND]..."}
            if argv[-1] == "--help":
                return result
            manifest = json.loads(Path(argv[-2]).read_text())
            Path(argv[-1]).write_text("P1 workspace write succeeded\n")
            rows = [{"path": item["path"], "write_open": "denied", "unchanged": True}
                    for item in manifest["files"].values()]
            if Path(kwargs["cwd"]).name == "linked":
                rows[0]["write_open"] = "allowed"
            result["stdout"] = json.dumps({"schema": "forge-p1-package-access/1",
                                           "positive_control": "allowed", "files": rows})
            return result

        with patch.object(probe.platform, "system", return_value="Darwin"), \
                patch.object(probe.runtime_probe, "dependency_files", return_value=[]), \
                patch.object(probe.runtime_probe, "native_dependencies", return_value={
                    "files": [Path(sys.executable)], "os_cached_libraries": [], "unresolved": []}), \
                patch.object(probe.shutil, "which", return_value=sys.executable), \
                patch.object(probe, "execute", side_effect=execute):
            self.assertEqual(probe.runtime_audit(packet, "codex", 2), 1)
        report = json.loads(next(packet.glob("runtime-audit-*/report.json")).read_text())
        self.assertEqual(report["p1_status"], "FAIL")
        self.assertEqual(len(report["roots"]), 4)
        self.assertEqual(report["roots"]["linked"]["status"], "FAIL")
        self.assertEqual(report["roots"]["primary"]["status"], "PASS")

    def test_runtime_audit_requires_staging_before_any_execution(self):
        packet = self.prepare()
        with patch.object(probe, "execute") as execute:
            with self.assertRaisesRegex(ValueError, "--runtime"):
                probe.runtime_audit(packet, "codex", 2)
            execute.assert_not_called()

    def test_runtime_audit_preserves_help_failure(self):
        def stage(package):
            (package / "runtime").mkdir()
            probe.write_json(package / "runtime/runtime.json", {})

        with patch.object(probe.runtime_probe, "stage", side_effect=stage):
            packet = probe.prepare(self.base / "packet", self.source, with_runtime=True)
        with patch.object(probe.platform, "system", return_value="Darwin"), \
                patch.object(probe.runtime_probe, "dependency_files", return_value=[]), \
                patch.object(probe.runtime_probe, "native_dependencies", return_value={
                    "files": [], "os_cached_libraries": [], "unresolved": []}), \
                patch.object(probe.shutil, "which", return_value=sys.executable), \
                patch.object(probe, "execute", return_value={"returncode": 1, "error": None,
                    "elapsed_seconds": 0, "stdout": "", "stderr": "failure"}) as execute:
            self.assertEqual(probe.runtime_audit(packet, "codex", 2), 3)
            execute.assert_called_once()
        report = json.loads(next(packet.glob("runtime-audit-*/report.json")).read_text())
        self.assertEqual(report["p1_status"], "BLOCKED")
        self.assertEqual(report["roots"], {})
        self.assertIn("help unavailable", report["prerequisite_error"])

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

    def test_sandbox_argv_uses_explicit_primitive_profile_and_detects_cli_shape(self):
        command = probe.sandbox_command("/codex", self.base,
            "Usage: codex sandbox [OPTIONS] [COMMAND]...", ["/python", "canary.py"])
        self.assertEqual(command[-3:], ["--", "/python", "canary.py"])
        self.assertEqual(command[1:6], ["sandbox", "--permission-profile", ":workspace", "--cd", str(self.base)])
        self.assertNotIn("--strict-config", command)
        self.assertNotIn("--ask-for-approval", command)
        with patch.object(probe.platform, "system", return_value="Darwin"):
            old = probe.sandbox_command("/codex", self.base, "Commands: macos linux", ["/python"])
        self.assertEqual(old[-4:], ["sandbox", "macos", "--", "/python"])
        self.assertIn("sandbox_workspace_write.network_access=false", old)
        self.assertIn("sandbox_workspace_write.writable_roots=[]", old)
        self.assertEqual(old[old.index("--ask-for-approval") + 1], "never")
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
