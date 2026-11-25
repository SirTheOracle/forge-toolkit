"""Real local Git effects under the candidate config guard, not model evidence."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TREE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("git_guard", TREE / "lib/forge_git_entry.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GitRuntime(unittest.TestCase):
    def setUp(self):
        fixtures = TREE / ".dev/forge-p3"
        fixtures.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="git-guard-", dir=fixtures)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "root"
        self.root.mkdir()
        self.runtime = self.base / "runtime"
        self.runtime.mkdir()
        (self.runtime / "bin").mkdir()
        self.env = {"PATH": "/usr/bin:/bin", "HOME": str(self.base), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": "/dev/null"}
        binary = shutil.which("git", path=self.env["PATH"])
        if sys.platform == "darwin":
            binary = subprocess.check_output(["/usr/bin/xcrun", "--find", "git"], env=self.env, text=True).strip()
        self.binary = binary
        exec_path = subprocess.check_output([binary, "--exec-path"], env=self.env, text=True).strip()
        (self.runtime / "runtime.json").write_text(json.dumps({"tools": {"git": binary}, "git_exec_path": exec_path}))
        shutil.copyfile(TREE / "lib/forge_git_entry.py", self.runtime / "git_entry.py")
        self.raw("init", "-q")
        self.raw("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm", "base")
        self.config = self.root / ".git/config"
        self.original = self.config.read_text()
        self.marker = self.base / "executed"

    def raw(self, *args):
        return subprocess.run([self.binary, "-C", str(self.root), *args], env=self.env,
                              text=True, capture_output=True, check=True)

    def run_guard(self, *args, cwd=None, extra=None):
        return subprocess.run([sys.executable, "-I", "-S", "-B", str(self.runtime / "git_entry.py"), *args],
                              cwd=cwd or self.root, env=dict(self.env, **(extra or {})),
                              text=True, capture_output=True, timeout=10)

    def test_normal_primary_and_linked_commands_work(self):
        result = self.run_guard("rev-parse", "--show-toplevel")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.root))
        linked = self.base / "linked"
        result = self.run_guard("worktree", "add", "-qb", "linked", str(linked))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_guard("status", "--porcelain", cwd=linked)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(guard.repository_configs(linked)[0], self.config)

    def test_include_and_conditional_include_are_refused_without_opening_fifo(self):
        fifo = self.base / "include-fifo"
        os.mkfifo(fifo)
        for section in ('include', 'includeIf "gitdir:**/root/.git"'):
            with self.subTest(section=section):
                self.config.write_text(self.original + '\n[' + section + ']\npath = ' + str(fifo) + '\n')
                result = self.run_guard("status", "--porcelain")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("GIT_CONFIG_EXECUTION_REFUSED", result.stderr)

    def test_executable_config_does_not_execute(self):
        cases = [('core', 'fsmonitor'), ('core', 'sshCommand'), ('filter "owned"', 'clean'),
                 ('diff "owned"', 'textconv'), ('merge "owned"', 'driver'), ('core', 'alternateRefsCommand'),
                 ('credential', 'helper'), ('remote "origin"', 'uploadpack'), ('gpg', 'program'), ('futureHook', 'command')]
        for section, key in cases:
            with self.subTest(section=section, key=key):
                self.config.write_text(self.original + '\n[' + section + ']\n' + key + ' = /usr/bin/touch ' + str(self.marker) + '\n')
                result = self.run_guard("status", "--porcelain")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(self.marker.exists())

    def test_linked_worktree_config_and_post_staging_additions_are_checked(self):
        linked = self.base / "linked"
        self.raw("worktree", "add", "-qb", "linked", str(linked))
        config = guard.repository_configs(linked)[1]
        config.write_text('[core]\nfsmonitor = /usr/bin/touch ' + str(self.marker) + '\n')
        result = self.run_guard("status", "--porcelain", cwd=linked)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.marker.exists())

    def test_default_repository_hooks_are_disabled(self):
        hook = self.root / ".git/hooks/post-checkout"
        hook.write_text('#!/bin/sh\n/usr/bin/touch "' + str(self.marker) + '"\n')
        hook.chmod(0o755)
        result = self.run_guard("worktree", "add", "-qb", "linked", str(self.base / "linked"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists())

    def test_environment_injection_cannot_enable_fsmonitor(self):
        result = self.run_guard("status", "--porcelain", extra={"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "/usr/bin/touch " + str(self.marker), "GIT_CONFIG_PARAMETERS": "'core.fsmonitor=bad'",
            "GIT_EXEC_PATH": str(self.base), "GIT_SSH_COMMAND": "/usr/bin/touch " + str(self.marker)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists())

    def test_config_alias_and_nonregular_file_refuse(self):
        self.config.unlink()
        self.config.symlink_to(self.base / "missing")
        result = self.run_guard("status", "--porcelain")
        self.assertEqual(result.returncode, 2)
        self.config.unlink()
        os.mkfifo(self.config)
        result = self.run_guard("status", "--porcelain")
        self.assertEqual(result.returncode, 2)

    def test_incomplete_git_directory_cannot_hide_ancestor_config(self):
        nested = self.root / "nested"
        (nested / ".git").mkdir(parents=True)
        self.config.write_text(self.original + '\n[core]\nfsmonitor = /usr/bin/touch ' + str(self.marker) + '\n')
        result = self.run_guard("status", "--porcelain", cwd=nested)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.marker.exists())

    def test_argv_cannot_select_unchecked_configuration_or_helper(self):
        for args in (("-c", "include.path=/evil", "status"), ("--exec-path=/evil", "status"),
                     ("--config-env=core.fsmonitor=EVIL", "status"), ("config", "--file", "/evil", "--list"),
                     ("fetch", "--upload-pack=/evil", "origin"), ("custom-command",)):
            with self.subTest(args=args):
                self.assertEqual(self.run_guard(*args).returncode, 2)
        self.assertEqual(self.run_guard("config", "--local", "--null", "--list").returncode, 0)

    def test_abbreviated_executable_options_and_transport_overrides_refuse(self):
        for args in (("fetch", "--upload-p=/evil", "origin"), ("fetch", "--rec=yes", "origin"),
                     ("merge", "--strat=evil", "--ff-only", "HEAD"), ("merge", "-sevil", "--ff-only", "HEAD"),
                     ("merge", "--no-ff", "HEAD"), ("-c", "protocol.ext.allow=always", "fetch", "origin")):
            self.assertEqual(self.run_guard(*args).returncode, 2)
        result = self.run_guard("merge", "--ff-only", "-q", "HEAD")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
