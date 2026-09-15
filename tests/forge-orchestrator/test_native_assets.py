"""Unit coverage for trace interpretation, never native qualification evidence."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

TREE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("native_assets", TREE / "lib/forge_native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeAssets(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "SKILL.md"
        self.path.write_text("# Protocol\n\nFirst contract.\nLast contract.\n")

    def event(self, **item):
        return {"method": "item/completed", "params": {"item": item}}

    def test_codex_requires_complete_successful_tool_output(self):
        def read(output, **extra):
            return self.event(type="commandExecution", command="cat " + str(self.path),
                              aggregatedOutput=output, **extra)
        events = [read("# Protocol\nFirst contract.", exitCode=0)]
        self.assertFalse(native.codex_reads(events, [self.path])[str(self.path)])
        events.append(read("Last contract.", exitCode=1))
        self.assertFalse(native.codex_reads(events, [self.path])[str(self.path)])
        events.append(read("Last contract.", exitCode=0))
        self.assertTrue(native.codex_reads(events, [self.path])[str(self.path)])

    def test_codex_model_claim_and_checksum_are_not_loads(self):
        events = [self.event(type="agentMessage", text=self.path.read_text()),
                  self.event(type="commandExecution", command="shasum " + str(self.path),
                             aggregatedOutput="a" * 64, exitCode=0)]
        self.assertFalse(native.codex_reads(events, [self.path])[str(self.path)])

    def test_claude_requires_matching_read_tool_result(self):
        call = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "id": "read-1", "input": {"file_path": str(self.path)}}]}}
        result = {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "wrong", "content": self.path.read_text()}]}}
        self.assertFalse(native.claude_reads([call, result], [self.path])[str(self.path)])
        result["message"]["content"][0]["tool_use_id"] = "read-1"
        self.assertTrue(native.claude_reads([call, result], [self.path])[str(self.path)])
        result["message"]["content"][0]["is_error"] = True
        self.assertFalse(native.claude_reads([call, result], [self.path])[str(self.path)])

    def test_codex_rpc_preserves_policy_but_cwd_is_not_a_config_key(self):
        receipt = {"provider_argv": ["/bin/codex", "-C", "/a/.worktree", "--strict-config",
                   "--sandbox", "workspace-write", "--ask-for-approval", "on-request", "-c",
                   'projects={"/a/.worktree"={trust_level="untrusted"}}', "--disable", "hooks",
                   "--enable", "skip_host_skill_discovery", "-m", "test-model", "prompt"]}
        argv = native.codex_argv(receipt)
        self.assertEqual(argv[:2], ["/bin/codex", "app-server"])
        self.assertIn('sandbox_mode="workspace-write"', argv)
        self.assertIn('approval_policy="on-request"', argv)
        self.assertFalse(any(arg.startswith("cwd=") for arg in argv))
        self.assertIn(receipt["provider_argv"][10], argv)
        receipt["provider_argv"].insert(-1, "--unknown-policy")
        with self.assertRaises(ValueError):
            native.codex_argv(receipt)


if __name__ == "__main__":
    unittest.main()
