import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class HookCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="forge-p1-hooks-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root / "events"
        self.evidence.mkdir()
        self.adapter = self.root / "adapter.py"
        self.adapter.write_text("import json,sys\njson.dump({'argv':sys.argv[1:],"
                                "'payload':json.load(sys.stdin)},open('received.json','w'))\n")
        self.config = self.root / "config.json"
        self.fixture = Path(__file__).parent / "fixtures/hook_capture.py"

    def run_hook(self, provider, payload):
        self.config.write_text(json.dumps({"provider":provider,"python":sys.executable,
            "adapter":str(self.adapter),"root":str(self.root),"environment":dict(os.environ),
            "evidence":str(self.evidence)}))
        return subprocess.run([sys.executable,str(self.fixture),str(self.config)],
                              input=json.dumps(payload),text=True,capture_output=True)

    def test_codex_forwards_exact_payload_without_decision_output(self):
        payload = {"hook_event_name":"PermissionRequest","tool_name":"Bash",
                   "tool_input":{"command":["echo","fixture"]},"permission_mode":"default"}
        result = self.run_hook("codex",payload)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,"")
        received = json.loads((self.root / "received.json").read_text())
        self.assertEqual(received,{"argv":["--codex","permissionrequest"],"payload":payload})
        record = json.loads(next(self.evidence.glob("*.json")).read_text())
        self.assertEqual(record["payload"],payload)
        self.assertEqual(record["adapter_result"]["returncode"],0)

    def test_claude_stop_keeps_provider_adapter_shape(self):
        result = self.run_hook("claude",{"hook_event_name":"Stop"})
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads((self.root / "received.json").read_text())["argv"],["stop"])

    def test_child_event_is_recorded_but_not_forwarded_as_parent_stop(self):
        result = self.run_hook("codex",{"hook_event_name":"SubagentStop","agent_id":"child-1"})
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.root / "received.json").exists())
        record = json.loads(next(self.evidence.glob("*.json")).read_text())
        self.assertIsNone(record["adapter_argv"])
        self.assertIsNone(record["adapter_result"])

    def test_non_object_payload_does_not_reach_adapter(self):
        result = self.run_hook("codex",[])
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(result.stdout,"")
        self.assertFalse((self.root / "received.json").exists())

    def test_adapter_decision_is_evidence_not_an_approval(self):
        self.adapter.write_text('print(\'{"decision":"allow"}\')\n')
        result = self.run_hook("codex", {"hook_event_name": "PermissionRequest"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        record = json.loads(next(self.evidence.glob("*.json")).read_text())
        self.assertEqual(json.loads(record["adapter_result"]["stdout"]), {"decision": "allow"})

    def test_adapter_failure_is_retained_without_decision_output(self):
        self.adapter.write_text('import sys\nprint("fixture failure", file=sys.stderr)\nsys.exit(7)\n')
        result = self.run_hook("claude", {"hook_event_name": "Stop"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        record = json.loads(next(self.evidence.glob("*.json")).read_text())
        self.assertEqual(record["adapter_result"]["returncode"], 7)
        self.assertIn("fixture failure", record["adapter_result"]["stderr"])
