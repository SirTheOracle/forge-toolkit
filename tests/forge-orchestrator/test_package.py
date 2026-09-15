"""Copied production package integrity; no native model qualification doubles."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib
import unittest
import uuid
from unittest.mock import patch

TREE = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader("package", str(TREE / "bin/forge-orchestrator-package"))
spec = importlib.util.spec_from_loader(loader.name, loader)
package = importlib.util.module_from_spec(spec)
loader.exec_module(package)


class Package(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (TREE / ".dev/forge-p3").mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="package-", dir=TREE / ".dev/forge-p3")
        cls.root = Path(cls.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(cls.root)], check=True)
        cls.ctx = package.host.Context(str(cls.root))
        cls.result = package.stage(cls.ctx, "codex", "/bin/echo", "fixture-no-native-qualification")
        cls.path = Path(cls.result["package"])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_repeat_and_distinct_explicit_entry(self):
        again = package.stage(self.ctx, "codex", "/bin/echo", "fixture-no-native-qualification")
        self.assertEqual(self.result, again)
        entry = Path(again["entry"]).read_text()
        self.assertIn(str(self.path / "skills/forge-orchestrator/SKILL.md"), entry)
        self.assertIn(str(self.path / "skills/forge-orchestrator/references/codex.md"), entry)
        self.assertNotIn("~/", entry)
        self.assertEqual(package.verify(self.path)["supported_providers"], [])

    def test_runtime_environment_and_yaml_are_pinned(self):
        receipt = package.verify(self.path)
        env = receipt["environment"]
        self.assertNotIn("TMPDIR", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("/opt/homebrew/bin", env["PATH"])
        self.assertEqual(env["FORGE_ORCHESTRATOR_PROVIDER"], "codex")
        cp = subprocess.run([str(self.path / "runtime/bin/python3"), "-c",
                             "import yaml,sys; print(yaml.__file__); print(sys.flags.isolated,sys.flags.no_site)"],
                            env=env, capture_output=True, text=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn(str(self.path / "runtime/vendor/yaml"), cp.stdout)
        self.assertIn("1 1", cp.stdout)
        escaped = subprocess.run([str(self.path / "runtime/bin/python3"), str(TREE / "install.sh")],
                                 env=env, capture_output=True, text=True)
        self.assertNotEqual(escaped.returncode, 0)

    def test_stable_auth_namespace_and_policy_only_inputs(self):
        receipt = package.verify(self.path)
        env = receipt["environment"]
        self.assertEqual(Path(env["CODEX_HOME"]), self.ctx.store.path / "authentication/codex")
        self.assertNotEqual(Path(env["HOME"]), Path.home())
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertFalse((Path(env["CODEX_HOME"]) / "auth.json").exists())
        self.assertEqual((Path(env["CODEX_HOME"]) / "config.toml").read_text(),
                         'cli_auth_credentials_store = "keyring"\n')
        if package.sys.platform == "darwin":
            locator = Path(env["HOME"]) / "Library/Keychains"
            self.assertTrue(locator.is_symlink())
            self.assertIn(str(locator), receipt["aliases"])

    def test_codex_native_config_uses_whole_table_for_dotted_paths(self):
        receipt = package.verify(self.path)
        argv = receipt["provider_argv"]
        project_arg = next(arg for arg in argv if arg.startswith("projects="))
        self.assertEqual(tomllib.loads(project_arg)["projects"][str(self.root)]["trust_level"], "untrusted")
        self.assertNotIn("danger-full-access", argv)
        self.assertIn("allow_login_shell=false", argv)
        self.assertIn("skip_host_skill_discovery", argv)

    def test_new_native_input_directories_and_aliases_refuse(self):
        receipt = package.verify(self.path)
        home = Path(receipt["environment"]["CODEX_HOME"])
        extra = home / "hooks"
        try:
            extra.mkdir()
            with self.assertRaisesRegex(package.host.Refusal, "native configuration"):
                package.verify(self.path)
        finally:
            extra.rmdir()
        try:
            extra.symlink_to(self.root, target_is_directory=True)
            with self.assertRaises(package.host.Refusal):
                package.verify(self.path)
        finally:
            extra.unlink()
        package.verify(self.path)

    def test_auth_status_uses_native_private_environment_and_exit_code(self):
        original = package.subprocess.run
        observed = []
        def invoke(argv, **kwargs):
            if argv == ["/bin/echo", "login", "status"]:
                observed.append((argv, kwargs))
                return subprocess.CompletedProcess(argv, 7)
            return original(argv, **kwargs)
        with patch.object(package.subprocess, "run", side_effect=invoke):
            self.assertEqual(package.authenticate(self.path, status=True), 7)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][1]["env"], package.verify(self.path)["environment"])
        self.assertNotIn("capture_output", observed[0][1])

    def test_qualifier_uses_pinned_worker_without_ambient_path(self):
        class ReachedProbe(Exception):
            pass
        with patch.object(package.shutil, "which", return_value=None):
            with patch.object(package, "policy_probes", side_effect=ReachedProbe) as probe:
                with self.assertRaises(ReachedProbe):
                    package.qualify(self.path)
        self.assertEqual(str(probe.call_args.args[3]), package.verify(self.path)["worker_binaries"]["codex"])
        with self.assertRaisesRegex(package.host.Refusal, "packaged worker executable"):
            package.qualify(self.path, "/bin/echo")

    def test_missing_changed_extra_and_symlink_rejected(self):
        path = self.path / "digests/coding.txt"
        old = path.read_bytes()
        try:
            path.chmod(0o600)
            path.write_text("changed")
            with self.assertRaises((package.host.Refusal, OSError)):
                package.verify(self.path)
            path.unlink()
            path.symlink_to(TREE / "digests/coding.txt")
            with self.assertRaises((package.host.Refusal, OSError)):
                package.verify(self.path)
        finally:
            path.unlink()
            package.write(path, old)
        extra = self.path / "prompts/shadow.txt"
        try:
            package.write(extra, "extra")
            with self.assertRaisesRegex(package.host.Refusal, "added or removed"):
                package.verify(self.path)
        finally:
            extra.unlink()

    def test_helper_ignores_startup_and_override_escape(self):
        poison = self.root / "poison.sh"
        marker = self.root / "escaped"
        poison.write_text('touch "' + str(marker) + '"\n')
        env = {**os.environ, "BASH_ENV": str(poison), "ENV": str(poison),
               "FORGE_FORGE_BIN": str(poison), "FORGE_BROKER_BIN": str(poison),
               "FORGE_BROKER_TEST_ENABLE": "1", "FORGE_ASSETS_REQUIRED": "0", "PATH": "/nonexistent",
               "BASH_FUNC_exec%%": '() { /usr/bin/touch "' + str(marker) + '"; builtin exec "$@"; }'}
        cp = subprocess.run([str(self.path / "bin/forge-bridge"), "help"], env=env,
                            cwd=self.root, text=True, capture_output=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertFalse(marker.exists())
        cp = subprocess.run([str(self.path / "bin/forge-broker"), "--help"], env=env,
                            cwd=self.root, text=True, capture_output=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertFalse(marker.exists())

    def test_runtime_change_stages_distinct_candidate(self):
        inputs = package.runtime.inputs()
        inputs["qualification_fixture_revision"] = "changed-runtime-input"
        with patch.object(package.runtime, "inputs", return_value=inputs):
            changed = package.stage(self.ctx, "codex", "/bin/echo", "fixture-no-native-qualification")
        self.assertNotEqual(changed["package"], self.result["package"])
        package.verify(self.path)

    def test_runtime_inventory_detects_added_alias_to_existing_module(self):
        fixture = self.root / "runtime-inventory-fixture"
        (fixture / "runtime").mkdir(parents=True)
        stdlib = fixture / "stdlib"
        stdlib.mkdir()
        module = stdlib / "original.py"
        module.write_text("pass\n")
        (fixture / "runtime/runtime.json").write_text(json.dumps({
            "python": str(module), "tools": {}, "stdlib": str(stdlib)}))
        before = package.runtime.dependency_files(fixture)
        (stdlib / "new_alias.py").symlink_to(module)
        self.assertNotEqual(before, package.runtime.dependency_files(fixture))

    def test_source_change_during_copy_refuses(self):
        original = package.host.file_bytes
        reads = 0
        def changed(path):
            nonlocal reads
            data = original(path)
            if str(path) == str(TREE / "digests/coding.txt"):
                reads += 1
                if reads > 1:
                    return data + b"\nchanged"
            return data
        with patch.object(package.host, "file_bytes", side_effect=changed):
            with self.assertRaisesRegex(package.host.Refusal, "changed during staging"):
                package.stage(self.ctx, "codex", "/bin/echo", "source-race-fixture")
        retry = package.stage(self.ctx, "codex", "/bin/echo", "source-race-fixture")
        package.verify(retry["package"])

    def test_canaries_removed_after_interruption(self):
        targets = [str(self.path / "digests/coding.txt"), str(self.path / "prompts/coding.txt")]
        with self.assertRaises(KeyboardInterrupt):
            with package.owned_canaries(self.path, targets, "interruption-fixture") as paths:
                self.assertTrue(all(p.exists() for p in paths))
                raise KeyboardInterrupt()
        self.assertTrue(all(not p.exists() for p in paths))
        package.verify(self.path)

    def test_watch_registration_uses_executable_scrubbed_entry(self):
        receipt = package.verify(self.path)
        body = (self.path / "bin/.forge-watch-body").read_text()
        assignment = re.search(r"^SELF=.*$", body, re.M).group()
        cp = subprocess.run(["/bin/bash", "-p", "-c", assignment + '\nprintf "%s" "$SELF"',
                             str(self.path / "bin/.forge-watch-body")],
                            env=receipt["environment"], text=True, capture_output=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(cp.stdout, str(self.path / "bin/forge-watch"))
        self.assertTrue(os.access(cp.stdout, os.X_OK))
        self.assertIn("<string>$SELF</string>", body)

    def test_fresh_broker_checks_expected_hashes_before_import(self):
        receipt = package.verify(self.path)
        marker = self.root / "unexpected-import"
        for relative in ("bin/forge-orchestrator-package", "lib/forge_assets.py"):
            path = self.path / relative
            old, mode = path.read_bytes(), path.stat().st_mode & 0o777
            try:
                path.chmod(0o600)
                path.write_bytes(old + ('\nopen(' + repr(str(marker)) + ', "x").close()\n').encode())
                cp = subprocess.run([str(self.path / "bin/forge-broker"), "serve", "--root", str(self.root),
                                     "--session", "unused", "--incarnation", "0", "--mode", "contain"],
                                    env=receipt["environment"], text=True, capture_output=True, timeout=15)
                self.assertNotEqual(cp.returncode, 0, relative)
                self.assertIn("BROKER_RUNTIME_CHANGED", cp.stderr)
                self.assertFalse(marker.exists(), relative)
            finally:
                path.write_bytes(old)
                path.chmod(mode)

    def test_copied_broker_attests_full_package_and_refuses_stale_helper(self):
        receipt = package.verify(self.path)
        metadata = json.loads((self.path / "runtime/runtime.json").read_text())
        tmux = metadata["tools"]["tmux"]
        socket = "forge-p3-package-" + uuid.uuid4().hex
        env = dict(receipt["environment"])
        def mux(*args):
            return subprocess.run([tmux, "-L", socket, "-f", "/dev/null", *args],
                                  env=env, capture_output=True, text=True, timeout=15)
        def broker(*args, request=None):
            return subprocess.run([str(self.path / "bin/forge-broker"), *args, "--root", str(self.root)],
                                  env=env, input=json.dumps(request) if request else None,
                                  capture_output=True, text=True, timeout=30)
        changed = self.path / "digests/coding.txt"
        old = changed.read_bytes()
        started = False
        try:
            cp = mux("new-session", "-d", "-s", "candidate", "-c", str(self.root), "/bin/sleep 120")
            self.assertEqual(cp.returncode, 0, cp.stderr)
            binding = mux("display-message", "-p", "-t", "candidate:0.0", "#{socket_path},#{pid},#{session_id}|#{session_created}")
            self.assertEqual(binding.returncode, 0, binding.stderr)
            context, incarnation = binding.stdout.strip().split("|")
            env["TMUX"] = context.replace(",$", ",")
            self.assertRegex(env["TMUX"], r"^/[^\n]+,[0-9]+,[0-9]+$")
            cp = broker("start", "--fresh", "--session", "candidate", "--incarnation", incarnation, "--mode", "contain")
            log = self.ctx.protected / "broker.log"
            self.assertEqual(cp.returncode, 0, cp.stderr + (log.read_text() if log.exists() else ""))
            started = True
            info = json.loads(cp.stdout)
            identity = info["runtime_identity"]
            inventoried = {item["path"] for item in identity["files"]}
            self.assertIn(str(changed), inventoried)
            self.assertIn(str(self.path / "package.json"), inventoried)
            cp = broker("control", "--expect-runtime-sha256", identity["sha256"], request={"action": "inspect-runtime"})
            self.assertEqual(cp.returncode, 0, cp.stderr)
            cp = broker("start", "--session", "candidate", "--incarnation", incarnation, "--mode", "contain")
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertEqual(json.loads(cp.stdout)["pid"], info["pid"])
            changed.chmod(0o600)
            changed.write_text("changed after startup")
            cp = broker("status", "--json")
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertEqual(json.loads(cp.stdout)["runtime_state"], "changed")
            cp = broker("control", "--expect-runtime-sha256", identity["sha256"], request={"action": "inspect-runtime"})
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("BROKER_RUNTIME_CHANGED", cp.stderr)
        finally:
            changed.chmod(0o600)
            changed.write_bytes(old)
            changed.chmod(0o400)
            if started:
                cp = broker("stop", "--timeout", "5")
                self.assertEqual(cp.returncode, 0, cp.stderr)
            mux("kill-server")


if __name__ == "__main__":
    unittest.main()
