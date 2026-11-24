"""Copied production package integrity; no native model qualification doubles."""
import importlib.machinery
import importlib.util
import json
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
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

    def test_git_executable_and_native_helper_directory_are_inventoried(self):
        receipt = package.verify(self.path)
        metadata = json.loads((self.path / "runtime/runtime.json").read_text())
        files = {item["path"] for item in receipt["files"]}
        self.assertIn(str(Path(metadata["tools"]["git"]).resolve()), files)
        helper_dir, helpers = package.runtime.git_helpers(metadata["tools"]["git"])
        self.assertEqual(str(self.path / "runtime/git-core"), metadata["git_exec_path"])
        self.assertEqual({p.name for p in Path(metadata["git_exec_path"]).iterdir()}, set(package.runtime.GIT_HELPERS))
        self.assertTrue(helpers)
        self.assertTrue(all(str(path) in files for path in Path(metadata["git_exec_path"]).iterdir()))
        if package.sys.platform == "darwin":
            self.assertNotEqual(metadata["tools"]["git"], "/usr/bin/git")
            for name in ("forge", "forge-broker", "forge-orchestrator-host"):
                text = (self.path / "bin" / (".forge-body" if name == "forge" else name)).read_text()
                self.assertNotRegex(text, r'''["']/usr/bin/git["']''')
                self.assertIn(str(self.path / "runtime/bin/git"), text)
        audit = package.runtime.audit(self.path, receipt)
        if audit["script_dependencies"]:
            # The aggregate runner supplies a scripted tmux socket shim. That
            # test-only runtime must not become a qualified production runtime.
            self.assertEqual(audit["status"], "unsupported")
            self.assertTrue(audit["blockers"])
        else:
            self.assertEqual(audit["status"], "not-run")
            self.assertEqual(audit["static_status"], "pass")
            self.assertEqual(audit["blockers"], [])

    def test_runtime_measurement_executes_copied_entries_under_hostile_overrides(self):
        directory = self.ctx.store.path / "reports" / ("runtime-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        measured = package.runtime.measure(self.path, package.verify(self.path), directory)
        if measured["script_dependencies"]:
            self.assertEqual(measured["status"], "unsupported")
        else:
            self.assertEqual(measured["status"], "pass", measured)
            self.assertEqual(len(measured["observations"]), 5)
            self.assertTrue((directory / "runtime-probe.json").exists())
        package.verify(self.path)

    def test_implicit_startup_addition_invalidates_existing_candidate(self):
        existing = package.runtime.implicit_inputs()
        with patch.object(package.runtime, "implicit_inputs", return_value=existing + [{"path": "/fixture/new-startup", "kind": "file"}]):
            with self.assertRaisesRegex(package.host.Refusal, "startup"):
                package.verify(self.path)

    def test_real_builtin_request_transport_without_profile_or_launch(self):
        directory = self.ctx.store.path / "reports" / ("transport-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        before = self.ctx.store.profile(), self.ctx.store.current()
        measured = package.hook_transport(self.path, package.verify(self.path), directory)
        self.assertEqual(measured["status"], "pass", measured)
        self.assertEqual((self.ctx.store.profile(), self.ctx.store.current()), before)

    def test_management_binds_its_original_package_and_measures_real_worker_denial(self):
        root = self.root / "management-bound"
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        ctx = package.host.Context(str(root))
        staged = package.stage(ctx, "codex", "/bin/echo", "fixture-no-native-qualification")
        copied = Path(staged["package"])
        cp = subprocess.run([str(copied / "bin/forge-orchestrator-host"), "prepare-management", "--root", str(root)],
                            text=True, capture_output=True, timeout=30)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(package.host.management_inputs(ctx)["package"], package.host.file_identity(copied / "package.json"))
        directory = ctx.store.path / "reports/test-management"
        directory.mkdir(parents=True)
        receipt = package.verify(copied)
        measured = package.management_probe(copied, receipt, directory, package.host.topology(ctx), receipt["worker_binaries"]["codex"])
        self.assertEqual(measured["status"], "not-run", measured)
        self.assertTrue(measured["operator_positive"])
        self.assertTrue(all(row["pass"] for row in measured["observations"]))
        self.assertIsNone(ctx.store.profile())
        target = copied / "lib/forge_runtime.py"
        before = target.read_bytes()
        try:
            target.chmod(0o600)
            target.write_bytes(before + b"\nraise RuntimeError('must not execute changed management dependency')\n")
            with self.assertRaises(package.host.Refusal):
                package.host.management_inputs(ctx)
        finally:
            target.write_bytes(before)
            target.chmod(0o400)

    def test_manual_policy_claim_cannot_hide_an_executed_protected_write(self):
        for mutate in (False, True):
            directory = self.ctx.store.path / "reports" / ("manual-" + uuid.uuid4().hex)
            directory.mkdir(parents=True)
            def runner(receipt, argv, cwd, transcript):
                if mutate:
                    Path(argv[-1]).write_text("unexpected execution")
                return {"status": "pass"}
            result = package.manual_policy(self.path, package.verify(self.path), directory, [{"root": str(self.root)}], runner)
            self.assertEqual(result["status"], "fail" if mutate else "pass")

    def test_git_helper_addition_changes_runtime_inventory(self):
        root = self.root / "git-runtime-fixture"
        (root / "runtime").mkdir(parents=True)
        helpers = root / "git-core"
        helpers.mkdir()
        stdlib = root / "stdlib"
        stdlib.mkdir()
        (root / "runtime/runtime.json").write_text(json.dumps({"python": "/bin/sh", "tools": {},
            "stdlib": str(stdlib), "git_exec_path": str(helpers)}))
        before = package.runtime.dependency_files(root)
        (helpers / "git-remote-new").write_text("not a qualified helper")
        self.assertNotEqual(before, package.runtime.dependency_files(root))

    def test_copied_git_and_broker_refuse_repository_includes(self):
        config = self.root / ".git/config"
        before = config.read_bytes()
        try:
            config.write_bytes(before + b'\n[include]\npath = /nonexistent/qualification-include\n')
            for argv in ([str(self.path / "runtime/bin/git"), "-C", str(self.root), "status", "--porcelain"],
                         [str(self.path / "bin/forge-broker"), "status", "--root", str(self.root)]):
                cp = subprocess.run(argv, cwd=self.root, text=True, capture_output=True, timeout=15)
                self.assertNotEqual(cp.returncode, 0)
                self.assertIn("GIT_CONFIG_EXECUTION_REFUSED", cp.stderr)
        finally:
            config.write_bytes(before)
        package.verify(self.path)

    def test_quoted_root_cannot_inject_into_copied_git_calls(self):
        root = self.root / "quote'and\"space root"
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        staged = package.stage(package.host.Context(str(root)), "codex", "/bin/echo", "fixture-no-native-qualification")
        copied = Path(staged["package"])
        cp = subprocess.run([str(copied / "bin/forge-orchestrator-package"), "verify", "--package", str(copied)],
                            cwd=root, text=True, capture_output=True, timeout=30)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(json.loads(cp.stdout)["integrity"], "pass")

    def test_stable_auth_namespace_and_policy_only_inputs(self):
        receipt = package.verify(self.path)
        env = receipt["environment"]
        self.assertEqual(Path(env["CODEX_HOME"]), self.ctx.store.path / "authentication-p4" /
                         package.host.file_identity(receipt["worker_binaries"]["codex"])["sha256"] / "codex")
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
        hook_arg = next(arg for arg in argv if arg.startswith("hooks="))
        self.assertEqual(tomllib.loads(hook_arg), json.loads(Path(receipt["attention_hooks"]).read_text()))
        self.assertNotIn("--dangerously-bypass-hook-trust", argv)

    def test_claude_empty_mcp_configuration_has_required_server_map(self):
        staged = package.stage(self.ctx, "claude", "/bin/echo", "fixture-no-native-qualification")
        argv = package.verify(staged["package"])["provider_argv"]
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(json.loads(argv[argv.index("--mcp-config") + 1]), {"mcpServers": {}})
        settings = json.loads(Path(argv[argv.index("--settings") + 1]).read_text())
        receipt = package.verify(staged["package"])
        self.assertEqual(settings, json.loads(Path(receipt["attention_hooks"]).read_text()))
        self.assertNotIn("--safe-mode", argv)
        self.assertIn("--disable-slash-commands", argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "default")

    def test_attention_configs_are_copied_pinned_and_provider_specific(self):
        for provider in ("codex", "claude"):
            staged = package.stage(self.ctx, provider, "/bin/echo", "fixture-no-native-qualification")
            receipt = package.verify(staged["package"])
            attention = Path(receipt["attention_hooks"])
            hooks = json.loads(attention.read_text())["hooks"]
            self.assertTrue({"UserPromptSubmit", "Stop", "PermissionRequest", "PostToolUse"} <= set(hooks))
            self.assertNotIn("SubagentStop", hooks)
            for event, entries in hooks.items():
                for entry in entries:
                    for hook in entry["hooks"]:
                        argv = shlex.split(hook["command"])
                        self.assertEqual(argv, [str(Path(staged["package"]) / "bin/forge-cc-hook")]
                                         + (["--codex"] if provider == "codex" else []) + [event.lower()])
                        self.assertEqual(hook["type"], "command")
                        self.assertEqual(hook["timeout"], 10)
            before = attention.read_bytes()
            try:
                attention.chmod(0o600)
                attention.write_text('{"hooks":{}}')
                with self.assertRaises(package.host.Refusal):
                    package.verify(staged["package"])
            finally:
                attention.write_bytes(before)
                attention.chmod(0o400)

    def test_codex_host_disables_remote_plugins(self):
        # codex-cli 0.171 installs curated remote plugins into CODEX_HOME at TUI
        # startup unless remote_plugin is disabled; that drifts discovery inputs.
        argv = package.verify(self.path)["provider_argv"]
        pairs = list(zip(argv, argv[1:]))
        self.assertIn(("--disable", "plugins"), pairs)
        self.assertIn(("--disable", "remote_plugin"), pairs)

    def test_codex_plugin_directory_is_sealed(self):
        # The 0.171 TUI installs remote plugins despite the feature flags; a sealed
        # empty plugins directory makes that write fail instead of drifting discovery.
        receipt = package.verify(self.path)
        plugins = Path(receipt["environment"]["CODEX_HOME"]) / "plugins"
        info = os.lstat(plugins)
        self.assertTrue(plugins.is_dir() and not plugins.is_symlink())
        self.assertEqual(info.st_mode & 0o222, 0)
        self.assertEqual(list(plugins.iterdir()), [])
        with self.assertRaises(PermissionError):
            (plugins / "cache").mkdir()
        try:
            plugins.chmod(0o700)
            with self.assertRaises(package.host.Refusal) as caught:
                package.stage(self.ctx, "codex", "/bin/echo", "fixture-no-native-qualification")
            self.assertEqual(caught.exception.code, "NATIVE_DISCOVERY_CHANGED")
            (plugins / "cache").mkdir()
            with self.assertRaises(package.host.Refusal):
                package.verify(self.path)
        finally:
            shutil.rmtree(plugins / "cache", ignore_errors=True)
            plugins.chmod(0o500)
        package.verify(self.path)

    def test_native_hook_trust_is_the_only_mutable_auth_configuration(self):
        receipt = package.verify(self.path)
        config = Path(receipt["environment"]["CODEX_HOME"]) / "config.toml"
        item = next(item for item in receipt["files"] if item["path"] == str(config))
        self.assertEqual(item["kind"], "codex-hook-trust")
        before = config.read_bytes()
        try:
            config.chmod(0o600)
            config.write_text(before.decode() + '\n[hooks.state."fixture"]\nenabled = false\ntrusted_hash = "sha256:' + 'a' * 64 + '"\n')
            package.host.verify_file(item)
            if Path("/usr/bin/python3").exists():
                code = ("import importlib.machinery,json,sys; "
                        "host=importlib.machinery.SourceFileLoader('host',sys.argv[1]).load_module(); "
                        "host.verify_file(json.loads(sys.argv[2]))")
                cp = subprocess.run(["/usr/bin/python3", "-I", "-c", code, str(TREE / "bin/forge-orchestrator-host"), json.dumps(item)],
                                    text=True, capture_output=True, timeout=10)
                self.assertEqual(cp.returncode, 0, cp.stderr)
            package.verify(self.path)
            self.assertEqual(package.stage(self.ctx, "codex", "/bin/echo", "fixture-no-native-qualification"), self.result)
            # Codex 0.171 writes its own first-run UI acknowledgement on startup.
            config.write_text(before.decode() + '\n[tui]\nscreen_reader_detection_done = true\n\n'
                              '[tui.model_availability_nux]\n"gpt-6.1-sol" = 1\n\n[notice]\nhide_full_access_warning = true\n')
            package.host.verify_file(item)
            package.verify(self.path)
            for suffix in ('sandbox_mode = "danger-full-access"\n',
                           '[tui]\nscreen_reader_detection_done = "true"\n',
                           '[tui.nested]\nflag = "on"\n',
                           '[tui]\nlevel = 1.5\n',
                           '[notice]\nmodel_migrations = { a = "b" }\n',
                           '[hooks]\nUserPromptSubmit = []\n',
                           '[hooks.state.fixture]\ncommand = "/bin/true"\n',
                           '[hooks.state.fixture]\nenabled = "true"\n',
                           '[hooks.state.fixture]\ntrusted_hash = "anything"\n'):
                config.write_text(before.decode() + suffix)
                with self.assertRaises(package.host.Refusal):
                    package.verify(self.path)
        finally:
            config.write_bytes(before)
            config.chmod(0o400)

    @unittest.skipUnless(shutil.which("codex") and shutil.which("sandbox-exec"), "requires local offline Codex config reader")
    def test_codex_native_hook_discovery_and_trust_without_a_model(self):
        native = package.module("native_hooks", TREE / "lib/forge_native.py")
        receipt = package.verify(self.path)
        config = Path(receipt["environment"]["CODEX_HOME"]) / "config.toml"
        before = config.read_bytes()
        argv = native.codex_argv(receipt)
        argv[0] = shutil.which("codex")
        argv = [shutil.which("sandbox-exec"), "-p", "(version 1)(allow default)(deny network*)", *argv]
        try:
            with tempfile.TemporaryDirectory(dir=self.root) as tmp:
                with native.Rpc(argv, receipt["environment"], str(self.root), Path(tmp) / "hooks.jsonl", timeout=15) as client:
                    result = client.call("hooks/list", {"cwds": [str(self.root)]})["data"][0]
                    self.assertEqual(result["errors"], [])
                    self.assertEqual(len(result["hooks"]), 4)
                    for row in result["hooks"]:
                        self.assertEqual(row["trustStatus"], "untrusted")
                        self.assertFalse(row["isManaged"])
                        self.assertIn(str(self.path / "bin/forge-cc-hook"), row["command"])
                    row = result["hooks"][0]
                    # Only this disposable fixture is approved, using the native
                    # config writer behind /hooks. No thread or turn is started.
                    client.call("config/value/write", {"keyPath": "hooks.state." + json.dumps(row["key"]) + ".trusted_hash",
                                "value": row["currentHash"], "mergeStrategy": "replace"})
                    rows = client.call("hooks/list", {"cwds": [str(self.root)]})["data"][0]["hooks"]
                    self.assertEqual(next(r for r in rows if r["key"] == row["key"])["trustStatus"], "trusted")
                    self.assertEqual(package.discovery_inputs(receipt["environment"]), receipt["discovery"])
                    package.verify(self.path)
                changed = [arg.replace("forge-cc-hook", "forge-cc-hook-changed") if arg.startswith("hooks=") else arg for arg in argv]
                with native.Rpc(changed, receipt["environment"], str(self.root), Path(tmp) / "changed.jsonl", timeout=15) as client:
                    rows = client.call("hooks/list", {"cwds": [str(self.root)]})["data"][0]["hooks"]
                    self.assertTrue(all(r["trustStatus"] in ("untrusted", "modified") for r in rows))
                    self.assertEqual(next(r for r in rows if r["key"] == row["key"])["trustStatus"], "modified")
        finally:
            config.chmod(0o600)
            config.write_bytes(before)
            config.chmod(0o400)

    @unittest.skipUnless(shutil.which("tmux"), "requires local tmux")
    def test_copied_attention_commands_emit_for_both_providers(self):
        tmux = shutil.which("tmux")
        socket = "forge-p4-hooks-" + uuid.uuid4().hex
        def mux(*args):
            return subprocess.run([tmux, "-L", socket, "-f", "/dev/null", *args],
                                  capture_output=True, text=True, check=True)
        mux("new-session", "-d", "-s", "p4-attention", "-c", str(self.root), "/bin/cat")
        try:
            mux("split-window", "-d", "-t", "p4-attention:0", "-c", str(self.root), "/bin/cat")
            socket_path = mux("display-message", "-p", "-t", "p4-attention:0.0", "#{socket_path}").stdout.strip()
            panes = [mux("display-message", "-p", "-t", f"p4-attention:0.{i}", "#{pane_id}").stdout.strip() for i in (0, 1)]
            attention = self.root / ".dev/attention"
            attention.mkdir(parents=True, exist_ok=True)
            for provider in ("codex", "claude"):
                staged = package.stage(self.ctx, provider, "/bin/echo", "fixture-no-native-qualification")
                receipt = package.verify(staged["package"])
                hooks = json.loads(Path(receipt["attention_hooks"]).read_text())["hooks"]
                env = dict(receipt["environment"], TMUX=socket_path + ",0,0", TMUX_PANE=panes[0], FORGE_ROLE="orchestrator")
                def emit(event, payload, pane=None):
                    command = hooks[event][0]["hooks"][0]["command"]
                    cp = subprocess.run(shlex.split(command), input=json.dumps(payload), cwd=self.root,
                                        env=dict(env, TMUX_PANE=pane or panes[0]), text=True, capture_output=True, timeout=15)
                    self.assertEqual((cp.returncode, cp.stdout), (0, ""), cp.stderr)
                emit("UserPromptSubmit", {"prompt": "local fixture", "session_id": "native", "turn_id": "turn"})
                emit("PermissionRequest", {"tool_name": "Bash", "tool_input": {"command": "pwd"}})
                self.assertEqual(len(list(attention.glob("perm.p4-attention.*"))), 1)
                emit("PostToolUse", {"tool_name": "Bash", "tool_input": {"command": "pwd"}})
                self.assertFalse(list(attention.glob("perm.p4-attention.*")))
                emit("Stop", {"last_assistant_message": "host fixture", "session_id": "native", "turn_id": "turn"})
                stop = attention / "stop.p4-attention.json"
                record = json.loads(stop.read_text())
                self.assertEqual(record["role"], "orchestrator")
                self.assertEqual(record["snippet"], "host fixture")
                self.assertEqual(record["provider"], "unknown")  # No native model is admitted in this fixture.
                before = stop.read_bytes()
                emit("Stop", {"last_assistant_message": "worker fixture", "turn_id": "worker"}, panes[1])
                self.assertEqual(stop.read_bytes(), before)
                self.assertTrue(list(attention.glob("wstop.*")))
        finally:
            mux("kill-server")

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

    def capability_fixture(self):
        directory = self.ctx.store.path / "reports" / ("fixture-only-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        artifact = directory / "not-native-evidence.txt"
        artifact.write_text("Synthetic schema test only. Never native qualification evidence.\n")
        report = {"schema": "forge-orchestrator-capability/1", "root": self.ctx.identity,
                  "package": package.host.file_identity(self.path / "package.json"),
                  "topology_sha256": package.host.records.digest(package.host.topology(self.ctx)),
                  "checks": dict.fromkeys(package.ADMISSION_CHECKS, "pass"),
                  "artifacts": [package.host.file_identity(artifact)]}
        report["checks"]["authentication"] = "available"
        return directory / "report.json", report

    def test_candidate_producer_matches_p2_schema_without_admission(self):
        evidence = {"reports": ["0" * 64], "loading": "behavioral", "native_policy": "pass",
                    "workflows": ["fixture-only-not-native-proof"]}
        candidate = package.candidate_manifest(self.path, package.verify(self.path), evidence)
        package.host.validate_candidate(self.ctx, candidate, qualification=False)
        self.assertEqual(candidate["on_spawn"]["supported_providers"], ["codex"])
        self.assertEqual(candidate["conformance"], {})
        self.assertIsNone(self.ctx.store.profile())

    def local_capability_fixture(self):
        if not self.ctx.management.exists():
            package.host.prepare_management(self.ctx)
        path, report = self.capability_fixture()
        receipt = package.verify(self.path)
        report.update(validation="local",
                      management_sha256=package.host.records.digest(package.host.management_inputs(self.ctx)),
                      qualifier=package.host.file_identity(self.path / "bin/forge-orchestrator-package"),
                      provider=receipt["definition"]["executable"],
                      worker=package.host.file_identity(receipt["worker_binaries"]["codex"]),
                      management_entry={"status": "not-run", "operator_positive": True,
                                        "observations": [{"kind": "worker", "pass": True}]})
        report["checks"] = dict.fromkeys(package.ADMISSION_CHECKS, "not-run")
        report["checks"].update(dict.fromkeys(package.LOCAL_RELEASE_CHECKS, "pass"))
        report["checks"]["authentication"] = "unavailable"
        path.write_text(package.encoded(report))
        return path, report, receipt

    def test_local_publication_keeps_native_grade_unverified_and_never_registers(self):
        path, report, receipt = self.local_capability_fixture()
        published = package.publish_candidate(self.path, path, local_release=True)
        self.assertEqual(published["admission"], "local-release-not-registered")
        candidate = package.host.read_json(published["candidate"])
        package.host.validate_candidate(self.ctx, candidate)
        self.assertEqual(candidate["providers"]["codex"]["evidence"]["native_policy"], "unverified")
        self.assertIsNone(self.ctx.store.profile())
        with self.assertRaisesRegex(package.host.Refusal, "promoted to native"):
            package.validate_capability(self.path, receipt, path)
        report["checks"]["native_policy"] = "pass"
        path.write_text(package.encoded(report))
        with self.assertRaisesRegex(package.host.Refusal, "disguise native results"):
            package.validate_capability(self.path, receipt, path, local_release=True)

    def test_local_publication_requires_every_local_gate_and_measured_worker_denial(self):
        path, report, receipt = self.local_capability_fixture()
        for gate in package.LOCAL_RELEASE_CHECKS:
            for value in (None, "not-run", "fail", "unsupported"):
                with self.subTest(gate=gate, value=value):
                    report["checks"].pop(gate, None)
                    if value is not None:
                        report["checks"][gate] = value
                    path.write_text(package.encoded(report))
                    with self.assertRaisesRegex(package.host.Refusal, gate):
                        package.validate_capability(self.path, receipt, path, local_release=True)
            report["checks"][gate] = "pass"
        for management in ({}, {"operator_positive": False, "observations": [{"kind": "worker", "pass": True}]},
                           {"operator_positive": True, "observations": []},
                           {"operator_positive": True, "observations": [{"kind": "native", "pass": True}]},
                           {"operator_positive": True, "observations": [{"kind": "worker", "pass": False}]}):
            report["management_entry"] = management
            path.write_text(package.encoded(report))
            with self.assertRaisesRegex(package.host.Refusal, "measured management worker denial"):
                package.validate_capability(self.path, receipt, path, local_release=True)

    def test_local_qualifier_never_loads_model_probes_or_authenticates(self):
        receipt = package.verify(self.path)
        expected_auth = [receipt["definition"]["executable"]["path"], "login", "status"]
        original_run = package.subprocess.run
        def run(argv, **kwargs):
            if argv[0] == "/usr/bin/git":
                return original_run(argv, **kwargs)
            self.assertEqual(argv, expected_auth)
            return subprocess.CompletedProcess(argv, 1, "", "not logged in")
        with patch.object(package, "module", side_effect=AssertionError("native probe module loaded")), \
                patch.object(package, "verify", return_value=receipt), \
                patch.object(package.runtime, "measure", return_value={"status": "pass"}), \
                patch.object(package, "policy_probes", return_value=("pass", [])), \
                patch.object(package, "hook_transport", return_value={"status": "pass"}), \
                patch.object(package, "management_probe", return_value={"status": "not-run", "operator_positive": True,
                    "observations": [{"kind": "worker", "pass": True}]}), \
                patch.object(package.subprocess, "run", side_effect=run), \
                patch.object(package, "publish_candidate", return_value={"admission": "fixture-only"}) as publish:
            # All effects are doubles; only exercise mode selection here.
            result = package.qualify(self.path, local_release=True)
            self.assertEqual(result["admission"], "fixture-only")
            self.assertEqual(result["checks"]["native_loading"], "not-run")
            self.assertEqual(result["checks"]["authentication"], "unavailable")
            self.assertTrue(publish.call_args.kwargs["local_release"])
        with self.assertRaisesRegex(package.host.Refusal, "never runs model-led"):
            package.qualify(self.path, native=True, local_release=True)

    def test_local_qualifier_cli_reports_failure_and_rejects_native_mode(self):
        argv = ["forge-orchestrator-package", "qualify", "--package", str(self.path)]
        for flags, admission, expected in (([], "unsupported", 0), (["--local-release"], "unsupported", 3),
                                           (["--local-release"], "local-release-not-registered", 0)):
            with self.subTest(flags=flags, admission=admission), \
                    patch.object(package.sys, "argv", argv + flags), \
                    patch.object(package, "qualify", return_value={"admission": admission}), patch("builtins.print"):
                self.assertEqual(package.main(), expected)
        with patch.object(package.sys, "argv", argv + ["--native", "--local-release"]), \
                patch("sys.stderr"), self.assertRaises(SystemExit) as conflict:
            package.main()
        self.assertEqual(conflict.exception.code, 2)

    def test_candidate_publication_refuses_each_missing_or_failed_gate(self):
        path, report = self.capability_fixture()
        receipt = package.verify(self.path)
        for gate in (*package.ADMISSION_CHECKS, "authentication"):
            original = report["checks"].pop(gate)
            for value in (None, "not-run", "fail", "unsupported"):
                with self.subTest(gate=gate, value=value):
                    if value is not None:
                        report["checks"][gate] = value
                    path.write_text(package.encoded(report))
                    with self.assertRaises(package.host.Refusal) as refused:
                        package.validate_capability(self.path, receipt, path)
                    self.assertEqual(refused.exception.code, "ORCHESTRATOR_NOT_QUALIFIED")
                    self.assertFalse((path.parent / "candidate.json").exists())
                    self.assertFalse((path.parent / "conformance.json").exists())
            report["checks"][gate] = original

    def test_candidate_publication_refuses_changed_or_unprotected_evidence(self):
        path, report = self.capability_fixture()
        report["topology_sha256"] = "0" * 64
        path.write_text(package.encoded(report))
        with self.assertRaisesRegex(package.host.Refusal, "package/root/topology"):
            package.publish_candidate(self.path, path)
        path, report = self.capability_fixture()
        artifact = Path(report["artifacts"][0]["path"])
        path.write_text(package.encoded(report))
        artifact.write_text("changed evidence")
        with self.assertRaises(package.host.Refusal) as refused:
            package.publish_candidate(self.path, path)
        self.assertEqual(refused.exception.code, "INPUT_CHANGED")
        report["artifacts"] = [package.host.file_identity(self.path / "package.json")]
        path.write_text(package.encoded(report))
        with self.assertRaisesRegex(package.host.Refusal, "protected measurement artifacts"):
            package.publish_candidate(self.path, path)
        report["artifacts"] = []
        path.write_text(package.encoded(report))
        with self.assertRaisesRegex(package.host.Refusal, "protected measurement artifacts"):
            package.publish_candidate(self.path, path)
        with self.assertRaises(package.host.Refusal) as refused:
            package.publish_candidate(self.path, self.root / "report.json")
        self.assertEqual(refused.exception.code, "QUALIFICATION_UNPROTECTED")
        self.assertIsNone(self.ctx.store.profile())

    def test_synthetic_publication_does_not_register_and_later_evidence_drift_refuses(self):
        # Only a schema/consumer integration fixture. No native qualification is
        # inferred from these synthetic statuses or the /bin/echo provider.
        if not self.ctx.management.exists():
            package.host.prepare_management(self.ctx)
        path, report = self.capability_fixture()
        receipt = package.verify(self.path)
        report.update(management_sha256=package.host.records.digest(package.host.management_inputs(self.ctx)),
                      qualifier=package.host.file_identity(self.path / "bin/forge-orchestrator-package"),
                      provider=receipt["definition"]["executable"],
                      worker=package.host.file_identity(receipt["worker_binaries"]["codex"]))
        path.write_text(package.encoded(report))
        published = package.publish_candidate(self.path, path)
        candidate = package.host.read_json(published["candidate"])
        package.host.validate_candidate(self.ctx, candidate)
        self.assertEqual(published["admission"], "qualified-not-registered")
        self.assertIsNone(self.ctx.store.profile())
        Path(report["artifacts"][0]["path"]).write_text("Changed synthetic evidence")
        with self.assertRaises(package.host.Refusal) as refused:
            package.host.validate_candidate(self.ctx, candidate)
        self.assertEqual(refused.exception.code, "INPUT_CHANGED")

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
                # Identity is hashed via file_sha256, so the staging copy is the first file_bytes read.
                if reads == 1:
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

    def test_model_policy_summary_requires_nonce_and_exact_operation_digest(self):
        receipt = package.verify(self.path)
        canary = self.root / "synthetic-canary"
        expected = [{"path": str(canary), "operation": operation, "denied": True}
                    for operation in ("create", "overwrite", "rename", "unlink", "symlink-redirection")]
        digest = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
        good = {"nonce": "fixture", "opened": [], "tested": 1, "workspace_control": True,
                "operations_count": 5, "operations_sha256": digest}
        for patch_value in ({}, {"nonce": "stale"}, {"operations_sha256": "0" * 64},
                            {"operations_count": 4}, {"workspace_control": False}, {"opened": [str(canary)]}):
            directory = self.ctx.store.path / "reports" / ("policy-fixture-" + uuid.uuid4().hex)
            directory.mkdir(parents=True)
            def runner(actual_receipt, argv, cwd, transcript):
                self.assertEqual(actual_receipt, receipt)
                self.assertEqual(argv[1:4], ["-I", "-S", "-B"])
                self.assertNotIn("sandbox", argv)
                self.assertEqual(argv[-1], "fixture")
                return subprocess.CompletedProcess(argv, 0, json.dumps(dict(good, **patch_value)), "")
            with self.subTest(patch=patch_value):
                status, rows = package._policy_probes(self.path, receipt, directory, Path("/unused/worker"),
                    [{"root": str(self.root)}], [str(canary)], "fixture", [canary], native_runner=runner)
                self.assertEqual(status, "fail" if patch_value else "pass")
                self.assertEqual(len(rows), 1)

    def test_model_policy_script_rejects_uncontained_execution(self):
        receipt = package.verify(self.path)
        directory = self.ctx.store.path / "reports" / ("uncontained-fixture-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        canary = self.root / "uncontained-owned-canary"
        canary.write_text("owned fixture")
        def runner(actual_receipt, argv, cwd, transcript):
            return subprocess.run(argv, cwd=cwd, env=receipt["environment"], capture_output=True, text=True, timeout=10)
        try:
            status, rows = package._policy_probes(self.path, receipt, directory, Path("/unused/worker"),
                [{"root": str(self.root)}], [str(canary)], "uncontained", [canary], native_runner=runner)
            self.assertEqual(status, "fail")
            self.assertEqual(rows[0]["result"]["opened"], [str(canary)])
            self.assertTrue(rows[0]["result"]["workspace_control"])
        finally:
            canary.unlink(missing_ok=True)

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
        trust = Path(env["CODEX_HOME"]) / "config.toml"
        old_trust = trust.read_bytes()
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
            trust.chmod(0o600)
            trust.write_text(old_trust.decode() + '\n[hooks.state.fixture]\ntrusted_hash = "sha256:' + 'a' * 64 + '"\n')
            cp = broker("control", "--expect-runtime-sha256", identity["sha256"], request={"action": "inspect-runtime"})
            self.assertEqual(cp.returncode, 0, cp.stderr)
            cp = broker("start", "--session", "candidate", "--incarnation", incarnation, "--mode", "contain")
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertEqual(json.loads(cp.stdout)["pid"], info["pid"])
            trust.write_text(old_trust.decode() + 'sandbox_mode = "danger-full-access"\n')
            cp = broker("control", "--expect-runtime-sha256", identity["sha256"], request={"action": "inspect-runtime"})
            self.assertNotEqual(cp.returncode, 0)
            trust.write_bytes(old_trust)
            changed.chmod(0o600)
            changed.write_text("changed after startup")
            cp = broker("status", "--json")
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertEqual(json.loads(cp.stdout)["runtime_state"], "changed")
            cp = broker("control", "--expect-runtime-sha256", identity["sha256"], request={"action": "inspect-runtime"})
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("BROKER_RUNTIME_CHANGED", cp.stderr)
        finally:
            trust.chmod(0o600)
            trust.write_bytes(old_trust)
            trust.chmod(0o400)
            changed.chmod(0o600)
            changed.write_bytes(old)
            changed.chmod(0o400)
            if started:
                cp = broker("stop", "--timeout", "5")
                self.assertEqual(cp.returncode, 0, cp.stderr)
            mux("kill-server")


if __name__ == "__main__":
    unittest.main()
