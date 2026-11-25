import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import native_probe
import runtime_probe


class AskCallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="forge-p1-ask-")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "workspace"
        self.package = base / "operator/package"
        (self.package / "bin").mkdir(parents=True)
        shutil.copyfile(native_probe.SOURCE / "bin/forge", self.package / "bin/forge")
        runtime_probe.stage(self.package)
        # A contained worker cannot query tmux; this is a hermetic refusal stub.
        (self.package / "runtime/bin/tmux").write_text("#!/bin/sh\nexit 1\n")
        (self.root / ".claude").mkdir(parents=True)
        (self.root / ".claude/forge-project.yml").write_text("name: ask-fixture\n")
        proposal = self.root / ".dev/proposals/ask-fixture"
        proposal.mkdir(parents=True)
        (proposal / "forge-log.yml").write_text(
            'entries:\n  - timestamp: "2026-09-14T00:00:00Z"\n'
            '    stage: adhoc\n    to: codex-a\n    response: null\n')
        self.log = base / "callback.json"
        bridge = self.package / "bin/forge-bridge"
        bridge.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                          'with open(os.environ["CALLBACK_LOG"],"w") as f:\n'
                          ' json.dump({"argv":sys.argv[1:],"cwd":os.getcwd()},f)\n'
                          'raise SystemExit(int(os.environ.get("CALLBACK_RC","0")))\n')
        bridge.chmod(0o755)
        self.env = dict(runtime_probe.environment(self.package, base / "home"),
                        CALLBACK_LOG=str(self.log), TMUX_PANE="%3", TMUX_SESSION="ask-session")

    def ask(self, *args, failure=False):
        env = dict(self.env, CALLBACK_RC="7" if failure else "0")
        result = subprocess.run(["/bin/bash", str(self.package / "bin/forge"), "ask",
                                 "--slug", "ask-fixture", "--stage", "adhoc",
                                 "--worker", "codex-a", *args, "May I proceed?"],
                                cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result, json.loads(self.log.read_text())

    def test_local_ask_uses_self_callback_when_tmux_is_denied(self):
        _, call = self.ask()
        self.assertNotIn("--cross-session", call["argv"])
        self.assertNotIn("--target-session", call["argv"])
        self.assertEqual(call["cwd"], os.path.realpath(self.root))
        self.assertEqual(call["argv"], ["callback", "--slug", "ask-fixture", "--stage", "adhoc",
                                       "--worker", "codex-a", "--status", "BLOCKED",
                                       "--origin", "ask", "--message", "May I proceed?", "--quiet"])

    def test_explicit_root_relay_retains_cross_session_validation(self):
        _, call = self.ask("--root", str(self.root))
        self.assertEqual(call["argv"][:4], ["callback", "--target-session", "ask-session", "--cross-session"])

    def test_callback_failure_remains_fail_open_and_keeps_attention(self):
        result, _ = self.ask(failure=True)
        self.assertIn("bridge BLOCKED callback failed", result.stderr)
        asks = list((self.root / ".dev/attention").glob("ask-*.json"))
        self.assertEqual(len(asks), 1)
        self.assertEqual(json.loads(asks[0].read_text())["session"], "ask-session")
