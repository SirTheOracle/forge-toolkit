#!/usr/bin/env python3
"""Socket binding tests without a live tmux server or broker daemon."""

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


path = Path(__file__).resolve().parents[2] / "bin/forge-broker"
loader = importlib.machinery.SourceFileLoader("forge_broker", str(path))
spec = importlib.util.spec_from_loader(loader.name, loader)
broker = importlib.util.module_from_spec(spec)
loader.exec_module(broker)


class SocketTests(unittest.TestCase):
    def test_default_context_preserves_legacy_binding(self):
        self.assertEqual(broker.tmux_socket_from_context(""), "")
        default = f"/tmp/tmux-{os.getuid()}/default,123,0"
        self.assertEqual(broker.tmux_socket_from_context(default), "")

    def test_custom_context_and_comma_in_socket_name(self):
        self.assertEqual(broker.tmux_socket_from_context("/tmp/probe,one,123,2"),
                         str(Path("/tmp/probe,one").resolve()))

    def test_malformed_context_never_falls_back_to_default(self):
        for context in ("garbage", "relative,123,0", "/tmp/a,no,0", "/tmp/a,123,no", "/tmp/\0a,123,0"):
            with self.subTest(context=context), self.assertRaises(broker.Refusal):
                broker.tmux_socket_from_context(context)

    def test_both_tmux_probes_use_bound_socket_and_minimal_environment(self):
        answer = subprocess.CompletedProcess([], 0, "123\n", "")
        with patch.object(broker, "TMUX_SOCKET", "/tmp/p1-owned"), \
                patch.dict(os.environ, {"TMUX": "/tmp/other,456,0", "FORGE_BROKER_TEST_ENABLE": "0"}), \
                patch.object(broker.subprocess, "run", return_value=answer) as run:
            self.assertEqual(broker.tmux_field("fixture:.3", "#{pane_pid}"), "123")
            self.assertEqual(broker.tmux_session_created("fixture"), "123")
        for call in run.call_args_list:
            self.assertEqual(call.args[0][:3], ["/opt/homebrew/bin/tmux", "-S", "/tmp/p1-owned"])
            self.assertEqual(call.kwargs["env"], {"PATH": "/usr/bin:/bin:/opt/homebrew/bin"})

    def test_request_cannot_select_a_socket(self):
        request = {"action": "verify-session-incarnation", "session": "fixture",
                   "expected_incarnation": "123", "tmux_socket": "/tmp/attacker"}
        with patch.object(broker.subprocess, "run") as run:
            with self.assertRaises(broker.Refusal) as raised:
                broker.verify_session_incarnation(request, caller="control")
            self.assertEqual(raised.exception.code, "CONTROL_FIELDS_DENIED")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
