"""P2 request transport tests; stubs never qualify a native host or profile."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv

import yaml


ROOT = Path(__file__).resolve().parents[2]


class RequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = tempfile.TemporaryDirectory(prefix="forge-p2-python-")
        cls.addClassCleanup(cls.runtime.cleanup)
        env = Path(cls.runtime.name)
        venv.EnvBuilder(with_pip=False).create(env)
        # Fake HOME must not hide the test's PyYAML dependency. Install only the
        # already-imported package into this disposable venv, without env prefixes.
        site = env / "lib" / ("python%d.%d" % sys.version_info[:2]) / "site-packages"
        shutil.copytree(Path(yaml.__file__).parent, site / "yaml")
        cls.python_bin = env / "bin"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="forge-p2-request-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "project"
        self.home = self.base / "home"
        self.shim = self.base / "shim"
        for path in (self.root, self.home, self.shim):
            path.mkdir()
        subprocess.run(["/usr/bin/git", "init", "-q", str(self.root)], check=True)
        self.log = self.base / "calls.log"
        self.hook_output = self.base / "hook.json"
        self.script("tmux", """#!/bin/bash
printf 'tmux %s\\n' "$*" >> "$P2_LOG"
case "$1" in
  has-session) exit "${P2_HAS:-1}" ;;
  new-session) [ -z "${P2_RACE_FILE:-}" ] || printf 'sample\\t%s\\n' "$P2_ROOT" > "$P2_RACE_FILE"; exit "${P2_NEW_RC:-0}" ;;
  list-sessions)
    if [ -n "${P2_DISCOVER_FILE:-}" ]; then
      if [ -f "$P2_DISCOVER_FILE" ]; then cat "$P2_DISCOVER_FILE"
      else printf 'sample\\t%s\\n' "$P2_ROOT" > "$P2_DISCOVER_FILE"; fi
    fi
    [ -n "${P2_RACE_FILE:-}" ] && [ -f "$P2_RACE_FILE" ] && cat "$P2_RACE_FILE"; exit 1 ;;
  list-panes)
    case "$*" in
      *pane_left*) printf '0 0 0 200 200\\n1 0 20 99 200\\n2 0 35 99 200\\n3 100 20 100 200\\n4 100 35 100 200\\n' ;;
      *) seq 0 "${P2_LAST_PANE:-4}" ;;
    esac ;;
  display-message) case "$*" in *session_created*) echo 1234 ;; *) echo "$P2_ROOT" ;; esac ;;
  show-environment) echo 'TMUX_SESSION=sample' ;;
esac
exit 0
""")
        self.script("helper", """#!/bin/bash
