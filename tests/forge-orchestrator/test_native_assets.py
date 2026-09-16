"""Unit coverage for trace interpretation, never native qualification evidence."""
import importlib.util
import copy
import json
import shlex
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TREE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("native_assets", TREE / "lib/forge_native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeAssets(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name).resolve() / "SKILL.md"
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

    def test_policy_evidence_requires_exact_native_command_and_real_exit(self):
        argv = ["/pinned/python", "-I", "-S", "-B", "/protected/probe with spaces.py", "nonce"]
        command = shlex.join(argv)
        self.assertTrue(native.exact_command(command, argv))
        self.assertTrue(native.exact_command("/bin/zsh -lc " + shlex.quote(command), argv))
        for changed in (command + " ; true", "echo " + command, command.replace("-I", "-i"),
                        "/bin/zsh -lc " + shlex.quote(command + " || true"), "'"):
            self.assertFalse(native.exact_command(changed, argv))
        event = self.event(type="commandExecution", command=command, status="completed", exitCode=7,
                           aggregatedOutput='{"opened":["forbidden"]}')
        result = native.command_result([event], argv)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, event["params"]["item"]["aggregatedOutput"])
        for events in ([event, event], [self.event(type="agentMessage", text="passed")], []):
            with self.assertRaises(ValueError):
                native.command_result(events, argv)
        event["params"]["item"]["status"] = "declined"
        with self.assertRaises(ValueError):
            native.command_result([event], argv)

    def graph(self):
        expected = {"A": {"own": "A", "other": "UNKNOWN", "value": 11},
                    "B": {"own": "B", "other": "UNKNOWN", "value": 17}, "C": {"total": 28}}
        def call(tool, *threads):
            return self.event(type="collabAgentToolCall", tool=tool, status="completed",
                              receiverThreadIds=list(threads),
                              agentsStates={thread: {"status": "completed"} for thread in threads})
        events = [call("spawnAgent", "a"), call("spawnAgent", "b"), call("wait", "a", "b"),
                  call("followupTask", "a"), call("sendInput", "b"), call("wait", "a", "b"),
                  call("spawnAgent", "c"), call("wait", "c")]
        transcripts = {label.lower(): [{"type": "agentMessage", "text": json.dumps(value)}]
                       * (1 if label == "C" else 2) for label, value in expected.items()}
        return events, transcripts, expected

    def test_children_require_native_graph_and_child_owned_final_results(self):
        events, transcripts, expected = self.graph()
        result = native.child_graph(events, transcripts, expected, followups=True)
        self.assertEqual(result["status"], "pass")
        self.assertFalse(result["plaintext_isolation_attested"])
        parent_claim = [self.event(type="agentMessage", text=json.dumps(expected))]
        self.assertEqual(native.child_graph(parent_claim, transcripts, expected)["status"], "fail")
        transcripts["a"].append({"type": "agentMessage", "text": '{"own":"wrong"}'})
        self.assertEqual(native.child_graph(events, transcripts, expected, followups=True)["status"], "fail")

    def test_children_reject_missing_followup_timed_out_wait_and_early_synthesis(self):
        events, transcripts, expected = self.graph()
        cases = []
        cases.append(events[:3] + events[4:])
        timed_out = copy.deepcopy(events)
        timed_out[5]["params"]["item"]["agentsStates"]["a"]["status"] = "running"
        cases.append(timed_out)
        cases.append(events[:5] + [events[6], events[5], events[7]])
        cases.append([events[2], *events[:2], *events[3:]])
        cases.append(events[:5] + events[6:])
        for invalid in cases:
            with self.subTest(events=invalid):
                self.assertEqual(native.child_graph(invalid, transcripts, expected, followups=True)["status"], "fail")

    def test_children_reject_duplicate_children_and_cross_contamination(self):
        events, transcripts, expected = self.graph()
        duplicate = copy.deepcopy(events)
        duplicate[1]["params"]["item"]["receiverThreadIds"] = ["a"]
        self.assertEqual(native.child_graph(duplicate, transcripts, expected, followups=True)["status"], "fail")
        transcripts["a"][-1] = {"type": "agentMessage", "text": json.dumps(dict(expected["A"], other="B"))}
        self.assertEqual(native.child_graph(events, transcripts, expected, followups=True)["status"], "fail")

    def test_native_events_are_scoped_to_the_parent_thread(self):
        parent, child = self.event(type="agentMessage", text="parent"), self.event(type="agentMessage", text="child")
        parent["params"]["threadId"], child["params"]["threadId"] = "p", "c"
        self.assertEqual(native.thread_events([child, parent, self.event(type="agentMessage")], "p"), [parent])

    def test_only_completed_child_turns_count_as_followup_answers(self):
        commentary = {"type": "agentMessage", "phase": "commentary", "text": "working"}
        final = {"type": "agentMessage", "phase": "final_answer", "text": "{}"}
        thread = {"turns": [{"status": "completed", "items": [commentary, final, final]}]}
        self.assertEqual(native.child_turn_items(thread), [final])
        thread["turns"][0]["status"] = "inProgress"
        with self.assertRaises(ValueError):
            native.child_turn_items(thread)
        with self.assertRaises(ValueError):
            native.child_turn_items({"turns": []})

    def test_digest_parent_gets_only_path_not_artifact_contents(self):
        directory = Path(self.temp.name)
        prompt, expected, artifact = native.child_packet(directory, {"definition": {"provider": "codex"}},
                                                       directory, "digest")
        self.assertNotIn(str(artifact), prompt)
        self.assertNotIn(expected["digest"]["result"], prompt)
        self.assertIn(json.dumps("Follow " + str(directory / "digest-prompt.txt")), prompt)
        self.assertIn(str(artifact), (directory / "digest-prompt.txt").read_text())
        self.assertEqual(json.loads(artifact.read_text()), expected["digest"])

    def test_duplicate_assets_clean_only_owned_inputs_after_failure(self):
        root = Path(self.temp.name).resolve()
        paths = [root / "global/skills/forge-orchestrator/SKILL.md", root / "local/skills/forge-orchestrator/SKILL.md"]
        with self.assertRaises(RuntimeError):
            with native.duplicate_assets(paths, "test-marker"):
                self.assertTrue(all(path.exists() for path in paths))
                raise RuntimeError("interrupted")
        self.assertFalse((root / "global").exists())
        self.assertFalse((root / "local").exists())

    def test_duplicate_assets_refuse_existing_file_and_discovery_alias(self):
        root = Path(self.temp.name).resolve()
        fresh = root / "fresh/skill.md"
        with self.assertRaises(FileExistsError):
            with native.duplicate_assets([fresh, self.path], "test-marker"):
                self.fail("existing file should refuse")
        self.assertFalse(fresh.exists())
        self.assertIn("# Protocol", self.path.read_text())
        alias = root / "alias"
        alias.symlink_to(root, target_is_directory=True)
        with self.assertRaises(OSError):
            with native.duplicate_assets([alias / "new-skill.md"], "test-marker"):
                self.fail("symlink should refuse")
        self.assertFalse((root / "new-skill.md").exists())

    def test_duplicate_assets_preserve_concurrent_replacement(self):
        path = Path(self.temp.name).resolve() / "owned/SKILL.md"
        with self.assertRaisesRegex(ValueError, "preserving replacements"):
            with native.duplicate_assets([path], "test-marker"):
                path.write_text("concurrent edit")
        self.assertEqual(path.read_text(), "concurrent edit")

    def test_duplicate_creation_is_anchored_across_parent_symlink_swap(self):
        root = Path(self.temp.name).resolve()
        outside = root / "outside"
        outside.mkdir()
        path = root / "global/skills/forge-orchestrator/SKILL.md"
        original, swapped = native.os.open, False
        def swap(name, flags, *args, **kwargs):
            nonlocal swapped
            if name == "forge-orchestrator" and not swapped:
                swapped = True
                (root / "global/skills").rename(root / "moved")
                (root / "global/skills").symlink_to(outside, target_is_directory=True)
            return original(name, flags, *args, **kwargs)
        with patch.object(native.os, "open", side_effect=swap):
            with native.duplicate_assets([path], "test-marker"):
                self.assertEqual(list(outside.iterdir()), [])
                self.assertTrue((root / "moved/forge-orchestrator/SKILL.md").is_file())
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue((root / "global/skills").is_symlink())

    def test_duplicate_probe_requires_candidate_reads_without_decoy_contamination(self):
        decoy = Path(self.temp.name) / "decoy.md"
        events = [self.event(type="commandExecution", command="cat " + str(self.path), exitCode=0,
                             aggregatedOutput=self.path.read_text())]
        self.assertEqual(native.duplicate_result(events, [self.path], [decoy], "UNIQUE_DECOY", "codex")["status"], "pass")
        contaminated = [*events, self.event(type="agentMessage", text="UNIQUE_DECOY")]
        self.assertEqual(native.duplicate_result(contaminated, [self.path], [decoy], "UNIQUE_DECOY", "codex")["status"], "fail")
        read_decoy = [*events, self.event(type="commandExecution", command="cat " + str(decoy), exitCode=0)]
        self.assertEqual(native.duplicate_result(read_decoy, [self.path], [decoy], "UNIQUE_DECOY", "codex")["status"], "fail")
        self.assertEqual(native.duplicate_result([], [self.path], [decoy], "UNIQUE_DECOY", "codex")["status"], "fail")


if __name__ == "__main__":
    unittest.main()
