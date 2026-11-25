"""Worker launch argv tests with a recording executable, never a model call."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class WorkerModelTests(unittest.TestCase):
    def test_exec_uses_requested_model_and_effort_without_changing_containment(self):
        source = (ROOT / "bin/forge").read_text()
        start = source.index("cmd_codex_launch() {")
        end = source.index("\ncmd_codex_broker() {", start)
        function = source[start:end]
        with tempfile.TemporaryDirectory(prefix="forge-worker-model-") as directory:
            toolkit = Path(directory)
            executable = toolkit / "record-codex"
            executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            executable.chmod(0o700)
            (toolkit / "config").mkdir()
            (toolkit / "config/codex-forge-runtime.json").write_text(
                json.dumps({"codex": {"binary": str(executable)}}))
            script = ('_toolkit_root() { printf "%s\\n" "$TEST_TOOLKIT"; }\n'
                      'cmd_codex_doctor() { printf "launch_ready=true\\n"; }\n'
                      'die() { printf "%s\\n" "$*" >&2; exit 1; }\n' + function
                      + '\ncmd_codex_launch --root "$1" --pane "$2" --effort "$3"\n')
            for pane, effort in ((3, "high"), (4, "medium")):
                with self.subTest(pane=pane, effort=effort):
                    result = subprocess.run(
                        ["/bin/bash", "--noprofile", "--norc", "-c", script, "worker-test", str(ROOT), str(pane), effort],
                        env=dict(os.environ, TEST_TOOLKIT=str(toolkit), TMUX_PANE=""),
                        capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    argv = result.stdout.splitlines()
                    self.assertEqual(argv[argv.index("-m") + 1], "gpt-5.6-sol")
                    self.assertIn("model_reasoning_effort=" + effort, argv)
                    self.assertIn("--strict-config", argv)
                    self.assertEqual(argv[argv.index("--sandbox") + 1], "workspace-write")
                    self.assertEqual(argv[argv.index("--ask-for-approval") + 1], "never")
                    self.assertIn("sandbox_workspace_write.network_access=false", argv)
                    self.assertIn("sandbox_workspace_write.writable_roots=[]", argv)
                    self.assertIn("service_tier=fast", argv)


if __name__ == "__main__":
    unittest.main()