printf 'helper %s\\n' "$*" >> "$P2_LOG"
case "$1" in codex-doctor) echo launch_ready=true ;; esac
""")
        self.script("hook", "#!" + str(self.python_bin / "python3") + "\n" +
                    "import json,os,sys\n" +
                    "with open(os.environ['P2_HOOK_OUTPUT'],'w') as f:\n" +
                    " json.dump({'argv':sys.argv[1:],'provider':os.environ.get('FORGE_ORCHESTRATOR_PROVIDER'),"
                    "'version':os.environ.get('FORGE_ORCHESTRATOR_REQUEST_VERSION')},f)\n")
        self.env = {
            "HOME": str(self.home), "PATH": f"{self.shim}:{self.python_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "P2_LOG": str(self.log), "P2_ROOT": str(self.root), "P2_HOOK_OUTPUT": str(self.hook_output),
            "FORGE_TMUX_BIN": str(self.shim / "tmux"), "FORGE_BIN": str(self.shim / "helper"),
            "FORGE_BRIDGE_BIN": str(self.shim / "helper"), "FORGE_BROKER_BIN": str(self.shim / "helper"),
            "FORGE_CODEX_DOCTOR_BIN": str(self.shim / "helper"), "FORGE_START_BIN": str(self.shim / "hook"),
            "FORGE_WATCH_TRIGGER": "0", "FORGE_REGISTRY_FILE": str(self.base / "registry.yml"),
            "FORGE_START_ATTACH": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        }

    def script(self, name, content):
        path = self.shim / name
        path.write_text(content)
        path.chmod(0o755)
        return path

    def invoke(self, command, args, **env):
        prefix = [str(ROOT / "bin/forge"), "spawn"] if command == "spawn" else [str(ROOT / "bin/forge-start")]
        return subprocess.run([*prefix, *args], cwd=self.root, env=dict(self.env, **env),
                              text=True, capture_output=True, timeout=20)

    def assert_no_effect(self, result, diagnostic):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(diagnostic, result.stderr)
        self.assertFalse(self.log.exists(), self.log.read_text() if self.log.exists() else "")
        self.assertFalse(self.hook_output.exists())
        self.assertFalse((self.base / "registry.yml").exists())
        self.assertFalse((self.root / ".dev").exists())
        self.assertFalse((self.root / ".claude/settings.json").exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_invalid_provider_before_any_effect(self):
        for command in ("start", "spawn"):
            for value in ("Claude", "openai", "codex; echo unsafe", "--here", " "):
                with self.subTest(command=command, value=value):
                    self.assert_no_effect(self.invoke(command, ["--orchestrator", value]), "claude|codex")

    def test_missing_empty_and_duplicate_provider_before_effects(self):
        for command in ("start", "spawn"):
            for args in (["--orchestrator"], ["--orchestrator", ""],
                         ["--orchestrator", "claude", "--orchestrator", "claude"],
                         ["--orchestrator", "claude", "--orchestrator", "codex"]):
                with self.subTest(command=command, args=args):
                    self.assert_no_effect(self.invoke(command, args), "orchestrator")

    def test_codex_request_never_falls_back_or_creates_state(self):
        for command in ("start", "spawn"):
            for args in (["--orchestrator", "codex", "sample"],
                         ["sample", "--orchestrator", "codex"]):
                with self.subTest(command=command, args=args):
                    self.assert_no_effect(self.invoke(command, args), "ORCHESTRATOR_NOT_QUALIFIED")

    def test_codex_refusal_also_gates_bypass_like_options(self):
        for args in (["--dry-run"], ["--no-populate"], ["--force"], ["--allow-api-billing"]):
            with self.subTest(args=args):
                self.assert_no_effect(self.invoke("spawn", [*args, "--orchestrator", "codex"]),
                                      "ORCHESTRATOR_NOT_QUALIFIED")
        self.assert_no_effect(self.invoke("start", ["--here", "--no-attach", "--orchestrator", "codex"],
                                          FORGE_START_CODEX_PREFLIGHT="0"), "ORCHESTRATOR_NOT_QUALIFIED")

    def test_project_provider_claim_does_not_authorize_hook(self):
        config = self.root / ".claude/forge-project.yml"
        config.parent.mkdir()
        config.write_text(yaml.safe_dump({"forge": {"control_center": {
            "on_spawn": [str(self.shim / "hook")], "supported_providers": ["claude", "codex"]}}}))
        self.assert_no_effect(self.invoke("spawn", ["--orchestrator", "codex"]), "ORCHESTRATOR_NOT_QUALIFIED")

    def test_populate_codex_stamp_is_request_not_authority(self):
        self.assert_no_effect(self.invoke("start", ["--populate-existing", "sample"],
            FORGE_ORCHESTRATOR_PROVIDER="codex", FORGE_ORCHESTRATOR_REQUEST_VERSION="1"),
            "ORCHESTRATOR_NOT_QUALIFIED")

    def test_populate_rejects_incomplete_unknown_or_contradictory_transport(self):
        for stamp in ({"FORGE_ORCHESTRATOR_PROVIDER": "claude"},
                      {"FORGE_ORCHESTRATOR_REQUEST_VERSION": "1"},
                      {"FORGE_ORCHESTRATOR_PROVIDER": "", "FORGE_ORCHESTRATOR_REQUEST_VERSION": "1"},
                      {"FORGE_ORCHESTRATOR_PROVIDER": "claude", "FORGE_ORCHESTRATOR_REQUEST_VERSION": "2"},
                      {"FORGE_ORCHESTRATOR_PROVIDER": "codex", "FORGE_ORCHESTRATOR_REQUEST_VERSION": "1"}):
            with self.subTest(stamp=stamp):
                self.assert_no_effect(self.invoke("start", ["--orchestrator", "claude", "--populate-existing", "sample"], **stamp),
                                      "orchestrator request")

    def test_start_rejects_extra_args_instead_of_ignoring_provider(self):
        for args in (["sample", "extra"], ["sample", "--unknown"], ["--populate-existing"],
                     ["--populate-existing", "--here"], ["sample", "--populate-existing", "other"]):
            with self.subTest(args=args):
                self.assert_no_effect(self.invoke("start", args), "forge-start:")

    def test_start_claude_request_preserves_launch_argv(self):
        result = self.invoke("start", ["sample", "--here", "--orchestrator", "claude"])
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.log.read_text()
        self.assertIn("tmux send-keys -t sample:.0 claude --model claude-opus-5 Enter", log)
        self.assertNotIn("respawn-pane", log)
        self.assertNotIn("attach-session", log)

    def test_start_ambient_request_does_not_become_global_default(self):
        result = self.invoke("start", ["--here", "sample"],
                             FORGE_ORCHESTRATOR_PROVIDER="codex", FORGE_ORCHESTRATOR_REQUEST_VERSION="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("claude --model claude-opus-5", self.log.read_text())

    def test_populate_valid_transport_keeps_existing_positional_argv(self):
        result = self.invoke("start", ["--populate-existing", "sample"], P2_HAS="0", P2_LAST_PANE="0",
                             FORGE_ORCHESTRATOR_PROVIDER="claude", FORGE_ORCHESTRATOR_REQUEST_VERSION="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FORGE_ROLE=orchestrator claude --model claude-opus-5", self.log.read_text())

    def test_spawn_builtin_receives_versioned_stamp_and_same_positional_args(self):
        result = self.invoke("spawn", ["sample", "--orchestrator", "claude"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.hook_output.read_text()),
                         {"argv": ["--populate-existing", "sample"], "provider": "claude", "version": "1"})

    def test_spawn_legacy_custom_hook_gets_stamp_without_argv_change(self):
        config = self.root / ".claude/forge-project.yml"
        config.parent.mkdir()
        config.write_text(yaml.safe_dump({"forge": {"control_center": {
            "on_spawn": [str(self.shim / "hook"), "literal argument with spaces"]}}}))
        result = self.invoke("spawn", ["sample"], FORGE_ORCHESTRATOR_PROVIDER="codex", FORGE_ORCHESTRATOR_REQUEST_VERSION="999")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.hook_output.read_text()),
                         {"argv": ["literal argument with spaces", "sample"], "provider": "claude", "version": "1"})

    def test_explicit_reuse_refuses_without_touching_legacy_session(self):
        listing = self.base / "sessions.tsv"
        listing.write_text("existing\t" + str(self.root) + "\n")
        for args in ([], ["--dry-run"]):
            with self.subTest(args=args):
                self.assert_no_effect(self.invoke("spawn", ["--orchestrator", "claude", *args],
                    FORGE_TMUX_LIST=str(listing)), "ORCHESTRATOR_BINDING_UNAVAILABLE")

    def test_omitted_provider_retains_legacy_ensure_without_population(self):
        listing = self.base / "sessions.tsv"
        listing.write_text("existing\t" + str(self.root) + "\n")
        result = self.invoke("spawn", [], FORGE_TMUX_LIST=str(listing))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ENSURE @existing", result.stdout)
        self.assertFalse(self.hook_output.exists())
        log = self.log.read_text()
        for operation in ("new-session", "respawn-pane", "send-keys", "kill-session"):
            self.assertNotIn(operation, log)

    def test_explicit_provider_race_does_not_claim_matching_host(self):
        result = self.invoke("spawn", ["sample", "--orchestrator", "claude"],
                             P2_NEW_RC="1", P2_RACE_FILE=str(self.base / "race.tsv"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ORCHESTRATOR_BINDING_UNAVAILABLE", result.stderr)
        self.assertFalse(self.hook_output.exists())
        log = self.log.read_text()
        self.assertEqual(log.count("tmux new-session"), 1)
        for operation in ("respawn-pane", "send-keys", "kill-session"):
            self.assertNotIn(operation, log)

    def test_explicit_name_still_checks_existing_session(self):
        result = self.invoke("start", ["forge-42", "--here", "--orchestrator", "claude"],
                             COUNT="42", P2_HAS="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)
        self.assertEqual(self.log.read_text(), "tmux has-session -t forge-42\n")

    def test_session_appearing_after_preflight_is_not_an_explicit_match(self):
        result = self.invoke("spawn", ["sample", "--orchestrator", "claude"],
                             P2_DISCOVER_FILE=str(self.base / "discovered.tsv"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ORCHESTRATOR_BINDING_UNAVAILABLE", result.stderr)
        self.assertFalse(self.hook_output.exists())
        log = self.log.read_text()
        for operation in ("new-session", "respawn-pane", "send-keys", "kill-session"):
            self.assertNotIn(operation, log)
