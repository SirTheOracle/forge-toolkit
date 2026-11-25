#!/usr/bin/env python3
"""Exercise the real proposed CLI contract using disposable roots and fake tmux only."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FORGE = ROOT / "bin/forge"
BRIDGE = ROOT / "bin/forge-bridge"


def function(name):
    source = BRIDGE.read_text()
    match = re.search(r"(?m)^" + re.escape(name) + r"\(\) \{", source)
    if match is None:
        raise AssertionError("Missing required bridge function: " + name)
    end = re.search(r"(?m)^\}$", source[match.start():])
    if end is None:
        raise AssertionError("Missing function end: " + name)
    return source[match.start():match.start() + end.end()]


class Prerequisites(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="forge-web-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / ".dev").mkdir()
        (self.root / ".claude").mkdir()
        (self.root / ".claude/forge-project.yml").write_text("project: fixture\n")
        (self.root / "home").mkdir()
        (self.root / "tools").mkdir()
        self.tsv = self.root / "sessions.tsv"
        self.tsv.write_text(f"forge-review\t{self.root}\t$4\t100\n")
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("FORGE_") and k not in ("TMUX", "TMUX_PANE", "TMUX_SESSION")}
        self.env.update(HOME=str(self.root / "home"), FORGE_WATCH_TRIGGER="0",
                        FORGE_TMUX_LIST=str(self.tsv), FORGE_DISPATCH_DRY_RUN="1",
                        FORGE_BRIDGE_BIN=str(BRIDGE), FORGE_FORGE_BIN=str(FORGE),
                        PATH=str(self.root / "tools") + os.pathsep + self.env.get("PATH", ""))
        # Refuse unexpected probes; no test can reach the operator's tmux binary.
        stub = self.root / "tools/tmux"
        stub.write_text("#!/bin/bash\nprintf 'unexpected tmux call\\n' >&2\nexit 97\n")
        stub.chmod(0o755)
        self.env["FORGE_TMUX_BIN"] = str(stub)

    def cli(self, tool, *args, env=None):
        return subprocess.run(["bash", str(tool), *args], cwd=self.root,
                              env=self.env if env is None else env,
                              capture_output=True, text=True, timeout=15)

    def test_capability_markers_have_no_state_side_effects(self):
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        for tool in (FORGE, BRIDGE):
            result = self.cli(tool, "web-contract-version")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "forge-web-contract/1\n")
        after = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        self.assertEqual(before, after)

    def test_expected_literal_dispatch_and_replacement_refusal(self):
        text = "first line\n--target-session @other $literal"
        args = ["dispatch", "@forge-review", "--allow-api-billing", "--pointer",
                "--expected-root", str(self.root), "--expected-session-id", "$4",
                "--expected-incarnation", "100", "--", text]
        result = self.cli(FORGE, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = list((self.root / ".dev/attention").glob("dispatch-*.json"))
        self.assertEqual(len(records), 1)
        record = json.loads(records[0].read_text())
        self.assertEqual((self.root / record["payload_path"]).read_text(), text)
        self.assertEqual(record["target_pane"], 0)
        self.tsv.write_text(f"forge-review\t{self.root}\t$5\t100\n")
        result = self.cli(FORGE, *args)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expectation failed", result.stderr)
        self.assertEqual(list((self.root / ".dev/attention").glob("dispatch-*.json")), records)

    def test_expanded_ask_is_redacted_bounded_and_retains_raw_hash(self):
        question = "token=fixture-secret\n" + "é" * 40000
        env = dict(self.env, TMUX_SESSION="forge-review")
        result = self.cli(FORGE, "ask", "--root", str(self.root), "--session-scope", question, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        path, = (self.root / ".dev/attention").glob("ask-*.json")
        record = json.loads(path.read_text())
        self.assertNotIn("fixture-secret", record["question_text"])
        self.assertTrue(record["question_text_truncated"])
        self.assertLessEqual(len(record["question_text"].encode()), 65536)
        self.assertGreater(record["question_text_bytes"], 65536)
        self.assertEqual(record["question_sha256"], hashlib.sha256(question.encode()).hexdigest())
        self.assertLess(path.stat().st_size, 1 << 20)

    def test_callback_revalidation_rejects_same_second_object_replacement(self):
        script = function("_callback_selection_unchanged") + r'''
_target_ident_of() { printf '$5\t100\n'; }
session_incarnation_of() { printf '100'; }
_callback_mutation_select() { printf 'unchanged'; }
_FORGE_EXPECT_SID='$4'
_FORGE_VALID_SESSION=forge-review
_FORGE_VALID_SID='$4'
_FORGE_VALID_CREATED=100
_callback_selection_unchanged unchanged /fixture demo coding forge-review 100
'''
        result = subprocess.run(["bash", "-c", script], env=self.env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CALLBACK_IDENTITY_CHANGED", result.stderr)
        legacy = script.replace("_FORGE_EXPECT_SID='$4'", "_FORGE_EXPECT_SID=''")
        result = subprocess.run(["bash", "-c", legacy], env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_expected_delivery_uses_one_pane_object_for_every_key_command(self):
        script = function("_pin_pane_object") + "\n" + function("_worker_send_locked") + r'''
_tmux_probe() { printf '%%99\t$4\t%s\t100\n' "$FIXTURE_ROOT"; }
ownership_root() { printf '%s' "$1"; }
tmux() { printf '%s\n' "$*" >> "$FIXTURE_COMMANDS"; }
_FORGE_EXPECT_SID='$4'
_FORGE_EXPECT_INC=100
_FORGE_VALID_ROOT="$FIXTURE_ROOT"
_worker_send_locked forge-review:0.0 'literal message'
'''
        commands = self.root / "commands"
        env = dict(self.env, FIXTURE_ROOT=str(self.root), FIXTURE_COMMANDS=str(commands))
        result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = commands.read_text().splitlines()
        self.assertGreaterEqual(len(lines), 3)
        self.assertTrue(all("-t %99" in line for line in lines), lines)
        self.assertFalse(any("forge-review:" in line for line in lines), lines)

    def test_expected_missing_callback_is_conflict_legacy_remains_noop(self):
        script = function("_park_expect_ok") + "\n" + function("cmd_park") + r'''
_resolve_project_root() { printf '%s' "$FIXTURE_ROOT"; }
_bind_target_root() { printf '%s' "$FIXTURE_ROOT"; }
require_identity() { ID_target_session=forge-review; ID_target_incarnation=100; }
_require_root_cwd() { return 0; }
_callback_mutation_select() { return 1; }
cmd_park --slug demo --stage coding --reason wait --expected-pending-ts t --expected-callback-id cb --expected-callback-sha256 0000000000000000000000000000000000000000000000000000000000000000
'''
        env = dict(self.env, FIXTURE_ROOT=str(self.root))
        result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EXPECTATION MISMATCH", result.stderr)
        legacy = script[:script.rfind("cmd_park --slug")] + "cmd_park --slug demo --stage coding --reason wait\n"
        result = subprocess.run(["bash", "-c", legacy], env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PARK NOOP", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
